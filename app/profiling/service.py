"""ProfilingService — top-level service for AI player profiling.

Owns the DB connection pool and orchestrates hand recording, game lifecycle
tracking, stat aggregation, style classification, and profile formatting.
Follows the same psycopg2 SimpleConnectionPool pattern as LeaderboardService.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from psycopg2 import DatabaseError, OperationalError, pool

from app.profiling.hand_recorder import HandRecord
from app.profiling.profile_formatter import format_profile, format_profiles_block
from app.profiling.stat_aggregator import StatAggregator
from app.profiling.style_classifier import classify_style

logger = logging.getLogger(__name__)


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
        """CREATE TABLE IF NOT EXISTS for all 6 profiling tables."""
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
                            game_type       TEXT,
                            blind_structure JSONB,
                            num_players     INTEGER,
                            started_at      TIMESTAMPTZ,
                            ended_at        TIMESTAMPTZ
                        );
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
                        CREATE TABLE IF NOT EXISTS game_model_stats (
                            game_id         TEXT REFERENCES games(game_id),
                            model_id        TEXT,
                            api_calls       INTEGER NOT NULL DEFAULT 0,
                            api_failures    INTEGER NOT NULL DEFAULT 0,
                            latency_ms      BIGINT  NOT NULL DEFAULT 0,
                            finish_pos      INTEGER NOT NULL DEFAULT 0,
                            PRIMARY KEY (game_id, model_id)
                        );
                    """)
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_games_started_at ON games (started_at);"
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_hands_played_at ON hands (played_at);"
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_errors_occurred_at ON errors (occurred_at);"
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_gms_model_id ON game_model_stats (model_id);"
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
                            (game_id, game_type, blind_structure, num_players, started_at)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (game_id) DO NOTHING;
                        """,
                        (
                            game_id,
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

        player_results: list of dicts with keys player_id, finish_position,
        final_stack, buy_in, net_profit.
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

    def record_game_model_stats(self, game_id: str, model_stats: list[dict]) -> None:
        """Insert per-game per-model API stats rows.

        model_stats: list of dicts with keys model_id, api_calls, api_failures,
        latency_ms, finish_pos.
        """
        if not self._available:
            return
        try:
            conn = self._pool.getconn()
            try:
                with conn.cursor() as cur:
                    for ms in model_stats:
                        cur.execute(
                            """
                            INSERT INTO game_model_stats
                                (game_id, model_id, api_calls, api_failures,
                                 latency_ms, finish_pos)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (game_id, model_id) DO NOTHING;
                            """,
                            (
                                game_id,
                                ms.get("model_id", ""),
                                ms.get("api_calls", 0),
                                ms.get("api_failures", 0),
                                ms.get("latency_ms", 0),
                                ms.get("finish_pos", 0),
                            ),
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                self._pool.putconn(conn)
        except (OperationalError, DatabaseError) as e:
            logger.error("Failed to record game model stats %s: %s", game_id, e)
        except Exception as e:
            logger.error("Unexpected error recording game model stats %s: %s", game_id, e)

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
    #  Historical stats queries (for per-model detail page)              #
    # ------------------------------------------------------------------ #

    def get_model_daily_stats(self, model_id: str, days: int = 30) -> list[dict]:
        """Return daily aggregates for a model over the last N days.

        Each row: {date, games, wins, avg_finish, net_profit, errors,
                   api_calls, api_failures, avg_latency_ms}
        """
        if not self._available:
            return []
        try:
            conn = self._pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        WITH gms AS (
                            SELECT
                                date_trunc('day', g.started_at) AS day,
                                COUNT(*)                       AS games,
                                COUNT(*) FILTER (WHERE gms.finish_pos = 1) AS wins,
                                COALESCE(AVG(gms.finish_pos), 0) AS avg_finish,
                                COALESCE(SUM(gr.net_profit), 0) AS net_profit,
                                COALESCE(SUM(gms.api_calls), 0)  AS api_calls,
                                COALESCE(SUM(gms.api_failures), 0) AS api_failures,
                                COALESCE(
                                    AVG(gms.latency_ms) FILTER (WHERE gms.api_calls > 0),
                                    0
                                ) AS avg_latency_ms
                            FROM game_model_stats gms
                            JOIN games g ON gms.game_id = g.game_id
                            LEFT JOIN game_results gr
                                ON gr.game_id = gms.game_id
                               AND gr.player_id = (
                                   SELECT player_id FROM players
                                   WHERE model = %s LIMIT 1
                               )
                            WHERE gms.model_id = %s
                              AND g.started_at >= NOW() - (%s || ' days')::INTERVAL
                            GROUP BY day
                        ),
                        errs AS (
                            SELECT
                                date_trunc('day', e.occurred_at) AS day,
                                COUNT(*) AS errors
                            FROM errors e
                            JOIN players p ON e.player_id = p.player_id
                            WHERE p.model = %s
                              AND e.occurred_at >= NOW() - (%s || ' days')::INTERVAL
                            GROUP BY day
                        )
                        SELECT
                            COALESCE(gms.day, errs.day) AS date,
                            COALESCE(gms.games, 0)         AS games,
                            COALESCE(gms.wins, 0)          AS wins,
                            COALESCE(gms.avg_finish, 0)    AS avg_finish,
                            COALESCE(gms.net_profit, 0)    AS net_profit,
                            COALESCE(gms.api_calls, 0)      AS api_calls,
                            COALESCE(gms.api_failures, 0)  AS api_failures,
                            COALESCE(gms.avg_latency_ms, 0) AS avg_latency_ms,
                            COALESCE(errs.errors, 0)       AS errors
                        FROM gms
                        FULL OUTER JOIN errs ON gms.day = errs.day
                        ORDER BY date;
                        """,
                        (model_id, model_id, str(days), model_id, str(days)),
                    )
                    columns = [desc[0] for desc in cur.description]
                    rows = []
                    for row in cur.fetchall():
                        d = dict(zip(columns, row))
                        if d.get("date") is not None:
                            d["date"] = (
                                d["date"].isoformat()
                                if hasattr(d["date"], "isoformat")
                                else str(d["date"])
                            )
                        rows.append(d)
                    return rows
            finally:
                self._pool.putconn(conn)
        except (OperationalError, DatabaseError) as e:
            logger.warning("Failed to get model daily stats: %s", e)
            return []
        except Exception as e:
            logger.warning("Unexpected error getting model daily stats: %s", e)
            return []

    def get_model_style_trends(self, model_id: str, buckets: int = 10) -> list[dict]:
        """Return rolling VPIP/PFR/AF/WTSD stats over time buckets.

        Splits the player's hand history into N chronological buckets and
        computes the same stats StatAggregator produces, per bucket.
        Each row: {bucket, hands, vpip, pfr, af, wtsd}
        """
        if not self._available:
            return []
        try:
            conn = self._pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        WITH ranked AS (
                            SELECT hp.*,
                                   h.played_at,
                                   NTILE(%s) OVER (ORDER BY h.played_at) AS bucket
                            FROM hand_players hp
                            JOIN hands h ON hp.hand_id = h.hand_id
                            JOIN players p ON hp.player_id = p.player_id
                            WHERE p.model = %s
                        )
                        SELECT
                            bucket,
                            COUNT(*) AS hands,
                            COALESCE(ROUND(AVG(vpip::int) * 100)::int, 0) AS vpip,
                            COALESCE(ROUND(AVG(pfr::int) * 100)::int, 0) AS pfr,
                            COALESCE(ROUND(AVG(went_to_showdown::int) * 100)::int, 0) AS wtsd
                        FROM ranked
                        GROUP BY bucket
                        ORDER BY bucket;
                        """,
                        (buckets, model_id),
                    )
                    columns = [desc[0] for desc in cur.description]
                    rows = []
                    for row in cur.fetchall():
                        d = dict(zip(columns, row))
                        rows.append(d)
                    return rows
            finally:
                self._pool.putconn(conn)
        except (OperationalError, DatabaseError) as e:
            logger.warning("Failed to get model style trends: %s", e)
            return []
        except Exception as e:
            logger.warning("Unexpected error getting style trends: %s", e)
            return []

    def get_model_summary(self, model_id: str) -> dict:
        """Return headline summary for a model: lifetime stats + style label.

        Joins leaderboard cumulative data with a rolling-window style label.
        Returns {} on failure.
        """
        if not self._available:
            return {}
        try:
            conn = self._pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
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
                        WHERE model_id = %s;
                        """,
                        (model_id,),
                    )
                    columns = [desc[0] for desc in cur.description]
                    row = cur.fetchone()
                    if row is None:
                        return {}
                    summary = dict(zip(columns, row))

                    # Rolling-window style from profiling tables
                    cur.execute(
                        "SELECT player_id FROM players WHERE model = %s ORDER BY created_at DESC LIMIT 1;",
                        (model_id,),
                    )
                    pid_row = cur.fetchone()
                    if pid_row:
                        pid = pid_row[0]
                        aggregator = StatAggregator()
                        stats = aggregator.get_player_stats(conn, pid, window=self.stat_window_size)
                        if stats is not None:
                            summary["style"] = classify_style(
                                stats, min_hands=self.min_hands_for_profile
                            )
                            summary["vpip"] = stats.vpip
                            summary["pfr"] = stats.pfr
                            summary["af"] = stats.af
                            summary["wtsd"] = stats.wtsd
                            summary["sample_hands"] = stats.hands
                        else:
                            summary["style"] = "Unknown"
                            summary["sample_hands"] = 0
                    else:
                        summary["style"] = "Unknown"
                        summary["sample_hands"] = 0
                    return summary
            finally:
                self._pool.putconn(conn)
        except (OperationalError, DatabaseError) as e:
            logger.warning("Failed to get model summary: %s", e)
            return {}
        except Exception as e:
            logger.warning("Unexpected error getting model summary: %s", e)
            return {}

    def get_model_recent_hands(self, model_id: str, limit: int = 20) -> list[dict]:
        """Return the most recent hand_players rows for a model.

        Each row: {played_at, hand_id, game_id, vpip, pfr, went_to_showdown,
                   won_hand, net_result, position, hole_cards}
        """
        if not self._available:
            return []
        try:
            conn = self._pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                            h.played_at, hp.hand_id, h.game_id,
                            hp.vpip, hp.pfr, hp.three_bet, hp.went_to_showdown,
                            hp.won_hand, hp.net_result, hp.position, hp.hole_cards
                        FROM hand_players hp
                        JOIN hands h ON hp.hand_id = h.hand_id
                        JOIN players p ON hp.player_id = p.player_id
                        WHERE p.model = %s
                        ORDER BY h.played_at DESC
                        LIMIT %s;
                        """,
                        (model_id, limit),
                    )
                    columns = [desc[0] for desc in cur.description]
                    rows = []
                    for row in cur.fetchall():
                        d = dict(zip(columns, row))
                        if d.get("played_at") is not None:
                            d["played_at"] = (
                                d["played_at"].isoformat()
                                if hasattr(d["played_at"], "isoformat")
                                else str(d["played_at"])
                            )
                        rows.append(d)
                    return rows
            finally:
                self._pool.putconn(conn)
        except (OperationalError, DatabaseError) as e:
            logger.warning("Failed to get model recent hands: %s", e)
            return []
        except Exception as e:
            logger.warning("Unexpected error getting recent hands: %s", e)
            return []
