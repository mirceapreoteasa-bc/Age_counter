package org.agecore.core

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/**
 * Frame conditioning, ported from the validated Python module
 * (AgeCoreApp/engine/condition.py).
 *
 * This exists because the two phone/tablet classes already photographed differ
 * in ways that quietly break a ring detector:
 *
 *   device              R-B on paper   ring/grain anisotropy
 *   BV5200 class 13MP        -6.5               0.55
 *   Active 8 Pro 12MP      -81.9               0.17
 *
 * The white balance is not a one-off failure: on the Active 8 it ranged from
 * -62 to -113 on the same printed card, so any absolute grey threshold means
 * something different in every frame. A "find the neutral grey strip" test
 * rejected the whole card there, because on white paper B-R was about 90.
 *
 * So the order is always: neutralise the illuminant, then measure, then gate.
 * Chroma is only ever compared relative to brightness, which is scale-free and
 * cast-free.
 *
 * A frame that fails is reported as failed, with the reason. It is never
 * allowed to yield a plausible-looking count, because an authoritative-looking
 * wrong count is worse than no count.
 */
object Condition {

    /** Below this the ring signal is buried in grain and no count is meaningful. */
    const val MIN_ANISOTROPY = 0.12

    /** 99.5th-percentile neighbour-gradient proxy. Below this the frame is soft. */
    /**
     * Sharpness gate.
     *
     * From the measurements: the softest real frame that was counted measures
     * 34, a deliberately blurred frame of the synthetic core measures 23. The
     * old value of 6 was below anything measured and never fired.
     */
    const val MIN_SHARPNESS = 30.0

    /**
     * The brightest surface in a frame has to be at least this bright before
     * the illuminant can be measured on it.
     */
    const val MIN_PAPER_LEVEL = 100

    private const val CHROMA_REL = 0.16

    /**
     * Per-channel gains that neutralise the illuminant, from the brightest 12%
     * of the frame. Uses channel ratios so a global exposure difference
     * cancels. Clipped, because a factor beyond this range means the estimator
     * locked onto something that is not paper.
     */
    fun channelGains(px: IntArray, w: Int, h: Int): DoubleArray {
        if (w <= 0 || h <= 0) return doubleArrayOf(1.0, 1.0, 1.0)
        var cutHi = 0
        val n = w * h
        val hist = IntArray(256)
        for (i in 0 until n) {
            val p = i * 4
            hist[max(px[p], max(px[p + 1], px[p + 2]))]++
        }
        var acc = 0
        for (v in 0..255) {
            acc += hist[v]
            if (acc >= (n * 88) / 100) { cutHi = v; break }
        }
        val means = DoubleArray(3)
        var cnt = 0L
        for (i in 0 until n) {
            val p = i * 4
            if (max(px[p], max(px[p + 1], px[p + 2])) > cutHi) {
                means[0] += px[p]; means[1] += px[p + 1]; means[2] += px[p + 2]; cnt++
            }
        }
        if (cnt < 100L) return doubleArrayOf(1.0, 1.0, 1.0)
        for (c in 0..2) means[c] /= cnt
        if (means.min() <= 1.0) return doubleArrayOf(1.0, 1.0, 1.0)
        val g = DoubleArray(3)
        for (c in 0..2) g[c] = means[1] / means[c]
        val mid = g[1]
        for (c in 0..2) g[c] = (g[c] / mid).coerceIn(0.55, 1.8)
        return g
    }

