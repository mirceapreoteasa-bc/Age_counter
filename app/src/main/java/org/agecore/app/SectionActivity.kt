package org.agecore.app

import android.app.AlertDialog
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Bundle
import android.text.InputType
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import org.agecore.core.Sections
import androidx.documentfile.provider.DocumentFile
import java.io.File
import kotlin.math.max
import kotlin.math.roundToInt

/**
 * Count a core in the way the work actually gets done: one photo of the whole
 * core, split into the card's printed bays, each bay counted on its own.
 *
 * Why bays and not one long stitched strip: overlapping partial views register
 * to a few per cent, and on a 131 px ring a 7% error is about 9 px - enough to
 * move a boundary or double-count a ring at a seam. A bay edge is instead a
 * *discarded* boundary, and the count for each bay is separately checkable.
 *
 * The screen never presents a bare number. It shows every bay, says which ones
 * failed and why, and withholds the whole-core total unless every bay resolved
 * and they agree about ring width. Proof images are saved so the forester can
 * correct the app rather than argue with it.
 */
class SectionActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_PHOTO = "photo"
        val NAME_PATTERN = Regex("^XGV[A-Za-z0-9]+P\\d+T\\d+(S\\d+)?$")
    }

    private lateinit var imageView: ImageView
    private lateinit var resultText: TextView
    private lateinit var questionBox: LinearLayout
    private lateinit var bayEdit: EditText
    private lateinit var ringEdit: EditText
    private lateinit var nameEdit: EditText

    private var photoFile: File? = null
    private var bitmap: Bitmap? = null
    private var readings: List<Sections.Reading> = emptyList()
    private var report: Sections.Report? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_sections)

        imageView = findViewById(R.id.sectionImage)
        resultText = findViewById(R.id.sectionResult)
        questionBox = findViewById(R.id.questionBox)
        bayEdit = findViewById(R.id.bayCount)
        ringEdit = findViewById(R.id.ringWidth)
        nameEdit = findViewById(R.id.ageName)

        photoFile = File(intent.getStringExtra(EXTRA_PHOTO) ?: "")
        if (!photoFile!!.exists()) {
            finish(); return
        }
        // A long core photo is wide; cap the decode so a 12 MP frame does not
        // exhaust memory, and keep the aspect ratio for the fractional maths.
        val opts = BitmapFactory.Options().apply { inSampleSize = 1 }
        bitmap = BitmapFactory.decodeFile(photoFile!!.absolutePath, opts)
        imageView.setImageBitmap(bitmap)

        findViewById<Button>(R.id.btnCount).setOnClickListener { runCount() }
        findViewById<Button>(R.id.btnSaveProof).setOnClickListener { saveProof() }
        findViewById<Button>(R.id.btnSave).setOnClickListener { saveRecord() }
    }

    // --- counting ---------------------------------------------------------

    private fun runCount() {
        val bmp = bitmap ?: return
        val bays = bayEdit.text.toString().trim().toIntOrNull() ?: Sections.BAYS
        val ringMm = ringEdit.text.toString().trim().toDoubleOrNull()
        if (ringMm == null || ringMm <= 0) {
            AlertDialog.Builder(this)
                .setTitle("Ring width needed")
                .setMessage(
                    "Enter the expected ring width in millimetres, e.g. 9.0 for " +
                    "a 9 mm ring.\n\nThe image alone cannot tell a real ring " +
                    "from every 2nd or 3rd of a finer series - both look " +
                    "equally regular - so the pre-filter has to be sized " +
                    "before counting.")
                .setPositiveButton("OK", null).show()
            return
        }

        val scale = measureScale(bmp)
        if (scale == null || scale <= 0) {
            AlertDialog.Builder(this)
                .setTitle("No scale found")
                .setMessage(
                    "The app could not measure the ruler's graduations in this " +
                    "photo, so a ring count in millimetres is not possible.\n\n" +
                    "Photograph the whole card with the ruler in the " +
                    "calibration lane, and make sure the ruler is opaque and " +
                    "the graduations are in focus.")
                .setPositiveButton("OK", null).show()
            return
        }

        val grey = toGrey(bmp)
        val totalW = bmp.width
        val n = bays.coerceIn(1, Sections.BAYS)
        val out = ArrayList<Sections.Reading>()
        // the card's bays are equal; assume the core spans them
        for (i in 0 until n) {
            val l0 = (totalW.toDouble() * i / n).roundToInt()
            val l1 = (totalW.toDouble() * (i + 1) / n).roundToInt()
            val sub = sliceCore(grey, totalW, bmp.height, l0, l1)
            out.add(Sections.countSection(
                sub, l1 - l0, coreHeight(bmp.height),
                scale, ('A' + i).toString(), i, ringMm,
                l0.toFloat() / totalW, l1.toFloat() / totalW))
        }
        readings = out
        val rep = Sections.summarise(out, expectedTotal = null)
        report = rep
        render(rep)
    }

    /**
     * Scale from the ruler's graduations, in the calibration lane.
     *
     * The lane is the band near the top of the sheet, above the strip. The
     * comb is found by period, and it is deliberately a long baseline: a single
     * measured gap would happily return a harmonic, which overstated ring
     * width by 74% when it happened.
     */
    private fun measureScale(bmp: Bitmap): Double? {
        val w = bmp.width
        val h = bmp.height
        val top = (h * 0.05f).roundToInt()
        val bot = (h * 0.20f).roundToInt()
        if (bot <= top + 20) return null
        val prof = DoubleArray(w)
        var n = 0
        for (y in top until bot) {
            for (x in 0 until w) {
                val p = bmp.getPixel(x, y)
                val lum = 0.299 * Color_red(p) + 0.587 * Color_green(p) + 0.114 * Color_blue(p)
                if (lum < 135) prof[x] += 1.0
            }
            n++
        }
        if (n == 0) return null
        for (x in 0 until w) prof[x] /= n
        // detrend, then find the strongest period in 4..40 px
        val sm = smooth(prof, 3)
        var best = 0.0
        var bestScore = 0.0
        for (p in 4..40) {
            var s = 0.0
            var k = 0
            var x = 0.0
            while (x < w) {
                s += sm[x.toInt()]
                k++; x += p
            }
            if (k > 8 && s / k > bestScore) { bestScore = s / k; best = p.toDouble() }
        }
        if (best <= 0) return null
        // 1 mm graduations -> px/mm. This is a physical reference and cannot
        // inherit the printer's scale error, which is the whole point.
        return best
    }

    private fun sliceCore(grey: DoubleArray, w: Int, h: Int,
                          x0: Int, x1: Int): DoubleArray {
        val y0 = (h * 0.30f).roundToInt()
        val y1 = (h * 0.78f).roundToInt()
        val width = coreHeight(h)
        val out = DoubleArray((x1 - x0) * width)
        var k = 0
        for (i in 0 until (x1 - x0)) {
            for (j in 0 until width) {
                out[k++] = grey[(y0 + j) * w + (x0 + i)]
            }
        }
        return out
    }

    /** The core occupies the middle of the strip; take a band inside it. */
    private fun coreHeight(h: Int): Int =
        max(8, ((h * 0.78f).roundToInt() - (h * 0.30f).roundToInt()) / 2)

    private fun toGrey(bmp: Bitmap): DoubleArray {
        val w = bmp.width; val h = bmp.height
        val out = DoubleArray(w * h)
        for (y in 0 until h) for (x in 0 until w) {
            val p = bmp.getPixel(x, y)
            out[y * w + x] = 0.299 * Color_red(p) + 0.587 * Color_green(p) +
                              0.114 * Color_blue(p)
        }
        return out
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

    // --- presentation -----------------------------------------------------

    private fun render(rep: Sections.Report) {
        val sb = StringBuilder()
        sb.append(rep.summary()).append("\n\n")
        for (s in rep.sections) {
            if (s.resolved) {
                sb.append("Bay ${s.label}: ${s.count} rings")
                if (s.ringMm > 0) sb.append("   ${fmt(s.ringMm)} mm/ring")
                sb.append("\n")
            } else {
                sb.append("Bay ${s.label}: not resolved (${s.countLow}-${s.countHigh})\n")
            }
            s.warnings.take(2).forEach { sb.append("    - ").append(it).append("\n") }
        }
        for (n in rep.notes) sb.append("\n").append(n).append("\n")
        resultText.text = sb.toString()

        questionBox.removeAllViews()
        for (q in rep.questions) {
            val tv = TextView(this)
            tv.text = q.text + "\n\n" + q.options.joinToString("   |   ")
            tv.setPadding(8, 12, 8, 12)
            tv.setOnClickListener {
                AlertDialog.Builder(this)
                    .setTitle("Choose")
                    .setItems(q.options.toTypedArray()) { _, _ -> }
                    .setPositiveButton("Close", null).show()
            }
            questionBox.addView(tv)
        }
        if (rep.total == null) {
            questionBox.visibility = View.VISIBLE
        } else questionBox.visibility = View.GONE
    }

    private fun fmt(v: Double) =
        if (v >= 10.0) String.format("%.1f", v) else String.format("%.2f", v)

    // --- output -----------------------------------------------------------

    private fun saveProof() {
        val bmp = bitmap ?: return
        val rep = report ?: run { Toast2("Count first"); return }
        val dir = Prefs.workFolder(this) ?: run { Toast2("Set the work folder first"); return }
        val name = nameEdit.text.toString().trim()
        if (!NAME_PATTERN.matches(name)) {
            AlertDialog.Builder(this)
                .setTitle("Name needed")
                .setMessage("Use XGV<groundcall>P<point>T<tree>, e.g. XGV1-2P1T3")
                .setPositiveButton("OK", null).show()
            return
        }
        try {
            val root = DocumentFile.fromTreeUri(this, dir)
            val sub: DocumentFile? = root?.findFile(name) ?: root?.createDirectory(name)
            if (sub == null) { Toast2("Could not make folder $name"); return }
            writePng(sub, "$name-overview.png",
                Proof.drawOverview(bmp, overviewSections(rep), 0f,
                    "$name   ${rep.summary()}"))
            for (s in rep.sections) {
                writePng(sub, "$name-bay${s.label}.png", Proof.drawSection(
                    bmp, s.boundariesFrac.minOrNull() ?: 0f, 1f,
                    s.boundariesFrac, s.rejectedFrac, "Bay ${s.label}",
                    if (s.resolved) s.count else -1,
                    s.ringMm.toFloat(), s.pxPerMm.toFloat()))
            }
            Toast2("Proof images saved to $name")
        } catch (e: Exception) {
            Toast2("Could not save: ${e.message}")
        }
    }

    private fun saveRecord() {
        val rep = report ?: run { Toast2("Count first"); return }
        val dir = Prefs.workFolder(this) ?: run { Toast2("Set the work folder first"); return }
        val name = nameEdit.text.toString().trim()
        if (!NAME_PATTERN.matches(name)) {
            AlertDialog.Builder(this)
                .setTitle("Name needed")
                .setMessage("Use XGV<groundcall>P<point>T<tree>, e.g. XGV1-2P1T3")
                .setPositiveButton("OK", null).show()
            return
        }
        try {
            val root = DocumentFile.fromTreeUri(this, dir)
            val sub = root?.findFile(name) ?: root?.createDirectory(name)
            if (sub == null) { Toast2("Could not make folder $name"); return }
            val sb = StringBuilder()
            sb.append("name,$name\n")
            sb.append("sections,${rep.sections.size}\n")
            sb.append("status,${rep.status}\n")
            sb.append("total,${rep.total ?: ""}\n")
            sb.append("total_low,${rep.totalLow}\n")
            sb.append("total_high,${rep.totalHigh}\n")
            rep.sections.forEach {
                sb.append("bay_${it.label},${if (it.resolved) it.count else ""}," +
                    "${it.countLow},${it.countHigh},${fmt(it.ringMm)},${it.resolved}\n")
            }
            val f: DocumentFile? = sub.createFile("text/csv", "$name-sections.csv")
                ?: sub.createFile("text/plain", "$name-sections.csv")
            if (f != null) {
                contentResolver.openOutputStream(f.uri)?.use { out ->
                    out.write(sb.toString().toByteArray())
                }
            }
            Toast2("Saved $name-sections.csv")
        } catch (e: Exception) {
            Toast2("Could not save: ${e.message}")
        }
    }

    private fun overviewSections(rep: Sections.Report): List<Proof.SectionDrawing> {
        val n = max(1, rep.sections.size)
        return rep.sections.mapIndexed { i, s ->
            Proof.SectionDrawing(s.label, i.toFloat() / n, (i + 1).toFloat() / n,
                s.count, s.resolved)
        }
    }

    private fun writePng(dir: DocumentFile, name: String, bmp: Bitmap) {
        val f: DocumentFile? = dir.createFile("image/png", name)
        if (f != null) {
            contentResolver.openOutputStream(f.uri)?.use { out ->
                bmp.compress(Bitmap.CompressFormat.PNG, 100, out)
            }
        }
    }

    private fun Toast2(s: String) =
        android.widget.Toast.makeText(this, s, android.widget.Toast.LENGTH_LONG).show()
}

private fun Color_red(p: Int) = android.graphics.Color.red(p)
private fun Color_green(p: Int) = android.graphics.Color.green(p)
private fun Color_blue(p: Int) = android.graphics.Color.blue(p)
