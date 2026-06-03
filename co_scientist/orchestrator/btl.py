"""Bradley-Terry-Luce ranking for bounded pairwise tournaments."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable


def rank_btl(
    item_ids: list[str],
    comparisons: Iterable[tuple[str, str, str]],
    *,
    iterations: int = 100,
    eps: float = 1e-9,
) -> dict[str, float]:
    """Estimate BTL strengths from pairwise comparisons.

    `comparisons` contains `(item_a, item_b, winner_id)` tuples. The returned
    dict is ordered from strongest to weakest and normalized onto a log-strength
    scale where an all-tied/no-comparison set returns `0.0` for every item.
    """
    ids = list(dict.fromkeys(item_ids))
    if not ids:
        return {}
    wins = {item_id: 0.0 for item_id in ids}
    pairs: dict[tuple[str, str], int] = {}
    for a, b, winner in comparisons:
        if a not in wins or b not in wins or winner not in wins or a == b:
            continue
        wins[winner] += 1.0
        key = tuple(sorted((a, b)))
        pairs[key] = pairs.get(key, 0) + 1
    if not pairs:
        return {item_id: 0.0 for item_id in ids}

    strengths = {item_id: 1.0 for item_id in ids}
    for _ in range(iterations):
        updated: dict[str, float] = {}
        for item_id in ids:
            denom = 0.0
            for (a, b), n in pairs.items():
                if item_id not in (a, b):
                    continue
                other = b if item_id == a else a
                denom += n / max(strengths[item_id] + strengths[other], eps)
            updated[item_id] = wins[item_id] / max(denom, eps) if wins[item_id] else eps
        mean_strength = sum(updated.values()) / len(updated)
        strengths = {k: v / max(mean_strength, eps) for k, v in updated.items()}

    ordered = sorted(ids, key=lambda item_id: (-strengths[item_id], item_id))
    out: OrderedDict[str, float] = OrderedDict()
    for item_id in ordered:
        out[item_id] = round(strengths[item_id] - 1.0, 6)
    return dict(out)
