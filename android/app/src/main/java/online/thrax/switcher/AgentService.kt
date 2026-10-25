package online.thrax.switcher

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Base64
import android.util.Log
import online.thrax.switcher.core.App
import online.thrax.switcher.core.DeviceConfig
import online.thrax.switcher.core.Holder
import online.thrax.switcher.core.Machine
import online.thrax.switcher.core.Updater
import online.thrax.switcher.features.Feature
import online.thrax.switcher.features.FeatureHost
import online.thrax.switcher.features.Features
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger

/**
 * The connection to the hub (mutual TLS over Wi-Fi) and the features running over it. A foreground
 * service so Android keeps it alive; the hub also exempts the app from battery optimisation.
 *
 * Protocol (on top of the Windows agent's): hello carries the features this app has; the hub answers
 * with {"type": "config", "theme", "features": {id: bool}} and the service runs exactly those, then
 * reports {"type": "capabilities"} so the hub routes only what's enabled. "status" (who holds the
 * keyboard) is the same message the Windows tray gets.
 */
class AgentService : Service(), FeatureHost {
    private val main = Handler(Looper.getMainLooper())
    private val sender = Executors.newSingleThreadExecutor()
    private val ids = AtomicInteger()
    @Volatile private var running = false
    @Volatile private var ws: WebSocket? = null
    private val active = mutableMapOf<String, Feature>()
    private val waiting = java.util.concurrent.ConcurrentHashMap<Int, java.util.concurrent.CompletableFuture<JSONObject>>()
    private lateinit var config: DeviceConfig

    override val context: Context get() = this
    override val connected: Boolean get() = ws != null

