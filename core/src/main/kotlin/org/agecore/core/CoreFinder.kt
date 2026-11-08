package org.agecore.core

import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sqrt

/**
 * Find the age core in a photo, so the forester confirms a proposed path
 * instead of tapping one out from memory.
 *
 * Port of `engine/pipeline.py`'s `find_core`, measured on five real cores:
 *
 * | core | width error |
 * |---|---|
 * | 382 | +0.7% |
 * | 092604_592 | +5.1% |
 * | D71_7984 | -22.9% (locks onto the printed channel, not the core) |
 * | PXL_ACT_327 | proposed path on the core |
 *
 * Two backgrounds are handled because the field kit uses both and neither cue
 * works on the other:
 *
 *  * **fabric** (denim, a table): the core is warmer than its surroundings.
 *    Red minus blue, normalised by brightness, separates wood from blue or
 *    neutral cloth. It is *not* used on white paper, which reads warm.
 *  * **paper** (a clean card): the sheet is bright and even, the core is dark
 *    and textured. A cut a few per cent below the sheet's own level keeps the
 *    paper and rejects the core.
 *
 * The core is much longer than it is wide and roughly straight, so the
 * candidate must pass an aspect gate and a straightness measure; a sleeve, a
 * shadow, a rule or a piece of clothing fails one of them.
 *
 * The scale is the 5 mm core diameter, a *nominal* figure. It is reported as
 * pxPerMm so a later measurement can contradict it; nothing here claims an
 * absolute scale.
 *
 * Known limit, measured rather than hidden: a core lying in a dark printed
 * channel is not found (D71_7984), because the channel is as straight and as
 * long as the core. That case needs the card path, not this one.
 *
 * Coordinates are pixels of the array passed in; the caller decides how that
 * relates to the original photo.
 */
object CoreFinder {

    /** A nominal borer core is 5 mm across, used as a ratio, not a ruler. */
    const val CORE_DIA_MM = 5.0

    /** A core is far longer than it is wide; below this it is a fragment. */
    const val MIN_ASPECT = 12.0

    /** The forester's floor for a core worth counting. */
    const val MIN_LENGTH_MM = 30.0

    private const val MIN_AREA = 150
    private const val MIN_COLS = 20

    data class Core(
        /** "paper" or "fabric": which cue isolated it. */
        val source: String,
        /** pith end, in the coordinates of the array passed to [find]. */
        val ax: Double,
        val ay: Double,
        /** bark end, in the same coordinates. */
        val bx: Double,
        val by: Double,
        val widthPx: Double,
        /** From the 5 mm assumption; report it as an assumption. */
        val pxPerMm: Double,
        val score: Double,
        val notes: List<String>,
        /** true when the core was found running vertically, then mapped back */
        val rotated: Boolean = false,
    ) {
        val lengthPx: Double get() = hypot(bx - ax, by - ay)
        val lengthMm: Double get() = if (pxPerMm > 0) lengthPx / pxPerMm else 0.0
        val angleDeg: Double
            get() = Math.toDegrees(kotlin.math.atan2(by - ay, bx - ax))
    }

    private class Cand(
        val top: DoubleArray,
        val bot: DoubleArray,
        val xs: IntArray,
        val width: Double,
        val aspect: Double,
        val bend: Double,
        val score: Double,
        val x0: Int,
        val x1: Int,
    )

    /**
     * @param argb row-major ARGB pixels, `width * height`
     * @return the core, or null when no object passes the shape gates
     */
    fun find(argb: IntArray, w: Int, h: Int): Core? {
        if (w < 8 || h < 8 || argb.size < w * h) return null
        findAlongX(argb, w, h)?.let { return it }

        // A core is as often vertical in the frame as horizontal. Re-run the
        // column-wise search on the transpose rather than making the caller
        // rotate the photo; the two orientations are the same problem.
        val t = transpose(argb, w, h)
        val found = findAlongX(t, h, w) ?: return null
        return found.copy(
            ax = found.ay, ay = found.ax, bx = found.by, by = found.bx,
            rotated = true,
            notes = found.notes + listOf("the core runs vertically in the frame"),
        )
    }

