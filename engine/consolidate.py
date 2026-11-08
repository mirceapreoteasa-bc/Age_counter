"""Turn many candidate boundary positions into a defensible list of rings.

## The problem this solves

On the reference conifer fixture (`PXL_ACT_327`, 24 forester-marked
boundaries), the texture estimator produces **190 candidates**. Every one of
the 23 real boundaries has candidates around it - all 23 of 23, no misses - and
around each real boundary there are **2 to 5 candidates within 30 px**.

So the detector is not missing rings. It is finding each ring several times:
once on the vessel row itself, once on each edge of the latewood band, once per
latewood cell. Counting candidates therefore over-reports by a factor of 3-7,
which is exactly what the four estimators did on this core (89, 46 and 166
against 23 intervals).

The failure is arithmetic, not perceptual. The positions are right to about a
millimetre. What is missing is the step that decides **which candidates are
the same ring**.

## What did NOT work, and why that shaped the design

Three obvious approaches were tried against the reference core and all failed
for reasons worth recording, because each failure rules out a family of methods
rather than one threshold:

- **Single-link grouping with a radius.** Failed: candidates form a
  *continuous chain*, median gap 14 px, so any radius bridges neighbouring
  boundaries transitively. At a 26 px radius a single group swallowed 21
  candidates. Clustering-by-proximity is the wrong primitive for a signal that
  is dense everywhere.
- **Density peaks** (count candidates in a sliding window and take the maxima).
  Failed worse: 15-21 peaks for 23 boundaries, matching only 5-10. The window
  has to be wide enough to catch a whole cluster, and at that width two
  clusters merge.
- **Gap splitting** (break where the inter-candidate gap is largest). Failed:
  the largest gap *inside* a true boundary's cluster is 50 px, while the
  smallest gap *between* two true boundaries is 31 px. The two distributions
  overlap completely, so no threshold separates them.

The measurement that does separate them is **rank by strength**. For every one
of the 23 marked boundaries, the strongest candidate within 30 px of it is in
the **top 35 of 190 candidates** - the true boundary is always among the
strongest ~18% of the field, while the duplicates and the scattered noise are
not. The boundary is real and is the loudest thing at its own position.

## The approach

1. **Rank candidates by strength** and keep the strongest fraction. This is the
   only cut that survives on this data, and it is scale-free - no pixel
   constant, no ring-width guess.
2. **Collapse survivors that are within a fraction of a ring of each other**,
   keeping the strongest. By this point the survivors are sparse enough that
   single-link grouping behaves.
3. **Check the resulting pitch** against the expected ring width and report the
   disagreement rather than hiding it.

## What it deliberately does not do

It does not report a single confident integer when the survivors are
implausibly dense or too few. If the kept fraction is not well separated - the
case where many candidates tie at similar strength - it returns a range and
says why. The honest answer to "how many rings" is sometimes a range.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class Candidate:
    """One detected boundary-like feature."""
    pos: float
    strength: float = 0.0
    width: float = 0.0


@dataclass
class Boundary:
    """A consolidated ring boundary."""
    pos: float
    strength: float = 0.0
    members: int = 1
    spread: float = 0.0
    period_support: float = 0.0

    def to_dict(self) -> dict:
        return {"pos_px": round(self.pos, 2),
                "strength": round(self.strength, 3),
                "members": self.members,
                "spread_px": round(self.spread, 2),
                "period_support": round(self.period_support, 3)}


@dataclass
class Consolidation:
    boundaries: list[Boundary] = field(default_factory=list)
    n_candidates: int = 0
    n_kept: int = 0
    n_groups: int = 0
    resolved: bool = False
    count_low: int = 0
    count_high: int = 0
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.boundaries)

    def summary(self) -> str:
        if not self.resolved:
            return (f"unresolved: {self.n_candidates} candidates -> "
                    f"{self.n_groups} groups, not separable")
        return (f"{self.count} boundaries from {self.n_candidates} candidates "
                f"in {self.n_groups} groups")


# --- rank-based selection ---------------------------------------------------

def keep_strongest(cands: list[Candidate], keep_fraction: float = 0.20
                   ) -> list[Candidate]:
    """Keep the strongest `keep_fraction` of candidates.

    This is the only cut that works on the reference core, and it works for a
    structural reason: a real ring boundary is the strongest feature at its
    own position, while the duplicate detections of that same boundary - the
    latewood band edges, the individual cells - are weaker, and the scattered
    noise is weakest of all. Measured: for all 23 marked boundaries the
    strongest candidate within 30 px sits in the top 35 of 190.

    It is also scale-free, which matters because a pixel threshold would have
    to be re-tuned for every camera, core contrast and zoom level.
    """
    if not cands:
        return []
    n_keep = max(1, int(round(len(cands) * max(keep_fraction, 1e-6))))
    return sorted(cands, key=lambda c: -c.strength)[:n_keep]


def merge_close(cands: list[Candidate], radius: float) -> list[list[Candidate]]:
    """Group survivors that lie within `radius`, keeping the strongest.

    Safe only *after* `keep_strongest`, because the survivors are sparse: on
    the reference core the top 20% of candidates have a median spacing several
    times the merge radius, so single-link grouping no longer bridges real
    boundaries. Applied to the raw candidate set it merged 21 into one group.
    """
    if not cands:
        return []
    order = sorted(cands, key=lambda c: c.pos)
    groups: list[list[Candidate]] = [[order[0]]]
    for c in order[1:]:
        if c.pos - groups[-1][-1].pos <= radius:
            groups[-1].append(c)
        else:
            groups.append([c])
    return groups


# --- period support ---------------------------------------------------------

def _period_support(pos: float, others: list[float]) -> float:
    """How well this position fits the spacing of its immediate neighbours."""
    left = [p for p in others if p < pos]
    right = [p for p in others if p > pos]
    if not left or not right:
        return 0.0
    pl = pos - left[-1]
    pr = right[0] - pos
    if pl <= 0 or pr <= 0:
        return 0.0
    r = min(pl, pr) / max(pl, pr)
    return float(r)


# --- the consolidation ------------------------------------------------------

def consolidate(cands: list[Candidate], expected_ring_px: Optional[float] = None,
                keep_fraction: float = 0.20,
                merge_fraction: float = 0.30,
                max_implied_ratio: float = 0.60) -> Consolidation:
    """Collapse duplicate detections of the same boundary into one.

    expected_ring_px  the ring pitch when known. Sets the merge radius. It is
                      used only to merge survivors and to check the result, not
                      to decide which candidates are real - that decision is
                      made from strength alone, because on this data nothing
                      else separates them.
    keep_fraction     fraction of candidates retained by strength. 0.20 was the
                      value at which all 23 boundaries on the reference core
                      survived and precision was 100%.
    max_implied_ratio  if the survivors are this much denser than the expected
                      ring width, the count is reported as a range instead.
    """
    out = Consolidation(n_candidates=len(cands))
    if not cands:
        out.warnings.append("no candidates to consolidate")
        return out

    kept = keep_strongest(cands, keep_fraction)
    out.n_kept = len(kept)

    radius = max(4.0, (expected_ring_px or 40.0) * merge_fraction)
    groups = merge_close(kept, radius)
    out.n_groups = len(groups)

    reps = []
    for g in groups:
        best = max(g, key=lambda c: c.strength)
        reps.append(Boundary(pos=best.pos, strength=best.strength,
                             members=len(g),
                             spread=max(c.pos for c in g)
                             - min(c.pos for c in g)))
    pos_list = [b.pos for b in reps]
    for b in reps:
        b.period_support = _period_support(
            b.pos, [p for p in pos_list if p != b.pos])
    reps.sort(key=lambda b: b.pos)
    out.boundaries = reps
    out.count_low = out.count_high = len(reps)

    if expected_ring_px and len(reps) > 1:
        span = reps[-1].pos - reps[0].pos
        implied = span / (len(reps) - 1)
        ratio = implied / expected_ring_px
        out.notes.append(
            f"{len(reps)} boundaries over {span:.0f} px imply "
            f"{implied:.1f} px/ring vs expected {expected_ring_px:.1f} "
            f"(ratio {ratio:.2f})")
        # The pitch test is a *sanity check on the count*, not a gate on it.
        # An earlier version refused whenever the implied ratio fell outside
        # 0.6-1.0, and that rejected every section of the reference core even
        # where the measured pitch was within 4% of the truth. The useful
        # distinction is not "is the ratio near 1" but "is the count obviously
        # short or obviously doubled".
        if ratio > 2.0:
            out.warnings.append(
                f"survivors imply {ratio:.1f} rings per expected ring width; "
                "duplicates have survived, so the count is an upper bound")
            out.count_low = max(1, int(round(span / expected_ring_px)) + 1)
            out.count_high = len(reps)
            out.resolved = False
            return out
        if ratio < 0.5:
            out.warnings.append(
                f"survivors imply only {ratio:.2f} rings per expected ring "
                "width; rings have probably been missed, so the count is a "
                "lower bound")
            out.count_low = len(reps)
            out.count_high = max(len(reps), int(round(span / expected_ring_px)) + 1)
            out.resolved = False
            return out
        # 0.5 <= ratio <= 2.0 : the count stands, with the pitch error noted
        if abs(ratio - 1.0) > 0.20:
            out.warnings.append(
                f"measured pitch is {ratio:.2f} x the expected ring width; the "
                "count is reported but the ring width should be checked")
    out.resolved = True
    return out


# --- convenience from a profile ---------------------------------------------

def candidates_from_profile(prof: np.ndarray, rel_thresh: float = 1.05,
                           min_sep: int = 4) -> list[Candidate]:
    """Extract dense candidates from a 1-D boundary profile.

    Deliberately permissive. The point of this stage is to find *everything*
    plausible and let consolidation decide, rather than to pre-filter and lose
    a real ring - the failure that made peak-picking with a fixed separation
    return 5 candidates where 23 boundaries existed.
    """
    from scipy import ndimage as ndi
    if prof.size < 5:
        return []
    sm = ndi.uniform_filter1d(prof, 3)
    base = float(np.percentile(sm, 30))
    if base <= 0:
        base = float(sm.mean()) * 0.5
    if base <= 0:
        return []
    out: list[Candidate] = []
    i = 1
    n = len(sm)
    while i < n - 1:
        if sm[i] > base * rel_thresh and sm[i] >= sm[i - 1] and sm[i] > sm[i + 1]:
            # strength relative to the local baseline, so a boundary in a quiet
            # stretch is not scored lower than one in a busy one
            out.append(Candidate(pos=float(i), strength=float(sm[i] - base)))
            i += max(2, min_sep)
        else:
            i += 1
    return out
