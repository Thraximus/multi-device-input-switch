# Multi-device input switch

One keyboard, mouse and game controller for a **Linux PC**, a **Windows laptop** and your **Android phones
and tablets** — switched with **Ctrl + Scroll Lock**. A DIY KVM without the "V": input reaches the other
machines through real hardware (a Raspberry Pi Pico as a USB keyboard/mouse/Xbox controller, an ESP32 as a
Bluetooth keyboard/mouse), so it works everywhere a physical keyboard does — including games with
kernel anti-cheat that block software-injected input.

On top of the input switching, small companion apps tie the machines together: shared clipboard (text and
images), notifications and incoming calls on every device, and a tray app on Linux to control it all.

## Features

- **Instant switching** between the PC, the laptop and up to four Bluetooth devices, with a hotkey, the tray
  menu or the GUI. Keys are never left stuck: switching waits until everything is released.
- **Real hardware input**: the laptop sees a USB composite device (boot keyboard, 16-bit mouse at 1000 Hz,
  media keys, Xbox 360 controller with rumble); phones and tablets see a Bluetooth LE keyboard and mouse.
- **Per-device choice**: decide for every keyboard, mouse or controller whether it follows the switch or stays.
- **Two-way**: a controller plugged into the laptop can be used on the Linux PC (virtual Xbox 360 pad).
- **Clipboard sync** between all machines, text and images, with a 10-entry history on the PC
  (memory only; password-manager copies are never kept).
- **Notifications**: phone/tablet notifications on the PC (with dismiss and inline reply), mirrored to the
  other devices, filtered per app.
- **Calls**: incoming calls — regular, WhatsApp, LINE, … — pop up on the PC even over full-screen games,
  with Answer / Decline.
- **Linux GUI + tray** (PySide6) with two looks: native Plasma/Breeze and the "Cogitator" skin. A matching
  Android app (Jetpack Compose) and a Windows tray agent.
- **Self-updating** Windows agent and Android app, installed by the hub itself (the Pico even types the
  Windows installer for you).
- **Secure by default**: mutual TLS with pinned certificates, one-time pairing tokens, signed updates.

## How it works

```
                        USB (serial, 921600 baud)                 UART                 USB
 Linux PC (hub) ─────────────────────────────► ESP32 ──────────────────────► Pico ─────────────► Windows laptop
   keyboard, mouse, controller                   │                                             (sees a USB keyboard,
   tray GUI                                      │ Bluetooth LE HID                            mouse, Xbox controller)
                                                 └───────────────► phones / tablets (see a Bluetooth keyboard + mouse)

 Linux PC (hub) ◄──────── Wi-Fi / LAN, mutual TLS ────────► Windows agent, Android app
                          (clipboard, notifications, calls, status, updates)
```

The **hub** runs on the Linux PC as a user service. It grabs the input devices that should follow the switch
and sends their events as small framed messages over one USB serial link to an **ESP32**. The ESP32 forwards
laptop-bound messages to the **Pico** (which is plugged into the laptop and acts as a USB HID device) and
handles phone/tablet-bound messages itself as a Bluetooth LE HID peripheral connected to several hosts at once.

Everything beyond raw input (clipboard, notifications, calls, gamepads coming back from the laptop, status
and switching from the other side) goes over the network to small **agents**: a headless Python agent with
a tray icon on Windows, and the **Switcher** app on Android.

## Documentation

| | |
|---|---|
| [Hardware](docs/hardware.md) | parts, wiring, flashing the ESP32 and the Pico |
| [Setup](docs/setup.md) | the hub service, the GUI, the Windows agent, phones and tablets, configuration |
| [Architecture](docs/architecture.md) | components, the serial protocol, the network API, security |
| [Android app](docs/android.md) | features, permissions, how clipboard reading works, adding a feature |

## Quick start

1. Build the hardware: an ESP32 DevKit and a Raspberry Pi Pico, three wires — see [Hardware](docs/hardware.md).
2. `cp .env.example .env` and fill in the serial port and toolchain paths — see [Setup](docs/setup.md#configuration).
3. Flash the Pico and the ESP32, then install the hub: `tools/install-service.sh` and `tools/install-gui.sh`.
4. Plug the Pico into the laptop — it works as a keyboard and mouse right away. Press **Ctrl + Scroll Lock**.
5. Optional: install the Windows agent and pair phones/tablets from the GUI's **Machines** page.

## Requirements

- **Hub**: Linux with evdev/uinput (tested on Arch Linux with KDE Plasma 6 on Wayland), Python 3.11+,
  `python-evdev`, `pyudev`, `websockets`, `cryptography`, `jeepney`, `PySide6`, `wl-clipboard`, `adb`.
- **Laptop**: Windows 10/11. The agent ships its own Python; no admin rights needed.
- **Phones/tablets**: Android 8+ (clipboard and notification features tested on Android 11 and 13).
- **Building**: Pico SDK (submodule), ESP-IDF 6, Android SDK + JDK 17/21 for the app.

## Status

Working and used daily: switching, laptop + phone + tablet targets, two-way controllers, clipboard,
notifications, calls, the GUIs, self-updates. Planned: audio routing (the PC as the audio output for every
machine, including phone calls — needs a USB Bluetooth adapter), file sharing, using the phone as a touchpad,
the laptop's own keyboard/touchpad on Linux, screen sharing.