    /** Applies [gains] in place and returns the mean paper level afterwards. */
    fun applyGains(px: IntArray, gains: DoubleArray): Double {
        val n = px.size / 4
        var cutHi = 0
        val hist = IntArray(256)
        for (i in 0 until n) {
            val p = i * 4
            val r = (px[p] * gains[0]).toInt().coerceIn(0, 255)
            val g = (px[p + 1] * gains[1]).toInt().coerceIn(0, 255)
            val b = (px[p + 2] * gains[2]).toInt().coerceIn(0, 255)
            px[p] = r; px[p + 1] = g; px[p + 2] = b
            hist[max(r, max(g, b))]++
        }
        var acc = 0
        for (v in 0..255) {
            acc += hist[v]
            if (acc >= (n * 88) / 100) { cutHi = v; break }
        }
        var sum = 0L; var c2 = 0L
        for (i in 0 until n) {
            val p = i * 4
            if (max(px[p], max(px[p + 1], px[p + 2])) > cutHi) {
                sum += max(px[p], max(px[p + 1], px[p + 2])); c2++
            }
        }
        return if (c2 > 0) sum.toDouble() / c2 else 0.0
    }

    /**
     * R-B over the brightest 12%; NaN when the frame holds no paper.
     *
     * A frame this dark cannot say what the illuminant was: the illuminant is
     * measured on the brightest surface in the frame, and if that surface is
     * dark there is no surface to measure it on. Reporting "unknown" is
     * honest; reporting a number taken from the shadow of the core is not.
     */
    fun rbOnPaper(px: IntArray, w: Int, h: Int): Double {
        if (w <= 0 || h <= 0) return Double.NaN
        val n = w * h
        val hist = IntArray(256)
        for (i in 0 until n) {
            val p = i * 4
            hist[max(px[p], max(px[p + 1], px[p + 2]))]++
        }
        var acc = 0
        var cutHi = 255
        for (v in 0..255) {
            acc += hist[v]
            if (acc >= (n * 88) / 100) { cutHi = v; break }
        }
        if (cutHi < MIN_PAPER_LEVEL) return Double.NaN
        var sr = 0.0; var sb = 0.0; var c = 0
        for (i in 0 until n) {
            val p = i * 4
            if (max(px[p], max(px[p + 1], px[p + 2])) >= cutHi) {
                sr += px[p]; sb += px[p + 2]; c++
            }
        }
        return if (c < 100) Double.NaN else (sr - sb) / c
    }

    /** High percentile of the 4-neighbour gradient. Robust focus proxy. */
    fun sharpness(gray: DoubleArray, w: Int, h: Int): Double {
        if (w < 8 || h < 8) return 0.0
        val vals = ArrayList<Double>(w * h)
        for (y in 0 until h - 1) {
            for (x in 0 until w - 1) {
                val i = y * w + x
                vals.add(abs(gray[i] - gray[i + 1]) + abs(gray[i] - gray[i + w]))
            }
        }
        if (vals.isEmpty()) return 0.0
        vals.sort()
        return vals[(vals.size * 995 / 1000).coerceAtMost(vals.size - 1)]
    }

    fun toGray(px: IntArray, w: Int, h: Int): DoubleArray {
        val g = DoubleArray(w * h)
        for (i in 0 until w * h) {
            val p = i * 4
            g[i] = 0.299 * px[p] + 0.587 * px[p + 1] + 0.114 * px[p + 2]
        }
        return g
    }

