package org.agecore.app

import android.app.AlertDialog
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Matrix
import android.os.Bundle
import android.text.InputType
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.documentfile.provider.DocumentFile
import org.agecore.core.Chain
import org.agecore.core.CoreFinder
import org.agecore.core.Detector
import java.io.File
import kotlin.math.floor
import kotlin.math.hypot
import kotlin.math.roundToInt

/**
 * Count screen: show the photo, propose the path along the core, let the
 * forester adjust it, give the scale and the ring width, then read the result.
 *
 * The path is found automatically where the finder can see the core, and the
 * forester can still tap either end to correct it. The proposal is a
 * convenience, not a claim: the photo and the marks remain the evidence.
 *
 * The result is a set of candidate rings, never a bare number. If the pre-filter
 * ladder disagrees the screen says so instead of printing a count.
 */
class CountActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_PHOTO = "photo"

        /** XGV<groundcall>P<point>T<tree> with an optional S<segment>. */
        val NAME_PATTERN = Regex("^XGV[A-Za-z0-9]+P\\d+T\\d+(S\\d+)?$")
    }

    private lateinit var image: ImageView
    private lateinit var pathView: OverlayView
    private lateinit var resultText: TextView
    private lateinit var ringWidthInput: EditText
    private lateinit var coreLengthInput: EditText
    private lateinit var countButton: Button
    private lateinit var saveButton: Button

    /**
     * Identifies the newest count request.
     *
     * Sampling and counting run off the UI thread. If the forester moves the
     * path or presses Redo while a count is in flight, that answer is for a
     * path that no longer exists, so the generation is bumped and the stale
     * answer is dropped rather than shown.
     */
    private var countGeneration = 0

    private var photoFile: File? = null
    private var bitmap: Bitmap? = null
    private var result: Detector.Result? = null
    /** The chain counter's answer, when it ran. */
    private var chainResult: Chain.Result? = null

    /**
     * Width of the core proposed by [CoreFinder], in full-resolution pixels.
     *
     * The sampling band is a fraction of the *core*, not of the frame: at 10%
     * of the image height a 165 px core on a 3120 px photo was sampled as a
     * 312 px band, with 147 px of background in it. Measured on 382 and 592,
     * 60% of the core width gives 126 and 60 rings against truths of 132 and
     * 60; the frame-relative band gives 152 and 55.
     */
    private var coreWidthPx: Double? = null

    /** Scale of the last applied count, for the result text. */
    private var lastPxPerMm = 0.0

    /** Phase-two bookkeeping: distinguishes "still running" from "failed". */
    private var ladderDone = false

    /**
     * The count that goes in the record.
     *
     * The chain counter is the primary one, because on four real cores with
     * known counts it lands within 5% where the pre-filter ladder is 35-65%
     * out. The ladder's answer is still recorded, beside it: a disagreement
     * between two independent counters is a fact about the frame, and the
     * record should carry it rather than hide it.
     */
    private fun primaryCount(res: Detector.Result): Int =
        chainResult?.takeIf { it.count >= 2 }?.count ?: res.count
    private var startPt: Pair<Float, Float>? = null
    private var endPt: Pair<Float, Float>? = null
    private var intervals: List<Detector.Interval> = emptyList()
    /** true when the forester is counting only a portion of a whole core. */
    private var partialCount = false
    private var pithAtStart = true

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_count)

        image = findViewById(R.id.image)
        pathView = findViewById(R.id.overlay)
        pathView.imageView = image
        resultText = findViewById(R.id.resultText)
        ringWidthInput = findViewById(R.id.ringWidth)
        coreLengthInput = findViewById(R.id.coreLength)

        val path = intent.getStringExtra(EXTRA_PHOTO) ?: run { finish(); return }
        photoFile = File(path)
        bitmap = android.graphics.BitmapFactory.decodeFile(path)
        image.setImageBitmap(bitmap)
        image.scaleType = ImageView.ScaleType.FIT_CENTER

        pathView.setOnPathChanged { s, e ->
            cancelCount()
            // The old count belongs to the old path: never leave marks or a
            // savable record on screen that no longer matches the line.
            result = null; chainResult = null; intervals = emptyList()
            ladderDone = false
            saveButton.isEnabled = false
            pathView.setBands(emptyList(), 1)
            startPt = s; endPt = e
            resultText.text = if (s == null) {
                "Tap the two ends of the core."
            } else {
                "Path set (${((hypot(e!!.first - s!!.first, e.second - s.second)).roundToInt())} px). Enter scale and ring width, then Count."
            }
        }

        // The finder needs the ImageView laid out, because the path comes back
        // in image pixels and has to be drawn in view pixels.
        image.post { proposeCore() }

        countButton = findViewById(R.id.btnCount)
        saveButton = findViewById(R.id.btnSave)
        countButton.setOnClickListener { runCount() }
        findViewById<Button>(R.id.btnPartial).setOnClickListener { choosePartial() }
        saveButton.setOnClickListener { promptForName() }
        findViewById<Button>(R.id.btnRedo).setOnClickListener {
            cancelCount()
            startPt = null; endPt = null; result = null; chainResult = null
            intervals = emptyList(); coreWidthPx = null; ladderDone = false
            saveButton.isEnabled = false
            pathView.reset()
            // Redo used to clear the path and leave the forester to tap it
            // again. Now that there is a finder, redo means "drop my edits and
            // propose again"; if it finds nothing the screen says to tap.
            proposeCore()
        }
    }

    /**
     * Partial_age_count: the forester is counting only a portion of a core, so
     * the app must not treat either end as a pith or a bark. It just counts the
     * span between the taps and says so.
     */
    private fun choosePartial() {
        val options = arrayOf(
            "Pith side is the START of my path",
            "Pith side is the END of my path",
            "Cancel",
        )
        AlertDialog.Builder(this)
            .setTitle("Partial age count")
            .setMessage(
                "Count only the portion between your two taps. Bark is usually " +
                    "not collected and many cores lack a proper pith, so the " +
                    "app will not look for either end, and will not add rings " +
                    "for missing years.\n\nWhich end is the pith side?",
            )
            .setItems(options) { _, which ->
                when (which) {
                    0 -> { partialCount = true; pithAtStart = true }
                    1 -> { partialCount = true; pithAtStart = false }
                    else -> return@setItems
                }
                findViewById<Button>(R.id.btnPartial).text = "Partial: START"
                result = null; intervals = emptyList()
                pathView.setBands(emptyList(), 0)
                resultText.text = "Partial count. Tap the two ends of the portion " +
                    "to count, then press Count."
            }
            .show()
    }

    /**
     * Propose the core path automatically, so the forester confirms instead of
     * tapping blind. The search runs on a reduced copy (the finder works at a
     * few hundred pixels across) and the ends are mapped back through the
     * ImageView's matrix, so a letterboxed photo is still right.
     *
     * A failure is not an error: many frames have no core the finder can see,
     * and then the screen falls back to tapping, which is what it always did.
     */
    private fun proposeCore() {
        val bmp = bitmap ?: return
        resultText.text = "Looking for the core..."
        Thread {
            val t0 = System.nanoTime()
            val found = try {
                findCoreIn(bmp)
            } catch (e: Exception) {
                null
            }
            android.util.Log.i("AgeCoreProposal",
                "find took ${(System.nanoTime() - t0) / 1_000_000} ms")
            runOnUiThread {
                if (isFinishing || isDestroyed) return@runOnUiThread
                if (found == null) {
                    resultText.text = "No core found automatically. " +
                        "Tap the two ends of the core."
                    return@runOnUiThread
                }
                applyProposal(found)
            }
        }.start()
    }

    /**
     * Find the core in the bitmap the screen already has.
     *
     * Re-decoding the file at 1/4 scale was the slow part on the BV5200 (a
     * 10 MB JPEG, several seconds). The full bitmap is in memory anyway, and
     * scaling it down is a native blit.
     */
    private fun findCoreIn(bmp: Bitmap): CoreFinder.Core? {
        var scale = 1
        while (maxOf(bmp.width, bmp.height) / scale > 1500) scale *= 2
        val small = if (scale == 1) bmp else Bitmap.createScaledBitmap(
            bmp, bmp.width / scale, bmp.height / scale, true,
        )
        val w = small.width
        val h = small.height
        val px = IntArray(w * h)
        small.getPixels(px, 0, w, 0, 0, w, h)
        if (small !== bmp) small.recycle()
        val core = CoreFinder.find(px, w, h) ?: return null
        // Working pixels -> full-resolution image pixels.
        val fx = bmp.width.toDouble() / w
        val fy = bmp.height.toDouble() / h
        return core.copy(
            ax = core.ax * fx, ay = core.ay * fy,
            bx = core.bx * fx, by = core.by * fy,
            widthPx = core.widthPx * (fx + fy) / 2.0,
            pxPerMm = core.pxPerMm * (fx + fy) / 2.0,
        )
    }

    private fun applyProposal(core: CoreFinder.Core) {
        coreWidthPx = core.widthPx
        val a = core.ax.toFloat() to core.ay.toFloat()
        val b = core.bx.toFloat() to core.by.toFloat()
        startPt = a
        endPt = b
        pathView.setPath(a, b)
        val lenMm = core.lengthPx / maxOf(core.pxPerMm, 1e-9)
        val sb = StringBuilder()
        sb.append("Core found (${core.source} background). Check the path: " +
            "tap either end to correct it.")
        sb.append("\nLength about ${"%.0f".format(lenMm)} mm at the 5 mm width " +
            "assumption; enter the measured core length.")
        core.notes.filter { it.contains("below") }.forEach { sb.append("\n- $it") }
        resultText.text = sb.toString()
    }

    /** Everything the background count needs, captured on the UI thread. */
    private class CountInput(
        val bmp: Bitmap,
        val ax: Float, val ay: Float, val bx: Float, val by: Float,
        val band: Int,
        val pxPerMm: Double,
        val ringMm: Double?,
    )

    /** The answer, computed off the UI thread and applied on it. */
    private class CountOutput(
        /** Null while the ladder cross-check is still running (phase two). */
        val res: Detector.Result?,
        val chain: Chain.Result?,
        val intervals: List<Detector.Interval>,
        val marks: List<Detector.Band>,
        val band: Int,
        val pxPerMm: Double,
        val len: Int,
        val strip: DoubleArray,
        val ringMm: Double?,
    )

    /**
     * Drop an in-flight count and put the buttons back. Called when the path
     * changes, on Redo, and when the screen goes away.
     */
    private fun cancelCount() {
        countGeneration++
        if (::countButton.isInitialized) {
            countButton.isEnabled = true
            countButton.text = "Count"
        }
        // Save is enabled by the count itself, not here: a cancelled count
        // must not leave an older record saveable against a new path.
    }

    /** Scale comes from the forester, who can see the ruler. */
    private fun runCount() {
        val bmp = bitmap ?: return
        val s = startPt ?: return Toast.makeText(this, "Tap the core path first", Toast.LENGTH_SHORT).show()
        val e = endPt ?: return
        val coreMm = coreLengthInput.text.toString().toDoubleOrNull()
            ?: return Toast.makeText(this, "Enter the core length in mm", Toast.LENGTH_SHORT).show()
        val ringMm = ringWidthInput.text.toString().toDoubleOrNull()

        // The path is already in bitmap pixels (see OverlayView).
        val ax = s.first; val ay = s.second
        val bx = e.first; val by = e.second
        val lenPx = hypot(bx - ax, by - ay)
        if (lenPx < 50) {
            Toast.makeText(this, "Path too short", Toast.LENGTH_SHORT).show(); return
        }
        val pxPerMm = lenPx / coreMm
        // A band relative to the core when its width is known, because the
        // frame is not the core: 10% of a 3120 px photo is 312 px for a 165 px
        // core, so most of the sampled strip would be background.
        val band = coreWidthPx
            ?.let { maxOf(24, (0.60 * it).roundToInt()) }
            ?.coerceAtMost(bmp.height)
            ?: maxOf(24, (bmp.height * 0.10).roundToInt())

        // Bilinear sampling touches four pixels per sample, so on a 4000 px
        // core this is a few hundred thousand reads. Run it off the UI thread
        // instead of freezing the screen while it finishes.
        val gen = ++countGeneration
        countButton.isEnabled = false
        countButton.text = "Counting..."
        saveButton.isEnabled = false
        resultText.text = "Counting..."
        val input = CountInput(bmp, ax, ay, bx, by, band, pxPerMm, ringMm)
        Thread {
            val out = try {
                computeCount(input)
            } catch (e: Exception) {
                null
            }
            runOnUiThread {
                if (isFinishing || isDestroyed) return@runOnUiThread
                countButton.isEnabled = true
                countButton.text = "Count"
                if (gen != countGeneration) return@runOnUiThread
                if (out == null) {
                    resultText.text = "The count failed. Check the path and try again."
                    return@runOnUiThread
                }
                applyCount(out)
            }
            // Phase two: the ladder is a cross-check and costs about ten
            // seconds on a long core, while the chain takes under half a
            // second. Show the count and its marks first, then fill this in.
            if (out != null && out.res == null) {
                val ladder = try {
                    Detector.count(out.strip, out.len, out.band, out.pxPerMm,
                        out.ringMm)
                } catch (e: Exception) {
                    null
                }
                val ivs = if (out.ringMm != null && out.ringMm > 0) {
                    try {
                        Detector.countIntervals(out.strip, out.len, out.band,
                            out.pxPerMm, out.ringMm, 6)
                    } catch (e: Exception) {
                        emptyList()
                    }
                } else emptyList()
                runOnUiThread {
                    if (isFinishing || isDestroyed) return@runOnUiThread
                    if (gen != countGeneration) return@runOnUiThread
                    applyLadder(ladder, ivs)
                }
            }
        }.start()
    }

    /** Sampling and both counters; pure, so it can run off the UI thread. */
    private fun computeCount(input: CountInput): CountOutput {
        val t0 = System.nanoTime()
        val strip = sample(input.bmp, input.ax, input.ay, input.bx, input.by,
            input.band)
        val len = strip.size / input.band
        // The chain counter is the primary one: on four real cores with known
        // counts it lands within 5% where the pre-filter ladder is 35-65% out.
        // The ladder is kept as the cross-check, and the two are shown
        // together, because a disagreement is information.
        val chain = if (input.ringMm != null && input.ringMm > 0) {
            Chain.countRings(strip, len, input.band, input.pxPerMm, input.ringMm)
        } else null
        val tChain = System.nanoTime()
        // The ladder is only the cross-check, but its bands are the fallback
        // marks when the chain failed, so it must run before the first paint
        // in that case. When the chain has a count, defer it to phase two -
        // and defer the intervals too, because they are nine passes per
        // stretch and cost as much as the ladder.
        val chainOk = chain != null && chain.count >= 2
        val res = if (chainOk) null
            else Detector.count(strip, len, input.band, input.pxPerMm, input.ringMm)
        val intervals = if (chainOk) emptyList()
            else if (input.ringMm != null && input.ringMm > 0) {
                Detector.countIntervals(strip, len, input.band, input.pxPerMm,
                    input.ringMm, 6)
            } else emptyList()
        // The marks on the photo are the chain's boundaries when it has them:
        // one thin rectangle per accepted boundary, so the picture shows
        // exactly what was counted.
        val marks = if (chainOk) {
            chain!!.positions.map { p ->
                Detector.Band(p - 1, p + 1, p.toDouble(), 2.0,
                    2.0 / input.pxPerMm, 0, input.band)
            }
        } else {
            res!!.bands
        }
        android.util.Log.d("AgeCoreCount",
            "sample ${(tChain - t0) / 1_000_000} ms, chain " +
                "${(System.nanoTime() - tChain) / 1_000_000} ms, " +
                "strip ${len}x${input.band}, ladder ${if (res == null) "deferred" else "now"}")
        return CountOutput(res, chain, intervals, marks, input.band,
            input.pxPerMm, len, strip, input.ringMm)
    }

    /** Put the chain's answer on screen, on the UI thread. */
    private fun applyCount(out: CountOutput) {
        chainResult = out.chain
        intervals = out.intervals
        result = out.res
        lastPxPerMm = out.pxPerMm
        pathView.setBands(out.marks, out.band)
        // The record needs the ladder, so saving waits for phase two.
        ladderDone = out.res != null
        saveButton.isEnabled = out.res != null
        render()
    }

    /** Phase two: the cross-check arrived (or failed); redraw the text. */
    private fun applyLadder(res: Detector.Result?,
                            ivs: List<Detector.Interval>) {
        result = res
        intervals = ivs
        ladderDone = true
        saveButton.isEnabled = res != null
        render()
    }

    /** Build the result text from the current fields. */
    private fun render() {
        val res = result
        val chain = chainResult
        val pxPerMm = lastPxPerMm

        val sb = StringBuilder()
        if (chain != null && chain.count >= 2) {
            sb.append("Rings: ${chain.count}\n")
            sb.append("Boundaries marked in red: ${chain.positions.size}\n")
            if (chain.skipped > 0) {
                sb.append("Stretches with no boundary found: ${chain.skipped}\n")
            }
            sb.append("Spacing: ${"%.3f".format(chain.spacingMedianPx / pxPerMm)} mm " +
                "(${"%.1f".format(chain.spacingMedianPx)} px)\n")
            if (res == null) {
                sb.append(
                    if (ladderDone) "Cross-check (pre-filter ladder): failed\n"
                    else "Cross-check (pre-filter ladder): checking...\n",
                )
            } else {
                sb.append("Cross-check (pre-filter ladder): ${res.count} rings" +
                    if (res.resolved) "" else " (unresolved)" + "\n")
                if (res.resolved && res.count >= 2) {
                    val diff = kotlin.math.abs(res.count - chain.count).toDouble() /
                        maxOf(1, chain.count)
                    if (diff > 0.2) {
                        sb.append("The two counters differ by ${"%.0f".format(diff * 100)}%, " +
                            "so the count is provisional - check the marks.\n")
                    }
                }
            }
        } else if (res != null && res.resolved) {
            sb.append("Rings: ${res.count}\n")
            sb.append("Confidence: +/- ${res.confidence}\n")
            sb.append("Spacing: ${"%.3f".format(res.spacingMm)} mm (${"%.1f".format(res.pxPerRing)} px)\n")
            sb.append("Candidates shown in red: ${res.bands.size}")
        } else if (res != null) {
            sb.append("NOT RESOLVED\n")
            sb.append("No count is reported when the ring spacing is not stable.\n")
        } else {
            sb.append("NOT RESOLVED\n")
            sb.append("The cross-check failed, so nothing is counted.\n")
        }
        res?.warnings?.forEach { sb.append("\n- $it") }
        if (intervals.isNotEmpty()) {
            sb.append("\n\nIntervals:")
            intervals.forEachIndexed { i, iv ->
                val tag = when (iv.quality) {
                    "good" -> "ok"; "marginal" -> "check"; else -> "POOR"
                }
                sb.append("\n  ${i + 1}: ${iv.count} rings  [$tag]")
            }
            val poor = intervals.count { it.quality == "poor" }
            val marginal = intervals.count { it.quality == "marginal" }
            if (poor > 0 || marginal > 0) {
                sb.append("\n$poor poor and $marginal marginal stretches are " +
                    "not counted confidently. They are left unmarked in the proof.")
            }
        }
        if (partialCount) {
            sb.append("\n\nPARTIAL COUNT: rings between the two taps only. " +
                "The ${if (pithAtStart) "start" else "end"} of the path is the pith side.")
            sb.append("\nBark and pith are not searched for, and no rings are " +
                "added for missing years.")
        }
        resultText.text = sb.toString()
    }

    /** Resample along the path so the core runs along axis 0 of a band x len array. */
    private fun lum(c: Int): Double =
        0.299 * ((c shr 16) and 0xFF) +
            0.587 * ((c shr 8) and 0xFF) + 0.114 * (c and 0xFF)

    /**
     * Resample the path into a strip, length x band.
     *
     * Bilinear with a (band - 1) / 2 offset, matching
     * `map_coordinates(order=1)` in engine/annotate.py. The pixels are pulled
     * once with a single getPixels over the path's bounding box: calling
     * getPixel four times per sample (1.6 M JNI calls) took 2.6 s on the
     * BV5200, and this is the same picture.
     */
    private fun sample(bmp: Bitmap, ax: Float, ay: Float, bx: Float, by: Float,
                       band: Int): DoubleArray {
        val len = hypot(bx - ax, by - ay).roundToInt().coerceAtLeast(16)
        val ux = (bx - ax) / len; val uy = (by - ay) / len
        val vx = -uy; val vy = ux
        val half = (band - 1) / 2.0

        var minX = Double.MAX_VALUE; var maxX = -Double.MAX_VALUE
        var minY = Double.MAX_VALUE; var maxY = -Double.MAX_VALUE
        for (t in intArrayOf(0, len - 1)) for (q in doubleArrayOf(-half, half)) {
            val x = ax + ux * t + vx * q
            val y = ay + uy * t + vy * q
            if (x < minX) minX = x; if (x > maxX) maxX = x
            if (y < minY) minY = y; if (y > maxY) maxY = y
        }
        val x0 = floor(minX).toInt().coerceIn(0, bmp.width - 1)
        val y0 = floor(minY).toInt().coerceIn(0, bmp.height - 1)
        val x1 = (floor(maxX).toInt() + 2).coerceIn(0, bmp.width - 1)
        val y1 = (floor(maxY).toInt() + 2).coerceIn(0, bmp.height - 1)
        val rw = x1 - x0 + 1
        val rh = y1 - y0 + 1
        val px = IntArray(rw * rh)
        bmp.getPixels(px, 0, rw, x0, y0, rw, rh)

        val out = DoubleArray(len * band)
        var k = 0
        for (t in 0 until len) {
            for (q in 0 until band) {
                val o = q - half
                val x = ax + ux * t + vx * o
                val y = ay + uy * t + vy * o
                val cx = floor(x).toInt()
                val cy = floor(y).toInt()
                val fx = x - cx
                val fy = y - cy
                val lx = (cx - x0).coerceIn(0, rw - 1)
                val ly = (cy - y0).coerceIn(0, rh - 1)
                val nx = (lx + 1).coerceAtMost(rw - 1)
                val ny = (ly + 1).coerceAtMost(rh - 1)
                val v00 = lum(px[ly * rw + lx])
                val v10 = lum(px[ly * rw + nx])
                val v01 = lum(px[ny * rw + lx])
                val v11 = lum(px[ny * rw + nx])
                out[k++] = v00 * (1 - fx) * (1 - fy) + v10 * fx * (1 - fy) +
                    v01 * (1 - fx) * fy + v11 * fx * fy
            }
        }
        return out
    }

    private fun promptForName() {
        val res = result
        if (res == null) {
            Toast.makeText(this, "Run a count first", Toast.LENGTH_SHORT).show(); return
        }
        val input = EditText(this).apply {
            hint = "XGV123P4T5  (add S1 for a segment)"
            inputType = InputType.TYPE_CLASS_TEXT
            setSingleLine()
        }
        AlertDialog.Builder(this)
            .setTitle("Save age count")
            .setMessage(
                "Name this count. Format XGV<groundcall>P<point>T<tree>, with " +
                    "S<segment> for part of a broken core.",
            )
            .setView(input)
            .setPositiveButton("Save") { _, _ -> save(input.text.toString().trim()) }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun save(name: String) {
        val res = result ?: return
        if (!NAME_PATTERN.matches(name)) {
            AlertDialog.Builder(this)
                .setTitle("Name not valid")
                .setMessage(
                    "\"$name\" does not match XGV<groundcall>P<point>T<tree> " +
                        "with an optional S<segment>.\nFor example XGV237P2T6 or " +
                        "XGV237P2T6S1. Nothing was saved.",
                )
                .setPositiveButton(android.R.string.ok, null)
                .show()
            return
        }
        val folder = Prefs.workFolder(this)
        if (folder == null) {
            Toast.makeText(this, "Set a work folder in Settings first", Toast.LENGTH_LONG).show()
            return
        }
        val doc = DocumentFile.fromTreeUri(this, folder)
            ?: return Toast.makeText(this, "Work folder unavailable", Toast.LENGTH_LONG).show()

        val bmp = bitmap ?: return
        val proof = renderProof(bmp, name)
        val png = doc.findFile("$name.png") ?: doc.createFile("image/png", "$name.png")
        if (png != null) {
            contentResolver.openOutputStream(png.uri)?.use { proof.compress(Bitmap.CompressFormat.PNG, 100, it) }
        }
        val json = doc.findFile("$name.json") ?: doc.createFile("application/json", "$name.json")
        json?.let { f ->
            contentResolver.openOutputStream(f.uri)?.use {
                it.write(buildJson(name, res, png?.name, f.name).toByteArray())
            }
        }
        appendCsv(doc, name, res, png?.name, json?.name)

        AlertDialog.Builder(this)
            .setTitle("Saved")
            .setMessage(
                "Saved to the work folder:\n\n$name.png  (rings marked)\n" +
                    "$name.json  (ring positions)\ncounts.csv  (updated)\n\n" +
                    "Rings ${primaryCount(res)}, confidence +/- ${res.confidence}." +
                    if (res.resolved) "" else "\nStatus: NOT RESOLVED - check the marks.",
            )
            .setPositiveButton(android.R.string.ok) { _, _ -> finish() }
            .show()
    }

    private fun renderProof(bmp: Bitmap, name: String): Bitmap {
        val res = result ?: return bmp
        val out = bmp.copy(Bitmap.Config.ARGB_8888, true)
        val c = Canvas(out)
        val p = Paint().apply {
            color = Color.rgb(255, 40, 40); style = Paint.Style.STROKE
            strokeWidth = maxOf(2f, bmp.width / 900f)
        }
        val s = startPt ?: return out
        val e = endPt ?: return out
        val ax = s.first; val ay = s.second
        val bx = e.first; val by = e.second
        val ux = (bx - ax) / hypot(bx - ax, by - ay)
        val uy = (by - ay) / hypot(bx - ax, by - ay)
        val half = maxOf(12f, bmp.height * 0.05f)
        // Draw the chain's boundaries when it ran: those are the rings the
        // record counts, so the picture must show the same thing.
        val chain = chainResult
        if (chain != null && chain.count >= 2) {
            chain.positions.forEach { pos ->
                val t = pos.toFloat()
                val cx = ax + ux * t
                val cy = ay + uy * t
                val nx = -uy * half; val ny = ux * half
                c.drawLine(cx - nx, cy - ny, cx + nx, cy + ny, p)
            }
            return out
        }
        res.bands.forEach { b ->
            val t0 = b.startPx.toFloat(); val t1 = b.endPx.toFloat()
            val corners = listOf(
                ax + ux * t0 - uy * half to ay + uy * t0 + ux * half,
                ax + ux * t1 - uy * half to ay + uy * t0 + ux * half,
                ax + ux * t1 + uy * half to ay + uy * t1 - ux * half,
                ax + ux * t0 + uy * half to ay + uy * t1 - ux * half,
            )
            val path = android.graphics.Path().apply {
                moveTo(corners[0].first, corners[0].second)
                for (i in 1..3) lineTo(corners[i].first, corners[i].second)
                close()
            }
            c.drawPath(path, p)
        }
        return out
    }

    private fun buildJson(name: String, res: Detector.Result, png: String?, json: String?): String {
        val sb = StringBuilder()
        sb.append("{\n  \"schema\": \"agecore.count/1\",\n")
        sb.append("  \"age_name\": \"$name\",\n")
        sb.append("  \"ring_count\": ${primaryCount(res)},\n")
        sb.append("  \"chain_count\": ${chainResult?.count ?: "null"},\n")
        sb.append("  \"ladder_count\": ${res.count},\n")
        sb.append("  \"confidence_plus_minus\": ${res.confidence ?: "null"},\n")
        sb.append("  \"resolved\": ${res.resolved},\n")
        sb.append("  \"count_type\": \"${if (partialCount) "partial" else "whole"}\",\n")
        sb.append("  \"pith_at_start\": $pithAtStart,\n")
        sb.append("  \"intervals\": [\n")
        intervals.forEachIndexed { i, iv ->
            sb.append("    {\"i\": ${i + 1}, \"start_px\": ${iv.startPx}, " +
                "\"end_px\": ${iv.endPx}, \"count\": ${iv.count}, " +
                "\"quality\": \"${iv.quality}\"}")
            if (i < intervals.lastIndex) sb.append(",")
            sb.append("\n")
        }
        sb.append("  ],\n")
        sb.append("  \"px_per_mm\": ${res.pxPerMm},\n")
        sb.append("  \"spacing_mm\": ${res.spacingMm},\n")
        sb.append("  \"ladder_counts\": ${res.ladderCounts},\n")
        sb.append("  \"warnings\": [")
        res.warnings.forEachIndexed { i, w -> if (i > 0) sb.append(", "); sb.append("\"$w\"") }
        sb.append("],\n")
        sb.append("  \"proof_image\": \"${png ?: ""}\",\n")
        sb.append("  \"boundaries_px\": ${chainResult?.positions?.joinToString(",") ?: "[]"},\n")
        sb.append("  \"rings\": [\n")
        res.bands.forEachIndexed { i, b ->
            sb.append("    {\"i\": ${i + 1}, \"start_px\": ${b.startPx}, \"end_px\": ${b.endPx}, ")
            sb.append("\"width_mm\": ${b.widthMm}}")
            if (i < res.bands.lastIndex) sb.append(",")
            sb.append("\n")
        }
        sb.append("  ]\n}\n")
        return sb.toString()
    }

    private fun appendCsv(doc: DocumentFile, name: String, res: Detector.Result,
                          png: String?, json: String?) {
        val cols = listOf(
            "Age_Name", "Ring_Count", "Confidence_+/-(yrs)", "Status", "Photo",
            "Record_JSON", "Spacing_mm", "Px_per_mm", "Ladder_Counts",
            "Count_Type", "Pith_At_Start", "Poor_Intervals", "Recorded_UTC",
            "Chain_Count", "Ladder_Count",
        )
        val row = listOf(
            name.take(17), primaryCount(res).toString(), (res.confidence ?: "").toString(),
            if (res.resolved) "counted" else "unresolved", png ?: "", json ?: "",
            "%.4f".format(res.spacingMm), "%.2f".format(res.pxPerMm),
            res.ladderCounts.joinToString(" "),
            if (partialCount) "partial" else "whole", pithAtStart.toString(),
            intervals.count { it.quality == "poor" }.toString(),
            java.time.Instant.now().toString(),
            (chainResult?.count ?: "").toString(),
            res.count.toString(),
        )
        val f = doc.findFile("counts.csv") ?: doc.createFile("text/csv", "counts.csv")
            ?: return
        val existing = contentResolver.openInputStream(f.uri)?.bufferedReader()?.readText()
        val text = StringBuilder()
        if (existing.isNullOrBlank()) text.append(cols.joinToString(",")).append("\n")
        text.append(row.joinToString(",")).append("\n")
        val mode = if (existing.isNullOrBlank()) "wt" else "wa"
        contentResolver.openOutputStream(f.uri, mode)?.use { it.write(text.toString().toByteArray()) }
    }

}
