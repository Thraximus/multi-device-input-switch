#!/usr/bin/env python3
"""Agent <-> hub end to end on one machine: pairing, clipboard sync, bootstrap download.

Run from the repo root: python3 tests/test_agent.py
Uses a throwaway hub (temporary dirs, own ports, no serial port) and in-memory clipboards.
"""
import asyncio
import json
import os
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="agenttest-"))
os.environ["XDG_CONFIG_HOME"] = str(tmp / "config")
os.environ["XDG_RUNTIME_DIR"] = str(tmp / "run")
os.environ["SWITCHER_API_PORT"] = "47310"  # not the real hub's ports
os.environ["SWITCHER_NOTIFICATION_SECONDS"] = "1"  # removals wait this long; keep the test quick
os.environ["SWITCHER_PAIRING_PORT"] = "47311"
os.environ["SWITCHER_ENV_FILE"] = str(tmp / "no.env")  # the repo's .env doesn't apply to tests
(tmp / "run").mkdir()
cfg_dir = tmp / "config" / "pi-switcher-hub"
cfg_dir.mkdir(parents=True)
(cfg_dir / "config.json").write_text(json.dumps({
    "targets": [{"id": "laptop", "name": "Laptop", "kind": "pico", "port": str(tmp / "no-such-tty")}]
}))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import state as agent_state  # noqa: E402
from agent.client import Agent  # noqa: E402
from agent.clipboard_mem import MemoryClipboard  # noqa: E402
from agent.pairing import pair  # noqa: E402
from hub.service import Hub  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    failures += not ok
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))


class FakeLinuxClipboard:
    available = True

    def __init__(self, on_change):
        self.on_change = on_change
        self.item = None

    def start(self):
        pass

    async def stop(self):
        pass

    async def set(self, mime, data):
        self.item = (mime, data)

    def user_copies(self, mime, data):  # what wl-paste --watch would report
        self.item = (mime, data)
        self.on_change(mime, data)


class FakeDesktop:
    """Stands in for hub.notifications (D-Bus); records what the hub asked it to do."""
    def __init__(self):
        self.calls = []

    def start(self):
        pass

    async def stop(self):
        pass

    def posted(self, agent, msg):
        self.calls.append(("posted", agent["id"], msg.get("key")))
        self.last = msg

    def removed(self, agent, key):
        self.calls.append(("removed", agent["id"], key))

    def close_all(self, agent_id):
        self.calls.append(("close_all", agent_id, None))

    def mirror_action(self, msg):
        self.calls.append(("mirror_action", msg.get("id"), msg.get("action")))

    def call(self, agent, msg):
        self.calls.append(("call", agent["id"], msg.get("state")))


class FakeTray:
    def __init__(self):
        self.status = None
        self.connected = False
        self.updates = []
        self.balloons = []

    def set_status(self, status):
        self.status = status
        self.updates.append(status)

    def set_connected(self, connected):
        self.connected = connected

    def close(self):
        pass

    def balloon(self, title, text, picture=None):
        self.balloons.append((title, text))

    def clear_balloon(self):
        self.balloons.append(None)


def tray_model_tests():
    from agent import tray
    status = {"hub": "linux-pc", "self": "laptop", "holder": "laptop", "pending": None,
              "targets": [{"id": "laptop", "name": "Laptop", "kind": "pico", "connected": True},
                          {"id": "bt-1", "name": "Phone", "kind": "ble", "connected": True},
                          {"id": "bt-2", "name": "Tablet", "kind": "ble", "connected": False}]}
    check("tray icon: devices here", tray.icon_key(status, True) == tray.HERE)
    check("tray icon: offline", tray.icon_key(status, False) == tray.OFFLINE)
    check("tray icon: on the Linux PC", tray.icon_key({**status, "holder": None}, True) == tray.LINUX)
    check("tray tooltip", tray.tooltip(status, True) == "Switcher Hub — devices on Laptop (this machine)")
    items = tray.menu(status, True, "1.0")
    targets = [i for i in items if "target" in i]
    check("tray menu: Linux + every target, holder checked, unconnected greyed",
          [(i["target"], i["checked"], i["enabled"]) for i in targets]
          == [(None, False, True), ("laptop", True, True), ("bt-1", False, True), ("bt-2", False, False)])
    check("tray menu offline: no switching", not any("target" in i for i in tray.menu(None, False, "1.0")))
    icons = tray.write_icons(tmp / "icons")
    check("tray icons written", all(p.stat().st_size > 0 for p in icons.values()) and len(icons) == 6)


