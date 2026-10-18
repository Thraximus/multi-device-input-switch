# Architecture

```
┌────────────────────────── Linux PC ──────────────────────────┐
│                                                              │
│  input devices ──evdev (grab)──► hub service ◄──Unix socket── GUI / tray / hub.ctl
│                                   │   │   ▲                  │
│  virtual pads ◄──────uinput───────┘   │   │ D-Bus            │
│  clipboard ◄───wl-clipboard───────────┤   └── desktop notifications (Plasma)
│                                       │                      │
└───────────────────────────────────────┼──────────────────────┘
               serial (USB, framed)     │        TCP: mutual TLS WebSocket (agents)
                                        ▼                 ▲
                ESP32 router ──UART──► Pico ──USB HID──► Windows laptop ── agent (tray, clipboard,
                    │                                                    controllers, notifications)
                    └──Bluetooth LE HID──► phones/tablets ── Switcher app (clipboard, notifications, calls)
```

## Components

| Component | Where | What it does |
|---|---|---|
| **hub** (`hub/`) | Linux, systemd user service | device pool, switching, the serial link, the agent server, clipboard, notifications, calls |
| **GUI** (`gui/`) | Linux, PySide6 | tray + window over the hub's Unix socket |
| **Pico firmware** (`firmware/src/`) | RP2040, Pico SDK + TinyUSB | USB composite device: boot keyboard, mouse, media keys, Xbox 360 (XInput) |
| **ESP32 router** (`firmware/esp32/`) | ESP32, ESP-IDF | forwards laptop messages to the Pico; Bluetooth LE HID for up to four hosts |
| **Windows agent** (`agent/`) | Windows, embedded Python | tray, clipboard, controllers → Linux, notification toasts, Pico flashing, self-update |
| **Android app** (`android/`) | Kotlin + Compose | clipboard, notifications, calls, status; feature modules switched from the PC |

### Hub internals

- `devices.py` — the device pool: udev discovery, one entry per physical device (all its event nodes), the
  "follows switch" toggle, and loop prevention (see below).
- `router.py` — where input goes: the hotkey, grabbing devices for the active target, waiting for keys to be
  released before switching, keyboard LED sync, rumble back to controllers.
- `targets.py` — the serial link (`SerialLink`, framing, heartbeat), the USB target (`PicoTarget`, HID and
  gamepad state → reports) and the Bluetooth channel/targets (`BleChannel`, `BleTarget`).
- `api.py` — the three listeners (below), agent capabilities, installing agents, firmware flashing via agents.
- `clipboard.py`, `notifications.py`, `remote.py` (agent controllers → uinput), `agent_settings.py`
  (per-device feature switches and settings), `android.py` (ADB install), `security.py`, `config.py`, `env.py`.

## Serial protocol (hub ↔ ESP32 ↔ Pico)

Frames: `0xA5, type, length, payload[length], crc8(type, length, payload)` (CRC-8, polynomial 0x07). Shared
definitions: `hub/protocol.py` and `firmware/src/protocol.h`.

The **channel bit 0x40** in the type decides where a frame goes: channel 0 is forwarded unchanged to the Pico;
channel 0x40 is handled by the ESP32 itself as Bluetooth input.

| Hub → device | Type | Payload |
|---|---|---|
| mouse | 0x01 | buttons, dx, dy (int16), wheel, pan |
| keyboard | 0x02 | modifiers, reserved, 6 key codes (boot report) |
| consumer key | 0x03 | usage (uint16) |
| release all | 0x04 | — |
| ping | 0x05 | — (answered with a status frame) |
| bootloader | 0x06 | — (Pico reboots into its USB bootloader) |
| gamepad | 0x0B | buttons (XInput layout), triggers, sticks |
| Bluetooth: pair / forget / select | 0x48 / 0x49 / 0x4A | seconds / address / address of the host that receives input |

| Device → hub | Type | Payload |
|---|---|---|
| keyboard LEDs | 0x81 | LED bits (Bluetooth: address first) |
| status | 0x82 | flags: mounted, suspended, XInput active / pairing open |
| rumble | 0x86 | strong, weak motor |
| Bluetooth passkey / hosts | 0xC4 / 0xC5 | pairing code / connected hosts with their connection intervals |

The hub pings every 100 ms; if the ESP32 or the Pico hears nothing for 500 ms, they release every key and
button, so a crashed hub never leaves input stuck.

