# Hardware

Two cheap microcontroller boards and three wires. The PC talks to the **ESP32** over USB; the ESP32 passes
laptop-bound input to the **Raspberry Pi Pico** over a UART and speaks Bluetooth LE to phones and tablets
itself. The Pico is plugged into the laptop and shows up there as an ordinary USB keyboard, mouse and Xbox
360 controller.

```
 Linux PC ──USB──► ESP32 DevKit ──3 wires──► Raspberry Pi Pico ──USB──► Windows laptop
                       │
                       └── Bluetooth LE ──► phones, tablets (up to 4 connected at once)
```

## Parts

| Part | Notes |
|---|---|
| ESP32 DevKit V1 (ESP32-WROOM-32) | Any classic ESP32 dev board with a USB-to-serial chip (CP2102 or CH340) works. Not the C3/S3 variants (different Bluetooth/UART setup, untested). |
| Raspberry Pi Pico (RP2040) | Original or clone. The firmware uses UART0 and the onboard LED only. |
| 3 jumper wires | female–female if both boards have pin headers |
| USB data cable PC → ESP32 | must carry data (charge-only cables won't enumerate) |
| USB data cable Pico → laptop | see the note on USB-C clones below |

> **USB-C Pico clones:** many have no CC resistors, so a USB-C ↔ USB-C cable gives them no power at all.
> Use a USB-A → USB-C **data** cable. A charge-only cable powers the board but the laptop never sees it.

## Wiring

| ESP32 DevKit | Pico | Signal |
|---|---|---|
| GPIO 16 (labelled RX2 on many boards) | GP1 (pin 2, UART0 RX) | ESP32 → Pico |
| GPIO 17 (labelled TX2) | GP0 (pin 1, UART0 TX) | Pico → ESP32 |
| GND | GND (e.g. pin 3) | common ground |

```
   ESP32 DevKit V1                         Raspberry Pi Pico
  ┌───────────────┐                       ┌────────────────┐
  │        GPIO16 ├───────────────────────┤ GP1  (pin 2)   │
  │        GPIO17 ├───────────────────────┤ GP0  (pin 1)   │
  │           GND ├───────────────────────┤ GND  (pin 3)   │
  │               │                       │                │
  └──────┬────────┘                       └───────┬────────┘
       USB → Linux PC                           USB → laptop
```

- The board labels don't matter: the ESP32's UART matrix routes TX to GPIO 16 and RX to GPIO 17 whatever
  the silkscreen says. Different pins are fine too — set them in `.env` (`SWITCHER_ESP32_PICO_TX_PIN`,
  `SWITCHER_ESP32_PICO_RX_PIN`, `SWITCHER_PICO_LINK_TX_PIN`, `SWITCHER_PICO_LINK_RX_PIN`) and reflash.
- Each board is powered by its own USB cable. **Don't** connect 3V3 or VIN between them — only GND.
- Both sides use 3.3 V logic, so no level shifting is needed.
- The link runs at 921600 baud (`SWITCHER_LINK_BAUD`); keep the wires short (≤ 20 cm).

## Firmware settings

Both firmwares are built with values from the repo's `.env` (see [Setup → Configuration](setup.md#configuration)):

| Setting | Default | Used for |
|---|---|---|
| `SWITCHER_USB_ID` | `1209:0001` | the Pico's USB VID:PID ([pid.codes](https://pid.codes) test ID, private use). The hub and agents use it to never forward the Pico's own input back. |
| `SWITCHER_USB_MANUFACTURER`, `SWITCHER_USB_PRODUCT` | `pi-switcher-hub`, `Switcher Hub` | USB strings the laptop shows |
| `SWITCHER_BLE_NAME` | `Switcher Hub` | the name phones show when pairing |
| `SWITCHER_LINK_BAUD` | `921600` | serial speed hub ↔ ESP32 ↔ Pico (the hub reads it too) |
| pin settings | see above | UART wiring |

Change one, then rebuild and reflash both boards (and restart the hub) so they agree.

## Building and flashing

### ESP32 (router firmware)

Needs [ESP-IDF](https://docs.espressif.com/projects/esp-idf/) 6.x (`IDF_PATH` in `.env`).

```sh
tools/flash-esp32.sh            # builds, pauses the hub service, flashes, restarts it
```

The DevKit's auto-reset circuit puts it into download mode on its own. If flashing can't connect, hold
**BOOT** while it starts. Leave EN floating (no jumpers) in normal use.

### Pico

The Pico SDK is a git submodule:

```sh
git submodule update --init firmware/pico-sdk
git -C firmware/pico-sdk submodule update --init lib/tinyusb
```

**First time:** hold **BOOTSEL** while plugging the Pico into the PC; it appears as a drive named
`RPI-RP2`. Build and copy the firmware onto it:

```sh
cmake -S firmware -B firmware/build -G Ninja && ninja -C firmware/build
cp firmware/build/switcher_hub.uf2 /run/media/$USER/RPI-RP2/
```

**Later updates** don't need the button — the hub can reboot the Pico into its bootloader over the link:

- Pico plugged into the laptop (normal use, needs the Windows agent): build, then
  `python3 -m hub.ctl flash-pico laptop` — the agent copies the firmware onto the drive on the laptop.
  The GUI's **Machines → Flash Pico** does the same.
- Pico plugged into the PC: `tools/flash.sh` (builds with the `.env` settings, then flashes).

## Checking it

```sh
python3 -m hub.ctl status       # with the hub running: targets, links, Bluetooth, devices
python3 tools/link_test.py      # hub stopped, Pico on the PC: round-trip latency and a test mouse movement
python3 tools/ble_diag.py       # hub stopped: the ESP32's Bluetooth bonds and connections
```

`hub.ctl status` explains what's wrong when something is: "serial adapter not found" (ESP32 not plugged
in, or wrong `SWITCHER_SERIAL_PORT`), "serial link open, Pico not answering" (wiring, Pico not powered),
"Pico answers, but the machine hasn't detected it" (charge-only cable, USB port).

## Performance

- Laptop: USB polling at 1000 Hz; hub → Pico → hub round trip ≈ 1.5 ms.
- Bluetooth: 7.5–11.25 ms connection interval (the phone decides). Mouse reports go one at a time, each
  sent the moment the previous one is acknowledged, so motion is smooth instead of bunched. The active
  device's link is kept awake so keys arrive at the next connection event; measured extra delay for
  typing: median ≈ 7 ms, worst ≈ 33 ms (`tools/ble_latency.py`).
- The serial link carries input only (≈ 1 Mbit/s) — no video or audio.
