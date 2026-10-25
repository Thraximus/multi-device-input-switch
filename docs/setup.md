# Setup

Order: [hardware](hardware.md) → configuration → hub service → GUI → (optional) Windows agent → phones and
tablets. Everything runs from a checkout of this repository on the Linux PC.

## Configuration

All machine-specific or secret settings live in **`.env`** at the repository root. It's gitignored; start
from the documented template:

```sh
cp .env.example .env
```

The code only contains generic defaults. `.env` is read by the hub and the GUI (`hub/env.py`), the shell
tools (`tools/env.sh`), the Android build and — through the flash tools — both firmwares. Variables set in
the real environment take precedence over the file.

The ones you'll almost certainly set:

| Variable | What |
|---|---|
| `SWITCHER_SERIAL_PORT` | the ESP32's serial port; use the stable name from `ls /dev/serial/by-id/` |
| `SWITCHER_INSTALL_LAYOUT` | `us` or `uk`: the Windows machine's keyboard layout (used when the Pico types the agent installer) |
| `SWITCHER_KEYBOARD_LAYOUT` | `us` or `uk`: the machines' layout when a phone types on them (default: `SWITCHER_INSTALL_LAYOUT`) |
| `IDF_PATH` | ESP-IDF checkout, for building the ESP32 firmware |
| `ANDROID_HOME`, `JAVA_HOME` | Android SDK and a JDK 17/21, for building the Android app |
| `SWITCHER_ANDROID_APP_ID` | the app's ID on your devices (pick your own reverse-domain name) |
| `SWITCHER_KEYSTORE_PASSWORD` | password of the app's signing key (created on the first build) |

Everything else — ports, hotkey, USB/Bluetooth identity, pins, link speed — is documented in `.env.example`.
Restart the hub after changing `.env`; firmware settings need a reflash.

## Hub service (Linux)

