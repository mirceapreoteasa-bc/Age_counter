package org.agecore.app

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Draw the evidence, so the forester can check the app rather than trust it.
 *
 * A count the forester cannot verify is worth very little, and the whole
 * project has been about not producing confident wrong numbers. So every
 * result ships with an annotated image: the bands the detector actually used,
 * the sections it could and could not resolve, and the rejected candidates
 * marked differently from the accepted ones.
 *
 * Colour carries the meaning:
 *   green  - a boundary the app accepted
 *   amber  - a boundary it accepted but flagged as doubtful
 *   grey   - a candidate it rejected, so the forester can see what was dropped
 *   red    - a section boundary, which is discarded rather than counted
 */
object Proof {

    // Color.rgb() is a call, not a constant expression, so these are val
    // rather than const val.
    private val GREEN: Int = Color.rgb(40, 200, 90)
    private val AMBER: Int = Color.rgb(255, 170, 0)
    private val GREY: Int = Color.rgb(150, 150, 150)
    private val RED: Int = Color.rgb(230, 60, 60)

    /**
     * Render one section.
     *
     * The section is a horizontal slice of the photo: the core runs along the
     * long axis, and [section] gives its position in *fractional* coordinates
     * so the drawing is independent of the image resolution.
     */
    fun drawSection(
        source: Bitmap,
        sectionLeft: Float,
        sectionRight: Float,
        boundariesFrac: List<Float>,
        rejectedFrac: List<Float> = emptyList(),
        label: String = "",
        count: Int = -1,
        ringMm: Float = 0f,
        pxPerMm: Float = 0f,
    ): Bitmap {
        val out = source.copy(Bitmap.Config.ARGB_8888, true)
        val c = Canvas(out)
        val w = out.width.toFloat()
        val h = out.height.toFloat()
        val top = h * 0.30f
        val bot = h * 0.78f

        // section extent
        val paint = Paint(Paint.ANTI_ALIAS_FLAG)
        paint.style = Paint.Style.FILL
        paint.color = Color.argb(38, 0, 0, 0)
        c.drawRect(sectionLeft * w, top - 12f, sectionRight * w, bot + 12f, paint)
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = max(2f, w * 0.004f)
        paint.color = RED
        c.drawLine(sectionLeft * w, top - 12f, sectionLeft * w, bot + 12f, paint)
        c.drawLine(sectionRight * w, top - 12f, sectionRight * w, bot + 12f, paint)

        // rejected candidates: thin, grey, so they read as "looked at, not used"
        paint.strokeWidth = max(1f, w * 0.0015f)
        paint.color = GREY
        for (f in rejectedFrac) {
            if (f < sectionLeft || f > sectionRight) continue
            c.drawLine(f * w, top, f * w, bot, paint)
        }

        // accepted boundaries: full height of the core, numbered
        val text = Paint(Paint.ANTI_ALIAS_FLAG)
        text.color = Color.WHITE
        text.textSize = max(14f, w * 0.022f)
        text.isFakeBoldText = true
        var n = 0
        for (f in boundariesFrac) {
            if (f < sectionLeft || f > sectionRight) continue
            n++
            // alternate colour if the spacing is suspiciously tight
            val tight = boundariesFrac.zipWithNext().any { (a, b) ->
                abs(b - a) * w < w * 0.012f }
            paint.color = if (tight) AMBER else GREEN
            paint.strokeWidth = max(3f, w * 0.005f)
            c.drawLine(f * w, top, f * w, bot, paint)
            c.drawText(n.toString(), f * w + 4f, top - 6f, text)
        }

        // header
        val head = Paint(Paint.ANTI_ALIAS_FLAG)
        head.color = Color.WHITE
        head.textSize = max(18f, w * 0.030f)
        head.isFakeBoldText = true
        val shade = Paint(Paint.ANTI_ALIAS_FLAG)
        shade.color = Color.argb(170, 0, 0, 0)
        c.drawRect(0f, 0f, w, h * 0.10f, shade)
        val cap = if (count >= 0)
            "$label  $count rings" + if (ringMm > 0f) "  ${fmt(ringMm)} mm/ring" else ""
        else "$label  not resolved"
        c.drawText(cap, w * 0.02f, h * 0.07f, head)

        val foot = Paint(Paint.ANTI_ALIAS_FLAG)
        foot.color = Color.LTGRAY
        foot.textSize = max(14f, w * 0.020f)
        val scaleTxt = if (pxPerMm > 0f)
            "${fmt(pxPerMm)} px/mm   green = counted, grey = rejected, red = section edge"
        else "no scale   green = counted, grey = rejected"
        c.drawText(scaleTxt, w * 0.02f, h * 0.955f, foot)
        return out
    }

