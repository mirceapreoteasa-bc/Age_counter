package org.agecore.core

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Count rings one printed bay at a time, and never stitch a whole core.
 *
 * The forester's proposed workflow, adopted because it removes a measured
 * failure: one or two photos of the whole core, plus 10-20 partial views,
 * counted per bay (A, B, C, ...) rather than assembled into one long strip.
 *
 * Stitching measured badly. Overlapping views register to a few per cent, and a
 * 7% error on a 131 px ring is about 9 px - enough to shift a boundary or
 * double-count a ring at a seam. Counting inside the printed bays removes the
 * seam entirely: a bay edge is a *discarded* boundary, not a guess, and both
 * sides say so.
 *
 * Honest expectations, agreed with the forester:
 *  - conifer cores in decent light should work; the boundary is a latewood band
 *  - deciduous cores are expected to be hard, in the way they are hard for the
 *    eye; on the cottonwood frames the increment-borer grooves are more visible
 *    than the parenchyma boundaries, so the strongest signal is often not a
 *    ring boundary at all
 *  - in poor light or on a cheap camera this may decline, and that is the
 *    correct outcome, not a failure
 */
object Sections {

    /**
 * One counted year: the gap between two consecutive accepted boundaries.
 *
 * Drawn as a narrow rectangle centred on the interval, so a reviewer can see
 * exactly which stretch of core the app decided to call a year. The half width
 * is a quarter of the interval, which keeps consecutive marks separate even in
 * dense wood while still sitting inside the ring they describe.
 */
data class Interval(
    val centreFrac: Float,
    val halfWidthFrac: Float,
)

/** v4 landscape card: bays 50.00 mm apart, strip 30 mm tall. */
    const val BAY_PITCH_MM = 50.0
    const val BAYS = 5

    data class Reading(
        val label: String,
        val index: Int,
        val count: Int,
        val countLow: Int,
        val countHigh: Int,
        val resolved: Boolean,
        val pxPerMm: Double,
        val ringMm: Double,
        val contrast: Double,
        /** accepted boundary positions, as a fraction of the whole image */
        val boundariesFrac: List<Float>,
        /** candidates that were found and rejected, for the proof image */
        val rejectedFrac: List<Float>,
        val warnings: List<String>,
        /**
         * One entry per counted year, in image-width fractions. The reported
         * count equals this list's size, so the number on the proof image is
         * always exactly the number of rectangles drawn on it.
         */
        val intervals: List<Interval> = emptyList(),
        /** ring lean in degrees from the core axis; 0 for square rings */
        val ringAngleDeg: Double = 0.0,
    )

    data class Report(
        val sections: List<Reading>,
        val total: Int?,          // null unless every section resolved
        val totalLow: Int,
        val totalHigh: Int,
        val status: String,        // counted | partial | unresolved
        val notes: List<String>,
        val questions: List<Question>,
    ) {
        fun summary(): String {
            val parts = sections.joinToString(", ") {
                if (it.resolved) "${it.label}=${it.count}" else "${it.label}=?"
            }
            val tail = if (total != null) "  TOTAL $total" else "  total $status"
            return parts + tail
        }
    }

    data class Question(
        val id: String,
        val text: String,
        val options: List<String>,
        val severity: String,
    )

    /**
     * Local texture along the core: the boundary signal.
     *
     * Texture rather than brightness, because the lower half of a cylindrical
     * core is in self-shadow, and a brightness threshold reads that shadow as
     * card. That mistake gave a factor-of-two error in the core diameter and
     * had to be corrected before any scale was believable.
     */
    fun textureProfile(strip: DoubleArray, length: Int, width: Int,
                       win: Int): DoubleArray {
        val out = DoubleArray(length)
        if (length < 2 * win + 2 || width <= 0) return out
        val n = 2 * win
        // The standard deviation is taken *down each column* (along the core,
        // where the ring boundary lies) and then averaged across the width.
        // Pooling the whole window instead - an earlier port did - mixes in
        // the cylindrical shading across the core, which is not a ring, and
        // measurably changes the count (382: 138 rings against 126).
        for (i in win until (length - win)) {
            var acc = 0.0
            for (j in 0 until width) {
                var s = 0.0
                var s2 = 0.0
                for (k in -win until win) {
                    val v = strip[(i + k) * width + j]
                    s += v; s2 += v * v
                }
                val m = s / n
                acc += kotlin.math.sqrt(max(0.0, s2 / n - m * m))
            }
            out[i] = acc / width
        }
        return out
    }

