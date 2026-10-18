"""Building blocks that draw themselves through the current theme."""
from PySide6.QtCore import Property, QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import theme
from .theme import cog_path


def T():
    return theme.current()


class Background(QWidget):
    """The window ground (with the cogitator's dim circuitry).

    Every Background draws the same window-sized picture offset to its own position, so the scroll viewports
    (which paint their own ground, so partial repaints never leave stale pixels) line up with the window.
    """

    def paintEvent(self, e):
        p = QPainter(self)
        p.setClipRect(e.rect())
        win = self.window()
        offset = self.mapTo(win, self.rect().topLeft()) if win is not self else self.rect().topLeft()
        p.translate(-offset.x(), -offset.y())
        T().paint_background(p, QRectF(win.rect()))


class Panel(QFrame):
    """A card / plate. `clickable` panels highlight on hover and emit clicked."""
    clicked = Signal()

    def __init__(self, parent=None, clickable=False, margins=(16, 14, 16, 14), spacing=10):
        super().__init__(parent)
        self.clickable = clickable
        self.active = False
        self.dim = False
        self._hover = False
        self.setAttribute(Qt.WA_Hover, clickable)
        if clickable:
            self.setCursor(Qt.PointingHandCursor)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(spacing)

    def set_active(self, active):
        if active != self.active:
            self.active = active
            self.update()

    def enterEvent(self, e):
        self._hover = self.clickable and self.isEnabled()
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if self.clickable and self.isEnabled() and e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        T().paint_panel(p, QRectF(self.rect()), self.active, self._hover, self.dim)


class Rule(QWidget):
    """Separator under a panel heading: hazard chevrons or a hairline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(8 if T().ornate else 1)

    def paintEvent(self, _):
        T().paint_rule(QPainter(self), QRectF(self.rect()))


class Heading(QLabel):
    def __init__(self, text, size=16, color="text", parent=None):
        super().__init__(parent)
        self._raw = text
        self.setFont(T().display_font(size))
        key = "brass_hi" if T().ornate and color == "text" else color
        self.setStyleSheet(f"color: {T().c[key]}; background: transparent;")
        self.setText(text)


class Caption(QLabel):
    """Small secondary text; in the cogitator skin it's the spaced uppercase 'litany'."""

    def __init__(self, text="", color=None, wrap=False, elide=False, parent=None):
        super().__init__(parent)
        self._color = color
        self._elide = elide
        self._full = ""
        self.setFont(T().label_font())
        self.setWordWrap(wrap)
        if elide:  # one line, cut with "…" to the available width; the full text is the tooltip
            self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.set(text, color)

    def set(self, text, color=None):
        key = color or ("litany" if T().ornate else "text2")
        self.setStyleSheet(f"color: {T().c[key]}; background: transparent;")
        self._full = T().text(text)
        if self._elide:
            self._apply_elide()
        else:
            self.setText(self._full)

    def _apply_elide(self):
        shown = QFontMetrics(self.font()).elidedText(self._full, Qt.ElideRight, max(0, self.width()))
        self.setText(shown)
        self.setToolTip(self._full if shown != self._full else "")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._elide:
            self._apply_elide()


class Body(QLabel):
    def __init__(self, text="", color="text", size=0, bold=False, wrap=False, elide=False, parent=None):
        super().__init__(parent)
        self._elide = elide
        self._full = ""
        self.setFont(T().font(size, bold))
        self.setWordWrap(wrap)
        if elide:  # one line, cut with "…"; the full text is the tooltip
            self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.set(text, color)

    def set(self, text, color="text"):
        self.setStyleSheet(f"color: {T().c[color]}; background: transparent;")
        self._full = text
        if self._elide:
            self._apply_elide()
        else:
            self.setText(text)

    def _apply_elide(self):
        shown = QFontMetrics(self.font()).elidedText(self._full, Qt.ElideRight, max(0, self.width()))
        self.setText(shown)
        self.setToolTip(self._full[:600] if shown != self._full else "")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._elide:
            self._apply_elide()


class Glyph(QLabel):
    def __init__(self, icon_name, size=22, color_key=None, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.set(icon_name, color_key)

    def set(self, icon_name, color_key=None):
        pix = T().icon(icon_name, self._size, color_key) if color_key else T().icon(icon_name, self._size)
        self.setPixmap(pix)


class Button(QPushButton):
    """Native push button under Plasma; a chamfered brass plate under the cogitator skin."""

    def __init__(self, text, variant="normal", parent=None):
        super().__init__(text, parent)
        self.variant = variant
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        if T().ornate:
            f = T().font(-1.5)
            f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 118)
            self.setFont(f)

    def sizeHint(self):
        if not T().ornate:
            return super().sizeHint()
        fm = QFontMetrics(self.font())
        return QSize(fm.horizontalAdvance(T().text(self.text())) + 40, fm.height() + 16)

    def paintEvent(self, e):
        if not T().ornate:
            return super().paintEvent(e)
        p = QPainter(self)
        T().paint_button(p, QRectF(self.rect()), T().text(self.text()), self.variant, self.underMouse(),
                         self.isDown(), self.isEnabled(), self.font())


class Toggle(QAbstractButton):
    """An animated on/off switch."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self):
        return QSize(46, 24) if T().ornate else QSize(40, 22)

    def set_checked_quietly(self, on):
        """Reflect the hub's state without emitting toggled (no request loop)."""
        if on == self.isChecked():
            return
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self._pos = 1.0 if on else 0.0
        self.update()

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get_pos(self):
        return self._pos

    def _set_pos(self, v):
        self._pos = v
        self.update()

    knob = Property(float, _get_pos, _set_pos)  # not "pos": that is QWidget's own position

    def paintEvent(self, _):
        T().paint_toggle(QPainter(self), QRectF(self.rect()), self._pos, self.isEnabled())


