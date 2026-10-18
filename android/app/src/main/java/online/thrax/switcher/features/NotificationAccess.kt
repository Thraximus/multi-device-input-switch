package online.thrax.switcher.features

import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.provider.Settings
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification

/**
 * The one notification listener, shared by the features that read notifications (notifications, calls).
 * Its component is enabled only while at least one of them runs, so with all of them off Android doesn't
 * bind it at all and the app sees no notifications.
 */
object NotificationAccess {
    @Volatile var listener: NotificationListener? = null
    private val users = mutableSetOf<String>()

    @Synchronized
    fun use(ctx: Context, feature: String, on: Boolean) {
        val before = users.isNotEmpty()
        if (on) users += feature else users -= feature
        if (before == users.isNotEmpty()) {
            if (on) listener?.let { l -> runCatching { l.activeNotifications }.getOrNull()?.forEach { dispatch(l, it, true) } }
            return
        }
        val component = ComponentName(ctx, NotificationListener::class.java)
        ctx.packageManager.setComponentEnabledSetting(
            component,
            if (users.isNotEmpty()) PackageManager.COMPONENT_ENABLED_STATE_ENABLED
            else PackageManager.COMPONENT_ENABLED_STATE_DISABLED,
            PackageManager.DONT_KILL_APP,
        )
        if (users.isNotEmpty()) runCatching { NotificationListenerService.requestRebind(component) }
    }

    fun granted(ctx: Context): Boolean {
        val flat = Settings.Secure.getString(ctx.contentResolver, "enabled_notification_listeners") ?: return false
        return flat.split(":").any { ComponentName.unflattenFromString(it)?.packageName == ctx.packageName }
    }

    fun dispatch(l: NotificationListener, sbn: StatusBarNotification, replay: Boolean) {
        NotificationsFeature.active?.posted(l, sbn, replay)
        CallsFeature.active?.posted(l, sbn, replay)
    }
}

/** Bound by Android while a notification-reading feature is on and notification access is granted. */
class NotificationListener : NotificationListenerService() {
    override fun onListenerConnected() {
        NotificationAccess.listener = this
        NotificationsFeature.active?.listenerUp(true)
        activeNotifications?.forEach { NotificationAccess.dispatch(this, it, true) }
    }

    override fun onListenerDisconnected() {
        if (NotificationAccess.listener === this) NotificationAccess.listener = null
        NotificationsFeature.active?.listenerUp(false)
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        NotificationAccess.dispatch(this, sbn, false)
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification) {
        NotificationsFeature.active?.removed(sbn)
        CallsFeature.active?.removed(sbn)
    }
}