    // ------------------------------------------------------------- one axis

    private fun findAlongX(argb: IntArray, w: Int, h: Int): Core? {
        val n = w * h
        val lum = DoubleArray(n)
        val r = DoubleArray(n)
        val b = DoubleArray(n)
        for (i in 0 until n) {
            val c = argb[i]
            val rr = (c shr 16) and 0xFF
            val gg = (c shr 8) and 0xFF
            val bb = c and 0xFF
            r[i] = rr.toDouble()
            b[i] = bb.toDouble()
            lum[i] = 0.299 * rr + 0.587 * gg + 0.114 * bb
        }

        var best: Pair<String, Cand>? = null
        val warmth = DoubleArray(n) { (r[it] - b[it]) / (lum[it] + 20.0) }

        // --- fabric: the core is the warm object ---------------------------
        run {
            val thr = otsuPercentile(warmth, 1.0, 99.0)
            var mask = BooleanArray(n) { warmth[it] > thr }
            // Centre crop, so clutter at the frame edge cannot bridge objects.
            val cropTop = (h * 0.20).toInt()
            val cropBot = (h * 0.80).toInt()
            for (y in 0 until h) {
                if (y in cropTop until cropBot) continue
                for (x in 0 until w) mask[y * w + x] = false
            }
            mask = open(mask, w, h, 9, 5)
            val c = bestCandidate(mask, w, h, warmth, argb)
            val cur = best
            if (c != null && (cur == null || c.score > cur.second.score)) {
                best = "fabric" to c
            }
        }

        // --- paper: the core is the dark, textured object ------------------
        run {
            val margin = max(4, (h * 0.06).toInt())
            val levels = DoubleArray(2 * margin * w)
            var k = 0
            for (y in 0 until margin) for (x in 0 until w) levels[k++] = lum[y * w + x]
            for (y in h - margin until h) for (x in 0 until w) levels[k++] = lum[y * w + x]
            val paper = median(levels, k)
            val cut = paper * 0.96
            var mask = BooleanArray(n) { lum[it] < cut }
            val tex = localStd(lum, w, h, 25)
            val paperTex = median(tex, min(n, max(4, h / 12) * w))
            if (paperTex < 1.0 && paper > 60.0) {
                for (i in 0 until n) if (mask[i]) mask[i] = tex[i] > paperTex * 1.6
            }
            mask = open(mask, w, h, 25, 9)
            mask = close(mask, w, h, 25, 25)
            val darkness = DoubleArray(n) {
                ((paper - lum[it]) / paper).coerceIn(0.0, 1.0)
            }
            val c = bestCandidate(mask, w, h, darkness, argb)
            val cur = best
            if (c != null && (cur == null || c.score > cur.second.score)) {
                best = "paper" to c
            }
        }

        val (source, cand) = best ?: return null

        // The ends taper and 5 mm is nominal, so measure the width over the
        // middle of the core; a tapered end would bias it low.
        val top = runningMedian(cand.top, 9)
        val bot = runningMedian(cand.bot, 9)
        val lo = (cand.xs.size * 0.2).toInt()
        val hi = max(lo + 1, (cand.xs.size * 0.8).toInt())
        val widths = DoubleArray(cand.xs.size) { bot[it] - top[it] }
        val midW = DoubleArray(hi - lo) { widths[lo + it] }
        val coreW = median(midW, midW.size).takeIf { it > 0 }
            ?: median(widths, widths.size)

        // A straight axis through the middle of the object. The forester's own
        // measurements show a core that is not pressed flat wanders by about a
        // third of its width, and a straight sampling path is more stable than
        // the wandering midline.
        var sx = 0.0
        var sy = 0.0
        for (i in cand.xs.indices) {
            val mx = cand.xs[i].toDouble()
            sy += ((top[i] + bot[i]) / 2.0)
            sx += mx
        }
        val mx = sx / cand.xs.size
        val my = sy / cand.xs.size
        var num = 0.0
        var den = 0.0
        for (i in cand.xs.indices) {
            val x = cand.xs[i].toDouble()
            num += (x - mx) * (((top[i] + bot[i]) / 2.0) - my)
            den += (x - mx) * (x - mx)
        }
        val slope = if (den > 1e-9) num / den else 0.0
        fun axisY(x: Double) = my + slope * (x - mx)

        val xa = cand.xs.first().toDouble()
        val xb = cand.xs.last().toDouble()
        if (xb - xa < 2) return null

        val notes = ArrayList<String>()
        val pxPerMm = coreW / CORE_DIA_MM
        val core = Core(
            source = source,
            ax = xa, ay = axisY(xa), bx = xb, by = axisY(xb),
            widthPx = coreW,
            pxPerMm = pxPerMm,
            score = cand.score,
            notes = notes,
        )
        if (core.lengthMm < MIN_LENGTH_MM) {
            notes.add(
                "core is only ${core.lengthMm.roundToInt()} mm long at the " +
                    "5 mm assumption, below the ${MIN_LENGTH_MM.roundToInt()} mm " +
                    "the forester counts",
            )
        }
        notes.add(
            "scale is from the 5 mm core diameter, a nominal figure: it is a " +
                "ratio check, not an absolute measurement",
        )
        return core
    }

