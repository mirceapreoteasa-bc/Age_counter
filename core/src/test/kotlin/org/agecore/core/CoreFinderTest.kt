package org.agecore.core

import kotlin.math.abs
import kotlin.math.hypot
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The core finder is tested on synthetic frames with a known core, because the
 * real photographs it was calibrated on live outside the repository. The
 * checks are the ones the Python version was measured on: the core is found,
 * the right background cue is used, the width is close, and a blob with no
 * visible core produces nothing.
 */
class CoreFinderTest {

    private fun argb(r: Int, g: Int, b: Int): Int =
        (0xFF shl 24) or (r shl 16) or (g shl 8) or b

    private class Noise(seed: Int) {
        private var s = seed
        fun next(): Int {
            s = (s * 1103515245 + 12345) and 0x7FFFFFFF
            return (s shr 16) and 0xFF
        }
        fun jitter(): Int = (next() % 7) - 3
    }

    /** A flat background with a little grain. */
    private fun background(w: Int, h: Int, r: Int, g: Int, b: Int, seed: Int): IntArray {
        val noise = Noise(seed)
        return IntArray(w * h) {
            argb(
                (r + noise.jitter()).coerceIn(0, 255),
                (g + noise.jitter()).coerceIn(0, 255),
                (b + noise.jitter()).coerceIn(0, 255),
            )
        }
    }

    /**
     * A warm bar with ring bands across it and cylindrical shading down it.
     * The bands matter: the paper path rejects a perfectly flat patch as
     * background, because a clean sheet is flat and wood is not.
     */
    private fun drawCore(
        px: IntArray, w: Int, h: Int,
        x0: Int, x1: Int, y0: Int, y1: Int,
        base: Int, band: Int = 34, pitch: Int = 11,
    ) {
        val noise = Noise(99)
        for (y in y0..y1) {
            val t = (y - y0).toDouble() / maxOf(1, y1 - y0)
            val shading = 1.0 - 0.28 * (2.0 * t - 1.0) * (2.0 * t - 1.0)
            for (x in x0..x1) {
                var v = base * shading
                if ((x - x0) % pitch < maxOf(2, pitch / 3)) v -= band
                v += noise.jitter()
                px[y * w + x] = argb(
                    v.coerceIn(0.0, 255.0).toInt(),
                    (v * 0.82).coerceIn(0.0, 255.0).toInt(),
                    (v * 0.62).coerceIn(0.0, 255.0).toInt(),
                )
            }
        }
    }

    @Test
    fun `finds a warm core on dark fabric`() {
        val w = 900
        val h = 300
        val px = background(w, h, 38, 48, 92, seed = 3)
        drawCore(px, w, h, 100, 800, 128, 172, base = 170)

        val core = CoreFinder.find(px, w, h)
        assertNotNull(core)
        assertEquals("fabric", core.source)
        assertTrue(abs(core.ax - 100) < 30, "pith end at x=${core.ax}")
        assertTrue(abs(core.bx - 800) < 30, "bark end at x=${core.bx}")
        assertTrue(abs(core.widthPx - 45) / 45 < 0.25, "width ${core.widthPx}")
        assertTrue(abs(core.angleDeg) < 5.0, "angle ${core.angleDeg}")
    }

    @Test
    fun `finds a dark core on clean paper`() {
        val w = 900
        val h = 300
        val px = background(w, h, 212, 211, 206, seed = 11)
        drawCore(px, w, h, 120, 780, 130, 170, base = 135, band = 40, pitch = 9)

        val core = CoreFinder.find(px, w, h)
        assertNotNull(core)
        assertEquals("paper", core.source)
        assertTrue(abs(core.widthPx - 41) / 41 < 0.30, "width ${core.widthPx}")
    }

    @Test
    fun `an empty frame has no core`() {
        val w = 640
        val h = 480
        val px = background(w, h, 200, 200, 196, seed = 5)
        assertNull(CoreFinder.find(px, w, h))
    }

    @Test
    fun `a square warm patch is not a core`() {
        val w = 600
        val h = 400
        val px = background(w, h, 40, 50, 90, seed = 7)
        drawCore(px, w, h, 260, 340, 160, 240, base = 170)
        assertNull(CoreFinder.find(px, w, h))
    }

    @Test
    fun `a vertical core is found and reported as vertical`() {
        val w = 320
        val h = 900
        val px = background(w, h, 36, 46, 88, seed = 13)
        drawCore(px, w, h, 130, 172, 100, 800, base = 168)

        val core = CoreFinder.find(px, w, h)
        assertNotNull(core)
        assertTrue(core.rotated, "should be found on the transpose")
        assertTrue(abs(core.ax - core.bx) < 12, "axis is vertical: ${core.ax} -> ${core.bx}")
        assertTrue(abs(core.ay - 100) < 30, "one end at y=${core.ay}")
        assertTrue(abs(core.by - 800) < 30, "other end at y=${core.by}")
        assertTrue(hypot(core.bx - core.ax, core.by - core.ay) > 600, "length")
    }

    @Test
    fun `the 5 mm scale is reported as an assumption`() {
        val w = 900
        val h = 300
        val px = background(w, h, 38, 48, 92, seed = 3)
        drawCore(px, w, h, 100, 800, 128, 172, base = 170)
        val core = assertNotNull(CoreFinder.find(px, w, h))
        assertTrue(
            core.notes.any { it.contains("5 mm") && it.contains("nominal") },
            "notes: ${core.notes}",
        )
        assertTrue(abs(core.pxPerMm - core.widthPx / 5.0) < 1e-9)
    }
}
