"""ProfilingService — top-level service for AI player profiling.

Owns the DB connection pool and orchestrates hand recording, game lifecycle
tracking, stat aggregation, style classification, and profile formatting.
Follows the same psycopg2 SimpleConnectionPool pattern as LeaderboardService.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone

from psycopg2 import DatabaseError, OperationalError, pool

from app.profiling.hand_recorder import HandRecord
from app.profiling.profile_formatter import format_profile, format_profiles_block
from app.profiling.stat_aggregator import PlayerStats, StatAggregator
from app.profiling.style_classifier import classify_style

logger = logging.getLogger(__name__)


def _compute_model_af(action_rows: list[tuple[object, str]]) -> float:
    raises = 0
    calls = 0
    for actions_json, player_name in action_rows:
        if actions_json is None:
            continue
        actions = actions_json if isinstance(actions_json, list) else json.loads(actions_json)
        for entry in actions:
            phase = entry[0] if isinstance(entry, (list, tuple)) else entry.get("phase", "")
            player = entry[1] if isinstance(entry, (list, tuple)) else entry.get("player", "")
            action = entry[2] if isinstance(entry, (list, tuple)) else entry.get("action", "")
            if phase not in {"flop", "turn", "river"} or player != player_name:
                continue
            if action in ("R", "A"):
                raises += 1
            elif action == "C":
                calls += 1
    return round(raises / calls, 2) if calls else 0.0


class ProfilingService:
    """Top-level profiling service.

    Disabled (no-op) when DATABASE_URL is None or PROFILING_ENABLED=false.
    All public recording methods are wrapped in try/except so failures
    never disrupt gameplay.
    """

    def __init__(self, database_url: str | None) -> None:
        self._available = False
        self._pool = None

        # Read configuration from env vars
        self.stat_window_size = int(os.environ.get("STAT_WINDOW_SIZE", "80"))
        self.min_hands_for_profile = int(os.environ.get("MIN_HANDS_FOR_PROFILE", "10"))
        self.include_recent_hands = int(os.environ.get("INCLUDE_RECENT_HANDS", "5"))
        self.include_self_stats = os.environ.get("INCLUDE_SELF_STATS", "true").lower() != "false"
        profiling_enabled = os.environ.get("PROFILING_ENABLED", "true").lower() != "false"

        if not profiling_enabled:
            logger.info("Profiling disabled via PROFILING_ENABLED=false")
            return

        if database_url is None:
            logger.warning("DATABASE_URL is not set — profiling functionality disabled")
            return

        try:
            self._pool = pool.SimpleConnectionPool(minconn=1, maxconn=5, dsn=database_url)
            self._available = True
        except OperationalError:
            logger.error("Failed to connect to database — profiling disabled")

    @property
    def available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------ #
    #  Database initialisation                                             #
    # ------------------------------------------------------------------ #

    def init_db(self) -> None:
        """Create and migrate profiling tables."""
        if not self._available:
            return
        try:
            conn = self._pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS players (
                            player_id   TEXT PRIMARY KEY,
                            label       TEXT,
                            model       TEXT,
                            config      JSONB,
                            created_at  TIMESTAMPTZ DEFAULT NOW()
                        );
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS games (
                            game_id         TEXT PRIMARY KEY,
                            lobby_id        TEXT NOT NULL DEFAULT 'arena',
                            game_type       TEXT,
                            blind_structure JSONB,
                            num_players     INTEGER,
                            started_at      TIMESTAMPTZ,
                            ended_at        TIMESTAMPTZ
                        );
                    """)
                    cur.execute(
                        "ALTER TABLE games "
                        "ADD COLUMN IF NOT EXISTS lobby_id TEXT NOT NULL DEFAULT 'arena';"
                    )
                    cur.execute("""
                        UPDATE games
                        SET lobby_id = split_part(game_type, ':', 2)
                        WHERE game_type LIKE 'arena:%'
                          AND split_part(game_type, ':', 2) != ''
                          AND lobby_id = 'arena';
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS hands (
                            hand_id             TEXT PRIMARY KEY,
                            game_id             TEXT REFERENCES games(game_id),
                            hand_number         INTEGER,
                            dealer              TEXT,
                            small_blind_player  TEXT,
                            big_blind_player    TEXT,
                            blind_amounts       INTEGER[2],
                            community_cards     TEXT,
                            pot                 INTEGER,
                            actions             JSONB,
                            played_at           TIMESTAMPTZ DEFAULT NOW()
                        );
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS hand_players (
                            hand_id             TEXT REFERENCES hands(hand_id),
                            player_id           TEXT REFERENCES players(player_id),
                            vpip                BOOLEAN,
                            pfr                 BOOLEAN,
                            three_bet           BOOLEAN,
                            fold_to_three_bet   BOOLEAN,
                            cbet                BOOLEAN,
                            fold_to_cbet        BOOLEAN,
                            is_steal_attempt    BOOLEAN,
                            went_to_showdown    BOOLEAN,
                            won_hand            BOOLEAN,
                            position            TEXT,
                            hole_cards          TEXT,
                            starting_stack      INTEGER,
                            net_result          INTEGER,
                            PRIMARY KEY (hand_id, player_id)
                        );
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS game_results (
                            game_id         TEXT REFERENCES games(game_id),
                            player_id       TEXT REFERENCES players(player_id),
                            finish_position INTEGER,
                            final_stack     INTEGER,
                            buy_in          INTEGER,
                            net_profit      INTEGER,
                            PRIMARY KEY (game_id, player_id)
                        );
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS errors (
                            id              SERIAL PRIMARY KEY,
                            hand_id         TEXT,
                            player_id       TEXT,
                            error_type      TEXT,
                            raw_response    TEXT,
                            occurred_at     TIMESTAMPTZ DEFAULT NOW()
                        );
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS model_game_metrics (
                            game_id         TEXT REFERENCES games(game_id),
                            player_id       TEXT REFERENCES players(player_id),
                            model_id        TEXT NOT NULL,
                            display_name    TEXT NOT NULL,
                            api_calls       INTEGER NOT NULL DEFAULT 0,
                            api_failures    INTEGER NOT NULL DEFAULT 0,
                            latency_sum_ms  BIGINT NOT NULL DEFAULT 0,
                            recorded_at     TIMESTAMPTZ DEFAULT NOW(),
                            PRIMARY KEY (game_id, player_id)
                        );
                    """)
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_players_model ON players (model);")
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_games_lobby_started "
                        "ON games (lobby_id, started_at);"
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_games_ended_at ON games (ended_at);"
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_game_results_player "
                        "ON game_results (player_id);"
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_hands_played_at ON hands (played_at);"
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_hand_players_player "
                        "ON hand_players (player_id);"
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_model_metrics_model_recorded "
                        "ON model_game_metrics (model_id, recorded_at);"
                    )
                conn.commit()
            finally:
                self._pool.putconn(conn)
        except (OperationalError, DatabaseError) as e:
            logger.error("Failed to initialize profiling tables: %s", e)
            self._available = False

    # ------------------------------------------------------------------ #
    #  Hand recording                                                      #
    # ------------------------------------------------------------------ #

    def record_hand(self, hand_record: HandRecord) -> None:
        """Insert hand + hand_players + errors in a single transaction.

        Wrapped in try/except so a DB failure never disrupts gameplay.
        """
        if not self._available:
            return
        try:
            conn = self._pool.getconn()
            try:
                with conn.cursor() as cur:
                    # Insert hand row
                    cur.execute(
                        """
                        INSERT INTO hands
                            (hand_id, game_id, hand_number, dealer,
                             small_blind_player, big_blind_player,
                             blind_amounts, community_cards, pot, actions)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (hand_id) DO NOTHING;
                        """,
                        (
                            hand_record.hand_id,
                            hand_record.game_id,
                            hand_record.hand_number,
                            hand_record.dealer,
                            hand_record.small_blind_player,
                            hand_record.big_blind_player,
                            list(hand_record.blind_amounts),
                            hand_record.community_cards,
                            hand_record.pot,
                            json.dumps(hand_record.actions),
                        ),
                    )

                    # Insert hand_players rows
                    for pf in hand_record.player_flags:
                        cur.execute(
                            """
                            INSERT INTO hand_players
                                (hand_id, player_id, vpip, pfr, three_bet,
                                 fold_to_three_bet, cbet, fold_to_cbet,
                                 is_steal_attempt, went_to_showdown, won_hand,
                                 position, hole_cards, starting_stack, net_result)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (hand_id, player_id) DO NOTHING;
                            """,
                            (
                                hand_record.hand_id,
                                pf.player_id,
                                pf.vpip,
                                pf.pfr,
                                pf.three_bet,
                                pf.fold_to_three_bet,
                                pf.cbet,
                                pf.fold_to_cbet,
                                pf.is_steal_attempt,
                                pf.went_to_showdown,
                                pf.won_hand,
                                pf.position,
                                pf.hole_cards,
                                pf.starting_stack,
                                pf.net_result,
                            ),
                        )

                    # Insert error rows
                    for err in hand_record.errors:
                        cur.execute(
                            """
                            INSERT INTO errors
                                (hand_id, player_id, error_type, raw_response)
                            VALUES (%s, %s, %s, %s);
                            """,
                            (
                                hand_record.hand_id,
                                err.player_id,
                                err.error_type,
                                err.raw_response,
                            ),
                        )

                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                self._pool.putconn(conn)
        except (OperationalError, DatabaseError) as e:
            logger.error("Failed to record hand %s: %s", hand_record.hand_id, e)
        except Exception as e:
            logger.error("Unexpected error recording hand %s: %s", hand_record.hand_id, e)

    # ------------------------------------------------------------------ #
    #  Game lifecycle                                                      #
    # ------------------------------------------------------------------ #

    def record_game_start(
        self,
        game_id: str,
        lobby_id: str,
        game_type: str,
        blind_structure: list[tuple[int, int]],
        num_players: int,
        players: list[dict],
    ) -> None:
        """Insert a games row and upsert players rows.

        players: list of dicts with keys player_id, label, model, config.
        """
        if not self._available:
            return
        try:
            conn = self._pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO games
                            (game_id, lobby_id, game_type, blind_structure,
                             num_players, started_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (game_id) DO NOTHING;
                        """,
                        (
                            game_id,
                            lobby_id,
                            game_type,
                            json.dumps(blind_structure),
                            num_players,
                            datetime.now(timezone.utc),
                        ),
                    )

                    for p in players:
                        cur.execute(
                            """
                            INSERT INTO players (player_id, label, model, config)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (player_id) DO UPDATE SET
                                label  = EXCLUDED.label,
                                model  = EXCLUDED.model,
                                config = EXCLUDED.config;
                            """,
                            (
                                p.get("player_id", ""),
                                p.get("label", ""),
                                p.get("model", ""),
                                json.dumps(p.get("config")) if p.get("config") else None,
                            ),
                        )

                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                self._pool.putconn(conn)
        except (OperationalError, DatabaseError) as e:
            logger.error("Failed to record game start %s: %s", game_id, e)
        except Exception as e:
            logger.error("Unexpected error recording game start %s: %s", game_id, e)

    def record_game_end(self, game_id: str, player_results: list[dict]) -> None:
        """Update games.ended_at and insert game_results rows.

        player_results includes result fields and, for AI players, model_id,
        display_name, api_calls, api_failures, and latency_sum_ms.
        """
        if not self._available:
            return
        try:
            conn = self._pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE games SET ended_at = %s WHERE game_id = %s;
                        """,
                        (datetime.now(timezone.utc), game_id),
                    )

                    for pr in player_results:
                        cur.execute(
                            """
                            INSERT INTO game_results
                                (game_id, player_id, finish_position,
                                 final_stack, buy_in, net_profit)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (game_id, player_id) DO NOTHING;
                            """,
                            (
                                game_id,
                                pr.get("player_id", ""),
                                pr.get("finish_position", 0),
                                pr.get("final_stack", 0),
                                pr.get("buy_in", 0),
                                pr.get("net_profit", 0),
                            ),
                        )
                        model_id = pr.get("model_id")
                        if model_id:
                            cur.execute(
                                """
                                INSERT INTO model_game_metrics
                                    (game_id, player_id, model_id, display_name,
                                     api_calls, api_failures, latency_sum_ms)
                                VALUES (%s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (game_id, player_id) DO NOTHING;
                                """,
                                (
                                    game_id,
                                    pr.get("player_id", ""),
                                    model_id,
                                    pr.get("display_name", model_id),
                                    pr.get("api_calls", 0),
                                    pr.get("api_failures", 0),
                                    pr.get("latency_sum_ms", 0),
                                ),
                            )

                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                self._pool.putconn(conn)
        except (OperationalError, DatabaseError) as e:
            logger.error("Failed to record game end %s: %s", game_id, e)
        except Exception as e:
            logger.error("Unexpected error recording game end %s: %s", game_id, e)

    # ------------------------------------------------------------------ #
    #  Profile generation                                                  #
    # ------------------------------------------------------------------ #

    def get_opponent_profiles(
        self,
        game_id: str,
        player_ids: list[str],
        self_player_id: str,
        id_to_label: dict[str, str],
    ) -> str:
        """Orchestrate StatAggregator → StyleClassifier → ProfileFormatter.

        Returns a formatted profile block string for prompt injection.
        Returns empty string on any failure.
        """
        if not self._available:
            return ""
        try:
            conn = self._pool.getconn()
            try:
                aggregator = StatAggregator()
                profiles: list[str] = []

                for pid in player_ids:
                    is_self = pid == self_player_id
                    if is_self:
                        continue

                    label = id_to_label.get(pid, pid)
                    stats = aggregator.get_player_stats(conn, pid, window=self.stat_window_size)
                    style = (
                        classify_style(stats, min_hands=self.min_hands_for_profile)
                        if stats is not None
                        else "Unknown"
                    )

                    profile_str = format_profile(
                        label=label,
                        stats=stats,
                        style=style,
                        is_self=False,
                        min_hands=self.min_hands_for_profile,
                    )
                    profiles.append(profile_str)

                return format_profiles_block(profiles)
            finally:
                self._pool.putconn(conn)
        except (OperationalError, DatabaseError) as e:
            logger.warning("Failed to generate opponent profiles: %s", e)
            return ""
        except Exception as e:
            logger.warning("Unexpected error generating profiles: %s", e)
            return ""

    # ------------------------------------------------------------------ #
    #  Historical model statistics                                       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _scope_clause(alias: str, lobby_id: str | None) -> tuple[str, list[str]]:
        if lobby_id is None:
            return "", []
        return f" AND {alias}.lobby_id = %s", [lobby_id]

    @staticmethod
    def _rows_as_dicts(cur) -> list[dict]:
        columns = [desc[0] for desc in cur.description]
        rows = []
        for row in cur.fetchall():
            item = dict(zip(columns, row))
            for key in ("date", "ended_at"):
                value = item.get(key)
                if value is not None and hasattr(value, "isoformat"):
                    item[key] = value.isoformat()
            rows.append(item)
        return rows

    def get_model_history(self, model_id: str, lobby_id: str | None = None, days: int = 30) -> dict:
        """Return daily performance/reliability data and placement counts."""
        if not self._available:
            return {"daily": [], "placements": []}
        try:
            conn = self._pool.getconn()
            try:
                game_scope, game_scope_params = self._scope_clause("g", lobby_id)
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        WITH performance AS (
                            SELECT
                                date_trunc('day', g.ended_at) AS day,
                                COUNT(*) AS games,
                                COUNT(*) FILTER (WHERE gr.finish_position = 1) AS wins,
                                AVG(gr.finish_position)::float AS avg_placement,
                                SUM(gr.net_profit) AS net_profit
                            FROM game_results gr
                            JOIN games g ON g.game_id = gr.game_id
                            JOIN players p ON p.player_id = gr.player_id
                            WHERE p.model = %s
                              AND g.ended_at >= NOW() - (%s * INTERVAL '1 day')
                              {game_scope}
                            GROUP BY day
                        ),
                        reliability AS (
                            SELECT
                                date_trunc('day', mgm.recorded_at) AS day,
                                SUM(mgm.api_calls) AS api_calls,
                                SUM(mgm.api_failures) AS api_failures,
                                (SUM(mgm.api_failures) * 100.0)
                                    / NULLIF(SUM(mgm.api_calls), 0) AS failure_rate_pct,
                                SUM(mgm.latency_sum_ms)::float
                                    / NULLIF(SUM(mgm.api_calls), 0) AS avg_latency_ms
                            FROM model_game_metrics mgm
                            JOIN games g ON g.game_id = mgm.game_id
                            WHERE mgm.model_id = %s
                              AND mgm.recorded_at >= NOW() - (%s * INTERVAL '1 day')
                              {game_scope}
                            GROUP BY day
                        )
                        SELECT
                            COALESCE(performance.day, reliability.day) AS date,
                            COALESCE(performance.games, 0) AS games,
                            COALESCE(performance.wins, 0) AS wins,
                            COALESCE(performance.avg_placement, 0) AS avg_placement,
                            COALESCE(performance.net_profit, 0) AS net_profit,
                            COALESCE(reliability.api_calls, 0) AS api_calls,
                            COALESCE(reliability.api_failures, 0) AS api_failures,
                            COALESCE(reliability.failure_rate_pct, 0) AS failure_rate_pct,
                            COALESCE(reliability.avg_latency_ms, 0) AS avg_latency_ms
                        FROM performance
                        FULL OUTER JOIN reliability
                            ON performance.day = reliability.day
                        ORDER BY date;
                        """,
                        [
                            model_id,
                            days,
                            *game_scope_params,
                            model_id,
                            days,
                            *game_scope_params,
                        ],
                    )
                    daily = self._rows_as_dicts(cur)

                    cur.execute(
                        f"""
                        SELECT
                            gr.finish_position AS position,
                            COUNT(*) AS games
                        FROM game_results gr
                        JOIN games g ON g.game_id = gr.game_id
                        JOIN players p ON p.player_id = gr.player_id
                        WHERE p.model = %s
                          AND g.ended_at >= NOW() - (%s * INTERVAL '1 day')
                          {game_scope}
                        GROUP BY gr.finish_position
                        ORDER BY gr.finish_position;
                        """,
                        [model_id, days, *game_scope_params],
                    )
                    placements = self._rows_as_dicts(cur)
                    return {"daily": daily, "placements": placements}
            finally:
                self._pool.putconn(conn)
        except (OperationalError, DatabaseError) as e:
            logger.warning("Failed to query model history: %s", e)
            return {"daily": [], "placements": []}
        except Exception as e:
            logger.warning("Unexpected error querying model history: %s", e)
            return {"daily": [], "placements": []}

    def get_model_style(self, model_id: str, lobby_id: str | None = None, window: int = 80) -> dict:
        """Return recent aggregate style statistics and daily trends."""
        if not self._available:
            return {}
        try:
            conn = self._pool.getconn()
            try:
                game_scope, game_scope_params = self._scope_clause("g", lobby_id)
                recent_sql = f"""
                    SELECT hp.*, h.played_at, h.actions, p.label
                    FROM hand_players hp
                    JOIN hands h ON h.hand_id = hp.hand_id
                    JOIN games g ON g.game_id = h.game_id
                    JOIN players p ON p.player_id = hp.player_id
                    WHERE p.model = %s {game_scope}
                    ORDER BY h.played_at DESC
                    LIMIT %s
                """
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        WITH recent AS ({recent_sql})
                        SELECT
                            COUNT(*) AS hands,
                            ROUND(AVG(vpip::int) * 100)::int AS vpip,
                            ROUND(AVG(pfr::int) * 100)::int AS pfr,
                            ROUND(AVG(three_bet::int)
                                FILTER (WHERE three_bet IS NOT NULL) * 100)::int AS three_bet,
                            ROUND(AVG(fold_to_three_bet::int)
                                FILTER (WHERE fold_to_three_bet IS NOT NULL) * 100)::int
                                AS fold_to_three_bet,
                            ROUND(AVG(cbet::int)
                                FILTER (WHERE cbet IS NOT NULL) * 100)::int AS cbet,
                            ROUND(AVG(fold_to_cbet::int)
                                FILTER (WHERE fold_to_cbet IS NOT NULL) * 100)::int
                                AS fold_to_cbet,
                            ROUND(AVG(went_to_showdown::int) * 100)::int AS wtsd
                        FROM recent;
                        """,
                        [model_id, *game_scope_params, window],
                    )
                    row = cur.fetchone()
                    if row is None or row[0] == 0:
                        return {}

                    cur.execute(
                        f"""
                        WITH recent AS ({recent_sql})
                        SELECT actions, label FROM recent;
                        """,
                        [model_id, *game_scope_params, window],
                    )
                    af = _compute_model_af(cur.fetchall())

                    stats = PlayerStats(
                        hands=row[0],
                        vpip=row[1] or 0,
                        pfr=row[2] or 0,
                        af=af,
                        three_bet=row[3] or 0,
                        fold_to_three_bet=row[4] or 0,
                        cbet=row[5] or 0,
                        fold_to_cbet=row[6] or 0,
                        wtsd=row[7] or 0,
                    )
                    result = asdict(stats)
                    result["style"] = classify_style(stats, min_hands=self.min_hands_for_profile)

                    cur.execute(
                        f"""
                        WITH recent AS ({recent_sql})
                        SELECT
                            date_trunc('day', played_at) AS date,
                            COUNT(*) AS hands,
                            ROUND(AVG(vpip::int) * 100)::int AS vpip,
                            ROUND(AVG(pfr::int) * 100)::int AS pfr,
                            ROUND(AVG(three_bet::int)
                                FILTER (WHERE three_bet IS NOT NULL) * 100)::int AS three_bet,
                            ROUND(AVG(cbet::int)
                                FILTER (WHERE cbet IS NOT NULL) * 100)::int AS cbet,
                            ROUND(AVG(went_to_showdown::int) * 100)::int AS wtsd
                        FROM recent
                        GROUP BY date
                        ORDER BY date;
                        """,
                        [model_id, *game_scope_params, window],
                    )
                    result["history"] = self._rows_as_dicts(cur)
                    return result
            finally:
                self._pool.putconn(conn)
        except (OperationalError, DatabaseError) as e:
            logger.warning("Failed to query model style: %s", e)
            return {}
        except Exception as e:
            logger.warning("Unexpected error querying model style: %s", e)
            return {}

    def get_model_games(
        self, model_id: str, lobby_id: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Return recent completed games for a model."""
        if not self._available:
            return []
        try:
            conn = self._pool.getconn()
            try:
                game_scope, game_scope_params = self._scope_clause("g", lobby_id)
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT
                            g.ended_at,
                            g.lobby_id,
                            gr.game_id,
                            gr.finish_position,
                            gr.final_stack,
                            gr.net_profit
                        FROM game_results gr
                        JOIN games g ON g.game_id = gr.game_id
                        JOIN players p ON p.player_id = gr.player_id
                        WHERE p.model = %s
                          AND g.ended_at IS NOT NULL
                          {game_scope}
                        ORDER BY g.ended_at DESC
                        LIMIT %s;
                        """,
                        [model_id, *game_scope_params, limit],
                    )
                    return self._rows_as_dicts(cur)
            finally:
                self._pool.putconn(conn)
        except (OperationalError, DatabaseError) as e:
            logger.warning("Failed to query recent model games: %s", e)
            return []
        except Exception as e:
            logger.warning("Unexpected error querying recent model games: %s", e)
            return []