    private fun smooth(a: DoubleArray, w: Int = 3): DoubleArray {
        val out = DoubleArray(a.size)
        for (i in a.indices) {
            var s = 0.0; var n = 0
            for (k in -w..w) {
                val j = i + k
                if (j in a.indices) { s += a[j]; n++ }
            }
            out[i] = s / n
        }
        return out
    }

    /** A candidate boundary. Public because the proof image needs it. */
    data class Cand(val pos: Int, val strength: Double)

    /** Dense candidates, deliberately permissive: consolidation decides. */
    private fun candidates(prof: DoubleArray, relThresh: Double = 1.05): List<Cand> {
        if (prof.size < 5) return emptyList()
        val sm = smooth(prof, 2)
        val sorted = sm.sorted()
        var base = sorted[(sorted.size * 0.30).toInt()]
        if (base <= 0) base = sm.average() * 0.5
        if (base <= 0) return emptyList()
        val out = ArrayList<Cand>()
        var i = 1
        while (i < sm.size - 1) {
            if (sm[i] > base * relThresh && sm[i] >= sm[i - 1] && sm[i] > sm[i + 1]) {
                out.add(Cand(i, sm[i] - base))
                i += 4
            } else i++
        }
        return out
    }

    private data class Con(
        val pos: Int, val strength: Double, val members: Int, val spread: Double,
    )

    /**
     * Collapse duplicates of the same boundary.
     *
     * Measured on the reference core: 190 candidates for 23 real boundaries, and
     * for every marked boundary the strongest candidate within 30 px was in the
     * top 35. So the cut is by strength, which is scale-free. Proximity grouping
     * fails here - candidates form a continuous chain, so any radius merges
     * neighbours - and so does gap splitting, because the largest gap inside a
     * true cluster (50 px) exceeds the smallest gap between two true
     * boundaries (31 px).
     */
    fun consolidate(cands: List<Cand>, expectedRingPx: Double,
                    keepFraction: Double = 0.35,
                    mergeFraction: Double = 0.15): Triple<Int, Int, Boolean> {
        if (cands.isEmpty()) return Triple(0, 0, false)
        val nKeep = max(1, (cands.size * keepFraction).roundToInt())
        val kept = cands.sortedByDescending { it.strength }.take(nKeep)
            .sortedBy { it.pos }
        val radius = max(4.0, expectedRingPx * mergeFraction)
        val reps = ArrayList<Con>()
        var i = 0
        while (i < kept.size) {
            var j = i
            while (j + 1 < kept.size && (kept[j + 1].pos - kept[j].pos) <= radius) j++
            val best = kept.subList(i, j + 1).maxByOrNull { it.strength }!!
            reps.add(Con(best.pos, best.strength, j - i + 1,
                         (kept[j].pos - kept[i].pos).toDouble()))
            i = j + 1
        }
        // A count is the number of intervals, so it needs two boundaries. A
        // section with a single candidate has no interval at all: reporting
        // count=1 there claimed a ring that was never found, which is worse
        // than declining. The earlier version returned resolved=true here and
        // 801 of 1206 batch rows were exactly this case.
        if (reps.size < 2) return Triple(0, 0, false)
        val span = (reps.last().pos - reps.first().pos).toDouble()
        val ratio = span / ((reps.size - 1) * expectedRingPx)
        // A sanity check on the count, not a gate on it. Refusing everything is
        // not the same as being safe: an earlier version vetoed any section
        // whose ratio fell outside 0.6-1.0 and rejected even sections that
        // measured within 4% of the truth.
        return when {
            ratio > 2.0 -> Triple(max(1, (span / expectedRingPx).roundToInt() + 1),
                                  reps.size, false)
            ratio < 0.5 -> Triple(reps.size,
                                  max(reps.size, (span / expectedRingPx).roundToInt() + 1),
                                  false)
            else -> Triple(reps.size, reps.size, true)
        }
    }