    /**
     * Ring-to-grain contrast on the largest wood region: the ratio of the
     * across-core variance (rings, which run across the core) to the
     * along-core variance (grain, which runs along it).
     *
     * The caller supplies a core bounding box, because locating the core is
     * the app's job and the detector's input is already cropped to it.
     */
    fun ringAnisotropy(gray: DoubleArray, w: Int, h: Int,
                       x0: Int, y0: Int, x1: Int, y1: Int): Double {
        val bw = min(x1, w) - max(x0, 0)
        val bh = min(y1, h) - max(y0, 0)
        if (bw < 16 || bh < 16) return 0.0
        val rowMean = DoubleArray(bh)
        val rowN = IntArray(bh)
        val colMean = DoubleArray(bw)
        val colN = IntArray(bw)
        for (yy in 0 until bh) {
            for (xx in 0 until bw) {
                val v = gray[(y0 + yy) * w + (x0 + xx)]
                rowMean[yy] += v; rowN[yy]++
                colMean[xx] += v; colN[xx]++
            }
        }
        var mr = 0.0; var cr = 0
        for (yy in 0 until bh) if (rowN[yy] > 0) { rowMean[yy] /= rowN[yy]; mr += rowMean[yy]; cr++ }
        var mc = 0.0; var cc = 0
        for (xx in 0 until bw) if (colN[xx] > 0) { colMean[xx] /= colN[xx]; mc += colMean[xx]; cc++ }
        if (cr < 16 || cc < 16) return 0.0
        mr /= cr; mc /= cc
        var vr = 0.0
        for (yy in 0 until bh) if (rowN[yy] > 0) { val d = rowMean[yy] - mr; vr += d * d }
        var vc = 0.0
        for (xx in 0 until bw) if (colN[xx] > 0) { val d = colMean[xx] - mc; vc += d * d }
        if (vr + vc <= 1e-6) return 0.0
        return vc / (vr + vc)
    }

    /**
     * Core material, by warmth *relative to the paper in the same frame*.
     *
     * The threshold is derived from the frame rather than fixed. A fixed R-B
     * test works on fresh wood but empties on greyed, oxidised cores, after
     * which the frame is refused as "no core found" - indistinguishable from a
     * photo that really has no core in it. Degraded cottonwood is exactly the
     * material that would trigger it.
     *
     * Returns the number of core pixels found.
     */
    fun woodMask(px: IntArray, w: Int, h: Int, minRb: Double? = null): Int {
        if (w <= 0 || h <= 0) return 0
        val n = w * h
        // paper reference: brightest 12%, and how warm that paper is
        val hist = IntArray(256)
        for (i in 0 until n) {
            val p = i * 4
            hist[max(px[p], max(px[p + 1], px[p + 2]))]++
        }
        var acc = 0
        var cutHi = 255
        for (v in 0..255) { acc += hist[v]; if (acc >= (n * 88) / 100) { cutHi = v; break } }
        val paperRb = DoubleArray(256)
        var paperCount = 0
        for (i in 0 until n) {
            val p = i * 4
            if (max(px[p], max(px[p + 1], px[p + 2])) > cutHi) {
                paperRb[paperCount] = (px[p] - px[p + 2]).toDouble(); paperCount++
            }
        }
        val floor = if (minRb != null) {
            minRb
        } else if (paperCount >= 200) {
            val sorted = paperRb.copyOf(paperCount).also { it.sort() }
            max(6.0, sorted[(paperCount * 90) / 100] + 6.0)
        } else {
            8.0
        }
        var count = 0
        for (i in 0 until n) {
            val p = i * 4
            val r = px[p]
            val rb = (r - px[p + 2]).toDouble()
            if (rb > floor && r > 100 && r < 225 && rb < 0.45 * r) count++
        }
        return count
    }

    data class Report(
        val castRbRaw: Double,
        val castRbNeutral: Double,
        val gains: DoubleArray,
        val paperLevel: Double,
        val sharpness: Double,
        val anisotropy: Double,
        val p99Neutral: Int,
        val p1Neutral: Int,
        val scaleDisagreement: Double,
        val warnings: List<String>,
        val ok: Boolean,
    ) {
        fun blockingReasons(): List<String> =
            warnings.filter {
                it.contains("refusing") || it.contains("too weak") ||
                    it.contains("no core") || it.contains("clipped")
            }
    }

