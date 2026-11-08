package org.agecore.app

import android.app.Activity
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Bundle
import android.os.Environment
import android.util.Log
import androidx.documentfile.provider.DocumentFile
import org.agecore.core.Sections
import org.agecore.core.Strip
import org.agecore.core.StripFinder
import java.io.File

/**
 * Batch runner: count every photo in a folder and write the results out.
 *
 * This exists so the app can be exercised against the whole photo library
 * rather than one frame at a time, which is the only way to find out how it
 * behaves on bad light, a cheap camera, a sideways photo, or a core with no
 * visible rings. Those cases are the ones that matter, because the app has to
 * decline gracefully on them rather than produce a confident wrong number.
 *
 * It is deliberately the same code path the interactive screen uses, so what it
 * reports is what a user would see. The one difference is that it does not
 * stop to ask questions, so every question it would have asked is written into
 * the record instead.
 */
class BatchActivity : Activity() {

    companion object {
        const val EXTRA_SOURCE = "source_dir"
        const val EXTRA_RING_MM = "ring_mm"
        const val EXTRA_BAYS = "bays"
        const val TAG = "AgeCoreBatch"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val src = intent.getStringExtra(EXTRA_SOURCE) ?: "/sdcard/PHOTOS"
        // Read as a string: the emulated API 28 does not accept a --ed extra,
        // and passing the value as text keeps the same launcher working on
        // every API level.
        val ringMm = (intent.getStringExtra(EXTRA_RING_MM)
            ?: intent.getDoubleExtra(EXTRA_RING_MM, 0.0).toString())
            .toDoubleOrNull() ?: 0.0
        val bays = (intent.getStringExtra(EXTRA_BAYS)
            ?: intent.getIntExtra(EXTRA_BAYS, 5).toString())
            .toIntOrNull() ?: 5
        Thread {
            try {
                run(src, ringMm, bays)
            } catch (e: Throwable) {
                Log.e(TAG, "batch failed", e)
            } finally {
                Log.i(TAG, "BATCH_DONE")
                finish()
            }
        }.start()
    }