    /**
     * Count one bay.
     *
     * @param strip       grey pixels of the bay, core along axis 0 (length x width)
     * @param sectionLeft fractional x of the bay's left edge in the whole photo
     * @param sectionRight fractional x of the right edge
     */
    fun countSection(
        strip: DoubleArray, length: Int, width: Int,
        pxPerMm: Double, label: String, index: Int,
        expectedRingMm: Double,
        sectionLeft: Float, sectionRight: Float,
        forcedAngleDeg: Double? = null,
    ): Reading {
        val warn = ArrayList<String>()
        if (length <= 0 || width <= 0) {
            warn.add("no image data for this section")
            return Reading(label, index, 0, 0, 0, false, pxPerMm, 0.0, 0.0,
                           emptyList(), emptyList(), warn)
        }
        if (pxPerMm <= 0) {
            warn.add("no scale, so a count cannot be given in millimetres")
            return Reading(label, index, 0, 0, 0, false, 0.0, 0.0, 0.0,
                           emptyList(), emptyList(), warn)
        }
        val ringPx = expectedRingMm * pxPerMm
        if (ringPx < 8) {
            warn.add("expected ring width is only ${ringPx.roundToInt()} px at " +
                     "${fmt(pxPerMm)} px/mm, below the resolvable limit")
            return Reading(label, index, 0, 0, 0, false, pxPerMm, 0.0, 0.0,
                           emptyList(), emptyList(), warn)
        }

        // Ring geometry first. A diagonal ring boundary blurs into the axis
        // profile, so the strip is de-rotated until the ring lines stand
        // vertical; x then measures spacing perpendicular to the rings. The
        // accepted positions are mapped back to the original image, and since
        // the de-rotation leaves the middle row untouched the positions stay
        // valid as image fractions.
        val angle = forcedAngleDeg ?: RingGeometry.angleDeg(strip, length, width)
        val win = max(3, (ringPx * 0.05).roundToInt())

        // Count both ways and keep the de-rotation only if it actually explains
        // the wood better.
        //
        // A lean measurement can be confidently wrong: one frame of the
        // reference core reads 0 degrees and a second frame of the very same
        // core read -13, and following the wrong lean turned a count of 18 into
        // 15. Ring spacing in real wood is fairly even year to year, so the
        // correct orientation is the one that makes the accepted boundaries
        // more regularly spaced. That is a check on the answer rather than on
        // the angle, and it holds even when the angle itself is unreliable.
        data class Try(val prof: DoubleArray, val cands: List<Cand>,
                       val n: Int, val hi: Int, val ok: Boolean, val reps: List<Int>)

        fun attempt(src: DoubleArray?): Try {
            val p = textureProfile(src ?: strip, length, width, win)
            val c = candidates(p)
            val (cn, chi, cok) = consolidate(c, ringPx)
            val kN = max(1, (c.size * 0.35).roundToInt())
            val kp = c.sortedByDescending { it.strength }.take(kN).sortedBy { it.pos }
            val rad = max(4.0, ringPx * 0.15)
            val rp = ArrayList<Int>()
            var q = 0
            while (q < kp.size) {
                var z = q
                while (z + 1 < kp.size && (kp[z + 1].pos - kp[q].pos) <= rad) z++
                rp.add(kp.subList(q, z + 1).maxByOrNull { it.strength }!!.pos)
                q = z + 1
            }
            return Try(p, c, cn, chi, cok, rp)
        }

        /** Spread of the gaps between accepted boundaries: lower is more even. */
        fun raggedness(r: Try): Double {
            val p = r.reps
            if (p.size < 3) return Double.MAX_VALUE
            val gaps = DoubleArray(p.size - 1) { (p[it + 1] - p[it]).toDouble() }
            val m = gaps.average()
            if (m <= 0) return Double.MAX_VALUE
            return Math.sqrt(gaps.sumOf { (it - m) * (it - m) } / gaps.size) / m
        }

        val flat = attempt(null)
        var useDerot = false
        var derot: DoubleArray? = null
        if (Math.abs(angle) >= 1.0) {
            val d = RingGeometry.derotate(strip, length, width, angle)
            if (d != null) {
                val rot = attempt(d)
                // Require a real improvement, not a wash.
                if (raggedness(rot) < raggedness(flat) * 0.92) {
                    useDerot = true
                    derot = d
                }
            }
        }
        val chosen = if (useDerot) attempt(derot) else flat
        if (useDerot) {
            warn.add("rings run ${fmt(Math.abs(angle))} deg off the axis; " +
                     "de-rotated, and that made the spacing more even")
        } else if (Math.abs(angle) >= 1.0) {
            warn.add("a ${fmt(Math.abs(angle))} deg lean was measured but " +
                     "correcting it did not even out the ring spacing, so the " +
                     "core was counted square")
        }

        val prof = chosen.prof
        val cands = chosen.cands
        val n = chosen.n
        val hi = chosen.hi
        val resolved = chosen.ok
        val usedAngle = if (useDerot) angle else 0.0

        val reps = ArrayList(chosen.reps)
        val radius = max(4.0, ringPx * 0.15)
        val fracOf = { p: Int -> (sectionLeft + (sectionRight - sectionLeft) *
                (p.toFloat() / max(1, length))).coerceIn(sectionLeft, sectionRight) }
        val acceptedFrac = reps.map(fracOf)
        val repSet = reps.toHashSet()
        val rejectedFrac = cands.filter { c ->
            reps.none { r -> abs(r - c.pos) <= radius }
        }.map { fracOf(it.pos) }

        var ringMm = 0.0
        var contrast = 0.0
        if (reps.size > 1) {
            val span = (reps.last() - reps.first()).toDouble()
            ringMm = span / (reps.size - 1) / pxPerMm
            contrast = (cands.maxOfOrNull { it.strength } ?: 0.0) / max(1, cands.size)
        }
        if (reps.size < 2) {
            warn.add("only ${reps.size} boundary candidate(s) found; a count " +
                     "needs at least two, so this section is unresolved")
        } else if (!resolved) {
            warn.add("section not resolved: $n to $hi boundaries")
        }

        if (ringMm > 0) {
            val ratio = ringMm / expectedRingMm
            if (ratio > 1.25 || ratio < 0.8) {
                warn.add("measured ring width ${fmt(ringMm)} mm is " +
                         String.format("%.2f", ratio) +
                         " x the expected ${fmt(expectedRingMm)} mm; " +
                         "check this section by eye")
            }
        }
        // One rectangle per counted year: the gap between each consecutive
        // pair of accepted boundaries. The count is then exactly how many
        // rectangles the proof image shows, which is what makes a count
        // reviewable. Previously the count was the number of boundaries, so a
        // bay holding 7 boundaries reported 7 while only 6 gaps exist - and
        // nothing on the image revealed the difference.
        val intervals = ArrayList<Interval>()
        for (i in 0 until acceptedFrac.size - 1) {
            val a = acceptedFrac[i]
            val b = acceptedFrac[i + 1]
            if (b <= a) continue
            intervals.add(Interval((a + b) / 2f, (b - a) * 0.125f))
        }
        val nCount = intervals.size
        warn.add("${acceptedFrac.size} boundaries gave $nCount intervals " +
                 "(width 25% of each gap)")

        return Reading(label, index, nCount, if (resolved) nCount else min(n, hi),
                       if (resolved) nCount else max(n, hi), resolved,
                       pxPerMm, ringMm, contrast, acceptedFrac, rejectedFrac,
                       warn, intervals, usedAngle)
    }

