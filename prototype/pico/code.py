# Reads 7-byte packets from USB serial and replays them as a real USB mouse.
# Packet: 0xA5, buttons (u8), dx (i16 LE), dy (i16 LE), wheel (i8)
import struct
import usb_cdc
import usb_hid

ser = usb_cdc.data
mouse = next(d for d in usb_hid.devices if d.usage_page == 0x01 and d.usage == 0x02)
report = bytearray(4)  # buttons, x, y, wheel (signed 8-bit each)

buf = b""
buttons = 0
sent_buttons = 0
dx = dy = wheel = 0


def clamp(v):
    return -127 if v < -127 else 127 if v > 127 else v


def flush(btn):
    """Send accumulated movement with the given button state, in int8-sized chunks."""
    global dx, dy, wheel, sent_buttons
    while dx or dy or wheel or btn != sent_buttons:
        x, y, w = clamp(dx), clamp(dy), clamp(wheel)
        report[0] = btn
        report[1] = x & 0xFF
        report[2] = y & 0xFF
        report[3] = w & 0xFF
        mouse.send_report(report)
        dx -= x
        dy -= y
        wheel -= w
        sent_buttons = btn


while True:
    n = ser.in_waiting
    if n:
        # bytearray item deletion isn't supported in CircuitPython, so parse by index and slice once.
        buf += ser.read(n)
        i = 0
        while len(buf) - i >= 7:
            if buf[i] != 0xA5:
                i += 1  # resync
                continue
            b, x, y, w = struct.unpack_from("<Bhhb", buf, i + 1)
            i += 7
            if b != buttons:
                # Don't merge a click into movement from before it.
                flush(buttons)
                buttons = b
            dx += x
            dy += y
            wheel += w
        buf = buf[i:]
    if dx or dy or wheel or buttons != sent_buttons:
        flush(buttons)
