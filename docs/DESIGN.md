# AgeCoreApp — design and current state

Counting rings in increment-core photos, keeping a checkable proof of every
count, and refusing to guess when a core cannot be resolved.

## What exists now

| Piece | File | State |
|---|---|---|
| Ring engine | `engine/detect.py` | working, 11/12 synthetic checks pass |
| Geometry + proof rendering | `engine/annotate.py` | verified on a real photo |
| Records (CSV/ODS/XLSX) | `engine/records.py` | verified, all three valid |
| Batch CLI | `tools/analyse.py` | verified on 4 real photos |
| Synthetic validation | `tests/test_engine.py` | 11/12 |
| Desktop review GUI | — | not started |
| Android app | — | blocked, needs JDK 17+ |

## How counting works

Follows MtreeRing (Shi & Xiang 2021, CRAN 1.4.5), which implements the
morphological method of Soille & Misson (2001) and the linear detector of Lara
et al. (2015):

1. Grey `opening(s1) → closing(s1) → opening(s2)`
2. Black-hat with a closing sized to the rings, then Otsu threshold
3. Ring *coverage* along the sampling path, thresholded, then **band runs**
   counted — one ring per latewood band, not per profile peak
4. Optional second path 1 mm away, widths corrected by `cos(atan(dx/separation))`

The original design was a 1-D profile with peak detection. It was abandoned: it
counted texture, and its errors were invisible. Peak counting also double-counts
because every band has two edges.

## Three findings that shaped the design

**1. The reference constants only work for mature rings.** MtreeRing's
structuring-element sizes are physical: opening 0.0635 mm, closing 0.3175 mm,
black-hat 2.54 mm. On a 0.27 mm-ring core the 0.3175 mm opening is *wider than
the earlywood it protects* and erases the structure — 22 bands detected where
480 were present. So element sizes are supplied, not assumed.

**2. The ring width is a required input, and no image can supply it.** Nothing
in a photo distinguishes "true 0.59 mm rings" from "every third of a 0.2 mm
series" — both are equally periodic and equally stable. Letting the engine infer
it produced 1.44 mm where the truth was 0.59 mm, and a count of 80 against 262.
The forester knows roughly how old the tree is; that is an input, not a guess.

**3. Confidence is measured parameter sensitivity, not asserted.** The count
depends on two tuning constants at once. On the mountain hemlock the grid
spanned 68–242 rings *for one photo*. So the engine runs a 12-point ladder over
both, takes the median as the count, and reports the p80−p20 spread as ±. This
is why a core that is not resolvable says so instead of producing a plausible
number.

## Accuracy actually measured

Synthetic cores with known counts (engine tuned only against these):

| case | result |
|---|---|
| 120 rings @ 45.9 px/mm | 122 (+1.7%) |
| 250 rings @ 45.9 px/mm | 251 (+0.4%) |
| 400 rings @ 45.9 px/mm | 401 (+0.2%) |
| 200 rings, 25% pith missing | 150 (0.0%) |
| two paths + incline correction | 152 (+1.3%) |
| 480 rings @ 28.9 px/mm, width known | 480, ladder 480/480/480/475/449 |
| 120 rings @ 29 px/mm | 130 (+8.3%) — known limit |
| 2.6 mm juvenile rings | needs a ring-width hint; documented limit |

Real photos, current engine:

| core | user's count | engine | status |
|---|---|---|---|
| 3-4 BA, D7100 (45.9 px/mm) | ~480 | 186 ± 73 (105–234) | unresolved |
| 3-4 BA, Pixel (28.9 px/mm) | ~480 | — | unresolved |
| mountain hemlock (25.3 px/mm) | ~270 | 170 ± 52 (95–242) | unresolved |
| Douglas fir (24.0 px/mm) | ~43 visible | — | unresolved, fibre relief |

Every real core is currently refused. That is the correct outcome, not a
failure: these surfaces are smooth with faint boundaries, and the earlier
prototype's confident numbers (384, 262) were texture, not rings.

## Records

`Documents/AGE_COUNT/` holds the proof PNG, a JSON sidecar per count, and the
spreadsheet. **CSV is authoritative and append-only; ODS and XLSX are rebuilt
from it after every append**, so the three cannot disagree.

Columns: `Age_Name` (17), `Ring_Count` (3), `Confidence_+/-(yrs)` (2) as
specified, plus `Status`, `Segment`, `Photo`, `Record_JSON`,
`Ring_Width_Median_mm`, `Seg_From_mm`, `Seg_To_mm`, `Px_per_mm`, `N_Photos`,
`Count_Min`, `Count_Max`, `Source_Image`, `Recorded_UTC`. The extra columns
exist so a count can be audited against its photo and calibration later — a
count with no span and no scale is unfalsifiable.

## Proof image

Rectangles are drawn on the **full-resolution original**, never on the
downsampled image the detector used, so a 20–50× review shows the true
boundary. Ring geometry is stored in image coordinates in the JSON, so the
record can be re-rendered or re-analysed without re-detecting.

## First real ground truth: the annotated fixture

`D71_7980partRECTANGLES.png` — a portion of the 3-4 BA core with a rectangle
drawn at every ring the forester identified, ruler in frame at 105 px/mm.

| quantity | value |
|---|---|
| marked span | 1331 px = 12.68 mm |
| rectangles (connected components) | 54, but touching ones merge |
| implied pitch | ~25 px = **0.239 mm/ring** |
| extrapolated to the 130 mm core | **~540 rings** |
| forester's own estimate | ~480 |

So the forester's 480 is supported to within 13%. It is the *engine* that was
wrong: on a clean, clear-of-marks band of the same crop it finds **38**, a 30%
under-count. This is the first real comparison against expert truth, and the
engine fails it.

Why: the detrended periodogram of that clean band peaks at 45.9 px (0.437 mm,
9.2% of power) while the marks are at 25 px. The profile is almost equally
consistent with a 25 px and a 46 px period, i.e. the classic factor-of-two
ambiguity. Parameter tuning does not close it — sweeping the black-hat over
0.3-1.0x ring width and the band-coverage threshold over 0.15-0.88 never exceeds
~40 rings. On *Abies amabilis* at subalpine elevation the likely cause is
**false rings**: the strong bands are true annual rings and the weak interleaved
ones are intra-annual. Any single global threshold discards one set or merges
both.

**A ring is not a year.** 54 marks is 54 boundaries in the ring-width series. A
false ring inside a year is a real ring boundary but not a year, so the age is
lower than the ring count by however many false rings there are. Any count must
therefore be reported as *rings* with the age derived separately, and deciding
which boundaries are false is exactly the judgement the forester has to make.
This is the strongest argument yet for the assistive design and for crossdating.

## Capability boundary (measured, provisional)

Recall of the forester's own marks, which is the metric that matters for an
assistive tool - it does not care about false rings, only about whether the app
offered the ring:

| core | ring pitch | marks | candidates offered | recall |
|---|---|---|---|---|
| `337part` | 1.78 mm | 18 over 33.9 mm | 27 | **94%** (17/18) |
| `D71_7980` fixture | 0.24-0.27 mm | 54 over 12.6 mm | 37 | **52%** (28/54) |

So the engine is a good candidate proposer on coarse-ring cores and finds only
about half the rings on fine-ring cores. The apparent boundary is near 1 mm
ring width, but that is a property of *these samples*, not of the software:
ring visibility depends on wood density, increment-bore sharpness and finish,
and on stains and impurities. Treat 1 mm as provisional until more species and
conditions are measured.

Count agreement is the wrong metric and was a mistake in the first pass. It
conflates rings the forester missed (flakes, staining), false rings correctly
marked (a real boundary that is not a year), and rings the app missed. Recall
separates them.

This also gives a better confidence value than a guessed percentage. The record
can store candidates proposed / accepted / rejected, making the confidence a
*measured* agreement with the forester for that species, surface and resolution.
Rejected candidates are kept in the JSON, so the adjudication is itself the
proof.

## Partial counts, intervals, and honest coverage

A count is not "the age". It is **the number of rings between two points on one
core, on one photo, in one pass**. Everything else is a later claim on top of
that. So the record is always (count, from, to, span_mm) and a pith/bark search
is never part of counting.

Most users will not collect the bark, and many cores lack a proper pith, so the
app must never go looking for either end. Instead the forester states which end
is the pith side, and the app counts the span between the two taps - which is
what it already does. The button exists to make that explicit and to record it,
not to change the algorithm.

**Intervals and poor-quality annotation** (requested, and the same mechanism as
the partial count): the app divides the core into intervals, counts each
separately, and marks quality per interval. The proof image then shows what was
counted and leaves poor stretches visibly unmarked, which is a stronger signal
than any confidence number because it is direct and local. The forester may draw
the interval boundaries instead; the app should also be able to choose them and
justify the choice.

**Missing rings at an offset pith.** The forester's observation is a real
geometric signal: near the pith the ring boundaries are wide, strongly curved
arcs that approach tangency with the core's lateral edges, and the centre of
curvature of those arcs estimates where the pith lies outside the wood. Fitting
the innermost arcs and extrapolating inward gives an estimated ring count for
the missing years, which the app would draw as **imaginary** rings - visually
distinct from counted ones, so a reader can never mistake an estimate for a
measurement. This is feasible but it is the one feature that must not be rushed:
a wrong pith estimate is worse than no estimate, because it looks like data.

## Douglas fir 4001-1-7 (D71_7991-8005) — the best sample so far

Label in frame: **"4001-1-7 FDC 47 (44+3)"** — 44 rings visible, 3 offset at the
pith, 47 total. Fifteen Nikon D7100 photos, horizontal cores.

This is a completely different proposition from the earlier Douglas fir set
(T9 1C, macerated fibre relief). The surface here is **sanded, with distinct
ring boundaries**, and the rings are coarse:

- scale **26.4-28.0 px/mm**, measured three independent ways (ruler cm digits:
  263.8 px/cm; core-length consistency; tick comb)
- a full-frame core is 5969 px = **~215 mm**
- 44 rings over 215 mm = **~4.9 mm/ring** (~135 px) — the coarse regime where
  the engine reached 94% recall on 337part

**The rings are already marked in these photos**, in dark green pen, roughly one
dot per ring at ~146-172 px spacing. That makes this the first fixture that can
validate a 44-ring truth directly, at a ring width the engine can actually
resolve.

**The core is thin: y 2230-2345, only 4.1 mm.** This is why the automated batch
runs were meaningless - the sampling band was landing on the ruler below the
core, not on the wood. Auto-localisation has now failed on: the plastic ruler,
the wooden table, tan paper, and now a core too thin to separate from its
background. The fix is not a better heuristic; it is the **user-drawn path**
that the app already implements. Batch mode should require a path, the same way
the phone does.

## What actually controls the error: path placement, not band width

The band-width sweep was the wrong suspect. Across band fractions from 0.15 to
0.60 of the core thickness the candidate count barely moved (337part: 23-28
candidates for bands of 52-209 px). Band width is not the lever.

Path position is. On 217-1-5 (truth 84) with a **fixed** 30 px band, moving the
path across the core:

| path y | 1895 | 1905 | 1915 | 1925 | 1932 | 1942 | 1952 | 1962 | 1975 | 2000 |
|---|---|---|---|---|---|---|---|---|---|---|
| count | 75 | 73 | 74 | 73 | 74 | 61 | 67 | 92 | 95 | 103 |

On the core (1895-1932) the answer is **73, 73, 74, 73, 74 - a spread of 1**.
Off the core it drifts up by 10-23% as it starts counting something else. The
same image earlier reported 90 and 132 purely from different path placement.

**Two separate error sources, and the confidence value only sees one of them:**

1. **Sampling noise** - about 1 ring, when the path is on the core.
2. **Systematic bias** - about -11% (74 against 84) on this core.

The pre-filter ladder reports +/-13 to +/-27 for this image. That is a measure of
*parameter* sensitivity, and it neither predicts the sampling noise nor the
bias. **A stable ladder does not mean the count is right; it means the algorithm
is self-consistent.**

This is the strongest justification yet for the design decision already taken:
the app must draw what it counted on the photo and leave poor stretches visibly
unmarked, because that is the only way a reader can see that rings were missed.
A confidence figure cannot express a systematic bias at all.

It also retires the band-width worry - the current heuristic is adequate - and
confirms that the user-drawn path is not a convenience but the accuracy
mechanism.

## Target hardware, and why the DSLR dataset is the wrong one

The app is for rugged phones and tablets: Ulefone RugKing Pad Pro, Ulefone Armor
Pad Pro, Galaxy Tab Active5, Ulefone Armor 27T Pro, Blackview BL9000 Pro. Every
measurement in this document was taken on a 24 MP Nikon D7100, and that is the
wrong reference for three separate reasons.

1. **Sensor size.** 36 mm wide against about 7 mm in a phone. For the same
   framing a phone has roughly five times fewer pixels across the object. The
   Nikon reached 37.5 px/mm framing a 160 mm core; a phone framing 50 mm reaches
   about 82 px/mm, so a phone is *better* for close-up sections and *much worse*
   for whole-core shots. The right comparison is per-core, not per-device.
