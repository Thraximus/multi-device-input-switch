"""The window's pages. Each keeps its widgets keyed by id and updates them in place on every state push,
rebuilding only when the set of machines/devices changes (so hover and toggle animations survive)."""
import base64
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QGridLayout, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from hub import env
from hub.notifications import ICON_DIR

from . import model, settings, theme
from .widgets import (Background, Body, Button, Caption, Chip, Cog, Divider, Dot, Glyph, Heading, Panel, Rule, Tag,
                      Toggle, hbox, vbox)

REPO = Path(__file__).resolve().parent.parent
DEFAULT_UF2 = REPO / "firmware/build/switcher_hub.uf2"


def T():
    return theme.current()


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear(item.layout())


class Page(QWidget):
    title = ""
    subtitle = ""
    icon = ""

    def __init__(self, client, notify):
        super().__init__()
        self.client = client
        self.notify = notify
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(28, 22, 28, 28)
        self.root.setSpacing(12)
        self.root.addWidget(Heading(self.title, 26))
        if self.subtitle:
            self.root.addWidget(Caption(self.subtitle, wrap=True))

    def update_state(self, state):
        pass

    def reply(self, ok_text=None):
        """Callback for a hub request: report errors (and optionally success) in the footer."""
        def done(r):
            if r.get("type") == "error":
                self.notify(r.get("error", "failed"), "bad")
            elif ok_text:
                self.notify(ok_text, "ok")
        return done


# --- Switch ------------------------------------------------------------------------------------------------


# Feature switches shown on the Notifications page rather than with the rest of a machine's features.
NOTIFICATION_FEATURES = {"notifications", "notification_mirror", "calls"}

CARD_MIN_WIDTH = 170
CARD_SPACING = 14


class MachineCard(Panel):
    def __init__(self, tid, parent=None):
        super().__init__(parent, clickable=True, margins=(16, 14, 16, 16), spacing=6)
        self.tid = tid
        self.glyph = Glyph("computer", 32, None)
        self.cog = Cog(22, 10, "brass_hi") if T().ornate else None
        top = [self.glyph, None]
        if self.cog:
            self.cog.hide()
            top.append(self.cog)
        self.chip = Chip()
        top.append(self.chip)
        self.body.addLayout(hbox(*top))
        self.name = Heading("", 15)
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)  # a long name can't widen the card
        self.body.addWidget(self.name)
        self.dot = Dot()
        self.status = Caption(elide=True)
        self.detail = Caption(elide=True)
        if T().ornate:  # the litany's wide tracking leaves too little room on a card
            f = self.detail.font()
            f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
            self.detail.setFont(f)
        self.body.addLayout(hbox(self.dot, self.status, spacing=4))
        self.bus = Tag()
        self.bus.hide()
        # same row height with or without a tag, so cards in a row stay level
        self.detail.setFixedHeight(max(self.detail.sizeHint().height(), self.bus.sizeHint().height()))
        self.body.addLayout(hbox(18, self.bus, self.detail, spacing=6))
        self.setMinimumWidth(CARD_MIN_WIDTH)

    def update_from(self, state, t):
        holding = model.holder(state) == self.tid
        pending = state.get("pending") == self.tid
        if t is None:  # this PC
            self.glyph.set("computer")
            self.name.setText("This PC")
            level, text, detail = "ok", "Linux", "hub"
            self.bus.set("", "")
            usable = True
        else:
            self.glyph.set(model.target_icon(t))
            self.name.setText(t["name"])
            level, text, detail = model.card_status(t)
            bus = model.target_bus(t)
            self.bus.set(*bus)
            if t["connected"] and detail.startswith(bus[0]):  # the tag says it already
                detail = detail[len(bus[0]):].lstrip(" ·")
            usable = t["connected"]
            agent = model.agent_for(state, t["id"])
            if agent and t["connected"]:
                detail += (" · " if detail else "") + "agent " + ("online" if agent["online"] else "offline")
        self.dot.set(level)
        self.status.set(text)
        self.detail.set(detail)
        self.set_active(holding)
        self.dim = not usable
        self.setEnabled(usable or holding)
        self.setCursor(Qt.ArrowCursor if holding else Qt.PointingHandCursor)
        if pending:
            self.chip.set("switching…", "accent")
            self.chip.setToolTip("switches once all keys are released")
        elif holding:
            self.chip.set("holding", "accent")
        else:
            self.chip.set("")
        if self.cog:
            self.cog.setVisible(holding)
            self.cog.set_spinning(holding, 2.0)
        self.setToolTip("" if holding else (f"Switch the keyboard and mouse to {self.name.text()}" if usable
                                            else "Not connected"))


class DeviceRow(QWidget):
    toggled = Signal(str, bool)

    def __init__(self, dev, parent=None):
        super().__init__(parent)
        self.dev_id = dev["id"]
        self.glyph = Glyph(model.device_icon(dev), 22, None)
        self.name = Body(dev["name"])
        self.kinds = Caption()
        self.bus = Tag()
        self.chip = Chip()
        self.toggle = Toggle()
        self.toggle.toggled.connect(lambda on: self.toggled.emit(self.dev_id, on))
        self.mode = Caption()
        self.mode.setFixedWidth(118)
        self.mode.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay = hbox(self.glyph, 6, vbox(self.name, hbox(self.kinds, self.bus, None, spacing=8), spacing=1), self.chip, 10, self.mode,
                   self.toggle, spacing=8, margins=(4, 6, 4, 6))
        lay.setStretch(2, 1)
        self.setLayout(lay)

    def update_from(self, state, dev):
        self.name.set(dev["name"])
        self.kinds.set(" · ".join(dev["kinds"]) or "other")
        bus = model.device_bus(dev)
        self.bus.set(*(bus or ("", "")))
        if bus and bus[0] == "network":
            self.bus.setToolTip("forwarded by the agent on that machine over the network")
        remote = dev.get("origin", model.LINUX) != model.LINUX
        self.toggle.setEnabled(dev["supported"])
        self.toggle.set_checked_quietly(dev["transfer"])
        if not dev["supported"]:
            self.mode.set("not supported")
        else:
            self.mode.set("follows switch" if dev["transfer"] else "stays here")
        if dev.get("grabbed"):
            self.chip.set("→ This PC" if remote else f"→ {model.holder_name(state)}", "accent")
        else:
            self.chip.set("")
        self.toggle.setToolTip("On: this device moves with Ctrl+Scroll Lock. Off: it always stays on its own machine.")


