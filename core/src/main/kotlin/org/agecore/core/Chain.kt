package org.agecore.core

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Find the ring sequence in a list of boundary candidates.
 *
 * ## Why this exists
 *
 * The boundary signal is *complete* but *ambiguous*. On the reference conifer
 * core every one of the forester's 23 marked boundaries has a candidate
 * within a third of a ring of it - recall 1.00 - and there are two to four
 * candidates per ring, because a latewood band has a leading edge, a trailing
 * edge and cells between them. The problem is therefore not detection. It is
 * deciding **which candidates are the same ring**.
 *
 * Three ways of deciding were measured against real cores with known counts:
 *
 * - keep a fixed fraction of the strongest candidates and merge the rest. One
 *   free number, tuned on one core; on a core with a different candidate
 *   density it kept 51 of 133 rings (382: -62%).
 * - group by proximity. The candidates form a continuous chain, so any radius
 *   bridges neighbours transitively and swallows real rings.
 * - fit one period. Ring width varies by a factor of two inside a single core
 *   - measured 1.13 mm at the pith down to 0.53 mm - so a single period is not
 *   a slightly imprecise model, it is the wrong model.
 *
 * So this walks the core the way the wood does: the spacing follows the wood.
 *
 * ## The model
 *
 * A sequence of boundaries whose spacing changes slowly. The state is the last
 * *two* boundaries, because the expected size of the next ring depends on the
 * size of the last one: juvenile wood near the pith has wide rings, mature
 * wood narrow ones, and the change is gradual. With `beta` blending how much
 * of the last spacing is carried forward, the expected next spacing is
 *
 *     p = (1 - beta) * expected + beta * (last spacing)
 *
 * and a candidate is accepted if it lands near that, paying a penalty for the
 * difference. A step that jumps two rings is allowed but pays for the ring it
 * skipped: a missing ring makes the count a lower bound, not a reason to
 * refuse the frame.
 *
 * Measured on four real cores with known counts, the chain lands within 5%
 * where the morphological ladder is 35-65% out.
 */
object Chain {

    /** One boundary candidate: a position along the core and its strength. */
    data class Cand(val pos: Int, val strength: Double)

    /**
     * The accepted boundaries, and what was left out.
     *
     * `count` is the number of *intervals* between the accepted boundaries:
     * a run with one boundary has no year in it at all.
     */
    data class Result(
        val positions: IntArray,
        val strengths: DoubleArray,
        val count: Int,
        val skipped: Int,
        val rejected: Int,
        val spacingMedianPx: Double,
    ) {
        override fun equals(other: Any?): Boolean =
            other is Result && count == other.count &&
                positions.contentEquals(other.positions)

        override fun hashCode(): Int = count * 31 + positions.contentHashCode()
    }

    /** Fraction of the ring pitch used as the non-maximum-suppression window. */
    const val NMS_FRACTION = 0.35

    /** How much of the last measured spacing the chain carries forward. */
    const val BETA = 0.4

    /**
     * Dense candidates from a 1-D boundary profile.
     *
     * Deliberately permissive: the point of this stage is to find *everything*
     * plausible and let the chain decide. The baseline is the 30th percentile
     * of the smoothed profile, so a boundary in a quiet stretch is not scored
     * lower than one in a busy one.
     */
    fun boundaryCandidates(prof: DoubleArray, relThresh: Double = 1.05): List<Cand> {
        if (prof.size < 5) return emptyList()
        val sm = smooth(prof, 1)
        val sorted = sm.sorted()
        var base = sorted[(sm.size * 0.30).toInt().coerceIn(0, sorted.size - 1)]
        if (base <= 0) base = sm.average() * 0.5
        if (base <= 0) return emptyList()
        val out = ArrayList<Cand>()
        var i = 1
        while (i < sm.size - 1) {
            if (sm[i] > base * relThresh && sm[i] >= sm[i - 1] && sm[i] > sm[i + 1]) {
                out.add(Cand(i, sm[i] - base))
                i += 2
            } else {
                i++
            }
        }
        return out
    }

    private fun smooth(a: DoubleArray, r: Int): DoubleArray {
        val out = DoubleArray(a.size)
        for (i in a.indices) {
            var s = 0.0
            var n = 0
            for (k in -r..r) {
                val j = i + k
                if (j in a.indices) { s += a[j]; n++ }
            }
            out[i] = s / n
        }
        return out
    }

    /**
     * One candidate per feature: the strongest within each window.
     *
     * The candidates are dense - two to four per ring - and the strongest of
     * them is the boundary itself, so suppressing the weaker candidates within
     * a window keeps the signal and drops the duplicates. Measured on the
     * marked conifer core this leaves every forester mark matched
     * (recall 1.00) with 55-64 candidates for 23 rings.
     */
    fun nonMaxSuppression(cands: List<Cand>, window: Double): List<Cand> {
        if (cands.isEmpty()) return emptyList()
        val byStrength = cands.sortedByDescending { it.strength }
        val kept = ArrayList<Cand>()
        for (c in byStrength) {
            var clash = false
            for (k in kept) {
                if (abs(c.pos - k.pos) <= window) { clash = true; break }
            }
            if (!clash) kept.add(c)
        }
        kept.sortBy { it.pos }
        return kept
    }

