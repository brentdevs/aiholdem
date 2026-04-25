"""Stat aggregation for poker player profiling.

Queries hand_players rows within a configurable window and computes
aggregate statistics (VPIP, PFR, AF, etc.) for a given player.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PlayerStats:
    """Aggregate statistics for a single player over a window of hands."""

    hands: int
    vpip: int  # percentage 0-100
    pfr: int  # percentage 0-100
    af: float  # aggression factor
    three_bet: int  # percentage 0-100
    fold_to_three_bet: int  # percentage 0-100
    cbet: int  # percentage 0-100
    fold_to_cbet: int  # percentage 0-100
    wtsd: int  # percentage 0-100


# Single SQL query using AVG() and FILTER to compute all boolean-based
# statistics in one database round-trip (Requirement 3.11).
_STATS_SQL = """
SELECT
    COUNT(*) AS hands,
    ROUND(AVG(vpip::int) * 100)::int AS vpip,
    ROUND(AVG(pfr::int) * 100)::int AS pfr,
    ROUND(AVG(three_bet::int) FILTER (WHERE three_bet IS NOT NULL) * 100)::int AS three_bet,
    ROUND(AVG(fold_to_three_bet::int) FILTER (WHERE fold_to_three_bet IS NOT NULL) * 100)::int AS fold_to_three_bet,
    ROUND(AVG(cbet::int) FILTER (WHERE cbet IS NOT NULL) * 100)::int AS cbet,
    ROUND(AVG(fold_to_cbet::int) FILTER (WHERE fold_to_cbet IS NOT NULL) * 100)::int AS fold_to_cbet,
    ROUND(AVG(went_to_showdown::int) * 100)::int AS wtsd
FROM (
    SELECT hp.* FROM hand_players hp
    JOIN hands h ON hp.hand_id = h.hand_id
    WHERE hp.player_id = %s
    ORDER BY h.played_at DESC
    LIMIT %s
) sub;
"""

# AF query: count postflop raises and calls from the actions JSONB
# within the same window of recent hands for the player.
_AF_SQL = """
SELECT h.actions
FROM hand_players hp
JOIN hands h ON hp.hand_id = h.hand_id
WHERE hp.player_id = %s
ORDER BY h.played_at DESC
LIMIT %s;
"""

# Fetch the player's display name (used in action history) from the players table.
_PLAYER_NAME_SQL = """
SELECT label FROM players WHERE player_id = %s;
"""


def _compute_af(actions_rows: list[tuple], player_name: str) -> float:
    """Compute Aggression Factor from actions JSONB rows.

    AF = postflop raises / postflop calls for the player.
    Returns 0.0 when no postflop calls exist (Requirement 3.9).

    NOTE: The actions JSONB stores player display names (not IDs),
    so player_name must be the display name.
    """
    raises = 0
    calls = 0
    postflop_phases = {"flop", "turn", "river"}

    for (actions_json,) in actions_rows:
        if actions_json is None:
            continue
        actions = actions_json if isinstance(actions_json, list) else json.loads(actions_json)
        for entry in actions:
            phase = entry[0] if isinstance(entry, (list, tuple)) else entry.get("phase", "")
            player = entry[1] if isinstance(entry, (list, tuple)) else entry.get("player", "")
            action = entry[2] if isinstance(entry, (list, tuple)) else entry.get("action", "")

            if phase not in postflop_phases:
                continue
            if player != player_name:
                continue
            if action in ("R", "A"):
                raises += 1
            elif action == "C":
                calls += 1

    if calls == 0:
        return 0.0
    return round(raises / calls, 2)


class StatAggregator:
    """Queries hand_players for a player's recent N hands and computes aggregate stats."""

    def get_player_stats(
        self, conn, player_id: str, window: int = 80
    ) -> PlayerStats | None:
        """Compute aggregate stats for a player over the most recent *window* hands.

        Uses a single SQL query with AVG() and FILTER clauses for boolean stats,
        and a separate pass over actions JSONB for Aggression Factor.

        Returns None when no rows exist for the player.
        """
        with conn.cursor() as cur:
            cur.execute(_STATS_SQL, (player_id, window))
            row = cur.fetchone()

        if row is None or row[0] == 0:
            return None

        hands, vpip, pfr, three_bet, fold_to_three_bet, cbet, fold_to_cbet, wtsd = row

        # Fetch actions JSONB for AF computation
        with conn.cursor() as cur:
            cur.execute(_AF_SQL, (player_id, window))
            actions_rows = cur.fetchall()

        # Look up the player's display name (action history uses names, not IDs)
        player_name = player_id  # fallback
        with conn.cursor() as cur:
            cur.execute(_PLAYER_NAME_SQL, (player_id,))
            name_row = cur.fetchone()
            if name_row and name_row[0]:
                player_name = name_row[0]

        af = _compute_af(actions_rows, player_name)

        return PlayerStats(
            hands=hands,
            vpip=vpip or 0,
            pfr=pfr or 0,
            af=af,
            three_bet=three_bet or 0,
            fold_to_three_bet=fold_to_three_bet or 0,
            cbet=cbet or 0,
            fold_to_cbet=fold_to_cbet or 0,
            wtsd=wtsd or 0,
        )
