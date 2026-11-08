package org.agecore.core

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.test.Test
import kotlin.test.assertTrue

/**
 * The chain counter against synthetic cores with known counts.
 *
 * The synthetic core is rendered in the engine's own orientation - the core
 * runs along axis 0 and the rings are bands across it - so the test measures
 * the counter and not the image handling.
 */
class ChainTest {

    /** A core with `n` rings, each `pitch` px wide, plus sub-ring structure. */
    private fun render(
        n: Int,
        pitch: Double,
        width: Int = 60,
        cells: Int = 2,
        seed: Int = 7,
    ): DoubleArray {
        val length = (n * pitch).toInt() + 40
        val a = DoubleArray(length * width) { 215.0 }
        var rng = java.util.Random(seed.toLong())
        for (r in 0 until n) {
            val centre = (r * pitch + pitch / 2).toInt()
            // A latewood band: dark, and split into cells so the detector sees
            // several features per ring. The band is half a ring wide, which
            // is what a real latewood band looks like, and the cells inside
            // it are close enough to be one feature at the ring scale.
            val band = (pitch * 0.5).toInt()
            val cellW = max(1, (band / cells) - 1)
            for (c in 0 until cells) {
                val c0 = centre - band / 2 + (c * band / cells)
                for (y in c0 until c0 + cellW) {
                    if (y < 0 || y >= length) continue
                    for (x in 0 until width) {
                        a[y * width + x] -= 55.0 + rng.nextDouble() * 6.0
                    }
                }
            }
        }
        // cylindrical shading and grain
        for (y in 0 until length) {
            for (x in 0 until width) {
                val across = 2.0 * x / max(1, width - 1) - 1.0
                a[y * width + x] *= (1.0 - 0.14 * across * across)
                a[y * width + x] += rng.nextDouble() * 4.0 - 2.0
            }
        }
        return a
    }

    private fun count(strip: DoubleArray, n: Int, width: Int, pitch: Double): Int {
        val ringMm = pitch / 40.0                 // pretend 40 px/mm
        val pxPerMm = 40.0
        val r = Chain.countRings(strip, n, width, pxPerMm, ringMm)
        return r.count
    }

    @Test
    fun countsAKnownNumberOfRings() {
        for ((rings, pitch) in listOf(40 to 30.0, 80 to 22.0, 120 to 16.0)) {
            val width = 60
            val strip = render(rings, pitch, width)
            val got = count(strip, strip.size / width, width, pitch)
            val err = abs(got - rings) / rings.toDouble()
            assertTrue(
                err <= 0.10,
                "$rings rings at ${pitch}px pitch: counted $got (err ${"%.1f".format(err * 100)}%)",
            )
        }
    }

    @Test
    fun suppressesDuplicateFeaturesWithinARing() {
        val rings = 60
        val width = 60
        val pitch = 24.0
        val strip = render(rings, pitch, width, cells = 3)
        val length = strip.size / width
        val prof = Sections.textureProfile(strip, length, width, 3)
        val cands = Chain.boundaryCandidates(prof)
        val kept = Chain.nonMaxSuppression(cands, Chain.NMS_FRACTION * pitch)
        assertTrue(cands.size > kept.size, "NMS should remove duplicates")
        // A latewood band has two edges, so two survivors per ring is what the
        // suppression can do on its own; the chain is what turns them into
        // one boundary per ring.
        assertTrue(
            kept.size <= rings * 2.2,
            "NMS left ${kept.size} candidates for $rings rings",
        )
        val chain = Chain.findChain(kept, pitch)
        assertTrue(
            abs(chain.count - rings) <= rings * 0.10,
            "chain found ${chain.count} rings for $rings",
        )
    }

    @Test
    fun anEmptyCoreGivesNoCount() {
        val width = 40
        val strip = DoubleArray(600 * width) { 200.0 }
        val r = Chain.countRings(strip, 600, width, 40.0, 0.5)
        assertTrue(r.count == 0, "a featureless strip must not report rings")
    }

    @Test
    fun aStripShorterThanARingIsRefused() {
        val width = 40
        val strip = DoubleArray(20 * width) { 200.0 }
        val r = Chain.countRings(strip, 20, width, 40.0, 0.5)
        assertTrue(r.count == 0, "a strip shorter than a ring must not report rings")
    }

    @Test
    fun theExpectedPitchSizesTheWalk() {
        // a core with 50 rings of 20 px; asked with the right pitch it should
        // find them, and asked with half the pitch it should find twice as
        // many - which is exactly the octave ambiguity the app warns about
        val width = 60
        val pitch = 20.0
        val strip = render(50, pitch, width)
        val length = strip.size / width
        val prof = Sections.textureProfile(strip, length, width, 3)
        val cands = Chain.boundaryCandidates(prof)
        val kept = Chain.nonMaxSuppression(cands, Chain.NMS_FRACTION * pitch)
        val right = Chain.findChain(kept, pitch)
        val half = Chain.findChain(kept, pitch / 2)
        assertTrue(
            abs(right.count - 50) <= 5,
            "at the right pitch: ${right.count} rings for 50",
        )
        assertTrue(
            half.count > right.count,
            "at half the pitch the count should rise (${half.count} vs ${right.count})",
        )
    }
}