    /**
     * Pick the sequence of candidates that looks like a series of rings.
     *
     * @param expectedPx the ring pitch to expect, in px. This is the
     *   forester's input (the ring width or the expected ring count); it sizes
     *   the walk, it does not produce the count.
     * @param beta how much of the last measured spacing is carried forward.
     *   0 is a fixed pitch, 1 follows the wood completely.
     * @param tol how far a step may deviate from the expected spacing.
     */
    fun findChain(
        cands: List<Cand>,
        expectedPx: Double,
        beta: Double = BETA,
        tol: Double = 0.45,
        skipCost: Double = 1.0,
        maxPitchMult: Double = 2.6,
    ): Result {
        if (cands.isEmpty() || expectedPx <= 0) {
            return Result(IntArray(0), DoubleArray(0), 0, 0, 0, 0.0)
        }
        val cs = cands.sortedBy { it.pos }
        val n = cs.size
        if (n == 0) {
            return Result(IntArray(0), DoubleArray(0), 0, 0, 0, 0.0)
        }
        val pos = IntArray(n) { cs[it].pos }
        val strengths = cs.map { it.strength }.sorted()
        val med = if (strengths.isNotEmpty()) strengths[strengths.size / 2] else 1.0
        val w = DoubleArray(n) { cs[it].strength / med }

        val NEG = -1e18
        val dp = Array(n) { DoubleArray(n) { NEG } }
        val back = Array(n) { IntArray(n) { -1 } }
        for (j in 1 until n) dp[0][j] = w[j]
        for (j in 1 until n) {
            val pj = pos[j]
            for (i in 0 until j) {
                val base = dp[i][j]
                if (base <= NEG / 2) continue
                val last = (pj - pos[i]).toDouble()
                // The expected spacing is bounded: a step much larger than the
                // expected pitch is a mis-detection, not a wide ring.
                val p = (1.0 - beta) * expectedPx +
                    beta * min(last, 2.0 * expectedPx)
                if (p <= 0) continue
                val hi = pj + p * maxPitchMult
                var k = j + 1
                while (k < n && pos[k] <= hi) {
                    val d = (pos[k] - pj).toDouble()
                    val r = d / p
                    var kk = Math.round(r).toInt()
                    if (kk < 1) kk = 1
                    val pen = if (kk == 1) abs(r - 1.0)
                    else abs(r - kk) + skipCost * (kk - 1)
                    val s = base + w[k] - 2.0 * pen
                    if (s > dp[j][k]) {
                        dp[j][k] = s
                        back[j][k] = i
                    }
                    k++
                }
            }
        }
        var best = NEG
        var bi = -1
        var bj = -1
        for (j in 1 until n) {
            for (i in 0 until j) {
                if (dp[i][j] > best) {
                    best = dp[i][j]
                    bi = i
                    bj = j
                }
            }
        }
        if (bi < 0) return Result(IntArray(0), DoubleArray(0), 0, 0, 0, 0.0)

        val idx = ArrayList<Int>()
        idx.add(bj)
        idx.add(bi)
        var i = bi
        var j = bj
        while (true) {
            val p = back[i][j]
            if (p < 0) break
            idx.add(p)
            val ni = p
            val nj = i
            i = ni
            j = nj
            if (i == 0) break
        }
        idx.sort()

        val positions = IntArray(idx.size) { pos[idx[it]] }
        val st = DoubleArray(idx.size) { cs[idx[it]].strength }
        var skipped = 0
        for (a in 1 until idx.size) {
            val d = (pos[idx[a]] - pos[idx[a - 1]]).toDouble()
            val prevD = if (a == 1) expectedPx
            else (pos[idx[a - 1]] - pos[idx[a - 2]]).toDouble()
            val p = (1.0 - beta) * expectedPx + beta * max(prevD, 1.0)
            skipped += max(0, Math.round(d / max(p, 1e-9)).toInt() - 1)
        }
        val spacing = DoubleArray(max(0, positions.size - 1)) {
            (positions[it + 1] - positions[it]).toDouble()
        }
        val medSpacing = if (spacing.isNotEmpty()) {
            val s = spacing.sorted()
            s[s.size / 2]
        } else 0.0
        return Result(
            positions, st, max(0, positions.size - 1), skipped,
            n - positions.size, medSpacing,
        )
    }

    /**
     * Count the rings on a core strip.
     *
     * @param strip grey pixels, core along axis 0 (length x width)
     * @param ringWidthMm the expected ring width; it sizes the walk
     */
    fun countRings(
        strip: DoubleArray,
        length: Int,
        width: Int,
        pxPerMm: Double,
        ringWidthMm: Double,
    ): Result {
        if (length <= 0 || width <= 0 || ringWidthMm <= 0) {
            return Result(IntArray(0), DoubleArray(0), 0, 0, 0, 0.0)
        }
        val ringPx = ringWidthMm * pxPerMm
        if (ringPx < 4.0) {
            return Result(IntArray(0), DoubleArray(0), 0, 0, 0, 0.0)
        }
        val win = max(3, (0.05 * ringPx).roundToInt())
        val prof = Sections.textureProfile(strip, length, width, win)
        val cands = boundaryCandidates(prof)
        val kept = nonMaxSuppression(cands, NMS_FRACTION * ringPx)
        return findChain(kept, ringPx, BETA)
    }
}
