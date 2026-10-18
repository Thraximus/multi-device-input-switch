"""Two switchable looks.

Plasma: the native Breeze style and the user's own colour scheme, so the window sits with the rest of the
desktop. Custom widgets draw with palette colours and KDE's positive/neutral/negative text colours.

Cogitator: the machine-cult skin from the Archivum reader (its tokens.cogitator.css):
scorched iron, brass edges, rivets, hazard chevrons, a red status rune used sparingly, Grenze Gotisch for
display type and IBM Plex Mono for everything else. Dark only, by design.

Widgets never hard-code colours: they ask the current theme (`current()`) for a token or a painter, so the
two looks can't drift structurally.
"""
import configparser
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPalette, QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import QApplication, QStyleFactory

FONTS = Path(__file__).resolve().parent / "fonts"

_current = None


def current():
    return _current


def cog_path(teeth, r_root, r_tip, r_hole=0.0):
    """Gear outline centred on (0, 0); the hole is cut with the odd-even fill rule (Archivum's cogD)."""
    path = QPainterPath()
    step = 2 * math.pi / teeth
    for i in range(teeth):
        a = i * step
        for j, (f, r) in enumerate(((0, r_tip), (0.38, r_tip), (0.5, r_root), (0.88, r_root))):
            pt = QPointF(math.cos(a + f * step) * r, math.sin(a + f * step) * r)
            if i == 0 and j == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
    path.closeSubpath()
    if r_hole:
        path.addEllipse(QPointF(0, 0), r_hole, r_hole)
    path.setFillRule(Qt.OddEvenFill)
    return path


def tinted(icon_name, size, color, dpr=1.0):
    """A theme icon recoloured to one flat colour (keeps its alpha)."""
    src = QIcon.fromTheme(icon_name).pixmap(size, size)
    if src.isNull():
        return src
    pix = QPixmap(src.size())
    pix.setDevicePixelRatio(src.devicePixelRatio())
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.drawPixmap(0, 0, src)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(pix.rect(), color)
    p.end()
    return pix


class Theme:
    name = ""
    label = ""
    ornate = False

    def __init__(self, app):
        self.app = app
        self.c = {}

    def color(self, key):
        return QColor(self.c[key])

    def status_color(self, level):
        return self.color({"ok": "ok", "warn": "warn", "bad": "bad", "busy": "info"}.get(level, "muted"))

    def text(self, s):
        """Labels/captions as this theme writes them."""
        return s

    def icon(self, name, size, color_key="text"):
        return tinted(name, size, self.color(color_key))

    # fonts
    def font(self, size_delta=0, bold=False):
        f = QFont(self.base_font)
        f.setPointSizeF(max(6.0, f.pointSizeF() + size_delta))
        f.setBold(bold)
        return f

    def display_font(self, size):
        f = QFont(self.base_font)
        f.setPointSizeF(size)
        f.setBold(True)
        return f

    def label_font(self):
        return self.font(-1)

    # painters (overridden per theme)
    def paint_background(self, p, rect):
        p.fillRect(rect, self.color("ground"))

    def paint_panel(self, p, rect, active=False, hover=False, dim=False):
        raise NotImplementedError

    def paint_rule(self, p, rect):
        raise NotImplementedError

    def paint_button(self, p, rect, text, variant, hover, down, enabled, font):
        raise NotImplementedError

    def paint_toggle(self, p, rect, pos, enabled):
        raise NotImplementedError

    def paint_nav(self, p, rect, text, icon_name, selected, hover):
        raise NotImplementedError