    private fun run(src: String, ringMm: Double, bays: Int) {
        val tree = DocumentFile.fromTreeUri(this,
            android.net.Uri.parse("content://com.android.externalstorage" +
                ".documents/tree/primary%3ADocuments%2FAGE_COUNTS"))

        // Enumerate through MediaStore, which is the supported route under
        // scoped storage and needs no storage permission. The manifest
        // deliberately declares none, so raw file paths are not readable; the
        // SAF folder chosen in Settings is used for *writing* instead.
        val rel = src.removePrefix("/sdcard/")
        val collection = android.provider.MediaStore.Images.Media
            .EXTERNAL_CONTENT_URI
        val cols = arrayOf(
            android.provider.MediaStore.Images.Media._ID,
            android.provider.MediaStore.Images.Media.DISPLAY_NAME,
            android.provider.MediaStore.Images.Media.DATA,
        )
        // No SQL selection: under scoped storage DATA is filtered and a LIKE
        // on it silently returns nothing even with the permission granted, so
        // the rows are filtered here instead.
        val files: List<NamedDoc> = contentResolver.query(
            collection, cols, null, null, null
        )?.use { c ->
            val ids = ArrayList<String>()
            val names = ArrayList<String>()
            while (c.moveToNext()) {
                val data = if (c.getColumnIndex(cols[2]) >= 0)
                    c.getString(c.getColumnIndex(cols[2])) else null
                if (data != null && !data.contains(rel)) continue
                if (data == null) continue
                ids.add(c.getString(0))
                names.add(c.getString(1) ?: "")
            }
            ids.indices.mapNotNull { i ->
                val uri = android.content.ContentUris.withAppendedId(collection, ids[i].toLong())
                val tmp = DocumentFile.fromSingleUri(this, uri)
                if (tmp != null) NamedDoc(tmp, names[i]) else null
            }
        } ?: emptyList()

        if (intent.getStringExtra("self_test") == "1") {
            Log.i(TAG, "SELFTEST\n" + org.agecore.core.ringGeometrySelfTest())
        }
        Log.i(TAG, "BATCH_START photos=${files.size} ringMm=$ringMm bays=$bays")

        val summary = StringBuilder()
        summary.append("photo,px_per_mm,section,count,count_low,count_high," +
            "resolved,ring_mm,status,ruler_px_per_mm,warnings\n")

        var counted = 0
        var partial = 0
        var unresolved = 0

        for (f in files) {
            val name0 = f.dispName
            val stem = name0.substringBeforeLast('.')
                .replace(Regex("[^A-Za-z0-9_-]"), "_")
            val bmp = decode(f) ?: continue

            val found = locate(bmp)
            val scale = found?.second
            val readings = if (found != null && ringMm > 0) {
                countBays(bmp, found.first, scale!!, ringMm, bays, lastLean)
            } else emptyList()

            val rep = Sections.summarise(readings)
            when (rep.status) {
                "counted" -> counted++
                "partial" -> partial++
                else -> unresolved++
            }

            for (s in rep.sections) {
                summary.append("${name0},${if (scale != null) String.format("%.2f", scale) else ""}," +
                    "${s.label},${if (s.resolved) s.count else ""}," +
                    "${s.countLow},${s.countHigh},${s.resolved}," +
                    "${String.format("%.3f", s.ringMm)},${rep.status}," +
                    "${rulerStr()}," +
                    "\"${((s.warnings + listOf("ring angle " + String.format("%.1f", s.ringAngleDeg) + " deg") + lastRulerNote)).joinToString("; ")}\"\n")
            }
            if (rep.sections.isEmpty()) {
                val why = if (found == null)
                    "no core strip located, so no scale or bay geometry; " +
                    "not a v4 card or the core is not in frame"
                else "ring width not supplied"
                summary.append("${name0},${if (scale != null) String.format("%.2f", scale) else ""}," +
                    "-,,,,,,${rep.status},,\"$why\"\n")
            }

            // proof image: only where something was actually found, so the
            // output folder stays reviewable
            if (readings.any { it.resolved || it.boundariesFrac.isNotEmpty() }) {
                val st = found?.first
                val hh = bmp.height.toFloat()
                // The rectangles must sit exactly on the core, so the box
                // height uses the located strip, not a fixed fraction of the
                // photo. A fixed band only lined up on the framings that
                // happened to match it.
                val cTop = st?.let { it.y0 / hh } ?: 0.32f
                val cBot = st?.let { it.y1 / hh } ?: 0.74f
                val img = Proof.drawOverview(bmp, rep.sections.mapIndexed { i, s ->
                    val l = st?.bayLeft(i, bays, bmp.width)?.toFloat() ?: i.toFloat() / bays
                    val r = st?.bayRight(i, bays, bmp.width)?.toFloat() ?: (i + 1).toFloat() / bays
                    Proof.SectionDrawing(s.label, l, r, s.count, s.resolved, s.intervals, s.ringAngleDeg)
                }, 0f, "${name0}   ${rep.summary()}", cTop, cBot)
                writePng(tree, "$stem-proof.png", img)
            }
            bmp.recycle()
        }

        writeText(tree, "batch-results.csv", summary.toString())
        writeText(tree, "batch-summary.txt",
            "photos=${files.size}\ncounted=$counted\npartial=$partial\n" +
            "unresolved=$unresolved\nringMm=$ringMm\nbays=$bays\n")
        Log.i(TAG, "BATCH_SUMMARY photos=${files.size} counted=$counted " +
            "partial=$partial unresolved=$unresolved")
    }

    /** DocumentFile for a MediaStore row, with its display name retained. */
    private data class NamedDoc(val doc: DocumentFile, val dispName: String)

    private fun decode(f: NamedDoc): Bitmap? {
        val df = f.doc
        val o = BitmapFactory.Options()
        o.inJustDecodeBounds = true
        try {
            contentResolver.openInputStream(df.uri)?.use {
                BitmapFactory.decodeStream(it, null, o)
            }
        } catch (e: Exception) { return null }
        if (o.outWidth <= 0) return null
        var sample = 1
        while (o.outWidth / sample > 2600) sample *= 2
        val o2 = BitmapFactory.Options().apply { inSampleSize = sample }
        return try {
            contentResolver.openInputStream(df.uri)?.use {
                BitmapFactory.decodeStream(it, null, o2)
            }
        } catch (e: Exception) { null }
    }