    /** Full-core overview with every section labelled. */
    fun drawOverview(
        source: Bitmap,
        sections: List<SectionDrawing>,
        pxPerMm: Float,
        header: String,
        coreTop: Float = 0.32f,
        coreBot: Float = 0.74f,
    ): Bitmap {
        val out = source.copy(Bitmap.Config.ARGB_8888, true)
        val c = Canvas(out)
        val w = out.width.toFloat()
        val h = out.height.toFloat()
        val top = h * coreTop
        val bot = h * coreBot
        val paint = Paint(Paint.ANTI_ALIAS_FLAG)

        for (s in sections) {
            val l = s.left * w
            val r = s.right * w
            paint.style = Paint.Style.FILL
            paint.color = if (s.resolved) Color.argb(30, 40, 200, 90)
                          else Color.argb(30, 230, 60, 60)
            c.drawRect(l, top, r, bot, paint)
            paint.style = Paint.Style.STROKE
            paint.strokeWidth = max(2f, w * 0.003f)
            paint.color = if (s.resolved) GREEN else RED
            c.drawLine(l, top - 10f, l, bot + 10f, paint)
            c.drawLine(r, top - 10f, r, bot + 10f, paint)

            // One red rectangle per counted year, the same convention used in
            // the hand-marked reference photos. Height is the full width of the
            // core and width is 25% of the interval, so each box sits inside
            // the growth band it claims and never overlaps its neighbour.
            //
            // Cyan boxes are counted years and their number is exactly the
            // count on the label. An unresolved bay still shows the intervals
            // the app was weighing, in grey, because seeing why it refused is
            // the point of a proof image - but grey boxes are NOT part of any
            // total, so the cyan count and the reported count always agree.
            val strokeC = if (s.resolved) MARK_CYAN else MARK_GREY
            val fillC = if (s.resolved) Color.argb(56, 0, 229, 255)
                        else Color.argb(34, 150, 150, 150)
            //
            // The marker lies ALONG the ring, in the middle of the band between
            // two dark lines. When the rings are square to the axis that is a
            // vertical rectangle, which is the familiar case. When they run
            // obliquely a vertical box would cut diagonally across the very
            // ring it claims to be inside, so the box is sheared to lean with
            // them: each end is displaced by the ring's own slope.
            val mid = (top + bot) / 2f
            val halfH = (bot - top) / 2f
            val shearX =
                (Math.tan(Math.toRadians(s.ringAngleDeg)) * halfH).toFloat()
            for (iv in s.intervals) {
                val cx = iv.centreFrac * w
                val hw = iv.halfWidthFrac * w
                val path = Path()
                path.moveTo(cx - hw - shearX, top)
                path.lineTo(cx + hw - shearX, top)
                path.lineTo(cx + hw + shearX, bot)
                path.lineTo(cx - hw + shearX, bot)
                path.close()
                paint.style = Paint.Style.STROKE
                paint.strokeWidth = max(3f, w * 0.0032f)
                paint.color = strokeC
                c.drawPath(path, paint)
                paint.style = Paint.Style.FILL
                paint.color = fillC
                c.drawPath(path, paint)
            }
            // A short tick at the middle of the core, on the band centre, so
            // the reading is unambiguous even where boxes crowd together.
            for (iv in s.intervals) {
                val cx = iv.centreFrac * w
                val hw = max(2f, iv.halfWidthFrac * w)
                paint.style = Paint.Style.STROKE
                paint.strokeWidth = max(2f, w * 0.0018f)
                paint.color = strokeC
                c.drawLine(cx - hw, mid, cx + hw, mid, paint)
            }

            val t = Paint(Paint.ANTI_ALIAS_FLAG)
            t.color = Color.WHITE
            t.textSize = max(16f, w * 0.026f)
            t.isFakeBoldText = true
            c.drawText(
                if (s.resolved) "${s.label} ${s.count}" else "${s.label} ?",
                l + 6f, top - 16f, t)
        }

        val shade = Paint(Paint.ANTI_ALIAS_FLAG)
        shade.color = Color.argb(180, 0, 0, 0)
        c.drawRect(0f, 0f, w, h * 0.12f, shade)
        val head = Paint(Paint.ANTI_ALIAS_FLAG)
        head.color = Color.WHITE
        head.textSize = max(20f, w * 0.032f)
        head.isFakeBoldText = true
        c.drawText(header, w * 0.02f, h * 0.055f, head)
        val sub = Paint(Paint.ANTI_ALIAS_FLAG)
        sub.color = Color.LTGRAY
        sub.textSize = max(15f, w * 0.022f)
        val lean = sections.map { it.ringAngleDeg }
            .filter { Math.abs(it) >= 0.5 }
        c.drawText("green bay = resolved, red bay = not resolved; " +
            "CYAN box = a year the app COUNTED; grey = candidates in a bay it " +
            "refused; any red boxes are your own marks" +
            (if (pxPerMm > 0f) "   ${fmt(pxPerMm)} px/mm" else "") +
            (if (lean.isNotEmpty())
                String.format("   rings %.1f deg off axis, markers follow them",
                    lean.average()) else ""),
            w * 0.02f, h * 0.10f, sub)
        return out
    }

    data class SectionDrawing(
        val label: String,
        val left: Float,
        val right: Float,
        val count: Int,
        val resolved: Boolean,
        /** one entry per counted year; drives the red rectangles */
        val intervals: List<org.agecore.core.Sections.Interval> = emptyList(),
        /** ring lean in degrees from the core axis */
        val ringAngleDeg: Double = 0.0,
    )

    /**
     * Cyan, not red. The reference photos already carry the user's own red
     * hand-drawn rectangles, and drawing the app's marks in the same red made
     * the two sets impossible to tell apart, which defeats the whole point of
     * a proof image. Cyan also stays legible over the green-brown core and
     * against the green/red bay shading.
     */
    private val MARK_CYAN = Color.rgb(0, 229, 255)

    /** Candidates in a bay the app refused to count. Deliberately muted. */
    private val MARK_GREY = Color.rgb(150, 150, 150)

    private fun fmt(v: Float) = if (v >= 10f) String.format("%.1f", v)
                               else String.format("%.2f", v)
}
