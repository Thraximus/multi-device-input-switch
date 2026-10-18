package online.thrax.switcher.core

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import android.util.Base64
import android.util.Log
import online.thrax.switcher.R
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest

/**
 * Updates over Wi-Fi from the hub, like the Windows agent's: on connect, ask for the android manifest,
 * download a newer APK in chunks over the (pinned, mutual-TLS) hub connection, check its SHA-256, and
 * hand it to Android's PackageInstaller.
 *
 * Trust: Android installs an update only if it's signed with the same key as the installed app, so a
 * forged APK can't get in even if the transfer were compromised; the hub's Ed25519 manifest signature
 * (which Android 11 can't verify without a library) would add nothing on top of that.
 *
 * Android 12+ installs silently when this app is its own installer of record (the hub's USB install sets
 * that, and so does the first update); otherwise Android asks, and we post "tap to install".
 */
class Updater(private val ctx: Context, private val request: (JSONObject) -> JSONObject?) {
    fun check() {
        val reply = request(JSONObject().put("type", "get_update").put("platform", "android")) ?: return
        if (reply.optString("type") != "update") return  // no Android build on the hub
        val manifest = JSONObject(reply.getString("manifest"))
        val remote = manifest.optString("version")
        val local = runCatching { ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName }.getOrNull() ?: "0"
        if (manifest.optString("platform") != "android" || !newer(remote, local)) return
        val files = manifest.getJSONArray("files")
        val entry = (0 until files.length()).map { files.getJSONObject(it) }.firstOrNull { it.optString("path") == APK }
            ?: return
        App.state.value = App.state.value.copy(update = "Downloading update $remote…")
        val apk = download(entry) ?: run {
            App.state.value = App.state.value.copy(update = "Update $remote failed to download")
            return
        }
        App.state.value = App.state.value.copy(update = "Installing update $remote…")
        install(apk)
    }

    private fun download(entry: JSONObject): File? {
        val size = entry.getLong("size")
        val dir = File(ctx.cacheDir, "update").apply { mkdirs() }
        val file = File(dir, APK)
        val sha = MessageDigest.getInstance("SHA-256")
        file.outputStream().use { out ->
            var offset = 0L
            while (offset < size) {
                val chunk = request(JSONObject().put("type", "get_file").put("platform", "android")
                    .put("path", APK).put("offset", offset)) ?: return null
                val data = Base64.decode(chunk.optString("data"), Base64.DEFAULT)
                if (data.isEmpty()) return null
                out.write(data)
                sha.update(data)
                offset += data.size
            }
        }
        val hex = sha.digest().joinToString("") { "%02x".format(it) }
        if (hex != entry.getString("sha256")) {
            Log.w(TAG, "update failed its checksum")
            file.delete()
            return null
        }
        return file
    }

    private fun install(apk: File) {
        val installer = ctx.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(ctx.packageName)
            if (Build.VERSION.SDK_INT >= 31) setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }
        val id = installer.createSession(params)
        installer.openSession(id).use { session ->
            session.openWrite(APK, 0, apk.length()).use { out ->
                apk.inputStream().use { it.copyTo(out) }
                session.fsync(out)
            }
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
            val status = PendingIntent.getBroadcast(ctx, 0, Intent(ctx, UpdateReceiver::class.java), flags)
            session.commit(status.intentSender)
        }
        apk.delete()
    }

    companion object {
        const val TAG = "SwitcherUpdate"
        const val APK = "switcher.apk"
        private const val CHANNEL = "updates"
        private const val NOTE = 3

        fun newer(remote: String, local: String): Boolean {
            fun parts(v: String) = v.split(".").map { it.toIntOrNull() ?: 0 }
            val r = parts(remote)
            val l = parts(local)
            for (i in 0 until maxOf(r.size, l.size)) {
                val a = r.getOrElse(i) { 0 }
                val b = l.getOrElse(i) { 0 }
                if (a != b) return a > b
            }
            return false
        }

        /** Android wants the user to confirm: offer it as a notification (we're likely in the background). */
        fun askToConfirm(ctx: Context, confirm: Intent) {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Updates", NotificationManager.IMPORTANCE_DEFAULT))
            val tap = PendingIntent.getActivity(ctx, 3, confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            nm.notify(NOTE, Notification.Builder(ctx, CHANNEL)
                .setSmallIcon(R.drawable.ic_switcher)
                .setContentTitle("Switcher update ready")
                .setContentText("Tap to install the new version from the hub")
                .setContentIntent(tap)
                .setAutoCancel(true)
                .build())
            App.state.value = App.state.value.copy(update = "Update ready — tap the notification to install")
        }
    }
}

/** PackageInstaller's verdict on an update session. */
class UpdateReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        when (val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT) ?: return
                Updater.askToConfirm(ctx, confirm)
            }
            PackageInstaller.STATUS_SUCCESS -> {}  // we're replaced; MY_PACKAGE_REPLACED restarts the service
            else -> {
                val why = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "status $status"
                Log.w(Updater.TAG, "update not installed: $why")
                App.state.value = App.state.value.copy(update = "Update not installed: $why")
            }
        }
    }
}
