package org.agecore.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import android.widget.ImageView
import org.agecore.core.Detector
import kotlin.math.hypot

/**
 * Draws the core path and the ring candidates over the photo.
 *
 * The path is stored in **bitmap pixels**, not view pixels. The view is laid
 * out again whenever the text below it grows or shrinks, so a path kept in
 * view coordinates drifts away from the photograph it belongs to - on the
 * BV5200 the proposal text alone moved the image by 90 px. Mapping through
 * the ImageView's matrix at draw and touch time keeps the path on the core.
 *
 * A top-level class, not a nested one: the layout names it as a tag, and an
 * XML tag cannot address a Kotlin nested class (`CountActivity.OverlayView`
 * resolves to a class literally named that, not to `CountActivity$OverlayView`,
 * so the screen crashed on inflation the first time it was run on a device).
 */
class OverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyle: Int = 0,
) : View(context, attrs, defStyle) {

    /** The image whose matrix maps bitmap pixels to this view's coordinates. */
    var imageView: ImageView? = null

    private val paint = Paint().apply { strokeWidth = 4f; style = Paint.Style.STROKE }
    private var start: Pair<Float, Float>? = null
    private var end: Pair<Float, Float>? = null
    private var bands: List<Detector.Band> = emptyList()
    private var bandPx = 24f
    private var listener: ((Pair<Float, Float>?, Pair<Float, Float>?) -> Unit)? = null

    fun setOnPathChanged(l: (Pair<Float, Float>?, Pair<Float, Float>?) -> Unit) {
        listener = l
    }

    fun reset() {
        start = null; end = null; bands = emptyList(); invalidate()
    }

    /** Show a proposed path without pretending it was tapped. Bitmap pixels. */
    fun setPath(s: Pair<Float, Float>, e: Pair<Float, Float>) {
        start = s; end = e; bands = emptyList(); invalidate()
    }

    fun setBands(b: List<Detector.Band>, band: Int) {
        bands = b; bandPx = band.toFloat(); invalidate()
    }

    private val matrixValues = FloatArray(9)

    /** Uniform scale of the image matrix, used to size the band in view px. */
    private fun viewScale(m: Matrix): Float {
        m.getValues(matrixValues)
        return hypot(matrixValues[Matrix.MSCALE_X], matrixValues[Matrix.MSKEW_Y])
    }

    override fun onTouchEvent(e: MotionEvent): Boolean {
        if (e.action != MotionEvent.ACTION_DOWN) return true
        val iv = imageView ?: return true
        val inv = Matrix()
        if (!iv.imageMatrix.invert(inv)) return true
        val q = floatArrayOf(e.x, e.y)
        inv.mapPoints(q)
        val p = q[0] to q[1]
        val s0 = start
        val e0 = end
        when {
            s0 == null -> start = p
            e0 == null -> end = p
            else -> {
                // With a proposed path already on screen, a tap means "put the
                // nearer end here", not "start over".
                val ds = hypot(p.first - s0.first, p.second - s0.second)
                val de = hypot(p.first - e0.first, p.second - e0.second)
                if (ds <= de) start = p else end = p
            }
        }
        listener?.invoke(start, end); invalidate(); return true
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val iv = imageView ?: return
        val s = start ?: return
        val m = iv.imageMatrix
        val scale = viewScale(m)
        val sp = floatArrayOf(s.first, s.second)
        m.mapPoints(sp)
        paint.color = Color.rgb(0, 200, 255)
        val e = end
        if (e == null) {
            canvas.drawCircle(sp[0], sp[1], 12f, paint)
            return
        }
        val ep = floatArrayOf(e.first, e.second)
        m.mapPoints(ep)
        canvas.drawLine(sp[0], sp[1], ep[0], ep[1], paint)
        if (bands.isEmpty()) return
        paint.color = Color.rgb(255, 40, 40)
        val d = hypot(ep[0] - sp[0], ep[1] - sp[1])
        if (d < 1f) return
        val ux = (ep[0] - sp[0]) / d
        val uy = (ep[1] - sp[1]) / d
        val half = bandPx / 2f * scale
        bands.forEach { b ->
            // b.startPx/endPx are along the core in bitmap pixels.
            val t0 = b.startPx * scale
            val t1 = b.endPx * scale
            val x0 = sp[0] + ux * t0; val y0 = sp[1] + uy * t0
            val x1 = sp[0] + ux * t1; val y1 = sp[1] + uy * t1
            val nx = -uy * half; val ny = ux * half
            val r = RectF(
                minOf(x0 - nx, x1 - nx), minOf(y0 - ny, y1 - ny),
                maxOf(x0 + nx, x1 + nx), maxOf(y0 + ny, y1 + ny),
            )
            canvas.drawRect(r, paint)
        }
    }
}
