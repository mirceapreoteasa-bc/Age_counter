package org.agecore.core

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Ring detection, ported from the validated Python engine
 * (AgeCoreApp/engine/detect.py), which follows MtreeRing 1.4.5 (Soille & Misson
 * 2001 morphology).
 *
 * Status: validated against synthetic cores to 0.2-5%, and measured on two
 * expert-marked real cores at 94% recall of the forester's marks on a 1.78 mm
 * ring core and 52% on a 0.25 mm core. It is a *candidate proposer*, not an
 * authority: the forester accepts or rejects. It never silently reports a
 * number it cannot stand behind - see [Result.resolved].
 */
object Detector {

    const val SE_OPENING_MM = 25.4 / 400.0
    const val SE_CLOSING_MM = 25.4 / 80.0
    const val SE_BLACKHAT_MM = 25.4 / 10.0
    const val MIN_RING_MM = 0.1
    const val MIN_PX_PER_RING = 6.0

    /** s2 element and black-hat as multiples of the ring width. */
    private val LADDER = listOf(
        0.20 to 0.50, 0.20 to 0.60, 0.20 to 0.70,
        0.28 to 0.50, 0.28 to 0.60, 0.28 to 0.70,
        0.36 to 0.50, 0.36 to 0.60, 0.36 to 0.70,
    )

    data class Band(
        val startPx: Int,
        val endPx: Int,
        val centrePx: Double,
        val widthPx: Double,
        val widthMm: Double,
        val crossLo: Int,
        val crossHi: Int,
    )

    data class Result(
        val count: Int,
        val bands: List<Band>,
        val pxPerMm: Double,
        val spacingMm: Double,
        val pxPerRing: Double,
        val contrast: Double,
        val confidence: Int?,
        val resolved: Boolean,
        val warnings: List<String>,
        val ladderCounts: List<Int>,
    )

    // --- separable min/max filters (grey morphology) ------------------------

    /**
     * Exact min/max over a rectangular window, in O(1) per pixel with a
     * monotonic deque.
     *
     * `size` is the footprint **side**, as in engine/detect.py (`_rect(size)`
     * fed to scipy's grey_erosion), not a radius: scipy anchors an even
     * footprint at size/2, so erosion spans [x - size/2, x + (size-1)/2]. The
     * dilation is the mirror image of that window; only then is closing
     * extensive. Treating the Python size as a radius doubled every element,
     * and using the unmirrored window made the black-hat response negative;
     * either way the ladder collapsed.
     *
     * The naive loop this replaces cost 12.6 s on a 4156 x 98 strip for the
     * nine ladder rungs (each element runs four such passes).
     */
    private fun slide(
        src: DoubleArray, w: Int, h: Int, loOff: Int, hiOff: Int, isMin: Boolean,
    ): DoubleArray {
        val tmp = DoubleArray(src.size)
        val dq = IntArray(max(w, h))
        for (y in 0 until h) {
            val row = y * w
            var head = 0; var tail = 0; var next = 0
            for (x in 0 until w) {
                val hi = min(w - 1, x + hiOff)
                while (next <= hi) {
                    val v = src[row + next]
                    while (tail > head) {
                        val bv = src[row + dq[tail - 1]]
                        if (if (isMin) bv >= v else bv <= v) tail-- else break
                    }
                    dq[tail++] = next
                    next++
                }
                val lo = max(0, x - loOff)
                while (tail > head && dq[head] < lo) head++
                tmp[row + x] = src[row + dq[head]]
            }
        }
        val out = DoubleArray(src.size)
        for (x in 0 until w) {
            var head = 0; var tail = 0; var next = 0
            for (y in 0 until h) {
                val hi = min(h - 1, y + hiOff)
                while (next <= hi) {
                    val v = tmp[next * w + x]
                    while (tail > head) {
                        val bv = tmp[dq[tail - 1] * w + x]
                        if (if (isMin) bv >= v else bv <= v) tail-- else break
                    }
                    dq[tail++] = next
                    next++
                }
                val lo = max(0, y - loOff)
                while (tail > head && dq[head] < lo) head++
                out[y * w + x] = tmp[dq[head] * w + x]
            }
        }
        return out
    }

    private fun minFilter(src: DoubleArray, w: Int, h: Int, size: Int): DoubleArray =
        slide(src, w, h, size / 2, (size - 1) / 2, isMin = true)

    private fun maxFilter(src: DoubleArray, w: Int, h: Int, size: Int): DoubleArray =
        slide(src, w, h, (size - 1) / 2, size / 2, isMin = false)

