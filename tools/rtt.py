#!/usr/bin/env python3
"""Round-trip time hub -> link -> device -> hub for each channel (stop the hub service first)."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from hub import protocol  # noqa: E402
from hub.link import Link  # noqa: E402
from hub.config import SERIAL_PORT  # noqa: E402

link = Link(sys.argv[1] if len(sys.argv) > 1 else SERIAL_PORT)  # default: SWITCHER_SERIAL_PORT
time.sleep(1.5)
link.read_frames()
for name, ch in (("pico", protocol.CHANNEL_PICO), ("esp32/ble", protocol.CHANNEL_BLE)):
    samples = []
    for _ in range(100):
        time.sleep(0.01)
        link.read_frames()  # drop late replies so each sample times its own ping
        t0 = time.perf_counter()
        link.send(protocol.ping(ch))
        while time.perf_counter() - t0 < 0.2:
            if any(t == protocol.MSG_STATUS | ch for t, _ in link.read_frames()):
                samples.append((time.perf_counter() - t0) * 1000)
                break
    samples.sort()
    if samples:
        print(f"{name:10} n={len(samples):3}  median {samples[len(samples)//2]:.2f} ms  p95 {samples[int(len(samples)*.95)-1]:.2f} ms  max {samples[-1]:.2f} ms")
    else:
        print(f"{name:10} no replies")