def new_agent_state(name):
    os.environ["PI_SWITCHER_AGENT_HOME"] = str(tmp / name)
    return agent_state.State()


async def local_req(hub, msg):
    from websockets.asyncio.client import unix_connect
    from hub.config import SOCKET_PATH
    async with unix_connect(str(SOCKET_PATH)) as ws:
        await ws.send(json.dumps(msg))
        return json.loads(await ws.recv())


async def wait_for(cond, timeout=3):
    for _ in range(int(timeout / 0.05)):
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


async def main():
    hub = Hub(asyncio.get_running_loop())
    hub.notifications = FakeDesktop()  # never pop real notifications from tests
    linux = FakeLinuxClipboard(hub.clipboard._from_linux)
    hub.clipboard.linux = linux
    await hub.start()
    tasks = []
    try:
        # --- bootstrap download over HTTPS, token-gated ---
        pairing = await hub.api.pair_start("laptop")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        def get(path):
            try:
                with urllib.request.urlopen(f"https://127.0.0.1:47311{path}", context=ctx, timeout=5) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, b""
        status, body = await asyncio.to_thread(get, f"/agent/{pairing['token']}/bootstrap.ps1")
        check("bootstrap script served with token", status == 200 and pairing["token"].encode() in body
              and hub.identity.fingerprint.encode() in body, str(status))
        status, _ = await asyncio.to_thread(get, "/agent/wrong-token/bootstrap.ps1")
        check("bootstrap refused without valid token", status == 404, str(status))

        # --- two agents pair ---
        s1 = new_agent_state("agent1")
        await pair(s1, "127.0.0.1", 47311, pairing["token"], pairing["hub_fingerprint"])
        p2 = await hub.api.pair_start("laptop")
        s2 = new_agent_state("agent2")
        await pair(s2, "127.0.0.1", 47311, p2["token"], p2["hub_fingerprint"])
        check("agents paired", s1.paired and s2.paired and s1.hub["api_port"] == 47310)

        bad = new_agent_state("agent3")
        p3 = await hub.api.pair_start("laptop")
        try:
            await pair(bad, "127.0.0.1", 47311, p3["token"], "00" * 32)
            check("wrong hub fingerprint aborts pairing", False)
        except SystemExit:
            check("wrong hub fingerprint aborts pairing", True)

        c1, c2 = MemoryClipboard(), MemoryClipboard()
        a1 = Agent(s1, c1, platform_name="windows", check_updates=False)
        a2 = Agent(s2, c2, platform_name="windows", check_updates=False)
        tasks = [asyncio.ensure_future(a1.run()), asyncio.ensure_future(a2.run())]
        ok = await wait_for(lambda: a1.connected.is_set() and a2.connected.is_set())
        check("agents connect over mutual TLS", ok)
        state = hub.state()
        check("agents online with clipboard capability",
              all(a["online"] and "clipboard" in a["capabilities"] for a in state["agents"]), str(state["agents"]))
        check("audio seam present in state", state["audio"] == {"output": None, "implemented": False, "sources": []})

        # --- clipboard: Linux -> agents ---
        linux.user_copies("text/plain;charset=utf-8", "hello from linux ✓".encode())
        ok = await wait_for(lambda: c1.get() and c2.get() and c1.get()[1] == c2.get()[1] == "hello from linux ✓".encode())
        check("Linux copy reaches both agents", ok, f"{c1.get()} {c2.get()}")

        # --- agent1 -> Linux and agent2, no echo back to agent1 ---
        seq_before = c1.sequence()
        c1.set("text/plain;charset=utf-8", b"from laptop")
        ok = await wait_for(lambda: linux.item == ("text/plain;charset=utf-8", b"from laptop")
                            and c2.get() == ("text/plain;charset=utf-8", b"from laptop"))
        check("agent copy reaches Linux and the other agent", ok, f"{linux.item} {c2.get()}")
        await asyncio.sleep(0.6)
        check("no echo back to the sender", c1.sequence() == seq_before + 1, f"{seq_before} -> {c1.sequence()}")

        # --- images ---
        png = b"\x89PNG\r\n\x1a\n" + os.urandom(300000)
        c2.set("image/png", png)
        ok = await wait_for(lambda: linux.item == ("image/png", png) and c1.get() == ("image/png", png), 5)
        check("image (300 KB) syncs", ok)

        # --- clipboard history ---
        TEXT = "text/plain;charset=utf-8"
        hist = lambda: hub.state()["clipboard"]["history"]  # noqa: E731
        check("history newest first, with origin and preview",
              hist()[0]["mime"] == "image/png" and hist()[0]["origin"] not in ("linux", None)
              and any(h.get("preview") == "from laptop" for h in hist()), str([h.get("preview") for h in hist()]))
        c1.secret = True
        c1.set(TEXT, b"hunter2", secret=True)
        ok = await wait_for(lambda: linux.item == (TEXT, b"hunter2") and c2.get() == (TEXT, b"hunter2"))
        check("secret from an agent still syncs, marked secret", ok and c2.secret)
        check("secret never enters the history", not any(h.get("preview") == "hunter2" for h in hist()))
        hub.clipboard._from_linux(TEXT, b"pw from keepassxc", True)
        check("secret from Linux (password manager hint) kept out too",
              not any("keepassxc" in (h.get("preview") or "") for h in hist()))
        old = next(h for h in hist() if h.get("preview") == "from laptop")
        r = await local_req(hub, {"type": "clipboard_restore", "item": old["id"]})
        ok = await wait_for(lambda: linux.item == (TEXT, b"from laptop") and c1.get() == (TEXT, b"from laptop")
                            and c2.get() == (TEXT, b"from laptop"))
        check("restoring an entry puts it on every machine", r["type"] == "ok" and ok)
        check("restored entry moves to the top", hist()[0]["id"] == old["id"])
        r = await local_req(hub, {"type": "clipboard_get", "item": old["id"]})
        check("entry data fetchable (thumbnails)", r.get("data") == "ZnJvbSBsYXB0b3A=")
        for i in range(12):
            hub.clipboard._from_linux(TEXT, f"item {i}".encode())
        check("history keeps the last 10", len(hist()) == 10 and hist()[0]["preview"] == "item 11")
        await local_req(hub, {"type": "clipboard_remove", "item": hist()[0]["id"]})
        check("remove one entry", len(hist()) == 9 and hist()[0]["preview"] == "item 10")
        await local_req(hub, {"type": "clipboard_remove"})
        check("clear history", hist() == [])
        r = await local_req(hub, {"type": "clipboard_restore", "item": "nope"})
        check("unknown entry is an error", r["type"] == "error")
        # --- apps with switchable features (Android): config pushed, per-device switches, theme ---
        from websockets.asyncio.client import connect as ws_connect
        from agent.tls import client_context
        async with ws_connect(f"wss://127.0.0.1:47310", ssl=client_context(s2)) as phone:
            await phone.send(json.dumps({"type": "hello", "id": 1, "name": "phone", "platform": "android",
                                         "version": "0.2.0", "capabilities": ["tray", "clipboard"],
                                         "features": [{"id": "clipboard", "title": "Clipboard", "default": True},
                                                      {"id": "notifications", "title": "Notifications"}]}))
            msgs = [json.loads(await asyncio.wait_for(phone.recv(), 3)) for _ in range(2)]
            cfg = next((m for m in msgs if m["type"] == "config"), None)
            check("config pushed on hello (defaults applied)",
                  cfg is not None and cfg["features"] == {"clipboard": True, "notifications": False}
                  and cfg["theme"] == "material", str(msgs))
            phone_id = next(a["id"] for a in hub.state()["agents"] if a.get("version") == "0.2.0")
            r = await local_req(hub, {"type": "set_agent_feature", "agent": phone_id, "feature": "notifications",
                                      "enabled": True})
            pushed = None
            for _ in range(5):
                m = json.loads(await asyncio.wait_for(phone.recv(), 3))
                if m["type"] == "config":
                    pushed = m
                    break
            check("feature switch pushed to the device", r["type"] == "ok" and pushed is not None
                  and pushed["features"]["notifications"] is True)
            entry = next(a for a in hub.state()["agents"] if a["id"] == phone_id)
            check("state lists the device's features and version", entry["version"] == "0.2.0" and
                  {f["id"]: f["enabled"] for f in entry["features"]} == {"clipboard": True, "notifications": True})
            await phone.send(json.dumps({"type": "capabilities", "capabilities": ["tray"]}))
            ok = await wait_for(lambda: "clipboard" not in next(a for a in hub.state()["agents"]
                                                                if a["id"] == phone_id)["capabilities"])
            check("capabilities follow the device (clipboard off → not routed)", ok)
            await local_req(hub, {"type": "set_appearance", "pc_theme": "cogitator"})
            pushed = None
            for _ in range(5):
                m = json.loads(await asyncio.wait_for(phone.recv(), 3))
                if m["type"] == "config":
                    pushed = m
                    break
            check("devices follow the PC's Cogitator theme ('auto')", pushed is not None and pushed["theme"] == "cogitator")
            # notifications only reach the desktop while the device runs the feature
            await phone.send(json.dumps({"type": "notification", "key": "k1", "app": "Chat", "title": "hi"}))
            await asyncio.sleep(0.2)
            check("notification ignored while the feature is off",
                  not any(c[0] == "posted" for c in hub.notifications.calls))
            await phone.send(json.dumps({"type": "capabilities", "capabilities": ["tray", "notifications"]}))
            await phone.send(json.dumps({"type": "notification", "key": "k1", "app": "Chat", "title": "hi"}))
            await phone.send(json.dumps({"type": "notification_removed", "key": "k1"}))
            ok = await wait_for(lambda: [c[0] for c in hub.notifications.calls if c[0] in ("posted", "removed")] == ["posted", "removed"])
            check("replay flag reaches the desktop side", "replay" not in hub.notifications.last)
            check("notification shown and removed on the desktop", ok, str(hub.notifications.calls))
            hub.api.send_to_agent(phone_id, {"type": "notification_action", "key": "k1", "action": "dismiss"})
            got = None
            for _ in range(5):
                m = json.loads(await asyncio.wait_for(phone.recv(), 3))
                if m["type"] == "notification_action":
                    got = m
                    break
            check("dismiss from the desktop reaches the device", got is not None and got["key"] == "k1")
            await phone.send(json.dumps({"type": "capabilities", "capabilities": ["tray"]}))
            ok = await wait_for(lambda: ("close_all", phone_id, None) in hub.notifications.calls)
            check("turning the feature off clears its desktop notifications", ok)
            # mirroring: another device with "notification_mirror" gets a copy; its actions reach the source
            from hub.notifications import DesktopNotifications
            fake_desktop = hub.notifications
            hub.config.data["desktop_notifications"] = False  # routing only; nothing on the real desktop
            hub.notifications = DesktopNotifications(hub)
            async with ws_connect(f"wss://127.0.0.1:47310", ssl=client_context(s1)) as tablet:
                await tablet.send(json.dumps({"type": "hello", "id": 1, "name": "tablet", "platform": "android",
                                              "capabilities": ["notification_mirror"]}))
                await asyncio.sleep(0.3)

                async def next_of(ws, kind):
                    for _ in range(8):
                        m = json.loads(await asyncio.wait_for(ws.recv(), 3))
                        if m["type"] == kind:
                            return m
                    return None
                await phone.send(json.dumps({"type": "capabilities", "capabilities": ["tray", "notifications"]}))
                await phone.send(json.dumps({"type": "notification", "key": "k9", "app": "Chat", "title": "yo",
                                             "text": "hi", "reply": True}))
                mirror = await next_of(tablet, "mirror_notification")
                check("notification mirrored to the other device", mirror is not None and mirror["title"] == "yo"
                      and mirror["device"] and mirror["reply"] is True, str(mirror))
                await tablet.send(json.dumps({"type": "mirror_action", "id": mirror["id"], "action": "reply",
                                              "text": "sure"}))
                act = await next_of(phone, "notification_action")
                check("reply on the mirror reaches the source device", act is not None and act["key"] == "k9"
                      and act["text"] == "sure")
                await phone.send(json.dumps({"type": "notification_removed", "key": "k9"}))
                gone = await next_of(tablet, "mirror_notification_removed")
                check("removed on the source → removed on the mirror", gone is not None and gone["id"] == mirror["id"])
                await phone.send(json.dumps({"type": "notification", "key": "old", "title": "x", "replay": True}))
                try:
                    m = json.loads(await asyncio.wait_for(tablet.recv(), 0.5))
                except asyncio.TimeoutError:
                    m = None
                check("replays never pop up on mirrors", m is None or m["type"] != "mirror_notification")
            hub.notifications = fake_desktop
            # calls: routed to the desktop only while the device runs the feature
            await phone.send(json.dumps({"type": "call", "state": "ringing", "number": "123"}))
            await asyncio.sleep(0.2)
            check("call ignored while the calls feature is off", ("call", phone_id, "ringing") not in hub.notifications.calls)
            await phone.send(json.dumps({"type": "capabilities", "capabilities": ["tray", "calls"]}))
            await phone.send(json.dumps({"type": "call", "state": "ringing", "number": "123", "name": "Ann"}))
            ok = await wait_for(lambda: ("call", phone_id, "ringing") in hub.notifications.calls)
            check("incoming call reaches the desktop", ok)
            hub.api.send_to_agent(phone_id, {"type": "call_action", "action": "answer"})
            got = None
            for _ in range(8):
                m = json.loads(await asyncio.wait_for(phone.recv(), 3))
                if m["type"] == "call_action":
                    got = m
                    break
            check("Answer on the desktop reaches the phone", got is not None and got["action"] == "answer")
            await phone.send(json.dumps({"type": "capabilities", "capabilities": ["tray"]}))
            ok = await wait_for(lambda: hub.notifications.calls[-1] == ("call", phone_id, "ended"))
            check("turning calls off takes a ringing call off the desktop", ok, str(hub.notifications.calls[-3:]))
            # app calls (WhatsApp/LINE): the desktop's Answer carries the call notification's key back
            from hub.notifications import DesktopNotifications as _DN
            real = _DN(hub)
            real.call({"id": phone_id, "target": "bt-7d7c"}, {"state": "ringing", "key": "0|com.whatsapp|7", "app": "WhatsApp"})
            real.calls[phone_id] = 4242  # as if the desktop had shown it with this id

            class _Sig:
                def __init__(self, member, body):
                    from jeepney import HeaderFields
                    self.header = type("H", (), {"fields": {HeaderFields.member: member}})()
                    self.body = body
            real._signal(_Sig("ActionInvoked", (4242, "decline")))
            got = None
            for _ in range(8):
                m = json.loads(await asyncio.wait_for(phone.recv(), 3))
                if m["type"] == "call_action":
                    got = m
                    break
            check("app call: Decline on the desktop carries the app's call key", got is not None
                  and got.get("key") == "0|com.whatsapp|7" and got["action"] == "decline", str(got))
            # per-feature settings (notification app filter) and the device's app list
            await phone.send(json.dumps({"type": "notification_apps", "apps": [
                {"package": "com.whatsapp", "label": "WhatsApp"}, {"package": "com.google.android.gm", "label": "Gmail"}]}))
            ok = await wait_for(lambda: [a["package"] for f in next(a for a in hub.state()["agents"]
                                                                    if a["id"] == phone_id)["features"]
                                         if f["id"] == "notifications" for a in f["catalog"]] == ["com.google.android.gm", "com.whatsapp"])
            check("device's app list reaches the GUI state (sorted by name)", ok)
            filt = {"show_new_apps": False, "apps": {"com.whatsapp": True}}
            r = await local_req(hub, {"type": "set_feature_settings", "agent": phone_id, "feature": "notifications",
                                      "settings": filt})
            pushed = None
            for _ in range(5):
                m = json.loads(await asyncio.wait_for(phone.recv(), 3))
                if m["type"] == "config":
                    pushed = m
                    break
            check("app filter pushed to the device in its config", r["type"] == "ok" and pushed is not None
                  and pushed["settings"]["notifications"] == filt)
            r = await local_req(hub, {"type": "set_agent_feature", "agent": phone_id, "feature": "bogus", "enabled": True})
            check("unknown feature refused", r["type"] == "error")
        c2.set("image/png", png)  # the image is current again for the tests below
        await wait_for(lambda: hub.clipboard.current.data == png)

        # --- two-way: a gamepad on the agent becomes a virtual Xbox pad on Linux ---
        import evdev
        from evdev import ecodes as e
        from agent.gamepad_mem import MemoryPads
        for t in tasks:
            t.cancel()
        pads = MemoryPads()
        pads.plug("xinput0", "Xbox 360 controller (slot 1)")
        ap = Agent(s1, MemoryClipboard(), platform_name="windows", check_updates=False, pads=pads)
        tasks = [asyncio.ensure_future(ap.run())]
        dev_id = f"remote:{s1.hub['agent_id']}:xinput0"
        ok = await wait_for(lambda: dev_id in hub.remote.pads and "xinput0" in ap.forwarded, 5)
        check("remote pad forwarded while devices are on Linux", ok, str(list(hub.remote.devices)))
        st = hub.state()
        check("remote pad listed with its origin", any(d["id"] == dev_id and d["origin"] == "laptop" for d in st["devices"]))
        await asyncio.sleep(0.5)
        nodes = [evdev.InputDevice(p) for p in evdev.list_devices()]
        vpad = next((n for n in nodes if n.name.startswith("pi-switcher-hub Xbox")), None)
        check("virtual Xbox 360 pad exists", vpad is not None and (vpad.info.vendor, vpad.info.product) == (0x045E, 0x028E))
        check("loop guard ignores the virtual pad", not any(d["name"].startswith("pi-switcher-hub") for d in st["devices"]))
        if vpad is not None:
            vpad.grab()
            pads.pads["xinput0"] = (0x1000 | 0x0001, 200, 0, 12000, 30000, 0, 0)  # A + dpad up, LT, sticks
            await asyncio.sleep(0.3)
            events = {}
            try:
                for ev in vpad.read():
                    events[(ev.type, ev.code)] = ev.value
            except BlockingIOError:
                pass
            check("buttons/triggers/sticks arrive", events.get((e.EV_KEY, e.BTN_A)) == 1 and events.get((e.EV_ABS, e.ABS_Z)) == 200
                  and events.get((e.EV_ABS, e.ABS_X)) == 12000 and events.get((e.EV_ABS, e.ABS_Y)) == -30001
                  and events.get((e.EV_ABS, e.ABS_HAT0Y)) == -1, str(events))
            vpad.ungrab()
            # A game's rumble effect on the virtual pad reaches the real controller. The "game" must be a
            # separate process: the upload ioctl blocks without releasing the GIL until the hub answers.
            game = await asyncio.create_subprocess_exec(sys.executable, "-c", f"""
import time, evdev
from evdev import ecodes as e, ff
d = evdev.InputDevice({vpad.path!r})
fx = d.upload_effect(ff.Effect(e.FF_RUMBLE, -1, 0, ff.Trigger(0, 0), ff.Replay(500, 0),
                               ff.EffectType(ff_rumble_effect=ff.Rumble(40000, 20000))))
d.write(e.EV_FF, fx, 1)
time.sleep(0.5)
""")
            await asyncio.wait_for(game.wait(), 10)
            ok = await wait_for(lambda: ("xinput0", 40000, 20000) in pads.rumbles)
            check("rumble reaches the real controller", ok, str(pads.rumbles))
        await local_req(hub, {"type": "set_transfer", "device": dev_id, "enabled": False})
        ok = await wait_for(lambda: dev_id not in hub.remote.pads and not ap.forwarded)
        check("transfer off stops forwarding", ok)
        await local_req(hub, {"type": "set_transfer", "device": dev_id, "enabled": True})
        pads.unplug("xinput0")
        ok = await wait_for(lambda: dev_id not in hub.remote.devices, 4)
        check("unplugged pad disappears", ok)

        # --- a late agent gets the current clipboard on connect ---
        for t in tasks:
            t.cancel()
        c3 = MemoryClipboard()
        a3 = Agent(s1, c3, platform_name="windows", check_updates=False)
        tasks = [asyncio.ensure_future(a3.run())]
        ok = await wait_for(lambda: c3.get() == ("image/png", png), 5)
        check("reconnecting agent receives current clipboard", ok)

        # --- tray: status pushed to agents that have one; switching from the tray ---
        for t in tasks:
            t.cancel()
        tray_ui = FakeTray()
        from agent.features import FeatureSet, NotificationMirror
        feats = FeatureSet([NotificationMirror(tray_ui)], tmp / "agent-features.json")
        at = Agent(s1, MemoryClipboard(), platform_name="windows", check_updates=False, tray=tray_ui, features=feats)
        switched = []
        real_switch = hub.router.switch
        hub.router.switch = lambda target: switched.append(target)
        tasks = [asyncio.ensure_future(at.run())]
        ok = await wait_for(lambda: tray_ui.status is not None and tray_ui.connected)
        check("tray agent gets holder status", ok and tray_ui.status["holder"] is None
              and tray_ui.status["self"] == "laptop" and [t["id"] for t in tray_ui.status["targets"]] == ["laptop"])
        check("non-tray agents get no status", a3 is not None and "tray" not in a3.capabilities)
        await asyncio.to_thread(at.switch, "laptop")  # called from the tray's own thread
        ok = await wait_for(lambda: switched == ["laptop"])
        check("tray switch reaches the hub", ok, str(switched))
        hub.router.switch = real_switch
        before = len(tray_ui.updates)
        hub.rename_target("laptop", "Work laptop")
        ok = await wait_for(lambda: tray_ui.status["targets"][0]["name"] == "Work laptop")
        check("status re-sent when it changes", ok)
        hub.state_changed()  # nothing tray-relevant changed
        await asyncio.sleep(0.3)
        check("unchanged status not re-sent", len(tray_ui.updates) == before + 1, f"{before} -> {len(tray_ui.updates)}")

        # --- Windows agent features: switchable from the PC, mirror shows notifications on the tray ---
        laptop_id = next(a["id"] for a in hub.state()["agents"] if a["online"] and a.get("features"))
        entry = next(a for a in hub.state()["agents"] if a["id"] == laptop_id)
        check("Windows agent lists its features (mirror off by default)",
              [(f["id"], f["enabled"]) for f in entry["features"]] == [("notification_mirror", False)])
        await local_req(hub, {"type": "set_agent_feature", "agent": laptop_id, "feature": "notification_mirror",
                              "enabled": True})
        ok = await wait_for(lambda: "notification_mirror" in next(
            a for a in hub.state()["agents"] if a["id"] == laptop_id)["capabilities"])
        check("switching it on from the PC starts it on the agent", ok and feats.enabled["notification_mirror"])
        hub.api.send_to_agents({"type": "mirror_notification", "id": "m1", "device": "Phone", "app": "Chat",
                                "title": "yo", "messages": [{"sender": "Ann", "text": "hi"}]},
                               capability="notification_mirror")
        ok = await wait_for(lambda: tray_ui.balloons and tray_ui.balloons[-1] is not None)
        check("mirrored notification shown on the tray", ok and tray_ui.balloons[-1][0] == "yo"
              and "Ann: hi" in tray_ui.balloons[-1][1] and "Chat · Phone" in tray_ui.balloons[-1][1], str(tray_ui.balloons))
        hub.api.send_to_agents({"type": "mirror_notification_removed", "id": "m1"}, capability="notification_mirror")
        ok = await wait_for(lambda: tray_ui.balloons[-1] is None)
        check("removed on the source → taken off the tray", ok)
        # toast path (Windows): a toast's Dismiss button reports back to the hub as a mirror action
        mirror = feats.features["notification_mirror"]

        class FakeToasts:
            def __init__(self):
                self.shown = []

            def show(self, mid, msg):
                self.shown.append(mid)
                mirror._action(mid, "dismiss", "")  # the user pressed "Dismiss on Phone"

            def remove(self, mid):
                pass

            def clear(self):
                pass
        mirror.toasts = FakeToasts()
        hub.api.send_to_agents({"type": "mirror_notification", "id": "m2", "device": "Phone", "app": "Chat",
                                "title": "t"}, capability="notification_mirror")
        ok = await wait_for(lambda: ("mirror_action", "m2", "dismiss") in hub.notifications.calls)
        check("toast button press reaches the hub", ok and mirror.toasts.shown == ["m2"], str(hub.notifications.calls))
        mirror.toasts = None
        await local_req(hub, {"type": "set_agent_feature", "agent": laptop_id, "feature": "notification_mirror",
                              "enabled": False})
        ok = await wait_for(lambda: not feats.enabled["notification_mirror"])
        check("switching it off stops it", ok)
    finally:
        for t in tasks:
            t.cancel()
        await hub.stop()


tray_model_tests()
asyncio.run(main())
print("ALL PASSED" if not failures else f"{failures} FAILED")
sys.exit(1 if failures else 0)