    private fun open(a: DoubleArray, w: Int, h: Int, size: Int) =
        maxFilter(minFilter(a, w, h, size), w, h, size)

    private fun close(a: DoubleArray, w: Int, h: Int, size: Int) =
        minFilter(maxFilter(a, w, h, size), w, h, size)

    /** Otsu threshold. */
    fun otsu(values: DoubleArray): Double {
        var lo = Double.MAX_VALUE; var hi = -Double.MAX_VALUE
        for (v in values) { if (v < lo) lo = v; if (v > hi) hi = v }
        if (hi <= lo) return hi
        val bins = 256
        val hist = IntArray(bins)
        val scale = (bins - 1) / (hi - lo)
        for (v in values) hist[((v - lo) * scale).roundToInt().coerceIn(0, bins - 1)]++
        val total = values.size.toDouble()
        val p = DoubleArray(bins) { hist[it] / total }
        val centres = DoubleArray(bins) { lo + it / scale }
        var w0 = 0.0; var csum = 0.0; var best = -1.0; var bestT = lo
        var mTotal = 0.0
        for (i in 0 until bins) mTotal += p[i] * centres[i]
        for (i in 0 until bins) {
            w0 += p[i]; csum += p[i] * centres[i]
            val w1 = 1.0 - w0
            if (w0 <= 0 || w1 <= 0) continue
            val between = w0 * w1 * (csum / w0 - mTotal) * (csum / w0 - mTotal)
            if (between > best) { best = between; bestT = centres[i] }
        }
        return bestT
    }

    private fun pass(
        strip: DoubleArray, len: Int, band: Int, pxPerMm: Double,
        s2mm: Double, bhmm: Double, cover: Double,
    ): Pair<List<Band>, Double> {
        val w = band
        val h = len
        val s1 = max(1, (SE_OPENING_MM * pxPerMm).roundToInt())
        val s2 = max(1, (s2mm * pxPerMm).roundToInt())
        val bh = max(3, (bhmm * pxPerMm).roundToInt())

        var img = open(strip, w, h, s1)
        img = close(img, w, h, s1)
        img = open(img, w, h, s2)
        // Black-hat response: closing fills the dark latewood, so the
        // difference is the ring signal. Otsu must threshold *this*, not the
        // closed grey image - doing that put the threshold in luminance units
        // and the mask was always empty (the ladder reported 0 rings).
        val hat = close(img, w, h, bh)
        val response = DoubleArray(strip.size) { hat[it] - img[it] }
        val thr = otsu(response)

        val mask = BooleanArray(strip.size)
        for (i in strip.indices) mask[i] = response[i] > thr
        val signal = DoubleArray(h)
        for (y in 0 until h) {
            var c = 0
            for (x in 0 until w) if (mask[y * w + x]) c++
            signal[y] = c.toDouble() / w
        }
        val minRun = max(1, (MIN_RING_MM * pxPerMm).roundToInt())
        val bands = ArrayList<Band>()
        var y = 0
        while (y < h) {
            if (signal[y] <= cover) { y++; continue }
            val start = y
            while (y < h && signal[y] > cover) y++
            val end = y - 1
            if (end - start + 1 < minRun) continue
            var clo = -1; var chi = -1
            for (r in start..end) for (c in 0 until w) {
                if (mask[r * w + c]) { if (clo < 0) clo = c; chi = c }
            }
            if (clo < 0) continue
            val widthPx = (end - start + 1).toDouble()
            bands.add(
                Band(start, end, 0.5 * (start + end), widthPx, widthPx / pxPerMm, clo, chi),
            )
        }
        var inSum = 0.0; var inN = 0; var outSum = 0.0; var outN = 0
        for (v in signal) { if (v > cover) { inSum += v; inN++ } else { outSum += v; outN++ } }
        val contrast = if (inN > 0 && outN > 0) inSum / inN - outSum / outN else 0.0
        return bands to contrast
    }

