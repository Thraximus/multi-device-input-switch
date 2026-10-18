"""Clipboard sync between Linux and agents.

Linux side uses wl-clipboard (Wayland data-control protocol, works from a background service on KDE).
The hub keeps one current clipboard item; changes from Linux or any agent are applied everywhere else.
Echoes are suppressed by content hash.

It also keeps a short history (memory only, never written to disk) that the GUI can restore from.
Items a password manager marks as secret are synced as usual but never enter the history.
"""
import asyncio
import base64
import hashlib
import logging
import shutil
import time
from collections import OrderedDict

log = logging.getLogger(__name__)

TEXT = "text/plain;charset=utf-8"
PNG = "image/png"
MAX_BYTES = 16 << 20
HISTORY_ITEMS = 10
HISTORY_BYTES = 64 << 20
PREVIEW_CHARS = 300
# Set by KeePassXC & co. on KDE: the copied text is a password.
SECRET_HINT = "x-kde-passwordManagerHint"
# Preferred Linux MIME types, best first, mapped to the type we sync as.
LINUX_TYPES = [("image/png", PNG), ("text/plain;charset=utf-8", TEXT), ("UTF8_STRING", TEXT), ("text/plain", TEXT)]


def item_hash(mime, data):
    return hashlib.sha256(mime.encode() + b"\0" + data).hexdigest()


class ClipboardItem:
    def __init__(self, mime, data, origin, secret=False):
        self.mime = mime
        self.data = data
        self.origin = origin  # "linux" or agent id
        self.secret = secret
        self.hash = item_hash(mime, data)
        self.id = self.hash[:16]
        self.time = time.time()

    def to_message(self):
        msg = {"type": "clipboard", "mime": self.mime, "hash": self.hash,
               "data": base64.b64encode(self.data).decode()}
        if self.secret:
            msg["secret"] = True
        return msg

    def summary(self):
        """History entry for the GUI (no data)."""
        out = {"id": self.id, "mime": self.mime, "size": len(self.data), "origin": self.origin, "time": self.time}
        if self.mime == TEXT:
            out["preview"] = self.data[:PREVIEW_CHARS * 4].decode("utf-8", errors="replace")[:PREVIEW_CHARS]
        elif self.mime == PNG and self.data[12:16] == b"IHDR":
            out["width"] = int.from_bytes(self.data[16:20], "big")
            out["height"] = int.from_bytes(self.data[20:24], "big")
        return out


class LinuxClipboard:
    """Watches and sets the Wayland clipboard through wl-paste/wl-copy."""

    def __init__(self, on_change):
        self.on_change = on_change  # callback(mime, data, secret)
        self.available = bool(shutil.which("wl-paste") and shutil.which("wl-copy"))
        self._watch = None
        self._task = None
        self._ignore_hash = None

    def start(self):
        if not self.available:
            log.warning("wl-clipboard not installed; Linux clipboard sync disabled")
            return
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self):
        if self._task:
            self._task.cancel()
        if self._watch and self._watch.returncode is None:
            self._watch.terminate()

    async def _run(self):
        while True:
            try:
                # wl-paste runs the command on every clipboard change; we only need the signal.
                self._watch = await asyncio.create_subprocess_exec(
                    "wl-paste", "--watch", "echo", "changed",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                async for _ in self._watch.stdout:
                    await self._read()
                await self._watch.wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("clipboard watch failed")
            await asyncio.sleep(2)  # wl-paste exits if the compositor restarts; try again

    async def _read(self):
        types = (await self._run_cmd("wl-paste", "--list-types")).decode(errors="replace").split()
        for linux_type, mime in LINUX_TYPES:
            if linux_type in types:
                data = await self._run_cmd("wl-paste", "--no-newline", "--type", linux_type)
                if not data or len(data) > MAX_BYTES:
                    return
                if item_hash(mime, data) == self._ignore_hash:
                    return  # our own write coming back
                self.on_change(mime, data, SECRET_HINT in types)
                return

    async def set(self, mime, data):
        if not self.available:
            return
        self._ignore_hash = item_hash(mime, data)
        proc = await asyncio.create_subprocess_exec(
            "wl-copy", "--type", mime, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.communicate(data)

    @staticmethod
    async def _run_cmd(*args):
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 5)
        return out


class ClipboardService:
    """Hub-wide clipboard: one current item, propagated to Linux and every agent that supports it."""

    def __init__(self, hub, linux=None):
        self.hub = hub
        self.current = None
        self.history = OrderedDict()  # id -> ClipboardItem, newest last
        self.linux = linux if linux is not None else LinuxClipboard(self._from_linux)

    def start(self):
        self.linux.start()

    async def stop(self):
        await self.linux.stop()

    def _from_linux(self, mime, data, secret=False):
        self._accept(ClipboardItem(mime, data, "linux", secret))

    def from_agent(self, agent_id, msg):
        mime = msg.get("mime")
        if mime not in (TEXT, PNG):
            raise ValueError(f"unsupported clipboard type {mime!r}")
        data = base64.b64decode(msg["data"])
        if len(data) > MAX_BYTES:
            raise ValueError("clipboard item too large")
        self._accept(ClipboardItem(mime, data, agent_id, bool(msg.get("secret"))))

    def _accept(self, item):
        if self.current is not None and item.hash == self.current.hash:
            return  # echo of what we already have
        self.current = item
        log.info("clipboard from %s: %s, %d bytes%s", item.origin, item.mime, len(item.data),
                 " (secret)" if item.secret else "")
        if item.origin != "linux":
            asyncio.ensure_future(self.linux.set(item.mime, item.data))
        self.hub.api.send_to_agents(item.to_message(), capability="clipboard", exclude=item.origin)
        self._remember(item)
        self.hub.state_changed()

    # --- history -------------------------------------------------------------

    def _remember(self, item):
        if item.secret:
            return
        self.history.pop(item.id, None)  # copied again: move to the top
        self.history[item.id] = item
        while len(self.history) > HISTORY_ITEMS or (
                len(self.history) > 1 and sum(len(i.data) for i in self.history.values()) > HISTORY_BYTES):
            self.history.popitem(last=False)

    def history_summary(self):
        """Newest first."""
        return [item.summary() for item in reversed(self.history.values())]

    def restore(self, item_id):
        """Make a history entry the clipboard again, on every machine."""
        item = self.history.get(item_id)
        if item is None:
            raise ValueError("no such clipboard entry")
        self.current = item
        item.time = time.time()
        self.history.move_to_end(item_id)
        asyncio.ensure_future(self.linux.set(item.mime, item.data))
        self.hub.api.send_to_agents(item.to_message(), capability="clipboard")
        self.hub.state_changed()

    def get(self, item_id):
        item = self.history.get(item_id)
        if item is None:
            raise ValueError("no such clipboard entry")
        return {"type": "clipboard_item", **item.summary(), "data": base64.b64encode(item.data).decode()}

    def remove(self, item_id=None):
        """Forget one entry, or the whole history."""
        if item_id is None:
            self.history.clear()
        else:
            self.history.pop(item_id, None)
        self.hub.state_changed()

    def for_new_agent(self):
        """Current item for an agent that just connected (so both sides start in sync)."""
        return self.current.to_message() if self.current else None
