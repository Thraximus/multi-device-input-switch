# Android app ("Switcher")

The app runs as a foreground service that keeps an encrypted connection to the hub over Wi-Fi. Its screen
shows the connection, which machine has the keyboard and mouse (with **Use here** / **Return to PC**, and
**Send to** any other connected machine), and one card per feature with its status and anything Android
still needs to allow. **Disconnect** (on the screen or the status notification) closes the connection and
stops every feature; it stays off, across reboots too, until **Connect**. Settings live on the PC:
features are switched per device there, and the PC picks the app's look (Material or Cogitator).

Built with Kotlin and Jetpack Compose (`android/`); the TLS, WebSocket and pairing core is plain Java with no
libraries. Minimum Android 8; tested on Android 11 and 13.

## Features

| Feature | Default | What it needs on the device |
|---|---|---|
| **Clipboard** — text and images both ways | on | log access and "display over other apps" (both granted by the installer) |
| **Touchpad & keyboard** — this screen as a touchpad and keyboard for the PC or the laptop | on | — |
| **Notifications** — this device's notifications to the PC (filtered per app on the PC) | off | notification access (granted by the installer) |
| **Other devices' notifications** — shown here, with Dismiss and Reply acting on the source | off | permission to post notifications |
| **Calls** — incoming calls on the PC, even over games; Answer/Decline from there | off | phone state, call log, contacts, answer calls (the app asks), notification access for app calls |

Calls only appears on devices that can make calls. With Calls off, a ringing call still reaches the PC as an ordinary notification
(if Notifications is on).

**Touchpad**: one finger moves the pointer (faster movements go further), a tap clicks, two fingers scroll
(natural direction) and a two-finger tap right-clicks; the Left/Right buttons below the pad can be held for
dragging. Pick the machine at the top. The hub moves that machine's mouse whoever has the keyboard (another
phone or tablet works too, unless the keyboard is on a third one: Bluetooth reaches one at a time).

**Keyboard** (on the touchpad screen) opens the phone's keyboard; everything typed goes straight to the
machine, with no text box in between. A row above it adds Esc, Tab, arrows, Home/End, Page Up/Down, Delete,
F1–F12, and Ctrl/Alt/Shift/Win, which apply to the next key (Ctrl, then C = copy). Text is turned into key
presses for the machine's layout (`SWITCHER_KEYBOARD_LAYOUT`, `us` or `uk`); characters that layout has no
key for (e.g. accented letters) are skipped, and the screen says which.

## Reading the clipboard in the background

Since Android 10 only the app in front (or the keyboard) may read the clipboard, and clipboard change
listeners aren't called for apps in the background. The app works around this with two permissions the
installer grants over ADB:

1. **READ_LOGS** — every time Android refuses an app a background clipboard read, it logs
   `Denying clipboard access to <package>`. The app's own listener is refused the same way whenever something
   is copied, so watching the log for that line is a reliable "something was copied" signal.
2. **SYSTEM_ALERT_WINDOW** — the app then adds a 1×1 invisible overlay window that takes focus for a moment;
   while it has focus the app may read the clipboard like a foreground app, and the overlay disappears.

On Android 13 and later, Android asks the user before an app may read the system log ("Allow one-time
access"), and refuses apps in the background. After a reboot or an update the app shows a notification;
opening the app brings the prompt back. Android 12+ also shows a short "pasted from your clipboard" message
each time the app reads.

Images: the app reads `content://` images attached to the clip; some apps (e.g. Samsung Notes) label image
copies as HTML and put the picture in an `<img src>`, which the app also follows. Images the hub sends to
the device are served by the app's own content provider.

## Installing and updating

The hub installs the app over ADB (USB debugging), using the APK built by `tools/build-android.sh`:

- grants READ_LOGS, the overlay permission, "install unknown apps" (for self-updates), notification access,
  the call permissions and notification posting;
- exempts the app from battery optimisation and adds it to the VPN lockdown allowlist (see
  [Setup → VPNs](setup.md#vpns-on-the-device));
- turns on "show on-screen keyboard while a physical keyboard is connected";
- starts pairing with a one-time token (only when the device isn't paired yet — updates keep the pairing).

After that the app updates itself over Wi-Fi: on connect it asks the hub for the current build, downloads it
over the pinned connection, checks its SHA-256 and installs it through Android's PackageInstaller. Android
12+ applies it silently because the app is its own installer of record; older versions show a notification
to confirm. The APK must always be signed with the same key (`SWITCHER_KEYSTORE`, `SWITCHER_KEYSTORE_PASSWORD`).

## Adding a feature

A feature is one class implementing `features/Feature.kt`:

```kotlin
class FilesFeature : Feature {
    override val id = "files"                          // used in the hub config and protocol
    override val title = "Files"
    override val summary = "Send files to the PC"
    override val icon = Icons.Outlined.Folder
    override val messageTypes = setOf("file_offer")    // hub → device messages it handles
    override val status = MutableStateFlow("off")

    override fun permissions(ctx: Context) = listOf<Need>()   // shown on its card, with a fix button
    override fun start(host: FeatureHost) { /* host.send(JSONObject(...)) to talk to the hub */ }
    override fun stop() { /* release everything: a feature that's off must do nothing */ }
    override fun onMessage(msg: JSONObject) {}
    @Composable override fun Details() {}              // extra UI on its card
}
```

List it in `features/Features.kt`. The service announces it to the hub in `hello`, the PC's GUI shows a switch
for it per device, the hub pushes the switch state (and any per-feature settings) in `config`, and the
service starts/stops the feature and reports its capability. The hub side handles the feature's messages
in `hub/api.py`.

## Building

```sh
tools/build-android.sh                         # release APK, signed; copied where the hub serves it
(cd android && ./gradlew :app:recordRoborazziDebug)   # render the home screen to PNG on the JVM (no device)
```

Needs `ANDROID_HOME` and `JAVA_HOME` (JDK 17 or 21) in `.env`. The app ID on devices is
`SWITCHER_ANDROID_APP_ID`; the source package (`online.thrax.switcher`) is independent of it.
