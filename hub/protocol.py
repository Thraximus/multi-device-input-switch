"""Serial link protocol shared with firmware/src/protocol.h and firmware/esp32 (router).

Frame: 0xA5, type, len, payload[len], crc8(type, len, payload)

Channels: the ESP32 router forwards channel 0 (types 0x01-0x3F, replies 0x80-0xBF) to the Pico
unchanged and handles channel 0x40 (same messages + 0x40, replies 0xC0-0xFF) itself as a
Bluetooth LE keyboard/mouse.
"""
import struct

SOF = 0xA5
MAX_PAYLOAD = 64

# Hub -> Pico
MSG_MOUSE = 0x01
MSG_KEYBOARD = 0x02
MSG_CONSUMER = 0x03
MSG_RELEASE_ALL = 0x04
MSG_PING = 0x05
MSG_BOOTLOADER = 0x06
MSG_DEBUG_REQ = 0x07
MSG_GAMEPAD = 0x0B      # u16 buttons (XInput layout), u8 lt, u8 rt, i16 lx, ly, rx, ry (XInput: Y up)

# Pico -> Hub
MSG_KBD_LEDS = 0x81
MSG_STATUS = 0x82
MSG_DEBUG = 0x83
MSG_RUMBLE = 0x86       # u8 strong (large motor), u8 weak (small motor), 0-255

STATUS_MOUNTED = 0x01
STATUS_SUSPENDED = 0x02
STATUS_XINPUT = 0x04    # Pico: the host opened the XInput (Xbox 360) interface

CHANNEL_PICO = 0x00
CHANNEL_BLE = 0x40
CHANNEL_MASK = 0x40

# Bluetooth channel only (hub -> ESP32), sent with CHANNEL_BLE added
MSG_BLE_PAIR = 0x08     # u8 seconds: accept a new bond for this long (0 = stop)
MSG_BLE_FORGET = 0x09   # 6-byte address: forget that host; empty: forget all
MSG_BLE_SELECT = 0x0A   # 6-byte address of the host that receives input (all zero = none)
MSG_BLE_PASSKEY = 0x84  # reply: u32 passkey the phone shows during pairing
MSG_BLE_HOSTS = 0x85    # reply: n x (6-byte address, u16 connection interval in 1.25 ms units)
# On the Bluetooth channel MSG_KBD_LEDS carries the host address first: 6-byte address, u8 LEDs.
# Bluetooth STATUS flags: STATUS_MOUNTED = a bonded host is connected; plus:
STATUS_PAIRING = 0x04
MSG_BLE_CONFIRM = 0x0D   # u8 1/0: approve/reject the pairing code the ESP32 reported (MSG_BLE_PASSKEY)
MSG_DISPLAY = 0x0C      # u8 chunk (0-31), 32 bytes of the OLED picture; the last chunk shows it (optional OLED on the ESP32)


def crc8(data, crc=0):
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def frame(msg_type, payload=b""):
    head = bytes((msg_type, len(payload)))
    return bytes((SOF,)) + head + payload + bytes((crc8(head + payload),))


def mouse(buttons, dx, dy, wheel=0, pan=0, channel=0):
    return frame(MSG_MOUSE | channel, struct.pack("<Bhhbb", buttons, dx, dy, wheel, pan))


def keyboard(modifiers, keys, channel=0):
    keys = list(keys)[:6]
    return frame(MSG_KEYBOARD | channel, bytes([modifiers, 0] + keys + [0] * (6 - len(keys))))


def consumer(usage, channel=0):
    return frame(MSG_CONSUMER | channel, struct.pack("<H", usage))


def gamepad(buttons, lt, rt, lx, ly, rx, ry, channel=0):
    return frame(MSG_GAMEPAD | channel, struct.pack("<HBBhhhh", buttons, lt, rt, lx, ly, rx, ry))


def release_all(channel=0):
    return frame(MSG_RELEASE_ALL | channel)


def ping(channel=0):
    return frame(MSG_PING | channel)


class Parser:
    """Incremental parser for frames coming back from the Pico."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf += data
        frames = []
        while True:
            start = self.buf.find(SOF)
            if start < 0:
                self.buf.clear()
                break
            del self.buf[:start]
            if len(self.buf) < 4:
                break
            length = self.buf[2]
            if length > MAX_PAYLOAD:
                del self.buf[0]
                continue
            if len(self.buf) < 4 + length:
                break
            body = bytes(self.buf[1:3 + length])
            if crc8(body) == self.buf[3 + length]:
                frames.append((body[0], body[2:]))
                del self.buf[:4 + length]
            else:
                del self.buf[0]
        return frames
