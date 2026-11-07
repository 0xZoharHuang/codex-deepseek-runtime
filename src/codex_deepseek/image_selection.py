"""Pure lossless image selection under joint byte and reference budgets."""
from dataclasses import dataclass
from functools import lru_cache


class SelectionComplexityExceeded(RuntimeError):
    """A capped search cannot establish feasibility."""


@dataclass(frozen=True)
class Selection:
    indices: tuple[int, ...]
    savings: int
    referenced_bytes: int
    feasible: bool
    search_nodes: int = 0
    maximum_savings_proven: bool = False


def select_positions(items, slots, referenced_budget, required_savings=None, *, max_search_nodes=200_000):
    """Select (raw occurrence bytes, wire savings) in original encounter order.

    With a wire goal, stop at the first feasible plan. With no wire goal, retain
    the prior savings-first strategy within the aggregate byte budget.
    """
    if slots < 0 or referenced_budget < 0:
        return Selection((), 0, 0, False)
    ranked = sorted(((cost, saving, index) for index, (cost, saving) in enumerate(items)
                     if saving > 0 and 0 < cost <= referenced_budget),
                    key=lambda row: (-row[1], row[2]))
    top_savings = sum(row[1] for row in ranked[:slots])
    if required_savings is not None and required_savings <= 0:
        return Selection((), 0, 0, True, maximum_savings_proven=top_savings == 0)
    picked, total, used = [], 0, 0
    for cost, saving, index in ranked:
        if len(picked) == slots:
            break
        if used + cost <= referenced_budget:
            picked.append(index)
            used += cost
            total += saving
            if required_savings is not None and total >= required_savings:
                return Selection(tuple(sorted(picked)), total, used, True,
                                 maximum_savings_proven=total == top_savings)
    if required_savings is None or slots == 0 or not ranked:
        return Selection(tuple(sorted(picked)), total, used, required_savings is None,
                         maximum_savings_proven=total == top_savings)

    prefix = [0]
    for _, saving, _ in ranked:
        prefix.append(prefix[-1] + saving)
    n = len(ranked)
    # Rational density bound avoids an exclusion caused by rounding.
    density = [None] * n
    best = None
    for index in range(n - 1, -1, -1):
        cost, saving, _ = ranked[index]
        if best is None or saving * best[0] > best[1] * cost:
            best = (cost, saving)
        density[index] = best
    nodes = 0
    work = 0

    def charge_work():
        nonlocal work
        work += 1
        if work > max_search_nodes:
            raise SelectionComplexityExceeded('Exact image selection work budget exhausted; feasibility is unknown')

    @lru_cache(maxsize=max_search_nodes)
    def search(start, remaining_slots, budget, need):
        nonlocal nodes
        charge_work()
        nodes += 1
        if remaining_slots <= 0 or start >= n or budget <= 0:
            return None
        end = min(n, start + remaining_slots)
        if prefix[end] - prefix[start] < need:
            return None
        cost0, saving0 = density[start]
        if (budget * saving0) // cost0 < need:
            return None
        for current in range(start, n):
            cost, saving, original = ranked[current]
            end = min(n, current + remaining_slots)
            if prefix[end] - prefix[current] < need:
                break
            charge_work()
            if cost > budget:
                continue
            if saving >= need:
                return (original,)
            suffix = search(current + 1, remaining_slots - 1, budget - cost, need - saving)
            if suffix is not None:
                return (original, *suffix)
        return None

    found = search(0, min(slots, n), referenced_budget, required_savings)
    if found is None:
        return Selection(tuple(sorted(picked)), total, used, False, nodes,
                         maximum_savings_proven=total == top_savings)
    found = tuple(sorted(found))
    savings = sum(items[i][1] for i in found)
    return Selection(found, savings, sum(items[i][0] for i in found), True, nodes,
                     maximum_savings_proven=savings == top_savings)
