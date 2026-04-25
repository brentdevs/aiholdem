"""Unit tests for app.profiling.style_classifier.classify_style."""

from dataclasses import dataclass

from app.profiling.style_classifier import classify_style


@dataclass
class FakeStats:
    """Minimal stand-in for PlayerStats."""

    hands: int = 50
    vpip: int = 25
    pfr: int = 15
    af: float = 2.5
    three_bet: int = 8
    fold_to_three_bet: int = 50
    cbet: int = 60
    fold_to_cbet: int = 50
    wtsd: int = 25


# --- Unknown for insufficient hands ---


def test_unknown_below_min_hands():
    stats = FakeStats(hands=5)
    assert classify_style(stats) == "Unknown"


def test_unknown_at_zero_hands():
    stats = FakeStats(hands=0)
    assert classify_style(stats) == "Unknown"


def test_unknown_custom_min_hands():
    stats = FakeStats(hands=15)
    assert classify_style(stats, min_hands=20) == "Unknown"


def test_known_at_exact_min_hands():
    stats = FakeStats(hands=10, vpip=15, af=3.0, fold_to_three_bet=50, cbet=60, wtsd=25)
    result = classify_style(stats)
    assert result != "Unknown"


# --- Base style: Nit (VPIP < 20%) ---


def test_nit_high_af():
    stats = FakeStats(vpip=15, af=3.0, fold_to_three_bet=50, cbet=60, wtsd=25)
    assert classify_style(stats) == "Nit"


def test_nit_low_af():
    stats = FakeStats(vpip=10, af=1.0, fold_to_three_bet=50, cbet=60, wtsd=25)
    assert classify_style(stats) == "Nit"


def test_nit_zero_vpip():
    stats = FakeStats(vpip=0, af=0.0, fold_to_three_bet=50, cbet=60, wtsd=25)
    assert classify_style(stats) == "Nit"


def test_nit_vpip_19():
    stats = FakeStats(vpip=19, af=2.0, fold_to_three_bet=50, cbet=60, wtsd=25)
    assert classify_style(stats) == "Nit"


# --- Base style: TAG (VPIP 20-28%, AF > 2.0) ---


def test_tag():
    stats = FakeStats(vpip=22, af=2.5, fold_to_three_bet=50, cbet=60, wtsd=25)
    assert classify_style(stats) == "TAG"


def test_tag_boundary_vpip_20():
    stats = FakeStats(vpip=20, af=3.0, fold_to_three_bet=50, cbet=60, wtsd=25)
    assert classify_style(stats) == "TAG"


def test_tag_boundary_vpip_28():
    stats = FakeStats(vpip=28, af=2.1, fold_to_three_bet=50, cbet=60, wtsd=25)
    assert classify_style(stats) == "TAG"


# --- Base style: Solid/Semi-Aggressive (VPIP 20-28%, AF 1.0-2.0) ---


def test_solid_semi_aggressive():
    stats = FakeStats(vpip=24, af=1.5, fold_to_three_bet=50, cbet=60, wtsd=25)
    assert classify_style(stats) == "Solid/Semi-Aggressive"


def test_solid_semi_aggressive_af_boundary_low():
    stats = FakeStats(vpip=24, af=1.0, fold_to_three_bet=50, cbet=60, wtsd=25)
    assert classify_style(stats) == "Solid/Semi-Aggressive"


def test_solid_semi_aggressive_af_boundary_high():
    stats = FakeStats(vpip=24, af=2.0, fold_to_three_bet=50, cbet=60, wtsd=25)
    assert classify_style(stats) == "Solid/Semi-Aggressive"


# --- Base style: Semi-Passive (VPIP 20-28%, AF < 1.0) ---


def test_semi_passive():
    stats = FakeStats(vpip=25, af=0.5, fold_to_three_bet=50, cbet=60, wtsd=25)
    assert classify_style(stats) == "Semi-Passive"


def test_semi_passive_zero_af():
    stats = FakeStats(vpip=20, af=0.0, fold_to_three_bet=50, cbet=60, wtsd=25)
    assert classify_style(stats) == "Semi-Passive"


# --- Base style: LAG (VPIP > 28%, AF > 2.0 or AF 1.5-2.0) ---


def test_lag_high_af():
    stats = FakeStats(vpip=35, af=3.0, fold_to_three_bet=50, cbet=60, wtsd=25)
    assert classify_style(stats) == "LAG"


def test_lag_af_1_5():
    stats = FakeStats(vpip=30, af=1.5, fold_to_three_bet=50, cbet=60, wtsd=25)
    assert classify_style(stats) == "LAG"


def test_lag_af_2_0():
    stats = FakeStats(vpip=40, af=2.0, fold_to_three_bet=50, cbet=60, wtsd=25)
    assert classify_style(stats) == "LAG"


# --- Base style: Calling Station (VPIP > 28%, AF < 1.5) ---


def test_calling_station():
    stats = FakeStats(vpip=40, af=0.8, fold_to_three_bet=50, cbet=60, wtsd=25)
    assert classify_style(stats) == "Calling Station"


def test_calling_station_af_1_4():
    stats = FakeStats(vpip=29, af=1.4, fold_to_three_bet=50, cbet=60, wtsd=25)
    assert classify_style(stats) == "Calling Station"


# --- Tendency annotations ---


def test_folds_to_pressure():
    stats = FakeStats(vpip=22, af=2.5, fold_to_three_bet=75, cbet=60, wtsd=25)
    result = classify_style(stats)
    assert result.startswith("TAG.")
    assert "Folds to pressure." in result


def test_never_folds_to_3bet():
    stats = FakeStats(vpip=22, af=2.5, fold_to_three_bet=20, cbet=60, wtsd=25)
    result = classify_style(stats)
    assert "Never folds to 3bet." in result


def test_high_cbet():
    stats = FakeStats(vpip=22, af=2.5, fold_to_three_bet=50, cbet=80, wtsd=25)
    result = classify_style(stats)
    assert "High CBet." in result


def test_rarely_cbets():
    stats = FakeStats(vpip=22, af=2.5, fold_to_three_bet=50, cbet=35, wtsd=25)
    result = classify_style(stats)
    assert "Rarely CBets." in result


def test_showdown_bound():
    stats = FakeStats(vpip=22, af=2.5, fold_to_three_bet=50, cbet=60, wtsd=40)
    result = classify_style(stats)
    assert "Showdown bound." in result


def test_multiple_tendencies():
    stats = FakeStats(vpip=35, af=3.0, fold_to_three_bet=80, cbet=80, wtsd=40)
    result = classify_style(stats)
    assert "LAG." in result
    assert "Folds to pressure." in result
    assert "High CBet." in result
    assert "Showdown bound." in result


def test_no_tendencies_at_boundary():
    """Boundary values that should NOT trigger annotations."""
    stats = FakeStats(vpip=22, af=2.5, fold_to_three_bet=70, cbet=40, wtsd=35)
    result = classify_style(stats)
    assert result == "TAG"  # No annotations appended


def test_tendencies_not_appended_when_unknown():
    """Unknown should be returned without tendencies even if stats are extreme."""
    stats = FakeStats(hands=3, vpip=35, af=3.0, fold_to_three_bet=80, cbet=80, wtsd=40)
    assert classify_style(stats) == "Unknown"
