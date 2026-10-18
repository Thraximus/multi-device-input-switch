"""Tray icon: shows which machine holds the devices; the menu switches between them."""
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import model, theme
from .theme import cog_path


def _cog_icon(remote, online):
    """Cogitator tray icon: a brass cog whose hub glows red while the devices are on another machine."""
    t = theme.current()
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.translate(32, 32)
    p.scale(0.31, 0.31)
    p.setPen(Qt.NoPen)
    p.setBrush(t.color("brass_hi") if online else t.color("muted"))
    p.drawPath(cog_path(10, 76, 100, 40))
    p.setBrush(QColor("#e03a2a") if remote else t.color("brass_dk"))
    p.drawEllipse(QPointF(0, 0), 30, 30)
    p.end()
    return QIcon(pix)


class Tray(QSystemTrayIcon):
    open_requested = Signal()
    quit_requested = Signal()

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.menu = QMenu()
        self.setContextMenu(self.menu)
        self.activated.connect(self._activated)
        client.state_changed.connect(self.refresh)
        client.connection_changed.connect(lambda _: self.refresh(client.state))
        self.refresh(client.state)

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_requested.emit()

    def refresh(self, state=None):
        online = self.client.connected and state is not None
        holder = model.holder(state) if online else model.LINUX
        t = model.target_by_id(state, holder) if online else None
        if theme.current().ornate:
            self.setIcon(_cog_icon(holder != model.LINUX, online))
        elif not online:
            self.setIcon(QIcon.fromTheme("network-disconnect", QIcon.fromTheme("input-keyboard")))
        else:
            self.setIcon(QIcon.fromTheme(model.target_icon(t) if t else "input-keyboard"))
        self.setToolTip("Switcher Hub — " + (f"devices on {model.holder_name(state)}" if online
                                             else "hub service not running"))
        self._build_menu(state if online else None)

    def _build_menu(self, state):
        m = self.menu
        m.clear()
        if state is None:
            a = m.addAction("Hub service not running")
            a.setEnabled(False)
        else:
            head = m.addAction(f"Devices on: {model.holder_name(state)}")
            head.setEnabled(False)
            m.addSeparator()
            group = QActionGroup(m)
            entries = [(model.LINUX, "This PC", True, None)] + [
                (t["id"], t["name"], t["connected"], model.target_icon(t)) for t in state["targets"]]
            for tid, name, usable, icon in entries:
                a = QAction(QIcon.fromTheme(icon or "computer"), name, m)
                a.setCheckable(True)
                a.setChecked(model.holder(state) == tid)
                a.setEnabled(usable)
                a.triggered.connect(lambda _=False, tid=tid: self.client.request(
                    {"type": "switch", "target": None if tid == model.LINUX else tid}))
                group.addAction(a)
                m.addAction(a)
        m.addSeparator()
        m.addAction(QIcon.fromTheme("window"), "Open Switcher Hub", self.open_requested.emit)
        m.addAction(QIcon.fromTheme("application-exit"), "Quit", self.quit_requested.emit)
