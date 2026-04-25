"""Style classification for poker player profiling.

Maps aggregate player statistics to a human-readable style archetype
label with optional tendency annotations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol


class _StatsLike(Protocol):
    """Minimal interface for player stats needed by classify_style."""

    hands: int
    vpip: int
    af: float
    fold_to_three_bet: int
    cbet: int
    wtsd: int


if TYPE_CHECKING:
    pass  # PlayerStats will be importable from stat_aggregator once created


def classify_style(stats: _StatsLike, min_hands: int = 10) -> str:
    """Pure function: return style label with tendency annotations.

    Returns "Unknown" if stats.hands < min_hands.

    Classification grid (VPIP / AF):
      VPIP < 20%                        → "Nit"
      VPIP 20-28%, AF > 2.0             → "TAG"
      VPIP 20-28%, AF 1.0-2.0           → "Solid/Semi-Aggressive"
      VPIP 20-28%, AF < 1.0             → "Semi-Passive"
      VPIP > 28%, AF > 2.0              → "LAG"
      VPIP > 28%, AF 1.5-2.0            → "LAG"
      VPIP > 28%, AF < 1.5              → "Calling Station"

    Tendency annotations appended when:
      fold_to_3bet > 70%  → "Folds to pressure."
      fold_to_3bet < 30%  → "Never folds to 3bet."
      cbet > 75%          → "High CBet."
      cbet < 40%          → "Rarely CBets."
      wtsd > 35%          → "Showdown bound."
    """
    if stats.hands < min_hands:
        return "Unknown"

    # --- Determine base style from VPIP / AF grid ---
    style = _classify_base_style(stats.vpip, stats.af)

    # --- Append tendency annotations ---
    tendencies = _get_tendencies(
        stats.fold_to_three_bet, stats.cbet, stats.wtsd
    )

    if tendencies:
        return f"{style}. {' '.join(tendencies)}"
    return style


def _classify_base_style(vpip: int, af: float) -> str:
    """Map VPIP percentage and Aggression Factor to a base style label."""
    if vpip < 20:
        return "Nit"

    if vpip <= 28:
        if af > 2.0:
            return "TAG"
        if af >= 1.0:
            return "Solid/Semi-Aggressive"
        return "Semi-Passive"

    # vpip > 28
    if af >= 1.5:
        return "LAG"
    return "Calling Station"


def _get_tendencies(
    fold_to_three_bet: int, cbet: int, wtsd: int
) -> list[str]:
    """Return tendency annotation strings based on extreme stat values."""
    tendencies: list[str] = []

    if fold_to_three_bet > 70:
        tendencies.append("Folds to pressure.")
    elif fold_to_three_bet < 30:
        tendencies.append("Never folds to 3bet.")

    if cbet > 75:
        tendencies.append("High CBet.")
    elif cbet < 40:
        tendencies.append("Rarely CBets.")

    if wtsd > 35:
        tendencies.append("Showdown bound.")

    return tendencies