## Network API

The hub runs three listeners:

| Listener | Who | Transport |
|---|---|---|
| Unix socket `$XDG_RUNTIME_DIR/pi-switcher-hub.sock` (mode 0600) | GUI, `hub.ctl` | WebSocket, JSON |
| `SWITCHER_API_PORT` (47110) | paired agents | WebSocket over TLS 1.3, **mutual** (client certificates) |
| `SWITCHER_PAIRING_PORT` (47111) | agents being paired; the Windows bootstrap download | TLS, only while a one-time pairing token is open |

Messages are JSON objects with a `type`; requests may carry an `id` that the reply echoes. The hub pushes
`state` to subscribers whenever anything changes.

**Local (GUI/CLI) requests:** `state`, `subscribe`, `switch`, `cycle`, `set_transfer`, `pair_start`,
`ble_pair`, `forget_target`, `rename_target`, `install_agent`, `install_android`, `flash_pico`, `unpair`,
`clipboard_restore`, `clipboard_get`, `clipboard_remove`, `set_agent_feature`, `set_feature_settings`,
`set_appearance`, `set_desktop_notifications`.

**Agent → hub:** `hello` (name, platform, version, capabilities, switchable features), `capabilities`,
`switch`, `clipboard`, `devices` / `pad` (controllers), `notification` / `notification_removed` /
`notification_apps`, `mirror_action`, `call`, `pico_flash_result`, `get_update` / `get_file`.

**Hub → agent:** `welcome`, `config` (theme, enabled features, feature settings), `status` (who holds the
devices), `clipboard`, `forward` / `pad_rumble`, `notification_action`, `mirror_notification` /
`mirror_notification_removed`, `call_action`, `pico_flash`, `update` / `file`.

Agents announce **capabilities**, and the hub only routes what an agent announced — a feature switched off on
the PC is stopped on the device, which then stops announcing it.

## Security

- The hub and every agent have their own key pair (EC P-256). Agent connections use mutual TLS 1.3; the hub
  trusts exactly the certificates of paired agents, and agents pin the hub's certificate byte for byte.
- **Pairing** needs a one-time token, valid for minutes, on a port that's only open while the token exists.
  The Windows bootstrap command and the Android installer carry the token and the hub certificate's SHA-256
  fingerprint, so the agent verifies the hub before sending anything.
- **Windows updates** are described by a manifest signed with the hub's Ed25519 key (received at pairing);
  every file is checked against its SHA-256. **Android updates** rely on Android's own rule that an update
  must be signed with the same key as the installed app, plus the pinned hub connection.
- The local API is a Unix socket only the user can open. Nothing listens beyond the two agent ports.
- Secrets never live in the repository: keys and pairing data are under `SWITCHER_CONFIG_DIR`, settings and
  the app signing password in the gitignored `.env`.

## Loop prevention

Input must never circle between machines:

- The Pico is real hardware, so on the laptop its input isn't marked as injected. The Windows agent never
  forwards the controller whose USB VID:PID is `SWITCHER_USB_ID` (the hub tells the agent on connect).
- A Pico plugged into the Linux PC is ignored by the device pool (and reported as "passthrough").
- Devices the hub creates (virtual controllers for input arriving from the laptop) are named
  `pi-switcher-hub …` and ignored by the pool.
- Notifications mirrored to a device are posted by the Switcher app, which never forwards its own
  notifications.
- Clipboard items carry a content hash; a machine never sends back what it just received.

## Clipboard, notifications, calls

- **Clipboard**: the hub keeps the current item and a 10-entry history in memory (64 MB cap); new items from
  any machine go to all others. Linux uses `wl-clipboard`; Windows the Win32 clipboard (text, PNG/DIB images);
  Android see [Android app](android.md). Items a password manager marks as secret are synced but never kept
  in the history.
- **Notifications**: devices send notifications they're allowed to (per-app filter, applied on the device);
  the hub shows them on the desktop via `org.freedesktop.Notifications` (with "Dismiss on device" and inline
  reply) and mirrors them to devices with mirroring switched on. Actions travel back to the source device.
  Re-sent notifications after a reconnect are flagged as replays and never pop up again.
- **Calls**: ringing calls become critical desktop notifications, which Plasma shows even in Do Not Disturb
  and over full-screen apps; Answer/Decline act on the phone. Regular calls come from the phone state, app
  calls (WhatsApp, LINE, …) from their call notifications.
