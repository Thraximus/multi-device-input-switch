#!/usr/bin/env python3
"""End-to-end loopback test: Linux -> serial link -> Pico -> USB back into this same Linux PC.

Plug the Pico into this PC. Its input devices are grabbed during the test so nothing reaches the desktop.
Run from the repo root: python3 tools/link_test.py [SERIAL_PORT]   (default: SWITCHER_SERIAL_PORT from .env)
"""
import glob
import os
import sys
import time

import evdev
from evdev import ecodes as e

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from hub import protocol  # noqa: E402
from hub.link import Link  # noqa: E402
from hub.config import SERIAL_PORT  # noqa: E402

link = Link(sys.argv[1] if len(sys.argv) > 1 else SERIAL_PORT)  # default: SWITCHER_SERIAL_PORT
devs = [evdev.InputDevice(p) for p in sorted(glob.glob("/dev/input/by-id/usb-pi-switcher-hub_*event*"))]
if not devs:
    sys.exit("Pico input devices not found - is the Pico plugged into this PC?")
for d in devs:
    d.grab()
kbd = next(d for d in devs if d.path.endswith("event-kbd"))

failures = 0


def check(name, ok, detail=""):
    global failures
    failures += not ok
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def drain(seconds=0.15):
    """Collect (type, code, value) events from all Pico devices."""
    out, end = [], time.monotonic() + seconds
    while time.monotonic() < end:
        for d in devs:
            try:
                out += [(ev.type, ev.code, ev.value) for ev in d.read() if ev.type != e.EV_SYN and ev.type != e.EV_MSC]
            except BlockingIOError:
                pass
        time.sleep(0.002)
    return out


def frames_from_pico(seconds=0.2):
    out, end = [], time.monotonic() + seconds
    while time.monotonic() < end:
        out += link.read_frames()
        time.sleep(0.005)
    return out


try:
    drain()
    link.read_frames()

    link.send(protocol.ping())
    status = [p for t, p in frames_from_pico() if t == protocol.MSG_STATUS]
    check("ping -> status", bool(status) and status[-1][0] & protocol.STATUS_MOUNTED, f"status={status[-1].hex() if status else None}")

    link.send(protocol.mouse(0, 1000, -500, 1, 0))
    ev = drain()
    rel = lambda code: sum(v for t, c, v in ev if t == e.EV_REL and c == code)
    check("mouse move 16-bit", rel(e.REL_X) == 1000 and rel(e.REL_Y) == -500, f"x={rel(e.REL_X)} y={rel(e.REL_Y)}")
    check("mouse wheel", rel(e.REL_WHEEL) == 1, f"wheel={rel(e.REL_WHEEL)}")

    link.send(protocol.mouse(1, 0, 0) + protocol.mouse(0, 0, 0))  # press + release back-to-back
    ev = drain()
    check("click (press+release in one burst)", [(t, c, v) for t, c, v in ev if c == e.BTN_LEFT] == [(e.EV_KEY, e.BTN_LEFT, 1), (e.EV_KEY, e.BTN_LEFT, 0)], str(ev))

    link.send(protocol.keyboard(0x02, [0x04]))  # Left Shift + A
    link.send(protocol.keyboard(0, []))
    ev = drain()
    keys = [(c, v) for t, c, v in ev if t == e.EV_KEY]
    check("keyboard shift+A", (e.KEY_LEFTSHIFT, 1) in keys and (e.KEY_A, 1) in keys and (e.KEY_A, 0) in keys and (e.KEY_LEFTSHIFT, 0) in keys, str(keys))

    link.send(protocol.consumer(0xE9) + protocol.consumer(0))  # Volume up
    ev = drain()
    check("media key", [(c, v) for t, c, v in ev if t == e.EV_KEY] == [(e.KEY_VOLUMEUP, 1), (e.KEY_VOLUMEUP, 0)], str(ev))

    bad = bytearray(protocol.keyboard(0, [0x05]))
    bad[-1] ^= 0xFF
    link.send(b"\x00\xa5\x13" + bytes(bad))
    ev = drain()
    check("corrupted frame ignored", not ev, str(ev))

    link.read_frames()
    kbd.set_led(e.LED_CAPSL, 1)
    leds = [p for t, p in frames_from_pico(0.3) if t == protocol.MSG_KBD_LEDS]
    kbd.set_led(e.LED_CAPSL, 0)
    check("caps lock LED reaches the hub", any(p and p[0] & 0x02 for p in leds), f"led frames={[p.hex() for p in leds]}")

    link.send(protocol.keyboard(0, [0x73]))  # hold F24, then go silent
    drain(0.1)
    ev = drain(0.8)
    check("heartbeat timeout releases held keys", (e.EV_KEY, e.KEY_F24, 0) in ev, str(ev))

    # Latency: time from writing a frame to the event arriving back, over many samples.
    samples = []
    for i in range(200):
        drain(0)
        t0 = time.perf_counter()
        link.send(protocol.mouse(0, 1 if i % 2 else -1, 0))
        while True:
            got = False
            for d in devs:
                try:
                    if any(ev.type == e.EV_REL for ev in d.read()):
                        got = True
                except BlockingIOError:
                    pass
            if got or time.perf_counter() - t0 > 0.1:
                break
        samples.append((time.perf_counter() - t0) * 1000)
        time.sleep(0.003)
    samples.sort()
    print(f"latency ms: median {samples[len(samples)//2]:.2f}  p95 {samples[int(len(samples)*0.95)]:.2f}  max {samples[-1]:.2f}")
finally:
    link.send(protocol.release_all())
    for d in devs:
        d.ungrab()

print("ALL PASSED" if not failures else f"{failures} FAILED")
sys.exit(1 if failures else 0)
