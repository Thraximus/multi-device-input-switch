"""Switchable features of the Windows agent, mirroring the Android app's model: the hub's config says
which run (set per device on the PC), the agent starts/stops them and reports its capabilities."""
import base64
import json
import logging

log = logging.getLogger(__name__)


class NotificationMirror:
    """Notifications from the user's other devices. Shown as Windows toasts named after the source app,
    with "Dismiss on <device>" and Reply (toast_win.py); if toasts aren't available, as tray notifications
    (no buttons there)."""
    id = "notification_mirror"
    title = "Other devices' notifications"
    default = False
    message_types = {"mirror_notification", "mirror_notification_removed"}

    def __init__(self, tray, icon_dir=None):
        self.tray = tray
        self.icon_dir = icon_dir
        self.send = None      # set by FeatureSet.attach: thread-safe send to the hub
        self.toasts = None
        self.current = None   # mirror id of the tray notification on screen
        self.pending = {}     # mirror id -> message (to fall back to the tray if a toast fails)

    def start(self):
        if self.icon_dir is not None and self.toasts is None:
            try:
                from .toast_win import WindowsToasts
                self.toasts = WindowsToasts(self.icon_dir, self._action, self._toast_failed)
            except Exception as exc:  # no WinRT: tray notifications
                log.warning("toasts unavailable, using tray notifications: %s", exc)

    def stop(self):
        if self.toasts is not None:
            self.toasts.clear()
        self.tray.clear_balloon()
        self.current = None
        self.pending.clear()

    def on_message(self, msg):
        mid = msg.get("id")
        if msg["type"] == "mirror_notification_removed":
            self.pending.pop(mid, None)
            if self.toasts is not None:
                self.toasts.remove(mid)
            if mid == self.current:
                self.tray.clear_balloon()
                self.current = None
            return
        if self.toasts is not None:
            try:
                self.pending[mid] = msg
                self.toasts.show(mid, msg)
                return
            except Exception:
                log.exception("toast failed, using tray notifications from now on")
                self.toasts = None
        self._balloon(msg)

    def _balloon(self, msg):
        app, device = msg.get("app", ""), msg.get("device", "another device")
        lines = [(f"{m['sender']}: " if m.get("sender") else "") + m.get("text", "")
                 for m in (msg.get("messages") or [])[-3:]]
        text = "\n".join(lines) if lines else msg.get("text", "")
        picture = msg.get("image") or msg.get("icon")
        self.tray.balloon(msg.get("title") or app, f"{text}\n{app} · {device}".strip(),
                          base64.b64decode(picture) if picture else None)
        self.current = msg.get("id")

    def _action(self, mid, action, text):
        self.pending.pop(mid, None)
        if self.send is not None:
            out = {"type": "mirror_action", "id": mid, "action": action}
            if action == "reply":
                out["text"] = text
            self.send(out)

    def _toast_failed(self, mid):
        """Windows refused the toast (e.g. app identities not supported here): tray notifications instead."""
        log.warning("Windows rejected a toast; using tray notifications")
        self.toasts = None
        msg = self.pending.pop(mid, None)
        if msg is not None:
            self._balloon(msg)


class FeatureSet:
    """The agent's features and which are enabled (persisted, so they start before the hub answers)."""

    def __init__(self, features, path):
        self.features = {f.id: f for f in features}
        self.path = path
        try:
            saved = json.loads(path.read_text())
        except (OSError, ValueError):
            saved = {}
        self.enabled = {fid: bool(saved.get(fid, f.default)) for fid, f in self.features.items()}
        for fid, on in self.enabled.items():
            if on:
                self.features[fid].start()

    def attach(self, send):
        """Give features a way to send to the hub (callable from any thread)."""
        for f in self.features.values():
            f.send = send

    def describe(self):
        return [{"id": f.id, "title": f.title, "default": f.default} for f in self.features.values()]

    def capabilities(self):
        return [fid for fid, on in self.enabled.items() if on]

    def apply(self, wanted):
        """Hub config {"features": {id: bool}}: start/stop to match. Returns True if anything changed."""
        changed = False
        for fid, f in self.features.items():
            on = bool(wanted.get(fid, f.default))
            if on != self.enabled[fid]:
                (f.start if on else f.stop)()
                self.enabled[fid] = on
                changed = True
        if changed:
            try:
                self.path.write_text(json.dumps(self.enabled))
            except OSError:
                pass
        return changed

    def route(self, msg):
        """A hub message for an enabled feature; True if one handled it."""
        for fid, f in self.features.items():
            if self.enabled[fid] and msg.get("type") in f.message_types:
                f.on_message(msg)
                return True
        return False