    /**
     * True for a fluorescent-yellow marker lane, which is not a core.
     *
     * A photo card draws a highlighted lane over its ruler. It is long, warm
     * and straight, so it wins the shape score against the core underneath it,
     * and a strip of marker ink has no rings at all. Yellow ink is much
     * brighter than any wood in these photographs and has its green channel
     * close to red with little blue: measured 252,233,152 for the lane, while
     * the palest real core in the set is 204,175,145 and the greyest
     * 132,123,125. Without this the app proposed the lane on PXL_ACT_327.
     */
    private fun isHighlighter(rs: Double, gs: Double, bs: Double, n: Int): Boolean {
        if (n <= 0) return false
        val r = rs / n
        val g = gs / n
        val b = bs / n
        val l = 0.299 * r + 0.587 * g + 0.114 * b
        return l > 210.0 && g > 0.88 * r && b < 0.72 * r
    }

    /** Best candidate in a mask that passes the shape gates, or null. */
    private fun bestCandidate(mask: BooleanArray, w: Int, h: Int,
                              cue: DoubleArray, argb: IntArray): Cand? {
        val lab = IntArray(w * h)
        val count = label4(mask, w, h, lab)
        if (count == 0) return null
        val bx0 = IntArray(count + 1) { w }
        val bx1 = IntArray(count + 1) { -1 }
        val by0 = IntArray(count + 1) { h }
        val by1 = IntArray(count + 1) { -1 }
        val area = IntArray(count + 1)
        val cueSum = DoubleArray(count + 1)
        val rSum = DoubleArray(count + 1)
        val gSum = DoubleArray(count + 1)
        val bSum = DoubleArray(count + 1)
        for (i in lab.indices) {
            val l = lab[i]
            if (l == 0) continue
            val x = i % w
            val y = i / w
            area[l]++
            cueSum[l] += cue[i]
            val c = argb[i]
            rSum[l] += (c shr 16) and 0xFF
            gSum[l] += (c shr 8) and 0xFF
            bSum[l] += c and 0xFF
            if (x < bx0[l]) bx0[l] = x
            if (x > bx1[l]) bx1[l] = x
            if (y < by0[l]) by0[l] = y
            if (y > by1[l]) by1[l] = y
        }
        var best: Cand? = null
        for (l in 1..count) {
            if (area[l] < MIN_AREA) continue
            if (isHighlighter(rSum[l], gSum[l], bSum[l], area[l])) continue
            val meanCue = cueSum[l] / max(1, area[l])
            val c = measure(lab, l, w, bx0[l], bx1[l], by0[l], by1[l], meanCue)
                ?: continue
            if (c.aspect < MIN_ASPECT) continue
            if (c.score <= 0.0) continue
            if (best == null || c.score > best.score) best = c
        }
        return best
    }

