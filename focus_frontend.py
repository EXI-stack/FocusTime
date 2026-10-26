from __future__ import annotations

import math
import os
import sys
from typing import Optional

import focus_backend as backend
from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSequentialAnimationGroup,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

THEME = "system"
ALWAYS_ON_TOP = False

STRINGS = {
    "app_title": "Focus",
    "eyebrow": "A MOMENT FOR YOU",
    "title": "Focus time",
    "subtitle": "One thing at a time. You've got this.",
    "status_idle": "Ready when you are",
    "status_running": "Focusing…",
    "status_paused": "Paused",
    "status_finished": "Done — take a break",
    "action_start": "Start focus",
    "action_pause": "Pause",
    "action_resume": "Resume",
    "preset_label": "{minutes} min",
    "tray_title": "Focus complete",
    "tray_text": "Great work — time for a break.",
}

THEMES = {
    "light": {
        "bg": "#F6F4F1",
        "titlebar": "#EFEDEA",
        "surface": "#FFFFFF",
        "ring_track": "rgba(0,0,0,13)",
        "shadow": "rgba(31,26,22,26)",
        "text": "#262421",
        "text_2": "#6F6B66",
        "accent": "#C66A4D",
        "accent_hover": "#B45B3F",
        "accent_text": "#B4552D",
        "pill_selected_bg": "#F4E3DA",
        "pill_hover_bg": "rgba(0,0,0,10)",
        "header_icon_bg": "#E6E1EF",
        "header_icon_fg": "#7A6FA0",
    },
    "dark": {
        "bg": "#1A1815",
        "titlebar": "#201D19",
        "surface": "#24211C",
        "ring_track": "rgba(255,255,255,18)",
        "shadow": "rgba(0,0,0,115)",
        "text": "#EAE4DB",
        "text_2": "#A0988E",
        "accent": "#D97757",
        "accent_hover": "#E08D63",
        "accent_text": "#E08D63",
        "pill_selected_bg": "rgba(217,119,87,46)",
        "pill_hover_bg": "rgba(255,255,255,15)",
        "header_icon_bg": "#37313E",
        "header_icon_fg": "#B4A6D6",
    },
}

_current_theme = "light"
_active_colors = THEMES["light"]
_window: Optional["FocusWindow"] = None