    /**
     * Count rings on a strip oriented with the core along axis 0.
     *
     * @param ringWidthMm known ring width in mm, or null if unknown - in which
     *   case the result is unresolved, because no image can distinguish real
     *   rings from every 2nd or 3rd ring of a finer series.
     */
    fun count(
        strip: DoubleArray, len: Int, band: Int, pxPerMm: Double,
        ringWidthMm: Double?, cover: Double = 0.5,
    ): Result {
        if (ringWidthMm == null || ringWidthMm <= 0.0) {
            return Result(
                0, emptyList(), pxPerMm, 0.0, 0.0, 0.0, null, false,
                listOf(
                    "not_resolved: ring width required. Enter the expected " +
                        "ring count for this core (or the ring width in mm).",
                ),
                emptyList(),
            )
        }
        val counts = ArrayList<Int>()
        var best: Pair<List<Band>, Double>? = null
        for ((s2m, bhm) in LADDER) {
            val r = pass(strip, len, band, pxPerMm, s2m * ringWidthMm, bhm * ringWidthMm, cover)
            if (r.first.size >= 2) {
                counts.add(r.first.size)
                if (best == null || r.second > best!!.second) best = r
            }
        }
        val b = best ?: pass(strip, len, band, pxPerMm, 0.25 * ringWidthMm, 0.7 * ringWidthMm, cover)
        val bands = b.first
        val spacing = if (bands.size > 1) {
            val d = DoubleArray(bands.size - 1) { bands[it + 1].centrePx - bands[it].centrePx }
            d.sorted()[d.size / 2]
        } else 0.0

        val warn = ArrayList<String>()
        if (counts.size < 3) {
            return Result(
                bands.size, bands, pxPerMm, spacing / pxPerMm, spacing, b.second, null, false,
                listOf("not_resolved: too few rings found to judge stability."), counts,
            )
        }
        val sorted = counts.sorted()
        val median = sorted[sorted.size / 2]
        val p20 = sorted[(sorted.size * 0.2).toInt().coerceIn(0, sorted.size - 1)]
        val p80 = sorted[(sorted.size * 0.8).toInt().coerceIn(0, sorted.size - 1)]
        val conf = (p80 - p20).coerceIn(0, 99)
        val agree = counts.count { abs(it - median) <= 2 }
        val resolved = agree >= max(3, counts.size - 1) && median >= 2
        if (!resolved) {
            warn.add(
                "not_resolved: count varies ${sorted.first()}-${sorted.last()} with the " +
                    "pre-filter scale (now ${"%.3f".format(ringWidthMm)} mm). Try a " +
                    "closer photo, raking light, or set the ring width manually.",
            )
        } else if (conf > 25) {
            warn.add("pre-filter sensitivity $conf rings; treat the count as provisional.")
        }
        if (spacing < MIN_PX_PER_RING) {
            warn.add("not_resolved: only ${"%.1f".format(spacing)} px per ring.")
        }
        return Result(
            median, bands, pxPerMm, spacing / pxPerMm, spacing, b.second,
            if (resolved) conf else null, resolved, warn, counts,
        )
    }

    /** One stretch of the core, counted on its own, with a quality verdict. */
    data class Interval(
        val startPx: Int,
        val endPx: Int,
        val count: Int,
        val spacingPx: Double,
        /** "good", "marginal" or "poor" - judged from pre-filter sensitivity. */
        val quality: String,
        val spread: Int,
    )

    /**
     * Count the core interval by interval and label each stretch.
     *
     * This is what lets the proof image show *where* the count is weak: the
     * app draws counted intervals and leaves poor stretches visibly unmarked,
     * which says more than any single confidence figure because it is local and
     * direct. A reader can see the gap rather than being told about it.
     */
    fun countIntervals(
        strip: DoubleArray, len: Int, band: Int, pxPerMm: Double,
        ringWidthMm: Double, intervalCount: Int = 6,
    ): List<Interval> {
        if (ringWidthMm <= 0 || len < 4) return emptyList()
        val n = intervalCount.coerceAtLeast(1)
        val step = len / n
        if (step < 40) return emptyList()
        val out = ArrayList<Interval>()
        for (i in 0 until n) {
            val a = i * step
            val b = if (i == n - 1) len else a + step
            val sub = strip.copyOfRange(a * band, b * band)
            val counts = ArrayList<Int>()
            var contrast = 0.0
            for ((s2m, bhm) in LADDER) {
                val r = pass(sub, b - a, band, pxPerMm,
                    s2m * ringWidthMm, bhm * ringWidthMm, 0.5)
                contrast = max(contrast, r.second)
                if (r.first.size >= 1) counts.add(r.first.size)
            }
            if (counts.isEmpty()) {
                out.add(Interval(a, b, 0, 0.0, "poor", 0))
                continue
            }
            val sorted = counts.sorted()
            val count = sorted[sorted.size / 2]
            val spread = sorted.last() - sorted.first()
            val spacing = if (count > 1) step.toDouble() / count else 0.0
            val goodLimit = max(2, (0.10 * count).roundToInt())
            val marginalLimit = max(4, (0.25 * count).roundToInt())
            val quality = when {
                count < 2 -> "poor"
                spread <= goodLimit -> "good"
                spread <= marginalLimit -> "marginal"
                else -> "poor"
            }
            out.add(Interval(a, b, count, spacing, quality, spread))
        }
        return out
    }
}