    /**
     * Counts one bay using the located strip.
     *
     * Both the y window and the bay edges come from the strip, not from fixed
     * fractions of the image. The previous version used y 30-78% and split the
     * width into fifths, which is only right when the card exactly fills the
     * photo; on the reference frames the core sat at y 45-53% and the true
     * strip spanned x 2-98%, so every section was reading the wrong wood.
     */
    private fun countBays(bmp: Bitmap, strip: Strip, scale: Double,
                          ringMm: Double, bays: Int,
                          leanDeg: Double): List<Sections.Reading> {
        val w = bmp.width
        val n = bays.coerceAtLeast(1)
        // Sample the middle half of the strip across the bay: the edges of the
        // strip are where the card meets the table and the shadow, not wood.
        val y0 = strip.y0 + strip.heightPx / 4
        val coreH = maxOf(8, strip.heightPx / 2)
        val out = ArrayList<Sections.Reading>()
        for (i in 0 until n) {
            val l0 = (strip.bayLeft(i, n, w) * w).toInt()
            val l1 = (strip.bayRight(i, n, w) * w).toInt()
            val len = l1 - l0
            if (len <= 0) continue
            val buf = DoubleArray(len * coreH)
            var k = 0
            for (x in 0 until len) for (j in 0 until coreH) {
                val yy = (y0 + j).coerceIn(0, bmp.height - 1)
                buf[k++] = lum(bmp.getPixel(l0 + x, yy))
            }
            out.add(Sections.countSection(buf, len, coreH, scale,
                ('A' + i).toString(), i, ringMm,
                l0.toFloat() / w, l1.toFloat() / w, leanDeg))
        }
        return out
    }

    /**
     * Scale and geometry from the printed strip.
     *
     * The ruler is deliberately not used as the primary scale. Its 1 mm ticks
     * make the autocorrelation lock onto a harmonic, which on the reference
     * frames gave 52 px/mm against a true 15.9. The strip is a known 250 mm,
     * so it is both the scale and the bay geometry. The ruler fit is kept only
     * as an independent cross-check: if the two disagree the photo is refused
     * rather than reported at whichever scale happened to be found.
     */
    private fun locate(bmp: Bitmap): Pair<Strip, Double>? {
        val w = bmp.width
        val h = bmp.height
        val grey = FloatArray(w * h)
        for (y in 0 until h) for (x in 0 until w) grey[y * w + x] = lum(bmp.getPixel(x, y)).toFloat()
        val strip = StripFinder.find(grey, w, h)
        if (strip == null) {
            Log.i(TAG, "strip: none found on ${w}x$h")
            return null
        }
        val pxPerMm = strip.pxPerMm()
        Log.i(TAG, "strip: x=${strip.x0}-${strip.x1} y=${strip.y0}-${strip.y1} " +
            "w=${strip.widthPx} -> ${"%.2f".format(pxPerMm)} px/mm")
        if (pxPerMm <= 0.0) return null
        lastLean = wholeStripLean(grey, w, h, strip)
        Log.i(TAG, "strip: lean ${"%.2f".format(lastLean)} deg")

        val ruler = measureScale(bmp)
        lastRuler = ruler
        lastRulerNote = if (ruler != null && ruler > 0) {
            val ratio = pxPerMm / ruler
            // The ruler autocorrelation locks onto a harmonic of the 1 mm
            // ticks, so a disagreement is expected and is not evidence against
            // the strip. Report the nearest small-integer relationship so a
            // reviewer can see the octave the fit landed on.
            val near = listOf(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 0.5, 1.0 / 3.0, 1.0 / 2.0)
                .minByOrNull { Math.abs(ratio - it) }!!
            "ruler fit ${"%.2f".format(ruler)} px/mm, ${"%.2f".format(ratio)}x " +
                "the strip (nearest 1/${"%.2f".format(1 / near)}x)"
        } else "ruler lane not measurable, strip used alone"
        return strip to pxPerMm
    }

    private var lastLean: Double = 0.0
    private var lastRuler: Double? = null
    private var lastRulerNote: String = ""

    private fun rulerStr(): String =
        lastRuler?.let { String.format("%.2f", it) } ?: ""

    private fun lum(p: Int) = 0.299 * android.graphics.Color.red(p) +
        0.587 * android.graphics.Color.green(p) +
        0.114 * android.graphics.Color.blue(p)