    /**
     * Assemble the core report. A total is asserted only when every section
     * resolved and they agree about ring width; otherwise a range, with the
     * failing sections named.
     */
    fun summarise(readings: List<Reading>, expectedTotal: Int? = null): Report {
        val notes = ArrayList<String>()
        val questions = ArrayList<Question>()
        val ok = readings.filter { it.resolved }
        val bad = readings.filter { !it.resolved }

        val lo = ok.sumOf { it.countLow }
        val hi = ok.sumOf { it.countHigh }

        if (bad.isEmpty() && ok.size > 1) {
            val pitches = ok.map { it.ringMm }.filter { it > 0 }
            if (pitches.isNotEmpty()) {
                val med = pitches.sorted()[pitches.size / 2]
                val dev = pitches.maxOf { abs(it - med) } / med
                if (dev > 0.25) {
                    notes.add("sections disagree about ring width by " +
                              "${(dev * 100).roundToInt()}%, so at least one " +
                              "section is wrong; no total is asserted")
                    questions.add(Question("section_pitch",
                        "The sections measure different ring widths. Which " +
                        "of these does not look right to you?",
                        ok.map { "${it.label} (${fmt(it.ringMm)} mm)" },
                        "high"))
                    return Report(readings, null, lo, hi, "unresolved",
                                  notes, questions)
                }
            }
        }

        if (bad.isEmpty() && ok.isEmpty()) {
            // No sections at all. Reporting "counted" with a total of zero
            // would claim an age of 0 for a photo that was never measured.
            notes.add("no sections were measured, so nothing can be counted")
            return Report(readings, null, 0, 0, "unresolved", notes, questions)
        }
        if (bad.isEmpty()) {
            val total = ok.sumOf { it.count }
            notes.add("all ${ok.size} sections resolved")
            if (expectedTotal != null && expectedTotal > 0) {
                val err = (total - expectedTotal) * 100.0 / expectedTotal
                notes.add("you indicated about $expectedTotal rings; " +
                          "sections total $total (${err.roundToInt()}%)")
                questions.add(Question("total_check",
                    "The sections total $total rings. Does that match roughly " +
                    "what you count by eye?", listOf("about $total",
                    "clearly more", "clearly fewer"), "medium"))
            }
            return Report(readings, total, lo, hi, "counted", notes, questions)
        }

        if (ok.isNotEmpty()) {
            notes.add("${ok.size} of ${readings.size} sections resolved; the " +
                      "total covers those only ($lo to $hi) and is NOT a " +
                      "whole-core count")
            questions.add(Question("missing_sections",
                "These sections could not be counted: " +
                bad.joinToString(", ") { it.label } +
                ". Retake them in better light, or count only the rest?",
                listOf("retake the missing sections",
                       "count only the resolved sections",
                       "the core has damaged or missing rings there"), "high"))
            return Report(readings, null, lo, hi, "partial", notes, questions)
        }

        notes.add("no section could be counted; no total is offered")
        questions.add(Question("nothing_counted",
            "None of the sections could be counted. The usual causes are " +
            "poor light, the core being out of focus, or the boundary pattern " +
            "being too fine for this camera.",
            listOf("retake in daylight", "retake closer to the core",
                   "the core has no visible rings here",
                   "use a better camera"), "high"))
        return Report(readings, null, lo, hi, "unresolved", notes, questions)
    }

