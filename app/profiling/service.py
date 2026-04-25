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

from psycopg2 import pool, OperationalError, DatabaseError

from app.profiling.hand_recorder import HandRecord
from app.profiling.stat_aggregator import StatAggregator
from app.profiling.style_classifier import classify_style
from app.profiling.profile_formatter import format_profile, format_profiles_block

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
            self._pool = pool.SimpleConnectionPool(
                minconn=1, maxconn=5, dsn=database_url
            )
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
                    stats = aggregator.get_player_stats(
                        conn, pid, window=self.stat_window_size
                    )
                    style = classify_style(
                        stats, min_hands=self.min_hands_for_profile
                    ) if stats is not None else "Unknown"

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
