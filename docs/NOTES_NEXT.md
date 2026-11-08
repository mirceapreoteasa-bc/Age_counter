# AgeCoreApp — notes for next work (recorded 2026-10-27)

Not to implement yet. Pick up when better samples are available.

## 1. More than one strategy per core
- Use different detectors for different zones of a core (pith zone, mid zone, outer zone)
  and for different kinds of core (conifer vs broadleaf, clear vs fibrous surface,
  bright vs dark wood).
- Choose the strategy per zone from the photo, and report which one was used.

## 2. Sectioned counting (user-driven)
- The user can split a core into sections, optionally by marking the section ends with pen
  on the core, and photograph each section separately.
- Each section gets a partial count; the app joins the partial counts into one total.
- Reason: a single photo of a full core has too few pixels per ring for the outer rings.
  Sections restore pixels per ring.
- Each section photo needs its own scale (px/mm) and its own start/stop position on the core.

## 3. Showing the quality of a count
- The app returns an edited image of each photo with:
  - a contour around the core (the isolated outline);
  - a small rectangle drawn at each dark latewood band of each ring.
- The user reviews those rectangles and sees at once where the count is trustworthy and
  where it is not. Unreviewed or disputed rings can then be corrected by hand.
- Rectangles are drawn on the full-resolution photo, not on the downsampled image.

## Evidence behind these notes
- Ring boundaries are about two per year (light earlywood band edge, dark latewood band edge).
- Edge filters and dark-band counting each work on one core and fail on another
  (see tools/ridge_rings.py and tools/dark_bands.py results).
- Outer rings of wide cores are too thin for one photo at the current pixel density.
