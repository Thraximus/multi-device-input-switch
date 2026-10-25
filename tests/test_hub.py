#!/usr/bin/env python3
"""Hub tests that need no hardware: HID state, and API/pairing/mTLS/updates against a throwaway hub.

Run from the repo root: python3 tests/test_hub.py
The hub under test uses temporary config/runtime dirs, its own ports, and a target with no serial port.
"""
import asyncio
import base64
import hashlib
import json
import os
import ssl
import struct
import sys
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="hubtest-"))
os.environ["XDG_CONFIG_HOME"] = str(tmp / "config")
os.environ["XDG_RUNTIME_DIR"] = str(tmp / "run")
os.environ["SWITCHER_API_PORT"] = "47210"  # not the real hub's ports
os.environ["SWITCHER_PAIRING_PORT"] = "47211"
os.environ["SWITCHER_ENV_FILE"] = str(tmp / "no.env")  # the repo's .env doesn't apply to tests
(tmp / "run").mkdir()
cfg_dir = tmp / "config" / "pi-switcher-hub"
cfg_dir.mkdir(parents=True)
(cfg_dir / "config.json").write_text(json.dumps({
    "targets": [{"id": "laptop", "name": "Laptop", "kind": "pico", "port": str(tmp / "no-such-tty")}]
}))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evdev import ecodes as e  # noqa: E402
from websockets.asyncio.client import connect, unix_connect  # noqa: E402

from hub import protocol  # noqa: E402
from hub.config import SOCKET_PATH  # noqa: E402
from hub.security import make_self_signed, verify_signature  # noqa: E402
from hub.service import Hub  # noqa: E402
from hub.targets import HidState  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    failures += not ok
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))


def decode(frames):
    """Split concatenated frames into (type, payload) tuples."""
    return protocol.Parser().feed(b"".join(frames))


# --- HidState -------------------------------------------------------------------

sent = []
hid = HidState(sent.append)
hid.key("kbd", e.KEY_LEFTSHIFT, 1)
hid.key("kbd", e.KEY_A, 1)
hid.key("kbd", e.KEY_A, 2)  # autorepeat: ignored
hid.key("kbd", e.KEY_A, 0)
hid.key("kbd", e.KEY_LEFTSHIFT, 0)
kb = [p for t, p in decode(sent) if t == protocol.MSG_KEYBOARD]
check("keyboard reports", [p[0] for p in kb] == [2, 2, 2, 0] and kb[1][2] == 0x04 and kb[2][2] == 0, str(kb))

sent.clear()
hid.key("kbd", e.KEY_LEFTCTRL, 1)
hid.key("remote", e.KEY_B, 1)
hid.release_source("kbd")  # ctrl from kbd released, B from the other device still held
kb = [p for t, p in decode(sent) if t == protocol.MSG_KEYBOARD]
check("release one source keeps the other", kb[-1][0] == 0 and kb[-1][2] == 0x05, str(kb))

sent.clear()
hid.rel("mouse", e.REL_X, 5)
hid.rel("mouse", e.REL_Y, -3)
hid.syn("mouse")
hid.key("mouse", e.BTN_LEFT, 1)
hid.key("mouse", e.BTN_LEFT, 0)
ms = [struct.unpack("<Bhhbb", p) for t, p in decode(sent) if t == protocol.MSG_MOUSE]
check("mouse move then click", ms == [(0, 5, -3, 0, 0), (1, 0, 0, 0, 0), (0, 0, 0, 0, 0)], str(ms))

sent.clear()
hid.key("kbd", e.KEY_VOLUMEUP, 1)
hid.release_all()
types = [t for t, _ in decode(sent)]
check("media key + release_all", types == [protocol.MSG_CONSUMER, protocol.MSG_RELEASE_ALL], str(types))


# --- Gamepad -> XInput state --------------------------------------------------------

from types import SimpleNamespace as _NS  # noqa: E402

from hub.targets import PadState  # noqa: E402

