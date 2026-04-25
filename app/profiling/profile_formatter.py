"""Profile formatting for AI prompt injection.

Renders per-player profile strings and combines them into a compact
prompt block for injection into the AI decision prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.profiling.stat_aggregator import PlayerStats


def format_profile(
    label: str,
    stats: PlayerStats | None,
    style: str,
    is_self: bool = False,
    min_hands: int = 10,
) -> str:
    """Format a single player's profile string.

    If stats is None or stats.hands < min_hands:
        return "<label>: Unknown (N hands)"
    Otherwise:
        return "<label>: VPIP=N% PFR=N% AF=N.N 3bet=N% CBet=N% WTSD=N% | <style>"
    If is_self, prepend "(YOU)" to the label.
    """
    display_label = f"{label} (YOU)" if is_self else label

    if stats is None or stats.hands < min_hands:
        hand_count = stats.hands if stats is not None else 0
        return f"{display_label}: Unknown ({hand_count} hands)"

    return (
        f"{display_label}: "
        f"VPIP={stats.vpip}% "
        f"PFR={stats.pfr}% "
        f"AF={stats.af:.1f} "
        f"3bet={stats.three_bet}% "
        f"CBet={stats.cbet}% "
        f"WTSD={stats.wtsd}% "
        f"| {style}"
    )


def format_profiles_block(
    profiles: list[str],
    recent_hands: list[str] | None = None,
) -> str:
    """Combine profile strings and optional recent hand summaries into a prompt block."""
    if not profiles:
        return ""

    sections: list[str] = []
    sections.append("Player Profiles:")
    sections.extend(profiles)

    if recent_hands:
        sections.append("")
        sections.append("Recent Hands:")
        sections.extend(recent_hands)

    return "\n".join(sections)