    private fun fmt(v: Double) = if (v >= 10.0) String.format("%.1f", v)
                                 else String.format("%.2f", v)
}

/**
 * Locates the printed core strip inside a photo.
 *
 * The strip is the only strong, unambiguous feature in a well-framed v4 photo:
 * a dark wood band on a light card. Finding it solves two problems at once.
 *
 *  - Scale. The strip is a known 250.00 mm wide on the v4 card, so
 *    pxPerMm = stripWidthPx / 250. That is far more reliable than fitting the
 *    ruler graduations. Measured against three photos whose ruler-derived
 *    scale was already known, the strip gives -1.1%, +0.3% and +4.8% error,
 *    while ruler autocorrelation gave +10%, +227% and -50%: the ruler fit
 *    locks onto harmonics of the 1 mm ticks, the strip cannot.
 *  - Bay geometry. The bays are five equal divisions of the strip, not fifths
 *    of the image width, so this also removes the framing error that made
 *    every section drift when the card did not fill the photo.
 *
 * Returns null when no strip is found, which is the correct outcome for a
 * photo that is not a v4 card; callers must then decline rather than fall back
 * to a guessed scale.
 */
data class Strip(
    val x0: Int, val x1: Int, val y0: Int, val y1: Int,
) {
    val widthPx: Int get() = x1 - x0
    val heightPx: Int get() = y1 - y0

    /** v4 landscape strip is 250 mm across the five 50 mm bays. */
    fun pxPerMm(stripMm: Double = 250.0): Double =
        if (stripMm > 0) widthPx / stripMm else 0.0

    /** Left edge of bay [i] (0-based) as a fraction of the photo width. */
    fun bayLeft(i: Int, bays: Int, imgW: Int): Double =
        (x0 + widthPx * i.toDouble() / bays) / imgW

    /** Right edge of bay [i] as a fraction of the photo width. */
    fun bayRight(i: Int, bays: Int, imgW: Int): Double =
        (x0 + widthPx * (i + 1).toDouble() / bays) / imgW
}