    /** Per-column centreline, width and straightness for one component. */
    private fun measure(lab: IntArray, l: Int, w: Int,
                        x0: Int, x1: Int, y0: Int, y1: Int,
                        meanCue: Double): Cand? {
        val ncols = x1 - x0 + 1
        if (ncols < MIN_COLS) return null
        val top = DoubleArray(ncols)
        val bot = DoubleArray(ncols)
        val ok = BooleanArray(ncols)
        for (x in x0..x1) {
            var runStart = -1
            var bestS = -1
            var bestE = -1
            for (y in y0..y1) {
                if (lab[y * w + x] == l) {
                    if (runStart < 0) runStart = y
                    if (bestS < 0 || y - runStart > bestE - bestS) {
                        bestS = runStart
                        bestE = y
                    }
                } else {
                    runStart = -1
                }
            }
            if (bestS >= 0) {
                top[x - x0] = bestS.toDouble()
                bot[x - x0] = bestE.toDouble()
                ok[x - x0] = true
            }
        }
        var n = 0
        for (v in ok) if (v) n++
        if (n < MIN_COLS) return null

        val xs = IntArray(n)
        val tops = DoubleArray(n)
        val bots = DoubleArray(n)
        var k = 0
        for (i in 0 until ncols) {
            if (!ok[i]) continue
            xs[k] = x0 + i
            tops[k] = top[i]
            bots[k] = bot[i]
            k++
        }
        val widths = DoubleArray(n) { bots[it] - tops[it] }
        val width = median(widths, n)
        if (width <= 1e-6) return null
        val length = (x1 - x0 + 1).toDouble()
        val aspect = length / width

        val mids = DoubleArray(n) { (tops[it] + bots[it]) / 2.0 }
        val meanX = xs.sumOf { it.toDouble() } / n
        val meanY = mids.sumOf { it } / n
        var num = 0.0
        var den = 0.0
        for (i in 0 until n) {
            val dx = xs[i] - meanX
            num += dx * (mids[i] - meanY)
            den += dx * dx
        }
        val slope = if (den > 1e-9) num / den else 0.0
        var ss = 0.0
        for (i in 0 until n) {
            val fit = meanY + slope * (xs[i] - meanX)
            val d = mids[i] - fit
            ss += d * d
        }
        val straight = sqrt(ss / n)
        val bend = straight / max(length, 1.0)
        // The shape term dominates; the background cue only breaks near-ties.
        // Measured on 592: a cooler shadow band and the core both scored 1.36
        // (same length, same straightness), and the cue is what separates them
        // (warmth 0.07 against 0.25). Without it the choice depended on label
        // order, which is not a property of the photograph.
        val score = n / 1000.0 - 8.0 * bend + 0.5 * meanCue
        return Cand(tops, bots, xs, width, aspect, bend, score, x0, x1)
    }

    // --------------------------------------------------------------- masks

    /** 4-connected labelling; returns the number of components. */
    private fun label4(mask: BooleanArray, w: Int, h: Int, lab: IntArray): Int {
        val stack = IntArray(w * h)
        var cur = 0
        val n = w * h
        for (seed in 0 until n) {
            if (!mask[seed] || lab[seed] != 0) continue
            cur++
            var sp = 0
            stack[sp++] = seed
            lab[seed] = cur
            while (sp > 0) {
                val p = stack[--sp]
                val x = p % w
                val y = p / w
                if (x > 0) {
                    val q = p - 1
                    if (mask[q] && lab[q] == 0) { lab[q] = cur; stack[sp++] = q }
                }
                if (x < w - 1) {
                    val q = p + 1
                    if (mask[q] && lab[q] == 0) { lab[q] = cur; stack[sp++] = q }
                }
                if (y > 0) {
                    val q = p - w
                    if (mask[q] && lab[q] == 0) { lab[q] = cur; stack[sp++] = q }
                }
                if (y < h - 1) {
                    val q = p + w
                    if (mask[q] && lab[q] == 0) { lab[q] = cur; stack[sp++] = q }
                }
            }
        }
        return cur
    }

