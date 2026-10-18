"""Device notifications on the Linux desktop (org.freedesktop.Notifications over D-Bus, via jeepney), and
mirrored to every other device that runs the "notification_mirror" feature.

A device with the "notifications" feature sends {"type": "notification", key, app, package, title, text,
reply, icon?} and {"type": "notification_removed", key}. Each becomes a desktop notification labelled with
the device's name, with "Dismiss on <device>" and, where the app accepts replies, Plasma's inline "Reply".
Pressing those sends {"type": "notification_action", key, action: "dismiss"|"reply", text} back.

Mirroring: other devices get {"type": "mirror_notification", id, device, app, title, text, messages, reply,
icon?, image?} and {"type": "mirror_notification_removed", id}; their {"type": "mirror_action", id, action,
text} is routed to the device the notification came from. Mirrored copies are never forwarded again (the
receiving app posts them itself and skips its own notifications).
"""
import asyncio
import base64
import hashlib
import html
import logging
import os
import re
from pathlib import Path

from jeepney import DBusAddress, HeaderFields, MatchRule, message_bus, new_method_call
from jeepney.wrappers import unwrap_msg
from jeepney.io.asyncio import Proxy, open_dbus_router

log = logging.getLogger(__name__)

NOTIFICATIONS = DBusAddress("/org/freedesktop/Notifications", bus_name="org.freedesktop.Notifications",
                            interface="org.freedesktop.Notifications")
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "pi-switcher-hub"
ICON_DIR = CACHE / "icons"      # app icons, per package
IMAGE_DIR = CACHE / "images"    # notification pictures, per notification


def save_app_icons(apps):
    """Store the icons from a device's app list (the GUI's filter shows them); returns the list without
    icon data: [{package, label}]."""
    out = []
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    for app in apps[:1000]:
        package = re.sub(r"[^A-Za-z0-9._-]", "_", str(app.get("package", "")))[:100]
        if not package:
            continue
        if app.get("icon"):
            try:
                (ICON_DIR / f"{package}.png").write_bytes(base64.b64decode(app["icon"]))
            except (OSError, ValueError):
                pass
        out.append({"package": package, "label": str(app.get("label", package))[:80]})
    return sorted(out, key=lambda a: a["label"].lower())


