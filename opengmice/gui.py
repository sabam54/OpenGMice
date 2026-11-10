"""G Hub style Qt interface for Logitech mice (onboard memory profiles)."""
from __future__ import annotations

import copy
import math
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QPointF, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPen, QRadialGradient)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
                               QSizePolicy, QSlider, QStackedWidget, QTabWidget, QToolButton,
                               QVBoxLayout, QWidget)

from . import actions, hidpp, lighting
from .mouse import Mouse, discover_mice
from .profiles import MODE_ONBOARD, DpiStage, Profile, layout_for

APP_ID = os.environ.get("FLATPAK_ID", "opengmice")   # matches the .desktop file name
ACCENT = "#00b4ff"
LOGO_G = "#2fc9a6"   # teal, deliberately unlike any Logitech brand colour
SHIFT = "#ff9a1f"
BG = "#0b0d10"
PANEL = "#14171c"
PANEL2 = "#1b1f26"
TEXT = "#e8ebef"
DIM = "#8b93a0"

STYLE = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Inter', 'Noto Sans', sans-serif; font-size: 13px; }}
QFrame#panel {{ background: {PANEL}; border-radius: 10px; }}
QLabel, QCheckBox, QTabBar, QWidget#clear {{ background: transparent; }}
QFrame#topbar {{ background: {PANEL}; border-bottom: 1px solid #222832; }}
QLabel#h1 {{ font-size: 20px; font-weight: 600; letter-spacing: 1px; }}
QLabel#h2 {{ font-size: 11px; font-weight: 700; letter-spacing: 2px; color: {DIM}; }}
QLabel#dim {{ color: {DIM}; }}
QLabel#big {{ font-size: 34px; font-weight: 300; }}
QPushButton {{ background: {PANEL2}; border: 1px solid #2a303a; border-radius: 6px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #555c66; border-color: #22262d; }}
QPushButton#primary {{ background: {ACCENT}; color: #00111c; border: none; font-weight: 700; letter-spacing: 1px; }}
QPushButton#primary:disabled {{ background: #1f3644; color: #4f6675; }}
QToolButton#nav {{ background: transparent; border: none; border-left: 3px solid transparent;
                   padding: 14px 6px; color: {DIM}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
QToolButton#nav:checked {{ color: {TEXT}; border-left: 3px solid {ACCENT}; background: {PANEL}; }}
QToolButton#nav:hover {{ color: {TEXT}; }}
QComboBox, QLineEdit {{ background: {PANEL2}; border: 1px solid #2a303a; border-radius: 6px; padding: 6px 8px; }}
QComboBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background: {PANEL2}; selection-background-color: {ACCENT}; selection-color: #00111c; }}
QListWidget {{ background: {PANEL}; border: none; outline: none; }}
QListWidget::item {{ padding: 9px 10px; border-radius: 6px; }}
QListWidget::item:hover {{ background: {PANEL2}; }}
QListWidget::item:selected {{ background: #0d3449; color: {TEXT}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {DIM}; padding: 8px 12px; font-weight: 700; font-size: 11px; letter-spacing: 1px;
               border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #3a414c; background: {PANEL2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QSlider::groove:horizontal {{ height: 4px; background: #2a303a; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QComboBox#devices {{ background: transparent; border: 1px solid transparent; font-size: 19px; font-weight: 600;
                     letter-spacing: 1px; padding: 4px 8px; min-width: 360px; }}
QComboBox#devices:hover {{ border-color: #2a303a; }}
QScrollBar:vertical {{ background: transparent; width: 8px; }}
QScrollBar::handle:vertical {{ background: #2a303a; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""


# ============================================================== background I/O
class Worker(QObject):
    """Runs device I/O on one background thread, results delivered on the GUI thread."""
    _finished = Signal(object, object, object)

    def __init__(self):
        super().__init__()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self._finished.connect(self._deliver)

    def run(self, fn, on_ok=None, on_err=None):
        fut = self.pool.submit(fn)
        fut.add_done_callback(lambda f: self._finished.emit(f, on_ok, on_err))

    def _deliver(self, fut, on_ok, on_err):
        exc = fut.exception()
        if exc is not None:
            if on_err:
                on_err(exc)
            else:
                traceback.print_exception(exc)
        elif on_ok:
            on_ok(fut.result())


# ============================================================== mouse drawing
class MouseView(QWidget):
    """Top-down drawing of a G305-class mouse with clickable buttons and callouts."""
    buttonClicked = Signal(int)

    # label anchors: (side, y fraction)
    CALLOUTS = {0: ("L", 0.16), 1: ("R", 0.16), 2: ("R", 0.32), 3: ("L", 0.44),
                4: ("L", 0.30), 5: ("R", 0.47)}
    NAMES = ["PRIMARY CLICK", "SECONDARY CLICK", "MIDDLE CLICK", "BACK", "FORWARD", "DPI BUTTON"]
    TITLES = ["Primary Click", "Secondary Click", "Middle Click", "Back", "Forward", "DPI Button"]

    def __init__(self):
        super().__init__()
        self.setMinimumSize(520, 520)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.labels: list[str] = []
        self.selected: int | None = None
        self.hover: int | None = None
        self.show_callouts = True
        self.dpi_color: QColor | None = None
        self.logo_colors: list[QColor] = []   # empty = logo LED off / no LED
        self._paths: dict[int, QPainterPath] = {}
        self.setMouseTracking(True)

    def set_labels(self, labels):
        self.labels = list(labels)
        self.update()

    def set_selected(self, i):
        self.selected = i
        self.update()

    def _geometry(self):
        w, h = self.width(), self.height()
        mh = min(h * 0.86, max(200.0, w - 470) / 0.58)
        mw = mh * 0.58
        cx, top = w / 2, (h - mh) / 2
        return cx, top, mw, mh

    def _build(self):
        cx, top, mw, mh = self._geometry()
        L, R, B = cx - mw / 2, cx + mw / 2, top + mh
        body = QPainterPath()
        body.moveTo(cx, top)
        body.cubicTo(cx + mw * 0.42, top, R, top + mh * 0.18, R - mw * 0.02, top + mh * 0.42)
        body.cubicTo(R + mw * 0.02, top + mh * 0.70, cx + mw * 0.40, B, cx, B)
        body.cubicTo(cx - mw * 0.40, B, L - mw * 0.02, top + mh * 0.70, L + mw * 0.02, top + mh * 0.42)
        body.cubicTo(L, top + mh * 0.18, cx - mw * 0.42, top, cx, top)
        split_y = top + mh * 0.40
        gap = mw * 0.012

        def half(sign):
            p = QPainterPath()
            clip = QPainterPath()
            x0 = cx + sign * gap
            x1 = cx + sign * mw
            clip.addRect(QRectF(min(x0, x1), top - 5, abs(x1 - x0), split_y - top + 5))
            p = body.intersected(clip)
            return p

        left_btn, right_btn = half(-1), half(1)
        wheel = QPainterPath()
        ww, wh = mw * 0.09, mh * 0.13
        wheel.addRoundedRect(QRectF(cx - ww / 2, top + mh * 0.13, ww, wh), ww / 2, ww / 2)
        left_btn = left_btn.subtracted(wheel)
        right_btn = right_btn.subtracted(wheel)
        dpi = QPainterPath()
        dw, dh = mw * 0.07, mh * 0.045
        dpi.addRoundedRect(QRectF(cx - dw / 2, top + mh * 0.30, dw, dh), dw / 2, dw / 2)
        side = []
        for i, (y0, y1) in enumerate(((0.36, 0.48), (0.24, 0.355))):
            p = QPainterPath()
            r = QRectF(L - mw * 0.035, top + mh * y0, mw * 0.10, mh * (y1 - y0))
            p.addRoundedRect(r, mw * 0.04, mw * 0.04)
            side.append(p)
        self._body = body
        self._split_y = split_y
        self._paths = {0: left_btn, 1: right_btn, 2: wheel, 3: side[0], 4: side[1], 5: dpi}

    def _hit(self, pos):
        for i in (2, 5, 3, 4, 0, 1):
            if i in self._paths and self._paths[i].contains(QPointF(pos)):
                return i
        return None

    def mouseMoveEvent(self, e):
        h = self._hit(e.position())
        if h != self.hover:
            self.hover = h
            self.setCursor(Qt.CursorShape.PointingHandCursor if h is not None else Qt.CursorShape.ArrowCursor)
            self.update()

    def mousePressEvent(self, e):
        h = self._hit(e.position())
        if h is not None:
            self.buttonClicked.emit(h)

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def paintEvent(self, e):
        self._build()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, top, mw, mh = self._geometry()
        # floor glow
        glow = QRadialGradient(QPointF(cx, top + mh * 0.55), mw * 1.1)
        glow.setColorAt(0, QColor(0, 180, 255, 40))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), glow)
        # body
        grad = QLinearGradient(QPointF(cx - mw / 2, top), QPointF(cx + mw / 2, top + mh))
        grad.setColorAt(0, QColor("#2b3038"))
        grad.setColorAt(1, QColor("#121418"))
        p.setPen(QPen(QColor("#3a404a"), 1.5))
        p.setBrush(grad)
        p.drawPath(self._body)
        # buttons
        for i, path in self._paths.items():
            if i == self.selected:
                p.setBrush(QColor(0, 180, 255, 90))
                p.setPen(QPen(QColor(ACCENT), 2))
            elif i == self.hover:
                p.setBrush(QColor(255, 255, 255, 25))
                p.setPen(QPen(QColor("#6b7380"), 1.5))
            else:
                p.setBrush(QColor("#1d2128") if i in (2, 3, 4, 5) else QColor(0, 0, 0, 0))
                p.setPen(QPen(QColor("#3a404a"), 1.2))
            p.drawPath(path)
        # wheel ribs
        wr = self._paths[2].boundingRect()
        p.setPen(QPen(QColor("#4a515c"), 1))
        for k in range(1, 8):
            y = wr.top() + wr.height() * k / 8
            p.drawLine(QPointF(wr.left() + 3, y), QPointF(wr.right() - 3, y))
        # DPI indicator LED
        dr = self._paths[5].boundingRect()
        led = QColor(self.dpi_color or QColor(ACCENT))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(led)
        p.drawEllipse(QPointF(cx, dr.bottom() + mh * 0.03), mw * 0.012, mw * 0.012)
        # emblem (lit in the logo zone's colour when the mouse has one)
        er = mw * 0.09
        centre = QPointF(cx, top + mh * 0.72)
        if self.logo_colors:
            c0 = self.logo_colors[0]
            c1 = self.logo_colors[-1]
            halo = QRadialGradient(centre, er * 2.6)
            halo.setColorAt(0, QColor(c0.red(), c0.green(), c0.blue(), 110))
            halo.setColorAt(1, QColor(0, 0, 0, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            p.drawEllipse(centre, er * 2.6, er * 2.6)
            ring = QLinearGradient(QPointF(cx - er, centre.y() - er), QPointF(cx + er, centre.y() + er))
            ring.setColorAt(0, c0)
            ring.setColorAt(1, c1)
            p.setPen(QPen(QBrush(ring), 4))
        else:
            p.setPen(QPen(QColor("#2f353e"), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(centre, er, er)
        if self.show_callouts:
            self._paint_callouts(p, cx, top, mw, mh)

    def _paint_callouts(self, p, cx, top, mw, mh):
        f_small = QFont(self.font())
        f_small.setPointSizeF(7.5)
        f_small.setBold(True)
        f_big = QFont(self.font())
        f_big.setPointSizeF(10.5)
        for i, (side, fy) in self.CALLOUTS.items():
            if i not in self._paths:
                continue
            br = self._paths[i].boundingRect()
            anchor = QPointF(br.center().x(), br.center().y())
            if i in (0, 1):
                anchor = QPointF(br.center().x() + (-1 if side == "L" else 1) * br.width() * 0.15, top + mh * 0.18)
            y = top + mh * fy
            x_edge = cx + (-1 if side == "L" else 1) * (mw / 2 + 40)
            x_text = cx + (-1 if side == "L" else 1) * (mw / 2 + 48)
            active = i == self.selected or i == self.hover
            col = QColor(ACCENT) if active else QColor("#4b5360")
            p.setPen(QPen(col, 1.2))
            p.drawLine(anchor, QPointF(x_edge, y))
            p.setBrush(col)
            p.drawEllipse(anchor, 3, 3)
            label = self.labels[i] if i < len(self.labels) else ""
            w = 190
            rect = QRectF(x_text - (w if side == "L" else 0), y - 20, w, 40)
            align = (Qt.AlignmentFlag.AlignRight if side == "L" else Qt.AlignmentFlag.AlignLeft)
            p.setFont(f_small)
            p.setPen(QColor(DIM))
            p.drawText(rect.adjusted(0, 0, 0, -20), align | Qt.AlignmentFlag.AlignBottom, f"G{i + 1}  {self.NAMES[i]}")
            p.setFont(f_big)
            p.setPen(QColor(ACCENT) if active else QColor(TEXT))
            p.drawText(rect.adjusted(0, 20, 0, 0), align | Qt.AlignmentFlag.AlignTop, label)


# ============================================================== DPI track
class DpiTrack(QWidget):
    """Horizontal DPI bar with draggable level markers (log scale)."""
    stageMoved = Signal(int, int)       # index, new dpi (while dragging)
    stageReleased = Signal(int, int)
    stageSelected = Signal(int)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(110)
        self.values: list[int] = []
        self.default = 0
        self.shift = 0
        self.current_dpi: int | None = None
        self.lo, self.hi = 100, 25600
        self.snap = lambda v: v
        self._drag: int | None = None

    def configure(self, lo, hi, snap):
        self.lo, self.hi, self.snap = lo, hi, snap

    def set_stages(self, values, default, shift):
        self.values, self.default, self.shift = list(values), default, shift
        self.update()

    def _x(self, v):
        m = 16
        t = math.log(max(v, self.lo) / self.lo) / math.log(self.hi / self.lo)
        return m + t * (self.width() - 2 * m)

    def _v(self, x):
        m = 16
        t = min(1.0, max(0.0, (x - m) / (self.width() - 2 * m)))
        return self.snap(int(round(self.lo * (self.hi / self.lo) ** t)))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = 70
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#262b33"))
        p.drawRoundedRect(QRectF(16, y - 3, self.width() - 32, 6), 3, 3)
        # filled up to the default level
        if self.values:
            g = QLinearGradient(16, 0, self._x(self.values[self.default]), 0)
            g.setColorAt(0, QColor("#004a6b"))
            g.setColorAt(1, QColor(ACCENT))
            p.setBrush(g)
            p.drawRoundedRect(QRectF(16, y - 3, self._x(self.values[self.default]) - 16, 6), 3, 3)
        # ticks
        p.setPen(QColor("#4b5360"))
        f = QFont(self.font())
        f.setPointSizeF(7.5)
        p.setFont(f)
        for t in (100, 400, 1000, 2000, 4000, 8000, 16000, 32000):
            if self.lo <= t <= self.hi:
                x = self._x(t)
                p.drawLine(QPointF(x, y + 9), QPointF(x, y + 14))
                p.drawText(QRectF(x - 30, y + 15, 60, 14), Qt.AlignmentFlag.AlignCenter,
                           f"{t // 1000}K" if t >= 1000 else str(t))
        # current live dpi caret
        if self.current_dpi:
            x = self._x(self.current_dpi)
            p.setPen(QPen(QColor("#ffffff"), 1.5))
            p.drawLine(QPointF(x, y - 12), QPointF(x, y + 8))
        # value labels: lift a label to a second row when it would collide with its neighbour
        rows, last_x = [], [-1e9, -1e9, -1e9]
        for v in sorted(self.values):
            x = self._x(v)
            row = next((r for r in range(3) if x - last_x[r] > 40), min(range(3), key=lambda r: last_x[r]))
            last_x[row] = x
            rows.append((v, row))
        label_row = dict(rows)
        # markers
        for i, v in enumerate(self.values):
            x = self._x(v)
            is_def, is_shift = i == self.default, i == self.shift and self.shift != self.default
            col = QColor(ACCENT) if is_def else QColor(SHIFT) if is_shift else QColor("#d6dbe2")
            p.setPen(QPen(QColor(BG), 2))
            p.setBrush(col)
            if is_def:
                path = QPainterPath()
                path.moveTo(x, y - 10); path.lineTo(x + 10, y); path.lineTo(x, y + 10); path.lineTo(x - 10, y)
                path.closeSubpath()
                p.drawPath(path)
            else:
                p.drawEllipse(QPointF(x, y), 8, 8)
            p.setPen(QColor("#00111c") if (is_def or not is_shift) else QColor("#1a0d00"))
            p.drawText(QRectF(x - 8, y - 8, 16, 16), Qt.AlignmentFlag.AlignCenter, str(i + 1))
            p.setPen(col)
            ly = y - 30 - 14 * label_row.get(v, 0)
            p.drawText(QRectF(x - 40, ly, 80, 14), Qt.AlignmentFlag.AlignCenter, f"{v}")

    def _nearest(self, x):
        best, bd = None, 14
        for i, v in enumerate(self.values):
            d = abs(self._x(v) - x)
            if d < bd:
                best, bd = i, d
        return best

    def mousePressEvent(self, e):
        self._drag = self._nearest(e.position().x())
        if self._drag is not None:
            self.stageSelected.emit(self._drag)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            v = self._v(e.position().x())
            self.values[self._drag] = v
            self.stageMoved.emit(self._drag, v)
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self.stageReleased.emit(self._drag, self.values[self._drag])
        self._drag = None


# ============================================================== pages
def h2(text):
    l = QLabel(text.upper())
    l.setObjectName("h2")
    return l


def panel():
    f = QFrame()
    f.setObjectName("panel")
    return f


class KeyCapture(QLineEdit):
    captured = Signal(int, int)   # hid usage, modifier mask

    def __init__(self):
        super().__init__()
        self.setPlaceholderText("Click here and press a key combination")
        self.setReadOnly(True)

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta,
                 Qt.Key.Key_AltGr, Qt.Key.Key_Super_L, Qt.Key.Key_Super_R):
            return
        usage = actions.qt_key_to_hid(k)
        if usage is None:
            self.setText("Unsupported key")
            return
        m = e.modifiers()
        mods = 0
        if m & Qt.KeyboardModifier.ControlModifier:
            mods |= 0x01
        if m & Qt.KeyboardModifier.ShiftModifier and k != Qt.Key.Key_Backtab:
            mods |= 0x02
        if k == Qt.Key.Key_Backtab:
            mods |= 0x02
        if m & Qt.KeyboardModifier.AltModifier:
            mods |= 0x04
        if m & Qt.KeyboardModifier.MetaModifier:
            mods |= 0x08
        self.setText(actions.describe(actions.key(usage, mods)))
        self.captured.emit(usage, mods)


class DpiEdit(QLineEdit):
    """Numeric DPI field: type + Enter, scroll wheel or Up/Down arrows to step.

    A plain QLineEdit instead of QSpinBox: with a stylesheet and hidden arrows, QSpinBox
    lays its text field out 1 px wide under some Qt styles (seen in the Flatpak runtime).
    """
    valueChanged = Signal(int)

    def __init__(self, lo, hi, step=50):
        super().__init__()
        self.lo, self.hi, self.step = lo, hi, step
        self.prefix = ""
        self._value = lo
        self.setMinimumWidth(110)
        self.editingFinished.connect(self._parse)

    def value(self):
        return self._value

    def setPrefix(self, prefix):
        self.prefix = prefix
        self._show()

    def setValue(self, v):
        v = max(self.lo, min(self.hi, int(v)))
        changed = v != self._value
        self._value = v
        self._show()
        if changed:
            self.valueChanged.emit(v)

    def _show(self):
        if not self.hasFocus():
            self.setText(f"{self.prefix}{self._value} DPI")

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.setText(str(self._value))
        self.selectAll()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._show()

    def _parse(self):
        digits = "".join(ch for ch in self.text() if ch.isdigit())
        if digits:
            self.setValue(int(digits))
        self._show()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._parse()
            self.setValue(self._value + (self.step if e.key() == Qt.Key.Key_Up else -self.step))
            self.setText(str(self._value))
            return
        super().keyPressEvent(e)

    def wheelEvent(self, e):
        delta = e.angleDelta().y()
        if delta:
            self.setValue(self._value + (self.step if delta > 0 else -self.step))
            if self.hasFocus():
                self.setText(str(self._value))
        e.accept()


class SensitivityPage(QWidget):
    changed = Signal()
    liveDpi = Signal(int, int)

    def __init__(self):
        super().__init__()
        self.profile: Profile | None = None
        self.mouse: Mouse | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        top = panel()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(18, 16, 18, 16)
        tl.addWidget(h2("Sensitivity (DPI)"))
        row = QHBoxLayout()
        self.current = QLabel("—")
        self.current.setObjectName("big")
        row.addWidget(self.current)
        row.addStretch()
        legend = QLabel(f"<span style='color:{ACCENT}'>◆</span> default &nbsp;&nbsp; "
                        f"<span style='color:{SHIFT}'>●</span> DPI shift")
        legend.setObjectName("dim")
        row.addWidget(legend)
        tl.addLayout(row)
        self.track = DpiTrack()
        tl.addWidget(self.track)
        lay.addWidget(top)

        levels = panel()
        ll = QVBoxLayout(levels)
        ll.setContentsMargins(18, 16, 18, 16)
        hdr = QHBoxLayout()
        hdr.addWidget(h2("DPI levels"))
        hdr.addStretch()
        self.xy = QCheckBox("Separate X / Y")
        hdr.addWidget(self.xy)
        ll.addLayout(hdr)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(6)
        ll.addLayout(self.grid)
        add_row = QHBoxLayout()
        self.add_btn = QPushButton("+  Add level")
        add_row.addWidget(self.add_btn)
        add_row.addStretch()
        ll.addLayout(add_row)
        lay.addWidget(levels)

        rate = panel()
        rl = QGridLayout(rate)
        rl.setContentsMargins(18, 16, 18, 16)
        rl.addWidget(h2("Report rate (per second)"), 0, 0, 1, 2)
        self.rate_lbl = QLabel("Wireless")
        rl.addWidget(self.rate_lbl, 1, 0)
        self.rate = QComboBox()
        rl.addWidget(self.rate, 1, 1)
        self.rate_wired_lbl = QLabel("Wired")
        rl.addWidget(self.rate_wired_lbl, 2, 0)
        self.rate_wired = QComboBox()
        rl.addWidget(self.rate_wired, 2, 1)
        self.rate_note = QLabel("Bluetooth always runs at the rate your adapter allows; the values above "
                                "apply to LIGHTSPEED / USB.")
        self.rate_note.setObjectName("dim")
        self.rate_note.setWordWrap(True)
        rl.addWidget(self.rate_note, 3, 0, 1, 2)
        lay.addWidget(rate)
        lay.addStretch()

        self.track.stageMoved.connect(self._track_moved)
        self.track.stageReleased.connect(self._track_released)
        self.track.stageSelected.connect(self._test_stage)
        self.add_btn.clicked.connect(self._add)
        self.xy.toggled.connect(lambda _: self._rebuild())
        self.rate.currentIndexChanged.connect(self._rate_changed)
        self.rate_wired.currentIndexChanged.connect(self._rate_changed)
        self._rows = []

    def bind(self, mouse: Mouse, profile: Profile):
        self.mouse, self.profile = mouse, profile
        self.track.configure(mouse.dpi_min, mouse.dpi_max, mouse.snap_dpi)
        self.xy.blockSignals(True)
        self.xy.setChecked(any(s.x != s.y for s in profile.stages))
        self.xy.blockSignals(False)
        L = layout_for(profile.fmt)
        self.shared_rate = L.rate_wired == L.rate_wireless     # older formats store one rate
        self.rate_lbl.setText("Polling" if self.shared_rate else "Wireless")
        self.rate_wired_lbl.setVisible(not self.shared_rate)
        self.rate_wired.setVisible(not self.shared_rate)
        self.rate_note.setVisible(mouse.bus == "Bluetooth")
        rates = [r for r in (125, 250, 500, 1000, 2000, 4000, 8000) if r <= mouse.max_report_rate()]
        for combo, val in ((self.rate, profile.report_rate), (self.rate_wired, profile.report_rate_wired)):
            combo.blockSignals(True)
            combo.clear()
            for r in rates:
                combo.addItem(f"{r} Hz  ({1000 / r:g} ms)", r)
            if val not in rates:
                combo.addItem(f"{val} Hz", val)
            combo.setCurrentIndex(combo.findData(val))
            combo.blockSignals(False)
        self._rebuild()

    def set_current(self, dpi):
        self.track.current_dpi = dpi[0] if dpi else None
        self.current.setText(f"{dpi[0]} <span style='font-size:13px;color:{DIM}'>DPI</span>"
                             if dpi else "—")
        self.track.update()

    def _rebuild(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._rows = []
        p = self.profile
        if not p:
            return
        sep = self.xy.isChecked()
        for i, s in enumerate(p.stages):
            num = QLabel(str(i + 1))
            num.setFixedWidth(22)
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col = ACCENT if i == p.default_dpi else SHIFT if i == p.shift_dpi else "#d6dbe2"
            num.setStyleSheet(f"background:{col}; color:#00111c; border-radius:11px; font-weight:700; padding:2px;")
            x = self._spin(s.x)
            y = self._spin(s.y)
            y.setVisible(sep)
            x.setPrefix("X " if sep else "")
            x.valueChanged.connect(lambda v, i=i: self._value(i, "x", v))
            y.valueChanged.connect(lambda v, i=i: self._value(i, "y", v))
            d = QPushButton("Default")
            d.setCheckable(True)
            d.setChecked(i == p.default_dpi)
            d.clicked.connect(lambda _, i=i: self._set_default(i))
            sh = QPushButton("Shift")
            sh.setCheckable(True)
            sh.setChecked(i == p.shift_dpi)
            sh.setToolTip("DPI used while a button assigned to 'DPI Shift' is held")
            sh.clicked.connect(lambda _, i=i: self._set_shift(i))
            rm = QPushButton("✕")
            rm.setFixedWidth(34)
            rm.setEnabled(len(p.stages) > 1)
            rm.clicked.connect(lambda _, i=i: self._remove(i))
            for c, w in enumerate((num, x, y, d, sh, rm)):
                self.grid.addWidget(w, i, c)
            self._rows.append((x, y))
        self.add_btn.setEnabled(len(p.stages) < 5)
        self._sync_track()

    def _spin(self, v):
        s = DpiEdit(self.mouse.dpi_min, self.mouse.dpi_max)
        s.blockSignals(True)
        s.setValue(v)
        s.blockSignals(False)
        return s

    def _sync_track(self):
        p = self.profile
        self.track.set_stages([s.x for s in p.stages], p.default_dpi, p.shift_dpi)

    def _value(self, i, axis, v):
        snapped = self.mouse.snap_dpi(v)
        if snapped != v:   # show the value the sensor actually supports
            w = self._rows[i][0 if axis == "x" else 1]
            w.blockSignals(True)
            w.setValue(snapped)
            w.blockSignals(False)
        v = snapped
        s = self.profile.stages[i]
        setattr(s, axis, v)
        if axis == "x" and not self.xy.isChecked():
            s.y = v
        self._sync_track()
        self._test_stage(i)
        self.changed.emit()

    def _track_moved(self, i, v):
        x, y = self._rows[i]
        for w in (x, y) if not self.xy.isChecked() else (x,):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        s = self.profile.stages[i]
        s.x = v
        if not self.xy.isChecked():
            s.y = v

    def _track_released(self, i, v):
        self._test_stage(i)
        self.changed.emit()

    def _test_stage(self, i):
        s = self.profile.stages[i]
        self.liveDpi.emit(s.x, s.y)

    def _set_default(self, i):
        self.profile.default_dpi = i
        self._rebuild()
        self._test_stage(i)
        self.changed.emit()

    def _set_shift(self, i):
        self.profile.shift_dpi = i
        self._rebuild()
        self.changed.emit()

    def _add(self):
        p = self.profile
        last = p.stages[-1]
        v = self.mouse.snap_dpi(min(self.mouse.dpi_max, last.x * 2))
        p.stages.append(DpiStage(v, v, last.lod))
        self._rebuild()
        self.changed.emit()

    def _remove(self, i):
        p = self.profile
        del p.stages[i]
        for attr in ("default_dpi", "shift_dpi"):
            cur = getattr(p, attr)
            if cur == i:
                setattr(p, attr, 0)
            elif cur > i:
                setattr(p, attr, cur - 1)
        self._rebuild()
        self.changed.emit()

    def _rate_changed(self, _):
        if self.profile:
            self.profile.report_rate = self.rate.currentData()
            self.profile.report_rate_wired = (self.profile.report_rate if self.shared_rate
                                              else self.rate_wired.currentData())
            self.changed.emit()


class AssignmentsPage(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.profile: Profile | None = None
        self.button: int | None = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        head = panel()
        hl = QVBoxLayout(head)
        hl.setContentsMargins(18, 16, 18, 16)
        hl.addWidget(h2("Assignments"))
        self.sel_label = QLabel("Select a button on the mouse")
        self.sel_label.setObjectName("h1")
        hl.addWidget(self.sel_label)
        self.sel_cur = QLabel("")
        self.sel_cur.setObjectName("dim")
        hl.addWidget(self.sel_cur)
        btns = QHBoxLayout()
        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)
        for i in range(6):
            b = QPushButton(f"G{i + 1}")
            b.setCheckable(True)
            b.setFixedWidth(52)
            self.btn_group.addButton(b, i)
            btns.addWidget(b)
        btns.addStretch()
        hl.addLayout(btns)
        lay.addWidget(head)

        body = panel()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(10, 10, 10, 10)
        self.tabs = QTabWidget()
        self.lists: dict[str, QListWidget] = {}
        cats = {}
        for a in actions.catalog():
            cats.setdefault(a.category, []).append(a)
        tab_for = {"Mouse": "COMMANDS", "Sensitivity & Profiles": "ACTIONS", "Shortcuts": "COMMANDS",
                   "Media & System": "SYSTEM", "Other": "SYSTEM"}
        pages = {}
        for cat, acts in cats.items():
            tab = tab_for[cat]
            if tab not in pages:
                lw = QListWidget()
                lw.itemClicked.connect(self._picked)
                pages[tab] = lw
            lw = pages[tab]
            hdr = QListWidgetItem(cat.upper())
            hdr.setFlags(Qt.ItemFlag.NoItemFlags)
            f = hdr.font()
            f.setBold(True)
            f.setPointSizeF(8)
            hdr.setFont(f)
            hdr.setForeground(QColor(DIM))
            lw.addItem(hdr)
            for a in acts:
                it = QListWidgetItem("    " + a.label)
                it.setData(Qt.ItemDataRole.UserRole, a.raw)
                lw.addItem(it)
        for name in ("COMMANDS", "ACTIONS", "SYSTEM"):
            self.tabs.addTab(pages[name], name)
            self.lists[name] = pages[name]
        keys = QWidget()
        keys.setObjectName("clear")
        kl = QVBoxLayout(keys)
        kl.setContentsMargins(8, 12, 8, 8)
        kl.addWidget(QLabel("Press the key or shortcut this button should send:"))
        self.capture = KeyCapture()
        kl.addWidget(self.capture)
        hint = QLabel("Modifiers (Ctrl, Shift, Alt, Super) are recorded together with the key.")
        hint.setObjectName("dim")
        hint.setWordWrap(True)
        kl.addWidget(hint)
        kl.addStretch()
        self.tabs.insertTab(1, keys, "KEYS")
        bl.addWidget(self.tabs)
        lay.addWidget(body, 1)

        self.btn_group.idClicked.connect(self.select)
        self.capture.captured.connect(lambda u, m: self._assign(actions.key(u, m)))

    def bind(self, profile: Profile):
        self.profile = profile
        self.select(self.button if self.button is not None else 0)

    def select(self, i):
        self.button = i
        b = self.btn_group.button(i)
        if b:
            b.setChecked(True)
        self.sel_label.setText(f"G{i + 1} · {MouseView.TITLES[i]}")
        if self.profile and i < len(self.profile.buttons):
            self.sel_cur.setText("Currently: " + actions.describe(self.profile.buttons[i]))
        self.capture.clear()
        self.selectedChanged(i)

    def selectedChanged(self, i):  # overridden by window
        pass

    def _picked(self, item):
        raw = item.data(Qt.ItemDataRole.UserRole)
        if raw is not None:
            self._assign(bytes(raw))

    def _assign(self, raw):
        if self.profile is None or self.button is None:
            return
        if self.button == 0 and raw != actions.mouse_button(1):
            if QMessageBox.question(
                    self, "Reassign primary click?",
                    "You are about to remove Left Click from G1. If no other button keeps "
                    "Left Click you will have to use the keyboard to undo this.\n\nContinue?"
            ) != QMessageBox.StandardButton.Yes:
                return
        self.profile.buttons[self.button] = raw
        self.sel_cur.setText("Currently: " + actions.describe(raw))
        self.changed.emit()


class ProfilesPage(QWidget):
    changed = Signal()
    editProfile = Signal(int)
    activate = Signal(int)
    resetProfile = Signal(int)

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        box = panel()
        self.bl = QVBoxLayout(box)
        self.bl.setContentsMargins(18, 16, 18, 16)
        self.bl.addWidget(h2("On-board memory profiles"))
        info = QLabel("Profiles are stored on the mouse itself and keep working on any computer, "
                      "with or without this app. Use a button assigned to 'Cycle Profiles' to switch.")
        info.setWordWrap(True)
        info.setObjectName("dim")
        self.bl.addWidget(info)
        self.rows = QGridLayout()
        self.rows.setVerticalSpacing(8)
        self.bl.addLayout(self.rows)
        lay.addWidget(box)
        lay.addStretch()

    def bind(self, profiles, editing, active_sector):
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, p in enumerate(profiles):
            en = QCheckBox()
            en.setChecked(p.enabled)
            en.setToolTip("Include in the on-board profile cycle")
            en.toggled.connect(lambda v, p=p: (setattr(p, "enabled", v), self.changed.emit()))
            name = QLineEdit(p.name)
            name.setMaxLength(23)
            name.editingFinished.connect(lambda p=p, w=name: self._rename(p, w))
            badge = QLabel("● ACTIVE" if p.sector == active_sector else "")
            badge.setStyleSheet(f"color:{ACCENT}; font-weight:700; font-size:10px;")
            edit = QPushButton("Editing" if i == editing else "Edit")
            edit.setEnabled(i != editing)
            edit.clicked.connect(lambda _, i=i: self.editProfile.emit(i))
            act = QPushButton("Activate")
            act.setEnabled(p.sector != active_sector)
            act.clicked.connect(lambda _, i=i: self.activate.emit(i))
            rst = QPushButton("Reset")
            rst.setToolTip("Restore this profile to the factory default")
            rst.clicked.connect(lambda _, i=i: self.resetProfile.emit(i))
            for c, w in enumerate((en, name, badge, edit, act, rst)):
                self.rows.addWidget(w, i, c)

    def _rename(self, p, w):
        t = w.text().strip()
        if t and t != p.name:
            p.name = t
            self.changed.emit()


PRESET_COLORS = [0xFF2020, 0xFF7A00, 0xFFD400, 0x30FF60, 0x00E0C0, 0x00B4FF,
                 0x3F5BFF, 0xA040FF, 0xFF40C0, 0xFFFFFF]


class ColorSwatch(QPushButton):
    colorChanged = Signal(int)

    def __init__(self, rgb=0x00B4FF):
        super().__init__()
        self.setFixedSize(46, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._pick)
        self.set_color(rgb)

    def set_color(self, rgb):
        self.rgb = rgb
        self.setStyleSheet(f"background:#{rgb:06x}; border:2px solid #2a303a; border-radius:6px;")

    def _pick(self):
        c = QColorDialog.getColor(QColor(f"#{self.rgb:06x}"), self, "Choose colour")
        if c.isValid():
            self.set_color(c.rgb() & 0xFFFFFF)
            self.colorChanged.emit(self.rgb)


class EffectEditor(QFrame):
    """Editor for one 11-byte lighting record."""
    changed = Signal()

    def __init__(self, title, hint):
        super().__init__()
        self.setObjectName("panel")
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(18, 16, 18, 16)
        self.v.setSpacing(10)
        self.v.addWidget(h2(title))
        hl = QLabel(hint)
        hl.setObjectName("dim")
        hl.setWordWrap(True)
        self.v.addWidget(hl)
        self.effect = QComboBox()
        self.v.addWidget(self.effect)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        self.v.addLayout(self.body)
        self.setting: lighting.LightSetting | None = None
        self.effect.currentIndexChanged.connect(self._effect_changed)

    def bind(self, setting, supported):
        self.setting = setting
        self.effect.blockSignals(True)
        self.effect.clear()
        ids = [e for e in supported if e in lighting.EFFECTS]
        if setting.effect not in ids:
            ids.append(setting.effect)
        for eid in ids:
            label = lighting.EFFECTS[eid].name if eid in lighting.EFFECTS else f"Effect 0x{eid:02X} (unknown, kept as is)"
            self.effect.addItem(label, eid)
        self.effect.setCurrentIndex(self.effect.findData(setting.effect))
        self.effect.blockSignals(False)
        self._build()

    def _clear(self, layout):
        while layout.count():
            it = layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                self._clear(it.layout())

    def _build(self):
        self._clear(self.body)
        s = self.setting
        e = lighting.EFFECTS.get(s.effect)
        if e is None:
            return
        if e.note:
            n = QLabel(e.note)
            n.setObjectName("dim")
            self.body.addWidget(n)
        for prm in e.params:
            if prm.name in ("color", "color2"):
                self.body.addLayout(self._color_row(prm.name))
            elif prm.name == "period":
                self.body.addLayout(self._slider_row("Cycle time", "period", 500, 20000, 100,
                                                     lambda v: f"{v / 1000:.1f} s"))
            elif prm.name == "intensity":
                self.body.addLayout(self._slider_row("Brightness", "intensity", 1, 100, 1, lambda v: f"{v}%"))
            elif prm.name == "saturation":
                self.body.addLayout(self._slider_row("Saturation", "saturation", 0, 255, 1,
                                                     lambda v: f"{round(v / 2.55)}%"))
            elif prm.name == "speed":
                self.body.addLayout(self._slider_row("Speed", "speed", 1, 255, 1, str))

    def _color_row(self, key):
        row = QHBoxLayout()
        lbl = QLabel("Colour" if key == "color" else "Second colour")
        lbl.setFixedWidth(110)
        row.addWidget(lbl)
        sw = ColorSwatch(self.setting.values.get(key, 0x00B4FF))
        sw.setToolTip("Pick any colour")
        sw.colorChanged.connect(lambda rgb, k=key: self._set(k, rgb))
        row.addWidget(sw)
        row.addSpacing(10)
        for rgb in PRESET_COLORS:
            b = QPushButton()
            b.setFixedSize(20, 20)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"background:#{rgb:06x}; border:1px solid #11151a; border-radius:10px; padding:0;")
            b.clicked.connect(lambda _, rgb=rgb, k=key, sw=sw: (sw.set_color(rgb), self._set(k, rgb)))
            row.addWidget(b)
        row.addStretch()
        return row

    def _slider_row(self, label, key, lo, hi, step, fmt):
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setFixedWidth(110)
        row.addWidget(lbl)
        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(lo, hi)
        sl.setSingleStep(step)
        sl.setPageStep(step * 10)
        sl.setValue(min(hi, max(lo, self.setting.values.get(key, lo))))
        val = QLabel(fmt(sl.value()))
        val.setFixedWidth(52)
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sl.valueChanged.connect(lambda v: val.setText(fmt(v)))
        sl.sliderReleased.connect(lambda: self._set(key, sl.value()))
        sl.valueChanged.connect(lambda v: None if sl.isSliderDown() else self._set(key, v))
        row.addWidget(sl, 1)
        row.addWidget(val)
        return row

    def _set(self, key, v):
        if self.setting.values.get(key) != v:
            self.setting.values[key] = v
            self.changed.emit()

    def _effect_changed(self, _):
        eid = self.effect.currentData()
        if eid is None or eid == self.setting.effect or eid not in lighting.EFFECTS:
            return
        self.setting = lighting.with_effect(self.setting, eid)
        self._build()
        self.changed.emit()


class LightingPage(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(14)
        self.profile: Profile | None = None
        self.editors: list[tuple[int, EffectEditor]] = []
        foot = QLabel("Lighting is stored in the selected on-board profile and shows on the mouse "
                      "after SAVE TO MOUSE.")
        foot.setObjectName("dim")
        foot.setWordWrap(True)
        self.foot = foot

    def bind(self, mouse: Mouse, profile: Profile):
        self.profile = profile
        while self.lay.count():
            it = self.lay.takeAt(0)
            if it.widget() and it.widget() is not self.foot:
                it.widget().deleteLater()
        self.editors = []
        for z in mouse.zones[:2]:
            for rec, title, hint in ((z.index, f"{z.location} lighting", "Effect while the mouse is in use."),
                                     (2 + z.index, f"{z.location} · passive",
                                      "Effect the firmware switches to in its low-activity (passive) state.")):
                if rec >= len(profile.lighting):
                    continue
                ed = EffectEditor(title.upper(), hint)
                ed.bind(lighting.decode(profile.lighting[rec]), z.effects)
                ed.changed.connect(lambda rec=rec, ed=ed: self._edited(rec, ed))
                self.lay.addWidget(ed)
                self.editors.append((rec, ed))
        self.lay.addWidget(self.foot)
        self.lay.addStretch()

    def _edited(self, rec, ed):
        self.profile.lighting[rec] = lighting.encode(ed.setting)
        self.changed.emit()


def logo_colors(mouse: Mouse | None, profile: Profile | None) -> list[QColor]:
    """Colours to light the drawing's logo with, from the zone-0 primary record."""
    if not mouse or not profile or not mouse.zones or not profile.lighting:
        return []
    s = lighting.decode(profile.lighting[0])
    if s.effect == 0x00:
        return []
    if "color" not in s.values:   # colour cycle and friends
        return [QColor("#ff3b3b"), QColor("#3b7bff")]
    cols = [QColor(f"#{s.values['color']:06x}")]
    if "color2" in s.values:
        cols.append(QColor(f"#{s.values['color2']:06x}"))
    return cols


class InfoPage(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        box = panel()
        self.form = QGridLayout(box)
        self.form.setContentsMargins(18, 16, 18, 16)
        self.form.setVerticalSpacing(10)
        lay.addWidget(box)
        lay.addStretch()

    def bind(self, mouse: Mouse, battery, mode, profile: Profile | None):
        while self.form.count():
            w = self.form.takeAt(0).widget()
            if w:
                w.deleteLater()
        ob = mouse.onboard
        rows = [
            ("Device", mouse.name),
            ("Firmware", mouse.firmware or "—"),
            ("Connection", f"{mouse.bus}  ({mouse.node['path']})"),
            ("Product ID", f"046D:{mouse.pid:04X}"),
            ("HID++ protocol", ".".join(map(str, mouse.dev.protocol or ()))),
            ("Battery", f"{battery.percent}% — {battery.status}" if battery else "—"),
            ("Sensor range", f"{mouse.dpi_min} – {mouse.dpi_max} DPI"),
        ]
        if ob:
            rows += [
                ("Memory mode", "On-board" if mode == MODE_ONBOARD else "Host (software)"),
                ("Profile format", f"{ob.info.fmt}  ({ob.info.profile_count} slots, {ob.info.button_count} buttons)"),
            ]
        if profile:
            rows += [("Sleep after", f"{profile.ps_timeout} s idle"),
                     ("Power off after", f"{profile.po_timeout} s idle")]
        self.form.addWidget(h2("Device information"), 0, 0, 1, 2)
        for r, (k, v) in enumerate(rows, start=1):
            a = QLabel(k)
            a.setObjectName("dim")
            b = QLabel(v)
            b.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.form.addWidget(a, r, 0)
            self.form.addWidget(b, r, 1)


# ============================================================== main window
class BatteryWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(34, 16)
        self.pct = None
        self.charging = False

    def set(self, b):
        self.pct = b.percent if b else None
        self.charging = bool(b and b.charging)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(DIM), 1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(1, 2, 28, 12), 3, 3)
        p.setBrush(QColor(DIM))
        p.drawRect(QRectF(30, 6, 2.5, 4))
        if self.pct is not None:
            col = QColor("#ff4d4d") if self.pct <= 15 else QColor(ACCENT)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(3, 4, 24 * self.pct / 100, 8), 1.5, 1.5)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OpenGMice")
        self.setWindowIcon(QIcon.fromTheme(APP_ID, QIcon.fromTheme("input-mouse")))
        self.resize(1360, 860)
        self.worker = Worker()
        self.mouse: Mouse | None = None
        self.profiles: list[Profile] = []
        self.saved: list[bytes] = []
        self.rom: Profile | None = None
        self.editing = 0
        self.active_sector = None
        self.mode = None
        self.battery = None
        self._busy = False
        self.devices: list[dict] = []
        self._last_paths = None
        self.settings = QSettings("OpenGMice", "OpenGMice")
        self.want_unit = self.settings.value("last_device", "")

        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ---- top bar
        bar = QFrame()
        bar.setObjectName("topbar")
        bar.setFixedHeight(62)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(20, 0, 20, 0)
        logo = QLabel(f"<span style='color:{DIM};font-weight:400'>Open</span>"
                      f"<span style='color:{LOGO_G};font-weight:800'>G</span>Mice")
        logo.setStyleSheet("font-size:19px; letter-spacing:1px; font-weight:700;")
        bl.addWidget(logo)
        bl.addSpacing(28)
        self.dev_label = QLabel("Searching for devices…")
        self.dev_label.setObjectName("h1")
        bl.addWidget(self.dev_label)
        self.device_combo = QComboBox()
        self.device_combo.setObjectName("devices")
        self.device_combo.setToolTip("Connected mice")
        self.device_combo.hide()
        bl.addWidget(self.device_combo)
        bl.addStretch()
        prof_lbl = h2("Profile")
        bl.addWidget(prof_lbl)
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(220)
        bl.addWidget(self.profile_combo)
        bl.addSpacing(24)
        self.conn_label = QLabel("")
        self.conn_label.setObjectName("dim")
        bl.addWidget(self.conn_label)
        bl.addSpacing(10)
        self.batt = BatteryWidget()
        bl.addWidget(self.batt)
        self.batt_label = QLabel("")
        bl.addWidget(self.batt_label)
        v.addWidget(bar)

        # ---- body
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        v.addLayout(body, 1)

        rail = QFrame()
        rail.setFixedWidth(116)
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(0, 18, 0, 18)
        rl.setSpacing(2)
        self.nav = QButtonGroup(self)
        for i, text, icon in ((0, "SENSITIVITY", "speedometer"), (4, "LIGHTING", "color-management"),
                              (1, "ASSIGNMENTS", "input-mouse"), (2, "PROFILES", "view-list-details"),
                              (3, "DEVICE", "help-about")):
            b = QToolButton()
            b.setObjectName("nav")
            b.setText(text)
            b.setIcon(QIcon.fromTheme(icon))
            b.setIconSize(b.iconSize() * 1.6)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setCheckable(True)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.nav.addButton(b, i)
            rl.addWidget(b)
        rl.addStretch()
        body.addWidget(rail)

        self.stack = QStackedWidget()
        self.stack.setFixedWidth(560)
        self.sens = SensitivityPage()
        self.assign = AssignmentsPage()
        self.profpage = ProfilesPage()
        self.info = InfoPage()
        self.light = LightingPage()
        for w in (self.sens, self.assign, self.profpage, self.info, self.light):
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(18, 18, 8, 18)
            wl.addWidget(w)
            self.stack.addWidget(wrap)
        body.addWidget(self.stack)

        right = QVBoxLayout()
        right.setContentsMargins(10, 10, 20, 10)
        self.view = MouseView()
        right.addWidget(self.view, 1)
        actions_row = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("dim")
        actions_row.addWidget(self.status, 1)
        self.revert_btn = QPushButton("Revert")
        self.save_btn = QPushButton("SAVE TO MOUSE")
        self.save_btn.setObjectName("primary")
        self.save_btn.setMinimumWidth(170)
        actions_row.addWidget(self.revert_btn)
        actions_row.addWidget(self.save_btn)
        right.addLayout(actions_row)
        body.addLayout(right, 1)

        # ---- wiring
        self.nav.idClicked.connect(self._nav)
        self.nav.button(0).setChecked(True)
        self.view.buttonClicked.connect(self._mouse_button_clicked)
        self.assign.selectedChanged = lambda i: self.view.set_selected(i) if self.stack.currentIndex() == 1 else None
        for page in (self.sens, self.assign, self.profpage, self.light):
            page.changed.connect(self._changed)
        self.sens.liveDpi.connect(self._live_dpi)
        self.profpage.editProfile.connect(self._edit_profile)
        self.profpage.activate.connect(self._activate)
        self.profpage.resetProfile.connect(self._reset_profile)
        self.profile_combo.activated.connect(self._edit_profile)
        self.device_combo.activated.connect(self._device_chosen)
        self.save_btn.clicked.connect(self._save)
        self.revert_btn.clicked.connect(self._revert)
        self._set_enabled(False)

        self.scan_timer = QTimer(self, interval=2000, timeout=self._scan)
        self.poll_timer = QTimer(self, interval=3000, timeout=self._poll)
        # live DPI preview runs in host mode; go back to on-board mode shortly after the last change
        self.preview_timer = QTimer(self, interval=2500, singleShot=True, timeout=self._end_preview)
        self.scan_timer.start()
        QTimer.singleShot(0, self._scan)

    # ------------------------------------------------------------ helpers
    def _set_enabled(self, on):
        for w in (self.stack, self.view, self.profile_combo):
            w.setEnabled(on)
        self._update_dirty()

    def _say(self, text, error=False):
        self.status.setStyleSheet(f"color:{'#ff6b6b' if error else DIM}")
        self.status.setText(text)

    def _dirty(self):
        if not self.mouse or not self.profiles:
            return False
        size = self.mouse.onboard.info.sector_size
        try:
            return any(p.encode(size) != s for p, s in zip(self.profiles, self.saved))
        except ValueError:
            return True

    def _update_dirty(self):
        d = self._dirty()
        self.save_btn.setEnabled(d and not self._busy)
        self.revert_btn.setEnabled(d and not self._busy)
        if d and not self._busy:
            self._say("Unsaved changes")

    def _changed(self):
        self._refresh_labels()
        self._update_dirty()

    def _refresh_labels(self):
        p = self.current_profile()
        if p:
            self.view.set_labels([actions.describe(b) for b in p.buttons])
            self.view.logo_colors = logo_colors(self.mouse, p)
            self.view.update()

    def current_profile(self) -> Profile | None:
        return self.profiles[self.editing] if self.profiles else None

    # ------------------------------------------------------------ device discovery
    def _scan(self):
        """Runs every 2 s: rediscover mice when hidraw nodes appear or disappear."""
        if self._busy:
            return
        paths = tuple(sorted((n["path"], n["devnum"]) for n in hidpp.find_logitech_hidraw()))
        if paths == self._last_paths and (self.mouse or not paths):
            return
        if not paths:
            self._last_paths = paths
            self._show_devices([])
            self.dev_label.setText("No Logitech mouse found")
            self._say("Connect a mouse by USB, LIGHTSPEED receiver or Bluetooth.")
            return
        self._busy = True
        self.worker.run(discover_mice, lambda devs: self._discovered(devs, paths), self._connect_failed)

    def _discovered(self, devs, paths):
        self._busy = False
        self._last_paths = paths
        self._show_devices(devs)
        if not devs:
            self.dev_label.setText("No supported mouse found")
            self._say("Mice need on-board profile memory (Logitech G series). Move the mouse to wake it.")
            return
        if self.mouse is None:
            target = next((d for d in devs if d["unit"] == self.want_unit), devs[0])
            self._connect(target)

    def _show_devices(self, devs):
        self.devices = devs
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for d in devs:
            self.device_combo.addItem(f"{d['name'].upper()}", d["unit"])
            self.device_combo.setItemData(self.device_combo.count() - 1, d["link"], Qt.ItemDataRole.ToolTipRole)
        current = self.mouse.unit_id if self.mouse else self.want_unit
        i = self.device_combo.findData(current)
        self.device_combo.setCurrentIndex(max(i, 0))
        self.device_combo.blockSignals(False)
        self.device_combo.setVisible(bool(devs))
        self.dev_label.setVisible(not devs)

    def _connect(self, dev):
        self._busy = True
        self._say(f"Connecting to {dev['name']}…")

        def connect():
            last = None
            for n in dev["nodes"]:          # own node first, receiver slot as fallback
                try:
                    m = Mouse(n)
                    profiles, rom = m.onboard.load_all()
                    return m, profiles, rom, m.onboard.get_current_sector(), m.onboard.get_mode(), m.battery()
                except Exception as e:  # noqa: BLE001 - try the next endpoint
                    last = e
            raise last or RuntimeError("mouse did not answer")

        self.worker.run(connect, self._connected, self._connect_failed)

    def _release_mouse(self):
        """Leave preview mode and close the current mouse (queued behind pending I/O)."""
        self.poll_timer.stop()
        self.preview_timer.stop()
        m = self.mouse
        self.mouse = None
        self.profiles = []
        self._set_enabled(False)
        if m:
            def release():
                try:
                    m.end_preview()
                finally:
                    m.close()
            self.worker.run(release, None, lambda e: None)

    def _device_chosen(self, i):
        dev = self.devices[i] if 0 <= i < len(self.devices) else None
        if dev is None or (self.mouse and dev["unit"] == self.mouse.unit_id) or self._busy:
            self._show_devices(self.devices)
            return
        if self._dirty() and QMessageBox.question(
                self, "Unsaved changes", f"Discard unsaved changes to {self.mouse.name}?"
        ) != QMessageBox.StandardButton.Yes:
            self._show_devices(self.devices)
            return
        self._release_mouse()
        self.want_unit = dev["unit"]
        self._connect(dev)

    def _connect_failed(self, e):
        self._busy = False
        if isinstance(e, PermissionError):
            self.dev_label.setText("Permission denied")
            from .cli import udev_install_command
            cmd = udev_install_command()
            self._say("No access to the mouse. Install the udev rule (see the dialog) and replug it.", True)
            if not getattr(self, "_udev_hint_shown", False):
                self._udev_hint_shown = True
                box = QMessageBox(QMessageBox.Icon.Information, "Mouse access needed",
                                  "Linux does not let this app open the mouse yet. Run this once in a "
                                  "terminal, then unplug and replug the mouse (or its receiver):", parent=self)
                box.setInformativeText(cmd)
                box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                box.exec()
        else:
            self.dev_label.setText("Mouse not responding")
            self._say(f"{e} — move the mouse to wake it up.", True)

    def _connected(self, res):
        self._busy = False
        m, profiles, rom, active, mode, battery = res
        self.mouse, self.rom, self.active_sector, self.mode = m, rom, active, mode
        self.profiles = profiles
        size = m.onboard.info.sector_size
        self.saved = [p.encode(size) for p in profiles]
        self.editing = next((i for i, p in enumerate(profiles) if p.sector == active), 0)
        self.want_unit = m.unit_id
        self.settings.setValue("last_device", m.unit_id)
        self._show_devices(self.devices)
        self.conn_label.setText(m.bus.upper())
        self._battery(battery)
        self._populate()
        self._set_enabled(True)
        self.poll_timer.start()
        if active >= 0x100:
            self._say("Mouse is using its factory default. Edit Profile 1 and save to customise it.")
        else:
            self._say("Connected")
        self._poll()

    def _disconnected(self, e):
        self.poll_timer.stop()
        if self.mouse:
            self.mouse.close()
        self.mouse = None
        self.profiles = []
        self._set_enabled(False)
        self._last_paths = None     # rediscover on the next scan tick
        self._say(f"Mouse disconnected ({e})", True)

    def _battery(self, b):
        self.battery = b
        self.batt.set(b)
        self.batt_label.setText(f"{b.percent}%" + (" ⚡" if b.charging else "") if b else "")

    def _poll(self):
        if not self.mouse or self._busy:
            return
        m = self.mouse

        def poll():
            return m.battery(), m.get_dpi()

        def err(e):
            if isinstance(e, OSError) and not isinstance(e, hidpp.HidppTimeout):
                self._disconnected(e)

        self.worker.run(poll, lambda r: (self._battery(r[0]), self.sens.set_current(r[1])), err)

    # ------------------------------------------------------------ binding
    def _populate(self):
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for p in self.profiles:
            self.profile_combo.addItem(p.name + ("   ●" if p.sector == self.active_sector else ""))
        self.profile_combo.setCurrentIndex(self.editing)
        self.profile_combo.blockSignals(False)
        p = self.current_profile()
        self.sens.bind(self.mouse, p)
        self.assign.bind(p)
        self.profpage.bind(self.profiles, self.editing, self.active_sector)
        self.info.bind(self.mouse, self.battery, self.mode, p)
        self.light.bind(self.mouse, p)
        has_leds = bool(self.mouse.zones)
        self.nav.button(4).setVisible(has_leds)
        if not has_leds and self.stack.currentIndex() == 4:
            self.nav.button(0).setChecked(True)
            self._nav(0)
        self._refresh_labels()
        self._update_dirty()

    def _nav(self, i):
        self.stack.setCurrentIndex(i)
        self.view.set_selected(self.assign.button if i == 1 else None)
        if i == 3 and self.mouse:
            self.info.bind(self.mouse, self.battery, self.mode, self.current_profile())

    def _mouse_button_clicked(self, i):
        self.nav.button(1).setChecked(True)
        self._nav(1)
        self.assign.select(i)
        self.view.set_selected(i)

    def _edit_profile(self, i):
        self.editing = i
        self._populate()

    def _live_dpi(self, x, y):
        if self.mouse and not self._busy:
            m = self.mouse
            self.worker.run(lambda: (m.preview_dpi(x, y), m.get_dpi())[1], self.sens.set_current,
                            lambda e: self._say(f"Live DPI preview failed: {e}", True))
            self.preview_timer.start()

    def _end_preview(self):
        if self.mouse and self.mouse.previewing:
            m = self.mouse
            self.worker.run(lambda: (m.end_preview(), m.get_dpi())[1], self.sens.set_current,
                            lambda e: self._say(f"Could not leave preview: {e}", True))

    def _reset_profile(self, i):
        if not self.rom:
            return
        p = self.profiles[i]
        fresh = Profile.decode(p.sector, p.fmt, self.rom.raw, len(p.buttons), p.enabled)
        fresh.name = p.name
        self.profiles[i] = fresh
        self._populate()

    def _revert(self):
        if not self.mouse:
            return
        dev = next((d for d in self.devices if d["unit"] == self.mouse.unit_id), None)
        self._release_mouse()
        if dev:
            self._connect(dev)

    # ------------------------------------------------------------ saving
    def _save(self, activate_index=None):
        if not self.mouse or self._busy:
            return
        m = self.mouse
        profiles = copy.deepcopy(self.profiles)
        target = self.profiles[self.editing if activate_index is None else activate_index]
        if not target.enabled:
            target.enabled = True
            profiles[self.profiles.index(target)].enabled = True
        sector = target.sector
        self._busy = True
        self._update_dirty()
        self._say("Writing to on-board memory…")
        self.save_btn.setText("SAVING…")

        def work():
            ob = m.onboard
            if ob.get_mode() != MODE_ONBOARD:
                ob.set_mode(MODE_ONBOARD)
            m.previewing = False
            ob.save_profiles(profiles, activate=sector)
            return ob.load_all(), ob.get_current_sector(), ob.get_mode()

        def ok(res):
            self._busy = False
            self.save_btn.setText("SAVE TO MOUSE")
            (profiles, rom), self.active_sector, self.mode = res
            self.profiles, self.rom = profiles, rom
            size = m.onboard.info.sector_size
            self.saved = [p.encode(size) for p in profiles]
            self.editing = next((i for i, p in enumerate(profiles) if p.sector == sector), self.editing)
            self._populate()
            self._say("Saved to mouse ✓")
            self._poll()

        def err(e):
            self._busy = False
            self.save_btn.setText("SAVE TO MOUSE")
            self._update_dirty()
            self._say(f"Save failed: {e}", True)
            QMessageBox.warning(self, "Save failed", f"Could not write the profile:\n{e}\n\n"
                                "The mouse falls back to its factory profile if a sector is invalid.")

        self.worker.run(work, ok, err)

    def _activate(self, i):
        if self._dirty() or not self.profiles[i].enabled:
            self._save(activate_index=i)
            return
        m = self.mouse
        sector = self.profiles[i].sector

        def ok(_):
            self.active_sector = sector
            self.editing = i
            self._populate()
            self._say(f"Activated {self.profiles[i].name}")

        self.worker.run(lambda: m.onboard.set_current_sector(sector), ok,
                        lambda e: self._say(f"Activate failed: {e}", True))

    def closeEvent(self, e):
        if self._busy:
            e.ignore()
            QTimer.singleShot(300, self.close)
            return
        if self._dirty():
            r = QMessageBox.question(self, "Unsaved changes", "Save changes to the mouse before closing?",
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                     QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                e.ignore()
                return
            if r == QMessageBox.StandardButton.Save:
                self._save()
                QTimer.singleShot(200, self.close)
                e.ignore()
                return
        if self.mouse:
            # leave the mouse in on-board mode even if a live preview was running
            self.preview_timer.stop()
            self.worker.pool.shutdown(wait=True)
            try:
                self.mouse.end_preview()
            except Exception:  # noqa: BLE001
                pass
            self.mouse.close()
        self.worker.pool.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("OpenGMice")
    app.setDesktopFileName(APP_ID)
    app.setStyleSheet(STYLE)
    w = MainWindow()
    w.show()
    return app.exec()