    /** Binary erosion with a kw x kh box. Outside the frame counts as false. */
    private fun erode(mask: BooleanArray, w: Int, h: Int, kw: Int, kh: Int): BooleanArray {
        val rx = kw / 2
        val ry = kh / 2
        val tmp = BooleanArray(w * h)
        for (y in 0 until h) {
            val row = y * w
            var falseCount = 0
            for (x in 0 until min(w, rx + 1)) if (!mask[row + x]) falseCount++
            for (x in 0 until w) {
                val lo = x - rx
                val hi = x + rx
                if (lo < 0 || hi > w - 1) {
                    tmp[row + x] = false
                } else {
                    tmp[row + x] = falseCount == 0
                }
                if (x - rx >= 0 && !mask[row + x - rx]) falseCount--
                if (x + rx + 1 < w && !mask[row + x + rx + 1]) falseCount++
            }
        }
        val out = BooleanArray(w * h)
        for (x in 0 until w) {
            var falseCount = 0
            for (y in 0 until min(h, ry + 1)) if (!tmp[y * w + x]) falseCount++
            for (y in 0 until h) {
                val lo = y - ry
                val hi = y + ry
                if (lo < 0 || hi > h - 1) {
                    out[y * w + x] = false
                } else {
                    out[y * w + x] = falseCount == 0
                }
                if (y - ry >= 0 && !tmp[(y - ry) * w + x]) falseCount--
                if (y + ry + 1 < h && !tmp[(y + ry + 1) * w + x]) falseCount++
            }
        }
        return out
    }

    /** Binary dilation with a kw x kh box. */
    private fun dilate(mask: BooleanArray, w: Int, h: Int, kw: Int, kh: Int): BooleanArray {
        val rx = kw / 2
        val ry = kh / 2
        val tmp = BooleanArray(w * h)
        for (y in 0 until h) {
            val row = y * w
            var trueCount = 0
            for (x in 0 until min(w, rx + 1)) if (mask[row + x]) trueCount++
            for (x in 0 until w) {
                tmp[row + x] = trueCount > 0
                if (x - rx >= 0 && mask[row + x - rx]) trueCount--
                if (x + rx + 1 < w && mask[row + x + rx + 1]) trueCount++
            }
        }
        val out = BooleanArray(w * h)
        for (x in 0 until w) {
            var trueCount = 0
            for (y in 0 until min(h, ry + 1)) if (tmp[y * w + x]) trueCount++
            for (y in 0 until h) {
                out[y * w + x] = trueCount > 0
                if (y - ry >= 0 && tmp[(y - ry) * w + x]) trueCount--
                if (y + ry + 1 < h && tmp[(y + ry + 1) * w + x]) trueCount++
            }
        }
        return out
    }

    private fun open(m: BooleanArray, w: Int, h: Int, kw: Int, kh: Int) =
        dilate(erode(m, w, h, kw, kh), w, h, kw, kh)

    private fun close(m: BooleanArray, w: Int, h: Int, kw: Int, kh: Int) =
        erode(dilate(m, w, h, kw, kh), w, h, kw, kh)

    // --------------------------------------------------------- statistics

