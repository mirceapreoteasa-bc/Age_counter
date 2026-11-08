"""Count rings bay by bay, and never stitch a whole core together.

## Why sections, and not one long strip

The forester's proposed workflow is the right one, and it exists because
stitching measured badly. Overlapping partial views of a long core are
registered to a few percent, and a 7% registration error on a 131 px ring is
about 9 px - enough to shift boundaries, double-count a ring at a seam, or lose
one outright. Every seam is a chance to be wrong, and there is no way for the
app to tell a seam artefact from a real feature.

Counting **within** the printed bays avoids that entirely:

- the bays are at **known positions on the page**, 50.00 mm apart on the v4
  card, so a section is found by geometry rather than by image registration;
- a section boundary is a *discarded* boundary, not a guess. The ring that
  spans the seam is simply not counted, and both sides say so;
- per-section counts are independently checkable by eye, which is exactly what
  the forester wants to do;
- a bad section is isolated. It fails, and the rest still report.

The result is a list of per-section readings with an explicit total, and the
total is only asserted when every section resolved. When one does not, the
total is a **range** or is withheld, with the failing section named.

## What a section report must contain

A count without its context is not auditable. Each section reports the count,
the pitch actually measured, the contrast that justified it, the pitch
disagreement, and a flag saying whether the section is trusted. Two sections
that disagree about ring width are the early warning that one of them is wrong -
and on a real core, ring width *does* vary, so some disagreement is normal and
the question is how much.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from engine import detect as D
from engine import consolidate as C
from engine import ensemble as E

# v4 landscape card geometry, page millimetres.
BAY_PITCH_MM = 50.0
BAYS = 5
BAY_X0_MM = 20.0            # first divider; bays run 20 -> 270 mm
STRIP_Y_MM = 74.0
STRIP_H_MM = 30.0


@dataclass
class SectionReading:
    """One bay, counted independently."""
    label: str                       # "A" .. "E"
    index: int
    count: int = 0
    count_low: int = 0
    count_high: int = 0
    resolved: bool = False
    px_per_mm: float = 0.0
    ring_mm: float = 0.0
    pitch_ratio: float = 0.0         # measured pitch / expected pitch
    contrast: float = 0.0
    spread_pct: float = 0.0          # disagreement between estimators
    boundary_px: list[float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "index": self.index,
            "count": self.count,
            "count_low": self.count_low,
            "count_high": self.count_high,
            "resolved": self.resolved,
            "px_per_mm": round(self.px_per_mm, 3),
            "ring_mm": round(self.ring_mm, 4),
            "pitch_ratio": round(self.pitch_ratio, 3),
            "contrast": round(self.contrast, 4),
            "spread_pct": round(self.spread_pct, 1),
            "n_boundaries": len(self.boundary_px),
            "warnings": list(self.warnings),
            "note": self.note,
        }


@dataclass
class CoreReport:
    """All sections, and a total that is only asserted when it is earned."""
    sections: list[SectionReading] = field(default_factory=list)
    total: Optional[int] = None
    total_low: Optional[int] = None
    total_high: Optional[int] = None
    total_status: str = "unresolved"
    notes: list[str] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        for s in self.sections:
            if s.resolved:
                parts.append(f"{s.label}={s.count}")
            else:
                parts.append(f"{s.label}=unresolved")
        s = ", ".join(parts)
        if self.total is not None:
            s += f"   TOTAL {self.total}"
        else:
            s += f"   total {self.total_status}"
        return s

    def to_dict(self) -> dict:
        d = {"sections": [s.to_dict() for s in self.sections],
             "total": self.total,
             "total_low": self.total_low,
             "total_high": self.total_high,
             "total_status": self.total_status,
             "notes": list(self.notes),
             "questions": list(self.questions)}
        return d


def count_section(gray_strip: np.ndarray, px_per_mm: float, label: str,
                  index: int, expected_ring_mm: float,
                  methods: Optional[list[str]] = None) -> SectionReading:
    """Count one bay.

    gray_strip  the bay's pixels, oriented with the core along axis 0, i.e.
                (length_px, width_px). No registration with any other bay is
                involved, which is the entire point.
    expected_ring_mm  the ring width the forester expects, or the median from
                the other sections. It sizes the consolidation radius and the
                period check; it is not used to invent a count.
    """
    r = SectionReading(label=label, index=index, px_per_mm=px_per_mm)
    if gray_strip is None or gray_strip.size == 0:
        r.warnings.append("no image data for this section")
        return r
    if not px_per_mm or px_per_mm <= 0:
        r.warnings.append("no scale; a count cannot be given in millimetres")
        return r

    L = gray_strip.shape[0]
    ring_px = expected_ring_mm * px_per_mm
    if ring_px < 8:
        r.warnings.append(
            f"expected ring width {expected_ring_mm:.2f} mm is {ring_px:.0f} px "
            f"at {px_per_mm:.1f} px/mm - below the resolvable limit, so this "
            "section cannot be counted")
        return r

    # All estimators on this section alone.
    cmp_ = E.run_all(gray_strip, px_per_mm, 0, gray_strip.shape[1],
                     ring_width_mm=expected_ring_mm, methods=methods)

    # Consolidation is the primary counter: it is the one that reached full
    # recall on the reference core. The rest are shown as cross-checks.
    prof = E._texture_profile(gray_strip, 0, gray_strip.shape[1],
                              win=max(3, int(round(ring_px * 0.05))))
    cands = C.candidates_from_profile(prof)
    con = C.consolidate(cands, expected_ring_px=ring_px)

    usable = [x for x in cmp_.readings if x.resolved and x.count >= 2]
    counts = [x.count for x in usable]
    if con.count_low and con.count_high:
        counts = counts + [con.count_low, con.count_high]

    r.boundary_px = [b.pos for b in con.boundaries]
    r.contrast = con.boundaries[0].strength / max(len(con.boundaries), 1) if con.boundaries else 0.0
    r.spread_pct = (max(counts) - min(counts)) / max(max(counts), 1) * 100.0 if len(counts) > 1 else 0.0

    # measured pitch from the consolidated boundaries
    if len(con.boundaries) > 1:
        span = con.boundaries[-1].pos - con.boundaries[0].pos
        measured = span / (len(con.boundaries) - 1)
        r.ring_mm = measured / px_per_mm
        r.pitch_ratio = r.ring_mm / expected_ring_mm if expected_ring_mm else 0.0
    else:
        # fall back to the estimator that resolved
        if usable:
            r.ring_mm = usable[0].ring_mm
            r.pitch_ratio = r.ring_mm / expected_ring_mm if expected_ring_mm else 0.0

    # decide
    if not con.boundaries:
        r.warnings.append(
            "no boundaries survived consolidation; the boundary pattern is not "
            "separable in this section")
        for w in cmp_.notes[:1]:
            r.warnings.append(w)
        return r

    n = len(con.boundaries)
    if con.resolved:
        r.count = n
        r.count_low = r.count_high = n
        r.resolved = True
    else:
        r.count_low, r.count_high = con.count_low, con.count_high
        r.count = con.count_low
        r.warnings.append(
            f"section not resolved: {con.count_low}-{con.count_high} boundaries; "
            + "; ".join(con.warnings[:1]))

    # The naive profile_peaks estimator carries no information - it returned
    # 166 on the Pixel and 3 on the BV5200 for the same core - so its
    # disagreement must not veto a count. The meaningful cross-check is
    # between the estimators that actually use the boundary signal.
    strong = [x for x in usable if x.method in ("vessel_rows", "comb_fit")]
    if len(strong) > 1:
        sc = [x.count for x in strong]
        real_spread = (max(sc) - min(sc)) / max(max(sc), 1) * 100.0
        r.spread_pct = real_spread
        if real_spread > 35:
            r.warnings.append(
                f"the boundary-based methods disagree by {real_spread:.0f}% in "
                "this section, so the count is a range rather than a figure")
            r.resolved = False
        elif real_spread > 15:
            r.warnings.append(
                f"boundary methods differ by {real_spread:.0f}%; the count is "
                "reported but worth checking by eye")
    else:
        # only one boundary-based method resolved: cross-check on pitch instead
        if r.pitch_ratio and not (0.8 <= r.pitch_ratio <= 1.25):
            r.warnings.append(
                f"measured ring width {r.ring_mm:.2f} mm is "
                f"{r.pitch_ratio:.2f} x the expected {expected_ring_mm:.2f} mm; "
                "check this section by eye")
    return r


def summarise(sections: list[SectionReading],
              expected_total: Optional[int] = None) -> CoreReport:
    """Assemble a core report and decide what may be claimed about the total.

    A total is asserted only when every section resolved and the sections agree
    about ring width. Otherwise a range is given, or nothing, with the failing
    sections named - because a total that silently omits an unresolved bay is
    the exact failure this whole design exists to prevent.
    """
    rep = CoreReport(sections=list(sections))
    resolved = [s for s in sections if s.resolved]
    failed = [s for s in sections if not s.resolved]

    if resolved:
        lo = sum(s.count_low for s in resolved)
        hi = sum(s.count_high for s in resolved)
        rep.total_low, rep.total_high = lo, hi

    if not failed:
        if len(resolved) > 1:
            pitches = [s.ring_mm for s in resolved if s.ring_mm > 0]
            if pitches:
                med = float(np.median(pitches))
                dev = max(abs(p - med) for p in pitches) / med if med else 0.0
                if dev > 0.25:
                    rep.total_status = "unresolved"
                    rep.notes.append(
                        f"sections disagree about ring width by {dev*100:.0f}%, "
                        "so at least one section is wrong; the total is not "
                        "asserted")
                    rep.questions.append({
                        "id": "section_pitch",
                        "question": (
                            "The sections measure different ring widths. Which "
                            "of these sections does not look right to you?"),
                        "options": [f"{s.label} ({s.ring_mm:.2f} mm)"
                                    for s in resolved],
                        "why": "ring width should be broadly consistent, so a "
                               "section that disagrees is suspect",
                        "severity": "high"})
                    return rep
        rep.total = sum(s.count for s in resolved)
        rep.total_status = "counted"
        rep.notes.append(f"all {len(resolved)} sections resolved")
        if expected_total:
            err = (rep.total - expected_total) / expected_total * 100
            rep.notes.append(
                f"you indicated about {expected_total} rings; sections total "
                f"{rep.total} ({err:+.0f}%)")
            rep.questions.append({
                "id": "total_check",
                "question": (
                    f"The sections total {rep.total} rings. Does that match "
                    f"roughly what you count by eye (you indicated about "
                    f"{expected_total})?"),
                "options": [f"about {rep.total}", "clearly more",
                            "clearly fewer"],
                "why": "a per-section total is easier to check by eye than a "
                       "single number for a long core",
                "severity": "medium"})
        return rep

    # some sections failed
    if resolved:
        rep.total_status = "partial"
        rep.notes.append(
            f"{len(resolved)} of {len(sections)} sections resolved; the total "
            f"covers those only ({rep.total_low}-{rep.total_high}) and is NOT "
            "a whole-core count")
        rep.questions.append({
            "id": "missing_sections",
            "question": (
                "These sections could not be counted: "
                + ", ".join(s.label for s in failed)
                + ". Would you rather retake them in better light, or leave "
                  "them out and count the rest?"),
            "options": ["retake the missing sections",
                        "count only the resolved sections",
                        "the core has damaged or missing rings there"],
            "why": "a partial total is useful, but only if it is clearly a "
                   "partial and not presented as the whole core",
            "severity": "high"})
    else:
        rep.total_status = "unresolved"
        rep.notes.append("no section could be counted; no total is offered")
        rep.questions.append({
            "id": "nothing_counted",
            "question": (
                "None of the sections could be counted. The usual causes are "
                "poor light, the core being out of focus, or the boundary "
                "pattern being too fine for this camera."),
            "options": ["retake in daylight", "retake closer to the core",
                        "the core has no visible rings here",
                        "use a better camera"],
            "why": "the app is reporting that it could not see the boundaries, "
                   "which is different from reporting that there are none",
            "severity": "high"})
    return rep
