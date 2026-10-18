package online.thrax.switcher.features

/**
 * Every feature the app has, in the order the home screen shows them. New features (notifications,
 * calls/SMS, files, touchpad) are added here; nothing else needs to know about them.
 */
object Features {
    val clipboard = ClipboardFeature()
    val notifications = NotificationsFeature()
    val notificationMirror = NotificationMirrorFeature()
    val calls = CallsFeature()

    private val every: List<Feature> = listOf(clipboard, notifications, notificationMirror, calls)

    /** The features this device can run (set once at app start, see [init]). */
    var all: List<Feature> = every
        private set

    fun init(ctx: android.content.Context) {
        all = every.filter { it.available(ctx) }
    }

    fun byId(id: String): Feature? = all.firstOrNull { it.id == id }
}
