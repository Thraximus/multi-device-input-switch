"""Linux tray app for the hub.

Run from the repo root:  python3 -m gui [--hidden]
A second launch just brings the running window forward.
"""
import argparse
import signal
import sys

from PySide6.QtCore import QByteArray, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from . import theme
from .client import HubClient
from .settings import Settings
from .tray import Tray
from .window import MainWindow

INSTANCE = "pi-switcher-hub-gui"


class App:
    def __init__(self, qapp, hidden):
        self.qapp = qapp
        self.prefs = Settings()
        theme.apply(qapp, self.prefs["theme"])
        self.client = HubClient()
        self.window = None
        self.tray = Tray(self.client) if QSystemTrayIcon.isSystemTrayAvailable() else None
        if self.tray:
            self.tray.open_requested.connect(self.toggle)
            self.tray.quit_requested.connect(self.quit)
            self.tray.show()
        saved = self.prefs.data.get("geometry")
        self._build_window(geometry=QByteArray.fromBase64(saved.encode()) if saved else None)
        # The hub needs the PC theme for devices set to "Same as this PC"; re-sent on every (re)connect.
        self.client.connection_changed.connect(lambda up: up and self._report_theme())
        self.client.start()
        if not hidden or self.tray is None:
            self.show()

    def _build_window(self, page=0, geometry=None):
        self.window = MainWindow(self.client, self.prefs, self.set_theme)
        self.window.setWindowIcon(QIcon.fromTheme("input-keyboard"))
        self.window.closeEvent = self._close_event
        if geometry is not None:
            self.window.restoreGeometry(geometry)
        self.window.show_page(page)

    def _report_theme(self):
        self.client.request({"type": "set_appearance", "pc_theme": self.prefs["theme"]})

    def set_theme(self, name):
        if name == self.prefs["theme"]:
            return
        self.prefs["theme"] = name
        self._report_theme()
        old = self.window
        page, geometry, visible = old.current_page(), old.saveGeometry(), old.isVisible()
        theme.apply(self.qapp, name)
        self._build_window(page, geometry)
        if visible:
            self.window.show()
        old.hide()
        old.deleteLater()
        if self.tray:
            self.tray.refresh(self.client.state)

    def _remember_geometry(self):
        self.prefs["geometry"] = bytes(self.window.saveGeometry().toBase64()).decode()

    def _close_event(self, event):
        self._remember_geometry()
        if self.tray:
            event.ignore()
            self.window.hide()
        else:
            event.accept()
            self.quit()

    def show(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def toggle(self):
        if self.window.isVisible() and self.window.isActiveWindow():
            self._remember_geometry()
            self.window.hide()
        else:
            self.show()

    def quit(self):
        if self.window.isVisible():
            self._remember_geometry()
        self.client.stop()
        self.qapp.quit()


def main():
    ap = argparse.ArgumentParser(prog="gui")
    ap.add_argument("--hidden", action="store_true", help="start in the tray")
    args = ap.parse_args()

    qapp = QApplication(sys.argv)
    qapp.setApplicationName("pi-switcher-hub")
    qapp.setDesktopFileName("pi-switcher-hub-gui")
    qapp.setQuitOnLastWindowClosed(False)

    probe = QLocalSocket()
    probe.connectToServer(INSTANCE)
    if probe.waitForConnected(300):  # already running: ask it to show itself
        probe.write(b"show")
        probe.waitForBytesWritten(300)
        return
    QLocalServer.removeServer(INSTANCE)
    server = QLocalServer()
    server.listen(INSTANCE)

    app = App(qapp, args.hidden)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), app.show()))

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    timer = QTimer()  # let Python handle Ctrl+C while Qt runs
    timer.start(250)
    timer.timeout.connect(lambda: None)
    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()