class SwitchPage(Page):
    title = "Switch"
    subtitle = "Ctrl + Scroll Lock cycles through the connected machines · click one to send the devices there"
    icon = "input-keyboard"

    def __init__(self, client, notify):
        super().__init__(client, notify)
        self.root.addWidget(Divider("Devices are on"))
        self.grid = QGridLayout()
        self.grid.setSpacing(CARD_SPACING)
        self._cols = 0
        self.root.addLayout(self.grid)
        self.root.addWidget(Divider("Device pool"))
        self.groups_box = QVBoxLayout()
        self.groups_box.setSpacing(14)
        self.root.addLayout(self.groups_box)
        self.root.addWidget(Divider("Clipboard"))
        self.clip = Caption()
        self.root.addWidget(self.clip)
        self.root.addStretch(1)
        self.cards, self.rows = {}, {}
        self._card_keys = self._dev_keys = None

    def update_state(self, state):
        keys = [model.LINUX] + [t["id"] for t in state["targets"]]
        if keys != self._card_keys:
            self._card_keys = keys
            clear(self.grid)
            self.cards = {}
            for k in keys:
                card = MachineCard(k)
                card.clicked.connect(lambda k=k: self.switch(k))
                self.cards[k] = card
            self._cols = 0
            self._reflow()
        for k, card in self.cards.items():
            card.update_from(state, model.target_by_id(state, k))

        groups = model.device_groups(state)
        dkeys = [(o, [d["id"] for d in devs]) for o, _, devs in groups]
        if dkeys != self._dev_keys:
            self._dev_keys = dkeys
            clear(self.groups_box)
            self.rows = {}
            for origin, title, devs in groups:
                panel = Panel(margins=(16, 12, 16, 12), spacing=2)
                panel.body.addLayout(hbox(Heading(title, 13), None,
                                          Caption("forwarded to this PC while it holds the devices"
                                                  if origin != model.LINUX else "sent to the machine holding the devices")))
                panel.body.addWidget(Rule())
                if not devs:
                    panel.body.addWidget(Caption("no input devices"))
                for d in devs:
                    row = DeviceRow(d)
                    row.toggled.connect(self.set_transfer)
                    self.rows[d["id"]] = row
                    panel.body.addWidget(row)
                self.groups_box.addWidget(panel)
            for p in state.get("passthrough", []):
                self.groups_box.addWidget(Caption(f"ignored (passthrough, never forwarded): {p}"))
        for d in state["devices"]:
            if d["id"] in self.rows:
                self.rows[d["id"]].update_from(state, d)

        clip = state.get("clipboard", {})
        agents = [a["name"] for a in state["agents"] if a["online"] and "clipboard" in a.get("capabilities", [])]
        cur = clip.get("current")
        parts = ["synced with " + ", ".join(agents) if agents else "no machine to sync with"]
        if not clip.get("linux"):
            parts.append("wl-clipboard missing on this PC")
        if cur:
            kind = "image" if cur["mime"].startswith("image/") else "text"
            src = "this PC" if cur["origin"] == model.LINUX else (model.target_by_id(state, cur["origin"]) or {}).get(
                "name", cur["origin"])
            parts.append(f"last copied: {kind}, {model.human_size(cur['size'])}, from {src}")
        self.clip.set(" · ".join(parts))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._reflow()

    def _reflow(self):
        """As many card columns as fit the width (cards wrap onto more rows; never cut off)."""
        if not self.cards:
            return
        margins = self.root.contentsMargins()
        width = self.width() - margins.left() - margins.right()
        cols = max(1, min(len(self.cards), (width + CARD_SPACING) // (CARD_MIN_WIDTH + CARD_SPACING)))
        if cols == self._cols:
            return
        self._cols = cols
        for card in self.cards.values():
            self.grid.removeWidget(card)
        for i, card in enumerate(self.cards.values()):
            self.grid.addWidget(card, i // cols, i % cols)
        for c in range(max(self.grid.columnCount(), cols)):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)

    def switch(self, key):
        self.client.request({"type": "switch", "target": None if key == model.LINUX else key}, self.reply())

    def set_transfer(self, dev_id, on):
        self.client.request({"type": "set_transfer", "device": dev_id, "enabled": on}, self.reply())


# --- Machines ----------------------------------------------------------------------------------------------


def detail(label, value):
    k = Caption(label)
    k.setFixedWidth(130)
    v = Body(value, wrap=True)
    lay = hbox(k, v)
    lay.setStretch(1, 1)
    return lay, v


class TargetPanel(Panel):
    def __init__(self, page, t):
        super().__init__(margins=(18, 14, 18, 16), spacing=8)
        self.page = page
        self.tid = t["id"]
        self.kind = t["kind"]
        self.glyph = Glyph(model.target_icon(t), 28, None)
        self.name = Heading(t["name"], 16)
        self.dot = Dot()
        self.status = Caption()
        self.body.addLayout(hbox(self.glyph, 8, vbox(self.name, hbox(self.dot, self.status, spacing=4), spacing=0),
                                 None))
        self.body.addWidget(Rule())
        self.values = {}
        rows = (["link", "controller", "agent", "features"] if self.kind == "pico"
                else ["link", "address", "interval", "agent", "features"])
        for key in rows:
            lay, v = detail({"link": "connection", "controller": "game controller", "agent": "agent",
                             "features": "agent features", "address": "address", "interval": "interval"}[key], "")
            self.values[key] = v
            self.body.addLayout(lay)
            if key == "features":
                self.features_row = lay
        # Switchable features of the device's app (Android): one toggle each, pushed to the device by the hub.
        self.feature_list = QVBoxLayout()
        self.feature_list.setSpacing(2)
        self.body.addLayout(self.feature_list)
        self.feature_toggles = {}
        self._feature_keys = None
        buttons = [Button("Rename")]
        buttons[0].clicked.connect(self.rename)
        if self.kind == "pico":
            self.layout_box = QComboBox()
            self.layout_box.addItems(["us", "uk"])
            self.layout_box.setCurrentText(env.get("SWITCHER_INSTALL_LAYOUT", "us"))
            self.layout_box.setToolTip("Keyboard layout the laptop is set to (the Pico types the installer)")
            self.install = Button("Install agent")
            self.install.setToolTip("Types the agent install command on this machine through the Pico.\n"
                                    "The devices must be on this PC; don't touch the laptop while it types.")
            self.install.clicked.connect(self.install_agent)
            self.flash = Button("Flash Pico")
            self.flash.setToolTip(f"Flash {DEFAULT_UF2.relative_to(REPO)} through this machine's agent\n"
                                  "(right-click to pick another .uf2)")
            self.flash.clicked.connect(lambda: self.flash_pico(DEFAULT_UF2))
            self.flash.setContextMenuPolicy(Qt.CustomContextMenu)
            self.flash.customContextMenuRequested.connect(self.pick_uf2)
            buttons += [self.layout_box, self.install, self.flash]
        else:
            self.install = Button("Install app (USB)")
            self.install.setToolTip("First install (and pairing) of the Switcher app over USB (ADB).\n"
                                    "After that it updates itself over Wi-Fi when you run tools/build-android.sh.\n"
                                    "Plug the device in with USB debugging allowed.")
            self.install.clicked.connect(self.install_android)
            forget = Button("Forget", "danger")
            forget.clicked.connect(self.forget)
            buttons += [self.install, forget]
        self.body.addSpacing(4)
        self.body.addLayout(hbox(*buttons, None, spacing=10))

    def update_from(self, state, t):
        self.name.setText(t["name"])
        level, text = model.target_status(t)
        self.dot.set(level)
        self.status.set(text)
        self.set_active(model.holder(state) == self.tid)
        v = self.values
        if self.kind == "pico":
            v["link"].set("USB through the Pico (keyboard, mouse, media keys, Xbox controller)")
            v["controller"].set("Xbox 360 (XInput) ready" if t.get("gamepad") else "not active — reflash the Pico?")
            agent = self._agent_rows(state, "Install agent")
            online = bool(agent and agent["online"])
            self.flash.setEnabled(online and "pico-flash" in agent.get("capabilities", []))
            self.install.setEnabled(t["connected"])
        else:
            v["link"].set("Bluetooth LE through the ESP32 (keyboard, mouse, media keys)")
            v["address"].set(t.get("address") or "not yet connected")
            ms = t.get("conn_interval_ms")
            v["interval"].set(f"{ms:g} ms" if ms and t["connected"] else "—")
            self._agent_rows(state, "Install app")

    def _agent_rows(self, state, action):
        agent = model.agent_for(state, self.tid)
        v = self.values
        if agent:
            version = f" {agent['version']}" if agent.get("version") else ""
            v["agent"].set(f"{agent['name']} ({agent['platform']}{version}) · "
                           f"{'online' if agent['online'] else 'offline'}", "text" if agent["online"] else "muted")
            v["features"].set(", ".join(agent.get("capabilities", [])) or "—")
        else:
            v["agent"].set(f"not installed — use {action}", "muted")
            v["features"].set("—", "muted")
        self._feature_toggles(agent)
        return agent

    def _feature_toggles(self, agent):
        features = [f for f in (agent or {}).get("features") or [] if f["id"] not in NOTIFICATION_FEATURES]
        keys = [(agent["id"], f["id"]) for f in features] if features else []
        if keys != self._feature_keys:
            self._feature_keys = keys
            clear(self.feature_list)
            self.feature_toggles = {}
            for i, f in enumerate(features):
                toggle = Toggle()
                toggle.toggled.connect(lambda on, fid=f["id"], aid=agent["id"]: self.page.client.request(
                    {"type": "set_agent_feature", "agent": aid, "feature": fid, "enabled": on}, self.page.reply()))
                self.feature_toggles[f["id"]] = toggle
                k = Caption("on this device" if i == 0 else "")
                k.setFixedWidth(130)
                self.feature_list.addLayout(hbox(k, toggle, Body(f["title"]), None, spacing=10))
        for f in features:
            self.feature_toggles[f["id"]].set_checked_quietly(f["enabled"])
        # the plain capability list says the same thing less usefully once there are switches
        has_switches = bool((agent or {}).get("features"))
        for i in range(self.features_row.count()):
            w = self.features_row.itemAt(i).widget()
            if w is not None:
                w.setVisible(not has_switches)

    def rename(self):
        name, ok = QInputDialog.getText(self, "Rename", "Name:", text=self.name.text())
        if ok and name.strip():
            self.page.client.request({"type": "rename_target", "target": self.tid, "name": name.strip()},
                                     self.page.reply())

    def forget(self):
        if QMessageBox.question(self, "Forget", f"Unpair {self.name.text()}? It will need to pair again.") \
                == QMessageBox.Yes:
            self.page.client.request({"type": "forget_target", "target": self.tid},
                                     self.page.reply(f"{self.name.text()} forgotten"))

    def install_android(self):
        self.page.notify(f"installing the app on {self.name.text()} over USB…", "busy")
        self.page.client.request({"type": "install_android", "target": self.tid},
                                 self.page.reply("app installed; confirm on the device if it asks, then it pairs"))

    def install_agent(self):
        layout = self.layout_box.currentText()
        if QMessageBox.question(
                self, "Install agent",
                f"The Pico will type the install command on {self.name.text()} (layout: {layout.upper()}).\n\n"
                "Make sure its desktop is unlocked and nothing else has focus. Continue?") != QMessageBox.Yes:
            return
        self.page.notify(f"typing the installer on {self.name.text()}…", "busy")
        self.page.client.request({"type": "install_agent", "target": self.tid, "layout": layout},
                                 self.page.reply("installer typed; the agent connects when it finishes"))

    def pick_uf2(self, _pos):
        path, _ = QFileDialog.getOpenFileName(self, "Pico firmware", str(DEFAULT_UF2.parent), "UF2 (*.uf2)")
        if path:
            self.flash_pico(Path(path))

    def flash_pico(self, path):
        if not path.is_file():
            self.page.notify(f"{path} not found — build it with: ninja -C firmware/build", "bad")
            return
        built = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
        if QMessageBox.question(self, "Flash Pico", f"Flash {path.name} (built {built}) onto the Pico?\n\n"
                                "Keyboard and mouse fall back to this PC for a few seconds.") != QMessageBox.Yes:
            return
        data = base64.b64encode(path.read_bytes()).decode()
        self.page.notify("flashing the Pico…", "busy")

        def done(r):
            if r.get("type") == "error":
                self.page.notify("flash failed: " + r.get("error", "?"), "bad")
            else:
                self.page.notify(f"Pico flashed via {r.get('drive', 'the agent')}", "ok")
        self.page.client.request({"type": "flash_pico", "target": self.tid, "uf2": data}, done)


class AppFilterDialog(QDialog):
    """Per-app switches for a device's notifications, plus what happens to apps without a switch.
    Changes apply at once: the hub pushes them to the device, which filters before anything is sent."""

    def __init__(self, page, agent_id, feature, device):
        super().__init__(page)
        self.page, self.agent_id, self.feature = page, agent_id, feature
        self.setWindowTitle(f"Notifications from {device}")
        self.resize(560, 680)
        f = self._feature(page.client.state)
        settings = dict((f or {}).get("settings") or {})
        self.show_new = settings.get("show_new_apps", True)
        self.apps = dict(settings.get("apps") or {})
        catalog = (f or {}).get("catalog") or []

        bg = Background(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(bg)
        root = QVBoxLayout(bg)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)
        root.addWidget(Heading(f"Notifications from {device}", 18))
        root.addWidget(Caption("Switched-off apps' notifications never leave the device.", wrap=True))
        self.default_box = QComboBox()
        self.default_box.addItem("Show them", True)
        self.default_box.addItem("Hide them — only apps switched on below", False)
        self.default_box.setCurrentIndex(0 if self.show_new else 1)
        self.default_box.currentIndexChanged.connect(self._default_changed)
        root.addLayout(hbox(Caption("Apps without a switch (and new apps)"), self.default_box, None, spacing=10))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search apps")
        self.search.textChanged.connect(self._filter)
        all_on, all_off = Button("All on"), Button("All off")
        all_on.clicked.connect(lambda: self._set_visible(True))
        all_off.clicked.connect(lambda: self._set_visible(False))
        root.addLayout(hbox(self.search, all_on, all_off, spacing=8))

        listing = QWidget()
        self.rows_box = QVBoxLayout(listing)
        self.rows_box.setContentsMargins(0, 0, 8, 0)
        self.rows_box.setSpacing(2)
        self.rows = []
        for app in catalog:
            toggle = Toggle()
            toggle.set_checked_quietly(self.apps.get(app["package"], self.show_new))
            toggle.toggled.connect(lambda on, pkg=app["package"]: self._app_changed(pkg, on))
            icon = QLabel()
            icon.setFixedSize(28, 28)
            pix = QPixmap(str(ICON_DIR / f"{app['package']}.png"))
            if not pix.isNull():
                icon.setPixmap(pix.scaled(28, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            row = QWidget()
            names = vbox(Body(app["label"], elide=True), Caption(app["package"], elide=True), spacing=0)
            lay = hbox(icon, names, toggle, spacing=10, margins=(2, 4, 2, 4))
            lay.setStretch(1, 1)
            row.setLayout(lay)
            self.rows.append((app, toggle, row))
        self.chosen_divider = Divider("")
        self.rest_divider = Divider("All other apps")
        if catalog:
            self._arrange()
        else:
            self.rows_box.addWidget(Caption("The device hasn't sent its app list yet — switch Notifications on "
                                            "and wait for it to connect.", wrap=True))
            self.rows_box.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(listing)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        root.addWidget(scroll, 1)
        close = Button("Close", "primary")
        close.clicked.connect(self.accept)
        root.addLayout(hbox(Caption(f"{len(catalog)} apps"), None, close))

    def _feature(self, state):
        agent = next((a for a in (state or {}).get("agents", []) if a["id"] == self.agent_id), None)
        return next((f for f in (agent or {}).get("features", []) if f["id"] == self.feature), None)

    def _arrange(self):
        """Switched-on apps first, then a divider, then the rest, so the ones you picked are easy to find and
        turn off. Done when the dialog opens or the default changes — not on every click, so rows don't jump
        under the mouse."""
        while self.rows_box.count():
            self.rows_box.takeAt(0)  # detach only; the row widgets are kept
        picked = [r for r in self.rows if self.apps.get(r[0]["package"], self.show_new)]
        rest = [r for r in self.rows if r not in picked]
        self.chosen_divider.text = f"Switched on ({len(picked)})"
        self.chosen_divider.update()
        if picked:
            self.rows_box.addWidget(self.chosen_divider)
            for _, _, row in picked:
                self.rows_box.addWidget(row)
        self.chosen_divider.setVisible(bool(picked))
        self.rows_box.addWidget(self.rest_divider)
        self.rest_divider.setVisible(bool(picked))
        for _, _, row in rest:
            self.rows_box.addWidget(row)
        self.rows_box.addStretch(1)

    def _filter(self, text):
        t = text.lower().strip()
        for app, _, row in self.rows:
            row.setVisible(not t or t in app["label"].lower() or t in app["package"].lower())

    def _default_changed(self, _):
        self.show_new = self.default_box.currentData()
        for app, toggle, _ in self.rows:  # apps without their own switch follow the default
            if app["package"] not in self.apps:
                toggle.set_checked_quietly(self.show_new)
        self._arrange()
        self._save()

    def _app_changed(self, pkg, on):
        self.apps[pkg] = on
        self._save()

    def _set_visible(self, on):
        for app, toggle, row in self.rows:
            if row.isVisible():
                self.apps[app["package"]] = on
                toggle.set_checked_quietly(on)
        self._save()

    def _save(self):
        self.page.client.request({"type": "set_feature_settings", "agent": self.agent_id, "feature": self.feature,
                                  "settings": {"show_new_apps": self.show_new, "apps": self.apps}}, self.page.reply())


class BluetoothPanel(Panel):
    PAIR_SECONDS = 120

    def __init__(self, page):
        super().__init__(margins=(18, 14, 18, 16), spacing=8)
        self.page = page
        self.dot = Dot()
        self.status = Caption()
        self.body.addLayout(hbox(Glyph("network-bluetooth", 28, None), 8,
                                 vbox(Heading("Bluetooth", 16), hbox(self.dot, self.status, spacing=4), spacing=0),
                                 None))
        self.body.addWidget(Rule())
        self.hint = Body("Pair a phone or tablet: start pairing, then pick the hub in its Bluetooth settings. "
                         "It becomes a new machine to switch to. Up to 4 stay connected at once.", "text2", wrap=True)
        self.body.addWidget(self.hint)
        self.code_label = Caption("pairing code — confirm it matches on the phone/tablet")
        self.code = Heading("", 34, "amber" if T().ornate else "accent")
        self.code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body.addWidget(self.code_label)
        self.body.addWidget(self.code)
        self.pair = Button("Start pairing", "primary")
        self.pair.clicked.connect(self.toggle_pairing)
        self.countdown = Caption()
        self.body.addLayout(hbox(self.pair, self.countdown, None, spacing=12))
        self.pairing = False
        self.until = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)

    def update_from(self, state):
        chans = state.get("bluetooth", [])
        if not chans:
            self.dot.set("idle")
            self.status.set("no ESP32 Bluetooth channel configured")
            self.pair.setEnabled(False)
            return
        b = chans[0]
        self.dot.set("ok" if b["responding"] else "bad")
        n = len(b.get("connected", []))
        self.status.set(("ESP32 ready" if b["responding"] else "ESP32 not answering") + f" · {n} connected")
        self.pair.setEnabled(b["responding"])
        if b["pairing"] and not self.pairing:
            self.until = self.until if self.until > time.monotonic() else time.monotonic() + self.PAIR_SECONDS
        self.pairing = b["pairing"]
        self.pair.setText("Stop pairing" if self.pairing else "Start pairing")
        self.pair.variant = "danger" if self.pairing else "primary"
        self.pair.update()
        code = b.get("passkey")
        self.code_label.setVisible(bool(code))
        self.code.setVisible(bool(code))
        if code:
            s = f"{int(code):06d}"
            self.code.setText(f"{s[:3]} {s[3:]}")
        self._tick()

    def _tick(self):
        left = int(self.until - time.monotonic())
        self.countdown.set(f"open for {left} s — pick \"Switcher Hub\" on the phone" if self.pairing and left > 0
                           else ("pairing open" if self.pairing else ""))

    def toggle_pairing(self):
        seconds = 0 if self.pairing else self.PAIR_SECONDS
        if seconds:
            self.until = time.monotonic() + seconds
        self.page.client.request({"type": "ble_pair", "seconds": seconds}, self.page.reply())


class MachinesPage(Page):
    title = "Machines"
    subtitle = "Targets the hub can send devices to, their agents and Bluetooth pairing"
    icon = "computer-laptop"

    def __init__(self, client, notify):
        super().__init__(client, notify)
        self.list = QVBoxLayout()
        self.list.setSpacing(14)
        self.root.addLayout(self.list)
        self.root.addWidget(Divider("Bluetooth"))
        self.bt = BluetoothPanel(self)
        self.root.addWidget(self.bt)
        self.root.addStretch(1)
        self.panels = {}
        self._keys = None

    def update_state(self, state):
        keys = [t["id"] for t in state["targets"]]
        if keys != self._keys:
            self._keys = keys
            clear(self.list)
            self.panels = {}
            for t in state["targets"]:
                self.panels[t["id"]] = TargetPanel(self, t)
                self.list.addWidget(self.panels[t["id"]])
        for t in state["targets"]:
            self.panels[t["id"]].update_from(state, t)
        self.bt.update_from(state)


# --- Clipboard ---------------------------------------------------------------------------------------------


def ago(t):
    d = max(0, time.time() - t)
    if d < 45:
        return "just now"
    if d < 3600:
        return f"{round(d / 60)} min ago"
    if d < 86400:
        return f"{round(d / 3600)} h ago"
    return time.strftime("%d %b %H:%M", time.localtime(t))


class ClipEntry(Panel):
    THUMB = 56

    def __init__(self, page, entry):
        super().__init__(margins=(14, 10, 14, 10), spacing=4)
        self.page = page
        self.entry = entry
        self.item_id = entry["id"]
        image = entry["mime"].startswith("image/")
        self.thumb = Glyph("image-x-generic" if image else "text-plain", self.THUMB if image else 28, None)
        if image:
            self.thumb.setFixedSize(self.THUMB, self.THUMB)
            self.thumb.setAlignment(Qt.AlignCenter)
            page.thumbnail(self.item_id, self.thumb)
        if image:
            text = f"image {entry['width']} × {entry['height']}" if entry.get("width") else "image"
        else:
            text = " ".join((entry.get("preview") or "").split())  # one paragraph: newlines/tabs as spaces
        self.text = Body(text or "(empty)", "text" if text else "muted", elide=True)
        self.meta = Caption()
        self.chip = Chip()
        copy = Button("Copy")
        copy.setToolTip("Put this back on the clipboard of every machine")
        copy.clicked.connect(lambda: page.client.request({"type": "clipboard_restore", "item": self.item_id},
                                                        page.reply("copied to every machine")))
        remove = Button("Remove")
        remove.clicked.connect(lambda: page.client.request({"type": "clipboard_remove", "item": self.item_id},
                                                          page.reply()))
        lay = hbox(self.thumb, 6, vbox(self.text, hbox(self.meta, self.chip, None, spacing=8), spacing=2),
                   copy, remove, spacing=8)
        lay.setStretch(2, 1)
        self.body.addLayout(lay)

    def update_from(self, state, entry, current):
        self.entry = entry
        src = "this PC" if entry["origin"] == model.LINUX else self.page.origin_name(state, entry["origin"])
        kind = "image" if entry["mime"].startswith("image/") else "text"
        self.meta.set(f"{kind} · {model.human_size(entry['size'])} · from {src} · {ago(entry['time'])}")
        self.chip.set("on the clipboard" if current else "", "ok")
        self.set_active(current)


class ClipboardPage(Page):
    title = "Clipboard"
    subtitle = ("The last 10 copies from every machine. Kept in memory only (cleared when the hub restarts); "
                "copies a password manager marks as secret are never kept.")
    icon = "edit-paste"

    def __init__(self, client, notify):
        super().__init__(client, notify)
        self.sync = Caption()
        clear_btn = Button("Clear history", "danger")
        clear_btn.clicked.connect(self.clear)
        self.root.addLayout(hbox(self.sync, None, clear_btn, spacing=12))
        self.list = QVBoxLayout()
        self.list.setSpacing(10)
        self.root.addLayout(self.list)
        self.empty = Caption("nothing copied yet")
        self.root.addWidget(self.empty)
        self.root.addStretch(1)
        self.entries = {}
        self._keys = None
        self._thumbs = {}  # item id -> QPixmap (images don't change, fetch once)
        self.state = None
        timer = QTimer(self)
        timer.timeout.connect(lambda: self.state and self.update_state(self.state))
        timer.start(30000)  # keep "n min ago" fresh

    @staticmethod
    def origin_name(state, origin):
        agent = next((a for a in state["agents"] if a["id"] == origin), None)
        if agent:
            t = model.target_by_id(state, agent["target"])
            return t["name"] if t else agent["name"]
        t = model.target_by_id(state, origin)
        return t["name"] if t else origin

    def thumbnail(self, item_id, label):
        if item_id in self._thumbs:
            label.setPixmap(self._thumbs[item_id])
            return

        def done(r):
            if r.get("type") != "clipboard_item":
                return
            img = QImage.fromData(base64.b64decode(r["data"]))
            if img.isNull():
                return
            pix = QPixmap.fromImage(img.scaled(ClipEntry.THUMB, ClipEntry.THUMB, Qt.KeepAspectRatio,
                                               Qt.SmoothTransformation))
            self._thumbs[item_id] = pix
            try:
                label.setPixmap(pix)
            except RuntimeError:  # the entry was removed meanwhile
                pass
        self.client.request({"type": "clipboard_get", "item": item_id}, done)

    def update_state(self, state):
        self.state = state
        clip = state.get("clipboard", {})
        history = clip.get("history", [])
        agents = [a["name"] for a in state["agents"] if a["online"] and "clipboard" in a.get("capabilities", [])]
        line = "synced with " + ", ".join(agents) if agents else "no other machine to sync with right now"
        if not clip.get("linux"):
            line += " · wl-clipboard missing on this PC"
        self.sync.set(line)
        keys = [e["id"] for e in history]
        if keys != self._keys:
            self._keys = keys
            clear(self.list)
            self.entries = {}
            for e in history:
                self.entries[e["id"]] = ClipEntry(self, e)
                self.list.addWidget(self.entries[e["id"]])
            self._thumbs = {k: v for k, v in self._thumbs.items() if k in keys}
        self.empty.setVisible(not history)
        for e in history:
            self.entries[e["id"]].update_from(state, e, e["id"] == clip.get("current_id"))

    def clear(self):
        if QMessageBox.question(self, "Clear history", "Forget all clipboard history?") == QMessageBox.Yes:
            self.client.request({"type": "clipboard_remove"}, self.reply("history cleared"))


# --- Notifications -----------------------------------------------------------------------------------------


class NotificationCard(Panel):
    """One machine: whether its notifications go out (phones/tablets, with the app filter) and whether
    notifications from the other devices are shown on it."""

    def __init__(self, page, key, name, icon, rows):
        super().__init__(margins=(18, 14, 18, 16), spacing=8)
        self.page = page
        self.key = key
        self.status = Caption()
        self.body.addLayout(hbox(Glyph(icon, 28, None), 8,
                                 vbox(Heading(name, 16), self.status, spacing=0), None))
        self.body.addWidget(Rule())
        self.toggles = {}
        for row in rows:
            if row == "app":
                self.body.addWidget(Caption("Install or update the Switcher app/agent on this machine to "
                                            "send or show notifications.", wrap=True))
                continue
            if row == "call_audio":  # the audio seam: switch shown, not yet usable
                toggle = Toggle()
                toggle.setEnabled(False)
                self.body.addLayout(hbox(toggle, Body("Calls answered here: use this PC's speakers and mic",
                                                      color="muted"), None, spacing=10))
                self.body.addWidget(Caption("Comes with audio routing (needs a USB Bluetooth dongle). Until then "
                                            "Answer picks up on the phone.", wrap=True))
                continue
            toggle = Toggle()
            toggle.toggled.connect(lambda on, row=row: self._toggled(row, on))
            self.toggles[row] = toggle
            label = {"desktop": "Show notifications from other devices here",
                     "notification_mirror": "Show notifications from other devices here",
                     "notifications": "Send this device's notifications",
                     "calls": "Show incoming calls on the PC — even over games"}[row]
            items = [toggle, Body(label)]
            if row == "notifications":
                choose = Button("Choose apps…")
                choose.setToolTip("Which apps' notifications leave this device (filtered on the device)")
                choose.clicked.connect(lambda: AppFilterDialog(page, self.key, "notifications", name).exec())
                items.append(choose)
            self.body.addLayout(hbox(*items, None, spacing=10))

    def _toggled(self, row, on):
        if row == "desktop":
            self.page.client.request({"type": "set_desktop_notifications", "enabled": on}, self.page.reply())
        else:
            self.page.client.request({"type": "set_agent_feature", "agent": self.key, "feature": row, "enabled": on},
                                     self.page.reply())

    def update_from(self, values, status):
        for row, on in values.items():
            if row in self.toggles:
                self.toggles[row].set_checked_quietly(on)
        self.status.set(status)


class NotificationsPage(Page):
    title = "Notifications"
    subtitle = ("Which devices send their notifications, and where notifications from the other devices show up. "
                "Dismiss and Reply on a shown copy act on the device it came from.")
    icon = "notifications"  # the monochrome bell, like the rest of the rail

    def __init__(self, client, notify):
        super().__init__(client, notify)
        self.list = QVBoxLayout()
        self.list.setSpacing(14)
        self.root.addLayout(self.list)
        self.root.addStretch(1)
        self.cards = {}
        self._keys = None

    def _machines(self, state):
        """[(key, name, icon, rows, values, status)] — this PC first, then each target's agent."""
        out = [("linux", "This PC", "computer", ["desktop", "call_audio"],
                {"desktop": state.get("desktop_notifications", True)}, "Plasma notifications")]
        for t in state["targets"]:
            agent = model.agent_for(state, t["id"])
            features = {f["id"]: f["enabled"] for f in (agent or {}).get("features") or []
                        if f["id"] in NOTIFICATION_FEATURES}
            rows = [r for r in ("notifications", "notification_mirror", "calls") if r in features] or ["app"]
            if agent:
                kind = "app" if agent["platform"] == "android" else "agent"
                status = ("online" if agent["online"] else "offline") + (f" · {kind} {agent['version']}"
                                                                          if agent.get("version") else "")
            else:
                status = "no app installed"
            out.append((agent["id"] if agent else t["id"], t["name"], model.target_icon(t), rows, features, status))
        return out

    def update_state(self, state):
        machines = self._machines(state)
        keys = [(m[0], m[1], tuple(m[3])) for m in machines]
        if keys != self._keys:
            self._keys = keys
            clear(self.list)
            self.cards = {}
            for key, name, icon, rows, _, _ in machines:
                self.cards[key] = NotificationCard(self, key, name, icon, rows)
                self.list.addWidget(self.cards[key])
        for key, _, _, _, values, status in machines:
            self.cards[key].update_from(values, status)


# --- Audio -------------------------------------------------------------------------------------------------


class AudioPage(Page):
    title = "Audio"
    subtitle = "Where sound plays — chosen here, no hotkey"
    icon = "audio-headphones"

    def __init__(self, client, notify):
        super().__init__(client, notify)
        panel = Panel(margins=(18, 14, 18, 18), spacing=10)
        panel.dim = True
        panel.body.addLayout(hbox(Glyph("audio-headphones", 28, None), 8, Heading("Output", 16), None,
                                  Chip("not available yet")))
        panel.body.addWidget(Rule())
        self.output = QComboBox()
        self.output.setEnabled(False)
        self.output.setMinimumWidth(260)
        k = Caption("play sound on")
        k.setFixedWidth(130)
        panel.body.addLayout(hbox(k, self.output, None))
        panel.body.addWidget(Body(
            "Planned: headphones on this PC hear the laptop and take phone calls. The phone half needs a USB "
            "Bluetooth dongle (the ESP32 can't do classic Bluetooth audio while it's the keyboard), so this is "
            "parked until then.", "text2", wrap=True))
        self.root.addWidget(panel)
        self.root.addStretch(1)
        self._keys = None

    def update_state(self, state):
        audio = state.get("audio", {})
        keys = ["This PC"] + [t["name"] for t in state["targets"]]
        if keys != self._keys:
            self._keys = keys
            self.output.clear()
            self.output.addItems(keys)
        self.output.setEnabled(bool(audio.get("implemented")))


# --- Settings ----------------------------------------------------------------------------------------------


class ThemeCard(Panel):
    """A clickable preview of one theme, drawn with that theme's own colours."""

    def __init__(self, name, label, blurb, swatches):
        super().__init__(clickable=True, margins=(16, 14, 16, 14), spacing=6)
        self.theme_name = name
        self.body.addWidget(Heading(label, 15))
        self.body.addWidget(Caption(blurb, wrap=True))
        strip = QWidget()
        strip.setFixedHeight(18)
        strip.setStyleSheet("background: transparent;")
        lay = hbox(*[self._swatch(c) for c in swatches], None, spacing=4)
        strip.setLayout(lay)
        self.body.addWidget(strip)
        self.setMinimumWidth(240)

    @staticmethod
    def _swatch(color):
        w = QWidget()
        w.setFixedSize(26, 16)
        w.setStyleSheet(f"background: {color}; border: 1px solid #00000055;")
        return w


class SettingsPage(Page):
    title = "Settings"
    icon = "configure"
    theme_requested = Signal(str)

    def __init__(self, client, notify, prefs):
        super().__init__(client, notify)
        self.prefs = prefs
        self._syncing = False
        self.root.addWidget(Divider("Look"))
        cards = []
        for name, label, blurb, sw in (
                ("plasma", "Plasma", "native Breeze, follows your colour scheme",
                 ["#202326", "#3daee9", "#fcfcfc", "#27ae60"]),
                ("cogitator", "Cogitator", "scorched iron, brass and the red rune",
                 ["#0b0908", "#b08d3e", "#ffd98a", "#aa1111"])):
            card = ThemeCard(name, label, blurb, sw)
            card.set_active(prefs["theme"] == name)
            card.clicked.connect(lambda n=name: self.theme_requested.emit(n))
            cards.append(card)
        self.root.addLayout(hbox(*cards, None, spacing=14))

        self.root.addWidget(Divider("Phone & tablet look"))
        self.device_theme = QComboBox()
        for key, label in (("auto", "Same as this PC"), ("material", "Material (the device's own look)"),
                           ("cogitator", "Cogitator")):
            self.device_theme.addItem(label, key)
        self.device_theme.currentIndexChanged.connect(self._device_theme)
        self.root.addLayout(hbox(self.device_theme, Caption("the Switcher app on paired phones and tablets"),
                                 None, spacing=12))
        self.root.addWidget(Divider("Startup"))
        self.autostart = Toggle()
        self.autostart.set_checked_quietly(settings.autostart_enabled())
        self.autostart.toggled.connect(self._autostart)
        self.root.addLayout(hbox(self.autostart, Body("Start the tray icon when I log in"), None, spacing=10))
        self.root.addWidget(Caption("closing the window keeps it in the tray · quit from the tray menu", wrap=True))

        self.root.addWidget(Divider("Hub service"))
        self.dot = Dot()
        self.svc = Body("")
        restart = Button("Restart service")
        restart.clicked.connect(self._restart)
        logs = Button("Show log")
        logs.clicked.connect(self._logs)
        self.root.addLayout(hbox(self.dot, self.svc, None, logs, restart, spacing=10))
        self.root.addStretch(1)

    def _device_theme(self, _index):
        if not self._syncing:
            self.client.request({"type": "set_appearance", "device_theme": self.device_theme.currentData()},
                                self.reply("phones and tablets will switch look"))

    def update_state(self, state):
        choice = state.get("appearance", {}).get("device_theme", "auto")
        self._syncing = True
        self.device_theme.setCurrentIndex(max(0, self.device_theme.findData(choice)))
        self._syncing = False
        n = len(state["targets"])
        self.dot.set("ok")
        self.svc.set(f"running · {n} machine{'s' if n != 1 else ''} · {len(state['devices'])} devices")

    def set_connected(self, up):
        if not up:
            self.dot.set("bad")
            self.svc.set("not running — start it with: systemctl --user start pi-switcher-hub")

    def _autostart(self, on):
        settings.set_autostart(on)
        self.notify("will start at login" if on else "won't start at login", "ok")

    def _restart(self):
        subprocess.Popen(["systemctl", "--user", "restart", "pi-switcher-hub"])
        self.notify("restarting the hub service…", "busy")

    def _logs(self):
        for term in (["konsole", "-e"], ["xterm", "-e"]):
            try:
                subprocess.Popen(term + ["journalctl", "--user", "-u", "pi-switcher-hub", "-f", "-n", "200"])
                return
            except FileNotFoundError:
                continue
        self.notify("no terminal found; run: journalctl --user -u pi-switcher-hub -f", "warn")