def format_time(seconds: float | int) -> str:
    total = max(0, math.ceil(float(seconds)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _with_alpha(color: str, alpha: float) -> str:
    qcolor = QColor(color)
    qcolor.setAlphaF(max(0.0, min(1.0, alpha)))
    return f"rgba({qcolor.red()}, {qcolor.green()}, {qcolor.blue()}, {qcolor.alpha()})"


def _resolve_theme(theme: str) -> str:
    if theme in ("light", "dark"):
        return theme
    app = QApplication.instance()
    if app is None:
        return "light"
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
        if scheme == Qt.ColorScheme.Light:
            return "light"
    except Exception:
        pass
    try:
        return "dark" if app.palette().color(QPalette.ColorRole.Window).lightnessF() < 0.5 else "light"
    except Exception:
        return "light"


def _device_pixel_ratio() -> float:
    app = QGuiApplication.instance()
    return app.devicePixelRatio() if app is not None else 2.0


def _create_action_pixmap(kind: str, color: str | QColor, width: int = 20, height: int = 16) -> QPixmap:
    """Renders crisp, sub-pixel aligned vector pixmaps for Play, Pause, and Reset."""
    dpr = _device_pixel_ratio()
    pixmap = QPixmap(int(width * dpr), int(height * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    qcolor = QColor(color)

    if kind == "play":
        cx, cy = 8.0, height / 2.0
        path = QPainterPath()
        # Slightly shifted right (+1px) for optical center of mass of a triangle
        path.moveTo(cx - 3.2, cy - 4.8)
        path.lineTo(cx + 4.8, cy)
        path.lineTo(cx - 3.2, cy + 4.8)
        path.closeSubpath()
        pen = QPen(qcolor, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(qcolor)
        painter.drawPath(path)

    elif kind == "pause":
        cx, cy = 8.0, height / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor)
        bar_w, bar_h = 3.0, 10.8
        painter.drawRoundedRect(QRectF(cx - 4.3, cy - bar_h / 2.0, bar_w, bar_h), 1.4, 1.4)
        painter.drawRoundedRect(QRectF(cx + 1.3, cy - bar_h / 2.0, bar_w, bar_h), 1.4, 1.4)

    elif kind == "reset":
        cx, cy = width / 2.0, height / 2.0
        radius = 6.8
        arc_rect = QRectF(cx - radius, cy - radius, radius * 2.0, radius * 2.0)

        pen = QPen(qcolor, 1.85, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Counter-clockwise arc from 135° spanning 285°
        start_deg = 135.0
        span_deg = -285.0
        path = QPainterPath()
        path.arcMoveTo(arc_rect, start_deg)
        path.arcTo(arc_rect, start_deg, span_deg)
        painter.drawPath(path)

        # Crisp chevron arrowhead at the start of the counter-clockwise sweep (135°)
        rad = math.radians(start_deg)
        tip = QPointF(cx + radius * math.cos(rad), cy - radius * math.sin(rad))
        head = QPainterPath()
        head.moveTo(tip.x() - 0.4, tip.y() - 4.0)
        head.lineTo(tip.x() - 0.5, tip.y() + 0.3)
        head.lineTo(tip.x() + 3.8, tip.y() + 0.2)
        painter.drawPath(head)

    painter.end()
    return pixmap


def _generate_qss(c: dict) -> str:
    disabled_text = _with_alpha(c["text_2"], 0.4)
    return f"""
QLabel {{ background: transparent; color: {c['text']}; }}
QLabel#TitleBarLabel {{ font-size: 14px; font-weight: 600; }}
QLabel#Eyebrow {{ color: {c['accent_text']}; font-size: 11px; font-weight: 700; letter-spacing: 0.8px; }}
QLabel#Title {{ font-family: "Source Serif Pro", Georgia, "Times New Roman", serif; font-size: 34px; font-weight: 500; }}
QLabel#Subtitle {{ color: {c['text_2']}; font-size: 14px; }}
QLabel#TimeLabel {{ font-family: Inter, "SF Pro Display", "SF Pro Text", "Segoe UI", sans-serif; font-size: 56px; font-weight: 200; }}
QLabel#StatusLabel {{ color: {c['text_2']}; font-size: 13px; }}
QPushButton {{ background: transparent; border: none; color: {c['text']}; }}
QPushButton#Pill {{ background: {c['surface']}; color: {c['text_2']}; border-radius: 17px; font-size: 13px; padding: 0 14px; }}
QPushButton#Pill:hover {{ background: {c['pill_hover_bg']}; }}
QPushButton#Pill[selected="true"] {{ background: {c['pill_selected_bg']}; color: {c['accent_text']}; font-weight: 600; }}
QPushButton#Pill:disabled {{ color: {disabled_text}; }}
QPushButton#PrimaryButton {{ background: {c['accent']}; color: #FFFFFF; border-radius: 12px; font-size: 15px; font-weight: 600; padding: 0 22px; }}
QPushButton#PrimaryButton:hover {{ background: {c['accent_hover']}; }}
QPushButton#PrimaryButton:pressed {{ background: {c['accent_hover']}; }}
QPushButton#ResetButton {{ border-radius: 10px; }}
QPushButton#ResetButton:hover {{ background: {c['pill_hover_bg']}; }}
"""


def apply_theme() -> None:
    global _current_theme, _active_colors
    _current_theme = _resolve_theme(THEME)
    _active_colors = THEMES[_current_theme]
    if _window is None:
        return
    _window.setStyleSheet(_generate_qss(_active_colors))
    _window._circle.shadow_effect.setColor(QColor(_active_colors["shadow"]))
    _window._circle.refresh_time_label_metrics()
    _window._refresh_icons()
    _window.update()
    _window._titlebar.update()
    _window._circle.update()
    icon = _window._create_icon()
    _window.setWindowIcon(icon)
    if _window._tray is not None:
        _window._tray.setIcon(icon)


class _BackendBridge(QObject):
    event = Signal(str, object)

    def emit_event(self, event: str, data: dict) -> None:
        self.event.emit(event, data)


class ClockIcon(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(26, 26)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = _active_colors

        # Rounded badge background
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(c["header_icon_bg"]))
        painter.drawRoundedRect(QRectF(0.5, 0.5, 25.0, 25.0), 7.5, 7.5)

        # Sub-pixel centered clock face
        fg = QColor(c["header_icon_fg"])
        pen = QPen(fg, 1.85, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        center = QPointF(13.0, 13.0)
        painter.drawEllipse(center, 7.2, 7.2)

        # Connected clock hands
        hands = QPainterPath()
        hands.moveTo(13.0, 8.8)
        hands.lineTo(13.0, 13.0)
        hands.lineTo(15.8, 14.7)
        painter.drawPath(hands)


class TrafficButton(QPushButton):
    def __init__(self, kind: str, tooltip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self.setFixedSize(12, 12)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = self.underMouse()

        if self._kind == "close":
            fill = QColor("#E25B4E" if hovered else "#EC6A5E")
        else:
            fill = QColor("#E5B13D" if hovered else "#F4BF4F")

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawEllipse(QRectF(0.5, 0.5, 11.0, 11.0))

        if hovered:
            pen = QPen(QColor(0, 0, 0, 150), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            if self._kind == "close":
                painter.drawLine(QPointF(3.8, 3.8), QPointF(8.2, 8.2))
                painter.drawLine(QPointF(8.2, 3.8), QPointF(3.8, 8.2))
            else:
                painter.drawLine(QPointF(3.4, 6.0), QPointF(8.6, 6.0))


class TimerCircle(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(260, 260)
        self._progress = 0.0
        self._time_label = QLabel("25:00")
        self._time_label.setObjectName("TimeLabel")
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self._time_label.font()
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self._time_label.setFont(font)

        self._status_label = QLabel(STRINGS["status_idle"])
        self._status_label.setObjectName("StatusLabel")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addStretch(1)
        layout.addWidget(self._time_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)

        self.shadow_effect = QGraphicsDropShadowEffect(self)
        self.shadow_effect.setBlurRadius(24)
        self.shadow_effect.setOffset(0, 0)
        self.setGraphicsEffect(self.shadow_effect)

    def set_time(self, text: str) -> None:
        self._time_label.setText(text)

    def set_status(self, text: str) -> None:
        self._status_label.setText(text)

    def set_progress(self, progress: float) -> None:
        self._progress = max(0.0, min(1.0, float(progress)))
        self.update()

    def refresh_time_label_metrics(self) -> None:
        self._time_label.ensurePolished()
        fm = QFontMetrics(self._time_label.font())
        self._time_label.setMinimumWidth(max(fm.horizontalAdvance("88:88"), fm.horizontalAdvance("9:99:99")) + 12)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = _active_colors

        # 5px inset prevents the 6px stroke + antialiasing fringe from clipping at the poles
        rect = QRectF(self.rect()).adjusted(5.0, 5.0, -5.0, -5.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(c["surface"]))
        painter.drawEllipse(rect)

        track_pen = QPen(QColor(c["ring_track"]), 6.0)
        painter.setPen(track_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)

        if self._progress > 0:
            arc_pen = QPen(QColor(c["accent"]), 6.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(arc_pen)
            painter.drawArc(rect, 90 * 16, int(-self._progress * 360 * 16))


class TitleBar(QWidget):
    def __init__(self, window: "FocusWindow") -> None:
        super().__init__(window)
        self._window = window
        self._drag_offset: QPoint | None = None
        self.setObjectName("TitleBar")
        self.setFixedHeight(44)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(0)

        center = QWidget(self)
        center.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        center_layout = QHBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(8)
        center_layout.addWidget(ClockIcon(center))

        title = QLabel(STRINGS["app_title"], center)
        title.setObjectName("TitleBarLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        center_layout.addWidget(title)

        right = QWidget(self)
        right.setFixedWidth(52)
        right_layout = QHBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        right_layout.addStretch(1)

        min_btn = TrafficButton("minimize", "Minimize", right)
        min_btn.clicked.connect(self._window.hide)
        close_btn = TrafficButton("close", "Close", right)
        close_btn.clicked.connect(self._window.close)
        right_layout.addWidget(min_btn)
        right_layout.addWidget(close_btn)

        layout.addWidget(center)
        layout.addStretch(1)
        layout.addWidget(right)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        r = QRectF(self.rect())
        radius = 14.0
        path.moveTo(r.left(), r.bottom())
        path.lineTo(r.left(), r.top() + radius)
        path.arcTo(QRectF(r.left(), r.top(), radius * 2, radius * 2), 180.0, -90.0)
        path.lineTo(r.right() - radius, r.top())
        path.arcTo(QRectF(r.right() - radius * 2, r.top(), radius * 2, radius * 2), 90.0, -90.0)
        path.lineTo(r.right(), r.bottom())
        path.closeSubpath()
        painter.fillPath(path, QColor(_active_colors["titlebar"]))

    def mousePressEvent(self, event) -> None:
        self._window._stop_pulsate()
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class FocusWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
        if ALWAYS_ON_TOP:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("Root")
        self.setWindowTitle(STRINGS["app_title"])
        self.setFixedSize(440, 580)

        initial_state = backend.get_state()
        self._state = initial_state["state"]
        self._selected_preset = initial_state["preset_min"]
        self._notified_by_backend = False
        self._tray: Optional[QSystemTrayIcon] = None
        self._pulsate_group: Optional[QSequentialAnimationGroup] = None
        self._preset_buttons: list[QPushButton] = []
        self._play_icon = QIcon()
        self._pause_icon = QIcon()

        self._bridge = _BackendBridge()
        self._unsubscribe = backend.subscribe(self._bridge.emit_event)
        self._bridge.event.connect(self._on_backend_event)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._titlebar = TitleBar(self)
        root.addWidget(self._titlebar)
        self._content = QWidget(self)
        self._content.setObjectName("Content")
        root.addWidget(self._content)

        self._build_content(self._content)
        self._build_shortcuts()
        self._rebuild_presets()
        self._refresh_icons()
        self._create_tray()
        self._sync_from_backend()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_active_colors["bg"]))
        painter.drawRoundedRect(QRectF(self.rect()), 14.0, 14.0)

    def _build_content(self, content: QWidget) -> None:
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addSpacing(24)

        for object_name, text, spacing in (
            ("Eyebrow", STRINGS["eyebrow"], 8),
            ("Title", STRINGS["title"], 6),
            ("Subtitle", STRINGS["subtitle"], 20),
        ):
            label = QLabel(text)
            label.setObjectName(object_name)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
            layout.addSpacing(spacing)

        self._circle = TimerCircle()
        layout.addWidget(self._circle, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(20)

        presets = QWidget()
        preset_layout = QHBoxLayout(presets)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.addStretch(1)
        self._presets_layout = QHBoxLayout()
        self._presets_layout.setSpacing(10)
        preset_layout.addLayout(self._presets_layout)
        preset_layout.addStretch(1)
        layout.addWidget(presets)
        layout.addSpacing(14)

        actions = QWidget()
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.addStretch(1)

        self._primary_button = QPushButton()
        self._primary_button.setObjectName("PrimaryButton")
        self._primary_button.setFixedHeight(46)
        self._primary_button.setMinimumWidth(190)
        self._primary_button.setIconSize(QSize(20, 16))
        self._primary_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._primary_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._primary_button.clicked.connect(self._on_primary_clicked)

        self._reset_button = QPushButton()
        self._reset_button.setObjectName("ResetButton")
        self._reset_button.setFixedSize(40, 40)
        self._reset_button.setIconSize(QSize(20, 20))
        self._reset_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_button.setToolTip("Reset")
        self._reset_button.clicked.connect(self._on_reset_clicked)

        action_layout.addWidget(self._primary_button)
        action_layout.addSpacing(14)
        action_layout.addWidget(self._reset_button)
        action_layout.addStretch(1)
        layout.addWidget(actions)
        layout.addSpacing(20)
        layout.addStretch(1)

    def _refresh_icons(self) -> None:
        self._play_icon = QIcon(_create_action_pixmap("play", "#FFFFFF", 20, 16))
        self._pause_icon = QIcon(_create_action_pixmap("pause", "#FFFFFF", 20, 16))

        reset_icon = QIcon()
        normal_pix = _create_action_pixmap("reset", _active_colors["text_2"], 20, 20)
        disabled_color = QColor(_active_colors["text_2"])
        disabled_color.setAlphaF(0.38)
        disabled_pix = _create_action_pixmap("reset", disabled_color, 20, 20)
        reset_icon.addPixmap(normal_pix, QIcon.Mode.Normal)
        reset_icon.addPixmap(disabled_pix, QIcon.Mode.Disabled)
        self._reset_button.setIcon(reset_icon)
        self._update_controls()

    def _build_shortcuts(self) -> None:
        for key, slot in (
            ("Ctrl+T", self._toggle_theme),
            (Qt.Key.Key_Space, self._on_primary_clicked),
            ("R", self._on_reset_shortcut),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(slot)

    def _rebuild_presets(self) -> None:
        while self._presets_layout.count():
            item = self._presets_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._preset_buttons = []
        state = backend.get_state()
        for minutes in state["presets_min"]:
            button = QPushButton(STRINGS["preset_label"].format(minutes=minutes))
            button.setObjectName("Pill")
            button.setFixedHeight(34)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty("minutes", minutes)
            button.setProperty("selected", minutes == state["preset_min"])
            button.clicked.connect(lambda checked=False, m=minutes: self._on_preset_clicked(m))
            self._presets_layout.addWidget(button)
            self._preset_buttons.append(button)

    def _update_preset_selection(self) -> None:
        for button in self._preset_buttons:
            button.setProperty("selected", button.property("minutes") == self._selected_preset)
            button.style().unpolish(button)
            button.style().polish(button)

    def _update_preset_enabled(self) -> None:
        enabled = self._state not in (backend.STATE_RUNNING, backend.STATE_PAUSED)
        for button in self._preset_buttons:
            button.setEnabled(enabled)

    def _on_preset_clicked(self, minutes: int) -> None:
        self._stop_pulsate()
        if self._state not in (backend.STATE_RUNNING, backend.STATE_PAUSED):
            backend.select_preset(minutes)

    def _on_primary_clicked(self) -> None:
        self._stop_pulsate()
        if self._state == backend.STATE_RUNNING:
            backend.pause()
        elif self._state == backend.STATE_PAUSED:
            backend.resume()
        else:
            backend.start()

    def _on_reset_clicked(self) -> None:
        self._stop_pulsate()
        backend.reset()

    def _on_reset_shortcut(self) -> None:
        if self._reset_button.isEnabled():
            self._stop_pulsate()
            backend.reset()

    def _toggle_theme(self) -> None:
        global THEME
        THEME = "dark" if _current_theme == "light" else "light"
        apply_theme()

    def _bring_to_front(self) -> None:
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()
        if os.name == "nt":
            try:
                import ctypes

                hwnd = int(self.winId())
                ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
                ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

    def _start_pulsate(self) -> None:
        self._stop_pulsate()
        group = QSequentialAnimationGroup(self)
        fade_out = QPropertyAnimation(self, b"windowOpacity")
        fade_out.setDuration(1000)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.4)
        fade_out.setEasingCurve(QEasingCurve.Type.InOutCubic)
        fade_in = QPropertyAnimation(self, b"windowOpacity")
        fade_in.setDuration(1000)
        fade_in.setStartValue(0.4)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.Type.InOutCubic)
        group.addAnimation(fade_out)
        group.addAnimation(fade_in)
        group.setLoopCount(-1)
        self._pulsate_group = group
        group.start()

    def _stop_pulsate(self) -> None:
        group = self._pulsate_group
        if group is not None:
            group.stop()
            self._pulsate_group = None
        self.setWindowOpacity(1.0)

    def _update_tray_tooltip(self, remaining: float | int | None = None) -> None:
        if self._tray is None:
            return
        if self._state in (backend.STATE_RUNNING, backend.STATE_PAUSED):
            secs = backend.get_state()["remaining_sec"] if remaining is None else remaining
            self._tray.setToolTip(f"Focus Time · {format_time(secs)}")
        else:
            self._tray.setToolTip("Focus Time")

    def mousePressEvent(self, event) -> None:
        self._stop_pulsate()
        super().mousePressEvent(event)

    def _on_backend_event(self, event: str, data: object) -> None:
        if event == "tick" and self._state == backend.STATE_RUNNING and isinstance(data, dict):
            remaining = float(data.get("remaining_sec", 0.0))
            total = max(1.0, float(data.get("total_sec", 1.0)))
            self._circle.set_time(format_time(remaining))
            self._circle.set_progress(1.0 - remaining / total)
            self._update_window_title(remaining)
            self._update_tray_tooltip(remaining)
            return
        if event == "finished" and isinstance(data, dict):
            self._notified_by_backend = bool(data.get("notified", False))
        if event == "presets_changed":
            self._rebuild_presets()
        self._sync_from_backend()
        if event == "finished":
            self._circle.set_time("00:00")
            self._circle.set_progress(1.0)
            if not self._notified_by_backend:
                self._show_tray_notification()
            self._bring_to_front()
            self._start_pulsate()

    def _sync_from_backend(self) -> None:
        state = backend.get_state()
        self._state = state["state"]
        self._selected_preset = state["preset_min"]
        remaining, total = state["remaining_sec"], max(1, state["total_sec"])
        self._circle.set_time(format_time(remaining))
        progress = (
            0.0
            if self._state == backend.STATE_IDLE
            else 1.0
            if self._state == backend.STATE_FINISHED
            else 1.0 - float(remaining) / total
        )
        self._circle.set_progress(progress)
        self._update_controls()
        self._update_preset_selection()
        self._update_window_title(remaining)
        self._update_tray_tooltip(remaining)

    def _update_controls(self) -> None:
        status = {
            backend.STATE_IDLE: STRINGS["status_idle"],
            backend.STATE_RUNNING: STRINGS["status_running"],
            backend.STATE_PAUSED: STRINGS["status_paused"],
            backend.STATE_FINISHED: STRINGS["status_finished"],
        }
        self._circle.set_status(status.get(self._state, STRINGS["status_idle"]))
        if self._state == backend.STATE_RUNNING:
            self._primary_button.setIcon(self._pause_icon)
            self._primary_button.setText(STRINGS["action_pause"])
        elif self._state == backend.STATE_PAUSED:
            self._primary_button.setIcon(self._play_icon)
            self._primary_button.setText(STRINGS["action_resume"])
        else:
            self._primary_button.setIcon(self._play_icon)
            self._primary_button.setText(STRINGS["action_start"])
        self._reset_button.setEnabled(self._state != backend.STATE_IDLE)
        self._update_preset_enabled()

    def _update_window_title(self, seconds: float | int | None = None) -> None:
        if self._state in (backend.STATE_RUNNING, backend.STATE_PAUSED):
            seconds = backend.get_state()["remaining_sec"] if seconds is None else seconds
            self.setWindowTitle(f"{format_time(seconds)} · {STRINGS['app_title']}")
        else:
            self.setWindowTitle(STRINGS["app_title"])

    def _create_icon(self) -> QIcon:
        dpr = _device_pixel_ratio()
        size = 64
        pixmap = QPixmap(int(size * dpr), int(size * dpr))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Accent circle base
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_active_colors["accent"]))
        painter.drawEllipse(QRectF(4.0, 4.0, 56.0, 56.0))

        # Inner white clock ring & hands
        pen = QPen(QColor("#FFFFFF"), 4.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(32.0, 32.0), 16.5, 16.5)

        hands = QPainterPath()
        hands.moveTo(32.0, 22.0)
        hands.lineTo(32.0, 32.0)
        hands.lineTo(39.0, 36.0)
        painter.drawPath(hands)

        painter.end()
        return QIcon(pixmap)

    def _create_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(self._create_icon(), self)
        self._tray.setToolTip("Focus Time")
        menu = QMenu(self)
        show_action = menu.addAction("Open Focus")
        show_action.triggered.connect(self._restore_from_tray)
        menu.addSeparator()
        exit_action = menu.addAction("Exit")
        exit_action.triggered.connect(self.close)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        self._update_tray_tooltip()

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._restore_from_tray()

    def _show_tray_notification(self) -> None:
        if self._tray is None:
            return
        try:
            self._tray.showMessage(
                STRINGS["tray_title"],
                STRINGS["tray_text"],
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        try:
            self._stop_pulsate()
            self._unsubscribe()
            if self._tray is not None:
                self._tray.hide()
            backend.stop()
        finally:
            event.accept()


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
        app.setApplicationName("Focus Time")
        app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(True)
    return app


def launch() -> FocusWindow:
    global _window
    _ensure_app()
    if _window is None:
        _window = FocusWindow()
        apply_theme()
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window


def run() -> int:
    app = _ensure_app()
    launch()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run())