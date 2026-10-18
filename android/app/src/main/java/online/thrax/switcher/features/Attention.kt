package online.thrax.switcher.features

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import online.thrax.switcher.MainActivity
import online.thrax.switcher.R

/** Notifications that ask the user to open the app and fix something. */
object Attention {
    private const val CHANNEL = "attention"
    private const val LOG_ACCESS = 2

    fun logAccess(ctx: Context, ok: Boolean) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        if (ok) {
            nm.cancel(LOG_ACCESS)
            return
        }
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Needs attention", NotificationManager.IMPORTANCE_DEFAULT))
        val open = PendingIntent.getActivity(ctx, 1, Intent(ctx, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        nm.notify(
            LOG_ACCESS,
            Notification.Builder(ctx, CHANNEL)
                .setSmallIcon(R.drawable.ic_switcher)
                .setContentTitle("Copies on this device aren't synced")
                .setContentText("Tap, then allow log access, to turn copy detection back on")
                .setContentIntent(open)
                .setAutoCancel(true)
                .build(),
        )
    }
}