Dependencies (Arch package names; use your distribution's equivalents):

```sh
sudo pacman -S python-evdev python-pyudev python-websockets python-cryptography python-jeepney \
               pyside6 wl-clipboard android-tools python-pillow   # python-pillow: only for the optional OLED
```

The hub reads input devices and creates virtual ones, so your user needs access to `/dev/input/event*`
and `/dev/uinput` (on most distributions: membership of the `input` group, plus a udev rule granting the
group `/dev/uinput`), and to the serial port (`uucp` or `dialout` group).

```sh
tools/install-service.sh            # installs and starts the systemd user service "pi-switcher-hub"
python3 -m hub.ctl status           # targets, Bluetooth, devices, agents
journalctl --user -u pi-switcher-hub -f
```

The hub listens on a Unix socket (local GUI/CLI) and on two TCP ports for agents (`SWITCHER_API_PORT`,
default 47110, and `SWITCHER_PAIRING_PORT`, 47111). Allow both from your LAN if you run a firewall.

### Switching

**Ctrl + Scroll Lock** (`SWITCHER_HOTKEY`) cycles Linux → laptop → phone → tablet → Linux, skipping
machines that aren't connected. The switch happens once all keys are released, so nothing gets stuck. If a
target disappears (cable pulled, Bluetooth out of range) the devices fall back to Linux.

Each input device has a **follows switch** toggle (GUI or `hub.ctl transfer DEVICE on|off`): devices that
don't follow always stay on Linux — handy for a macro pad or a pen tablet's remote.

### CLI

```
python3 -m hub.ctl status | watch
python3 -m hub.ctl switch laptop|linux|TARGET
python3 -m hub.ctl transfer DEVICE_ID on|off
python3 -m hub.ctl ble-pair [SECONDS]                 open Bluetooth pairing
python3 -m hub.ctl ble-confirm [--reject]             approve the pairing code shown by status
python3 -m hub.ctl rename TARGET NAME
python3 -m hub.ctl forget TARGET                      unpair a Bluetooth device
python3 -m hub.ctl install-agent TARGET [--layout us|uk]
python3 -m hub.ctl install-android TARGET [--pair]
python3 -m hub.ctl flash-pico TARGET [UF2]
```

## GUI (Linux)

```sh
tools/install-gui.sh                # application menu entry + start at login
python3 -m gui                      # or start it now
```

A tray icon shows which machine has the devices (its menu switches); the window has pages for
**Switch** (machines and the device pool), **Machines** (agents, Bluetooth pairing, installing apps,
flashing the Pico), **Clipboard** (history), **Notifications**, **Audio** (not yet functional) and
**Settings** (theme: Plasma or Cogitator; the look of the Android app; start at login).

## Windows laptop

With just the Pico plugged in, the laptop already works as a target — no software needed. The **agent**
adds clipboard sync, the laptop's controller on Linux, notifications from your phones, a tray icon showing
who has the devices (and switching from the laptop), and flashing the Pico from Linux.

```sh
tools/build-agent-windows.sh        # builds the self-contained agent (embedded Python), served by the hub
```

Then **Machines → Laptop → Install agent** (or `hub.ctl install-agent laptop`). With the keyboard and mouse
on Linux and the laptop unlocked, the Pico types a short PowerShell command on the laptop: it downloads the
agent from the hub over HTTPS (pinned to the hub's certificate, with a one-time token), installs it into
`%LOCALAPPDATA%` (no admin), pairs and adds it to autostart. Make sure the laptop is on the keyboard layout
set in `SWITCHER_INSTALL_LAYOUT` (and not an IME) while it types.

Updates: bump `agent/VERSION` and run the build again; agents update themselves the next time they connect.

## Phones and tablets

### Bluetooth (input)

**Machines → Bluetooth → Start pairing**, then pick the hub (`SWITCHER_BLE_NAME`) in the device's Bluetooth
settings. The pairing code appears large in the GUI (and on the OLED) — compare it with the one on the device and
click **Approve** (or run `hub.ctl ble-confirm`) within 30 s, then confirm on the device. Without the hub's approval
nothing pairs. Pairing is off by default: the hub is only discoverable while it is open (2 minutes, one new device),
and connections that aren't a paired device are dropped after 3 s. Each paired device becomes a machine of its own; up to four stay connected, so switching
between them is instant.

Android hides the on-screen keyboard while a hardware keyboard is connected. Because the hub's keyboard
stays connected, the app installer turns on Android's **Show on-screen keyboard** setting
(`show_ime_with_hard_keyboard`); you can also set it in Settings → General management → Physical keyboard.

### The Switcher app (clipboard, notifications, calls)

```sh
tools/build-android.sh              # builds and signs the app (needs ANDROID_HOME, JAVA_HOME, SWITCHER_KEYSTORE_PASSWORD)
```

Enable USB debugging on the device, connect it to the PC, allow the prompt, then **Machines → (device) →
Install app (USB)** (or `hub.ctl install-android TARGET`). The hub finds the device among the connected
ones by its Bluetooth address, installs the app, grants the permissions it needs, and pairs it. After that
USB isn't needed: new builds reach the devices over Wi-Fi (silently on Android 12+, with a "tap to install"
notification on older versions).

Features are switched per device on the PC — **Machines** (clipboard, touchpad) and **Notifications** (sending
notifications, choosing apps, showing other devices' notifications, calls). See [Android app](android.md)
for what each needs.

### VPNs on the device

The app talks to the hub over the LAN. With an always-on VPN that uses **Block connections without VPN**
(lockdown), Android blocks LAN traffic for every app — including apps excluded via split tunnelling. The
installer therefore adds the app to Android's lockdown allowlist (`always_on_vpn_lockdown_whitelist`),
which takes effect after the device's next reboot (and is cleared if you toggle lockdown in Settings —
reinstalling from the PC restores it). Without lockdown, excluding the app in the VPN's split tunnelling
or allowing LAN connections is enough.

On the PC, the hub never hands out a VPN tunnel address; set `SWITCHER_HUB_ADDRESS` if it picks the wrong
LAN address.