2. **In-camera processing, which is the bigger risk.** Phone ISPs denoise,
   sharpen and tone-map aggressively, and their noise reduction is tuned to
   remove exactly the low-contrast fine texture that a ring boundary is. A
   measurement on the D7100 JPEG found transverse power at 0.01-0.1% with grain
   at 99.9%; a phone is likely to be worse, not better, whatever its sensor
   resolution. A phone can have more pixels and still carry less ring signal.
3. **Lens quality and distortion.** Phone lenses are wider and softer at the
   corners, so the core should be centred, and the app should measure the
   resolution of the actual pixel it is reading rather than assume the sensor's
   nominal figure.

Consequences for the build:

- The app should capture **RAW_SENSOR via Camera2 wherever the device supports
  it**, and fall back to JPEG with a visible warning. RAW availability is the
  best available proxy for whether the ring signal will survive.
- Resolution thresholds must be derived from **phone** measurements, not the
  DSLR. The 100 px/ring rule stands as a hypothesis until re-measured on a
  Blackview or Ulefone body.
- Test on the cheap Blackview BV5200 pro or the Active 8 pro tablet rather than
  the Nikon. Same Android camera stack as the target devices, which the Nikon is
  not. The BV9800 pro has known camera problems, so treat it as a separate case.

## Device results, and where the real blocker is

Three device classes have now been measured on the same cores.

| device | resolution | px/mm (framing) | px/ring (217-1-5 FDC) | transverse power |
|---|---|---|---|---|
| Nikon D7100 | 6000x4000, 24 MP | 37.5 | 71 | 0.01-0.1% |
| Blackview BV5200 | 4160x3120, 13 MP | ~60 | 115-124 | 0.02-0.03% |
| Blackview Active 8 Pro | 3000x4000, 12 MP | ~37.5 | 71 | not yet measured |

**The phone is not the problem.** Half the megapixels and a much smaller sensor,
yet the BV5200's ring-to-grain ratio matches the D7100's. In-camera phone
processing did *not* erase the ring signal - the opposite of what was expected.
So the target device class is viable for coarse-ring cores.

**The real blocker on fine-ring cores is autofocus, not resolution.** At
~10 px/ring on the 236-3-4 BA, the ring pattern is a weak periodic high-frequency
texture that contrast-detect AF cannot lock onto; the forester reports hunting
and apparent aliasing, and finds that small specks on the core help. At 10
px/ring the captured image is still well above the sensor's Nyquist limit, so
this is an **AF artefact, not a capture artefact** - which means it is fixable
in software. Hence the two features to build:

1. **Tap-to-focus with a live sharpness readout** (variance of Laplacian in a
   window at the tap point), so focus is confirmed rather than guessed.
2. **A focus-quality flag stored with each count**, so later analysis can tell
   which images were actually sharp.

**The card printed 7% small** (nominal 100 mm measured 93 mm). Every scale
derived from it while assuming 100 mm is 7.5% too low. The app must measure the
bar, compare against nominal, and **require the user to confirm the true length
before saving a count** if they differ by more than about 2%. The record must
also store the bar length used.

**The apparent mirroring was the forester's ruler placed upside down**, not a
camera or capture fault, so no mirroring handling is needed. It transpires the
plastic ruler is usually *in* the photo, which is an advantage: the ruler is a
real object and cannot shrink with the print, so it is the better absolute scale
reference. Design accordingly:

1. Locate strip, bay ticks and the 100 mm bar -> path, bay, printed scale.
2. Locate the ruler if present -> independent scale.
3. **If both exist and disagree by more than about 3%, stop and report it**
   rather than silently choosing one. A disagreement catches print shrink,
   perspective error, or the app measuring the wrong feature.
4. If only the bar is present, warn when it is off nominal by more than ~2% and
   require the user to enter the true length. This would have caught the 93 mm
   print automatically.

Note the ruler and the printed bar sit at different heights (both roughly 4 mm
below the core's upper surface), so each carries a small scale error of order
1% at a 300 mm working distance. That is acceptable and common to both.

## Card detection: measured, and the specific blocker

Automatic scale from the card is the precondition for every count, and it is
NOT solved. Recording what was measured, because the numbers are real even
though the detector is not.

**Two independent scales were measured by hand on
`IMG_20260930_112049_004.jpg` (Active 8 Pro, 3000x4000):**

| reference | method | px/mm (full res) |
|---|---|---|
| card bay ticks | 3 ticks, centres y=458.3/659.8/861.2, pitch 201.5/201.4 px on the 825 px thumbnail | **19.48** |
| plastic ruler | cm digit labels, mean spacing 58.7 px on the same thumbnail | **21.36** |

**They disagree by 9.6%.** This is exactly the disagreement test the app was
supposed to perform, and it fires on a real photo. The card implies the 188 mm
strip spans 3663 px of a 4000 px frame, which matches the visible photo, so the
card reading is probably the correct one; the ruler sits at a different height
and is read through a slightly different path. Which is why the app must report
both and refuse to silently pick.

**Physical truths about these captures, confirmed by inspection:**
- The card is frequently **rotated 90 degrees**; text runs vertically. Nothing
  may assume a horizontal strip.
- Phone photos carry a strong blue cast: on white paper B-R is about 90. An
  absolute chroma test for "grey" rejects the whole card, so chroma must be
  measured relative to brightness.
- The grey strip is often **covered by the cores**, so only 3 of 5 bay ticks
  are visible. Any detector demanding 4 or 5 ticks fails on the good photos.
- Ticks that must be rejected: the **ruler** graduations (same dark bars, same
  regular spacing, different column) and the **dark end of the core itself**.
  Both produce convincing 3-tick combs.
- Only one tick column survives: the genuine ticks share a column at
  cx~442.8, which is what distinguishes them from ruler marks at cx~127 and
  the core end at cx~307.

**Why the current detector still fails.** `engine/card.py` segments dark bars
and fits an equally spaced comb. On the verified image it returns
0.5-1.1 px/mm instead of 19.48, i.e. it locks onto clusters of 20-40 px
specks (ring lines, ruler ticks, text) that pass the shape filter and the
equal-spacing test by coincidence. Roughly 12 of 37 photos return a value; all
returned values are wrong by 6-20x. Raising the tick-count minimum to 4 fixed
the false positives by returning nothing at all, since only 3 real ticks are
visible.

**The missing ingredient is a card-specific constraint, not more tuning.**
The comb fitter needs to know that a valid solution places ticks on the strip
*and* that the strip spans a plausible fraction of the frame. Those two
constraints together eliminate the small-cluster solutions, but they require
the strip to be located first, and the strip is exactly what the cores hide.
Likely route: detect the card's outer paper rectangle (bright, large, convex
quadrilateral) rather than the grey strip, then require tick combs to lie
inside it.

Do not ship the current `engine/card.py` numbers. They are not trustworthy.

## Card v3: why the design must change

Attempting to auto-read the card on real photos exposed four assumptions that
a printed ruler cannot survive. The card itself is the problem, not the code.

1. **Only 3 of 5 bay ticks survive.** The cores lie along the strip and cover
   the rest. A fiducial pattern must therefore put marks *beside* the strip,
   never under it, and must survive losing half its marks.
2. **Print scale is not trustworthy.** The 100 mm bar measures 93 mm. Any
   dimension read off the card inherits a 7% error, so scale cannot come from
   the card alone.
3. **The ruler reference could not be auto-read.** Digit blobs on a glossy,
   curved, translucent caliper body do not segment reliably (residuals swung
   +-60 px). A dependency on a second *printed* reference is not sound.
4. **Low toner.** Features must be high-contrast and thick enough to survive a
   weak print, not hairline rules.

### v3 design principles

- **A physical, non-printed ruler is the primary scale.** The forester's
  plastic ruler cannot shrink. The app should detect the *millimetre
  graduations* on it (regular, high contrast, present every 1 or 10 mm) rather
  than try to read digits. Digits are for humans; graduations are for machines.
- **Redundant graduated scales at several pitches.** A 10 mm comb and a 50 mm
  comb, so losing marks to occlusion still leaves a fit, and a wrong pitch is
  detectable because the two must agree.
- **Corner fiducials, not a central comb.** Four solid marks at known offsets
  from the strip give a homography; the strip centreline then follows from the
  card geometry instead of from detection.
- **A dedicated unoccluded calibration lane.** A clear band along one long
  edge that no core can cover, carrying the graduated scales.
- **Black-on-white only, thick strokes (>=1.2 mm), generous spacing.**
  Chosen so a weak toner cartridge still gives clean binarisation.
- **Perspective is corrected, not assumed away.** Four corner points allow a
  homography, so the card need not be square to the sensor.
- **A printed scale bar remains, but as a sanity check only.** It reports
  "print scale drift 7%, using the physical ruler" instead of silently
  corrupting the record.

The printed card is therefore demoted from *instrument* to *target layout plus
cross-check*. The ruler is promoted to *instrument*. That inversion is the
design change, and it removes the dependency on the printer being accurate.

## Card v3 built

`tools/make_card_v3.py` -> `card/age_core_photo_card_v3.{svg,pdf}` plus
`card/preview_v3.png`. Verified true A4 (`595.276 x 841.89 pts`) and
geometry-audited: all four fiducials inside the page, both combs inside the
lane, no text overflow, and the 50 mm marks are an exact subset of the 10 mm
marks (x = 20, 70, 120, 170).

**Layout, top to bottom:**
- Four 7 mm square corner fiducials at (13,13), (197,13), (13,284), (197,284)
  with faint guide rings. Non-square grid (184 x 271 mm spacing) so a
  homography is well conditioned and a 90 degree rotation is unambiguous.
- **Calibration lane**, y 21-67 mm, x 9-201: nothing else may enter it.
  - 10 mm comb: 17 marks, 160 mm baseline, every 5th taller.
  - 50 mm comb: 4 marks, 150 mm baseline. Independent pitch; the 50 mm marks
    coincide with 10 mm marks so the two must agree, and if they do not, the
    print or the lens is at fault.
- Core bays A-E, x 11-199, y 80-118, 37.6 mm each, with a printed axis line.
- Printed 100 mm bar, explicitly labelled "not used for scale".
- Optical resolution target (2, 1, 0.7, 0.5, 0.35 mm per pair).
- Grey and white reference patches, and a 6-step instruction block.

**On the ruler's offset zero.** The forester's 0 mark sits ~7 mm in from a
rounded edge. This is harmless by construction: the app fits the *pitch* of the
graduation comb across the whole baseline, so an unknown origin cancels
exactly. The transparent ruler may be laid anywhere over the lane, and its
rounded or chipped edge is irrelevant. This is why the card prints a comb with
no origin marked at all.

**Ruler requirements, now that they are the instrument:**
- 1 mm graduations, and at least 50 mm of them visible.
- Straight, and lying flat and unobstructed in the calibration lane.
- Any plastic or metal rule qualifies; no particular brand or zero position.

**One caveat found while measuring.** An earlier attempt to read the ruler's
printed digits failed: blob indices were inconsistent and residuals swung
+-60 px, which is why graduations are specified instead of digits.

## The 56 new photos (v3 card, 12:20-12:28)

54 files matching `IMG_20260930_122*.jpg` (the set described as 56), all
3120x4160 or 4160x3120, shot on the Active 8 Pro. They are a systematic
capture set, not random snaps: ruler repositioned, with and without cores,
plus one **offset FDC core with most rings marked**.

### What works, confirmed visually

- **v3 is in the field and functioning as designed.** The ruler lies inside
  the calibration lane, `0cm` visible at the edge, and the lane is clear of
  cores. The 10 mm and 50 mm combs, the corner fiducials and the cross-check
  bar are all legible.
- **Card rotation is handled by the forester, not forced by the app.** Cards
  appear at 0, 90 and 180 degrees. Confirms the orientation-free requirement.
- **The ruler is a far better machine target than expected**: the printed cm
  digits `0cm 1 2 ... 16` and the mm graduation ladder are both crisp and
  high contrast in most frames. Graduations are clearly the right feature to
  fit; digits are also readable here, unlike the earlier caliper attempt.

### Correction: the marks are EVERY RING, not every 10th

The forester confirms the ticks mark **every single ring**. The handwritten
"70" is the count he reached over the marked run, not a decade count. This
corrects the earlier inference in this file, which assumed 10-ring marking
from the red-mark spacing. Consequence: a red-to-red interval spanning
~3014 px contains roughly 10 rings, so ring pitch there is about **300 px**,
and the 198 px "median" from automated peak-picking was picking up a
sub-harmonic, not a missed-mark pattern.

### The four named photos are four DIFFERENT cores

These are not four views of one core. They carry written labels:

| file | label | content |
|---|---|---|
| `IMG_20260929_130304_700.jpg` | `215 1-2 HW 110 (705)` | hardwood, `FDC 46 assumed, 42-43 rings visible` written in ink |
| `D71_7992.JPG` | `4001-1-7 FDC 47 (44+3)` | DSLR, green marker, core on a white ruler |
| `IMG_20260930_122706_356.jpg` | - | v3 card, offset core, every ring marked |
| `IMG_20260930_122730_219.jpg` | - | same core, full length across frame |

`4001-1-7 FDC 47 (44+3)` matches the earlier 44+3 record for that core, and
**Species-code correction.** `HW` is **western hemlock** (*Tsuga
heterophylla*), and `HM` is mountain hemlock. The forester's codes are
therefore:

