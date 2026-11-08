"""Find the ring sequence in a candidate list.

## Why this stage exists

The boundary signal is *complete* but *ambiguous*. On the reference conifer
core, every one of the forester's 23 marked boundaries has a candidate within
a third of a ring of it - recall 1.00 - and there are two to four candidates
per ring, because a latewood band has a leading edge, a trailing edge and
cells between them. The problem is therefore not detection. It is deciding
**which candidates are the same ring**.

Three ways of deciding have been measured:

* keep a fixed fraction of the strongest candidates and merge the rest
  (the old rule). It has one free number, tuned on one core, and it cannot
  work on a core with a different candidate density: on the fine-ring core it
  kept 51 of 133 rings.
* group by proximity. The candidates form a continuous chain, so any radius
  bridges neighbours transitively and swallows real rings.
* fit one period. The ring width varies by a factor of two inside a single
  core - measured 1.13 mm at the pith down to 0.53 mm - so a single period is
  not a slightly imprecise model, it is the wrong model.

So this stage does the thing the wood actually does: it walks the core and
lets the spacing follow the wood.

## The model

A sequence of boundaries whose spacing changes slowly. The state is the last
*two* boundaries, because the expected size of the next ring depends on the
size of the last one: juvenile wood near the pith has wide rings, mature wood
has narrow ones, and the change is gradual. With `beta` blending how much of
the last spacing is carried forward, the expected next spacing is

    p = (1 - beta) * expected + beta * (last spacing)

and a candidate is accepted if it lands within `tol` of that, paying a
penalty for the difference. A step that jumps two rings is allowed but pays
for the ring it skipped, because a missing ring makes the count a lower
bound and not a reason to refuse the frame.

The result is a list of boundary positions: the count is the number of
intervals between them, and every position is a place a reviewer can look.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class Chain:
    """The boundaries that were accepted, and what was left out."""

    positions: list[float]                 # accepted boundary positions, px
    strengths: list[float] = None          # strength of each accepted boundary
    skipped: int = 0                       # rings the walk stepped over
    rejected: int = 0                      # candidates not in the sequence
    spacing_px: list[float] = None         # measured spacing between them
    spacing_median_px: float = 0.0

    @property
    def count(self) -> int:
        """Rings between the first and the last boundary."""
        return max(0, len(self.positions) - 1)

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "n_boundaries": len(self.positions),
            "skipped": self.skipped,
            "rejected": self.rejected,
            "spacing_median_px": round(self.spacing_median_px, 2),
            "positions_px": [round(p, 1) for p in self.positions],
        }


def non_max_suppression(cands, window: float) -> list:
    """One candidate per feature: the strongest within each window.

    The candidates are dense - two to four per ring - and the strongest of
    them is the boundary itself, so suppressing the weaker candidates within a
    window keeps the signal and drops the duplicates. Measured on the marked
    conifer core, this leaves every forester mark matched (recall 1.00) with
    55-64 candidates for 23 rings.
    """
    if not cands:
        return []
    order = sorted(cands, key=lambda c: -c.strength)
    kept = []
    for c in order:
        if all(abs(c.pos - k.pos) > window for k in kept):
            kept.append(c)
    return sorted(kept, key=lambda c: c.pos)


def find_chain(cands, expected_px: float, beta: float = 0.5,
               tol: float = 0.45, strength_weight: float = 1.0,
               skip_cost: float = 1.0, max_pitch_mult: float = 2.6,
               min_strength_frac: float = 0.0) -> Chain:
    """Pick the sequence of candidates that looks like a series of rings.

    expected_px   the ring pitch to expect, in px. This is the forester's
                  input (the ring width or the expected ring count); it sizes
                  the walk, it does not produce the count.
    beta          how much of the last measured spacing is carried forward.
                  0 is a fixed pitch, 1 follows the wood completely.
    tol           how far a step may deviate from the expected spacing.
    """
    if not cands or expected_px <= 0:
        return Chain(positions=[])
    cs = sorted(cands, key=lambda c: c.pos)
    pos = np.array([c.pos for c in cs], float)
    st = np.array([c.strength for c in cs], float)
    if min_strength_frac > 0:
        med = float(np.median(st))
        keep = st >= min_strength_frac * med
        if keep.any():
            cs = [c for c, k in zip(cs, keep) if k]
            pos = np.array([c.pos for c in cs], float)
            st = np.array([c.strength for c in cs], float)
    n = len(cs)
    if n == 0:
        return Chain(positions=[])

    med = float(np.median(st)) or 1.0
    w = (st / med) * strength_weight

    # Plain Python lists: the inner loop is executed millions of times on a
    # dense core, and numpy scalar indexing dominates the cost otherwise.
    P = pos.tolist()
    W = w.tolist()
    NEG = -1e18
    dp = [[NEG] * n for _ in range(n)]
    back = [[-1] * n for _ in range(n)]
    for j in range(1, n):
        dp[0][j] = W[j]
    for j in range(1, n):
        pj = P[j]
        for i in range(j):
            base = dp[i][j]
            if base <= NEG / 2:
                continue
            last = pj - P[i]
            # The expected spacing is bounded: a step much larger than the
            # expected pitch is a mis-detection, not a wide ring, and letting
            # it widen the window makes the search quadratic for no reason.
            p = (1.0 - beta) * expected_px + beta * min(last, 2.0 * expected_px)
            if p <= 0:
                continue
            hi = pj + p * max_pitch_mult
            k = j + 1
            while k < n and P[k] <= hi:
                d = P[k] - pj
                r = d / p
                kk = int(round(r))
                if kk < 1:
                    kk = 1
                if kk == 1:
                    pen = abs(r - 1.0)
                else:
                    pen = abs(r - kk) + skip_cost * (kk - 1)
                s = base + W[k] - 2.0 * pen
                if s > dp[j][k]:
                    dp[j][k] = s
                    back[j][k] = i
                k += 1

    best_score = NEG
    bi = bj = -1
    for j in range(1, n):
        for i in range(j):
            if dp[i][j] > best_score:
                best_score = dp[i][j]
                bi, bj = i, j
    if bi < 0:
        return Chain(positions=[])
    idx = [bj, bi]
    i, j = bi, bj
    while True:
        p = back[i][j]
        if p < 0:
            break
        idx.append(p)
        i, j = p, i
        if i == 0:
            break
    idx = sorted(set(idx))

    positions = [float(pos[i]) for i in idx]
    strengths = [float(st[i]) for i in idx]
    spacing = list(np.diff(positions)) if len(positions) > 1 else []
    skipped = 0
    for a, b in zip(idx, idx[1:]):
        d = pos[b] - pos[a]
        prev_d = (pos[b] - pos[a]) if a == 0 else (pos[a] - pos[idx[idx.index(a) - 1]])
        p = (1.0 - beta) * expected_px + beta * max(prev_d, 1.0)
        r = d / max(p, 1e-9)
        skipped += max(0, int(round(r)) - 1)
    return Chain(
        positions=positions,
        strengths=strengths,
        skipped=skipped,
        rejected=n - len(idx),
        spacing_px=[float(s) for s in spacing],
        spacing_median_px=float(np.median(spacing)) if spacing else 0.0,
    )