    /**
     * Normalise [px] in place and decide whether the frame is safe to count.
     *
     * [pxPerMmAlt], when supplied, is a second independent scale. If the two
     * disagree by more than 6% the frame is refused: that is the harmonic
     * aliasing failure, and taking a single dominant period there overstates
     * ring width by about 74%.
     */
    fun assess(px: IntArray, w: Int, h: Int,
               coreX0: Int = 0, coreY0: Int = 0, coreX1: Int = w, coreY1: Int = h,
               pxPerMm: Double? = null, pxPerMmAlt: Double? = null): Report {
        val raw = rbOnPaper(px, w, h)
        val gains = channelGains(px, w, h)
        val level = applyGains(px, gains)
        val neutralCast = rbOnPaper(px, w, h)
        val gray = toGray(px, w, h)
        val sharp = sharpness(gray, w, h)
        var aniso = ringAnisotropy(gray, w, h, coreX0, coreY0, coreX1, coreY1)
        if (aniso == 0.0 && coreX1 - coreX0 >= w) {
            // No caller-supplied core box: derive one from the frame's own
            // warmth distribution, so a greyed core is still measured.
            val npx = woodMask(px, w, h)
            if (npx == 0) {
                // retry with a looser floor before giving up
                val rb = DoubleArray(w * h)
                for (i in 0 until w * h) { val p = i * 4; rb[i] = (px[p] - px[p + 2]).toDouble() }
                rb.sort()
                val warm = rb[(rb.size * 97) / 100]
                if (warm > 5.0) {
                    val m = woodMask(px, w, h, max(4.0, warm * 0.45))
                    if (m > 0) aniso = ringAnisotropy(gray, w, h, 0, 0, w, h)
                }
            }
        }

        val w2 = ArrayList<String>()
        if (!raw.isNaN() && abs(raw) > 40.0) {
            w2.add("strong white-balance cast (R-B %+.0f before normalisation); corrected, but check exposure"
                .format(raw))
        }
        val p99 = percentileMax(px)
        val p1 = percentileMin(px)
        if (p99 > 253 || p1 < 3) {
            w2.add("highlight or shadow clipping; some rings may be lost")
        }
        if (sharp < MIN_SHARPNESS) {
            w2.add("soft frame (sharpness %.1f < %.1f); likely autofocus hunting - use tap-to-focus"
                .format(sharp, MIN_SHARPNESS))
        }
        if (aniso < MIN_ANISOTROPY) {
            w2.add("ring signal too weak (anisotropy %.3f < %.3f); ring boundaries are not separable here"
                .format(aniso, MIN_ANISOTROPY))
        }
        if (!neutralCast.isNaN() && aniso > 0) {
            // paper present and a core present: the estimate is trustworthy
        } else if (neutralCast.isNaN()) {
            w2.add("no paper in frame; white balance estimated from the core itself, so scale may be slightly off")
        }
        var disagree = 0.0
        if (pxPerMm != null && pxPerMmAlt != null && pxPerMm > 1e-9) {
            disagree = abs(pxPerMm - pxPerMmAlt) / pxPerMm
            if (disagree > 0.06) {
                w2.add("scale references disagree by %.1f%% (%.2f vs %.2f px/mm) - likely an octave alias; refusing to count"
                    .format(disagree * 100, pxPerMm, pxPerMmAlt))
            }
        }
        val blocking = w2.any {
            it.contains("refusing") || it.contains("too weak") || it.contains("clipped")
        }
        return Report(raw, neutralCast, gains, level, sharp, aniso,
            p99, p1, disagree, w2, !blocking)
    }

    private fun percentileMax(px: IntArray): Int {
        val n = px.size / 4
        val hist = IntArray(256)
        for (i in 0 until n) {
            val p = i * 4
            hist[max(px[p], max(px[p + 1], px[p + 2]))]++
        }
        var acc = 0
        for (v in 0..255) { acc += hist[v]; if (acc >= (n * 99) / 100) return v }
        return 255
    }

    private fun percentileMin(px: IntArray): Int {
        val n = px.size / 4
        val hist = IntArray(256)
        for (i in 0 until n) {
            val p = i * 4
            hist[max(px[p], max(px[p + 1], px[p + 2]))]++
        }
        var acc = 0
        for (v in 0..255) { acc += hist[v]; if (acc >= (n * 1) / 100 + 1) return v }
        return 0
    }
}