psent = []
pad = PadState(psent.append)
for t, c, v in [(e.EV_KEY, e.BTN_A, 1), (e.EV_ABS, e.ABS_Y, -32768), (e.EV_ABS, e.ABS_X, 1000),
                (e.EV_ABS, e.ABS_RZ, 255), (e.EV_ABS, e.ABS_HAT0X, 1), (e.EV_SYN, e.SYN_REPORT, 0),
                (e.EV_SYN, e.SYN_REPORT, 0),  # nothing changed: no second frame
                (e.EV_KEY, e.BTN_TRIGGER_HAPPY3, 1), (e.EV_SYN, e.SYN_REPORT, 0)]:
    pad.event(_NS(type=t, code=c, value=v))
pads_out = [struct.unpack("<HBBhhhh", p) for t, p in decode(psent) if t == protocol.MSG_GAMEPAD]
check("gamepad: A, stick up (Y flipped), RT, dpad right",
      pads_out[0] == (0x1000 | 0x0008, 0, 255, 1000, 32767, 0, 0), str(pads_out))
check("gamepad: unchanged state not resent; dpad-as-buttons", len(pads_out) == 2 and pads_out[1][0] == 0x1000 | 0x0008 | 0x0001,
      str(pads_out))


# --- Router ---------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from evdev import EvdevError  # noqa: E402

from hub.router import Router  # noqa: E402


class FakeNode:
    path = "/dev/input/fake-kbd"

    def capabilities(self):
        return {e.EV_LED: [e.LED_CAPSL]}

    def leds(self):
        return [e.LED_NUML]

    def set_led(self, code, value):
        raise EvdevError('no write access to device "/dev/input/fake-kbd"')  # read-only node


class FakeDevice:
    def __init__(self):
        self.id, self.transfer, self.grabbed, self.nodes = "kbd", True, False, {"n": FakeNode()}
        self.kinds = {"keyboard"}

    def grab(self):
        self.grabbed = True

    def ungrab(self):
        self.grabbed = False


rsent = []
target = SimpleNamespace(id="laptop", name="Laptop", connected=True, leds=0, hid=HidState(rsent.append),
                         activate=lambda: None, deactivate=lambda: None, accepts=lambda kinds: True)
kbd_dev = FakeDevice()
pool = SimpleNamespace(devices={"kbd": kbd_dev})
router = Router(lambda: pool, {"laptop": target}, lambda: None)


def press(code, value):
    router.on_event(kbd_dev, SimpleNamespace(type=e.EV_KEY, code=code, value=value))


def hotkey():
    press(e.KEY_LEFTCTRL, 1)
    press(e.KEY_SCROLLLOCK, 1)
    press(e.KEY_SCROLLLOCK, 0)
    press(e.KEY_LEFTCTRL, 0)


press(e.KEY_LEFTCTRL, 1)
press(e.KEY_SCROLLLOCK, 1)
check("switch waits for keys to be released", router.active is None and router.pending == "laptop" and not kbd_dev.grabbed)
press(e.KEY_LEFTCTRL, 0)
press(e.KEY_SCROLLLOCK, 0)  # hotkey key released last
check("one press: Linux -> laptop (despite LED write failure)", router.active == "laptop" and kbd_dev.grabbed)
rsent.clear()
hotkey()
check("one press: laptop -> Linux (despite LED write failure)", router.active is None and not kbd_dev.grabbed)
kb = [p for t, p in decode(rsent) if t == protocol.MSG_KEYBOARD]
check("hotkey itself is never forwarded", all(0x47 not in p[2:] for p in kb), str(kb))  # 0x47 = Scroll Lock
hotkey()
hotkey()
check("repeated toggling stays consistent", router.active is None and not kbd_dev.grabbed)


# --- API ------------------------------------------------------------------------

def agent_ctx(hub_cert_pem, key_pem=None, cert_pem=None):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.load_verify_locations(cadata=hub_cert_pem)  # pin the hub
    if key_pem:
        kp, cp = tmp / f"k{id(key_pem)}", tmp / f"c{id(cert_pem)}"
        kp.write_bytes(key_pem)
        cp.write_bytes(cert_pem)
        ctx.load_cert_chain(cp, kp)
    return ctx


async def local(msg):
    async with unix_connect(str(SOCKET_PATH)) as ws:
        await ws.send(json.dumps(msg))
        return json.loads(await ws.recv())


