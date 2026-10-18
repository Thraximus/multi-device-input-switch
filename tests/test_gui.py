#!/usr/bin/env python3
"""GUI tests against a throwaway hub (no hardware, no display: Qt runs offscreen).

Run from the repo root: python3 tests/test_gui.py
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="guitest-"))
os.environ["XDG_CONFIG_HOME"] = str(tmp / "config")
os.environ["XDG_RUNTIME_DIR"] = str(tmp / "run")
os.environ["SWITCHER_API_PORT"] = "47410"  # not the real hub's ports
os.environ["SWITCHER_PAIRING_PORT"] = "47411"
os.environ["SWITCHER_ENV_FILE"] = str(tmp / "no.env")  # the repo's .env doesn't apply to tests
os.environ["QT_QPA_PLATFORM"] = "offscreen"
(tmp / "run").mkdir()
cfg_dir = tmp / "config" / "pi-switcher-hub"
cfg_dir.mkdir(parents=True)
(cfg_dir / "config.json").write_text(json.dumps({
    "targets": [{"id": "laptop", "name": "Laptop", "kind": "pico", "port": str(tmp / "no-such-tty")}]
}))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui import model, theme  # noqa: E402
from gui.client import HubClient  # noqa: E402
from gui.settings import Settings  # noqa: E402
from gui.pages import ClipEntry  # noqa: E402
from gui.window import MainWindow  # noqa: E402
from hub.service import Hub  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail and not ok else ""))
    failures += not ok


def wait_for(cond, ms=4000):
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: cond() and loop.quit())
    timer.start(20)
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    return cond()


# --- state reading -------------------------------------------------------------------------------------------

STATE = {
    "active": "bt-1", "pending": None, "agents": [],
    "targets": [
        {"id": "laptop", "name": "Laptop", "kind": "pico", "connected": True, "link": True, "responding": True},
        {"id": "bt-1", "name": "Phone", "kind": "ble", "connected": True, "link": True, "responding": True,
         "conn_interval_ms": 11.25, "address": "aa:bb"},
        {"id": "bt-2", "name": "My Tab", "kind": "ble", "connected": False, "link": True, "responding": True,
         "address": "cc:dd"},
    ],
    "devices": [
        {"id": "usb:kbd", "name": "Keyboard", "origin": "linux", "kinds": ["keyboard"]},
        {"id": "remote:x", "name": "Pad", "origin": "laptop", "kinds": ["gamepad"]},
    ],
}
check("holder name", model.holder_name(STATE) == "Phone")
check("status: BLE connected", model.target_status(STATE["targets"][1]) == ("ok", "connected · 11.25 ms"))
check("status: BLE out of range", model.target_status(STATE["targets"][2])[0] == "idle")
check("icons by kind/name", [model.target_icon(t) for t in STATE["targets"]]
      == ["computer-laptop", "smartphone", "tablet"])
check("connection tags", [model.device_bus({"bus": b}) and model.device_bus({"bus": b})[0]
                          for b in ("usb", "bluetooth", "remote", "serial")] == ["USB", "BT", "network", None])
check("machine tags: Pico is USB, ESP32 is BT", [model.target_bus(t)[0] for t in STATE["targets"]] == ["USB", "BT", "BT"])
groups = model.device_groups(STATE)
check("devices grouped by machine, this PC first",
      [(g[0], [d["id"] for d in g[2]]) for g in groups] == [("linux", ["usb:kbd"]), ("laptop", ["remote:x"])])

# --- live client + window against a real hub -----------------------------------------------------------------

class FakeLinuxClipboard:
    available = True

    def __init__(self):
        self.item = None

    def start(self):
        pass

    async def stop(self):
        pass

    async def set(self, mime, data):
        self.item = (mime, data)


hub_loop = asyncio.new_event_loop()
hub_ready = threading.Event()


def run_hub():
    asyncio.set_event_loop(hub_loop)

    async def main():
        global hub
        hub = Hub(hub_loop)
        hub.clipboard.linux = FakeLinuxClipboard()  # never touch the real desktop clipboard
        await hub.start()
        hub_ready.set()
    hub_loop.run_until_complete(main())
    hub_loop.run_forever()


threading.Thread(target=run_hub, daemon=True).start()
hub_ready.wait(10)

app = QApplication(["test"])
client = HubClient()
client.start()
check("client connects and receives state", wait_for(lambda: client.state is not None))
check("state lists the configured target", [t["id"] for t in client.state["targets"]] == ["laptop"])

replies = []
client.request({"type": "set_transfer", "device": "usb:nope", "enabled": True}, replies.append)
check("request round trip (error reply reaches the callback)",
      wait_for(lambda: replies) and replies[0].get("type") == "error")

for name in ("plasma", "cogitator"):
    theme.apply(app, name)
    w = MainWindow(client, Settings(), lambda n: None)
    w.update_state(client.state)
    for i in range(len(w.pages)):
        w.show_page(i)
    img = w.grab()
    check(f"{name}: window builds and renders all pages", not img.isNull() and img.width() > 0)
    card = w.pages[0].cards.get("laptop")
    check(f"{name}: a card per machine", card is not None and set(w.pages[0].cards) == {"linux", "laptop"})
    check(f"{name}: unconnected machine can't be clicked", card is not None and not card.isEnabled())
    page = w.pages[0]
    fake = dict(client.state, targets=[dict(client.state["targets"][0], id=f"t{i}", name=f"T{i}") for i in range(5)])
    page.update_state(fake)
    fits = []
    for width in (1300, 640, 300):
        page.resize(width, 800)
        page._reflow()
        right = max(c.geometry().right() for c in page.cards.values()) if page.isVisible() else 0
        fits.append((page._cols, right <= width))
    check(f"{name}: machine cards wrap to the width (6 cards)", [c for c, _ in fits] == [6, 3, 1], str(fits))
    page.update_state(dict(client.state, targets=[dict(client.state["targets"][0], connected=True, link=True,
                                                       name="A machine with a rather long name")]))
    heights = set()
    for width in (1300, 900, 640, 420):
        page.resize(width, 800)
        page.layout().activate()
        heights |= {c.sizeHint().height() for c in page.cards.values()}
    check(f"{name}: card height doesn't change with width or text", len(heights) == 1, str(heights))
    page.update_state(client.state)
    w.resize(1000, 600)
    w.show_page(1)
    w.show()
    wait_for(lambda: False, 200)
    sc = w.stack.currentWidget()
    check(f"{name}: scrollbar floats over the margin (page keeps full width)",
          sc.bar.isVisible() and sc.widget().width() == sc.viewport().width() == sc.width()
          and sc.bar.geometry().left() >= sc.width() - 28, f"{sc.bar.isVisible()} {sc.widget().width()} {sc.width()}")
    w.hide()
    w.notify("hello", "bad")
    check(f"{name}: footer shows action results", w.footer.text() in ("hello", "HELLO"))
    w.deleteLater()

from gui.widgets import Toggle  # noqa: E402
toggle = Toggle()
toggle.move(30, 7)
toggle.setChecked(True)
wait_for(lambda: toggle._pos == 1.0, 1000)
check("toggle animates its knob, not its position", toggle._pos == 1.0 and toggle.pos().x() == 30)

# --- clipboard history page ---
import base64  # noqa: E402
import io  # noqa: E402
from PIL import Image  # noqa: E402
buf = io.BytesIO()
Image.new("RGB", (40, 20), (200, 30, 30)).save(buf, "PNG")
TEXT = "text/plain;charset=utf-8"
hub_loop.call_soon_threadsafe(hub.clipboard._from_linux, TEXT, b"first\ncopy")
hub_loop.call_soon_threadsafe(hub.clipboard._from_linux, "image/png", buf.getvalue())
w = MainWindow(client, Settings(), lambda n: None)
cp = next(p for p in w.pages if p.title == "Clipboard")
ok = wait_for(lambda: client.state and len(client.state["clipboard"]["history"]) == 2)
cp.update_state(client.state)
entries = list(cp.entries.values())
check("clipboard page lists history newest first",
      ok and [e.entry["mime"] for e in entries] == ["image/png", TEXT], str([e.entry["mime"] for e in entries]))
check("text preview flattened to one paragraph", entries[1].text._full == "first copy")
check("image entry gets a thumbnail", wait_for(lambda: entries[0].thumb.pixmap() is not None
                                                and entries[0].thumb.pixmap().width() == ClipEntry.THUMB))
check("current entry marked", entries[0].active and not entries[1].active)
client.request({"type": "clipboard_restore", "item": entries[1].item_id})
check("copy from history reaches the clipboard", wait_for(lambda: hub.clipboard.linux.item == (TEXT, b"first\ncopy")))

client.stop()
hub_loop.call_soon_threadsafe(lambda: asyncio.ensure_future(hub.stop()))
print("\nall GUI tests passed" if not failures else f"\n{failures} GUI test(s) FAILED")
sys.exit(1 if failures else 0)