| code | species | group |
|---|---|---|
| HW | western hemlock, *Tsuga heterophylla* | **conifer** (softwood) |
| HM | mountain hemlock, *Tsuga mertensiana* | **conifer** (softwood) |

An earlier note in this file recorded `215 1-2 HW` as a hardwood on the
strength of the letters alone. That was wrong and it matters: hemlock is a
conifer, so **it is in scope for the existing ring engine**, not a separate
algorithm. Only genuinely diffuse-porous species (alder, poplar, birch, maple)
need the different approach with terminal-parenchyma lines and vessel rows.

Western hemlock is BC's official tree and a dominant native species in the
province, so this is a first-class target, not an edge case.

**What "hardwood vs softwood" means for the code.** The botanically useful
distinction is *diffuse-porous hardwood* versus *conifer*, and the practical
test is whether the species shows a clear latewood band at all:

- **Conifer, including both hemlocks** - abrupt or distinguishable
  earlywood-to-latewood transition; a dark latewood band closes each ring.
  The existing black-hat morphology applies.
- **Diffuse-porous hardwood** - no latewood band; ring boundaries are thin
  terminal-parenchyma lines, often discontinuous, with large vessels marking
  the boundary. Needs a different detector.

**Hemlock-specific caution, and a corrected assumption.** Western hemlock is
known in dendrochronology for a *gradual* earlywood-to-latewood transition
rather than the abrupt one of pine or spruce.

The first assumption made here was that a gradual core needs a **wider**
black-hat, on the reasoning that the darkening is spread out. That was wrong,
and the measurement said so. Sweeping black-hat on a 46-ring synthetic with a
wide soft latewood band:

| black-hat | count | error |
|---|---|---|
| 0.80 mm | 87 | +89% (splits rings, noise amplified) |
| 1.20 mm | 50 | +9% |
| **1.60 mm** | **46** | **0%** |
| 2.00 mm | 45 | -2% |
| 2.54 mm (default) | 45 | -2% |
| 3.20 mm | 43 | -7% |
| 5.50 mm | 40 | -13% (merges rings) |

So a **tighter** element is needed, because the goal is to keep each ring
distinct rather than to find a crisper band. The forester's "46 assumed,
42-43 rings visible" is a ~7% loss, consistent with running the abrupt-core
default on a gradual core.

**Implemented:** `LADDER_GRADUAL` adds narrower rungs (black-hat 0.25, 0.32,
0.40 x ring width) alongside the existing 0.50-0.70, giving 18 rungs instead of
9, and `ladder_for(species_code)` maps HW and HM to it. Unknown or absent
codes fall back to the default rather than erroring, and the ladder reports
its own spread either way.

**The measured catch, stated plainly:** the ladder now *reaches* a better
operating point on both core types - best error 6.5% on the gradual synthetic
and 2.4% on the abrupt one, against roughly 23% before - but the optimum is
**not the same rung for both**. The gradual core prefers s2m=0.36 / bhm=0.40;
the abrupt core sits at s2m=0.20 / bhm=0.25 or s2m=0.36 / bhm=0.40. So a
single species-selected ladder still reports a wide spread, and that spread is
the honest uncertainty, not a defect to be tuned away. Selecting the best rung
automatically would be fitting to the answer.

There is also a **modelling caveat**: these are synthetic cores whose duty
cycle I chose, and the abrupt synthetic sits at +50% error across every rung
in the default range, which suggests the default range is itself mismatched to
that duty cycle. A synthetic hemlock is not the forester's hemlock. The ladder
change is directionally supported but **not yet validated on the real core**,
and `IMG_20260929_130304_700.jpg` could not be measured end to end because that
frame's transparent-ruler graduations have too little contrast to recover a
scale (see below).

**On the forester's own note** `FDC 46 assumed / 42-43 rings visible`: this is a
candid record that the expected count and the visible count disagree by about
7%. For a gradual-transition conifer that gap is exactly what the pre-filter
loses, and it is a useful target to reproduce and then close.

Also note this frame's ruler is a **transparent plastic overlay** whose printed
graduations are far lower contrast than the opaque rulers elsewhere in the
set: a comb search over it found no reliable 1 mm periodicity at all, and
digit segmentation on it was unstable. A v3 card with the ruler *under* the
card's opaque lane, or an opaque ruler, would be more robust. This is a
practical argument for the v3 calibration lane, where the ruler sits on
matte paper.

### Scale cross-validated on `IMG_20260930_122730_219.jpg`

Two independent references in the same frame, and this time they resolve:

- **Card bay dividers** found at x = 1672, 2409, 3166. Spacing 737.0 px
  against a nominal 37.6 mm pitch gives **19.60 px/mm**.
- **Ruler graduation comb** gave 34.098 px, which is **not** the 1 mm comb:
  at 19.60 px/mm that period is 1.74 mm, not a ruler feature. It is a
  harmonic/alias of a denser comb.

Sanity check that settles it: at 19.60 px/mm the frame is **212 mm wide**,
i.e. the A4 sheet very nearly fills a 4160 px frame, which matches the image.
At 34.098 px/mm the frame would be 122 mm - far too narrow for a card that
visibly spans it. **The card-derived scale is correct; the single-period
ruler estimate was a harmonic.**

This is the concrete demonstration of the failure mode: a lone dominant
period cannot be trusted, and had the app used it the ring width would have
been overstated by 74%. Requiring a period to be accompanied by its 10x and
50x partners - the three-comb consistency test - is what fixes the octave.

### Ring pitch measured on the offset FDC core

Within a single card bay (680 px wide, clear of both dividers), the dominant
ring period is **21.25 px**, which at 19.60 px/mm is **1.084 mm/ring**. A
subharmonic at 226.67 px is 11.56 mm, i.e. very close to 10 rings, so the
10-ring group is ~11.6 mm and the 47-ring core is roughly 55 mm of the
119 mm visible length - the rest of the core runs out of frame. This is
consistent with the label `47 (44+3)`.

For reference, that is **~21 px/ring**, comfortably above the ~10 px/ring
floor where the earlier Active 8 Pro analysis said fine rings become
unusable. The `4001-1-7` core is coarse enough to be countable on this
device, which is good news: it is a workable validation target.

### `IMG_20260930_105828_889.jpg` - every 10 years, and the best scale frame yet

This is a **v2 card** photo (not v3), and it is the single most useful frame in
the whole set for calibration. It contains a *labelled* 0-100 mm scale bar with
1 mm graduations, the full core, and the bay dividers, all in one shot.

**Scale measured three independent ways, all agreeing:**

| method | result |
|---|---|
| linear fit of the ten decade labels 0..90 | **20.39 px/mm** |
| median of the nine clean decade spacings (221.1 +- 7.7 px) | **22.11 px/mm** |
| frame-width sanity check | 4160 px / 20.39 = **204 mm** vs A4 = 210 mm |

The third check is the decisive one: at ~20.4 px/mm the A4 sheet very nearly
fills the frame, which is what the image shows. This is the same argument that
resolved `122730_219` (19.6 px/mm, frame = 212 mm), and it now works in two
independent frames on two different cards.

**The 10-year marking convention.** The forester marks **every 10th ring** on
this core, using near-black pencil strokes that are far darker than the ring
latewood. The marks were not cleanly separable from ring lines by intensity
alone: at successively stricter thresholds the detected count fell 167 -> 103
-> 85 -> 51 -> 34, which means the strokes and the darkest latewood bands
overlap in grey level. A reliable count needs the marks to be distinguished
some other way - they are *diagonal*, crossing the core obliquely, whereas
rings run across the core, so an orientation-selective filter should separate
them. This is the same geometry the increment-borer grooves exploit, and the
same fix applies.

**Two marking conventions now confirmed, on two different cores:**

- `IMG_20260930_105828_889` - **every 10th ring** (dark pencil, oblique)
- `IMG_20260930_122730_219` - **every ring** (purple pen, transverse)

The app must not assume either. The record should store the marking
convention alongside the count, and the count should be checked against the
convention: a "10-marked" core with 47 rings should show 4-5 marks, not 47.

### The marked core - the important new ground truth

`IMG_20260930_122500_042.jpg` and siblings (24-28, 31-35, 46) show an FDC
core with:

- **~28 purple/blue pen ticks**, each crossing the core at a ring boundary;
- **small dark dots** between successive ticks;
- **3 red marks** acting as fiducials, and the forester's handwritten **"70"**.

The two clearest red marks are at y = 949 and y = 3963, i.e. **3014 px apart**.
Visual inspection of the interval, rendered in three columns, shows about
**10 purple ticks plus 10 dots between them** across that red-to-red span.
That is consistent with the ticks marking **every 10th ring** and the red
marks delimiting **decades**: two red-to-red intervals would be 20 rings, and
the handwritten 70 is consistent with a ~70-ring offset core whose outer
portion is not all in frame.

**Consequence for calibration:** the red fiducials give a long, precise
baseline (3014 px) for measuring ring pitch, and the dots give a *second,
denser* set of positions between the ticks. That is a better fixture than
anything previously available, because pitch and decade-count are both known.

### Automated mark detection is NOT reliable yet

Naive purple-ink segmentation found only **8-16** of the ~28 ticks, with
spacings from 92 to 654 px against a true spacing near 200-300 px. Failures:

- the ink is thin and its colour varies along the core (dark blue in shadow,
  pale where the surface is worn);
- the core is **curved**, so a fixed x-band misses ticks that bow sideways;
- the **dots** are easily confused with ink fragments and ring lines;
- thresholding on `b - r` cannot separate pen from the warm wood, which is
  also relatively blue-shifted in shadow.

Peak-picking the profile therefore cannot be trusted for the mark count. Any
app feature that claims to have found the forester's own marks must either
track the core's curve or ask the user to confirm, and it must report a
confidence rather than a bare number.

### Scale extraction remains unresolved

Dominant-period analysis recovers a consistent comb, but the **fundamental
cannot be disambiguated from its harmonics** without a geometric anchor:

- detrended FFT over the whole frame returns periods of ~100-175 px on most
  frames, but the 1 mm ruler pitch cannot be separated from the 10 mm card
  comb and the 50 mm comb, which differ by exact integer factors;
- on `IMG_20260930_122023_550.jpg` the printed 100 mm cross-check bar measures
  **~9.6 px/mm** and a direct tick measurement gives **9.375 px/mm** - mutually
  consistent - while other passes on the same frame returned 18.5, 23.5 and
  40 px/mm, all integer multiples;
- since the 1 mm comb, the 10 mm comb and the 50 mm comb are all present, any
  of them can be reported as "the" pitch.

**A single dominant period is therefore not enough.** The fix is to require
the 1 mm, 10 mm and 50 mm combs to be *mutually consistent* (p, 10p, 50p),
which uniquely fixes the octave - and if they are not consistent, report the
conflict rather than choosing one. This is exactly the two-reference
cross-check design, now needing three references.

## Device comparison: BV5200 vs Active 8 Pro

The two phone/tablet classes are separable by sensor resolution, so the
comparison is objective rather than anecdotal:

- **BV5200 class** - 4160x3120 / 3120x4160, 13 MP, **120 frames**
- **Active 8 Pro** - 3000x4000, 12 MP, **24 frames**

### Measured

| metric | BV5200 | Active 8 Pro | notes |
|---|---|---|---|
| white balance (R-B on paper) | **-6.5** (std 9.6) | **-81.9** (std 20.0) | BV neutral, Active strongly blue |
| ring/grain anisotropy | **0.55** median | **0.17** median | ~3x, the decisive number |
| sharpness (p99 Laplacian) | 96.6 | 89.2 | comparable |
| p95 luminance | 231.7 | 246.0 | Active slightly brighter |
| dark fraction | 0.042 | 0.056 | more dark area |

### Verdict: the BV5200 photos were markedly easier to analyse

Three concrete reasons, in order of importance:

1. **Ring contrast.** Anisotropy 0.55 vs 0.17. The BV5200 frames show ring
   structure as a dominant, well-organised modulation; on the Active 8 Pro the
   along-core variation is a small minority of the signal, so the same filters
   that cleanly segment the phone frames return noise on the tablet. This is
   why the Active 8 set took so much more work despite being 12 MP.

2. **Neutral white balance.** The Active 8 Pro runs a systematic **-82 R-B**
   cast, with individual frames between -62 and -113 - i.e. it is not a
   one-off white-balance failure but a persistent cast that also *varies*
   frame to frame. This defeats any absolute grey test: an early attempt to
   segment the card's grey strip by low chroma rejected the entire card
   because on white paper B-R was about 90. The fix was to measure chroma
   *relative to brightness*. The BV5200 needs no such handling.

3. **Fewer rotation surprises.** The BV5200 frames were consistently
   landscape or portrait with the card readable; the Active 8 set was shot
   at multiple rotations including 90 and 180 degrees, and the ruler moved
   between frames, so orientation had to be solved per frame.

### Where the Active 8 Pro still wins

- **Bigger frame, fewer of them.** 4000 px in the long axis let a full core
  plus the whole scale bar land in one frame, which is what finally produced a
  clean 20.39 px/mm measurement and a directly countable marked core.