async def api_tests():
    loop = asyncio.get_running_loop()
    hub = Hub(loop)
    await hub.start()
    try:
        state = await local({"type": "state"})
        check("local state", state["type"] == "state" and state["targets"][0]["id"] == "laptop"
              and state["targets"][0]["connected"] is False, str(state)[:200])
        check("socket is private", oct(SOCKET_PATH.stat().st_mode & 0o777) == "0o600")

        # --- Bluetooth hosts (simulated ESP32 frames) ---
        import time as _time
        from hub.targets import BleChannel
        ble = next(iter(BleChannel._by_port.values()))
        sent_ble = []
        ble.send = sent_ble.append
        ble.link.link = object()  # pretend the serial link is open
        ble.on_frame(protocol.MSG_STATUS, bytes([protocol.STATUS_MOUNTED, 0]))
        a1, a2 = bytes.fromhex("aabbccdd0001"), bytes.fromhex("aabbccdd0002")
        ble.on_frame(protocol.MSG_BLE_HOSTS, a1 + (9).to_bytes(2, "little"))
        ble.on_frame(protocol.MSG_BLE_HOSTS, a1 + (9).to_bytes(2, "little") + a2 + (6).to_bytes(2, "little"))
        bt = {t.address: t for t in hub.targets.values() if t.kind == "ble"}
        check("each Bluetooth host becomes a target", set(bt) == {"aa:bb:cc:dd:00:01", "aa:bb:cc:dd:00:02"}
              and all(t.connected for t in bt.values()), str(list(hub.targets)))
        check("new targets are saved to config", sum(t["kind"] == "ble" for t in hub.config["targets"]) == 2)
        t1, t2 = bt["aa:bb:cc:dd:00:01"], bt["aa:bb:cc:dd:00:02"]
        check("connection interval per host", t1.conn_interval_ms == 11.25 and t2.conn_interval_ms == 7.5)

        sent_ble.clear()
        hub.router.switch(t1.id)
        hub.router.switch(t2.id)
        frames = decode(sent_ble)
        selects = [p.hex() for t, p in frames if t == protocol.MSG_BLE_SELECT | protocol.CHANNEL_BLE]
        check("switching selects the host on the ESP32", selects == [a1.hex(), "000000000000", a2.hex()], str(selects))
        kinds = [t & ~protocol.CHANNEL_BLE for t, _ in frames]
        check("old host is released before it is deselected",
              kinds.index(protocol.MSG_RELEASE_ALL) < kinds.index(protocol.MSG_BLE_SELECT, 1), str(kinds))
        ble.on_frame(protocol.MSG_KBD_LEDS, a2 + b"\x02")
        check("caps lock LED routed to its host", t2.leds == 2 and t1.leds == 0)
        hub.router.switch(None)

        # --- Touchpad: the device behind t1 drives other machines ---
        phone = {"id": "agent-phone", "target": t1.id}
        replies = []
        sent_ble.clear()
        hub.touchpads.pointer(phone, {"target": t2.id, "dx": 5, "dy": -3, "buttons": 1}, replies.append)
        frames = decode(sent_ble)
        mice = [p for t, p in frames if t == protocol.MSG_MOUSE | protocol.CHANNEL_BLE]
        check("touchpad selects the Bluetooth host and moves its mouse",
              ble.selected == t2.address and mice and mice[-1][0] == 1
              and struct.unpack("<hh", mice[-1][1:5]) == (5, -3), str(frames))
        check("touchpad tells the device it works", replies == [{"type": "touchpad", "target": t2.id, "ok": True,
                                                                "reason": ""}], str(replies))
        sent_ble.clear()
        hub.touchpads.pointer(phone, {"target": t2.id, "release": True}, replies.append)
        mice = [p for t, p in decode(sent_ble) if t == protocol.MSG_MOUSE | protocol.CHANNEL_BLE]
        check("touchpad release lets go of the button", mice and mice[-1][0] == 0, str(mice))
        hub.touchpads.pointer(phone, {"target": t1.id, "dx": 1}, replies.append)
        check("touchpad refuses its own device", replies[-1]["ok"] is False and "this device" in replies[-1]["reason"])
        hub.router.held.clear()
        hub.router.switch(t1.id)
        replies.clear()
        sent_ble.clear()
        hub.touchpads.pointer(phone, {"target": t2.id, "dx": 1}, replies.append)
        check("touchpad refuses a second Bluetooth host while the keyboard is on another",
              replies and replies[-1]["ok"] is False and ble.selected == t1.address
              and not any(t == protocol.MSG_MOUSE | protocol.CHANNEL_BLE for t, _ in decode(sent_ble)), str(replies))
        hub.router.switch(None)

        import hub.touchpad as touchpad_mod
        moves = []
        real_mouse = touchpad_mod.VirtualInput
        keys = []
        touchpad_mod.VirtualInput = lambda: SimpleNamespace(
            move=lambda *a: moves.append(a), key=lambda c, d: keys.append((c, d)),
            release=lambda: moves.append("release"), close=lambda: moves.append("close"))
        try:
            hub.touchpads.pointer(phone, {"target": None, "dx": 7, "dy": 2, "wheel": 1, "buttons": 2}, replies.append)
            hub.touchpads.keys(phone, {"target": None, "text": "Hi"}, replies.append)
            await asyncio.sleep(0.1)
            hub.touchpads.agent_gone(phone["id"])
        finally:
            touchpad_mod.VirtualInput = real_mouse
        check("touchpad on Linux uses a virtual device, released when the device leaves",
              moves == [(2, 7, 2, 1, 0), "release", "close"] and hub.touchpads.linux is None, str(moves))
        check("keyboard mode types on Linux", keys == [(e.KEY_LEFTSHIFT, True), (e.KEY_H, True), (e.KEY_H, False),
                                                      (e.KEY_LEFTSHIFT, False), (e.KEY_I, True), (e.KEY_I, False)],
              str(keys))

        from hub.touchpad import key_events
        ev, skipped = key_events(key="c", mods=["ctrl"], layout="uk")
        check("keyboard shortcut: Ctrl+C presses Ctrl, C",
              ev == [(e.KEY_LEFTCTRL, True), (e.KEY_C, True), (e.KEY_C, False), (e.KEY_LEFTCTRL, False)], str(ev))
        ev, skipped = key_events(text='@"é', layout="uk")
        check("keyboard text follows the layout and reports what it can't type",
              ev[:4] == [(e.KEY_LEFTSHIFT, True), (e.KEY_APOSTROPHE, True), (e.KEY_APOSTROPHE, False),
                         (e.KEY_LEFTSHIFT, False)] and ev[5] == (e.KEY_2, True) and skipped == "é", str(ev))

        sent_ble.clear()
        replies.clear()
        hub.touchpads.keys(phone, {"target": t2.id, "key": "enter"}, replies.append)
        await asyncio.sleep(0.1)
        kb = [p for t, p in decode(sent_ble) if t == protocol.MSG_KEYBOARD | protocol.CHANNEL_BLE]
        check("keyboard mode presses keys on a Bluetooth device", [p[2] for p in kb] == [0x28, 0], str(kb))
        hub.touchpads.agent_gone(phone["id"])

        await local({"type": "forget_target", "target": t1.id})
        check("forget removes one host", t1.id not in hub.targets and t2.id in hub.targets
              and not any(t["id"] == t1.id for t in hub.config["targets"]))
        ble.link.link = None

        hub.router.held.clear()  # the test hub sees the real keyboards; a key down would (rightly) defer the switch
        r = await local({"type": "switch", "target": "laptop"})
        state = await local({"type": "state"})
        check("switch to disconnected target refused", state["active"] is None and state["pending"] is None, str(state)[:200])

        pairing = await local({"type": "pair_start", "target": "laptop"})
        check("pair_start", pairing["type"] == "pairing" and pairing["token"], str(pairing)[:200])
        check("ble_confirm without a waiting code is refused", (await local({"type": "ble_confirm"}))["type"] == "error")
        hub_cert = pairing["hub_cert"]

        key_pem, cert_pem = make_self_signed("test-agent")
        ctx = agent_ctx(hub_cert)
        async with connect("wss://127.0.0.1:47211", ssl=ctx) as ws:
            await ws.send(json.dumps({"type": "pair", "token": "wrong", "name": "x", "platform": "windows",
                                      "cert_pem": cert_pem.decode()}))
            check("wrong token rejected", json.loads(await ws.recv())["type"] == "error")
        async with connect("wss://127.0.0.1:47211", ssl=ctx) as ws:
            await ws.send(json.dumps({"type": "pair", "token": pairing["token"], "name": "Laptop agent",
                                      "platform": "windows", "cert_pem": cert_pem.decode()}))
            paired = json.loads(await ws.recv())
        check("pairing", paired["type"] == "paired" and paired["sign_public_key"] == hub.identity.sign_public_b64,
              str(paired)[:200])
        async with connect("wss://127.0.0.1:47211", ssl=ctx) as ws:
            await ws.send(json.dumps({"type": "pair", "token": pairing["token"], "name": "again",
                                      "platform": "windows", "cert_pem": cert_pem.decode()}))
            check("token is single-use", json.loads(await ws.recv())["type"] == "error")

        # Paired agent over mutual TLS.
        async with connect("wss://127.0.0.1:47210", ssl=agent_ctx(hub_cert, key_pem, cert_pem)) as ws:
            await ws.send(json.dumps({"type": "subscribe", "id": 1}))
            st = json.loads(await ws.recv())
            await asyncio.sleep(0.05)
            st2 = await local({"type": "state"})
            check("agent connects with its cert", st["type"] == "state" and st.get("id") == 1)
            check("agent shown online", any(a["online"] and a["name"] == "Laptop agent" for a in st2["agents"]), str(st2["agents"]))
            ids = iter(range(100, 10_000))

            async def ask(msg):
                """Send a request and return its reply, skipping pushes (state/status) that may arrive first —
                the test hub sees the real input devices, so pushes can come at any time."""
                rid = next(ids)
                await ws.send(json.dumps({**msg, "id": rid}))
                while True:
                    reply = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    if reply.get("id") == rid:
                        return reply

            check("agent cannot start pairing", (await ask({"type": "pair_start", "target": "laptop"}))["type"] == "error")

            # Signed update manifest + file download.
            dist = cfg_dir / "dist" / "windows"
            dist.mkdir(parents=True)
            (dist / "VERSION").write_text("1.2.3\n")
            payload = os.urandom(3 * 1024 * 1024 + 17)
            (dist / "agent.bin").write_bytes(payload)
            upd = await ask({"type": "get_update", "platform": "windows"})
            manifest = json.loads(upd["manifest"])
            check("manifest signature valid", verify_signature(paired["sign_public_key"], upd["manifest"].encode(), upd["signature"]))
            tampered = upd["manifest"].replace("1.2.3", "9.9.9").encode()
            check("tampered manifest rejected", not verify_signature(paired["sign_public_key"], tampered, upd["signature"]))
            got, offset = b"", 0
            while True:
                chunk = await ask({"type": "get_file", "platform": "windows", "path": "agent.bin", "offset": offset})
                data = base64.b64decode(chunk["data"])
                if not data:
                    break
                got += data
                offset += len(data)
            entry = next(f for f in manifest["files"] if f["path"] == "agent.bin")
            check("chunked download matches manifest", hashlib.sha256(got).hexdigest() == entry["sha256"] and manifest["version"] == "1.2.3")
            check("path traversal blocked",
                  (await ask({"type": "get_file", "platform": "windows", "path": "../../identity/hub.key"}))["type"] == "error")

        # A certificate the hub never paired must fail the TLS handshake.
        k2, c2 = make_self_signed("intruder")
        try:
            async with connect("wss://127.0.0.1:47210", ssl=agent_ctx(hub_cert, k2, c2)) as ws:
                await ws.send(json.dumps({"type": "state"}))
                await ws.recv()
            check("unpaired cert rejected", False)
        except Exception:
            check("unpaired cert rejected", True)

        # Unpairing revokes access.
        agent_id = paired["agent_id"]
        await local({"type": "unpair", "agent": agent_id})
        try:
            async with connect("wss://127.0.0.1:47210", ssl=agent_ctx(hub_cert, key_pem, cert_pem)) as ws:
                await ws.send(json.dumps({"type": "state"}))
                await ws.recv()
            check("unpaired agent loses access", False)
        except Exception:
            check("unpaired agent loses access", True)
    finally:
        await hub.stop()


