import logging
from psycopg2 import pool, OperationalError, DatabaseError
from app.leaderboard.models import GameResult

logger = logging.getLogger(__name__)


class LeaderboardService:
    def __init__(self, database_url: str | None) -> None:
        self._available = False
        self._pool = None

        if database_url is None:
            logger.warning("DATABASE_URL is not set — leaderboard functionality disabled")
            return

        try:
            self._pool = pool.SimpleConnectionPool(
                minconn=1, maxconn=5, dsn=database_url
            )
            self._available = True
        except OperationalError:
            logger.error("Failed to connect to database — leaderboard disabled")

    @property
    def available(self) -> bool:
        return self._available

    def init_db(self) -> None:
        if not self._available:
            return
        try:
            conn = self._pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS leaderboard (
                            model_id        TEXT PRIMARY KEY,
                            display_name    TEXT NOT NULL,
                            games_played    INTEGER NOT NULL DEFAULT 0,
                            wins            INTEGER NOT NULL DEFAULT 0,
                            placing_sum     INTEGER NOT NULL DEFAULT 0,
                            api_calls       INTEGER NOT NULL DEFAULT 0,
                            api_failures    INTEGER NOT NULL DEFAULT 0,
                            latency_sum_ms  BIGINT  NOT NULL DEFAULT 0,
                            retired         BOOLEAN NOT NULL DEFAULT FALSE
                        );
                    """)
                conn.commit()
            finally:
                self._pool.putconn(conn)
        except (OperationalError, DatabaseError) as e:
            logger.error("Failed to initialize leaderboard table: %s", e)
            self._available = False

    def record_game_results(self, results: list[GameResult]) -> None:
        if not self._available:
            return
        try:
            conn = self._pool.getconn()
            try:
                with conn.cursor() as cur:
                    for result in results:
                        win_value = 1 if result.placing == 1 else 0
                        cur.execute("""
                            INSERT INTO leaderboard
                                (model_id, display_name, games_played, wins,
                                 placing_sum, api_calls, api_failures, latency_sum_ms, retired)
                            VALUES (%s, %s, 1, %s, %s, %s, %s, %s, FALSE)
                            ON CONFLICT (model_id) DO UPDATE SET
                                display_name   = EXCLUDED.display_name,
                                games_played   = leaderboard.games_played + 1,
                                wins           = leaderboard.wins + EXCLUDED.wins,
                                placing_sum    = leaderboard.placing_sum + EXCLUDED.placing_sum,
                                api_calls      = leaderboard.api_calls + EXCLUDED.api_calls,
                                api_failures   = leaderboard.api_failures + EXCLUDED.api_failures,
                                latency_sum_ms = leaderboard.latency_sum_ms + EXCLUDED.latency_sum_ms;
                        """, (
                            result.model_id,
                            result.display_name,
                            win_value,
                            result.placing,
                            result.api_calls,
                            result.api_failures,
                            result.total_latency_ms,
                        ))
                conn.commit()
            finally:
                self._pool.putconn(conn)
        except (OperationalError, DatabaseError) as e:
            logger.error("Failed to record game results: %s", e)

    def sync_retired_status(self, active_models: list[str]) -> None:
        if not self._available:
            return
        try:
            conn = self._pool.getconn()
            try:
                with conn.cursor() as cur:
                    # Set retired=True for models NOT in active list
                    if active_models:
                        cur.execute(
                            "UPDATE leaderboard SET retired = TRUE WHERE model_id != ALL(%s);",
                            (active_models,),
                        )
                        cur.execute(
                            "UPDATE leaderboard SET retired = FALSE WHERE model_id = ANY(%s);",
                            (active_models,),
                        )
                    else:
                        cur.execute("UPDATE leaderboard SET retired = TRUE;")
                conn.commit()
            finally:
                self._pool.putconn(conn)
        except (OperationalError, DatabaseError) as e:
            logger.error("Failed to sync retired status: %s", e)

    def get_leaderboard(self) -> list[dict]:
        if not self._available:
            return []
        try:
            conn = self._pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            model_id,
                            display_name,
                            games_played,
                            wins,
                            (wins * 100.0) / NULLIF(games_played, 0) AS win_pct,
                            placing_sum::float / NULLIF(games_played, 0) AS avg_placing,
                            latency_sum_ms::float / NULLIF(api_calls, 0) AS avg_latency_ms,
                            (api_failures * 100.0) / NULLIF(api_calls, 0) AS failure_rate_pct,
                            retired
                        FROM leaderboard
                        ORDER BY win_pct DESC NULLS LAST;
                    """)
                    columns = [desc[0] for desc in cur.description]
                    return [dict(zip(columns, row)) for row in cur.fetchall()]
            finally:
                self._pool.putconn(conn)
        except (OperationalError, DatabaseError) as e:
            logger.error("Failed to query leaderboard: %s", e)
            return []
