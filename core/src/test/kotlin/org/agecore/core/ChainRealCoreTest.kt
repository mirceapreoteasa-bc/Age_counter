package org.agecore.core

import java.io.File
import javax.imageio.ImageIO
import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.roundToInt
import kotlin.test.Test
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/**
 * End to end on real cores with known counts: find the core, sample the strip
 * the way the count screen does, and count with the chain.
 *
 * The point is the *sampling band*. The screen used 10% of the image height,
 * which on these photos is about twice the core width; the background then
 * fills most of the strip. These checks record what 60% of the found core
 * width does instead, against the counts the forester established.
 *
 * The photographs live outside the repository; without them the checks print a
 * note and pass, so the suite still runs on another machine.
 */
class ChainRealCoreTest {

    private data class Case(
        val rel: String,
        val pxPerMm: Double,
        val ringMm: Double,
        val truth: Int,
    )

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

    private fun lumAt(px: IntArray, w: Int, h: Int, x: Int, y: Int): Double {
        val c = px[y.coerceIn(0, h - 1) * w + x.coerceIn(0, w - 1)]
        return 0.299 * ((c shr 16) and 0xFF) +
            0.587 * ((c shr 8) and 0xFF) + 0.114 * (c and 0xFF)
    }

    /** Same geometry and resampling as engine/annotate.py: bilinear, clamped. */
    private fun sample(
        px: IntArray, w: Int, h: Int,
        ax: Double, ay: Double, bx: Double, by: Double, band: Int,
    ): Pair<DoubleArray, Int> {
        val len = maxOf(16, hypot(bx - ax, by - ay).roundToInt())
        val ux = (bx - ax) / len
        val uy = (by - ay) / len
        val vx = -uy
        val vy = ux
        val out = DoubleArray(len * band)
        val half = (band - 1) / 2.0
        var i = 0
        for (t in 0 until len) {
            for (q in 0 until band) {
                val o = q - half
                val x = ax + ux * t + vx * o
                val y = ay + uy * t + vy * o
                val x0 = kotlin.math.floor(x).toInt()
                val y0 = kotlin.math.floor(y).toInt()
                val fx = x - x0
                val fy = y - y0
                val v00 = lumAt(px, w, h, x0, y0)
                val v10 = lumAt(px, w, h, x0 + 1, y0)
                val v01 = lumAt(px, w, h, x0, y0 + 1)
                val v11 = lumAt(px, w, h, x0 + 1, y0 + 1)
                out[i++] = v00 * (1 - fx) * (1 - fy) + v10 * fx * (1 - fy) +
                    v01 * (1 - fx) * fy + v11 * fx * fy
            }
        }
        return out to len
    }

    @Test
    fun `the chain counts real cores with a band from the found core width`() {
        val root = File(System.getenv("AGECORE_PHOTOS") ?: (System.getProperty("user.home") + "/ForestAgeCounter"))
        val cases = listOf(
            Case("OCT3/382.jpg", 29.2, 0.79, 132),
            Case("OCT3/091604_592.jpg", 31.4, 2.37, 60),
        )
        var ran = 0
        for (c in cases) {
            val f = File(root, c.rel)
            if (!f.isFile) {
                println("skip ${c.rel}: not present under $root")
                continue
            }
            val img = ImageIO.read(f)
            val (small, sw, sh) = downscale(img, 3)
            val core = assertNotNull(CoreFinder.find(small, sw, sh), c.rel)

            // The finder worked at 1/3 scale; map back to the photo.
            val fx = img.width.toDouble() / sw
            val fy = img.height.toDouble() / sh
            val ax = core.ax * fx
            val ay = core.ay * fy
            val bx = core.bx * fx
            val by = core.by * fy
            val width = core.widthPx * (fx + fy) / 2.0

            val full = IntArray(img.width * img.height)
            img.getRGB(0, 0, img.width, img.height, full, 0, img.width)

            val corePx = (0.60 * width).roundToInt()
            val framePx = (0.10 * img.height).roundToInt()
            val narrow = sample(full, img.width, img.height, ax, ay, bx, by, corePx)
            val wide = sample(full, img.width, img.height, ax, ay, bx, by, framePx)
            val n = Chain.countRings(narrow.first, narrow.second, corePx,
                c.pxPerMm, c.ringMm)
            val w = Chain.countRings(wide.first, wide.second, framePx,
                c.pxPerMm, c.ringMm)
            // The ladder is the cross-check, not the reported count. It must
            // at least run: a threshold bug (Otsu on the closed image instead
            // of on the black-hat response) made every rung empty and the
            // screen said "0 rings (unresolved)".
            val det = Detector.count(narrow.first, narrow.second, corePx,
                c.pxPerMm, c.ringMm)
            println(
                "${c.rel}: ladder ${det.count} resolved ${det.resolved} " +
                    "(counts ${det.ladderCounts}), chain ${n.count}",
            )
            assertTrue(
                det.ladderCounts.size >= 3 && det.count >= 2,
                "${c.rel}: the ladder found no rings (${det.ladderCounts})",
            )
            println(
                "${c.rel}: core ${width.roundToInt()} px, band ${corePx} px -> " +
                    "${n.count} rings (err ${"%.1f".format(100.0 * abs(n.count - c.truth) / c.truth)}%); " +
                    "frame band ${framePx} px -> ${w.count} rings",
            )
            ran++
            assertTrue(
                abs(n.count - c.truth) / c.truth.toDouble() <= 0.10,
                "${c.rel}: ${n.count} against ${c.truth}",
            )
            assertTrue(
                abs(n.count - c.truth) <= abs(w.count - c.truth),
                "${c.rel}: the core band (${n.count}) should not be worse than " +
                    "the frame band (${w.count})",
            )
        }
        if (ran == 0) println("no real cores available; checks skipped")
    }
}