object StripFinder {
    /**
     * @param grey row-major luminance, width * height
     */
    fun find(grey: FloatArray, w: Int, h: Int,
             darkLimit: Float = 170f, minWidthFrac: Double = 0.25,
             minHeightPx: Int = 8): Strip? {
        if (w <= 0 || h <= 0 || grey.size < w * h) return null

        // Rows whose mean is dark are candidate strip rows. The card is light
        // and the wood is dark, so the strip is the longest run of dark rows.
        val rowDark = BooleanArray(h)
        for (y in 0 until h) {
            var s = 0f
            for (x in 0 until w) s += grey[y * w + x]
            rowDark[y] = s / w < darkLimit
        }
        var bestLen = 0; var bestY0 = -1; var bestY1 = -1
        var y = 0
        while (y < h) {
            if (rowDark[y]) {
                val y0 = y
                while (y < h && rowDark[y]) y++
                if (y - y0 > bestLen) { bestLen = y - y0; bestY0 = y0; bestY1 = y }
            } else y++
        }
        if (bestLen < minHeightPx || bestY0 < 0) return null

        // The strip is a thin horizontal band of wood on a light card, so it
        // must be much wider than tall and must not run off the top or bottom
        // of the photo. Without these tests a large dark area anywhere in the
        // frame - a dark background, a shadow, clothing - is accepted as the
        // strip and then scaled as if it were 250 mm wide, which silently
        // invents a scale. Refusing here is the correct outcome.
        if (bestLen * 8 > w) return null
        if (bestY0 <= 0 || bestY1 >= h) return null

        // Within the strip rows, the strip columns are the longest dark run.
        val colDark = BooleanArray(w)
        for (x in 0 until w) {
            var s = 0f
            for (yy in bestY0 until bestY1) s += grey[yy * w + x]
            colDark[x] = s / (bestY1 - bestY0) < darkLimit
        }
        var cw = 0; var cw0 = -1; var cw1 = -1
        var x = 0
        while (x < w) {
            if (colDark[x]) {
                val x0 = x
                while (x < w && colDark[x]) x++
                if (x - x0 > cw) { cw = x - x0; cw0 = x0; cw1 = x }
            } else x++
        }
        if (cw0 < 0 || cw < (minWidthFrac * w).toInt()) return null
        // A strip touching both left and right edges is the frame, not the
        // card: the printed strip is inset from the card edges.
        if (cw0 <= 0 && cw1 >= w) return null

        // The card around the strip must be markedly lighter than the wood.
        val bandMean = meanLum(grey, w, h, cw0, cw1, bestY0, bestY1)
        val above = meanLum(grey, w, h, cw0, cw1,
            maxOf(0, bestY0 - bestLen), bestY0)
        val below = meanLum(grey, w, h, cw0, cw1, bestY1,
            minOf(h, bestY1 + bestLen))
        if (above < bandMean + 25.0 || below < bandMean + 25.0) return null

        return Strip(cw0, cw1, bestY0, bestY1)
    }