class Dot(QWidget):
    """Status lamp: ok / warn / bad / busy / idle."""

    def __init__(self, level="idle", parent=None):
        super().__init__(parent)
        self.level = level
        self.setFixedSize(14, 14)

    def set(self, level):
        if level != self.level:
            self.level = level
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = T().status_color(self.level)
        center = QPointF(7, 7)
        if T().ornate and self.level != "idle":
            g = QRadialGradient(center, 7)
            glow = QColor(c)
            glow.setAlpha(110)
            g.setColorAt(0, glow)
            g.setColorAt(1, QColor(0, 0, 0, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawEllipse(center, 7, 7)
        p.setPen(QPen(c.darker(150), 1) if self.level != "idle" else QPen(c, 1))
        p.setBrush(c if self.level != "idle" else Qt.NoBrush)
        p.drawEllipse(center, 3.6, 3.6)


class Tag(QFrame):
    """A tiny outlined tag with an icon, e.g. how a device is connected (USB / BT)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tag")
        self.glyph = QLabel()
        self.label = QLabel()
        f = T().label_font()
        f.setPointSizeF(f.pointSizeF() - 0.5)
        self.label.setFont(f)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 6, 0)
        lay.setSpacing(3)
        lay.addWidget(self.glyph)
        lay.addWidget(self.label)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.icon = None

    def set(self, text, icon_name):
        self.setVisible(bool(text))
        c = T().c
        ink = c["litany"] if T().ornate else c["text2"]
        radius = 0 if T().ornate else 3
        self.setStyleSheet(f"QFrame#tag {{ border: 1px solid {c['border']}; border-radius: {radius}px; "
                           f"background: transparent; }} QLabel {{ color: {ink}; background: transparent; }}")
        if icon_name != self.icon:
            self.icon = icon_name
            self.glyph.setPixmap(theme.tinted(icon_name, 12, QColor(ink)))
        self.label.setText(T().text(text))


class Chip(QLabel):
    """A small tag (HOLDING, FORWARDING, …)."""

    def __init__(self, text="", tone="normal", parent=None):
        super().__init__(parent)
        self.setFont(T().label_font())
        self.set(text, tone)

    def set(self, text, tone="normal"):
        self.setVisible(bool(text))
        c = T().c
        if T().ornate:
            fg, border, bg = {"accent": ("#ff9a80", "#7a2418", "#2a0d09"),
                              "ok": (c["ok"], "#3c4f25", "#141a0c")}.get(tone, (c["litany"], c["border"], "#15100b"))
            self.setStyleSheet(f"color: {fg}; border: 1px solid {border}; background: {bg}; padding: 1px 7px;")
        else:
            col = {"accent": c["accent"], "ok": c["ok"]}.get(tone, c["text2"])
            self.setStyleSheet(f"color: {col}; border: 1px solid {col}; border-radius: 3px; padding: 0px 6px;")
        self.setText(T().text(text))
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)


class Cog(QWidget):
    """A gear that turns while `spinning` (cogitator only; ~30 fps, only this small widget repaints)."""

    def __init__(self, size=28, teeth=10, color="brass", parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.teeth = teeth
        self.color = color
        self.angle = 0.0
        self.speed = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    def set_spinning(self, on, speed=4.0):
        self.speed = speed
        if on and not self._timer.isActive():
            self._timer.start()
        elif not on:
            self._timer.stop()

    def _tick(self):
        if self.isVisible():
            self.angle = (self.angle + self.speed) % 360
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.width() / 2
        p.translate(s, s)
        p.rotate(self.angle)
        p.scale(s / 100, s / 100)
        p.setPen(Qt.NoPen)
        p.setBrush(T().color(self.color))
        p.drawPath(cog_path(self.teeth, 76, 100, 36))


class Divider(QWidget):
    """A section break; the cogitator skin puts a small cog and a spaced label on a fading rule."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text
        self.setFixedHeight(30 if T().ornate else 28)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        t = T()
        f = t.label_font() if t.ornate else t.font(0, True)
        p.setFont(f)
        label = t.text(self.text)
        fm = QFontMetrics(f)
        y = r.center().y()
        if t.ornate:
            p.save()
            p.translate(9, y)
            p.scale(0.08, 0.08)
            p.setPen(Qt.NoPen)
            p.setBrush(t.color("brass_dk"))
            p.drawPath(cog_path(10, 76, 100, 36))
            p.restore()
            x = 26
            p.setPen(t.color("litany"))
        else:
            x = 0
            p.setPen(t.color("text"))
        p.drawText(QPointF(x, y + fm.ascent() / 2 - 1), label)
        x2 = x + fm.horizontalAdvance(label) + 12
        if t.ornate:
            g = QLinearGradient(x2, 0, r.right(), 0)
            g.setColorAt(0, t.color("border"))
            g.setColorAt(1, QColor(0, 0, 0, 0))
            p.fillRect(QRectF(x2, y, r.right() - x2, 1), g)
        else:
            p.fillRect(QRectF(x2, y, r.right() - x2, 1), t.color("border"))


def hbox(*widgets, spacing=8, margins=(0, 0, 0, 0)):
    lay = QHBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, int):
            lay.addSpacing(w)
        elif isinstance(w, (QHBoxLayout, QVBoxLayout)):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    return lay


def vbox(*widgets, spacing=4, margins=(0, 0, 0, 0)):
    lay = QVBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, (QHBoxLayout, QVBoxLayout)):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    return lay
