package org.agecore.app

import android.content.Context
import android.net.Uri
import java.io.File

/** Small key/value store for the chosen work folder and the in-flight capture. */
object Prefs {
    private const val FILE = "agecore_prefs"
    private const val KEY_FOLDER = "work_folder"
    private const val KEY_PENDING = "pending_photo"

    fun workFolder(ctx: Context): Uri? =
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .getString(KEY_FOLDER, null)?.let(Uri::parse)

    fun setWorkFolder(ctx: Context, uri: Uri?) {
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit()
            .apply {
                if (uri == null) remove(KEY_FOLDER) else putString(KEY_FOLDER, uri.toString())
            }
            .apply()
    }

    fun pendingPhoto(ctx: Context): File? =
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .getString(KEY_PENDING, null)?.let(::File)

    fun setPendingPhoto(ctx: Context, path: String?) {
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit()
            .apply {
                if (path == null) remove(KEY_PENDING) else putString(KEY_PENDING, path)
            }
            .apply()
    }
}