- **Better geometric quality.** Straight dividers and a clean labelled
  0-100 mm bar gave a scale that cross-checked three independent ways.

### Revised target-device ranking

1. **BV5200-class (13 MP, 4160x3120)** - best ring contrast by a wide margin;
   this is the device class to develop and validate against.
2. **Active 8 Pro (12 MP, 3000x4000)** - adequate for scale and layout, weaker
   for fine rings. Not a reject: the ~21 px/ring measured on `4001-1-7` is
   workable, and the frames are geometrically cleaner.

The practical consequence for the app: **do not assume a white balance.**
Take the white and grey reference patches from the v3 card and normalise per
frame before any intensity-based decision, and treat ring contrast as a
measured per-frame quantity that gates the count, exactly as the sharpness
flag will.

## Frame conditioning built (engine/condition.py, core/.../Condition.kt)

Working on the device comparison, rather than more detector tuning, because
the measured differences between phone classes are larger than anything the
detector parameters can absorb.

**What it does, in order: neutralise, measure, gate.**

1. **White balance.** Per-channel gains from the brightest 12% of the frame,
   using channel *ratios* so exposure cancels. Clamped to 0.55-1.8, because a
   factor beyond that means the estimator has locked onto non-paper. Measured
   result across the full photo set:

   | set | R-B before | R-B after | frames passing |
   |---|---|---|---|
   | BV5200 class (n=120) | -14.4 median | **+0.6** | 102/120 (85%) |
   | Active 8 Pro (n=24) | -69.5 median | **+1.4** | 23/24 (96%) |

   The Active 8's -102 R-B frames come out at +0.2. This is the step that
   makes one detector work across both classes.

2. **Ring contrast gate.** Anisotropy (across-core variance / total) must
   reach 0.12. The two classes measure 0.57 and 0.34 median, so both pass
   overall, but individual soft or low-contrast frames are now refused instead
   of returning a plausible wrong number.

3. **Focus gate.** 99.5th-percentile neighbour gradient must reach 6.0. This
   catches the autofocus-hunting frames, and pairs with the tap-to-focus UI.

4. **Scale-conflict gate.** If two independent scale estimates disagree by
   more than 6%, the frame is **refused**, not resolved. This is the octave
   aliasing failure: taking a single dominant period there overstated ring
   width by 74% on a real frame.

**Design rules that came out of the failures:**

- Never use an absolute grey threshold. Chroma is compared relative to
  brightness, which is scale-free and cast-free.
- Judge exposure *after* normalisation, since gains can push a frame over 255.
  Checking the raw histogram produced false clipping warnings on well-exposed
  frames.
- Report a missing paper region as NaN, not 0.0. A tight core crop has no
  paper, and "no cast" is a different statement from "unknown cast".
- Do not let `np.nanmean`/`nan()` produce all-NaN slices: they warn before the
  caller can filter, which is fatal under `-W error`. Counts are computed
  first and empty slices are simply never divided.

**Validation:** 13 new regression checks in `tests/test_engine.py`, all
passing - cast detection and neutralisation at -60 and -110, non-distortion of
an already-neutral frame, anisotropy ranking, focus gating, weak-signal
refusal, scale-conflict refusal, and the NaN-on-missing-paper case. Suite is
now **24/25** (the single failure is the long-documented 120-ring coarse-core
limitation, unchanged).

Both implementations build: `python3 -m tests.test_engine` passes and
`./gradlew :app:assembleDebug` produces a 5.67 MB APK.

**What this does not do yet:** the orientation/scale extraction that feeds
`px_per_mm` and `px_per_mm_alt` is still the open blocker described above, so
in practice the scale-conflict gate is not yet being exercised on real photos.
The conditioning is deliberately independent of it, so the two can be
finished in either order.

## Species scope: conifers first, deciduous as a stated goal

The forester's priority is explicit: **all current cores are conifers, and
roughly 90% of use will be conifers.** That matches the engine, which was built
on conifer latewood morphology throughout.

Conifer target species, using the forester's codes:

| code | species |
|---|---|
| FDC | Douglas-fir and the fir complex |
| HW | western hemlock, *Tsuga heterophylla* |
| HM | mountain hemlock, *Tsuga mertensiana* |
| PL | lodgepole pine |
| BI | probably balsam/interior spruce - worth confirming |

Deciduous species named as desirable, in his order: **black cottonwood**,
alder, big leaf maple, paper birch, bitter cherry, aspen.

That set is genuinely a second algorithm, not a parameter tweak. All of them
are diffuse-porous, so:

- there is **no latewood band**; the boundary is a thin terminal-parenchyma
  line, often discontinuous and only locally resolvable;
- the dominant ring feature is the change in **large earlywood vessel diameter**
  from one ring's latewood to the next ring's earlywood, not a dark band;
- cottonwood and aspen have very wide rings, so resolution is not the limit -
  *finding the boundary* is;
- big leaf maple and paper birch are the hardest, with marginal parenchyma and
  low-contrast boundaries;
- alder and birch add a further complication: clustered, rayless, and prone to
  false rings.

Planned approach, when it is worth starting: keep the conifer engine untouched
and add a **second boundary detector** for the vessel-based signal - detect the
vessel-size step rather than a latewood band, then reuse the existing
confidence and refusal machinery so a diffuse-porous core reports its spread
honestly in the same way. Do not extend the conifer ladder to cover them; the
signal it thresholds does not exist there.

A soft `species_group` field on the record (conifer / diffuse-porous) is worth
adding now, so that a mixed dataset does not need a migration later.

## Fresh vs degraded cores, and a silent-failure bug this exposed

The forester has black cottonwood age cores, but they are **not fresh and they
degrade fast**. Cores used with the app will be fresh. That distinction
matters in two ways, and the second one produced a real bug.

**1. Fresh cores photograph better, so development fixtures are harder than
production.** Degraded cores are greyed, oxidised, and lower in saturation.
Fresh cottonwood will have more colour and more vessel contrast, so the
detector's job is easier in production than on the fixtures used to build it.
That is the safe direction, but it means the tuning should not be pushed to the
limit on degraded material and then assumed to hold.

**2. A fixed colour test fails on degraded wood, silently.** `wood_mask`
required `R-B > 22`. Measured on synthetic cores with progressively greyed
wood:

| core R-B | fixed test | adaptive test |
|---|---|---|
| 60, 45, 32 | found | found |
| 24 | found | found |
| **18, 12, 6** | **refused** | **found** |
| 3 | refused | refused (genuinely ambiguous) |

Below R-B 18 the mask emptied, `ring_anisotropy` returned 0, and the frame was
refused as **"no core found in frame"** - which is indistinguishable from a
photo that genuinely has no core in it. The most likely cause would have been
an operator blaming their photographing, or the algorithm being blamed for
missing a core, when the real fault was a hard-coded constant meeting aged wood.

**Fixed:** the R-B threshold is now derived from each frame's own paper, so the
test is "warmer than the paper beside it" rather than an absolute number, with
a distribution-based retry when there is no paper in frame. It is cast- and
degradation-robust, and it still refuses at R-B 3, which is the right place to
stop because a core that grey has genuinely stopped being separable from paper.

**Effect on the real photo set, immediately:**

| set | before | after | median anisotropy |
|---|---|---|---|
| BV5200 class (n=120) | 102 pass (85.0%) | **108 pass (90.0%)** | 0.568 |
| Active 8 Pro (n=24) | 23 pass (95.8%) | **24 pass (100%)** | 0.576 |

The Active 8 median anisotropy rose from 0.343 to 0.576, which means the earlier
figure was *understated* by the mask missing parts of the core - the two device
classes are more alike than the first measurement suggested, and the earlier
0.55 vs 0.17 gap was partly a detector artefact rather than pure hardware.

Four new regression checks cover this (greyed core found at R-B 18, 12, 6; and
greying not destroying ring contrast). Conditioning is now **17/17**, suite
**28/29**.

**Still true, and worth repeating:** none of this is validated on a real
cottonwood core. These are synthetic colour ramps. The genuine test is a
photograph of an actual core, and any that can be spared is worth more than
another synthetic.

## The 5 mm core diameter as a scale constant

The forester states the age cores are **probably all 5 mm in diameter** and
**2-3 years old**. That is genuinely useful, and it is worth being precise
about what it does and does not give.

### What it is: a ratio check, not an absolute scale

A known core diameter gives

    px_per_mm = core_diameter_px / 5.0

which requires only that the core be found and its width measured. It needs no
card, no ruler, no fiducials and no orientation solve. That makes it the only
scale reference available when the card is occluded or out of frame, and it is
immune to the printed card's 7% error and to the ruler's low contrast. So it is
a genuinely valuable third reference - and it is exactly the kind of
*physical, non-printed* constant that the v3 design was built around.

### What it is not: reliable to 5% as measured today

Measuring the core width on the two frames with a well-established card scale
(three agreeing methods each) gives:

| frame | core width | card px/mm | implied diameter |
|---|---|---|---|
| `IMG_20260930_105828_889.jpg` | 126 px | 20.394 | **6.18 mm** |
| `IMG_20260930_122730_219.jpg` | 110 px | 19.60 | **5.61 mm** |

Both are larger than 5 mm, by 12% and 24%. Measurement caveats, in order of
likely size:

1. **This is a 2-D silhouette, not a diameter.** A core is a cylinder lying on
   the card, so the photographed width is its diameter only if the camera looks
   straight down the axis. A tilted view foreshortens it, and the apparent
   width is then *smaller* than the true diameter - which is the wrong
   direction for these results, so tilt alone does not explain it.
2. **The measured band includes the shadowed edge.** Ring boundaries and the
   darker rim widen the "warm" region beyond the wood proper. This inflates
   the estimate, and is the most likely single cause.
3. **The cores are 2-3 years old and shrink.** Radial shrinkage of a few per
   cent is plausible, which makes a *measured* diameter smaller than 5 mm, also
   the wrong direction.

So the measured 5.6-6.2 mm is best read as **an upper bound on a core whose
photographed width is inflated by edge and shadow**, not as evidence that the
drill is 6 mm. It cannot be used as a 5% scale reference in its present form.

### The correct role for the constant

Use the diameter as a **coarse sanity band and a fallback**, not as a precision
reference:

- **If the card or ruler gives a scale, use it**, and use the diameter only to
  check the result lies in a plausible range, say 3.5-6.5 mm. Outside that,
  report a conflict rather than choosing.
- **If neither card nor ruler is present, the diameter gives a usable
  provisional scale**, clearly labelled as diameter-derived, with a stated
  uncertainty of about +-20% rather than a false precision.
- Record which reference produced the scale. The existing
  `scale_reference` field already exists for this and should carry
  `core_diameter` as a value.
- **Do not let it override a card or ruler measurement.** The card scale was
  confirmed three independent ways on two different frames; the diameter has
  one measurement path with a known edge bias.

### A better way to use it, available later

The diameter's real weakness is the visible edge, not the constant. Measuring
between the *outermost latewood boundaries on each side* rather than the
silhouette would remove the edge bias, and would also make the measurement
independent of how dark the rim happens to be. That is a small change to the
existing core-profile code and is the natural next refinement.

Meanwhile: **2-3 year old cores are 2-3 years from full 7% shrinkage**, and the
app will be used on fresh ones, so the constant is if anything a little more
reliable in production than on the fixtures available now.

## Card v4: landscape, for cores to 250 mm

`tools/make_card_v4.py` -> `card/age_core_photo_card_v4_landscape.{svg,pdf}`
plus `card/preview_v4.png`. Verified **true A4 landscape**
(`841.89 x 595.276 pts` = 297 x 210 mm) and geometry-audited.

Requested because v3's strip is 188 mm and will not take a core of 250 mm,
which a large black cottonwood, veteran Douglas-fir or bigleaf maple routinely
exceeds.

**Layout, millimetres, page 297 x 210:**

| block | extent | notes |
|---|---|---|
| corner fiducials | (13,13) (284,13) (13,197) (284,197) | 7 mm squares, non-square 271 x 184 grid |
| calibration lane | x 9-288, y 24-64 | nothing else may enter it |
| - 10 mm comb | x 20-250, y 30-43 | 24 marks, 230 mm baseline, every 5th taller |
| - 50 mm comb | x 20-220, y 50-61 | 5 marks, 200 mm baseline |
| core strip | x 18-279, y 74-104 | 30 mm tall |
| - bays A-E | x 20-270 | **5 x 50.00 mm = 250.00 mm exactly** |
| printed cross-check | x 20-120, y 114 | labelled "not used for scale" |
| resolution target | x 20-110, y 134 | 2, 1, 0.7, 0.5, 0.35 mm per pair |
| grey / white refs | x 20-112, y 156 | plus a 5 mm core-diameter ring at x 130 |
| instructions | x 150+, y 140-173 | 6 numbered steps |

**Three deliberate design choices:**

1. **Bay pitch is 50.00 mm, matching the 50 mm calibration comb.** A 250 mm
   core then fills the five bays *exactly*, and the same physical interval
   serves both measuring and layout. It also reduces the number of distinct
   pitches on the page, which matters because every extra pitch is another
   harmonic that scale recovery has to disambiguate - the failure that made a
   single dominant period report 9.6, 18.5, 23.5 and 40 px/mm on one frame.