    override fun send(msg: JSONObject): Boolean {
        val w = ws ?: return false
        sender.execute {
            try {
                w.send(msg.toString())
            } catch (e: IOException) {
                Log.w(TAG, "send failed", e)
            }
        }
        return true
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
        Features.init(this)
        config = DeviceConfig(this)
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Connection", NotificationManager.IMPORTANCE_MIN))
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTE_STATUS, notification("connecting…"), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTE_STATUS, notification("connecting…"))
        }
        App.state.value = App.state.value.copy(skin = config.skin, enabled = enabledMap(config.enabled))
        applySettings(config.settings)
        applyFeatures(enabledMap(config.enabled))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_DISCONNECT || config.stopped) {  // the notification's button, or a restart after it
            disconnect(this)
            return START_NOT_STICKY
        }
        if (!running) {
            running = true
            Thread(::run, "hub").start()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        instance = null
        running = false
        ws?.close()
        active.values.forEach { it.stop() }
        active.clear()
        sender.shutdownNow()
        App.state.value = App.state.value.copy(connected = false, holder = null,
            connection = if (config.stopped) "Disconnected" else "Stopped")
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    /** Send a request and wait for the hub's reply (matched by id); null if offline or no answer. */
    fun request(msg: JSONObject, timeoutS: Long = 30): JSONObject? {
        val w = ws ?: return null
        val id = ids.incrementAndGet()
        val reply = java.util.concurrent.CompletableFuture<JSONObject>()
        waiting[id] = reply
        return try {
            w.send(msg.put("id", id).toString())
            reply.get(timeoutS, java.util.concurrent.TimeUnit.SECONDS)
        } catch (e: Exception) {
            null
        } finally {
            waiting.remove(id)
        }
    }

    /** Ask the hub to send the keyboard/mouse to a machine: a target id, or null for the hub's own PC. */
    fun switchTo(target: String?) {
        send(JSONObject().put("type", "switch").put("id", ids.incrementAndGet()).put("target", target ?: JSONObject.NULL))
    }

    // --- features ----------------------------------------------------------------------------------

    /** Hub config merged over each feature's default. */
    private fun enabledMap(saved: Map<String, Boolean>) =
        Features.all.associate { it.id to (saved[it.id] ?: it.defaultEnabled) }

    private fun applyFeatures(enabled: Map<String, Boolean>) = main.post {
        for (f in Features.all) {
            val on = enabled[f.id] == true
            if (on && f.id !in active) {
                f.start(this)
                active[f.id] = f
                if (connected) f.onConnected()
            } else if (!on && f.id in active) {
                active.remove(f.id)?.stop()
            }
        }
    }

    private fun applySettings(all: JSONObject) = main.post {
        for (f in Features.all) f.onSettings(all.optJSONObject(f.id) ?: JSONObject())
    }

    private fun capabilities(): JSONArray {
        val caps = JSONArray().put("tray")  // receive "status": who holds the keyboard
        val enabled = App.state.value.enabled
        Features.all.filter { enabled[it.id] == true }.forEach { caps.put(it.capability) }
        return caps
    }

    private fun onConfig(msg: JSONObject) {
        val features = msg.optJSONObject("features")
        val saved = features?.keys()?.asSequence()?.associateWith { features.getBoolean(it) } ?: config.enabled
        val skin = msg.optString("theme", config.skin)
        config.skin = skin
        config.enabled = saved
        msg.optJSONObject("settings")?.let {
            config.settings = it
            applySettings(it)
        }
        val enabled = enabledMap(saved)
        App.state.value = App.state.value.copy(skin = skin, enabled = enabled)
        applyFeatures(enabled)
        send(JSONObject().put("type", "capabilities").put("capabilities", capabilities()))
    }

    private fun onStatus(msg: JSONObject) {
        val self = msg.optString("self").ifEmpty { null }
        val holderId = if (msg.isNull("holder")) null else msg.optString("holder")
        val targets = msg.optJSONArray("targets") ?: JSONArray()
        val machines = mutableListOf(Machine(null, msg.optString("hub", "the PC"), "linux", true))
        for (i in 0 until targets.length()) {
            val t = targets.getJSONObject(i)
            machines += Machine(t.optString("id"), t.optString("name"), t.optString("kind"), t.optBoolean("connected"))
        }
        val name = machines.firstOrNull { it.id == holderId }?.name ?: machines[0].name
        App.state.value = App.state.value.copy(
            holder = Holder(name, holderId != null && holderId == self, self, !msg.isNull("pending"), machines, holderId),
        )
    }

    // --- connection --------------------------------------------------------------------------------

    private fun notification(text: String): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val disconnect = PendingIntent.getService(this, 0, Intent(this, AgentService::class.java).setAction(ACTION_DISCONNECT),
            PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_switcher)
            .setContentTitle("Switcher")
            .setContentText(text)
            .setContentIntent(open)
            .addAction(Notification.Action.Builder(null, "Disconnect", disconnect).build())
            .setOngoing(true)
            .build()
    }

    private fun setConnection(text: String, up: Boolean) {
        if (!running) return  // disconnecting: the connection thread's last words don't count
        App.state.value = App.state.value.copy(connection = text, connected = up)
        getSystemService(NotificationManager::class.java).notify(NOTE_STATUS, notification(text))
    }

    private fun run() {
        val state = HubState(this)
        var delay = 1
        while (running) {
            if (!state.paired()) {
                App.state.value = App.state.value.copy(paired = false)
                setConnection("Not paired — install from the hub", false)
                sleep(5)
                continue
            }
            try {
                session(state)
                delay = 1
            } catch (e: Exception) {
                Log.w(TAG, "hub connection lost", e)
                setConnection(describe(e), false)
            }
            ws = null
            App.state.value = App.state.value.copy(holder = null)
            sleep(delay)
            delay = minOf(delay * 2, 30)
        }
    }

    private fun session(state: HubState) {
        val hub = state.hub()
        val id = state.identity()
        val hubCert = Base64.decode(hub.getString("hub_cert_der"), Base64.DEFAULT)
        val host = hub.getString("host")
        App.state.value = App.state.value.copy(paired = true, host = host, agentId = hub.optString("agent_id"))
        WebSocket.connect(Certs.hubFactory(id[0], id[1], hubCert), host, hub.getInt("api_port"), READ_TIMEOUT_MS).use { w ->
            val features = JSONArray()
            Features.all.forEach {
                features.put(JSONObject().put("id", it.id).put("title", it.title).put("default", it.defaultEnabled))
            }
            w.send(
                JSONObject().put("type", "hello").put("id", ids.incrementAndGet())
                    .put("name", Build.MODEL).put("platform", "android").put("version", version())
                    .put("features", features).put("capabilities", capabilities()).toString(),
            )
            ws = w
            setConnection("Connected to $host", true)
            main.post { active.values.forEach { it.onConnected() } }
            val pinger = Thread({
                while (ws === w) {
                    sleep(PING_MS / 1000)
                    try {
                        w.ping()
                    } catch (e: IOException) {
                        return@Thread
                    }
                }
            }, "ping")
            pinger.isDaemon = true
            pinger.start()
            Thread({
                runCatching { Updater(this) { request(it) }.check() }.onFailure { Log.w(TAG, "update check failed", it) }
            }, "update").start()
            while (running) {
                val msg = JSONObject(w.receive())
                val waiter = if (msg.has("id")) waiting.remove(msg.optInt("id")) else null
                if (waiter != null) {  // the reply to a request() (update manifest, file chunk, …)
                    waiter.complete(msg)
                    continue
                }
                when (val type = msg.optString("type")) {
                    "config" -> onConfig(msg)
                    "status" -> onStatus(msg)
                    "error" -> Log.w(TAG, "hub error: ${msg.optString("error")}")
                    else -> active.values.firstOrNull { type in it.messageTypes }?.let { f -> main.post { f.onMessage(msg) } }
                }
            }
        }
    }

    private fun describe(e: Exception): String = when {
        e is java.net.ConnectException && e.message?.contains("EACCES") == true ->
            "Blocked by the VPN — allow Switcher past it (see the PC's Machines page)"
        e is java.net.SocketTimeoutException || e is java.net.ConnectException -> "Hub not reachable — retrying"
        e is java.io.EOFException -> "Hub closed the connection — retrying"
        else -> "Offline (${e.javaClass.simpleName}) — retrying"
    }

    private fun version(): String = runCatching { packageManager.getPackageInfo(packageName, 0).versionName }
        .getOrNull() ?: "dev"

    companion object {
        private const val TAG = "SwitcherAgent"
        private const val CHANNEL = "status"
        private const val NOTE_STATUS = 1
        private const val READ_TIMEOUT_MS = 60_000  // the hub pings every 20 s
        private const val PING_MS = 25_000
        private const val ACTION_DISCONNECT = "disconnect"

        @Volatile
        var instance: AgentService? = null
            private set

        @JvmStatic
        fun start(ctx: Context) {
            val i = Intent(ctx, AgentService::class.java)
            if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i) else ctx.startService(i)
        }

        /** Connect again after [disconnect]. */
        fun connect(ctx: Context) {
            DeviceConfig(ctx).stopped = false
            App.state.value = App.state.value.copy(stopped = false, connection = "connecting…")
            start(ctx)
        }

        /** Close the connection and stop every feature; stays that way (reboots too) until [connect]. */
        fun disconnect(ctx: Context) {
            DeviceConfig(ctx).stopped = true
            App.state.value = App.state.value.copy(stopped = true, connected = false, holder = null,
                connection = "Disconnected")
            ctx.stopService(Intent(ctx, AgentService::class.java))
        }

        private fun sleep(seconds: Int) = try {
            Thread.sleep(seconds * 1000L)
        } catch (_: InterruptedException) {
        }
    }
}
