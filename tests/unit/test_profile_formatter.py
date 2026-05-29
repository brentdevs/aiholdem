"""Unit tests for app.profiling.profile_formatter."""

from app.profiling.profile_formatter import format_profile, format_profiles_block
from app.profiling.stat_aggregator import PlayerStats


def _make_stats(**overrides) -> PlayerStats:
    defaults = dict(
        hands=50,
        vpip=25,
        pfr=18,
        af=2.3,
        three_bet=7,
        fold_to_three_bet=55,
        cbet=65,
        fold_to_cbet=40,
        wtsd=30,
    )
    defaults.update(overrides)
    return PlayerStats(**defaults)


class TestFormatProfile:
    def test_sufficient_hands_format(self):
        stats = _make_stats()
        result = format_profile("P1", stats, "TAG")
        assert result == "P1: VPIP=25% PFR=18% AF=2.3 3bet=7% CBet=65% WTSD=30% | TAG"

    def test_style_with_tendencies(self):
        stats = _make_stats()
        result = format_profile("P2", stats, "TAG. Folds to pressure. High CBet.")
        assert "| TAG. Folds to pressure. High CBet." in result

    def test_unknown_below_min_hands(self):
        stats = _make_stats(hands=5)
        result = format_profile("P3", stats, "Unknown", min_hands=10)
        assert result == "P3: Unknown (5 hands)"

    def test_unknown_when_stats_none(self):
        result = format_profile("P4", None, "Unknown")
        assert result == "P4: Unknown (0 hands)"

    def test_self_marker(self):
        stats = _make_stats()
        result = format_profile("P1", stats, "TAG", is_self=True)
        assert result.startswith("P1 (YOU): ")

    def test_self_marker_unknown(self):
        result = format_profile("P1", None, "Unknown", is_self=True)
        assert result == "P1 (YOU): Unknown (0 hands)"

    def test_af_one_decimal(self):
        stats = _make_stats(af=0.0)
        result = format_profile("P1", stats, "Nit")
        assert "AF=0.0" in result

    def test_zero_stats(self):
        stats = _make_stats(vpip=0, pfr=0, af=0.0, three_bet=0, cbet=0, wtsd=0)
        result = format_profile("P1", stats, "Nit")
        assert "VPIP=0%" in result
        assert "PFR=0%" in result


class TestFormatProfilesBlock:
    def test_empty_profiles(self):
        assert format_profiles_block([]) == ""

    def test_profiles_only(self):
        profiles = ["P1: VPIP=25% ...", "P2: Unknown (3 hands)"]
        result = format_profiles_block(profiles)
        assert result.startswith("Player Profiles:")
        assert "P1: VPIP=25% ..." in result
        assert "P2: Unknown (3 hands)" in result

    def test_profiles_with_recent_hands(self):
        profiles = ["P1: VPIP=25% ..."]
        hands = ["Hand #5: P1 raised, P2 folded"]
        result = format_profiles_block(profiles, recent_hands=hands)
        assert "Player Profiles:" in result
        assert "Recent Hands:" in result
        assert "Hand #5: P1 raised, P2 folded" in result

    def test_no_recent_hands_section_when_none(self):
        profiles = ["P1: VPIP=25% ..."]
        result = format_profiles_block(profiles, recent_hands=None)
        assert "Recent Hands:" not in result

    def test_no_recent_hands_section_when_empty(self):
        profiles = ["P1: VPIP=25% ..."]
        result = format_profiles_block(profiles, recent_hands=[])
        assert "Recent Hands:" not in result

    def test_token_budget_8_players(self):
        """Verify 8-player profile block stays within 700-900 token estimate."""
        profiles = []
        for i in range(8):
            stats = _make_stats()
            style = "TAG. Folds to pressure. High CBet."
            profiles.append(format_profile(f"P{i+1}", stats, style))
        block = format_profiles_block(profiles)
        estimated_tokens = len(block) / 4
        assert (
            100 <= estimated_tokens <= 900
        ), f"Token estimate {estimated_tokens:.0f} outside acceptable range"