    private fun meanLum(grey: FloatArray, w: Int, h: Int,
                        x0: Int, x1: Int, y0: Int, y1: Int): Double {
        if (x1 <= x0 || y1 <= y0) return 0.0
        var s = 0.0
        var n = 0
        for (y in y0 until minOf(h, y1)) for (x in x0 until minOf(w, x1)) {
            s += grey[y * w + x]; n++
        }
        return if (n == 0) 0.0 else s / n
    }
}

/**
 * Ring geometry: rings are not always square to the core axis.
 *
 * A core from a tree on a steep slope, or simply a core cut at a slant, has
 * ring boundaries that cross the core diagonally. Two things follow, and both
 * matter for a 470-year high-altitude core where rings are very narrow and the
 * obliquity is large:
 *
 *  - Marking. A vertical rectangle per year is meaningless when the rings run
 *    diagonally: it cuts across the ring it claims to be inside. The marker has
 *    to lie along the ring, in the middle of the band between two dark lines.
 *  - Counting. Collapsing the core to one profile along the axis blurs a
 *    diagonal boundary over its full slant, so the boundary contrast that the
 *    detector depends on is destroyed. Shearing the strip until the ring lines
 *    are vertical restores it, and x then measures spacing perpendicular to the
 *    rings, which is the correct measure of annual growth.
 *
 * The angle is found by shear-and-score rather than by tracking single lines
 * down the core: for each candidate angle the strip is sheared, collapsed, and
 * scored on how much high-frequency structure survives. The angle that
 * leaves the most is the ring angle. Validated by shearing a real core by
 * known amounts, the recovered magnitude was within 0.2 degrees over 0-45
 * degrees. Line-following was tried first and drifted badly on noisy wood.
 */
object RingGeometry {
    /** Beyond this the core is not a ring-counting problem at all. */
    const val MAX_ANGLE_DEG = 40.0

    /**
     * Sub-sample factor for the angle search. The angle is a coarse property of
     * the whole bay, so a quarter of the rows and columns is plenty and keeps
     * the search cheap enough to run on a phone.
     */
    private const val DS = 2

    /**
     * Collapses the strip along the across-core axis after shearing it, so the
     * ring lines stand vertical in the result.
     *
     * The buffer layout is `strip[i * m + j]`, where `i` runs along the core
     * over `n` samples and `j` runs across the core over `m` rows. Getting
     * these two axes the wrong way round shears across the core instead of
     * along it, which is what the first version did: it reported 11-39 degrees
     * of ring lean on cores whose rings are square to the axis.
     */
    private fun shearProfile(
        strip: DoubleArray, n: Int, m: Int, deg: Double, step: Int
    ): DoubleArray {
        val prof = DoubleArray(n)
        val t = Math.tan(Math.toRadians(deg))
        val pivot = m / 2
        var rows = 0
        var j = 0
        while (j < m) {
            val d = Math.round(t * (j - pivot)).toInt()
            var i = 0
            while (i < n) {
                val src = i - d
                if (src in 0 until n) prof[i] += strip[src * m + j]
                i++
            }
            rows++
            j += step
        }
        if (rows == 0) return prof
        for (i in prof.indices) prof[i] /= rows
        return prof
    }

    /** High-frequency energy left along the core: higher means better aligned. */
    private fun score(prof: DoubleArray, n: Int, step: Int): Double {
        if (n < 12) return 0.0
        var s = 0.0
        var cnt = 0
        var i = 4
        while (i < n - 4) {
            var acc = 0.0
            var k = 0
            while (k < 9) { acc += prof[i - 4 + k]; k++ }
            acc /= 9.0
            val d = prof[i] - acc
            s += d * d
            cnt++
            i += step
        }
        return if (cnt == 0) 0.0 else Math.sqrt(s / cnt)
    }

