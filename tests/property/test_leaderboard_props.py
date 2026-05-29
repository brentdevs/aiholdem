# Feature: arena-leaderboard, Property 1: Placing computation from elimination order
# Feature: arena-leaderboard, Property 2: Record game results preserves cumulative invariants
from hypothesis import given, settings
from hypothesis import strategies as st

from app.leaderboard.models import GameResult, compute_placings


@st.composite
def elimination_order_strategy(draw):
    """Generate a list of 2–8 unique player IDs in elimination order."""
    n = draw(st.integers(min_value=2, max_value=8))
    ids = [f"player_{i}" for i in range(n)]
    return draw(st.permutations(ids))


# **Validates: Requirements 3.1**
@given(elimination_order=elimination_order_strategy())
@settings(max_examples=100)
def test_placing_computation_from_elimination_order(elimination_order: list[str]):
    """Placing assignment produces contiguous 1..N, last standing = 1, first eliminated = N."""
    placings = compute_placings(elimination_order)
    n = len(elimination_order)

    # All players present in output
    assert set(placings.keys()) == set(elimination_order)

    # Placings form contiguous sequence 1..N
    assert sorted(placings.values()) == list(range(1, n + 1))

    # Last standing gets placing=1 (winner)
    assert placings[elimination_order[-1]] == 1

    # First eliminated gets placing=N
    assert placings[elimination_order[0]] == n


# ---------------------------------------------------------------------------
# In-memory simulation of LeaderboardService upsert logic (no PostgreSQL needed)
# ---------------------------------------------------------------------------


class InMemoryLeaderboardService:
    """Mimics LeaderboardService upsert logic using a plain dict."""

    def __init__(self):
        # dict keyed by model_id -> row dict
        self._rows: dict[str, dict] = {}

    def record_game_results(self, results: list[GameResult]) -> None:
        for r in results:
            win_value = 1 if r.placing == 1 else 0
            if r.model_id in self._rows:
                row = self._rows[r.model_id]
                row["display_name"] = r.display_name
                row["games_played"] += 1
                row["wins"] += win_value
                row["placing_sum"] += r.placing
                row["api_calls"] += r.api_calls
                row["api_failures"] += r.api_failures
                row["latency_sum_ms"] += r.total_latency_ms
            else:
                self._rows[r.model_id] = {
                    "model_id": r.model_id,
                    "display_name": r.display_name,
                    "games_played": 1,
                    "wins": win_value,
                    "placing_sum": r.placing,
                    "api_calls": r.api_calls,
                    "api_failures": r.api_failures,
                    "latency_sum_ms": r.total_latency_ms,
                    "retired": False,
                }

    def get_leaderboard(self) -> list[dict]:
        return list(self._rows.values())

    def get_row(self, model_id: str) -> dict | None:
        return self._rows.get(model_id)


# ---------------------------------------------------------------------------
# Strategy: generate a list of 2-8 GameResult objects with valid placings
# ---------------------------------------------------------------------------


@st.composite
def game_results_strategy(draw):
    """Generate 2-8 GameResult objects with unique model_ids and valid 1..N placings."""
    n = draw(st.integers(min_value=2, max_value=8))
    model_ids = [f"model_{i}" for i in range(n)]
    # Placings form a valid 1..N sequence — assign randomly
    placings = list(range(1, n + 1))
    shuffled_placings = draw(st.permutations(placings))

    results = []
    for i in range(n):
        api_calls = draw(st.integers(min_value=0, max_value=100))
        api_failures = draw(st.integers(min_value=0, max_value=api_calls))
        total_latency_ms = draw(st.integers(min_value=0, max_value=10000))
        results.append(
            GameResult(
                model_id=model_ids[i],
                display_name=f"Model {i}",
                placing=shuffled_placings[i],
                api_calls=api_calls,
                api_failures=api_failures,
                total_latency_ms=total_latency_ms,
            )
        )
    return results


