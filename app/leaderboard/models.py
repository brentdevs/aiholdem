from dataclasses import dataclass


@dataclass
class GameResult:
    model_id: str  # e.g. "google/gemini-2.5-flash"
    display_name: str  # e.g. "Gemini-2.5-flash"
    placing: int  # 1 = winner, 2 = second, etc.
    api_calls: int  # total API calls this game
    api_failures: int  # total failures/timeouts this game
    total_latency_ms: int  # sum of response latencies in ms this game


def compute_placings(elimination_order: list[str]) -> dict[str, int]:
    """Compute placings from elimination order.

    Args:
        elimination_order: Player IDs ordered from first eliminated to last
            standing. elimination_order[-1] is the winner (placing=1),
            elimination_order[0] is first eliminated (placing=N).

    Returns:
        Dict mapping player_id to placing (1 = winner, N = first eliminated).
    """
    n = len(elimination_order)
    return {player_id: n - i for i, player_id in enumerate(elimination_order)}
