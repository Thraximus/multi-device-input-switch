"""Windows toast notifications (WinRT via the `winrt` packages) for mirrored notifications.

Each source app gets its own app identity (AUMID), registered per user under
HKCU\\Software\\Classes\\AppUserModelId with the app's name and icon, so the toast's header reads e.g.
"WhatsApp" with its icon — not "Python". Toasts carry "Dismiss on <device>" and an inline reply box; their
Activated event (raised in this process while it runs) reports the button and the typed text.

If WinRT is missing or Windows refuses the toast (Failed event), the caller falls back to tray balloons.
"""
import hashlib
import logging
import re
import winreg
from pathlib import Path
from xml.sax.saxutils import escape

log = logging.getLogger(__name__)

AUMID_PREFIX = "PiSwitcherHub.Mirror."
GROUP = "switcher"


class WindowsToasts:
    def __init__(self, icon_dir, on_action, on_failed):
        """on_action(mirror id, "dismiss"|"reply", text); on_failed(mirror id) when Windows rejects a toast."""
        from winrt.system import unbox_string
        from winrt.windows.data.xml.dom import XmlDocument
        from winrt.windows.ui.notifications import (ToastActivatedEventArgs, ToastNotification,
                                                    ToastNotificationManager)
        self._unbox_string = unbox_string
        self._XmlDocument = XmlDocument
        self._ToastNotification = ToastNotification
        self._Activated = ToastActivatedEventArgs
        self._manager = ToastNotificationManager
        self.icon_dir = Path(icon_dir)
        self.icon_dir.mkdir(parents=True, exist_ok=True)
        self.on_action = on_action
        self.on_failed = on_failed
        self.shown = {}  # mirror id -> (aumid, toast)  (kept alive: events need the object)

    def show(self, mid, msg):
        app = str(msg.get("app") or "Notification")[:60]
        device = str(msg.get("device") or "another device")[:40]
        aumid = self._register(app, msg.get("icon"))
        picture = self._file(f"pic-{mid}.png", msg.get("image") or msg.get("icon"))
        lines = [(f"{m['sender']}: " if m.get("sender") else "") + m.get("text", "")
                 for m in (msg.get("messages") or [])[-3:]]
        body = "\n".join(lines) if lines else str(msg.get("text", ""))
        title = str(msg.get("title") or app)
        xml = ['<toast activationType="foreground"><visual><binding template="ToastGeneric">',
               f"<text>{escape(title[:200])}</text>", f"<text>{escape(body[:1000])}</text>"]
        if picture:
            xml.append(f'<image placement="appLogoOverride" hint-crop="circle" src="{picture.as_uri()}"/>')
        xml.append(f'<text placement="attribution">via {escape(device)}</text></binding></visual><actions>')
        if msg.get("reply"):
            xml.append(f'<input id="reply" type="text" placeHolderContent="{escape(f"Reply on {device}")}"/>')
            xml.append('<action content="Send" arguments="reply" hint-inputId="reply" activationType="foreground"/>')
        xml.append(f'<action content="{escape(f"Dismiss on {device}")}" arguments="dismiss" '
                   'activationType="foreground"/></actions></toast>')
        doc = self._XmlDocument()
        doc.load_xml("".join(xml))
        toast = self._ToastNotification(doc)
        toast.tag = mid[:64]
        toast.group = GROUP
        toast.add_activated(lambda sender, args, mid=mid: self._activated(mid, args))
        toast.add_failed(lambda sender, args, mid=mid: self.on_failed(mid))
        self._manager.create_toast_notifier_with_id(aumid).show(toast)
        self.shown[mid] = (aumid, toast)

    def remove(self, mid):
        entry = self.shown.pop(mid, None)
        if entry is None:
            return
        try:
            self._manager.history.remove_grouped_tag_with_id(mid[:64], GROUP, entry[0])
        except OSError:
            pass
        (self.icon_dir / f"pic-{mid}.png").unlink(missing_ok=True)

    def clear(self):
        for mid in list(self.shown):
            self.remove(mid)

    def _activated(self, mid, args):
        try:
            a = args.as_(self._Activated)
            action = a.arguments
            text = ""
            if action == "reply":
                text = self._unbox_string(a.user_input["reply"])
            if action in ("dismiss", "reply"):
                self.on_action(mid, action, text)
                self.remove(mid)
        except Exception:
            log.exception("toast action failed")

    def _register(self, app, icon_b64):
        """A per-user app identity named after the source app, so its toasts carry its name and icon."""
        slug = re.sub(r"[^A-Za-z0-9]", "", app)[:24] or "App"
        aumid = AUMID_PREFIX + slug + "." + hashlib.sha1(app.encode()).hexdigest()[:6]
        icon = self._file(f"app-{slug}.png", icon_b64, keep=True)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{aumid}") as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, app)
            if icon:
                winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(icon))
        return aumid

    def _file(self, name, b64, keep=False):
        if not b64:
            path = self.icon_dir / name
            return path if keep and path.is_file() else None
        import base64
        path = self.icon_dir / name
        try:
            path.write_bytes(base64.b64decode(b64))
        except (OSError, ValueError):
            return None
        return path