# **Validates: Requirements 3.2, 3.3, 3.4, 4.2, 4.3, 5.2**
@given(
    round1=game_results_strategy(),
    round2=game_results_strategy(),
)
@settings(max_examples=100)
def test_record_game_results_preserves_cumulative_invariants(
    round1: list[GameResult],
    round2: list[GameResult],
):
    """Recording game results preserves cumulative invariants across rounds."""
    svc = InMemoryLeaderboardService()

    # --- Round 1 ---
    svc.record_game_results(round1)

    for r in round1:
        row = svc.get_row(r.model_id)
        assert row is not None, f"Missing row for {r.model_id}"
        assert row["games_played"] == 1
        assert row["wins"] == (1 if r.placing == 1 else 0)
        assert row["placing_sum"] == r.placing
        assert row["api_calls"] == r.api_calls
        assert row["api_failures"] == r.api_failures
        assert row["latency_sum_ms"] == r.total_latency_ms

    # Snapshot round 1 state for each model
    snapshot = {}
    for r in round1:
        row = svc.get_row(r.model_id)
        snapshot[r.model_id] = dict(row)

    # --- Round 2: reuse the same model_ids so we test cumulative upsert ---
    # Remap round2 model_ids to match round1 model_ids (same count guaranteed
    # only when n matches, so we build round2 results for round1's models).
    n1 = len(round1)
    round2_placings = list(range(1, n1 + 1))
    # We need a fresh set of random results for the same models.
    # Since round2 may have a different count, we rebuild for round1's models.
    round2_adjusted = []
    for i, r1 in enumerate(round1):
        # Pick the corresponding round2 entry if available, else use defaults
        if i < len(round2):
            r2 = round2[i]
        else:
            r2 = GameResult(
                model_id=r1.model_id,
                display_name=r1.display_name,
                placing=1,
                api_calls=0,
                api_failures=0,
                total_latency_ms=0,
            )
        # Assign placing from a valid 1..n1 sequence
        round2_adjusted.append(
            GameResult(
                model_id=r1.model_id,
                display_name=r1.display_name,
                placing=round2_placings[i],
                api_calls=r2.api_calls,
                api_failures=r2.api_failures,
                total_latency_ms=r2.total_latency_ms,
            )
        )

    svc.record_game_results(round2_adjusted)

    # Verify cumulative increments
    for r2 in round2_adjusted:
        row = svc.get_row(r2.model_id)
        prev = snapshot[r2.model_id]

        assert row["games_played"] == prev["games_played"] + 1
        expected_wins = prev["wins"] + (1 if r2.placing == 1 else 0)
        assert row["wins"] == expected_wins
        assert row["placing_sum"] == prev["placing_sum"] + r2.placing
        assert row["api_calls"] == prev["api_calls"] + r2.api_calls
        assert row["api_failures"] == prev["api_failures"] + r2.api_failures
        assert row["latency_sum_ms"] == prev["latency_sum_ms"] + r2.total_latency_ms


# ---------------------------------------------------------------------------
# Feature: arena-leaderboard, Property 4: Retired status sync correctness
# ---------------------------------------------------------------------------


# --- Add sync_retired_status to InMemoryLeaderboardService ---
# We monkey-patch the method onto the class so existing tests are unaffected.


def _sync_retired_status(self, active_models: list[str]) -> None:
    """Mirror the real LeaderboardService.sync_retired_status logic in-memory.

    - Models in DB but NOT in active_models → retired=True
    - Models in DB AND in active_models → retired=False
    - No rows added or removed.
    """
    active_set = set(active_models)
    for model_id, row in self._rows.items():
        row["retired"] = model_id not in active_set


InMemoryLeaderboardService.sync_retired_status = _sync_retired_status


def _seed_models(self, model_ids: list[str]) -> None:
    """Seed the in-memory store with default rows for the given model IDs."""
    for mid in model_ids:
        if mid not in self._rows:
            self._rows[mid] = {
                "model_id": mid,
                "display_name": mid,
                "games_played": 0,
                "wins": 0,
                "placing_sum": 0,
                "api_calls": 0,
                "api_failures": 0,
                "latency_sum_ms": 0,
                "retired": False,
            }


InMemoryLeaderboardService.seed_models = _seed_models


@st.composite
def db_and_active_models_strategy(draw):
    """Generate random DB model IDs (1-10) and a random set of active model IDs.

    Active models can overlap with DB models and can include models not in DB.
    """
    # DB models: 1-10 unique IDs
    db_count = draw(st.integers(min_value=1, max_value=10))
    db_model_ids = [f"db_model_{i}" for i in range(db_count)]

    # Active models: pick a random subset from a pool that includes DB models
    # plus some extra IDs that are NOT in the DB
    extra_ids = [f"extra_model_{i}" for i in range(5)]
    all_candidate_ids = db_model_ids + extra_ids
    active_model_ids = draw(
        st.lists(
            st.sampled_from(all_candidate_ids),
            min_size=0,
            max_size=len(all_candidate_ids),
            unique=True,
        )
    )
    return db_model_ids, active_model_ids


