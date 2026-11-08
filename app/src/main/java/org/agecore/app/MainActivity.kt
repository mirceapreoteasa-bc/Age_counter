package org.agecore.app

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import java.io.File

/**
 * Main page.
 *
 *   Start counting  -> capture a photo, then hand it to CountActivity
 *   Settings        -> choose the work folder (Documents/AGE_COUNT) once
 *   Exit            -> close the app
 *
 * The work folder is a SAF tree URI chosen by the user, so no broad storage
 * permission is needed. Capture is delegated to the system camera, so no CAMERA
 * permission is needed either.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var folderLabel: TextView

    private val pickFolder =
        registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
            if (uri != null) {
                contentResolver.takePersistableUriPermission(
                    uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION or
                        Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
                )
                Prefs.setWorkFolder(this, uri)
                showFolder()
            }
        }

    private val takePhoto =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { res ->
            val photo = Prefs.pendingPhoto(this)
            if (res.resultCode == RESULT_OK && photo != null && photo.exists()) {
                startActivity(
                    Intent(this, SectionActivity::class.java)
                        .putExtra(SectionActivity.EXTRA_PHOTO, photo.absolutePath),
                )
            } else {
                photo?.delete()
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        folderLabel = findViewById(R.id.folderLabel)
        showFolder()

        findViewById<Button>(R.id.btnStart).setOnClickListener { capture() }
        findViewById<Button>(R.id.btnSettings).setOnClickListener { pickFolder.launch(null) }
        findViewById<Button>(R.id.btnExit).setOnClickListener { finishAffinity() }
    }

    override fun onResume() {
        super.onResume()
        showFolder()
    }

    private fun showFolder() {
        val uri = Prefs.workFolder(this)
        folderLabel.text = if (uri == null) {
            getString(R.string.no_folder_set)
        } else {
            val doc = androidx.documentfile.provider.DocumentFile.fromTreeUri(this, uri)
            val name = doc?.name ?: uri.toString()
            "${getString(R.string.work_folder)}:\n$name"
        }
    }

    private fun capture() {
        val dir = File(cacheDir, "captures").apply { mkdirs() }
        val photo = File(dir, "cap_${System.currentTimeMillis()}.jpg")
        Prefs.setPendingPhoto(this, photo.absolutePath)

        val uri: Uri = FileProvider.getUriForFile(
            this, "$packageName.fileprovider", photo,
        )
        val intent = Intent(android.provider.MediaStore.ACTION_IMAGE_CAPTURE)
            .putExtra(android.provider.MediaStore.EXTRA_OUTPUT, uri)
            .addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
        if (intent.resolveActivity(packageManager) != null) {
            takePhoto.launch(intent)
        } else {
            Prefs.setPendingPhoto(this, null)
            photo.delete()
            android.widget.Toast.makeText(
                this, "No camera app available on this device", android.widget.Toast.LENGTH_LONG,
            ).show()
        }
    }
}