# --- OLED display -----------------------------------------------------------------

from hub import display  # noqa: E402

M = display.Machine
machines = (M(None, "Linux PC", "pc", True, "this machine"), M("laptop", "Laptop", "laptop", True, "USB"),
            M("bt-1", "Galaxy Tab S8 Ultra with a long name", "tablet", False, "offline"))
scene = display.Scene(machines, None)
shot = display.draw_screen(scene)
check("display: picture is 128x64, one bit", shot.size == (128, 64) and shot.mode == "1")
check("display: something is drawn", any(shot.getdata()))
check("display: holder changes the picture", display.draw_screen(display.Scene(machines, "laptop")).tobytes() != shot.tobytes())
check("display: offline machine drawn too (long name cut)", any(display.draw_screen(display.Scene(machines, "bt-1")).getdata()))
check("display: pairing code panel", display.Scene(machines, None, True, "123456").panel == ("pairing", "123456") and
      display.draw_screen(display.Scene(machines, None, True, "123456")).tobytes() != shot.tobytes())
check("display: slide differs from both ends", display.draw_screen(display.Scene(machines, "laptop"), scene, 0.5).tobytes()
      not in (shot.tobytes(), display.draw_screen(display.Scene(machines, "laptop")).tobytes()))
packed = display.pack(shot)
check("display: 1024 bytes in SSD1306 page layout", len(packed) == 1024)
dot = display.Image.new("1", (128, 64), 0)
dot.putpixel((5, 10), 1)  # page 1, bit 2
check("display: pixel lands in page byte, LSB on top", display.pack(dot)[128 + 5] == 0b100 and sum(display.pack(dot)) == 4)
parts = display.chunks(packed)
check("display: 32 chunks of index + 32 bytes", len(parts) == 32 and all(len(c) == 33 and c[0] == i for i, c in enumerate(parts)) and
      b"".join(c[1:] for c in parts) == packed)