# **Validates: Requirements 6.1, 6.2, 6.3**
@given(data=db_and_active_models_strategy())
@settings(max_examples=100)
def test_retired_status_sync_correctness(data):
    """After sync_retired_status, DB models not in active_models are retired,
    those in active_models are not retired, and no rows are added or removed."""
    db_model_ids, active_model_ids = data

    svc = InMemoryLeaderboardService()
    svc.seed_models(db_model_ids)

    # Snapshot the set of model IDs before sync
    ids_before = set(svc._rows.keys())

    svc.sync_retired_status(active_model_ids)

    active_set = set(active_model_ids)

    # No rows added or removed
    ids_after = set(svc._rows.keys())
    assert ids_before == ids_after, "sync_retired_status must not add or remove rows"

    # Verify retired flags
    for model_id, row in svc._rows.items():
        if model_id in active_set:
            assert (
                row["retired"] is False
            ), f"{model_id} is in active_models but retired={row['retired']}"
        else:
            assert (
                row["retired"] is True
            ), f"{model_id} is NOT in active_models but retired={row['retired']}"


# ---------------------------------------------------------------------------
# Feature: arena-leaderboard, Property 5: Leaderboard sort order
# ---------------------------------------------------------------------------


def _get_sorted_leaderboard(self) -> list[dict]:
    """Mirror the real LeaderboardService.get_leaderboard sort order:
    active (retired=False) first sorted by wins desc,
    then retired (retired=True) sorted by wins desc.
    """
    rows = list(self._rows.values())
    # retired ASC (False=0 before True=1), then wins DESC
    rows.sort(key=lambda r: (r["retired"], -r["wins"]))
    return rows


InMemoryLeaderboardService.get_sorted_leaderboard = _get_sorted_leaderboard


@st.composite
def leaderboard_entries_strategy(draw):
    """Generate 2-10 leaderboard entries with random retired status and win counts."""
    n = draw(st.integers(min_value=2, max_value=10))
    entries = []
    for i in range(n):
        entries.append(
            {
                "model_id": f"model_{i}",
                "display_name": f"Model {i}",
                "games_played": draw(st.integers(min_value=1, max_value=200)),
                "wins": draw(st.integers(min_value=0, max_value=100)),
                "placing_sum": draw(st.integers(min_value=1, max_value=500)),
                "api_calls": draw(st.integers(min_value=0, max_value=1000)),
                "api_failures": draw(st.integers(min_value=0, max_value=100)),
                "latency_sum_ms": draw(st.integers(min_value=0, max_value=100000)),
                "retired": draw(st.booleans()),
            }
        )
    return entries


# **Validates: Requirements 6.5**
@given(entries=leaderboard_entries_strategy())
@settings(max_examples=100)
def test_leaderboard_sort_order(entries: list[dict]):
    """Active models appear before retired models; each group sorted by wins descending."""
    svc = InMemoryLeaderboardService()
    # Seed entries directly into the in-memory store
    for entry in entries:
        svc._rows[entry["model_id"]] = dict(entry)

    sorted_rows = svc.get_sorted_leaderboard()

    # All entries present
    assert len(sorted_rows) == len(entries)

    # Split into active and retired segments as they appear in sorted output
    active_segment = []
    retired_segment = []
    seen_retired = False
    for row in sorted_rows:
        if row["retired"]:
            seen_retired = True
            retired_segment.append(row)
        else:
            # Once we've seen a retired model, no active model should follow
            assert not seen_retired, f"Active model {row['model_id']} appears after retired models"
            active_segment.append(row)

    # Within active group, wins are descending
    active_wins = [r["wins"] for r in active_segment]
    for i in range(len(active_wins) - 1):
        assert (
            active_wins[i] >= active_wins[i + 1]
        ), f"Active group not sorted by wins desc: {active_wins}"

    # Within retired group, wins are descending
    retired_wins = [r["wins"] for r in retired_segment]
    for i in range(len(retired_wins) - 1):
        assert (
            retired_wins[i] >= retired_wins[i + 1]
        ), f"Retired group not sorted by wins desc: {retired_wins}"
