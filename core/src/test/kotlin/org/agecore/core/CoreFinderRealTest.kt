package org.agecore.core

import java.io.File
import javax.imageio.ImageIO
import kotlin.math.abs
import kotlin.test.Test
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/**
 * Check the port against the same photographs the Python finder was measured
 * on. The photographs live outside the repository, so a machine without them
 * skips the checks rather than failing; the numbers compared against are the
 * widths `engine/pipeline.py` reports for the same frame, so the test asks
 * "does the port agree with the measurement?" not "is the width correct?".
 *
 * D71_7984 is deliberately absent: the Python finder locks onto the printed
 * channel there (-22.9% width), and reproducing a known failure is not a
 * useful regression signal.
 */
class CoreFinderRealTest {

    private data class Case(val rel: String, val pythonWidthPx: Double)

    private fun downscale(img: java.awt.image.BufferedImage, s: Int):
        Triple<IntArray, Int, Int> {
        val nw = img.width / s
        val nh = img.height / s
        val out = IntArray(nw * nh)
        for (y in 0 until nh) {
            for (x in 0 until nw) {
                var r = 0
                var g = 0
                var b = 0
                for (dy in 0 until s) for (dx in 0 until s) {
                    val c = img.getRGB(x * s + dx, y * s + dy)
                    r += (c shr 16) and 0xFF
                    g += (c shr 8) and 0xFF
                    b += c and 0xFF
                }
                val n = s * s
                out[y * nw + x] = (0xFF shl 24) or
                    ((r / n) shl 16) or ((g / n) shl 8) or (b / n)
            }
        }
        return Triple(out, nw, nh)
    }

    @Test
    fun `agrees with the Python finder on real photographs`() {
        val root = File(System.getenv("AGECORE_PHOTOS") ?: (System.getProperty("user.home") + "/ForestAgeCounter"))
        val cases = listOf(
            Case("OCT3/382.jpg", 147.0),
            Case("OCT3/091604_592.jpg", 165.0),
            Case("PHOTOS/PXL_ACT_327.jpg", 129.0),
        )
        for (c in cases) {
            val f = File(root, c.rel)
            if (!f.isFile) {
                println("skip ${c.rel}: not present under $root")
                continue
            }
            val img = ImageIO.read(f)
            val (px, w, h) = downscale(img, 3)
            val core = CoreFinder.find(px, w, h)
            assertNotNull(core, "no core found in ${c.rel}")
            val width = core.widthPx * 3.0
            val err = abs(width - c.pythonWidthPx) / c.pythonWidthPx
            println(
                "${c.rel}: ${core.source} core ${core.ax.toInt()},${core.ay.toInt()} -> " +
                    "${core.bx.toInt()},${core.by.toInt()}, width $width px " +
                    "(python ${c.pythonWidthPx}, err ${"%.1f".format(err * 100)}%)",
            )
            assertTrue(err < 0.30, "${c.rel}: width $width vs ${c.pythonWidthPx}")
        }
    }

    /**
     * The photo card draws a yellow highlighter lane over its ruler. The lane
     * is longer and straighter than the core and used to win the shape score,
     * so the app proposed a strip of marker ink. It must now be rejected by
     * colour, and the core under the forester's red marks found instead.
     */
    @Test
    fun `the photo card's highlighter lane is not the core`() {
        val root = File(System.getenv("AGECORE_PHOTOS") ?: (System.getProperty("user.home") + "/ForestAgeCounter"))
        val f = File(root, "PHOTOS/PXL_ACT_327.jpg")
        if (!f.isFile) {
            println("skip PXL_ACT_327: not present under $root")
            return
        }
        val img = ImageIO.read(f)
        val (px, w, h) = downscale(img, 3)
        val core = assertNotNull(CoreFinder.find(px, w, h))
        val ya = core.ay * 3.0
        val yb = core.by * 3.0
        println(
            "PXL_ACT_327: ${core.source} core y ${ya.toInt()}..${yb.toInt()}, " +
                "width ${(core.widthPx * 3).toInt()} px",
        )
        assertTrue(core.source == "paper", "found a ${core.source} core")
        assertTrue(
            ya in 1350.0..1550.0 && yb in 1350.0..1550.0,
            "core at y $ya..$yb is not the marked core at y 1386..1477",
        )
    }
}