    /**
     * Ring angle in degrees from vertical (the core axis). Positive means the
     * ring lines lean towards +x as y increases. Returns 0.0 when the core is
     * too small to judge.
     */
    fun angleDeg(strip: DoubleArray, n: Int, m: Int): Double {
        if (n < 40 || m < 3) return 0.0
        var bestA = 0.0
        var bestS = -1.0
        var zeroS = -1.0
        var a = -MAX_ANGLE_DEG
        while (a <= MAX_ANGLE_DEG) {
            val sc = score(shearProfile(strip, n, m, a, DS), n, DS)
            if (a == 0.0) zeroS = sc
            if (sc > bestS) { bestS = sc; bestA = a }
            a += 2.0
        }
        // A short window barely constrains the lean: with a 360 px bay and 24
        // rows the score varies by only a few percent across many angles, and
        // noise wins. So a lean is only believed when it beats the square
        // reading by a clear margin; otherwise the rings are treated as square.
        // Reporting a lean we cannot support is worse than reporting none,
        // because it shears the strip and changes the count.
        if (zeroS > 0 && bestS < zeroS * MIN_GAIN) return 0.0
        var f = bestA - 2.0
        while (f <= bestA + 2.0) {
            val sc = score(shearProfile(strip, n, m, f, DS), n, DS)
            if (sc > bestS) { bestS = sc; bestA = f }
            f += 0.25
        }
        // The sign is opposite to the shear that undoes the lean, so negate to
        // report the lean of the rings themselves.
        return -bestA
    }

    /** How much better a claimed lean must be than square before it is used. */
    const val MIN_GAIN = 1.10

    /**
     * Shears [strip] so the ring lines become vertical, keeping only the part
     * that the shear maps fully inside. x then runs perpendicular to the rings.
     *
     * @return null when the shear would leave too little to measure
     */
    fun derotate(strip: DoubleArray, n: Int, m: Int,
                 angleDeg: Double): DoubleArray? {
        if (Math.abs(angleDeg) < 0.5) return strip
        val t = Math.tan(Math.toRadians(angleDeg))
        val maxShift = Math.abs(t * m).toInt() + 1
        if (maxShift * 2 > n / 4) return null
        val out = DoubleArray(n * m)
        val pivot = m / 2
        for (j in 0 until m) {
            val d = Math.round(-t * (j - pivot)).toInt()
            for (i in 0 until n) {
                val src = i - d
                if (src in 0 until n) out[i * m + j] = strip[src * m + j]
            }
        }
        return out
    }
}

/**
 * Self-check for [RingGeometry] against synthetic strips with a known lean.
 *
 * Rings are modelled as a cosine across the core, so the boundary lines are
 * exactly vertical before shearing and exactly at the injected angle after.
 * Without this there is no way to tell a wrong axis order in the shear from a
 * genuinely oblique core, and that mistake once reported 11-39 degrees of lean
 * on cores whose rings are square to the axis.
 */
fun ringGeometrySelfTest(): String {
    val out = StringBuilder()
    val n = 600
    val m = 40
    val period = 22.0
    fun strip(deg: Double): DoubleArray {
        val t = Math.tan(Math.toRadians(deg))
        val a = DoubleArray(n * m)
        for (j in 0 until m) {
            val d = Math.round(t * (j - m / 2)).toInt()
            for (i in 0 until n) {
                val src = i - d
                if (src in 0 until n) {
                    a[i * m + j] = 200.0 - 60.0 * Math.sin(2.0 * Math.PI * src / period)
                }
            }
        }
        return a
    }
    for (truth in doubleArrayOf(0.0, 5.0, 10.0, 20.0, 30.0, -15.0, -30.0)) {
        val got = RingGeometry.angleDeg(strip(truth), n, m)
        val d = RingGeometry.derotate(strip(truth), n, m, got)
        out.append("truth ${"%+.1f".format(truth)} -> got ${"%+.2f".format(got)}" +
            "  err ${"%+.2f".format(got - truth)}  " +
            "derotate=${if (d != null) "ok" else "refused"}\n")
    }
    return out.toString()
}