class DesktopNotifications:
    def __init__(self, hub):
        self.hub = hub
        self.router = None
        self._task = None
        self.shown = {}    # (agent id, key) -> desktop notification id
        self.by_id = {}    # desktop notification id -> (agent id, key)
        self.calls = {}     # agent id -> desktop notification id of its ringing call
        self.call_keys = {}  # agent id -> notification key of a ringing app call (WhatsApp, LINE, …)
        self.mirrored = {}  # (agent id, key) -> mirror id (sent to other devices)
        self.by_mirror = {}  # mirror id -> (agent id, key)

    @property
    def desktop_enabled(self):
        return self.hub.config.data.get("desktop_notifications", True)

    def set_desktop(self, enabled):
        self.hub.config.data["desktop_notifications"] = bool(enabled)
        self.hub.config.save()
        if not enabled:
            for nid in list(self.by_id):
                self._close(nid)

    def start(self):
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self):
        if self._task:
            self._task.cancel()

    async def _run(self):
        rule = MatchRule(type="signal", interface=NOTIFICATIONS.interface, path=NOTIFICATIONS.object_path)
        while True:
            try:
                async with open_dbus_router(bus="SESSION") as router:
                    await Proxy(message_bus, router).AddMatch(rule)
                    self.router = router
                    with router.filter(rule, bufsize=100) as signals:
                        while True:
                            self._signal(await signals.get())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # no session bus (yet), or it restarted
                log.warning("desktop notifications unavailable: %s", exc)
            self.router = None
            await asyncio.sleep(5)

    def _signal(self, msg):
        member = msg.header.fields.get(HeaderFields.member)
        nid = msg.body[0]
        call_agent = next((aid for aid, cid in self.calls.items() if cid == nid), None)
        if call_agent is not None:
            if member == "ActionInvoked" and msg.body[1] in ("answer", "decline"):
                out = {"type": "call_action", "action": msg.body[1]}
                if self.call_keys.get(call_agent):
                    out["key"] = self.call_keys[call_agent]  # app call: press the app's own button
                self.hub.api.send_to_agent(call_agent, out)
            elif member == "NotificationClosed":
                self.calls.pop(call_agent, None)
            return
        if nid not in self.by_id:
            return
        agent_id, key = self.by_id[nid]
        if member == "ActionInvoked" and msg.body[1] == "dismiss":
            self.hub.api.send_to_agent(agent_id, {"type": "notification_action", "key": key, "action": "dismiss"})
        elif member == "NotificationReplied":
            self.hub.api.send_to_agent(agent_id, {"type": "notification_action", "key": key, "action": "reply",
                                                  "text": msg.body[1]})
        elif member == "NotificationClosed":
            self._forget(nid)

    def _forget(self, nid):
        entry = self.by_id.pop(nid, None)
        if entry:
            self.shown.pop(entry, None)

    # --- from devices -----------------------------------------------------------------------------------

    def posted(self, agent, msg):
        if self.desktop_enabled:
            asyncio.ensure_future(self._show(agent, msg))
        self._mirror(agent, msg)

    # --- calls ------------------------------------------------------------------------------------------

    def call(self, agent, msg):
        """A device's call state: "ringing" pops up (critical urgency: shown even over full-screen games and
        in Do Not Disturb, unlike other notifications); "answered"/"ended" take it down."""
        if msg.get("state") == "ringing":
            self.call_keys[agent["id"]] = msg.get("key")
            asyncio.ensure_future(self._ring(agent, msg))
        else:
            self.call_keys.pop(agent["id"], None)
            nid = self.calls.pop(agent["id"], None)
            if nid is not None:
                self._close_call(nid)

    async def _ring(self, agent, msg):
        if self.router is None:
            return
        device = self._device_name(agent)
        number = str(msg.get("number") or "")[:40]
        name = str(msg.get("name") or "")[:80]
        photo = self._save_image(agent["id"], "call", msg.get("photo"))
        hints = {"urgency": ("y", 2), "x-kde-origin-name": ("s", device), "x-kde-display-appname": ("s", "Phone call"),
                 "category": ("s", "call.incoming"), "resident": ("b", True)}
        if photo:
            hints["image-path"] = ("s", photo.as_uri())
        app = str(msg.get("app") or "")[:40]
        kind = f"{app} call" if app else (number if name and number else "")
        body = html.escape(f"{kind} · " if kind else "") + html.escape(f"calling on {device}")
        call = new_method_call(NOTIFICATIONS, "Notify", "susssasa{sv}i", (
            "Phone call", self.calls.get(agent["id"], 0), "call-start", name or number or "Unknown caller", body,
            ["answer", f"Answer on {device}", "decline", "Decline"], hints, 0))
        try:
            self.calls[agent["id"]] = unwrap_msg(await self.router.send_and_get_reply(call))[0]
        except Exception as exc:
            log.warning("desktop refused the call notification from %s: %s", device, exc)

    def _close_call(self, nid):
        if self.router is not None:
            asyncio.ensure_future(self._send_quietly(new_method_call(NOTIFICATIONS, "CloseNotification", "u", (nid,))))

    # --- mirroring to other devices ----------------------------------------------------------------------

    def _mirror(self, agent, msg):
        key = (agent["id"], str(msg.get("key", "")))
        if msg.get("replay") and key not in self.mirrored:
            return  # same rule as the desktop: a reconnect never pops anything up again
        mid = hashlib.sha256(f"{key[0]}\0{key[1]}".encode()).hexdigest()[:16]
        self.mirrored[key] = mid
        self.by_mirror[mid] = key
        out = {"type": "mirror_notification", "id": mid, "device": self._device_name(agent)}
        for field in ("app", "title", "text", "messages", "subtext", "reply", "image", "time"):
            if field in msg:
                out[field] = msg[field]
        package = re.sub(r"[^A-Za-z0-9._-]", "_", str(msg.get("package", "app")))[:100]
        icon = ICON_DIR / f"{package}.png"
        if icon.is_file():
            out["icon"] = base64.b64encode(icon.read_bytes()).decode()
        self.hub.api.send_to_agents(out, capability="notification_mirror", exclude=agent["id"])

    def _unmirror(self, key):
        mid = self.mirrored.pop(key, None)
        if mid is not None:
            self.by_mirror.pop(mid, None)
            self.hub.api.send_to_agents({"type": "mirror_notification_removed", "id": mid},
                                        capability="notification_mirror")

    def mirror_action(self, msg):
        """Dismiss/Reply pressed on a mirrored copy: pass it to the device the notification came from."""
        key = self.by_mirror.get(str(msg.get("id", "")))
        if key is None:
            return
        action = msg.get("action")
        if action in ("dismiss", "reply"):
            out = {"type": "notification_action", "key": key[1], "action": action}
            if action == "reply":
                out["text"] = str(msg.get("text", ""))[:2000]
            self.hub.api.send_to_agent(key[0], out)

    async def _show(self, agent, msg):
        if self.router is None:
            return
        key = (agent["id"], str(msg.get("key", "")))
        if msg.get("replay") and key not in self.shown:
            return  # re-sent after a reconnect: already seen (or dismissed) on this PC, don't pop up again
        device = self._device_name(agent)
        package = re.sub(r"[^A-Za-z0-9._-]", "_", str(msg.get("package", "app")))[:100]
        icon_path = ICON_DIR / f"{package}.png"
        if msg.get("icon"):
            try:
                ICON_DIR.mkdir(parents=True, exist_ok=True)
                icon_path.write_bytes(base64.b64decode(msg["icon"]))
            except (OSError, ValueError):
                pass
        app = str(msg.get("app", package))[:80]
        actions = ["dismiss", f"Dismiss on {device}"]
        hints = {"x-kde-origin-name": ("s", device), "x-kde-display-appname": ("s", app)}
        image = self._save_image(agent["id"], str(msg.get("key", "")), msg.get("image"))
        if image:
            hints["image-path"] = ("s", image.as_uri())  # contact photo / picture, shown large
        if msg.get("reply"):
            actions += ["inline-reply", "Reply"]
            hints["x-kde-reply-placeholder-text"] = ("s", f"Reply on {device}…")
        call = new_method_call(NOTIFICATIONS, "Notify", "susssasa{sv}i", (
            app, self.shown.get(key, 0), str(icon_path) if icon_path.is_file() else "smartphone",
            str(msg.get("title", ""))[:200], self._body(msg), actions, hints, -1))
        try:
            # unwrap_msg: a D-Bus error comes back as a normal reply otherwise, and would pass silently
            nid = unwrap_msg(await self.router.send_and_get_reply(call))[0]
        except Exception as exc:
            log.warning("desktop refused %s notification from %s: %s (title %r, %d chars, actions %s, hints %s)",
                        app, device, exc, str(msg.get("title", ""))[:40], len(self._body(msg)), actions, sorted(hints))
            return
        self.shown[key] = nid
        self.by_id[nid] = key

    @staticmethod
    def _body(msg):
        """Chat apps: the last messages with the sender in bold; otherwise the text (and sub-text)."""
        lines = []
        for m in (msg.get("messages") or [])[-4:]:
            text = html.escape(str(m.get("text", ""))[:500])
            sender = html.escape(str(m.get("sender", ""))[:60])
            lines.append(f"<b>{sender}</b>: {text}" if sender else text)
        if not lines:
            lines.append(html.escape(str(msg.get("text", ""))[:2000]).replace("\n", "<br/>"))
        if msg.get("subtext"):
            lines.append(f"<i>{html.escape(str(msg['subtext'])[:100])}</i>")
        return "<br/>".join(lines)

    @staticmethod
    def _save_image(agent_id, key, data):
        if not data:
            return None
        try:
            IMAGE_DIR.mkdir(parents=True, exist_ok=True)
            path = IMAGE_DIR / (hashlib.sha256(f"{agent_id}\0{key}".encode()).hexdigest()[:24] + ".png")
            path.write_bytes(base64.b64decode(data))
            return path
        except (OSError, ValueError):
            return None

    def removed(self, agent, key):
        nid = self.shown.get((agent["id"], str(key)))
        if nid is not None:
            self._close(nid)
        self._unmirror((agent["id"], str(key)))

    def close_all(self, agent_id):
        """The device turned the feature off or disconnected: its notifications go too."""
        for (aid, _), nid in list(self.shown.items()):
            if aid == agent_id:
                self._close(nid)
        for key in [k for k in self.mirrored if k[0] == agent_id]:
            self._unmirror(key)

    def _close(self, nid):
        entry = self.by_id.get(nid)
        if entry:
            (IMAGE_DIR / (hashlib.sha256(f"{entry[0]}\0{entry[1]}".encode()).hexdigest()[:24] + ".png")).unlink(missing_ok=True)
        self._forget(nid)
        if self.router is not None:
            asyncio.ensure_future(self._send_quietly(new_method_call(NOTIFICATIONS, "CloseNotification", "u", (nid,))))

    async def _send_quietly(self, msg):
        try:
            await self.router.send(msg)
        except Exception:  # bus going away (e.g. the hub is stopping): nothing left to close
            pass

    def _device_name(self, agent):
        target = self.hub.targets.get(agent.get("target"))
        return target.name if target else agent.get("name", "device")