class PlasmaTheme(Theme):
    """Native: Breeze style + the system palette, so it follows the user's colour scheme."""
    name = "plasma"
    label = "Plasma"
    RADIUS = 5.0

    def apply(self, saved):
        app = self.app
        app.setStyleSheet("")
        if "Breeze" in QStyleFactory.keys():
            app.setStyle("Breeze")
        app.setPalette(saved["palette"])
        app.setFont(saved["font"])
        self.base_font = QFont(saved["font"])
        pal = saved["palette"]
        pos, neu, neg, act = _kde_status_colors()
        self.c = {
            "ground": pal.color(QPalette.Window).name(),
            "surface": pal.color(QPalette.Base).name(),
            "surface2": pal.color(QPalette.AlternateBase).name(),
            "text": pal.color(QPalette.WindowText).name(),
            "text2": _mix(pal.color(QPalette.WindowText), pal.color(QPalette.Window), 0.75).name(),
            "muted": pal.color(QPalette.PlaceholderText).name(),
            "border": _mix(pal.color(QPalette.WindowText), pal.color(QPalette.Base), 0.18).name(),
            "accent": pal.color(QPalette.Highlight).name(),
            "on_accent": pal.color(QPalette.HighlightedText).name(),
            "ok": pos, "warn": neu, "bad": neg, "info": act,
        }

    def display_font(self, size):
        f = QFont(self.base_font)
        f.setPointSizeF(self.base_font.pointSizeF() * size / 14.0 + 1)
        f.setWeight(QFont.DemiBold)
        return f

    def paint_panel(self, p, rect, active=False, hover=False, dim=False):
        r = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
        accent = self.color("accent")
        fill = self.color("surface")
        if active:
            fill = _mix(accent, fill, 0.18)
        elif hover:
            fill = _mix(accent, fill, 0.08)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(accent if (active or hover) else self.color("border"), 1.0))
        p.setBrush(fill)
        p.drawRoundedRect(r, self.RADIUS, self.RADIUS)

    def paint_rule(self, p, rect):
        p.fillRect(QRectF(rect.left(), rect.bottom(), rect.width(), 1), self.color("border"))

    def icon(self, name, size, color_key=None):
        # Breeze icons already follow the colour scheme; only recolour on request (e.g. the accent).
        if color_key is None:
            return QIcon.fromTheme(name).pixmap(size, size)
        return tinted(name, size, self.color(color_key))

    def paint_toggle(self, p, rect, pos, enabled):
        """Kirigami-style switch: pill track, highlight when on, round knob."""
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(rect).adjusted(1.5, 3.5, -1.5, -3.5)
        rad = r.height() / 2
        off = self.color("surface")
        on = self.color("accent")
        p.setPen(QPen(_mix(on, self.color("border"), pos) if enabled else self.color("border"), 1))
        p.setBrush(_mix(on, off, pos) if enabled else off)
        p.drawRoundedRect(r, rad, rad)
        d = r.height() + 2
        x = r.left() - 1 + (r.width() - d + 2) * pos
        knob = QRectF(x, r.top() - 1, d, d)
        p.setPen(QPen(_mix(on, self.color("border"), pos) if enabled else self.color("border"), 1))
        p.setBrush(QColor("#fcfcfc") if enabled else self.color("surface2"))
        p.drawEllipse(knob)

    def paint_nav(self, p, rect, text, icon_name, selected, hover):
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(rect).adjusted(6, 2, -6, -2)
        accent = self.color("accent")
        if selected or hover:
            p.setPen(QPen(accent, 1) if selected else Qt.NoPen)
            p.setBrush(_mix(accent, self.color("ground"), 0.35 if selected else 0.12))
            p.drawRoundedRect(r, 3, 3)
        p.drawPixmap(QPointF(r.left() + 10, r.center().y() - 11), self.icon(icon_name, 22))
        p.setPen(self.color("text"))
        p.setFont(self.base_font)
        p.drawText(r.adjusted(42, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, text)


class CogitatorTheme(Theme):
    name = "cogitator"
    label = "Cogitator"
    ornate = True
    CUT = 9.0  # chamfered corners, like the clip-path on Archivum's plates and buttons

    TOKENS = {
        "ground": "#0b0908", "iron": "#100d0a", "surface": "#191410", "surface2": "#1f1913",
        "border": "#3a2f1c", "border_strong": "#6d5526",
        "text": "#ede2cb", "text2": "#b9a887", "muted": "#6b5c3c", "litany": "#8f7a4d",
        "brass": "#b08d3e", "brass_hi": "#d9b45e", "brass_dk": "#6d5526", "amber": "#ffd98a",
        "accent": "#aa1111", "accent_hi": "#e03a2a", "on_accent": "#ffe9d0",
        "ok": "#7fae4f", "warn": "#d99a2b", "bad": "#ff5540", "info": "#9b8fd0",
    }

    def apply(self, saved):
        app = self.app
        for f in FONTS.glob("*.ttf"):
            QFontDatabase.addApplicationFont(str(f))
        app.setStyle("Fusion")
        self.c = dict(self.TOKENS)
        pal = QPalette()
        role = {
            QPalette.Window: "ground", QPalette.WindowText: "text", QPalette.Base: "iron",
            QPalette.AlternateBase: "surface", QPalette.Text: "text", QPalette.Button: "surface",
            QPalette.ButtonText: "brass_hi", QPalette.Highlight: "brass_dk", QPalette.HighlightedText: "amber",
            QPalette.ToolTipBase: "surface2", QPalette.ToolTipText: "text", QPalette.PlaceholderText: "muted",
            QPalette.Link: "brass_hi", QPalette.BrightText: "amber", QPalette.Mid: "border",
            QPalette.Dark: "iron", QPalette.Shadow: "ground", QPalette.Light: "border_strong",
        }
        for r, key in role.items():
            pal.setColor(r, QColor(self.c[key]))
        for r in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
            pal.setColor(QPalette.Disabled, r, QColor(self.c["muted"]))
        app.setPalette(pal)
        self.base_font = QFont("IBM Plex Mono")
        self.base_font.setPointSizeF(9.5)
        app.setFont(self.base_font)
        app.setStyleSheet(self.QSS % self.c)

    QSS = """
    QToolTip { background: %(surface2)s; color: %(text)s; border: 1px solid %(border_strong)s; padding: 4px 8px; }
    QMenu { background: %(surface)s; color: %(text)s; border: 1px solid %(border_strong)s; padding: 4px; }
    QMenu::item { padding: 5px 22px 5px 22px; }
    QMenu::item:selected { background: #2a2010; color: %(amber)s; }
    QMenu::item:disabled { color: %(litany)s; }
    QMenu::separator { height: 1px; background: %(border)s; margin: 4px 8px; }
    QMenu::indicator { width: 10px; height: 10px; margin-left: 6px; }
    QMenu::indicator:checked { background: %(accent)s; border: 1px solid %(accent_hi)s; }
    QLineEdit, QComboBox { background: %(iron)s; color: %(text)s; border: 1px solid %(border_strong)s;
        padding: 5px 8px; selection-background-color: #5a3a10; selection-color: %(amber)s; }
    QLineEdit:focus, QComboBox:focus { border-color: %(brass)s; }
    QComboBox:disabled { color: %(muted)s; border-color: %(border)s; }
    QComboBox QAbstractItemView { background: %(surface)s; color: %(text)s; border: 1px solid %(border_strong)s;
        selection-background-color: #2a2010; selection-color: %(amber)s; }
    QScrollArea { background: transparent; border: none; }
    QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
    QScrollBar::handle:vertical { background: %(border)s; min-height: 30px; }
    QScrollBar::handle:vertical:hover { background: %(brass_dk)s; }
    QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
    QScrollBar::add-page, QScrollBar::sub-page { background: none; }
    QMessageBox, QInputDialog, QFileDialog { background: %(ground)s; }
    QPushButton { background: #16110b; color: %(brass_hi)s; border: 1px solid %(brass_dk)s;
        padding: 6px 16px; text-transform: uppercase; }
    QPushButton:hover { background: #2a2010; color: %(amber)s; }
    QCheckBox { spacing: 8px; }
    QCheckBox::indicator { width: 13px; height: 13px; border: 1px solid %(brass_dk)s; background: %(iron)s; }
    QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent_hi)s; }
    QRadioButton::indicator { width: 12px; height: 12px; border: 1px solid %(brass_dk)s; border-radius: 6px;
        background: %(iron)s; }
    QRadioButton::indicator:checked { background: %(accent)s; border-color: %(accent_hi)s; }
    """

    def text(self, s):
        return s.upper()

    def display_font(self, size):
        f = QFont("Grenze Gotisch")
        f.setPointSizeF(size * 1.25)
        f.setWeight(QFont.Bold)
        try:
            f.setVariableAxis(QFont.Tag("wght"), 700)
        except (AttributeError, TypeError):
            pass
        return f

    def label_font(self):
        f = QFont(self.base_font)
        f.setPointSizeF(7.5)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 122)
        return f

    def font(self, size_delta=0, bold=False):
        f = QFont(self.base_font)
        f.setPointSizeF(max(6.0, self.base_font.pointSizeF() + size_delta))
        if bold:
            f.setWeight(QFont.Medium)
        return f

    def icon(self, name, size, color_key="brass_hi"):
        return tinted(name, size, self.color(color_key))

    # --- materials -----------------------------------------------------------

    def chamfer(self, r, cut=None):
        cut = self.CUT if cut is None else cut
        path = QPainterPath()
        path.moveTo(r.left() + cut, r.top())
        path.lineTo(r.right(), r.top())
        path.lineTo(r.right(), r.bottom() - cut)
        path.lineTo(r.right() - cut, r.bottom())
        path.lineTo(r.left(), r.bottom())
        path.lineTo(r.left(), r.top() + cut)
        path.closeSubpath()
        return path

    def paint_background(self, p, rect):
        p.fillRect(rect, self.color("ground"))
        g = QRadialGradient(QPointF(rect.center().x(), rect.top() - rect.height() * 0.12), rect.width() * 0.75)
        g.setColorAt(0, QColor("#1a1410"))
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(rect, g)
        # Dim circuit traces and corner cogs: decoration only, never animated (it would repaint the window).
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#211a10"), 1.4))
        w, h = rect.width(), rect.height()
        for pts in (((0, .16), (.18, .16), (.18, .08), (.42, .08), (.42, .19), (.66, .19)),
                    ((1, .44), (.86, .44), (.86, .56), (.7, .56)),
                    ((0, .82), (.22, .82), (.22, .9), (.5, .9), (.5, .8), (.74, .8))):
            path = QPainterPath(QPointF(pts[0][0] * w, pts[0][1] * h))
            for x, y in pts[1:]:
                path.lineTo(x * w, y * h)
            p.drawPath(path)
            p.setBrush(QColor("#211a10"))
            p.drawEllipse(QPointF(pts[-1][0] * w, pts[-1][1] * h), 3, 3)
            p.setBrush(Qt.NoBrush)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#17120c"))
        for cx, cy, teeth, r in ((w + 8, h + 8, 14, 110), (-20, h * 0.55, 10, 60)):
            p.save()
            p.translate(cx, cy)
            p.drawPath(cog_path(teeth, r * 0.76, r, r * 0.36))
            p.restore()

    def paint_hazard(self, p, r, opacity=0.5):
        p.save()
        p.setClipRect(r)
        p.setOpacity(opacity)
        p.fillRect(r, QColor("#141009"))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#7d632c"))
        step = 20.0
        x = r.left() - r.height()
        while x < r.right() + r.height():
            band = QPainterPath()
            band.moveTo(x, r.bottom())
            band.lineTo(x + r.height(), r.top())
            band.lineTo(x + r.height() + step / 2, r.top())
            band.lineTo(x + step / 2, r.bottom())
            band.closeSubpath()
            p.drawPath(band)
            x += step
        p.restore()

    def paint_rivets(self, p, r):
        p.setPen(Qt.NoPen)
        for x, y in ((r.left() + 6, r.top() + 6 + self.CUT * 0.3), (r.right() - 6, r.top() + 6),
                     (r.left() + 6, r.bottom() - 6), (r.right() - 6 - self.CUT * 0.3, r.bottom() - 6)):
            g = QRadialGradient(QPointF(x - 0.6, y - 0.6), 2.6)
            g.setColorAt(0, QColor("#a58541"))
            g.setColorAt(1, QColor("#2c2213"))
            p.setBrush(g)
            p.drawEllipse(QPointF(x, y), 2.0, 2.0)

    def paint_panel(self, p, rect, active=False, hover=False, dim=False):
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
        path = self.chamfer(r)
        if active:
            for i, a in ((6, 18), (3, 34)):  # amber glow around the plate holding the devices
                p.setPen(QPen(QColor(255, 217, 138, a), i))
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, QColor("#221a10" if (active or hover) else "#1c1710"))
        g.setColorAt(1, QColor("#14100b"))
        p.setBrush(g)
        edge = "brass_hi" if active else ("brass_dk" if hover else "border")
        p.setPen(QPen(self.color(edge), 1.0))
        p.drawPath(path)
        if active:  # the red status rune along the bottom edge
            p.fillRect(QRectF(r.left() + 1, r.bottom() - 2.5, r.width() - self.CUT - 1, 2), self.color("accent"))
        if not dim:
            self.paint_rivets(p, r)

    def paint_rule(self, p, rect):
        self.paint_hazard(p, QRectF(rect.left(), rect.bottom() - 5, rect.width(), 6), 0.45)

    def paint_button(self, p, rect, text, variant, hover, down, enabled, font):
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
        path = self.chamfer(r, 8)
        danger = variant == "danger"
        primary = variant == "primary"
        if not enabled:
            fill, edge, ink = QColor("#120e09"), self.color("border"), self.color("muted")
        elif danger:
            fill = QColor("#2a0f0a" if hover else "#1c0d0a")
            edge, ink = QColor("#7a2418" if hover else "#5a1a12"), QColor("#ff7a60" if hover else "#e06050")
        elif primary:
            fill = QColor("#7a1010" if hover else "#5a0e0e")
            edge, ink = self.color("accent_hi"), self.color("on_accent")
        else:
            fill = QColor("#2a2010" if hover else "#16110b")
            edge, ink = self.color("brass" if hover else "brass_dk"), self.color("amber" if hover else "brass_hi")
        if down:
            fill = fill.darker(130)
        if hover and enabled:
            p.setPen(QPen(QColor(176, 141, 62, 40), 5))
            p.drawPath(path)
        p.setPen(QPen(edge, 1))
        p.setBrush(fill)
        p.drawPath(path)
        p.setPen(ink)
        p.setFont(font)
        p.drawText(r, Qt.AlignCenter, text)

    def paint_toggle(self, p, rect, pos, enabled):
        """A lever in a slot: brass rivet knob, the slot glows red-amber when transferring."""
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(rect).adjusted(0.5, 2.5, -0.5, -2.5)
        path = self.chamfer(r, 5)
        on = QColor("#5a0e0e")
        off = QColor("#120e08")
        fill = _mix(on, off, pos) if enabled else off
        p.setPen(QPen(self.color("brass_dk" if enabled else "border"), 1))
        p.setBrush(fill)
        p.drawPath(path)
        if pos > 0.01 and enabled:
            g = QLinearGradient(r.topLeft(), r.topRight())
            g.setColorAt(0, QColor(170, 17, 17, int(140 * pos)))
            g.setColorAt(1, QColor(224, 102, 42, int(160 * pos)))
            p.fillPath(path, g)
        d = r.height() - 4
        x = r.left() + 2 + (r.width() - d - 4) * pos
        knob = QRectF(x, r.top() + 2, d, d)
        g = QRadialGradient(knob.center() - QPointF(d * .15, d * .15), d * .7)
        g.setColorAt(0, QColor("#ffd98a" if (pos > .5 and enabled) else "#c9a352"))
        g.setColorAt(1, QColor("#5d4b26"))
        p.setPen(QPen(QColor("#2c2213"), 1))
        p.setBrush(g if enabled else QColor("#3a2f1c"))
        p.drawEllipse(knob)

    def paint_nav(self, p, rect, text, icon_name, selected, hover):
        r = QRectF(rect)
        if selected:
            p.fillRect(r, QColor("#1c150c"))
            p.fillRect(QRectF(r.left(), r.top(), 3, r.height()), self.color("accent"))
            p.setPen(QPen(self.color("border"), 1))
            p.drawLine(r.topLeft() + QPointF(3, 0.5), r.topRight() + QPointF(0, 0.5))
            p.drawLine(r.bottomLeft() + QPointF(3, -0.5), r.bottomRight() + QPointF(0, -0.5))
        ink = "amber" if (selected or hover) else "text2"
        pix = self.icon(icon_name, 18, "brass_hi" if selected else "brass_dk")
        p.drawPixmap(QPointF(r.left() + 16, r.center().y() - 9), pix)
        p.setPen(self.color(ink))
        f = QFont(self.base_font)
        f.setPointSizeF(8.5)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 118)
        p.setFont(f)
        p.drawText(r.adjusted(46, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, text.upper())


def _mix(a, b, t):
    """t of colour a over b."""
    a, b = QColor(a), QColor(b)
    return QColor(round(a.red() * t + b.red() * (1 - t)), round(a.green() * t + b.green() * (1 - t)),
                  round(a.blue() * t + b.blue() * (1 - t)))


def _kde_status_colors():
    """Positive/neutral/negative/active text colours from the KDE colour scheme (Breeze defaults)."""
    vals = {"ForegroundPositive": "39,174,96", "ForegroundNeutral": "246,116,0",
            "ForegroundNegative": "218,68,83", "ForegroundActive": "61,174,233"}
    cfg = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cfg.read(Path.home() / ".config/kdeglobals")
        if cfg.has_section("Colors:View"):
            for k in vals:
                vals[k] = cfg.get("Colors:View", k, fallback=vals[k])
    except (configparser.Error, OSError):
        pass

    def rgb(s):
        try:
            return QColor(*[int(x) for x in s.split(",")[:3]]).name()
        except (ValueError, TypeError):
            return "#888888"
    return tuple(rgb(vals[k]) for k in ("ForegroundPositive", "ForegroundNeutral", "ForegroundNegative",
                                         "ForegroundActive"))


THEMES = {"plasma": PlasmaTheme, "cogitator": CogitatorTheme}
_saved = None


def apply(app, name):
    """Switch the whole application to a theme; returns it."""
    global _current, _saved
    if _saved is None:  # the system look, captured before anything is changed
        _saved = {"palette": QPalette(app.palette()), "font": QFont(app.font()), "style": app.style().name()}
    theme = THEMES.get(name, PlasmaTheme)(app)
    theme.apply(_saved)
    _current = theme
    return theme