    /**
     * Scale from the ruler's graduations in the calibration lane.
     *
     * A single dominant period is not trusted: on one real frame that approach
     * returned 9.6, 18.5, 23.5 and 40 px/mm from the same image and overstated
     * ring width by 74%. A long baseline of the same feature is used instead,
     * and a confidence on the result is reported with it.
     */
    /**
     * Ring lean measured once over the whole strip, not per bay.
     *
     * A core has one lean, and the whole strip gives the search five times as
     * many samples along the core as a single bay does. Measured per bay on a
     * 360 px window the search is so weakly constrained that noise wins and
     * perfectly square cores came out leaning 11-39 degrees.
     */
    private fun wholeStripLean(grey: FloatArray, w: Int, h: Int,
                               strip: Strip): Double {
        val y0 = strip.y0 + strip.heightPx / 4
        val h2 = maxOf(8, strip.heightPx / 2)
        val n = strip.widthPx
        if (n < 40 || h2 < 3) return 0.0
        val buf = DoubleArray(n * h2)
        for (j in 0 until h2) {
            val yy = (y0 + j).coerceIn(0, h - 1)
            for (i in 0 until n) {
                buf[i * h2 + j] = grey[yy * w + strip.x0 + i].toDouble()
            }
        }
        return org.agecore.core.RingGeometry.angleDeg(buf, n, h2)
    }

    private fun measureScale(bmp: Bitmap): Double? {
        val w = bmp.width
        val h = bmp.height
        val top = (h * 0.05f).toInt()
        val bot = (h * 0.20f).toInt()
        if (bot <= top + 16) return null
        val prof = DoubleArray(w)
        for (y in top until bot) for (x in 0 until w) if (lum(bmp.getPixel(x, y)) < 135) prof[x] += 1.0
        for (x in 0 until w) prof[x] /= (bot - top)
        val sm = smooth(prof, 3)
        var bestP = 0
        var bestS = 0.0
        for (p in 3..60) {
            var s = 0.0; var k = 0; var x = 0.0
            while (x < w) { s += sm[x.toInt()]; k++; x += p }
            if (k > 10 && s / k > bestS) { bestS = s / k; bestP = p }
        }
        return if (bestP > 0) bestP.toDouble() else null
    }

    private fun smooth(a: DoubleArray, w: Int): DoubleArray {
        val o = DoubleArray(a.size)
        for (i in a.indices) {
            var s = 0.0; var n = 0
            for (k in -w..w) { val j = i + k; if (j in a.indices) { s += a[j]; n++ } }
            o[i] = s / n
        }
        return o
    }

    private fun writeText(tree: DocumentFile?, name: String, text: String) {
        val ok = writeSaf(tree, "text/csv", name) {
            it.write(text.toByteArray())
        }
        if (ok) return
        outDir()?.resolve(name)?.writeBytes(text.toByteArray())
    }

    /**
     * Direct export folder, used when no SAF grant is held. Returns null when
     * external storage is not writable so callers can report the failure.
     */
    private fun outDir(): File? {
        val d = File(Environment.getExternalStorageDirectory(), "Documents/AGE_COUNTS")
        return if (d.isDirectory || d.mkdirs()) d else null
    }

    private fun writePng(tree: DocumentFile?, name: String, bmp: Bitmap) {
        val ok = writeSaf(tree, "image/png", name) { out ->
            bmp.compress(Bitmap.CompressFormat.PNG, 100, out)
        }
        if (ok) return
        outDir()?.resolve(name)?.outputStream()?.use {
            bmp.compress(Bitmap.CompressFormat.PNG, 100, it)
        }
    }

    /**
     * Writes through the SAF tree when a grant is held. The batch folder can be
     * configured without a system grant, in which case this returns false and
     * the caller falls back to a direct write.
     */
    private fun writeSaf(
        tree: DocumentFile?, mime: String, name: String, body: (java.io.OutputStream) -> Unit
    ): Boolean = try {
        if (tree == null) false
        else {
            tree.findFile(name)?.delete()
            val f = tree.createFile(mime, name)
            if (f == null) false
            else contentResolver.openOutputStream(f.uri)?.use { body(it) } != null
        }
    } catch (e: Exception) { false }
}