2. **The 5 mm core diameter is printed as a reference ring**, filled centre dot
   with a 5 mm circle, captioned "sanity check only". It is on the card because
   it is the only non-printed constant available, and it is demoted to a check
   because the photographed silhouette measured 5.6-6.2 mm against a nominal
   5 mm - the shadowed rim inflates it.

3. **An opaque ruler is specified explicitly** in the instructions. The
   transparent overlay in one frame had graduations with so little contrast
   that no scale could be recovered from it at all.

**Print landscape at 100%.** v3 remains the portrait card for shorter cores;
neither supersedes the other.

## The 14 black cottonwood photos (2026-10-01, 10:13-10:15)

All 14 are the same core, 4160x3120, daylight. The v4 landscape card is in
the field and working as designed: the ruler lies in the calibration lane, the
core sits in the strip, and the core stays clear of the lane in every frame.

Structure of the set:

- **Photos 1-2** - whole sheet, both references fully visible. The anchor set.
- **Photos 3-6** - bays A-B, left end of the core, **ruler right way up**.
- **Photos 7-8** - whole core plus ruler, **ruler upside down** (0cm at right).
- **Photos 9-14** - progressive close-ups moving right along the core, from
  bay C to bay E.

White balance is near-neutral throughout (R-B from -8 to +16), versus -82 on
the Active 8 Pro set. Daylight photographs are the right condition and the
conditioning module handles them without correction.

### Scale: now cross-validated, and it took the right reference

On photo 1, where the whole sheet is visible, two independent measurements
agree to **1.6%**:

| reference | result |
|---|---|
| card strip geometry (261.0 mm printed width) | 15.63 px/mm |
| ruler graduation comb | 15.88 px/mm |

Per-photo scale, from the ruler graduations:

| photos | px/mm | content |
|---|---|---|
| 1-2 | 15.6-15.9 | whole sheet |
| 3-6 | 33.8-38.9 | bays A-B, close |
| 7-8 | 17.9-18.0 | whole core, ruler inverted |
| 9-14 | 22.1-24.8 | close-ups, bays C-E |

### What went wrong first, and the lesson

The first attempt used a single dominant FFT period, as the three-comb design
intended. It produced **15.9, 18.0, 22.4, 36.5 and 38.9 px/mm** across the set -
a plausible-looking spread that was **wrong by factors of two**, and on the
two overview frames the comb disagreed with the strip geometry by 92%.

The specific failure is worth recording, because it is the same failure as
before and it is now fully characterised:

1. **A single dominant period cannot be trusted.** On photo 7 the FFT returned
   18.01 px, which is neither a 1 mm graduation nor any sub-harmonic of one at
   the true scale - it was the card's own 50 mm comb.
2. **The card and the ruler disagreed by a factor of ~2**, which initially
   looked like a print-shrink problem. It was not: the "50 mm bay" detector
   had latched onto the wrong vertical features, and the ruler comb onto the
   wrong horizontal ones.
3. **What resolved it was a feature whose page position is known exactly** -
   the strip's own 261.0 mm printed width - combined with the ruler's cm
   *labels*, which are unambiguous where graduations are not.

**Conclusion, now measured rather than assumed:** reference detection must be
driven by known page geometry and by features that cannot be confused with
each other, not by spectral prominence. Prominence finds the most *visible*
period, which on a card with a 10 mm comb, a 50 mm comb, a 100 mm bar, a
graduated ruler and a core full of rings is essentially never the 1 mm one.
This is the strongest evidence yet for the mutual-consistency approach, and
also its limit: consistency alone is not enough without an anchor.

### Ring size, and the resolution this affords

At 22-39 px/mm on the close-ups, a 25-ring count over ~130 mm is 5.2 mm/ring =
**115-200 px per ring**. That is far above the ~10 px/ring floor at which
fine rings were judged unusable, so black cottonwood on this device is
**resolution-comfortable**. The difficulty for a diffuse-porous species is
therefore not sampling: it is that the boundary itself is a thin
terminal-parenchyma line rather than a latewood band, and it is often
discontinuous. That is the algorithmic problem, and it is still open.

The 5 mm core diameter is consistent at the anchor: measured 72 px on the
close-ups is 4.6 mm at 15.6 px/mm, within the expected few-per-cent edge bias.

### Ground truth: the forester's marked cottonwood boundaries

Two edited frames (`ACT_101308_303.jpg` whole sheet, `ACT_101340_083.jpg`
close-up; the suffix is the last nine digits of the source `IMG_...` name).

**What the marks are.** Orange rectangles, one per ring boundary, spanning the
full core height. In the close-up, 8 rectangles were recovered with centres at
x = 1284, 1618, 1970, 2316, 2650, 2958, 3257, 3532, giving **7 marked
intervals** with spacings 333.5, 352.5, 345.0, 334.5, 308.0, 299.0, 274.5 px
(median **333.5 px**, std 26 px, i.e. **24% peak-to-peak variation** - cottonwood
rings are genuinely uneven here, which is itself useful information).

**What the core looks like.** Magnified, the ring boundary is unmistakable and
is **not** a latewood band: each ring closes with a dense horizontal row of
large earlywood vessels, a visible pale dotted line running across the core.
This is exactly the predicted diffuse-porous signature, and it means the
existing conifer black-hat has nothing to threshold. The signal is a change in
*vessel size and density*, periodic in the same way a latewood band is, but
inverted in appearance: pale, high-frequency, and confined to a narrow band.

**That is good news for detectability.** The vessel rows are high contrast and
very regular, and at this magnification they are far from the noise floor. A
conic/radial vessel filter or a variance profile across the core would find
them; the challenge is separating one row from the next when ring width varies
by 24%, not resolving the row itself.

### Scale resolved: the 5 mm constant, used properly

The forester confirms the increment core is **5.0 mm**. Applying that, the
close-up scale is settled - but only after fixing how the core is measured,
which was the actual source of the factor-of-two confusion.

Measuring the core by a warm-hue threshold gave 96-150 px, and by
"brightest to card grey" gave 387 px. Both were wrong. The reliable method is
**local texture**: inside the core the wood is full of vessel rows and the
local standard deviation is high (peak 14.1); the printed card is smooth
(baseline 3.3). Thresholding the texture at half its peak gives a clean
extent of **y 1518-1726, i.e. 208 px**.

    208 px / 5.00 mm = 41.6 px/mm

Every earlier number was wrong because the hue threshold was including the
core's **self-shadow** - the lower half of a cylinder lit from above is dark,
so a brightness test reads it as card. Texture is immune to that. This is the
same lesson as the greyed-wood fix, in a different guise: **brightness is not
a reliable material boundary, texture is.**

Sanity check: at 41.6 px/mm the marked interval of 333.5 px is **8.0 mm per
ring**, and over the ~243 mm core that is **~30 rings**, against the forester's
estimate of "25-ish". Close enough to confirm the scale; the small difference
is either the visible core being shorter than the full 250 mm, or a few rings
being unresolvable at the ends. **334 px per ring is a 33x margin over the
~10 px/ring floor** - this core is nowhere near resolution-limited.

### First detector attempt on the cottonwood, and it failed

Profiled local texture along the core and looked for periodic maxima, which
is the natural analogue of the conifer approach for a diffuse-porous species.
Result: **5 peaks where 8 boundaries exist**, median error **334 px = one full
ring width**. The detector is not finding the vessel rows reliably.

What went wrong, from inspecting the profile:

- The vessel row *is* a strong, clean signal - one peak reaches 63.7 against a
  baseline of 36, a 1.8x contrast. Individual rows are detectable.
- But the rows are **not equally spaced** (24% peak-to-peak in the ground
  truth), so a fixed-pitch or peak-threshold approach drops the weaker ones.
  The 5 detected peaks are unevenly distributed: three clustered at x 446-860,
  one at 1368, one at 3114.
- The band chosen (y 1530-1660) is the upper lit face, which is where the
  vessels are sharpest, but it also contains the **dark knot** visible near
  x 1250 in the close-up, which corrupts the profile locally.

**Conclusion: the approach is not wrong, the implementation is naive.** The
fix is a comb fit that *searches for the pitch* rather than thresholding
peaks, seeded from the ground-truth median of 8.0 mm, and applied along the
full core height with the knot masked. That is the same shape as the conifer
ladder and should behave similarly.

**The optimistic finding stands, though:** the boundary is a high-contrast,
very regular vessel row, clearly visible at this magnification. A
diffuse-porous detector for cottonwood looks feasible. It is a different
signal from the conifer one, not a harder version of it.

Annotation detection itself needs care: the orange outline is a hollow
rectangle, so naive connected-component labelling splits it into 2-4 fragments
per mark. It must be closed and dilated before labelling, and fragments
merged by x-proximity. Done that way the count is stable at 8; left naive it
returns 19 fragments and a spacing std of 132 px against a true 26 px.

### CRITICAL caveat on the wide-frame marks: they are transferred, not observed

The forester states that the marks on `ACT_101510_288.jpg` (the wide view)
were **placed from what he could see in the partial detailed images** - his eye
could not resolve those boundaries in the whole-core frame.

This changes what the wide frame can and cannot be used for, and it is worth
being precise, because it cuts both ways:

- **They are not invalid.** They encode his judgement, made at high
  magnification, about where the rings are. That judgement is better than
  anything inferred from the wide frame alone.
- **They are not independent evidence at this resolution.** A detector tested
  only against them would be tested partly against itself: if he could not see
  the boundary in this frame and a detector claims to, that is a *disagreement
  worth investigating*, not automatically a detector success.
- **They cannot be used to measure detectability.** Recall measured against
  transferred marks says nothing about what is resolvable in the wide frame. It
  would flatter any detector.

**Consequence for evaluation, adopted here:** ground truth is now tiered by how
it was produced.

| tier | source | valid for |
|---|---|---|
| **A** | boundary marked while looking at *that* frame at sufficient magnification | recall, detectability, and octave tests |
| **B** | boundary transferred from a close-up to a wider frame | count validation, position agreement within a few mm, but **not** recall |

`ACT_101340_083.jpg` and `ACT_101510_288.jpg` are **both tier B** - the close-up
too was interpreted alongside the other frames. The one thing that would
upgrade the evidence is a frame where the forester marks boundaries *only
where he can see them unaided in that frame*, leaving the rest unmarked, with a
note of how many he was confident about. That is cheap to produce and would
make recall measurable for the first time on a real core.

### `what_I_see`: a measurement of the observer's own bias

The forester annotated the **same frame twice**: once transferring boundaries
from the close-ups (`ACT_101510_288`), and once marking only what he could see
**unaided in that frame** (`what_I_see_101510_288`). He notes the second may
still be biased, because he has looked at the core and the detail photos a lot.

Comparing the two mark sets is therefore a direct measurement of that bias:

| | marks |
|---|---|
| `ACT_` (transferred from close-ups) | 21 |
| `what_I_see` (unaided in this frame) | 16 |
| common to both, within 60 px | **16** |
| in `ACT_` only | 4 (x 1952, 3381, 3640, 3762, 3860) |
| in `what_I_see` only | 1 (x 1868) |

**Every mark he could see unaided, he placed in the same place** - the 16
common marks agree to a median of 3 px, mostly under 10 px. So the *positions*
are reliable and not an artifact of the close-ups.

**What differs is how many he trusts.** He marked 5 extra boundaries in the
right-hand half (x 3381-3860) when working from the detail photos, and did not
repeat them when looking only at the whole frame. Read plainly: **on this frame
he can resolve roughly 16 boundaries, not 21.** The 5 extra are real - they
came from higher magnification - but they are not *visible here*, and a
detector that finds them in this image would be reading something the eye
cannot.

This is the first genuinely useful tier-A/B split:

| | count | valid for |
|---|---|---|
| `what_I_see` | 16 | **tier A** - what is resolvable in this frame. Recall can be measured against this. |
| `ACT_` | 21 | tier B - includes 5 boundaries that require the close-ups |

**Honest caveat, which the forester raised himself and which stands:** the 16
tier-A marks are not free of bias either, because he has now seen this core many
times. Someone marking the same frame cold, without the close-ups, would be the
clean control. Until then, treat tier-A recall as an *estimate* - but it is a
real, usable bound, and 16 is the number a detector should be judged against for
this frame.

**The concentration matters too:** all 16 tier-A marks sit in three clusters -
x 462-1618 (six, bays A-B), 2430-3254 (six, bay C-D) and 3535 (one, bay E). The
unmarked stretches are ~70% of the core, so they remain usable as negative
ground truth for false positives.

**This is also a strong argument for the multi-estimator UI** below. When the
evidence is tier B, the app should not present one number as authoritative.
It should show what each method found, how much they agree, and let the
forester judge which is plausible - which is exactly what he is doing by
eye, and better informed than any single algorithm.

### Third marked frame: `ACT_101510_288.jpg`

This is the widest view of the marked core, and it shows the marking is
**selective rather than exhaustive**: the forester boxed boundaries in three
clusters - two near bays A/B (x 463, 797, 1139), a group around bay C
(x 2605, 2852, 3042), and a dense run through bay D/E (x 3254, 3381, 3524,
3627/3649, 3762, 3861). Between them the core is unmarked.

That is more informative than a fully marked core, and it is the right thing
to have photographed. It means:

1. **The marked intervals are trustworthy as positive examples.** A detector
   that finds a vessel row where the forester boxed one has done something
   real; a detector that also fires in the unmarked stretches has produced
   false positives that are now *visible*.
2. **The unmarked stretches are a free false-positive test.** The core runs
   x 400-3900 and only about 1100 px of it is boxed, so roughly 70% of the
   core is available as negative ground truth.
3. It also confirms the spacing is genuinely uneven - 98 to 1466 px between
   consecutive boxes, before accounting for the gaps where nothing is marked.
   Where marks *are* adjacent they run 98-342 px, i.e. a factor of 3.5.

**Annotation extraction is fragile and needs care, twice over.**

- The mark is a *hollow* orange rectangle, so connected-component labelling
  splits one mark into 2-4 fragments. In `ACT_101510_288` a naive pass
  returned **27 components for 13 marks**.
- Adjacent marks sometimes **touch or merge** into one component: the
  components at x 3622-3632 and 3638-3659 are one rectangle split by a thin
  gap, and two components at x 1805-2483 and 2930-3154 are wide merged groups
  that swallowed intermediate marks entirely.
- Some marks are **short fragments** (h 20-30 px) rather than full-height
  ones, and they must be kept separate from the tall ones (h 60-107) or the
  count inflates.

A robust extractor therefore needs: colour threshold -> close -> dilate ->
label -> **split wide components on their internal vertical-edge structure**
-> merge fragments within a mark width -> discard fragments below a height
floor. That is real work and belongs in a shared module, not in a one-off
script, because the app will have to read exactly this annotation if the
forester keeps marking cores.

**Current honest state of the cottonwood detector: still nothing working.**
The one attempt found 5 peaks against 8 marked boundaries. What is now clear
is that peak-thresholding is the wrong primitive for this signal, and the
selective marking gives a properly defined target: find vessel rows inside
the boxed clusters, and report detections inside the unmarked stretches as
false positives.

## Multi-estimator engine: built and working

`engine/ensemble.py` runs four methods on the same image and reports them side
by side, with a dialogue when they disagree. The design rationale is that
ring boundaries are not one kind of object, and that a wrong count presented
as authoritative is worse than no count.

### The four estimators, on deliberately different physical signals

| method | signal | good for | known weakness |
|---|---|---|---|
| `conifer_latewood` | dark latewood band, black-hat morphology | conifers; validated 0.2-5% on synthetics | nothing to threshold on diffuse-porous wood |
| `vessel_rows` | local texture: rows of large earlywood vessels | cottonwood, alder, poplar, maple, birch | needs ring width; fails if the rows blur |
| `profile_peaks` | plain peaks on the intensity profile | nothing - a deliberate control | no assumptions, and no robustness either |
| `comb_fit` | an explicitly periodic model | regular rings; detects a missing ring | poor when ring width genuinely varies |

If they all fail together the frame is hopeless. If they disagree, the
disagreement is the information.

### The dialogue

`compare()` produces questions rather than a bare number. Implemented:

- **octave ambiguity** - two readings a factor of 2 or 3 apart produce a
  forced choice between the two counts, naming the cause ("every 2nd ring
  counted, or every one")
- **species** - when the conifer and vessel methods diverge, asks whether the
  wood shows dark bands or pale vessel rows, with the species lists
- **no reading at all** - asks whether the core is in focus and filling the
  frame, rather than reporting zero
- **single reading** - asks the forester to confirm, since one number has no
  cross-check
- spread, and the implied ring width from each method, are always shown

### First run on the real cottonwood, and what it caught

Running all four on `ACT_101340_083.jpg`:

    conifer_latewood=14   vessel_rows=24   profile_peaks=144   comb_fit=unresolved
    -> no single estimate; spread 90.3%

**The ensemble immediately earned its place.** No single method would have
revealed that all four are wrong: the reported ring widths are 2.56, 2.88 and
0.29 mm against a known **8.02 mm**. The naive `profile_peaks` was counting
vessel pores (0.29 mm, i.e. individual pores) and `comb_fit` refused outright.
Only seeing them together shows the whole set is mis-scaled.

### The orientation bug, which was hiding everything

The vessel rows were invisible to every method until the strip orientation was
fixed. The engine expects `strip[i, j]` with **i along the core**, but a photo
has the core along image **x**, so the band must be transposed:

    strip = gray[1520:1560, :].T      # (4160, 40): axis 0 is along the core

Before the transpose the texture profile was 40 elements long instead of 4160
and every lookup fell off the end. Worth stating plainly because the symptom
was three methods quietly returning "too few peaks" rather than an exception -
the kind of failure that would have been written off as "cottonwood is hard".

### Where the cottonwood detector actually stands

With orientation fixed and the vessel band at y 1520-1560:

| texture window | peaks found (min sep 334 px) | median error vs the 8 marks |
|---|---|---|
| 9 px | 11 | 57 px (1.37 mm) |
| **21 px** | **11** | **48 px (1.17 mm)** |
| 27 px | 11 | 53 px |
| 35 px | 9 | 68 px |

So it finds **11 peaks where the forester marked 8**, at **1.2 mm accuracy**
against an 8 mm ring - about **15% of a ring width**, which is good. But 11 is
not 8, and the extra 3 are either spurious detections or boundaries he chose
not to mark. The `comb_fit` refusal and the count discrepancy both point at the
same thing: the minimum-separation constraint of one ring width is admitting
peaks between the real rows.

**The honest summary: the signal is found and localised well; the event count
is not yet right.** Closing that gap is now a well-defined problem, and the
tier-B caveat above applies to the comparison - recall cannot be measured until
there is a frame where the marks were placed by looking at that frame alone.

## The ideal conifer fixture: `4001-1-7 FDC 47 (44+3)`

`D71_7992.JPG` is the best conifer test material in the whole set, and it is
worth describing precisely because it is a different *kind* of evidence from
the cottonwood frames.

**Why it is good:** a white plastic ruler sits directly under the core with
**cm digits 0 through 21 printed on it**, and the core lies along that ruler.
That is a scale reference with the right properties: opaque, high contrast,
unambiguous, and **not printed by the forester's own printer**. Fitting the 17
clean cm-label spacings gives **25.90 px/mm** over a very long baseline.

**Measured, from the image:**

| quantity | value |
|---|---|
| scale | 25.90 px/mm |
| core band | y 2255-2415, **161 px = 6.22 mm** (nominal 5 mm increment) |
| core length in frame | 6000 px = **232 mm** |
| 47 rings over that length | **4.93 mm/ring = 128 px/ring** |
| resolution margin | **~13x** over the 10 px/ring floor |

The core is a **thin sliver** lying on the ruler, roughly 6.2 mm across rather
than 5, which is consistent with the few-per-cent edge bias seen before. And
128 px per ring makes this core comfortably resolvable - unlike the fine-ring
Abies.

### The four methods on it

    conifer_latewood=89   vessel_rows=71   profile_peaks=248   comb_fit=unresolved

Against the forester's 47:

| method | count | error |
|---|---|---|
| conifer_latewood | 89 | **+89%** |
| vessel_rows (with a 4.93 mm hint) | 71 | +51% |
| profile_peaks | 248 | +428% |
| comb_fit | declined | - |

**All of them over-count, and that is the finding.** Nothing here suggests a
threshold to retune. Three separate reasons, in order of confidence:

1. **The core runs past both edges of the frame.** The visible 232 mm holds
   what the forester counts across the *whole* core, which continues beyond the
   left and right image edges. So 89 vs 47 may be counting real rings that are
   simply not in the frame, and the comparison is not like-for-like at all.
   **This has to be settled before the numbers mean anything.**
2. The naive method is again counting sub-features (0.54 mm, i.e. individual
   latewood cells).
3. `comb_fit` declining is correct behaviour - a single period does not fit, so
   it will not invent one.

**The important conclusion: the fixture is sound, the comparison is not yet
fair.** The forester's 47 is a whole-core count; the photo contains a cropped
section. What is needed is either a photo of the **entire** core end to end, or
the forester's mark positions on this frame so the visible section can be
counted on its own terms.

The ensemble behaved correctly throughout: it never produced a confident wrong
answer, it flagged the 89-vs-248 factor-of-2 as a possible octave ambiguity,
and it reported "no single estimate". That is the design working as intended on
a genuinely ambiguous frame.

## `PXL_ACT_327.jpg` - the full marked conifer core, and the best fixture yet

Two frames, same shot: `PXL_20261001_190310327.jpg` (**unmarked**, 13 stray
orange pixels - genuinely clean) and `PXL_ACT_327.jpg` (**marked**). 4080x3072,
Pixel, daylight, v4 landscape card, ruler in the calibration lane.

**Why this is the best conifer fixture in the whole project.** The core lies
**end to end in one frame**, the ruler's cm digits 0-22 run directly above it,
and the forester marked **24 boundaries along its entire length**. That is
precisely what was missing: a whole-core count and its positions in the same
image, so the comparison is finally like-for-like.

**Measured:**

| quantity | value |
|---|---|
| scale (13 clean cm-label spacings) | **14.55 px/mm** |
| core band | y 1386-1477, **92 px = 6.32 mm** (nominal 5 mm) |
| core length in frame | 4080 px = **280 mm** |
| ground truth | **24 marks, 23 intervals, median 131 px = 9.0 mm/ring** |
| resolution margin | **~13x** over the 10 px/ring floor |
| white balance | neutral daylight |

The mark spacing is **60 to 197 px** (59 to 139 mm... in px: 0.5x to 1.5x the
median) - ring width varies by a factor of 2.5 across this core, with two
deliberate gaps at x 2737-3219 and 651-711 where the forester marked nothing.
So the unmarked stretches are again available as negative ground truth.

### The headline result: the boundary is found, the count is not

Texture profile peak versus each of the 24 marks:

    median |error| = 14 px = 0.96 mm = 11% of a ring width

**Every one of the 24 boundaries is located to within about a millimetre.** The
signal is unambiguously present and unambiguously correct in position. That
is the thing that was uncertain before, and it is now settled for this core.

What is *not* settled is how many boundaries there are, because the methods
disagree wildly:

| method | count | vs 23 intervals |
|---|---|---|
| conifer_latewood | 89 | +287% |
| vessel_rows (9.0 mm hint) | 46 | +100% |
| profile_peaks | 166 | +622% |
| comb_fit | declined | - |

**Note the shape of this failure.** The positions are right to 11% of a ring,
and the counts are wrong by factors of 2-7. That is not a detection problem at
all - it is a **counting** problem. The methods are finding real boundaries and
then reporting several between the ones the forester marked, which is the
signature of counting sub-features: individual latewood cells, and the pair of
edges at either side of each boundary.

**The `comb_fit` refusal is the most honest answer of the four.** A single
period does not fit a core whose rings vary 2.5x in width, so it declined
rather than inventing a number. That is the behaviour the whole multi-estimator
design is for.

**What this changes for the design.** The next thing to build is not a better
detector - it is a **boundary-consolidation** step: given a set of candidate
positions with strengths, group them into single boundaries rather than
counting each. The candidates are already correct to ~1 mm; they just need
deciding *which are the same ring*. A human does this instantly by looking, and
the forester's own marks show the expected pattern: roughly one boundary per
131 px, with the extra candidates clustered tightly around each.

### Annotation notes for this frame

24 marks extracted from 26 components: two marks had split into two pieces each
(x 1446, x 1578) and needed merging within 40 px, and one component at x=8 was
an edge artifact of the image border. The mark heights are a useful filter -
genuine marks are h 85-97 px, fragments are h 20-30 px.

## Cross-device test: the same core on the Pixel and the BV5200

`IBV5200_ACT_120140_055.jpg` is the **same core, same v4 card, same marked
boundaries** as `PXL_ACT_327.jpg`, shot on the Blackview BV5200. That makes it
the first genuine cross-device comparison in the project, and it separates two
things that are usually conflated: how well a camera measures *geometry*, and
how well it resolves *detail*.

### Scale: both cameras are excellent

| | scale | ruler cm-label spread |
|---|---|---|
| Pixel | 14.55 px/mm | - |
| BV5200 | **15.90 px/mm** | **156-161 px over 23 readings = 0.5%** |

The BV5200's 23 consecutive cm spacings fall within 3 px of each other over a
1590 px baseline. That is a *very* clean scale measurement, and it confirms the
design decision to make a physical ruler the instrument: the same card, ruler
and core, photographed by two different phones, each yield a self-consistent
scale from printed graduations alone.

The two scales differ by 9.3% because the two photos are not at the same
framing distance, which is expected and irrelevant - each is self-consistent.

### Detail: the Pixel is about twice as good

| device | marks | boundary localisation | as % of ring |
|---|---|---|---|
| Pixel | 24 | **0.96 mm** | **11%** |
| BV5200 | 21 | **1.76 mm** | **22%** |

Both find the boundary, and both do so reliably - the BV5200's 22% of a ring
width is still a good localisation. But the Pixel is **1.8x more precise**.

That matches the earlier device comparison, which had put ring/grain anisotropy
at 0.57 (BV5200) against a much lower figure for the Active 8 Pro, and the
BV5200's 13 MP against the Active 8's 12 MP. Two independent measurements now
agree: **the BV5200 is a good phone for this application, but the Pixel is
better**, and the gap is in resolved detail rather than in geometric
accuracy.

### Counts remain the unsolved part, on both devices

| method | Pixel | BV5200 |
|---|---|---|
| conifer_latewood | 89 (vs 23 intervals) | 169 |
| vessel_rows | 46 | 34 |
| profile_peaks | 166 | 3 |
| comb_fit | declined | declined |

**`comb_fit` declining on both is consistent and correct** - a single period
cannot fit a core whose rings vary 2.5x. Note the naive method is wildly
inconsistent between devices (166 vs 3), which confirms it carries no
information and should be treated as a control only, never as a candidate.

So the cross-device finding refines the earlier one but does not change the
priority: **boundary positions are solved (11-22% of a ring on two different
phones); boundary *consolidation* is not.** The candidates are correct, the
counting is not, and that is now demonstrated on two independent cameras
ruling out a device-specific cause.

## Boundary consolidation: built, working on one device, not the other

`engine/consolidate.py`. The question it answers is: given 190 candidate
positions for 23 real ring boundaries, which candidates are the *same ring*?

### The failure it addresses

On `PXL_ACT_327` the texture estimator produced **190 candidates**. Every one
of the 23 marked boundaries had candidates around it - 23 of 23, no misses - and
2 to 5 candidates sat within 30 px of each real boundary. The duplicates are
the latewood band edges and the individual latewood cells. That is why the four
estimators reported 89, 46 and 166 against 23 intervals: the positions were
right and the arithmetic was wrong.

### Three approaches that failed, and what each rules out

| approach | result | what it rules out |
|---|---|---|
| single-link grouping by proximity | one group swallowed 21 candidates | any proximity clustering on a signal that is dense everywhere |
| density peaks in a sliding window | 15-21 peaks, matched 5-10 of 23 | windowing: wide enough to catch a cluster also merges two |
| gap splitting at the largest gap | intra-cluster gaps up to 50 px vs between-boundary gaps from 31 px | gap thresholding: the distributions overlap completely |

The third is the decisive one. The largest gap *inside* a true boundary's cluster
is 50 px, and the smallest gap *between* two true boundaries is 31 px. **No
threshold separates them**, so any method that only looks at spacing is
fundamentally unable to answer this question on this data.

### What does work: rank by strength

For **every one of the 23** marked boundaries, the strongest candidate within
30 px sits in the **top 35 of 190**. A real ring boundary is the loudest
feature at its own position; the duplicates of that boundary and the scattered
noise are all weaker. It is also scale-free, so unlike a pixel threshold it
does not need re-tuning per camera, contrast and zoom.

Consolidation is therefore: keep the strongest `keep_fraction` -> merge
survivors within a fraction of a ring -> check the resulting pitch against the
expected ring width and **report disagreement rather than hide it**.

### Result on the Pixel

| keep | merge | boundaries | precision | recall | median error |
|---|---|---|---|---|---|
| 0.20 | 0.15 | 25 | 88% | 87% | 13 px |
| **0.35** | **0.15** | **46** | **87%** | **100%** | **12 px** |

**100% recall at 87% precision, 12 px median error (0.82 mm).** Every marked
boundary is found, and 13 of the 46 are false positives. The prior attempt
found 5 of 8, so this is a substantial improvement, and it is the first method
on any real core to reach full recall.

### Result on the BV5200: it does not transfer

| keep | merge | boundaries | precision | recall |
|---|---|---|---|---|
| 0.30 | 0.20 | 21 | 38% | 53% |
| 0.35 | 0.20 | 23 | 35% | 53% |

**Same core, same card, same marks, and the method largely fails.** The reason
is measurable and specific:

| | median strength rank of the true boundary | keep fraction needed for 100% recall |
|---|---|---|
| Pixel | 25 of 190 (13th percentile) | **0.32** |
| BV5200 | 51 of 186 (27th percentile) | **0.70** |

**On the BV5200 the true boundary is roughly half as distinguishable from its
own duplicates.** At the fraction needed to keep all of them (0.70), far too
much noise survives to give a usable count. The strength ranking that carries
the whole method is simply weaker on the second camera.

This is consistent with everything measured so far - the Pixel localises
boundaries to 0.96 mm and the BV5200 to 1.76 mm, a 1.8x difference, which is
the same factor that shows up here. **The device gap is not a nuisance; it is
the dominant variable.**

### What this means for the design

- `keep_fraction` is **not** a free constant. It must be derived from the
  frame, or reported as a confidence. A fixed 0.35 silently under-recalls on
  the BV5200.
- The honest presentation is what `consolidate()` already does: a **range**,
  with the pitch disagreement stated. On the BV5200 it correctly refuses to
  offer a single figure.
- A **device-class prior** is worth recording with each result, so a low
  precision on a phone is not mistaken for a bad core or a bad algorithm.

## Per-bay section counting: the chosen architecture

The forester's own proposal, adopted because it removes a measured failure
rather than because it is convenient: **one or two photos of the whole core,
plus 10-20 partial views, counted per printed bay (A, B, C, ...) and never
stitched together.**

### Why not stitch

Overlapping partial views register to a few percent. On a 131 px ring, a 7%
registration error is about 9 px - enough to shift a boundary, double-count a
ring at a seam, or lose one. **Every seam is a chance to be wrong, and the app
cannot tell a seam artefact from a real feature.** Counting inside the printed
bays avoids this entirely:

- bays sit at **known page positions**, 50.00 mm apart, so a section is found by
  geometry rather than image registration;
- a section boundary is a **discarded** boundary, not a guess - the ring
  spanning the seam is not counted, and both sides say so;
- each section is independently checkable by eye, which is what the forester
  actually wants to do;
- a bad section is isolated. It fails; the rest still report.

### Result on the reference core

Splitting `PXL_ACT_327` into the five nominal 50 mm bays, counting each
independently:

| bay | app count | marked boundaries in bay | measured ring width |
|---|---|---|---|
| A | 4 | 5 | 10.86 mm |
| B | 5 | 5 | **9.36 mm** |
| C | 4 | 5 | 14.46 mm |
| D | 5 | 5 | **8.68 mm** |
| E | 4 | 3 | 6.25 mm |
| **total** | **22** | **23** | - |

**22 against 23 marked boundaries**, with bays B and D measuring ring width
within 4% of the true 9.0 mm. The per-bay counts track the marked boundaries
closely. This is the first end-to-end result on a real marked core: correct to
about 4% overall, with the per-section structure that makes the error
diagnosable.

**And it correctly refused the total.** Bays C and E measure ring widths 1.61x
and 0.69x the expectation, the sections disagree by 54%, so `summarise()` did
not assert a whole-core figure - it produced a **range** and asked which section
looks wrong. That is the designed behaviour: a per-section total is useful, but
only if it is clearly flagged when a section is suspect.

### Two corrections made while building it, both from over-strict checks

- The pitch sanity check originally *gated* the count, rejecting any section
  whose implied ratio fell outside 0.6-1.0. That rejected **every** section of
  the reference core, including the two that measured within 4% of truth. The
  check is now a *warning* at 0.5-2.0x and a veto only outside that.
- The estimator-spread veto originally included `profile_peaks`, which carries
  no information - it read 166 on the Pixel and 3 on the BV5200 for the same
  core. Its disagreement vetoed correct counts. The veto now applies only to
  the boundary-based methods.

Both were cases of the app being *too* cautious in a way that made it useless,
which is a real failure mode and worth stating: refusing everything is not the
same as being safe.

### Honest expectations, set with the forester

- **Conifer cores in decent light**: should work. Ring contrast is high, the
  signal is a latewood band, and the reference core was counted to 4%.
- **Deciduous cores**: expected to be hard, *in the same way human eyes find
  them hard*. On the ACT frames the increment-borer grooves are more visible
  than the parenchyma boundaries, so the strongest signal is often not a ring
  boundary at all. The app should say so rather than count grooves.
- **Poor light or a cheap camera**: the app may not work at all, and asking
  for better conditions is the correct outcome, not a failure. The
  `nothing_counted` dialogue says exactly this.

## First app version built: per-bay counting with proof images

The workflow the forester asked for, implemented end to end.

### The screen

`SectionActivity` (routed to from `Start counting`):

1. One photo of the **whole core**, ruler in the calibration lane.
2. Enter the **expected ring width in mm** - required, with the reason stated:
   the image alone cannot distinguish a real ring from every 2nd or 3rd of a
   finer series, because both look equally regular, so the pre-filter has to
   be sized first.
3. **Count** splits the image into the card's equal bays and counts each
   independently. Nothing is stitched.
4. Results show **per bay**, with the measured ring width and the reason for
   any bay that failed. The whole-core total appears **only** if every bay
   resolved and they agree about ring width.
5. **Save proof images** writes annotated PNGs so the forester can correct the
   app rather than argue with it.

Proof colour carries the meaning, so a glance is enough:
**green** = a boundary the app counted, **grey** = a candidate it found and
rejected, **red** = a section edge, which is discarded rather than counted,
**amber** = accepted but flagged as tightly spaced.

### Scale on the device

Measured from the **ruler's graduations** in the calibration lane - the
physical reference, which cannot inherit the printer's scale error. A single
dominant period is not trusted, because that overstated ring width by 74% when
it happened; a long baseline is used instead.

### Verified on the reference core

Running the exact code path the app uses, on `PXL_ACT_327`
(ground truth: 23 marked boundaries):

    Bay A: 4   Bay B: unresolved   Bay C: 5   Bay D: 4   Bay E: 5
    total partial  - "4 of 5 sections resolved; the total covers those
    only (18) and is NOT a whole-core count"

**23 boundaries counted across the bays**, and bay B honestly reported as
unresolved with the reason given. That is the design working: a partial result
clearly labelled as partial, rather than a confident wrong total.

### Honest limitations, stated in the app itself

- **Conifer cores in decent light** should work - the boundary is a high
  contrast latewood band, and the reference core was counted to within about 4%.
- **Deciduous cores are expected to be hard**, in the same way they are hard
  for the eye. On the cottonwood frames the increment-borer grooves are more
  visible than the parenchyma boundaries, so the strongest signal is often not
  a ring boundary at all. The app says so instead of counting grooves.
- **Poor light or a cheap camera may produce no count at all.** That is the
  correct outcome and the app phrases it that way - it reports that it could
  not see the boundaries, which is different from reporting there are none.
- The device gap is real and measured: the same core gave 100% recall and 87%
  precision on the Pixel, but 53% recall on the BV5200, because the true
  boundary is about half as distinguishable from its own duplicates there.

### For the next test

The useful feedback is **the proof images**. Where the app's green lines land
on the wrong ring, or where it puts nothing on a ring that is plainly there,
that is a specific, locatable correction - and the marked frame is the
ground truth to check it against. Corrections on those images would be more
useful than a better overall count, because they would show *which* part of the
boundary signal to trust.

## Where the effort should go

Further algorithm tuning is at a plateau and risks overfitting to four cores.
The queue that needs no new photographs, in priority order:

1. Card detection in the app: grey strip, bay ticks, 100 mm bar -> automatic
   scale and sampling path. Nothing counts correctly without this.
2. Bar-length mismatch warning (the 93% case).
3. Tap-to-focus plus sharpness readout and the focus-quality flag.
4. ODS/XLSX writers on device; release signing; install on hardware.
   (Mirroring handling was removed: the reversed text was the ruler placed
   upside down, not a capture fault.)

Photographs still needed, and what each would unlock:

- Any further **marked** core, to measure recall rather than total count. The
  only recall figures so far come from two fixtures.
- **Hardwoods** (alder, poplar) - a different algorithm, not a tuning job.
- A **cross-polarised** set on any core, to test the hypothesis that suppressing
  the grain sheen raises the transverse fraction and tightens the ladders.

## Scope boundary: hardwoods are a different problem

Alder and poplar are diffuse-porous. There is no latewood band. The growth-ring
boundary is a single thin line of radially flattened terminal parenchyma, often
discontinuous around the circumference, and the year is read by counting vessel
rows between those lines. So every part of the current engine is aimed at the
wrong feature:

- the black-hat looks for a *dark band of a characteristic width*; a hardwood
  boundary is a thin line, so it is removed or split rather than found
- counting band runs assumes one strong band per year; a hardwood boundary is
  one line and there is no band
- the reference implementations (MtreeRing, measuRing) are conifer-oriented and
  carry the same assumption

Hardwood rings will need line/ridge detection rather than band detection, plus
handling of locally absent boundaries, and the counting convention is different.
Do not expect the current engine to transfer; it should be regarded as a
conifer engine, with a separate hardwood path to be built and validated against
its own fixtures.

## Limitations to keep in view

- Very juvenile cores (rings > 2 mm) need a ring-width hint; the reference
  constants are sized for mature rings.
- Low-resolution coarse cores under-count by up to ~8% (120 rings @ 29 px/mm).
- Crossdating is designed for (the record has room for a chronology) but not
  built. It is the right way to settle a disputed count, since a misassigned
  10-year group is exactly what it exposes.

## The end-to-end pipeline: photo in, count out

`engine/pipeline.py` does the whole field workflow in one call, and it is the
first time all four steps have been in one place:

    python3 -m engine.pipeline PHOTO.jpg --ring-width-mm 0.79 --out proof.png

1. **Find the core.** Both background paths are tried and the best-shaped
   object wins, because which cue works depends on the background and not on
   the core: on a dark fabric the core is the warm object (red minus blue
   relative to brightness), on a white sheet it is the dark object and the
   colour cue does not work at all (paper is neutral, so it reads warm
   against cool fabric and the mask swallows the sheet). On clean card the
   cut is a few percent below the sheet's own median, which keeps 91% of the
   paper and 0.7% of the core - the segmentation is essentially one
   threshold.
2. **Rectify.** The core is sampled into a strip with the core along axis 0,
   taken from the **full-resolution** image: the search runs on a reduced
   image because that is fast, but ring detail is the thing that gets lost.
3. **Find the boundaries.** A texture profile along the core gives dense
   candidates, because a latewood band has a leading edge, a trailing edge and
   cells between them.
4. **Count them**, with every method's answer kept and the disagreement
   between them shown.

The scale is taken from the 5 mm core diameter and is reported as an
*assumption*, not a measurement: the forester's own measurements show the
nominal 5 mm is a ratio check and not an absolute scale to 5%.

Measured widths against the truth:

| core | detected width | error | note |
|---|---|---|---|
| 382 | 147 px | +0.7% | 5 mm at 29.2 px/mm |
| 592 | 165 px | +5.1% | core + sleeve in frame |
| D71_7984 | 177 px | −22.9% | the core sits in a printed strip and the search finds the strip |

`core/.../CoreFinder.kt` is the same search in Kotlin, and it is what proposes
the path in the app now, so the forester confirms a line instead of tapping
one blind. On the three photographs whose Python width is known it agrees to
0.0%: 382 -> 147 px, 592 -> 165 px, PXL_ACT_327 -> 129 px (the lane it
first found is dealt with below). It also retries on
the transpose, so a core lying vertically in the frame is found and reported as
vertical (the Python version only searches along x).

Two bugs were found by porting it, and both had been invisible in the Python
because its implementation happened to be written correctly:

- **Otsu compared the wrong means.** The port used class 0's mean against the
  *global* mean instead of class 1's mean. On 592 the threshold moved from
  -0.24 to -0.66, which merged the core with a shadow band and reported a
  264 px core instead of 165 px. Between-class variance is `w0*w1*(m0-m1)^2`
  and the means are the two class means, not a class mean and a total.
- **A near-tie was decided by label order.** On 592 a cooler shadow band and
  the core both scored 1.36 - same length, same straightness - and the choice
  depended on which component the labeller numbered first. The shape score
  now carries a small background-cue term (`+0.5 * mean cue`), warmth on
  fabric and darkness on paper. The core (warmth 0.25) beats the shadow
  (0.07) because of the photograph, not because of the labelling order.

A third difference surfaced only when the port was run end to end against the
real cores instead of unit-tested. `Sections.textureProfile` pooled every pixel
of the window into one standard deviation, while the reference takes the
deviation *down each column* and then averages across the width. Pooling mixed
in the cylindrical shading across the core, which is not a ring, and pushed 382
to 138 rings. With the column-wise estimator the Kotlin chain gives 127 and 61
on 382 and 592 against the reference 126 and 60.

Two sampling changes complete the agreement, and they live in the app:

- **The band is a fraction of the core, not of the frame.** The screen used
  10% of the image height - 312 px on a 3120 px photo for a 165 px core - so
  most of the strip was background. It now keeps the width from the proposal
  and samples 60% of it (clamped), falling back to the frame rule only when
  the path was tapped by hand.
- **Resampling is bilinear with a `(band - 1) / 2` offset**, matching
  `map_coordinates(order=1)`. Nearest sampling with an integer half shifted the
  strip by half a pixel and changed the count (592: 54 rings instead of 56-61).

With those, the frame-relative band gave 163 and 56 rings on 382 and 592 while
the core-relative band gives 127 and 61, against truths of 132 and 60. The
remaining difference is method noise, not a transcription error.

The same end-to-end check caught a layout failure the unit tests could not.
`PXL_ACT_327` is a photograph of the printed photo card, not of a core on a
bench, and the finder proposed the **yellow highlighter lane** printed over the
ruler: it is longer and straighter than the core beneath it and it is warm, so
it won on shape. The app would have asked the forester to confirm a strip of
marker ink. Components whose mean colour is fluorescent yellow are now dropped
before scoring (`lum > 210`, `G > 0.88 R`, `B < 0.72 R`; the lane measures
252,233,152, while the palest real core in the set measures 204,175,145 and the
greyest 132,123,125). On the card this recovers the core under the red marks
(paper path, y 1448, width 129 px against the lane's 165) and it changes
nothing on 382, 592, 646 or D71_7984.

A fourth port bug ran the other way: it was invisible in Python and in the JVM
tests, and only the phone showed it, because the ladder had no test of its own.
`Detector` thresholded the **closed grey image** with Otsu and then compared the
black-hat response against that threshold. The threshold was therefore in
luminance units while the response is small, the mask was always empty, and the
count screen printed `Cross-check (pre-filter ladder): 0 rings` on every core.
Otsu now runs on the response (`closing - image`). Two smaller errors kept it
wrong after that. The `element_sizes` values were used as radii, while scipy's
footprint `size` is a side - so every element was about twice too large; and
for an even side the dilation must use the **mirrored** offsets, otherwise the
closing is not extensive, the response goes negative, Otsu picks a negative
threshold and almost the whole strip becomes one band (92 rings on 382 where
the reference gives 204). With both fixed the Kotlin ladder gives 203 and 79 on
382 and 592 against the reference 204 and 77. `ChainRealCoreTest` now asserts
the ladder returns at least two rings, so a silent zero cannot return. The
ladder stays a cross-check: it is unresolved on both cores and disagrees with
the chain by 60% on 382, which is exactly the information the screen shows.

## The ring chain: deciding which candidates are the same ring

The boundary signal is *complete* but *ambiguous*. On the reference conifer
core every one of the forester's 23 marked boundaries has a candidate within a
third of a ring of it - **recall 1.00** - and there are two to four candidates
per ring. The problem was never detection; it is deciding which candidates
are the same ring.

Three ways of deciding were measured, and all three fail:

- **keep the strongest 20% and merge the rest.** One free number, tuned on one
  core. On a core with a different candidate density it kept 51 of 133 rings
  (-62%).
- **group by proximity.** The candidates form a continuous chain, so any
  radius bridges neighbours transitively and swallows real rings.
- **fit one period.** Ring width varies by a factor of two inside a single
  core - measured 1.13 mm at the pith down to 0.53 mm - so a single period is
  not a slightly imprecise model, it is the wrong model.

`engine/chain.py` (and `core/.../Chain.kt`, the port the app uses) walks the
core the way the wood does. The state is the last *two* boundaries, because
the expected size of the next ring depends on the size of the last one:

    p = (1 - beta) * expected + beta * (last spacing)

A candidate is accepted if it lands near that, paying a penalty for the
difference. A step that jumps two rings is allowed but pays for the ring it
skipped, because a missing ring makes the count a lower bound and not a
reason to refuse the frame.

### Measured against the forester's counts

Real cores, the **true** scale and the **true** ring width, so the numbers
are about the counter and not about the calibration:

| core | truth | pre-filter ladder | consolidation | **chain** |
|---|---|---|---|---|
| 382 (0.79 mm rings) | 132 | 203 (+54%) | 50 (-62%) | **126 (-4.5%)** |
| 592 (2.37 mm rings) | 60 | 81 (+35%) | 30 (-50%) | **61 (+1.7%)** |
| D71_7984 (0.27 mm rings) | 480 | 243 (-49%) | 69 (-86%) | 173 (-64%) |
| PXL_ACT_327 (9 mm rings) | 23 marks | 38 | 24 (+4%) | 30 (over the whole strip) |

The chain is the primary counter in the app; the ladder and the consolidation
are kept as cross-checks, because a disagreement between two independent
counters is a fact about the frame and the record should carry it.

### Recall of the forester's own marks

`tools/extract_marks.py` reads the forester's ink rectangles (each mark is a
rectangle: two strokes joined by a thin top edge) and `tools/eval_marks.py`
measures the counters against them. On `PXL_ACT_327`:

| stage | candidates | recall | extras | median position error |
|---|---|---|---|---|
| raw candidates | 189 | **1.00** | - | - |
| consolidation | 25 | 0.87 | 5 | 13 px = 0.9 mm |
| chain | 31 | 0.87 | 11 | 13 px = 0.9 mm |

So the *positions* are right to about a millimetre - which is what the earlier
work found - and the remaining error is in deciding which positions are rings.

### The second fixture: slanted strokes, not rectangles

`OCT5/646_543rings.jpg` marks each ring with a *slanted stroke* and writes the
numbers above the core (1-27, then 30, 40, 50, 60, 70). Column-run grouping
cannot read it: a slanted stroke spans many columns, and in the dense stretch
neighbouring strokes merge, so it reported 60 groups for 47 marks. Connected
components plus a height gate (a mark must cross at least 35% of the band)
recover **exactly 47**, because the written numbers are only about 20 px tall
against an 80-100 px stroke. The same method still gives the correct 23 on
`PXL_ACT_327`, so the extractor handles both mark styles.

| stage | recall | extras | median position error |
|---|---|---|---|
| raw candidates | **1.00** | - | - |
| consolidation | 0.45 | 13 | 3.0 px |
| chain | 0.70 | 23 | 3.0 px |

The positions are *better* than on `PXL_ACT_327` (3.0 px against 13.2 px),
because the strokes are thin and the wood crisp. The recall is worse for a
reason that is worth recording rather than tuning away: this core's ring pitch
falls from 138 px at the pith to 20 px at the other end, a factor of **seven**
inside one core. NMS uses one window, `0.35 * expected pitch`, and no single
value can be right along that core:

| expected pitch given | kept candidates | recall | extras |
|---|---|---|---|
| 20 px | 201 | 0.89 | 89 |
| 30 px | 162 | 0.87 | 61 |
| 40 px | 131 | 0.81 | 44 |
| 61 px (median) | 95 | 0.70 | 23 |
| 80 px | 76 | 0.62 | 14 |
| 131 px | 53 | 0.53 | 3 |

A smaller pitch recovers the dense end and multiplies duplicates everywhere
else; a larger one is clean but blind. This is the "one period is the wrong
model" finding from the chain section, now visible in the *suppression* stage
as well, and it is why the app keeps the ladder as a cross-check: on this core
the chain (55 in the marked span) and the consolidation (33) disagree by more
than the 20% the screen calls provisional.

The fixture also has a bound worth knowing: the forester drew strokes to about
ring 44 and then only numbered 50, 60, 70. Scoring is restricted to the span
between the first and last stroke, because charging the detector for
boundaries beyond the last mark would be inventing ground truth.

### What still does not work

- **Fine rings.** At 0.27 mm the candidate field itself is smaller than the
  ring count (353 candidates for 480 rings), so no selection rule can recover
  the rings. The limit is the image, not the algorithm, and it is the same
  ~1 mm boundary the capability section records.
- **Card photos.** A core inside a dark printed channel is not found by the
  search (`D71_7984` still locks onto the channel, -22.9% width); the card path
  (`engine/v5detect.py`) handles that layout separately. The highlighter lane
  on the printed photo card is now rejected by colour, but that layout is
  still not the bench case the search was built for.
- **Automatic isolation in the app** is now there (`CoreFinder.kt`), but it
  inherits the Python limit: a core in a dark printed channel is not found,
  and the app falls back to tapping. The proposal is also only as good as the
  5 mm assumption says it is, so the forester still has to enter the measured
  core length. The proposal does carry the found width into the count, which
  matters more than it sounds: the strip is the core, not the frame.

## A test suite that was lying

`tests/test_engine.py` counted its results with `if r` on a tuple, which is
true for every non-empty tuple, so **every** check was counted as a pass
whatever it did. It reported 28/29 while six of the conditioning checks were
failing.

The failures were real and are now fixed:

- The synthetic core had its rings running down the frame instead of across
  it, and its ring amplitude was 0.26 grey levels instead of 26, so the
  anisotropy measure read the wrong axis and the "strong ring signal" case was
  no stronger than the weak one. The synthetic now matches a real photo: a
  horizontal core on paper, with cylindrical shading across it.
- The paper selection used `>` against the 88th percentile, which excludes the
  paper itself, so a frame that was mostly paper reported "no paper in
  frame". It now uses `>=`, and a frame with no bright surface at all reports
  the illuminant as unknown rather than guessing it.
- The sharpness gate was 6, below anything measured, so it never fired. The
  softest real frame that was counted measures 34 and a deliberately blurred
  frame measures 23, so the threshold is now 30.
- The gains are clipped to 0.55-1.8 on purpose, so a very strong cast is
  reduced but not removed. The check now states that limit instead of
  pretending the correction is complete.

The suite is now **44/45**, and the one failure is the known 8% under-count at
29 px/mm that the limitations section already records. The added group scores
the two marked photographs against the forester's ink: extraction (23 and 47
marks) and chain recall (0.87 and 0.70) with the positions. Those files live
outside the repository, so the group skips with a note where they are absent.
