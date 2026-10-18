#!/usr/bin/env python3
"""Measure Bluetooth keyboard latency to an Android host (stop the hub service first).

Sends F13 key presses (no visible effect) through the ESP32 to the host with the given identity address,
in typing-like bursts with pauses, while `adb shell getevent` records when each press reaches the device.
Prints each key's extra delay over the fastest one (the fixed offset between the two clocks cancels).

  tools/ble_latency.py HOST_ADDRESS [SERIAL_PORT] [ADB_SERIAL]   (HOST_ADDRESS: the target's address, see hub.ctl status)
"""
import os
import random
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from hub import protocol as p  # noqa: E402
from hub.link import Link  # noqa: E402
from hub.config import SERIAL_PORT  # noqa: E402

F13 = 0x68  # HID usage; Android: KEY_F13
address = sys.argv[1]
port = sys.argv[2] if len(sys.argv) > 2 else SERIAL_PORT
adb = ["adb"] + (["-s", sys.argv[3]] if len(sys.argv) > 3 else [])

received = []
proc = subprocess.Popen(adb + ["shell", "getevent", "-lt"], stdout=subprocess.PIPE, text=True)


def reader():
    for line in proc.stdout:
        m = re.search(r"\[\s*(\d+\.\d+)\].*KEY_F13\s+DOWN", line)
        if m:
            received.append(float(m.group(1)))


threading.Thread(target=reader, daemon=True).start()
link = Link(port)
time.sleep(1.5)
link.read_frames()
link.send(p.frame(p.MSG_BLE_SELECT | p.CHANNEL_BLE, bytes.fromhex(address.replace(":", ""))))
time.sleep(0.5)

random.seed(1)
sent = []
for burst in range(6):
    for _ in range(8):  # fast typing: ~12 keys/s
        sent.append(time.monotonic())
        link.send(p.keyboard(0, [F13], p.CHANNEL_BLE))
        time.sleep(0.035)
        link.send(p.keyboard(0, [], p.CHANNEL_BLE))
        time.sleep(random.uniform(0.03, 0.08))
    pause_end = time.monotonic() + random.uniform(0.3, 0.9)  # a pause: the link could doze
    while time.monotonic() < pause_end:  # keep the hub's heartbeat going, as the real hub does
        link.send(p.ping(p.CHANNEL_BLE))
        time.sleep(0.1)
time.sleep(1.5)
link.send(p.frame(p.MSG_BLE_SELECT | p.CHANNEL_BLE, bytes(6)))
proc.terminate()

if len(received) != len(sent):
    print(f"sent {len(sent)} presses, saw {len(received)} — check the address/adb device")
n = min(len(sent), len(received))
offsets = [received[i] - sent[i] for i in range(n)]
base = min(offsets)
delays = [(o - base) * 1000 for o in offsets]
for i, d in enumerate(delays):
    print(f"key {i:2d}{' (after pause)' if i % 8 == 0 else ''}: +{d:6.1f} ms")
delays.sort()
print(f"\nextra delay: median {delays[n // 2]:.1f} ms, 90th pct {delays[int(n * .9)]:.1f} ms, max {delays[-1]:.1f} ms")