# --- Bluetooth pairing approval ---------------------------------------------------------

from hub.targets import BleChannel  # noqa: E402

psent = []
chan = object.__new__(BleChannel)
chan.passkey, chan.on_state = "123456", lambda: None
chan.send_frame = lambda t, p: psent.append((t, p))
chan.confirm(True)
check("pairing: approval sends the confirm frame and forgets the code",
      psent == [(protocol.MSG_BLE_CONFIRM, b"\x01")] and chan.passkey is None, str(psent))
chan.passkey = "654321"
chan.confirm(False)
check("pairing: rejection sends 0", psent[-1] == (protocol.MSG_BLE_CONFIRM, b"\x00"))
try:
    chan.confirm(True)
    check("pairing: nothing to approve is an error", False)
except ValueError:
    check("pairing: nothing to approve is an error", True)

dsent = []
fake_channel = SimpleNamespace(passkey=None, pairing=False, send_frame=lambda t, p: dsent.append((t, p)))
fake_hub = SimpleNamespace(
    targets={"laptop": SimpleNamespace(id="laptop", name="Laptop", kind="pico", connected=True)},
    router=SimpleNamespace(active="laptop", pending=None), bluetooth_channels=lambda: [fake_channel],
    loop=SimpleNamespace(call_later=lambda *a: SimpleNamespace(cancel=lambda: None)))
feed = display.Display(fake_hub)
feed.update()
check("display: first update sends a whole picture", [t for t, _ in dsent] == [protocol.MSG_DISPLAY] * 32 and
      [p[0] for _, p in dsent] == list(range(32)), str(len(dsent)))
dsent.clear()
feed.update()
check("display: unchanged state not resent", dsent == [])
feed.update(resend=True)
check("display: resend repeats it", len(dsent) == 32)
dsent.clear()
fake_hub.router.active = None
feed.update()
check("display: a new holder slides in (first step sent, rest scheduled)", len(dsent) == 32 and feed._slide is not None)


asyncio.run(api_tests())
print("ALL PASSED" if not failures else f"{failures} FAILED")
sys.exit(1 if failures else 0)
