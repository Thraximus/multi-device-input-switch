"""Main window: navigation rail, a 'devices are on' bar, the pages and a footer for action results."""
from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QHBoxLayout, QMainWindow, QScrollArea, QScrollBar,
                               QStackedWidget, QVBoxLayout, QWidget)

from . import model, theme
from .pages import AudioPage, ClipboardPage, MachinesPage, NotificationsPage, SettingsPage, SwitchPage
from .widgets import Background, Caption, Cog, Dot, Glyph, Heading, Rule, hbox, vbox


def T():
    return theme.current()


class NavButton(QAbstractButton):
    def __init__(self, text, icon_name, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.icon_name = icon_name
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        self.setFixedHeight(40)

    def sizeHint(self):
        return QSize(190, 40)

    def paintEvent(self, _):
        T().paint_nav(QPainter(self), QRectF(self.rect()), self.text(), self.icon_name, self.isChecked(),
                      self.underMouse())


class Rail(QWidget):
    """Left navigation column; the cogitator skin gives it an iron edge."""

    def paintEvent(self, _):
        if T().ornate:
            p = QPainter(self)
            p.fillRect(self.rect(), T().color("iron"))
            p.fillRect(QRectF(self.width() - 1, 0, 1, self.height()), T().color("border"))


class OverlayScroll(QScrollArea):
    """A scroll area whose scrollbar floats over the page's right margin instead of taking width from it,
    so nothing shifts when it appears. The page keeps a margin wide enough for it."""

    def __init__(self, page):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # still scrolls with the wheel
        self.setViewport(Background())
        self.setWidget(page)
        self.setStyleSheet("QScrollArea { background: transparent; }")
        inner = self.verticalScrollBar()
        self.bar = QScrollBar(Qt.Vertical, self)
        inner.rangeChanged.connect(self._range)
        inner.valueChanged.connect(self.bar.setValue)
        self.bar.valueChanged.connect(inner.setValue)
        # the ground is fixed to the window, so a scroll can't reuse (blit) the old pixels
        inner.valueChanged.connect(self.viewport().update)
        self._range(inner.minimum(), inner.maximum())

    def _range(self, lo, hi):
        inner = self.verticalScrollBar()
        self.bar.setRange(lo, hi)
        self.bar.setPageStep(inner.pageStep())
        self.bar.setSingleStep(inner.singleStep())
        self.bar.setValue(inner.value())
        self.bar.setVisible(hi > lo)
        self.bar.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w = min(self.bar.sizeHint().width(), 14)
        self.bar.setGeometry(self.width() - w - 4, 4, w, self.height() - 8)
        self.bar.setPageStep(self.verticalScrollBar().pageStep())


class MainWindow(QMainWindow):
    def __init__(self, client, prefs, on_theme):
        super().__init__()
        self.client = client
        self.setWindowTitle("Switcher Hub")
        self.setMinimumSize(820, 560)
        # Tall enough for the Switch page with four machines and a few devices, within the screen.
        avail = self.screen().availableGeometry()
        self.resize(min(1080, avail.width() - 40), min(860, avail.height() - 40))

        bg = Background()
        self.setCentralWidget(bg)
        outer = QHBoxLayout(bg)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # rail
        rail = Rail()
        rail.setFixedWidth(214)
        rv = QVBoxLayout(rail)
        rv.setContentsMargins(0, 18, 0, 14)
        rv.setSpacing(2)
        if T().ornate:
            self.brand_cog = Cog(34, 12, "brass")
            self.brand_cog.set_spinning(True, 0.6)
            brand = hbox(self.brand_cog, vbox(Heading("Switcher", 19), Caption("input cogitator"), spacing=0), None,
                         spacing=10, margins=(16, 0, 10, 0))
        else:
            brand = hbox(Glyph("input-keyboard", 32, None), vbox(Heading("Switcher Hub", 14),
                                                                   Caption("keyboard · mouse · pad"),
                                                                   spacing=0),
                         None, spacing=10, margins=(14, 0, 10, 0))
        rv.addLayout(brand)
        rv.addSpacing(18)

        self.settings = SettingsPage(client, self.notify, prefs)
        self.pages = [SwitchPage(client, self.notify), MachinesPage(client, self.notify),
                      ClipboardPage(client, self.notify), NotificationsPage(client, self.notify),
                      AudioPage(client, self.notify), self.settings]
        self.settings.theme_requested.connect(on_theme)
        self.stack = QStackedWidget()
        group = QButtonGroup(self)
        for i, page in enumerate(self.pages):
            btn = NavButton(page.title, page.icon)
            group.addButton(btn, i)
            rv.addWidget(btn)
            self.stack.addWidget(OverlayScroll(page))
        group.idClicked.connect(self.stack.setCurrentIndex)
        group.button(0).setChecked(True)
        self.nav = group
        rv.addStretch(1)
        self.hub_dot = Dot("idle")
        self.hub_text = Caption("connecting…")
        rv.addLayout(hbox(self.hub_dot, self.hub_text, None, spacing=4, margins=(16, 0, 10, 0)))
        outer.addWidget(rail)

        # right side: holder bar, pages, footer
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        bar = QWidget()
        self.holder_glyph = Glyph("computer", 26, "brass_hi" if T().ornate else None)
        self.holder_name = Heading("—", 18)
        self.pending = Caption()
        bar.setLayout(hbox(Caption("devices are on"), 6, self.holder_glyph, self.holder_name, 8, self.pending, None,
                           Caption("Ctrl + Scroll Lock to switch"), spacing=8, margins=(28, 14, 28, 10)))
        right.addWidget(bar)
        right.addWidget(Rule())
        right.addWidget(self.stack, 1)
        self.footer = Caption("")
        foot = QWidget()
        self.footer_dot = Dot("idle")
        self.footer_dot.hide()
        foot.setLayout(hbox(self.footer_dot, self.footer, None, spacing=6, margins=(28, 6, 28, 10)))
        right.addWidget(foot)
        outer.addLayout(right, 1)
        self._footer_timer = QTimer(self)
        self._footer_timer.setSingleShot(True)
        self._footer_timer.timeout.connect(lambda: (self.footer.set(""), self.footer_dot.hide()))

        client.state_changed.connect(self.update_state)
        client.connection_changed.connect(self.set_connected)
        self.set_connected(client.connected)
        if client.state:
            self.update_state(client.state)

    def notify(self, text, level="ok"):
        self.footer_dot.set(level)
        self.footer_dot.show()
        self.footer.set(text, {"bad": "bad", "warn": "warn"}.get(level))
        self._footer_timer.start(12000 if level == "bad" else 6000)

    def set_connected(self, up):
        self.hub_dot.set("ok" if up else "bad")
        self.hub_text.set("hub service connected" if up else "hub service not running")
        self.settings.set_connected(up)
        for page in self.pages:
            if page is not self.settings:
                page.setEnabled(up)

    def update_state(self, state):
        t = model.target_by_id(state, model.holder(state))
        self.holder_glyph.set(model.target_icon(t), "brass_hi" if T().ornate else None)
        self.holder_name.setText(model.holder_name(state))
        pend = model.target_by_id(state, state.get("pending")) if state.get("pending") else None
        self.pending.set(f"→ {pend['name']} once all keys are released" if pend else "")
        for page in self.pages:
            page.update_state(state)

    def current_page(self):
        return self.nav.checkedId()

    def show_page(self, i):
        self.nav.button(i).setChecked(True)
        self.stack.setCurrentIndex(i)