    /** Otsu threshold over the 1st..99th percentile range. */
    private fun otsuPercentile(v: DoubleArray, loPct: Double, hiPct: Double): Double {
        if (v.isEmpty()) return 0.0
        val s = v.sortedArray()
        val lo = s[min(s.size - 1, (s.size * loPct / 100.0).toInt())]
        val hi = s[min(s.size - 1, (s.size * hiPct / 100.0).toInt())]
        if (hi <= lo) return hi
        val bins = 256
        val hist = DoubleArray(bins)
        val scale = (bins - 1) / (hi - lo)
        for (x in v) {
            val q = ((x - lo) * scale).roundToInt().coerceIn(0, bins - 1)
            hist[q] += 1.0
        }
        var total = 0.0
        for (x in hist) total += x
        if (total <= 0) return hi
        val centres = DoubleArray(bins) { lo + it / scale }
        var w0 = 0.0
        var csum = 0.0
        var mTotal = 0.0
        for (i in 0 until bins) mTotal += hist[i] * centres[i]
        // Between-class variance: w0*w1*(mean0 - mean1)^2. Comparing class 0
        // with the global mean instead moves the threshold far enough to merge
        // the core with its shadow (592: 264 px against the correct 165 px).
        var best = -1.0
        var bestT = lo
        for (i in 0 until bins) {
            w0 += hist[i]
            csum += hist[i] * centres[i]
            val w1 = total - w0
            if (w0 <= 0 || w1 <= 0) continue
            val m0 = csum / w0
            val m1 = (mTotal - csum) / w1
            val between = w0 * w1 * (m0 - m1) * (m0 - m1)
            if (between > best) { best = between; bestT = centres[i] }
        }
        return bestT
    }

    /** Local standard deviation over a kw x kw box. */
    private fun localStd(v: DoubleArray, w: Int, h: Int, kw: Int): DoubleArray {
        val m1 = boxMean(v, w, h, kw)
        val v2 = DoubleArray(v.size) { v[it] * v[it] }
        val m2 = boxMean(v2, w, h, kw)
        return DoubleArray(v.size) {
            val x = m2[it] - m1[it] * m1[it]
            if (x > 0) sqrt(x) else 0.0
        }
    }

    private fun boxMean(v: DoubleArray, w: Int, h: Int, kw: Int): DoubleArray {
        val r = kw / 2
        val tmp = DoubleArray(w * h)
        for (y in 0 until h) {
            val row = y * w
            var s = 0.0
            var cnt = 0
            for (x in 0..min(w - 1, r)) { s += v[row + x]; cnt++ }
            for (x in 0 until w) {
                tmp[row + x] = s / cnt
                if (x - r >= 0) { s -= v[row + x - r]; cnt-- }
                if (x + r + 1 < w) { s += v[row + x + r + 1]; cnt++ }
            }
        }
        val out = DoubleArray(w * h)
        for (x in 0 until w) {
            var s = 0.0
            var cnt = 0
            for (y in 0..min(h - 1, r)) { s += tmp[y * w + x]; cnt++ }
            for (y in 0 until h) {
                out[y * w + x] = s / cnt
                if (y - r >= 0) { s -= tmp[(y - r) * w + x]; cnt-- }
                if (y + r + 1 < h) { s += tmp[(y + r + 1) * w + x]; cnt++ }
            }
        }
        return out
    }

    private fun median(v: DoubleArray, n: Int): Double {
        if (n <= 0) return 0.0
        val s = DoubleArray(n)
        System.arraycopy(v, 0, s, 0, n)
        s.sort()
        return if (n % 2 == 1) s[n / 2] else 0.5 * (s[n / 2 - 1] + s[n / 2])
    }

    /** Running median of width 9, edge-padded, as the Python contour smoother. */
    private fun runningMedian(v: DoubleArray, win: Int): DoubleArray {
        if (v.size < win) return v.copyOf()
        val out = DoubleArray(v.size)
        val half = win / 2
        val buf = DoubleArray(win)
        for (i in v.indices) {
            for (k in 0 until win) {
                val j = (i - half + k).coerceIn(0, v.size - 1)
                buf[k] = v[j]
            }
            buf.sort()
            out[i] = buf[half]
        }
        return out
    }

    private fun transpose(v: IntArray, w: Int, h: Int): IntArray {
        val out = IntArray(w * h)
        for (y in 0 until h) for (x in 0 until w) out[x * h + y] = v[y * w + x]
        return out
    }
}
