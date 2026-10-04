from __future__ import annotations

import json
import math
import os
import platform
import random
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

from PyQt6.QtCore import (
    QEasingCurve, QLineF, QMimeData, QObject, QParallelAnimationGroup, QPointF,
    QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont, QIcon,
    QFontDatabase, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPolygonF,
    QPen, QPixmap, QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QGridLayout, QInputDialog, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar,
)

try:
    from core.avatar import HoloAvatar
except Exception:      # pragma: no cover — HUD must never die over cosmetics
    HoloAvatar = None


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# Single source of truth for the release name — the window title, the header
# badge and the readme must never disagree again.
APP_VERSION  = "MARK LIV"
APP_PROTOCOL = APP_VERSION.split()[-1]

_DEFAULT_W, _DEFAULT_H = 1280, 820
_MIN_W,     _MIN_H     = 1024, 640
_LEFT_W  = 220
_RIGHT_W = 292

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"
_HUD_ICON_FONT = "Segoe UI Symbol"


class C:
    BG        = "#02050a"
    PANEL     = "#050b12"
    PANEL2    = "#07121c"
    BORDER    = "#123340"
    BORDER_B  = "#007d91"
    BORDER_A  = "#075968"
    PRI       = "#00d9ff"
    PRI_DIM   = "#00a8ff"
    PRI_GHO   = "#06202a"
    ACC       = "#ff8a24"
    ACC2      = "#6c63ff"
    GREEN     = "#00ff9c"
    GREEN_D   = "#00aa68"
    RED       = "#ff3b5c"
    MUTED_C   = "#ff3b5c"
    TEXT      = "#b8d8e5"
    TEXT_DIM  = "#638391"
    TEXT_MED  = "#95b5c0"
    WHITE     = "#e8f7ff"
    DARK      = "#030911"
    BAR_BG    = "#091821"


# Keys tied to the accent colour — status colours (ACC, GREEN, RED…) stay fixed
_HUE_LINKED = (
    "BG", "PANEL", "PANEL2", "BORDER", "BORDER_B", "BORDER_A",
    "PRI", "PRI_DIM", "PRI_GHO", "TEXT", "TEXT_DIM", "TEXT_MED",
    "WHITE", "DARK", "BAR_BG",
)
_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}

DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]


def apply_ui_accent(accent_hex: str) -> bool:
    """
    Re-derives the whole teal-family palette from the chosen accent colour
    (hue shift — brightness/saturation ratios are preserved, design stays intact).
    Painted elements (HUD, waveform, metrics) pick up the new colour on the next
    frame; stylesheet-based panels pick it up when they are rebuilt.
    """
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    base_h            = _hsv(_PALETTE_DEFAULTS["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh   = acc_h - base_h
    grey = acc_s < 0.08   # near-grey accent → the whole theme is desaturated

    for key, hex0 in _PALETTE_DEFAULTS.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        setattr(C, key, "#{:02x}{:02x}{:02x}".format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)))
    return True


def current_palette() -> dict[str, str]:
    """A snapshot of the accent-linked colours currently on class C."""
    return {k: getattr(C, k) for k in _HUE_LINKED}


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    """
    LIVE full theme change. Replaces the old palette colours with the new ones
    in EVERY widget's stylesheet across the app and repaints them. This way the
    colour change applies INSTANTLY across the whole interface — panels, buttons,
    borders included — not just the painted elements. No restart needed.
    """
    mapping = {old[k].lower(): new[k].lower()
               for k in old if old[k].lower() != new.get(k, old[k]).lower()}
    if not mapping:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        try:
            ss = w.styleSheet()
            if ss:
                s2 = ss
                for o, n in mapping.items():
                    if o in s2:
                        s2 = s2.replace(o, n)
                if s2 != ss:
                    w.setStyleSheet(s2)
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


class _AiOsBackdrop(QWidget):
    """Subtle animated wallpaper for the desktop shell, painted without assets."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(50)

    def _advance(self):
        if not self.isVisible():
            return
        self._phase = (self._phase + 0.012) % 1.0
        self.update()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        self._timer.start()
        super().showEvent(event)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        bounds = QRectF(0, 0, w, h)

        base = QLinearGradient(0, 0, w, h)
        base.setColorAt(0.0, QColor("#02050a"))
        base.setColorAt(0.48, QColor("#06101a"))
        base.setColorAt(1.0, QColor("#020710"))
        p.fillRect(bounds, base)

        for x_ratio, y_ratio, radius, color in (
            (0.08, 0.48, 0.48, C.PRI),
            (0.92, 0.55, 0.44, C.ACC2),
            (0.51, 0.52, 0.39, C.PRI_DIM),
        ):
            cx, cy = w * x_ratio, h * y_ratio
            glow = QRadialGradient(cx, cy, min(w, h) * radius)
            glow.setColorAt(0.0, qcol(color, 22))
            glow.setColorAt(0.55, qcol(color, 7))
            glow.setColorAt(1.0, qcol(color, 0))
            p.fillRect(bounds, QBrush(glow))

        step = max(26, round(min(w, h) / 30))
        p.setPen(QPen(QColor(0, 190, 220, 12), 1))
        for x in range(0, w + 1, step):
            p.drawLine(x, 0, x, h)
        for y in range(0, h + 1, step):
            p.drawLine(0, y, w, y)

        cx, cy = w * 0.5, h * 0.51
        orbit = min(w * 0.38, h * 0.46)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for scale, alpha, width in ((1.0, 20, 1.0), (0.82, 12, 1.0), (0.62, 8, 1.0)):
            rx, ry = orbit * scale, orbit * scale * 0.46
            p.setPen(QPen(QColor(0, 205, 236, alpha), width))
            p.drawEllipse(QRectF(cx - rx, cy - ry, rx * 2, ry * 2))

        # Angular circuit traces stay close to the edges, leaving the center uncluttered.
        for side in (-1, 1):
            sx = w * (0.09 if side < 0 else 0.91)
            direction = 1 if side < 0 else -1
            for index in range(5):
                sy = h * (0.16 + index * 0.17)
                length = w * (0.08 + (index % 2) * 0.035)
                path = QPainterPath(QPointF(sx, sy))
                path.lineTo(sx + direction * length * 0.45, sy)
                path.lineTo(sx + direction * length * 0.45, sy + h * 0.055)
                path.lineTo(sx + direction * length, sy + h * 0.055)
                p.setPen(QPen(QColor(0, 205, 236, 24), 1.0))
                p.drawPath(path)
                node = QPointF(sx + direction * length, sy + h * 0.055)
                p.setPen(QPen(QColor(0, 220, 255, 45), 1))
                p.setBrush(QColor(0, 220, 255, 14))
                p.drawEllipse(node, 3.5, 3.5)
                p.setBrush(Qt.BrushStyle.NoBrush)

        streams = (
            (QPointF(0, h * 0.22), QPointF(w * 0.36, h * 0.08),
             QPointF(w * 0.65, h * 0.31), QPointF(w, h * 0.17)),
            (QPointF(0, h * 0.78), QPointF(w * 0.34, h * 0.91),
             QPointF(w * 0.64, h * 0.67), QPointF(w, h * 0.83)),
            (QPointF(w * 0.05, h), QPointF(w * 0.18, h * 0.66),
             QPointF(w * 0.34, h * 0.74), QPointF(w * 0.48, h * 0.46)),
            (QPointF(w * 0.95, 0), QPointF(w * 0.84, h * 0.32),
             QPointF(w * 0.68, h * 0.26), QPointF(w * 0.53, h * 0.51)),
        )
        for index, (start, c1, c2, end) in enumerate(streams):
            path = QPainterPath(start)
            path.cubicTo(c1, c2, end)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(0, 185, 225, 16), 1.0))
            p.drawPath(path)
            p.setPen(QPen(QColor(0, 220, 255, 58), 1.2))
            point = path.pointAtPercent((self._phase + index * 0.23) % 1.0)
            p.setBrush(QColor(0, 220, 255, 60))
            p.drawEllipse(point, 2.3, 2.3)
            p.setBrush(Qt.BrushStyle.NoBrush)

        for index in range(20):
            x = ((index * 0.61803398875 + self._phase * (0.12 + index % 3 * 0.025)) % 1.0) * w
            y = ((index * 0.38196601125 + (index % 4) * 0.13) % 1.0) * h
            alpha = 20 + int(18 * (0.5 + 0.5 * math.sin(self._phase * math.tau + index)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(75, 211, 255, alpha))
            p.drawEllipse(QPointF(x, y), 1.0 + index % 2, 1.0 + index % 2)

        p.end()


# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok:  object = None   # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.download_mbps: float | None = None
        self.upload_mbps: float | None = None
        self.ping_ms: float | None = None
        self.internet_online: bool | None = None
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        try:
            self._last_net = psutil.net_io_counters()
        except psutil.Error:
            self._last_net = None
        self._last_net_t = time.time()
        self._network_tick = 0
        self._running = True
        # Probe caches — GPU (NVML) and temperature (WMI) are the expensive
        # queries; initialise their handles once and reuse them instead of
        # rebuilding a connection on every poll.
        self._slow_tick = 0            # gpu/temp refreshed every 3rd cycle
        self._pynvml    = None         # cached pynvml module + device handle
        self._pynvml_h  = None
        self._pynvml_ok = None         # None=untested, False=unavailable here
        self._nv_unix   = None         # cached (lib, dev) for Linux/macOS NVML
        self._wmi_conn  = None         # cached WMI connection (creating one is slow)
        self._wmi_ok    = None         # None=untested, False=unavailable here
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(2.0)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        try:
            nc = psutil.net_io_counters()
        except psutil.Error:
            nc = None
        now = time.time()
        dt  = now - self._last_net_t
        if nc is not None and self._last_net is not None and dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            if sent >= 0 and recv >= 0:
                net  = (sent + recv) / (1024 * 1024)
                upload_mbps = sent * 8 / 1_000_000
                download_mbps = recv * 8 / 1_000_000
            else:
                net = 0.0
                upload_mbps = None
                download_mbps = None
        else:
            net = 0.0
            upload_mbps = None
            download_mbps = None
        self._last_net   = nc
        self._last_net_t = now
        self._network_tick += 1

        if self._network_tick % 5 == 1:
            ping_ms = self._probe_internet()
            internet_online = ping_ms is not None
        else:
            with self._lock:
                ping_ms = self.ping_ms
                internet_online = self.internet_online

        # GPU and temperature change slowly and are the most expensive probes
        # (NVML / WMI) — refresh them every 3rd cycle (~6 s) instead of every
        # cycle, reusing the previous reading in between.
        self._slow_tick = (self._slow_tick + 1) % 3
        if self._slow_tick == 1:
            gpu = self._get_gpu()
            tmp = self._get_temp()
        else:
            gpu = self.gpu
            tmp = self.tmp

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.download_mbps = download_mbps
            self.upload_mbps = upload_mbps
            self.ping_ms = ping_ms
            self.internet_online = internet_online
            self.gpu = gpu
            self.tmp = tmp

    @staticmethod
    def _probe_internet() -> float | None:
        """Measure TCP connection latency to a public resolver without blocking the UI."""
        started = time.perf_counter()
        try:
            with socket.create_connection(("1.1.1.1", 443), timeout=1.2):
                return (time.perf_counter() - started) * 1000
        except OSError:
            return None

    def _get_gpu(self) -> float:
        # pynvml — subprocess-free; initialise once and reuse the handle.
        # Re-initialising NVML on every poll is slow, so cache it and stop
        # retrying pynvml entirely once it proves unavailable here.
        if self._pynvml_ok is not False:
            try:
                if self._pynvml_h is None:
                    import pynvml  # type: ignore
                    pynvml.nvmlInit()
                    self._pynvml    = pynvml
                    self._pynvml_h  = pynvml.nvmlDeviceGetHandleByIndex(0)
                    self._pynvml_ok = True
                return float(self._pynvml.nvmlDeviceGetUtilizationRates(self._pynvml_h).gpu)
            except Exception:
                self._pynvml_ok = False

        # Windows: nvml.dll via ctypes (already cached in _nvml_gpu_windows)
        if _OS == "Windows":
            return _nvml_gpu_windows()

        # Linux / macOS: libnvidia-ml shared lib via ctypes — init once, reuse
        try:
            import ctypes

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            if self._nv_unix is None:
                _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"
                nv = ctypes.CDLL(_lib)
                nv.nvmlInit_v2()
                dev = ctypes.c_void_p()
                nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
                self._nv_unix = (nv, dev)

            nv, dev = self._nv_unix
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0   # N/A — zero subprocess on all platforms

    def _get_temp(self) -> float:
        # psutil — works on Linux; occasionally Windows with driver support
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows: wmi module (pure Python COM, zero subprocess). Reuse a single
        # connection — building a fresh wmi.WMI() on every poll spins up a COM
        # connection each time and is very slow. Give up after one failure.
        if _OS == "Windows" and self._wmi_ok is not False:
            try:
                if self._wmi_conn is None:
                    import wmi  # type: ignore
                    self._wmi_conn = wmi.WMI(namespace="root/wmi")
                tz = self._wmi_conn.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception:
                self._wmi_ok   = False
                self._wmi_conn = None

        return -1.0   # N/A — zero subprocess on all platforms

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "download_mbps": self.download_mbps,
                "upload_mbps": self.upload_mbps,
                "ping_ms": self.ping_ms,
                "internet_online": self.internet_online,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

class HudCanvas(QWidget):
    def __init__(self, face_path: str, assistant_name: str = "J.A.R.V.I.S", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"
        self._assistant_name = assistant_name

        # The holographic head that fills the HUD. If it could not be imported
        # we fall back to the old glowing core so the panel is never empty.
        self._avatar = None
        if HoloAvatar is not None:
            try:
                self._avatar = HoloAvatar()
            except Exception:
                self._avatar = None

        # Which centrepiece to draw. Read once here and changed live by the
        # settings toggle; the avatar object is kept either way so switching
        # back is instant and costs no reload.
        try:
            from memory.config_manager import get_hud_style
            self.hud_style = get_hud_style()
        except Exception:
            self.hud_style = "face"
        self._core_phase = 0.0

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._step_t     = time.time()
        self._blink      = True
        self._blink_tick = 0

        # Rescaled-face cache: the smooth rescale is expensive, so we keep the
        # last result and only rebuild it when the (quantised) size changes.

        # Static grid-dot layer, pre-rendered once per size/theme into a pixmap
        # so paintEvent blits it in one call instead of thousands of drawPoint()s.
        self._grid_cache: QPixmap | None = None
        self._grid_key = None
        # Repaint throttle counter (idle frames drop to ~20 Hz — see _step()).
        self._paint_tick = 0

        # Live audio reactivity: _live_amp is written from the audio threads
        # (0.0–1.0), _amp_disp is the smoothed value the paint code reads.
        self._live_amp  = 0.0
        self._amp_disp  = 0.0
        # (frames, start_time, hop) posted by the playback thread — see
        # push_visemes(). None means "no schedule; use the plain level".
        self._visemes = None
        self._vis_i = None        # first schedule frame not yet handed to the mouth
        self._base_scale = 1.0    # slow "breathing" target; amp is added per-frame
        self._base_halo  = 55.0

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
        except Exception:
            pass

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: hand over a schedule of (level, openness, width) frames.

        The playback thread writes up to 200 ms of audio in one go, so a single
        averaged level would only move the mouth five times a second — enough to
        flap, nowhere near enough to articulate. It instead posts the whole
        slice's worth of 20 ms frames here and `_step()` plays them out against
        the wall clock, in step with the audio going to the speakers.

        `at` is the wall-clock time this batch will *begin to sound*, which the
        caller tracks as a playback cursor. It is not the time of the call, and
        the difference is the whole point: `stream.write` returns once the buffer
        accepts the samples, so consecutive batches are handed over far faster
        than they play. Anchoring each one to "now" made every batch start while
        its predecessor was still sounding, so each schedule replaced the last
        after a couple of frames and the mouth only ever played the opening
        instant of every 200 ms — the reason it did not match the words.

        Successive batches are therefore *appended* into one continuous
        timeline, not swapped in. A paragraph is one schedule; the mouth stops
        falling into a gap at every chunk boundary and having to climb back out.
        """
        try:
            if not frames:
                return
            hop = max(1e-3, float(hop))
            at = float(at)
            new = list(frames)
            cur = self._visemes
            if cur is not None:
                old, t0, ohop = cur
                if abs(ohop - hop) < 1e-6:
                    # Where in the existing timeline does this batch land?
                    i = int(round((at - t0) / hop))
                    if 0 <= i <= len(old) + 1:
                        # Continues (or slightly overlaps) what is already
                        # queued: extend rather than restart. Drop whatever has
                        # already been played so the list cannot grow without
                        # bound over a long reply.
                        merged = old[:i] + new
                        played = int((time.time() - t0) / hop) - 2
                        if played > 60:
                            merged = merged[played:]
                            t0 += played * hop
                            if self._vis_i is not None:
                                self._vis_i = max(0, self._vis_i - played)
                        self._visemes = (merged, t0, hop)
                        return
            self._visemes = (new, at, hop)
            self._vis_i = None
        except Exception:
            pass

    def set_audio_level(self, level: float) -> None:
        """Thread-safe entry point for the audio threads. Stores the louder of
        the incoming level and the current value so brief gaps between chunks
        don't make the waveform stutter; _step() decays it back down."""
        try:
            lv = float(level)
        except (TypeError, ValueError):
            return
        if lv < 0.0:
            lv = 0.0
        elif lv > 1.0:
            lv = 1.0
        if lv > self._live_amp:
            self._live_amp = lv

    def _make_grid(self, W: int, H: int) -> QPixmap:
        """Pre-render the static grid-dot background into a transparent pixmap so
        paintEvent can blit it once per frame instead of running a nested
        drawPoint() loop across the whole widget every 16 ms."""
        pm = QPixmap(max(1, W), max(1, H))
        pm.fill(Qt.GlobalColor.transparent)
        gp = QPainter(pm)
        gp.setPen(QPen(qcol(C.PRI_GHO), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                gp.drawPoint(x, y)
        gp.end()
        return pm

    def _step(self):
        self._tick += 1
        now = time.time()

        # ── Live audio reactivity ────────────────────────────────────────────
        # A viseme schedule, if one is playing, gives both the level and the
        # mouth shape for this exact instant; otherwise fall back to the peak
        # level the audio threads pushed in.
        v_open = v_wide = v_level = None
        v_seq = None
        sched = self._visemes
        if sched is not None:
            frames, t0, hop = sched
            i = int((now - t0) / hop)
            if 0 <= i < len(frames):
                # Hand over *every* frame since the last tick, not just the one
                # under the cursor. This timer runs at 60 Hz but the paint is
                # throttled and the machine may be busy, so a tick can span two
                # or three 20 ms frames — and a consonant closure is only two
                # frames long. Sampling one and discarding the rest is how the
                # closures between words went missing.
                j = self._vis_i if self._vis_i is not None else i
                v_seq = frames[max(0, j):i + 1]
                self._vis_i = max(j, i + 1)
                v_level, v_open, v_wide = frames[i]
                if v_seq:
                    peak = max(f[0] for f in v_seq)
                    if peak > self._live_amp:
                        self._live_amp = peak
            elif i >= len(frames):
                self._visemes = None        # schedule spent
                self._vis_i = None

        # Audio threads push peaks into _live_amp; decay it toward silence so
        # gaps between chunks fade out instead of freezing, then smooth it.
        self._live_amp *= 0.86
        self._amp_disp += (self._live_amp - self._amp_disp) * 0.45
        amp = self._amp_disp

        # The avatar animates off the very same smoothed level the waveform
        # uses — one audio source, so the mouth can never drift out of sync.
        dt = now - self._step_t
        self._step_t = now
        # Integrated, not derived from absolute time: multiplying wall-clock by
        # a rate that changes with state jumps the rings the instant JARVIS
        # starts talking. Same lesson the head's sway taught.
        self._core_phase += min(0.10, max(0.0, dt))

        if self._avatar is not None and self.hud_style == "face":
            self._avatar.step(dt, amp, speaking=self.speaking,
                              muted=self.muted, state=self.state,
                              v_open=v_open, v_wide=v_wide or 0.0,
                              v_level=v_level, v_seq=v_seq,
                              v_hop=(sched[2] if sched is not None else 0.02))
        else:
            # Fallback core: slow "breathing" base target, lifted by the level.
            if now - self._last_t > (0.12 if self.speaking else 0.5):
                if self.speaking:
                    self._base_scale = 1.03
                    self._base_halo  = 122.0
                elif self.muted:
                    self._base_scale = random.uniform(0.998, 1.002)
                    self._base_halo  = random.uniform(15, 28)
                else:
                    self._base_scale = random.uniform(1.001, 1.008)
                    self._base_halo  = random.uniform(48, 68)
                self._last_t = now

            if self.muted:
                self._tgt_scale, self._tgt_halo = self._base_scale, self._base_halo
            elif self.speaking:
                self._tgt_scale = self._base_scale + amp * 0.13
                self._tgt_halo  = self._base_halo  + amp * 95.0
            else:
                self._tgt_scale = self._base_scale + amp * 0.06
                self._tgt_halo  = self._base_halo  + amp * 75.0

            sp = 0.38 if self.speaking else (0.30 if amp > 0.02 else 0.15)
            self._scale += (self._tgt_scale - self._scale) * sp
            self._halo  += (self._tgt_halo  - self._halo)  * sp

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
            _blinked = True
        else:
            _blinked = False

        # Repaint throttling — advancing the animation state above is cheap at
        # 60 Hz, but the paint is heavy. Active (speaking, audio, thinking) runs
        # at ~30 Hz, which is the frame rate animation has used for talking
        # characters forever and is indistinguishable here; idle drops to ~20 Hz
        # so a sleeping HUD stops pinning a CPU core. The visuals stay smooth
        # either way because the animation state keeps stepping at 60 Hz.
        self._paint_tick = (self._paint_tick + 1) % 6
        active = (self.speaking or amp > 0.02
              or self.state in ("THINKING", "PROCESSING", "EXECUTING"))
        if _blinked or (self._paint_tick % 2 == 0 if active
                        else self._paint_tick % 3 == 0):
            # Nothing is on screen when the window is hidden or minimised, so
            # rendering the avatar into it is pure waste — and this app is meant
            # to sit running all day. The animation state above keeps stepping,
            # so it picks up mid-motion instead of snapping when you come back.
            if self._on_screen():
                self.update()

    def _on_screen(self) -> bool:
        """True only when this canvas can actually be seen by the user."""
        try:
            if not self.isVisible():
                return False
            win = self.window()
            return not (win.isMinimized() or win.isHidden())
        except Exception:
            return True      # never let a visibility check stop the HUD drawing

    # ── reactor core ─────────────────────────────────────────────────────────
    # The centrepiece for anyone who did not want a face looking back at them.
    # Built from the same budget as the head — software QPainter, no GPU — and
    # from the same principle: everything on it means something. The rings turn
    # at a rate the state sets, the spectrum ring is the real audio level, and
    # the core brightens with the voice. Nothing here is decoration that moves
    # for its own sake, which is what made the old glowing orb feel dead.

    def _core_colours(self):
        if self.muted:
            return qcol(C.MUTED_C), qcol(C.MUTED_C)
        if self.speaking:
            return qcol(C.PRI), qcol(C.ACC)
        if self.state in ("THINKING", "PROCESSING", "EXECUTING"):
            return qcol(C.PRI), qcol(C.ACC2)
        if self.state == "LISTENING":
            return qcol(C.PRI), qcol(C.GREEN)
        return qcol(C.PRI), qcol(C.PRI_DIM)

    def _paint_core(self, p: QPainter, cx: float, cy: float, r: float,
                    W: float = 0.0, H: float = 0.0):
        """Draw the reactor at (cx, cy) with outer radius r, using the whole
        canvas (W x H) for the marks that frame it."""
        main, acc = self._core_colours()
        bg = qcol(C.BG)
        amp = self._amp_disp
        t = self._core_phase
        live = (self.speaking or amp > 0.04) and not self.muted

        def blend(col: QColor, a: float) -> QColor:
            """Pre-mix onto the background instead of asking Qt to composite.
            The raster engine's opaque path is several times faster than its
            translucent one, and everything here is a line or an arc."""
            k = max(0.0, min(1.0, a))
            return QColor(int(bg.red()   + (col.red()   - bg.red())   * k),
                          int(bg.green() + (col.green() - bg.green()) * k),
                          int(bg.blue()  + (col.blue()  - bg.blue())  * k))

        p.setBrush(Qt.BrushStyle.NoBrush)

        # 1. The atmosphere. One radial gradient doing what a stack of discs did
        #    badly: a wide, soft body of light that gives the thing presence
        #    before any detail is read. This single element decides whether the
        #    HUD looks vast or looks small, so it is drawn first and drawn big.
        # Concentrated rather than spread: a gradient reaching the outer rim
        # washes the whole disc a flat dim blue and reads as fog. Ending it at
        # two thirds leaves it a body of light with somewhere to fall off to,
        # which is what makes it look lit rather than tinted.
        lift = 1.0 + 0.55 * amp + (0.18 if self.speaking else 0.0)
        p.setPen(Qt.PenStyle.NoPen)
        for gr, a0, a1 in ((r * 0.70, 0.48, 0.0), (r * 0.34, 0.60, 0.0)):
            g = QRadialGradient(cx, cy, gr)
            g.setColorAt(0.00, blend(main, min(0.95, a0 * lift)))
            g.setColorAt(0.45, blend(main, min(0.95, a0 * lift * 0.52)))
            g.setColorAt(0.78, blend(main, min(0.95, a0 * lift * 0.18)))
            g.setColorAt(1.00, blend(main, a1))
            p.setBrush(QBrush(g))
            p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # 2. Frame marks at the corners of the whole canvas, not of the circle.
        #    They are what set the scale: the eye reads the reactor as filling
        #    the room rather than sitting in the middle of it.
        if W > 40 and H > 40:
            m, arm = min(W, H) * 0.035, min(W, H) * 0.055
            p.setPen(QPen(blend(main, 0.45), 1.4))
            for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                x = cx + sx * (W / 2 - m)
                y = cy + sy * (H / 2 - m)
                p.drawLine(QLineF(x, y, x - sx * arm, y))
                p.drawLine(QLineF(x, y, x, y - sy * arm))

        # 3. Crosshair across the full canvas, broken around the core so it
        #    frames the reactor rather than crossing it.
        p.setPen(QPen(blend(main, 0.16), 1))
        gap = r * 0.62
        if W > 40:
            p.drawLine(QLineF(cx - W / 2, cy, cx - gap, cy))
            p.drawLine(QLineF(cx + gap, cy, cx + W / 2, cy))
        if H > 40:
            p.drawLine(QLineF(cx, cy - H / 2, cx, cy - gap))
            p.drawLine(QLineF(cx, cy + gap, cx, cy + H / 2))

        # 4. Two thin outer circles. Sparse on purpose — a dense ring reads as a
        #    grey band at this size, and restraint is what made the original
        #    look expensive.
        for rr, a in ((1.00, 0.54), (0.93, 0.28)):
            rad = r * rr
            p.setPen(QPen(blend(main, a), 1))
            p.drawEllipse(QRectF(cx - rad, cy - rad, rad * 2, rad * 2))

        # 5. Long, sparse graduations: 24 majors reaching well in from the rim,
        #    with shorter minors between them.
        major, minor = [], []
        for i in range(72):
            a = math.radians(i * 5.0)
            ca, sa = math.cos(a), math.sin(a)
            if i % 3 == 0:
                major.append(QLineF(cx + ca * r * 0.885, cy + sa * r * 0.885,
                                    cx + ca * r * 0.985, cy + sa * r * 0.985))
            else:
                minor.append(QLineF(cx + ca * r * 0.945, cy + sa * r * 0.945,
                                    cx + ca * r * 0.985, cy + sa * r * 0.985))
        p.setPen(QPen(blend(main, 0.65), 1.3))
        p.drawLines(major)
        p.setPen(QPen(blend(main, 0.32), 1))
        p.drawLines(minor)

        # 6. Sweeping arcs. Long spans, not dashes — the original's grandeur
        #    came from a few big strokes. Speed is the state: idle drifts,
        #    thinking hurries, speaking runs.
        rate = 1.0 + (1.9 if self.state in ("THINKING", "PROCESSING", "EXECUTING") else 0.0) \
                   + (1.2 if self.speaking else 0.0)
        for k, (rr, span, count, dirn, col, a, wid) in enumerate((
                (0.955, 118, 2, +1, acc,  0.75, 2.0),
                (0.845, 82,  3, -1, main, 0.38, 1.3),
                (0.760, 150, 1, +1, acc,  0.45, 1.6),
                (0.660, 64,  4, -1, main, 0.26, 1.1),
                (0.545, 128, 2, +1, main, 0.30, 1.2))):
            rad = r * rr
            p.setPen(QPen(blend(col, a), wid))
            box = QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
            base = (t * rate * (9 + k * 6) * dirn) % 360.0
            for sgm in range(count):
                p.drawArc(box, int((base + sgm * (360.0 / count)) * 16),
                          int(span * 16))

        # 7. The voice, as a ring of graduations that grow with it. Kept out at
        #    a wide radius so it never crowds the middle.
        n = 60
        ring = r * 0.415
        spikes = []
        for i in range(n):
            a = math.radians(i * (360.0 / n))
            ca, sa = math.cos(a), math.sin(a)
            wob = 0.5 + 0.5 * math.sin(t * 2.3 + i * 0.42)
            idle = 0.018 + 0.012 * math.sin(t * 1.2 + i * 0.7)
            h = r * (idle + (amp * 0.20 * wob if live else 0.0))
            spikes.append(QLineF(cx + ca * ring, cy + sa * ring,
                                 cx + ca * (ring + h), cy + sa * (ring + h)))
        p.setPen(QPen(blend(acc if live else main, 0.25 + 0.5 * amp), 1.6))
        p.drawLines(spikes)

        p.setPen(Qt.PenStyle.NoPen)
        for i in range(18):
            angle = math.radians((i * 137.5 + t * (7 + i % 3)) % 360)
            radius = r * (0.48 + 0.43 * ((i * 17) % 19) / 19)
            twinkle = 0.22 + 0.30 * (0.5 + 0.5 * math.sin(t * 2 + i * 1.7))
            p.setBrush(blend(main, twinkle))
            point = QPointF(cx + math.cos(angle) * radius,
                            cy + math.sin(angle) * radius)
            p.drawEllipse(point, 1.0 + (i % 3) * 0.35, 1.0 + (i % 3) * 0.35)

        # 8. The inner ring the name sits in.
        inner = r * 0.355
        p.setPen(QPen(blend(acc, 0.30 + 0.45 * amp), 1.5))
        p.drawEllipse(QRectF(cx - inner, cy - inner, inner * 2, inner * 2))

        for idx, rr in enumerate((0.285, 0.235, 0.175, 0.115, 0.055)):
            rad = r * rr
            p.setPen(QPen(blend(main if idx % 2 == 0 else acc,
                                0.50 if idx < 3 else 0.72),
                             1.0 if idx else 1.6))
            p.drawEllipse(QRectF(cx - rad, cy - rad, rad * 2, rad * 2))
        for idx, (rr, span, direction) in enumerate(((0.30, 54, 1), (0.22, 78, -1),
                                                       (0.145, 102, 1), (0.085, 64, -1))):
            rad = r * rr
            p.setPen(QPen(blend(acc if idx % 2 else main, 0.82), 2.0 if idx == 0 else 1.4))
            p.drawArc(QRectF(cx - rad, cy - rad, rad * 2, rad * 2),
                      int((t * rate * (22 + idx * 7) * direction) % 360 * 16),
                      span * 16)
        inner_ticks = []
        for i in range(48):
            a = math.radians(i * 7.5)
            ca, sa = math.cos(a), math.sin(a)
            start = r * (0.305 if i % 4 == 0 else 0.315)
            end = r * 0.34
            inner_ticks.append(QLineF(cx + ca * start, cy + sa * start,
                                      cx + ca * end, cy + sa * end))
        p.setPen(QPen(blend(main, 0.72), 1.1))
        p.drawLines(inner_ticks)
        core_radius = r * 0.045
        glow = QRadialGradient(cx, cy, r * 0.11)
        glow.setColorAt(0.0, blend(qcol(C.WHITE), 0.95))
        glow.setColorAt(0.25, blend(main, 0.92))
        glow.setColorAt(1.0, blend(main, 0.03))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QRectF(cx - r * 0.11, cy - r * 0.11, r * 0.22, r * 0.22))
        p.setPen(QPen(blend(qcol(C.WHITE), 0.95), 1))
        p.setBrush(blend(main, 0.95))
        p.drawEllipse(QPointF(cx, cy), core_radius, core_radius)

        # 9. The name, sized from the string rather than from the radius alone:
        #    "J.A.R.V.I.S" and a name someone renamed to "MAX" are very
        #    different widths, and a fixed fraction of r spills one of them past
        #    the ring it is supposed to sit inside.
        name = "AI CORE"
        if name:
            space = max(1.0, r * 0.018)
            fsz = max(8, int(min(r * 0.105,
                                 (inner * 1.75) / max(1, len(name)) * 1.6 - space)))
            f = QFont("Courier New", fsz, QFont.Weight.Bold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, space)
            p.setFont(f)
            p.setPen(QPen(blend(qcol(C.WHITE), 0.6 + 0.4 * min(1.0, amp * 2)), 1))
            p.drawText(QRectF(cx - r, cy - fsz, r * 2, fsz * 2),
                       Qt.AlignmentFlag.AlignCenter, name)

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():      # device not ready (e.g. 0-size during layout) — skip cleanly
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.BG))

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        # grid dots — blitted from a cached layer; rebuilt only when the size
        # or the theme's ghost colour changes (so live re-theming still works).
        _gkey = (W, H, C.PRI_GHO)
        if self._grid_cache is None or self._grid_key != _gkey:
            self._grid_cache = self._make_grid(W, H)
            self._grid_key   = _gkey
        p.drawPixmap(0, 0, self._grid_cache)

        # ── holographic head ────────────────────────────────────────────────
        # Sized to the band between the top of the canvas and the status line,
        # capped by width, so it fills the HUD at any window size — including
        # fullscreen — without ever colliding with the status text below.
        _sy_status = cy + fw * 0.40
        if self._avatar is not None and self.hud_style == "face":
            _band_t = 12.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            _r_head = min(fw * 0.355, _band_h / (self._avatar.SPAN + 0.08))
            _head_cy = _band_t + (_band_h - self._avatar.SPAN * _r_head) / 2.0 + _r_head

            if self.muted:
                _main = _acc = qcol(C.MUTED_C)
            else:
                _main = qcol(C.PRI)
                if self.speaking:
                    _acc = qcol(C.ACC)
                elif self.state in ("THINKING", "PROCESSING"):
                    _acc = qcol(C.ACC2)
                elif self.state == "LISTENING":
                    _acc = qcol(C.GREEN)
                else:
                    _acc = qcol(C.PRI)
            self._avatar.paint(p, cx, _head_cy, _r_head, _main, _acc, qcol(C.BG))

        # reactor core — the other centrepiece, and the fallback if the head
        # could not be built. There is no third path: the old face.png branch
        # was unreachable (no such file ships) and the bare orb it fell through
        # to is what this replaces.
        else:
            _band_t = 12.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            _r = min(W * 0.46, _band_h / 2.0)
            self._paint_core(p, cx, _band_t + _band_h / 2.0, _r, W, _band_h)

        # status text
        sy = _sy_status
        if self.muted:
            txt, col = "⊘  MUTED",     qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  SPEAKING",  qcol(C.ACC)
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"
            txt, col = f"{sym}  THINKING",   qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"
            txt, col = f"{sym}  PROCESSING", qcol(C.ACC2)
        elif self.state == "LISTENING":
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  LISTENING",  qcol(C.GREEN)
        else:
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  {self.state}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        # waveform — reacts to the real audio level (mic while listening,
        # JARVIS's own voice while speaking). Falls back to a gentle idle
        # ripple when there's no sound. _amp_disp is the smoothed 0–1 level.
        wy = sy + 30
        N, bw = 36, 8
        wx0 = (W - N * bw) / 2
        amp = self._amp_disp
        mid = (N - 1) / 2.0
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            else:
                env     = (1.0 - abs(i - mid) / mid) ** 0.7      # center-weighted hump
                shimmer = 0.55 + 0.45 * math.sin(self._tick * 0.18 + i * 0.7)
                idle    = 3.0 + 2.0 * math.sin(self._tick * 0.09 + i * 0.6)
                hgt     = int(max(2, min(24, idle + amp * 22.0 * env * shimmer)))
                if amp > 0.05:
                    cl = qcol(C.PRI) if hgt > 12 else qcol(C.PRI_DIM)
                else:
                    cl = qcol(C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)

        p.end()   # end deterministically so the backing store never flushes an active painter

class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        v = max(0.0, min(100.0, pct))
        if v == self._value and text == self._text:
            return          # unchanged — skip the repaint
        self._value = v
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

        p.end()


class _TacticalRadar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._angle = 0
        self.setFixedHeight(116)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(90)

    def _advance(self):
        self._angle = (self._angle + 3) % 360
        if self.isVisible():
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.PANEL))
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(qcol(C.PRI))
        p.drawText(7, 13, "ANIMATED HUD VISUALIZER")
        cx, cy = self.width() / 2, self.height() / 2 + 7
        radius = max(18, min(self.width() * 0.31, self.height() * 0.35))
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        for scale in (1.0, 0.68, 0.36):
            rr = radius * scale
            p.drawEllipse(QRectF(cx - rr, cy - rr, rr * 2, rr * 2))
        p.drawLine(QLineF(cx - radius, cy, cx + radius, cy))
        p.drawLine(QLineF(cx, cy - radius, cx, cy + radius))
        angle = math.radians(self._angle)
        p.setPen(QPen(qcol(C.PRI), 1.5))
        p.drawLine(QLineF(cx, cy, cx + math.cos(angle) * radius,
                          cy + math.sin(angle) * radius))
        p.setBrush(qcol(C.PRI, 22))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPie(QRectF(cx - radius, cy - radius, radius * 2, radius * 2),
                  self._angle * 16, 42 * 16)
        p.setBrush(qcol(C.PRI))
        for dx, dy, phase in ((-0.38, -0.14, 0), (0.22, -0.45, 90),
                              (0.48, 0.26, 170), (-0.16, 0.48, 240)):
            if (self._angle + phase) % 360 < 220:
                p.drawEllipse(QPointF(cx + dx * radius, cy + dy * radius), 2, 2)
        p.end()


class _SignalGraph(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._samples = [0.0] * 80
        self.setFixedHeight(48)

    def set_rate(self, megabytes_per_second: float):
        self._samples.append(max(0.0, min(1.0, megabytes_per_second / 10.0)))
        self._samples = self._samples[-80:]
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.PANEL))
        p.setPen(QPen(qcol(C.BORDER), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        path = QPainterPath()
        left, right = 5, self.width() - 5
        baseline = self.height() - 5
        for i, sample in enumerate(self._samples):
            x = left + (right - left) * i / max(1, len(self._samples) - 1)
            y = baseline - sample * (self.height() - 12)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        p.setPen(QPen(qcol(C.PRI), 1.3))
        p.drawPath(path)
        p.end()


class _NetworkGraph(QWidget):
    node_activated = pyqtSignal(str)

    _NODES = (
        ("JARVIS CORE", 0.50, 0.50),
        ("INTERNET", 0.50, 0.12),
        ("AI API", 0.84, 0.31),
        ("WEBSOCKET", 0.84, 0.69),
        ("BACKEND", 0.50, 0.88),
        ("DEVICES", 0.16, 0.69),
        ("CLOUD", 0.16, 0.31),
    )
    _EDGES = (("JARVIS CORE", "INTERNET"), ("JARVIS CORE", "AI API"),
              ("JARVIS CORE", "WEBSOCKET"), ("JARVIS CORE", "BACKEND"),
              ("JARVIS CORE", "DEVICES"), ("JARVIS CORE", "CLOUD"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self.setFixedHeight(218)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._statuses = {name: "N/A" for name, _, _ in self._NODES}
        self._statuses["JARVIS CORE"] = "ONLINE"
        self._down_mbps: float | None = None
        self._up_mbps: float | None = None
        self._ping_ms: float | None = None
        self._phase = 0.0
        self._zoom = 1.0
        self._pan = QPointF(0, 0)
        self._drag_pos: QPointF | None = None
        self._hovered: str | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(55)

    def set_snapshot(self, statuses: dict[str, str], download_mbps: float | None,
                     upload_mbps: float | None, ping_ms: float | None):
        self._statuses.update(statuses)
        self._down_mbps = download_mbps
        self._up_mbps = upload_mbps
        self._ping_ms = ping_ms
        self.update()

    def _advance(self):
        if not self.isVisible():
            return
        activity = max(self._down_mbps or 0.0, self._up_mbps or 0.0)
        self._phase = (self._phase + 0.012 + min(activity, 25.0) * 0.001) % 1.0
        self.update()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        self._timer.start()
        super().showEvent(event)

    def _positions(self) -> dict[str, QPointF]:
        extent = min(self.width(), self.height() - 20) * 0.74 * self._zoom
        cx = self.width() / 2 + self._pan.x()
        cy = self.height() / 2 + 5 + self._pan.y()
        return {
            name: QPointF(
                cx + (x - 0.5) * extent,
                cy + (y - 0.5) * extent,
            )
            for name, x, y in self._NODES
        }

    @staticmethod
    def _state_color(status: str) -> QColor:
        state = status.upper()
        if state in {"ONLINE", "CONNECTED", "LISTENING"}:
            if state == "LISTENING":
                return qcol(C.ACC)
            return qcol(C.GREEN)
        if state == "UNSTABLE":
            return qcol(C.ACC)
        if state in {"OFFLINE", "DISCONNECTED"}:
            return qcol(C.RED)
        return qcol(C.TEXT_DIM)

    def _node_at(self, point: QPointF) -> str | None:
        for name, pos in self._positions().items():
            radius = (15 if name == "JARVIS CORE" else 10) * self._zoom
            if math.hypot(point.x() - pos.x(), point.y() - pos.y()) <= radius + 5:
                return name
        return None

    def _latency_label(self, node: str) -> str:
        if node == "INTERNET":
            return f"{self._ping_ms:.0f} ms RTT" if self._ping_ms is not None else "RTT N/A"
        return ""

    def _node_detail(self, node: str) -> str:
        lines = [f"{node}  ·  {self._statuses.get(node, 'N/A')}"]
        latency = self._latency_label(node)
        if latency:
            lines.append(latency)
        if node == "INTERNET":
            down = f"{self._down_mbps:.2f} Mbps" if self._down_mbps is not None else "N/A"
            up = f"{self._up_mbps:.2f} Mbps" if self._up_mbps is not None else "N/A"
            lines.extend((f"DOWNLOAD  {down}", f"UPLOAD  {up}"))
            lines.append("Host network counters; traffic is not destination-specific.")
        if node in {"WEBSOCKET", "DEVICES"}:
            lines.append("Remote client connectivity from the live dashboard session")
        if node in {"AI API", "CLOUD"}:
            lines.append("Gemini Live session state")
        return "  |  ".join(lines)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.PANEL))
        p.setPen(QPen(qcol(C.BORDER), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))

        for x in range(12, self.width(), 18):
            p.setPen(QPen(qcol(C.BORDER, 55), 1))
            p.drawLine(x, 1, x, self.height() - 2)
        for y in range(12, self.height(), 18):
            p.drawLine(1, y, self.width() - 2, y)

        positions = self._positions()
        activity = max(self._down_mbps or 0.0, self._up_mbps or 0.0)
        active_traffic = activity > 0.001
        for source, target in self._EDGES:
            start, end = positions[source], positions[target]
            status = self._statuses.get(target, "N/A")
            color = self._state_color(status)
            pulse = min(activity / 8.0, 0.45) * (
                0.5 + 0.5 * math.sin(self._phase * math.tau))
            p.setPen(QPen(qcol(color.name(), int(90 + pulse * 150)), 1.0 + pulse))
            p.drawLine(QLineF(start, end))
            p.setPen(QPen(qcol(C.PRI, 35), 1))
            p.drawEllipse(QRectF(start.x() - 2, start.y() - 2, 4, 4))
            if active_traffic and target == "INTERNET" and status not in {
                "N/A", "OFFLINE", "DISCONNECTED"
            }:
                point = QPointF(
                    start.x() + (end.x() - start.x()) * self._phase,
                    start.y() + (end.y() - start.y()) * self._phase,
                )
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(qcol(C.PRI))
                p.drawEllipse(point, 2.4, 2.4)

        for name, pos in positions.items():
            color = self._state_color(self._statuses.get(name, "N/A"))
            radius = 13 if name == "JARVIS CORE" else 8
            if name == self._hovered:
                radius += 2
            p.setPen(QPen(qcol(color.name(), 185), 1.2))
            p.setBrush(qcol(color.name(), 28))
            p.drawEllipse(QRectF(pos.x() - radius - 4, pos.y() - radius - 4,
                                 2 * radius + 8, 2 * radius + 8))
            p.setPen(QPen(color, 1.4))
            p.setBrush(qcol(C.BG))
            p.drawEllipse(QRectF(pos.x() - radius, pos.y() - radius,
                                 2 * radius, 2 * radius))
            p.setPen(color)
            p.setFont(QFont("Courier New", 6 if name != "JARVIS CORE" else 7,
                            QFont.Weight.Bold))
            label_y = pos.y() + radius + 10
            p.drawText(QRectF(pos.x() - 38, label_y - 5, 76, 11),
                       Qt.AlignmentFlag.AlignCenter, name)
            latency = self._latency_label(name)
            if latency:
                p.setFont(QFont("Courier New", 6))
                p.setPen(qcol(C.TEXT_MED))
                p.drawText(QRectF(pos.x() - 34, label_y + 6, 68, 10),
                           Qt.AlignmentFlag.AlignCenter, latency)

        p.setPen(qcol(C.TEXT_DIM))
        p.setFont(QFont("Courier New", 6))
        p.drawText(7, self.height() - 6, "DRAG TO PAN  ·  WHEEL TO ZOOM  ·  CLICK NODE FOR DETAILS")
        p.end()

    def mouseMoveEvent(self, event):
        point = QPointF(event.position())
        if self._drag_pos is not None:
            delta = point - self._drag_pos
            self._pan += delta
            self._drag_pos = point
            self.update()
            return
        hovered = self._node_at(point)
        if hovered != self._hovered:
            self._hovered = hovered
            self.setCursor(Qt.CursorShape.PointingHandCursor if hovered
                           else Qt.CursorShape.OpenHandCursor)
            self.setToolTip(self._node_detail(hovered) if hovered else "")
            self.update()

    def mousePressEvent(self, event):
        point = QPointF(event.position())
        if event.button() == Qt.MouseButton.LeftButton:
            node = self._node_at(point)
            if node:
                self.node_activated.emit(node)
            else:
                self._drag_pos = point
                self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = None
            self.setCursor(Qt.CursorShape.PointingHandCursor if self._hovered
                           else Qt.CursorShape.OpenHandCursor)

    def wheelEvent(self, event):
        self._zoom = max(0.75, min(1.6, self._zoom + (0.08 if event.angleDelta().y() > 0 else -0.08)))
        self.update()


class _TrafficHistoryGraph(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(82)
        self._download: list[float | None] = []
        self._upload: list[float | None] = []
        self._max_samples = 30

    def add_sample(self, download: float | None, upload: float | None):
        self._download.append(download)
        self._upload.append(upload)
        self._download = self._download[-self._max_samples:]
        self._upload = self._upload[-self._max_samples:]
        self.update()

    def _series_path(self, samples: list[float | None], max_value: float) -> QPainterPath:
        path = QPainterPath()
        started = False
        left, right = 6, self.width() - 6
        top, bottom = 8, self.height() - 15
        for i, value in enumerate(samples):
            if value is None:
                started = False
                continue
            x = right if len(samples) == 1 else left + (right - left) * i / (self._max_samples - 1)
            y = bottom - min(value / max_value, 1.0) * (bottom - top)
            if started:
                path.lineTo(x, y)
            else:
                path.moveTo(x, y)
                started = True
        return path

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.PANEL))
        p.setPen(QPen(qcol(C.BORDER), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        for fraction in (0.25, 0.5, 0.75):
            y = 8 + int((self.height() - 23) * fraction)
            p.setPen(QPen(qcol(C.BORDER, 100), 1, Qt.PenStyle.DotLine))
            p.drawLine(4, y, self.width() - 4, y)
        values = [v for v in (*self._download, *self._upload) if v is not None]
        max_value = max(1.0, max(values, default=0.0) * 1.15)
        p.setPen(QPen(qcol(C.PRI), 1.5))
        p.drawPath(self._series_path(self._download, max_value))
        p.setPen(QPen(qcol(C.ACC2), 1.5))
        p.drawPath(self._series_path(self._upload, max_value))
        p.setPen(qcol(C.TEXT_DIM))
        p.setFont(QFont("Courier New", 6))
        p.drawText(5, self.height() - 3, "LAST 60 SEC  ·  MBPS")
        p.end()


class _LocationMap(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(74)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.PANEL))
        p.setPen(QPen(qcol(C.BORDER), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        for x in range(16, self.width(), 24):
            p.drawLine(x, 1, x, self.height() - 2)
        for y in range(12, self.height(), 18):
            p.drawLine(1, y, self.width() - 2, y)
        p.setPen(QPen(qcol(C.PRI_DIM), 1.2))
        path = QPainterPath()
        path.moveTo(self.width() * 0.12, self.height() * 0.36)
        path.lineTo(self.width() * 0.23, self.height() * 0.21)
        path.lineTo(self.width() * 0.35, self.height() * 0.27)
        path.lineTo(self.width() * 0.31, self.height() * 0.45)
        path.lineTo(self.width() * 0.39, self.height() * 0.56)
        path.lineTo(self.width() * 0.29, self.height() * 0.78)
        path.lineTo(self.width() * 0.20, self.height() * 0.61)
        path.closeSubpath()
        p.fillPath(path, qcol(C.PRI, 28))
        p.drawPath(path)
        path2 = QPainterPath()
        path2.moveTo(self.width() * 0.55, self.height() * 0.25)
        path2.lineTo(self.width() * 0.72, self.height() * 0.19)
        path2.lineTo(self.width() * 0.88, self.height() * 0.34)
        path2.lineTo(self.width() * 0.80, self.height() * 0.49)
        path2.lineTo(self.width() * 0.86, self.height() * 0.69)
        path2.lineTo(self.width() * 0.69, self.height() * 0.81)
        path2.lineTo(self.width() * 0.59, self.height() * 0.60)
        path2.closeSubpath()
        p.fillPath(path2, qcol(C.PRI, 22))
        p.drawPath(path2)
        p.setPen(QPen(qcol(C.PRI, 130), 1))
        p.drawLine(int(self.width() * 0.68), 4, int(self.width() * 0.68), self.height() - 4)
        p.drawLine(int(self.width() * 0.50), int(self.height() * 0.52),
                   int(self.width() * 0.83), int(self.height() * 0.52))
        marker = QPointF(self.width() * 0.68, self.height() * 0.52)
        p.setBrush(qcol(C.PRI, 50))
        p.setPen(QPen(qcol(C.PRI), 1.2))
        p.drawEllipse(marker, 8, 8)
        p.setBrush(qcol(C.WHITE))
        p.drawEllipse(marker, 2, 2)
        p.end()

class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # Cap scrollback so an hours-long session can't grow the document
        # without bound — keeps memory flat and every insert cheap. Oldest
        # lines drop off the top automatically.
        self.document().setMaximumBlockCount(600)
        self.setFont(QFont("Courier New", 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._ai_name_lc = "jarvis"   # updated when assistant name changes
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        _ai_pfx = f"{self._ai_name_lc}:"
        if   tl.startswith("you:"):                              self._tag = "you"
        elif tl.startswith(_ai_pfx) or tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):                             self._tag = "file"
        elif "err" in tl:                                        self._tag = "err"
        else:                                                    self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                # SYS lines are the bulk of the log. Amber fought the cyan HUD
                # and, being a fixed status colour rather than a hue-linked one,
                # stayed amber even after the accent picker retinted everything
                # else. TEXT_MED follows the theme and drops the contrast to a
                # level you can read past.
                "sys":  qcol(C.TEXT_MED),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        # The marching-ants dashed border is only meaningful while the user is
        # hovering or dragging a file over the zone. When idle, skip the repaint
        # entirely instead of redrawing the whole zone 25×/s forever — that idle
        # repaint held the GIL and stole time from the audio/response threads.
        if not (self._hovering or self._drag_over):
            return
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

        p.end()

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Courier New", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Courier New", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class _CameraPreview(QWidget):
    """Floating overlay that briefly shows what the camera captured."""

    _W, _H = 244, 188

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _CameraPreview {{
                background: rgba(0, 6, 10, 242);
                border: 1px solid {C.PRI};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("◈  VISUAL INPUT")
        title.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setFont(QFont("Courier New", 8))
        close_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._img_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.hide()

    def show_frame(self, img_bytes: bytes) -> None:
        px = QPixmap()
        px.loadFromData(img_bytes)
        if not px.isNull():
            max_w = self._W - 12
            scaled = px.scaled(
                max_w, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setFixedSize(scaled.width(), scaled.height())
            self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start(6_000)   # auto-dismiss after 6 s


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont("Courier New", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


class HueWheel(QWidget):
    """
    Circular colour picker. The user drags the handle (small white circle)
    around the wheel to choose from ALL hues. The filled circle in the centre
    is a live preview of the selected colour.
    """

    hue_picked    = pyqtSignal(str)   # while dragging (live)
    hue_committed = pyqtSignal(str)   # when the handle is released

    _RING = 16   # ring thickness (px)

    def __init__(self, initial_hex: str = DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue  = 0.53
        self._drag = False
        self.set_color(initial_hex)

    # ── API ──────────────────────────────────────────────────────────────────
    def color(self) -> str:
        return QColor.fromHsvF(self._hue, 1.0, 1.0).name()

    def set_color(self, hex_str: str):
        c = QColor((hex_str or "").strip())
        if c.isValid() and c.hsvHueF() >= 0:
            self._hue = c.hsvHueF()
            self.update()

    # ── geometry helpers ─────────────────────────────────────────────────────
    def _ring_rect(self) -> QRectF:
        m = self._RING / 2 + 3
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hue_from_pos(self, pos: QPointF) -> float:
        c  = QRectF(self.rect()).center()
        dx = pos.x() - c.x()
        dy = c.y() - pos.y()          # screen y goes down — flip to math axis
        ang = math.atan2(dy, dx)      # [-π, π], counter-clockwise
        return (ang / (2 * math.pi)) % 1.0

    # ── drawing ──────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect   = self._ring_rect()
        center = rect.center()

        grad = QConicalGradient(center, 0)
        for i in range(0, 361, 20):
            grad.setColorAt(i / 360.0, QColor.fromHsvF((i % 360) / 360.0, 1.0, 1.0))
        p.setPen(QPen(QBrush(grad), self._RING))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        # centre preview circle
        preview = QColor.fromHsvF(self._hue, 1.0, 1.0)
        inner   = rect.adjusted(30, 30, -30, -30)
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.setBrush(QBrush(preview))
        p.drawEllipse(inner)

        # draggable handle
        r   = rect.width() / 2
        ang = self._hue * 2 * math.pi
        hx  = center.x() + r * math.cos(ang)
        hy  = center.y() - r * math.sin(ang)
        p.setPen(QPen(QColor("#00060a"), 2))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)
        p.end()

    # ── fare ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        self._drag = True
        self._hue  = self._hue_from_pos(e.position())
        self.update()
        self.hue_picked.emit(self.color())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._hue = self._hue_from_pos(e.position())
            self.update()
            self.hue_picked.emit(self.color())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.hue_committed.emit(self.color())


class CustomizeOverlay(QWidget):
    """Floating overlay — change assistant name, user name, UI colour and voice."""

    saved = pyqtSignal(str, str, str, str)   # assistant_name, user_name, ui_color, voice
    _OW, _OH = 400, 588

    def __init__(self, assistant_name="JARVIS", user_name="",
                 ui_color=DEFAULT_UI_COLOR, voice="", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            CustomizeOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(8)

        def _lbl(txt, fs=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt); w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        _fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
               f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
               f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        lay.addWidget(_lbl("⚙  CUSTOMISE ASSISTANT", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_lbl("ASSISTANT NAME", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._name_input = QLineEdit(assistant_name)
        self._name_input.setFont(QFont("Courier New", 10))
        self._name_input.setFixedHeight(32)
        self._name_input.setStyleSheet(_fs)
        lay.addWidget(self._name_input)

        lay.addSpacing(4)
        lay.addWidget(_lbl("YOUR NAME  (leave blank for default sir / efendim)", 8,
                            color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._user_input = QLineEdit(user_name)
        self._user_input.setPlaceholderText("e.g.  Tony   (leave blank for auto)")
        self._user_input.setFont(QFont("Courier New", 10))
        self._user_input.setFixedHeight(32)
        self._user_input.setStyleSheet(_fs)
        lay.addWidget(self._user_input)

        # ── Assistant voice — Gemini prebuilt voices ─────────────────────────
        # Names are language-neutral proper nouns, so the row reads the same in
        # every locale. Selecting one and applying rebuilds the Live session.
        from memory.config_manager import AVAILABLE_VOICES, DEFAULT_VOICE
        lay.addSpacing(4)
        lay.addWidget(_lbl("ASSISTANT VOICE", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._sel_voice   = (voice or DEFAULT_VOICE)
        if self._sel_voice not in AVAILABLE_VOICES:
            self._sel_voice = DEFAULT_VOICE
        self._voice_btns: dict[str, QPushButton] = {}
        voice_row = QHBoxLayout(); voice_row.setSpacing(4)
        for _v in AVAILABLE_VOICES:
            b = QPushButton(_v)
            b.setCheckable(True)
            b.setFixedHeight(28)
            b.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, name=_v: self._on_voice_pick(name))
            self._voice_btns[_v] = b
            voice_row.addWidget(b)
        lay.addLayout(voice_row)
        self._refresh_voice_btns()

        # ── UI colour — colour wheel ─────────────────────────────────────────
        lay.addSpacing(4)
        clr_hdr = QHBoxLayout()
        clr_hdr.addWidget(_lbl("UI COLOUR  —  drag the handle", 8,
                               color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        clr_hdr.addStretch()
        df_btn = QPushButton("DEFAULT")
        df_btn.setFixedSize(64, 20)
        df_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        df_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        df_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        df_btn.clicked.connect(lambda: self._set_color(DEFAULT_UI_COLOR))
        clr_hdr.addWidget(df_btn)
        lay.addLayout(clr_hdr)

        self._initial_color = (ui_color or DEFAULT_UI_COLOR).strip().lower()
        self._sel_color     = self._initial_color
        self.on_preview     = None   # callable(hex) — live preview; MainWindow wires it

        self._wheel = HueWheel(self._sel_color)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch(); wheel_row.addWidget(self._wheel); wheel_row.addStretch()
        lay.addLayout(wheel_row)
        self._wheel.hue_picked.connect(self._on_wheel_pick)
        self._wheel.hue_committed.connect(self._on_wheel_commit)

        self._hex_input = QLineEdit(self._sel_color)
        self._hex_input.setPlaceholderText("#00d4ff   (custom hex colour)")
        self._hex_input.setFont(QFont("Courier New", 10))
        self._hex_input.setFixedHeight(28)
        self._hex_input.setStyleSheet(_fs)
        self._hex_input.textEdited.connect(self._on_hex_edited)
        lay.addWidget(self._hex_input)

        lay.addSpacing(6)
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        save_btn = QPushButton("▸  APPLY CHANGES")
        save_btn.setFixedHeight(34)
        save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setFont(QFont("Courier New", 9))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    # ── voice selection ──────────────────────────────────────────────────────
    def _on_voice_pick(self, name: str):
        self._sel_voice = name
        self._refresh_voice_btns()

    def _refresh_voice_btns(self):
        """Highlight the selected voice pill; dim the rest."""
        for name, b in self._voice_btns.items():
            on = (name == self._sel_voice)
            b.setChecked(on)
            if on:
                b.setStyleSheet(f"""
                    QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                        border: 1px solid {C.PRI}; border-radius: 3px; }}
                """)
            else:
                b.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
                """)

    # ── colour flow ──────────────────────────────────────────────────────────
    def _set_color(self, hx: str, update_wheel: bool = True, preview: bool = True):
        """Updates the selected colour; hex box + wheel stay in sync, theme is live-previewed."""
        self._sel_color = hx.strip().lower()
        self._hex_input.blockSignals(True)
        self._hex_input.setText(self._sel_color)
        self._hex_input.blockSignals(False)
        if update_wheel:
            self._wheel.set_color(self._sel_color)
        if preview and self.on_preview:
            self.on_preview(self._sel_color)

    def _on_wheel_pick(self, hx: str):
        # While dragging: update the hex box, don't apply the theme yet
        self._sel_color = hx
        self._hex_input.blockSignals(True)
        self._hex_input.setText(hx)
        self._hex_input.blockSignals(False)

    def _on_wheel_commit(self, hx: str):
        # Handle released → live-preview the whole interface
        self._set_color(hx, update_wheel=False)

    def _on_hex_edited(self, text: str):
        t = text.strip().lower()
        if t.startswith("#") and len(t) == 7:
            try:
                int(t[1:], 16)
            except ValueError:
                return
            self._set_color(t, update_wheel=True, preview=True)

    def _cancel(self):
        # If a preview was applied, revert to the colour from launch
        if self.on_preview and self._sel_color != self._initial_color:
            self.on_preview(self._initial_color)
        self.hide()

    def _save(self):
        name = self._name_input.text().strip() or "JARVIS"
        user = self._user_input.text().strip()
        self.saved.emit(name, user, self._sel_color or DEFAULT_UI_COLOR, self._sel_voice)
        self.hide()


class PluginManagerOverlay(QWidget):
    """Floating overlay — lists discovered plugins with per-plugin ON/OFF toggles."""

    _OW = 420

    def __init__(self, plugins: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginManagerOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🧩  PLUGIN MANAGER")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        if not plugins:
            empty = QLabel("No plugins found in /plugins.")
            empty.setFont(QFont("Courier New", 8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(empty)

        for p in plugins:
            lay.addLayout(self._build_row(p))

        lay.addSpacing(4)
        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(30)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        lay.addWidget(close_btn)
        self.adjustSize()

    def _build_row(self, p: dict) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)

        label_text = p["name"] if p["valid"] else f"{p['name']}  (⚠ {p['file']})"
        lbl = QLabel(label_text)
        lbl.setFont(QFont("Courier New", 8))
        lbl.setStyleSheet(f"color: {C.TEXT if p['valid'] else C.TEXT_DIM}; background: transparent;")
        lbl.setToolTip(p["description"] if p["valid"] else p["error"])
        lbl.setWordWrap(False)
        row.addWidget(lbl, stretch=1)

        btn = QPushButton()
        btn.setFixedSize(72, 24)
        btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        if not p["valid"]:
            btn.setText("BROKEN")
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
            """)
        else:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._style_toggle(btn, p["enabled"])
            btn.clicked.connect(lambda _, name=p["name"], b=btn: self._toggle(name, b))
        row.addWidget(btn)
        return row

    def _style_toggle(self, btn: QPushButton, enabled: bool):
        if enabled:
            btn.setText("ON")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            btn.setText("OFF")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
            """)

    def _toggle(self, name: str, btn: QPushButton):
        from memory.config_manager import get_plugin_enabled, save_plugin_enabled
        new_val = not get_plugin_enabled(name)
        save_plugin_enabled(name, new_val)
        self._style_toggle(btn, new_val)


class _HudOverlay(QWidget):
    """Base for the floating panels placed by hand over the HUD.

    They are children of the central widget but sit in no layout, so Qt never
    invalidates the region they occupy when they hide or shrink: the HUD keeps
    painting around them and their last frame stays on screen as a ghost. Any
    overlay positioned with _centre_overlay needs this."""

    def hideEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            # Repaint exactly what we were covering, before we stop covering it.
            p.update(self.geometry())
        super().hideEvent(e)

    def closeEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            p.update(self.geometry())
        super().closeEvent(e)


class CommandPalette(_HudOverlay):
    command_selected = pyqtSignal(str)
    _COMMANDS = (
        ("Open Agent Room", "agents"),
        ("Open Chat", "chat"),
        ("Start Voice", "voice_start"),
        ("Stop Voice", "voice_stop"),
        ("Open Memory", "memory"),
        ("Open Files", "files"),
        ("Open Vision", "vision"),
        ("Open Tasks", "tasks"),
        ("Open Automation", "automation"),
        ("Open Network", "network"),
        ("Open System Monitor", "system"),
        ("Open Settings", "settings"),
        ("Clear Conversation Context", "clear_context"),
    )
    _OW = 430

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"CommandPalette {{ background: rgba(0, 8, 16, 248); "
            f"border: 1px solid {C.BORDER_B}; border-radius: 6px; }}"
        )
        self.setFixedWidth(self._OW)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(7)
        title = QLabel("◈  COMMAND CENTER")
        title.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        layout.addWidget(title)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search commands…")
        self.search.setStyleSheet(
            f"color: {C.TEXT}; background: {C.PANEL2}; "
            f"border: 1px solid {C.BORDER}; padding: 8px;"
        )
        layout.addWidget(self.search)
        self.items = QListWidget()
        self.items.setStyleSheet(
            f"QListWidget {{ color: {C.TEXT}; background: transparent; "
            f"border: 1px solid {C.BORDER}; }}"
            f"QListWidget::item {{ padding: 7px; }}"
            f"QListWidget::item:selected {{ color: {C.PRI}; background: {C.PRI_GHO}; }}"
        )
        self.items.setMaximumHeight(330)
        layout.addWidget(self.items)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._activate_current)
        self.items.itemActivated.connect(self._activate_item)
        self._filter("")

    def _filter(self, text: str):
        self.items.clear()
        for label, command in self._COMMANDS:
            if text.casefold() not in label.casefold():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, command)
            self.items.addItem(item)
        if self.items.count():
            self.items.setCurrentRow(0)

    def _activate_current(self):
        item = self.items.currentItem()
        if item:
            self._activate_item(item)

    def _activate_item(self, item):
        self.command_selected.emit(str(item.data(Qt.ItemDataRole.UserRole)))
        self.hide()


class VisionCenterOverlay(_HudOverlay):
    capture_requested = pyqtSignal(str)
    _OW = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"VisionCenterOverlay {{ background: rgba(0, 8, 16, 248); "
            f"border: 1px solid {C.BORDER_B}; border-radius: 6px; }}"
        )
        self.setFixedWidth(self._OW)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        title = QLabel("◈  VISION CENTER")
        title.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        layout.addWidget(title)
        self.status = QLabel("VISION OFFLINE · Capture starts only when requested.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        layout.addWidget(self.status)
        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText("What should DEVA analyze?")
        self.prompt.setStyleSheet(
            f"color: {C.TEXT}; background: {C.PANEL2}; "
            f"border: 1px solid {C.BORDER}; padding: 7px;"
        )
        layout.addWidget(self.prompt)
        for label, mode in (("SCREEN VIEW  ·  CAPTURE", "screen"),
                            ("CAMERA VIEW  ·  CAPTURE", "camera")):
            button = QPushButton(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton {{ color: {C.PRI}; background: {C.PANEL2}; "
                f"border: 1px solid {C.BORDER_B}; padding: 9px; }}"
                f"QPushButton:hover {{ background: {C.PRI_GHO}; }}"
            )
            button.clicked.connect(lambda _=False, m=mode: self._capture(m))
            layout.addWidget(button)
        close = QPushButton("CLOSE")
        close.clicked.connect(self.hide)
        layout.addWidget(close)

    def _capture(self, mode: str):
        instruction = self.prompt.text().strip() or "Describe the visible content."
        self.capture_requested.emit(
            f"Capture my {mode} once and analyze it: {instruction}"
        )
        self.status.setText("VISION PROCESSING · Explicit capture requested.")

    def set_status(self, status: str):
        self.status.setText(status)


class TaskPlannerOverlay(_HudOverlay):
    task_requested = pyqtSignal(str)
    interrupt_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    _OW = 480

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"TaskPlannerOverlay {{ background: rgba(0, 8, 16, 248); "
            f"border: 1px solid {C.BORDER_B}; border-radius: 6px; }}"
        )
        self.setFixedWidth(self._OW)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        title = QLabel("◈  TASK PLANNER")
        title.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        layout.addWidget(title)
        self.status = QLabel("IDLE · Plans and actions are provided by the live assistant.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        layout.addWidget(self.status)
        plan_label = QLabel("PLAN RESPONSE · returned by the live assistant")
        plan_label.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        plan_label.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        layout.addWidget(plan_label)
        self.plan_view = QTextEdit()
        self.plan_view.setReadOnly(True)
        self.plan_view.setPlaceholderText("A plan appears here only when returned by the live assistant.")
        self.plan_view.setMaximumHeight(130)
        self.plan_view.setStyleSheet(
            f"color: {C.TEXT}; background: {C.PANEL2}; border: 1px solid {C.BORDER}; "
            "font-family: 'Courier New'; font-size: 8pt;"
        )
        layout.addWidget(self.plan_view)
        actions_label = QLabel("ACTUAL BACKEND ACTIONS")
        actions_label.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        actions_label.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        layout.addWidget(actions_label)
        self.actions_view = QTextEdit()
        self.actions_view.setReadOnly(True)
        self.actions_view.setPlaceholderText("No backend actions executed for this task.")
        self.actions_view.setMaximumHeight(90)
        self.actions_view.setStyleSheet(
            f"color: {C.TEXT_MED}; background: {C.PANEL2}; border: 1px solid {C.BORDER}; "
            "font-family: 'Courier New'; font-size: 8pt;"
        )
        layout.addWidget(self.actions_view)
        self._actions: list[str] = []
        self.request = QLineEdit()
        self.request.setPlaceholderText("Describe a multi-step task…")
        self.request.setStyleSheet(
            f"color: {C.TEXT}; background: {C.PANEL2}; "
            f"border: 1px solid {C.BORDER}; padding: 7px;"
        )
        layout.addWidget(self.request)
        row = QHBoxLayout()
        for label, action in (("START", self._start),
                              ("PAUSE", lambda _=False: self.interrupt_requested.emit()),
                              ("RESUME", lambda _=False: self.resume_requested.emit())):
            button = QPushButton(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton {{ color: {C.PRI}; background: {C.PANEL2}; "
                f"border: 1px solid {C.BORDER_B}; padding: 7px; }}"
                f"QPushButton:hover {{ background: {C.PRI_GHO}; }}"
            )
            button.clicked.connect(action)
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        for label, action in (("CANCEL", lambda _=False: self.cancel_requested.emit()),
                              ("RETRY", self._retry)):
            button = QPushButton(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton {{ color: {C.PRI}; background: {C.PANEL2}; "
                f"border: 1px solid {C.BORDER_B}; padding: 7px; }}"
                f"QPushButton:hover {{ background: {C.PRI_GHO}; }}"
            )
            button.clicked.connect(action)
            row.addWidget(button)
        layout.addLayout(row)
        note = QLabel(
            "Pause interrupts assistant generation. Resume continues from context. "
            "Cancel prevents future orchestration steps; an already-running action may "
            "finish. Retry starts the request again. Consequential actions require confirmation."
        )
        note.setWordWrap(True)
        note.setFont(QFont("Courier New", 7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        layout.addWidget(note)
        close = QPushButton("CLOSE")
        close.clicked.connect(self.hide)
        layout.addWidget(close)
        self._last_request = ""

    def _start(self):
        task = self.request.text().strip()
        if not task:
            self.status.setText("Enter a task before starting.")
            return
        self._last_request = task
        self.task_requested.emit(
            "Create a concise numbered high-level plan for this task, then "
            "execute only steps supported by your available tools. Ask before "
            "consequential or destructive actions. Task: " + task
        )

    def _retry(self):
        if not self._last_request:
            self.status.setText("No previous task to retry.")
            return
        self.request.setText(self._last_request)
        self._start()

    def set_status(self, state: str, detail: str = ""):
        self.status.setText(f"{state}" + (f" · {detail}" if detail else ""))

    def set_plan(self, text: str):
        from memory.memory_manager import redact_sensitive_text
        self.plan_view.setPlainText(redact_sensitive_text(text)[:2000])

    def record_action(self, name: str, status: str):
        line = f"[{status}] {name}"
        if status == "RUNNING":
            self._actions.append(line)
        else:
            for index in range(len(self._actions) - 1, -1, -1):
                if self._actions[index].startswith("[RUNNING] "):
                    self._actions[index] = line
                    break
            else:
                self._actions.append(line)
        self._actions = self._actions[-8:]
        self.actions_view.setPlainText("\n".join(self._actions))

    def set_actions(self, actions: list[str]):
        self._actions = list(actions[-8:])
        self.actions_view.setPlainText("\n".join(self._actions))


class _AgentRoomGraph(QWidget):
    agent_selected = pyqtSignal(object)
    _ORDER = ("coding", "research", "vision", "voice", "file",
              "automation", "memory", "system", "security", "study")
    _SHORT = {
        "coding": "CODING", "research": "RESEARCH", "file": "FILE",
        "vision": "VISION", "voice": "VOICE", "automation": "AUTO",
        "memory": "MEMORY", "system": "SYSTEM", "security": "SECURITY",
        "study": "STUDY",
    }
    _ROOM_POSITIONS = (
        (-0.67, -0.24), (0.0, -0.24), (0.67, -0.24),
        (-0.67, 0.12), (0.0, 0.12), (0.67, 0.12),
        (-0.67, 0.48), (0.0, 0.48), (0.67, 0.48),
        (0.0, 0.83),
    )
    _AGENT_COLORS = {
        "coding": QColor("#39d8ff"),
        "research": QColor("#6b9dff"),
        "vision": QColor("#ad7aff"),
        "voice": QColor("#58eff0"),
        "file": QColor("#70dcff"),
        "automation": QColor("#a287ff"),
        "memory": QColor("#55caff"),
        "system": QColor("#47edc2"),
        "security": QColor("#f279b4"),
        "study": QColor("#9c9aff"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(430)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._agents: dict[str, dict] = {}
        self._phase = 0.0
        self._core_state = "IDLE"
        self._audio_level = 0.0
        self._camera_on = False
        self._user_detected: bool | None = None
        self._gaze_x = 0.5
        self._gaze_y = 0.5
        self._selected_agent = ""
        self._yaw = 0.0
        self._target_yaw = 0.0
        self._zoom = 1.0
        self._target_zoom = 1.0
        self._pan_x = 0.0
        self._target_pan_x = 0.0
        self._pan_y = 0.0
        self._target_pan_y = 0.0
        self._drag_origin: QPointF | None = None
        self._drag_start: QPointF | None = None
        self._hit_boxes: dict[str, QRectF] = {}
        self._agent_headings = {agent_id: 0.0 for agent_id in self._ORDER}
        self._agent_target_headings = dict(self._agent_headings)
        self._last_mesh_face_count = 0
        self._last_agent_heights: dict[str, float] = {}
        self._last_core_height = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(55)

    def set_agents(self, agents: list[dict]):
        self._agents = {agent["id"]: dict(agent) for agent in agents}
        for index, agent_id in enumerate(self._ORDER):
            room_x, depth = self._ROOM_POSITIONS[index]
            if self._camera_on and self._user_detected is True:
                self._agent_target_headings[agent_id] = (
                    self._gaze_x - 0.5
                ) * 0.8
            elif self._agents.get(agent_id, {}).get("status") in {"ASSIGNED", "WORKING"}:
                self._agent_target_headings[agent_id] = math.atan2(
                    -room_x, -0.28 - depth
                )
            else:
                self._agent_target_headings[agent_id] = 0.0
        self.update()

    def set_runtime(self, state: str, audio_level: float):
        self._core_state = str(state or "IDLE").upper()
        self._audio_level = max(0.0, min(1.0, float(audio_level or 0.0)))
        self.update()

    def set_camera_presence(self, enabled: bool, detected: bool | None,
                            gaze_x: float = 0.5, gaze_y: float = 0.5):
        self._camera_on = enabled
        self._user_detected = detected if enabled else None
        self._gaze_x = max(0.0, min(1.0, gaze_x)) if enabled else 0.5
        self._gaze_y = max(0.0, min(1.0, gaze_y)) if enabled else 0.5
        for index, agent_id in enumerate(self._ORDER):
            room_x, depth = self._ROOM_POSITIONS[index]
            if self._camera_on and self._user_detected is True:
                self._agent_target_headings[agent_id] = (
                    self._gaze_x - 0.5
                ) * 0.8
            elif self._agents.get(agent_id, {}).get("status") in {"ASSIGNED", "WORKING"}:
                self._agent_target_headings[agent_id] = math.atan2(
                    -room_x, -0.28 - depth
                )
            else:
                self._agent_target_headings[agent_id] = 0.0
        self.update()

    def _advance(self):
        if self.isVisible():
            self._phase = (self._phase + 0.013) % 1.0
            self._yaw += (self._target_yaw - self._yaw) * 0.14
            self._zoom += (self._target_zoom - self._zoom) * 0.14
            self._pan_x += (self._target_pan_x - self._pan_x) * 0.14
            self._pan_y += (self._target_pan_y - self._pan_y) * 0.14
            for agent_id in self._ORDER:
                current = self._agent_headings[agent_id]
                target = self._agent_target_headings[agent_id]
                delta = (target - current + math.pi) % math.tau - math.pi
                self._agent_headings[agent_id] = current + delta * 0.13
            self.update()

    def focus_agent(self, agent_id: str):
        if agent_id not in self._agents:
            return
        self._selected_agent = agent_id
        index = self._ORDER.index(agent_id)
        room_x, depth = self._ROOM_POSITIONS[index]
        self._target_yaw = max(-0.8, min(0.8, room_x * 0.44))
        self._target_zoom = 1.35
        yaw = self._target_yaw
        projected_x = room_x * math.cos(yaw) - depth * math.sin(yaw) * 0.26
        projected_depth = depth * math.cos(yaw) + room_x * math.sin(yaw) * 0.26
        perspective = 0.72 + projected_depth * 0.32
        self._target_pan_x = max(
            -0.4, min(0.4, -projected_x * 0.55 * perspective * self._target_zoom)
        )
        self._target_pan_y = max(
            -0.4, min(0.4,
                      0.64 - (0.55 + projected_depth * 0.42 * self._target_zoom))
        )
        self._agent_target_headings[agent_id] = (
            (self._gaze_x - 0.5) * 0.8
            if self._camera_on and self._user_detected is True
            else math.atan2(-room_x, -0.28 - depth)
        )
        self.agent_selected.emit(dict(self._agents[agent_id]))
        self.update()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        self._timer.start()
        super().showEvent(event)

    @staticmethod
    def _color(status: str) -> QColor:
        return {
            "READY": QColor(C.GREEN),
            "IDLE": QColor(C.GREEN),
            "ASSIGNED": QColor(C.PRI),
            "WORKING": QColor(C.PRI),
            "WAITING": QColor(C.TEXT_DIM),
            "COMPLETED": QColor(C.GREEN),
            "FAILED": QColor(C.RED),
            "ERROR": QColor(C.RED),
            "LISTENING": QColor(C.PRI_DIM),
            "THINKING": QColor(C.ACC2),
            "ANALYZING": QColor(C.ACC2),
            "EXECUTING": QColor(C.ACC),
            "SPEAKING": QColor(C.PRI),
        }.get(status, QColor(C.TEXT_DIM))

    def _visual_state(self, agent: dict) -> str:
        status = str(agent.get("status", "WAITING")).upper()
        if status == "READY":
            status = "IDLE"
        if status == "FAILED":
            status = "ERROR"
        if status == "ASSIGNED":
            status = "THINKING"
        if (status in {"WORKING", "COMPLETED"} and agent.get("current_task")
                and self._core_state == "SPEAKING"):
            return "SPEAKING"
        if (agent.get("id") == "voice" and status == "IDLE"
                and self._core_state == "LISTENING"):
            return "LISTENING"
        if status in {"WORKING", "COMPLETED"} and self._core_state in {
            "ANALYZING", "THINKING",
        }:
            return "THINKING"
        return status

    @staticmethod
    def _path(points: list[QPointF]) -> QPainterPath:
        path = QPainterPath(points[0])
        for point in points[1:]:
            path.lineTo(point)
        path.closeSubpath()
        return path

    def _agent_point(self, index: int, width: float, height: float) -> QPointF:
        room_x, depth = self._ROOM_POSITIONS[index]
        x = room_x * math.cos(self._yaw) - depth * math.sin(self._yaw) * 0.26
        depth = depth * math.cos(self._yaw) + room_x * math.sin(self._yaw) * 0.26
        perspective = 0.72 + depth * 0.32
        return QPointF(
            width * (0.5 + x * 0.55 * perspective * self._zoom + self._pan_x),
            height * (0.55 + depth * 0.42 * self._zoom + self._pan_y),
        )

    @staticmethod
    def _draw_line(p: QPainter, a: QPointF, b: QPointF, color: QColor,
                   width: float = 1.0):
        p.setPen(QPen(color, width, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawLine(a, b)

    def _draw_room(self, p: QPainter, w: int, h: int):
        p.fillRect(self.rect(), QColor("#02070f"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QRadialGradient(w * 0.5, h * 0.34, w * 0.7))
        glow = QRadialGradient(w * 0.5, h * 0.34, w * 0.7)
        glow.setColorAt(0, QColor(8, 48, 67, 110))
        glow.setColorAt(0.48, QColor(9, 22, 44, 80))
        glow.setColorAt(1, QColor(2, 5, 12, 0))
        p.setBrush(glow)
        p.drawRect(self.rect())

        horizon = h * 0.38
        side = self._path([
            QPointF(0, h * 0.04), QPointF(w * 0.18, h * 0.14),
            QPointF(w * 0.27, horizon), QPointF(0, h * 0.70),
        ])
        p.setBrush(QColor(4, 18, 31, 210))
        p.setPen(QPen(QColor(0, 123, 151, 75), 1))
        p.drawPath(side)
        side_r = self._path([
            QPointF(w, h * 0.04), QPointF(w * 0.82, h * 0.14),
            QPointF(w * 0.73, horizon), QPointF(w, h * 0.70),
        ])
        p.drawPath(side_r)

        ceiling = self._path([
            QPointF(w * 0.18, h * 0.14), QPointF(w * 0.82, h * 0.14),
            QPointF(w * 0.73, horizon), QPointF(w * 0.27, horizon),
        ])
        p.setBrush(QColor(7, 24, 39, 180))
        p.setPen(QPen(QColor(0, 217, 255, 90), 1.1))
        p.drawPath(ceiling)
        for fraction in (0.22, 0.38, 0.62, 0.78):
            self._draw_line(p, QPointF(w * fraction, h * 0.14),
                            QPointF(w * (0.5 + (fraction - 0.5) * 0.52), horizon),
                            QColor(0, 175, 209, 44))
        for fraction in (0.28, 0.5, 0.72):
            self._draw_line(p, QPointF(w * fraction, h * 0.145),
                            QPointF(w * fraction, h * 0.03),
                            QColor(0, 217, 255, 75), 1.4)
            p.setPen(Qt.PenStyle.NoPen)
            light = QRadialGradient(w * fraction, h * 0.15, 42)
            light.setColorAt(0, QColor(0, 217, 255, 45))
            light.setColorAt(1, QColor(0, 217, 255, 0))
            p.setBrush(light)
            p.drawEllipse(QPointF(w * fraction, h * 0.15), 40, 20)

        floor = self._path([
            QPointF(0, h), QPointF(w, h), QPointF(w * 0.73, horizon),
            QPointF(w * 0.27, horizon),
        ])
        p.setBrush(QColor(4, 15, 25, 250))
        p.setPen(QPen(QColor(0, 217, 255, 100), 1))
        p.drawPath(floor)
        vanishing = QPointF(w * (0.5 + self._pan_x), horizon)
        for i in range(-12, 13):
            bottom = QPointF(w * 0.5 + i * w * 0.052, h)
            self._draw_line(p, vanishing, bottom, QColor(0, 170, 207, 48))
        for n in range(1, 10):
            t = n / 10
            y = horizon + (h - horizon) * (t * t)
            self._draw_line(p, QPointF(w * (0.27 - t * 0.27), y),
                            QPointF(w * (0.73 + t * 0.27), y),
                            QColor(0, 170, 207, 48))
        for x in (0.12, 0.88):
            self._draw_line(p, QPointF(w * x, h * 0.21),
                            QPointF(w * x, h * 0.73), QColor(0, 217, 255, 95), 2)
        for i in range(32):
            x = ((i * 71 + int(self._phase * 700)) % max(w, 1))
            y = h * (0.18 + ((i * 37) % 63) / 100)
            alpha = 50 + int(80 * abs(math.sin(self._phase * 6 + i)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(48, 210, 255, alpha))
            p.drawEllipse(QPointF(x, y), 1.1, 1.1)

    def _draw_hologram(self, p: QPainter, agent_id: str, center: QPointF,
                       scale: float, color: QColor, state: str):
        x, y = center.x(), center.y()
        panel = {
            "coding": ("CODE / DIAGNOSTICS", ("def run_task():", "  inspect()", "  return result")),
            "research": ("SOURCES / KNOWLEDGE", ("SOURCE 01  VERIFIED", "CONTEXT  ↗", "GRAPH  ·  18 NODES")),
            "file": ("DOCUMENT / INDEX", ("FILES  08", "PARSE  ▰▰▰▱", "INDEX  READY")),
            "vision": ("VISION / FRAME", ("FRAME  01", "OBJECT TRACK", "CONFIDENCE  98%")),
            "voice": ("AUDIO / TRANSCRIPT", ("INPUT  ▂▅▃▆", "SPEECH STREAM", "OUTPUT  READY")),
            "automation": ("EXECUTION GRAPH", ("TRIGGER  →  ACTION", "NODE  04 / 08", "PIPELINE LIVE")),
            "memory": ("CONTEXT / MEMORY", ("RECALL  12 NODES", "LINK  ACTIVE", "TIMELINE  ↗")),
            "system": ("SYSTEM TELEMETRY", ("CPU  ▰▰▱▱", "RAM  ▰▰▰▱", "NETWORK  LIVE")),
            "security": ("SECURITY / AUDIT", ("SURFACE SCAN", "POLICY CHECK", "STATUS  GUARDED")),
            "study": ("LEARNING / NOTES", ("DOCUMENT MAP", "TOPIC  06", "RECALL  READY")),
        }.get(agent_id, ("AGENT WORKSPACE", ("TASK STREAM", "DATA LIVE", "READY")))
        panel_w, panel_h = 150 * scale, 66 * scale
        rect = QRectF(x - panel_w / 2, y - 112 * scale, panel_w, panel_h)
        p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 145), 1))
        p.setBrush(QColor(3, 16, 28, 205))
        p.drawRoundedRect(rect, 3, 3)
        p.setPen(QColor(color.red(), color.green(), color.blue(), 210))
        p.setFont(QFont("Courier New", max(5, int(6 * scale)), QFont.Weight.Bold))
        p.drawText(QRectF(rect.x() + 7, rect.y() + 4, rect.width() - 12, 12),
                   Qt.AlignmentFlag.AlignLeft, panel[0])
        p.setPen(QColor(164, 219, 235, 195))
        p.setFont(QFont("Courier New", max(5, int(5 * scale))))
        for line, text in enumerate(panel[1]):
            p.drawText(QRectF(rect.x() + 7, rect.y() + 20 + line * 12,
                              rect.width() - 10, 10),
                       Qt.AlignmentFlag.AlignLeft, text)
        self._draw_line(p, QPointF(x, rect.bottom()),
                        QPointF(x, y - 42 * scale),
                        QColor(color.red(), color.green(), color.blue(), 115))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color.red(), color.green(), color.blue(), 105))
        for line in range(5):
            px = rect.right() - (line + 1) * 7 * scale
            p.drawEllipse(QPointF(px, rect.bottom() - 6 * scale),
                          1.4 * scale, 1.4 * scale)
        if state == "WORKING":
            for tick in range(3):
                yline = rect.bottom() - 15 * scale + tick * 4 * scale
                self._draw_line(p, QPointF(rect.left() + 7 * scale, yline),
                                QPointF(rect.left() + 25 * scale, yline),
                                QColor(color.red(), color.green(), color.blue(),
                                       55 + int(100 * abs(math.sin(self._phase * 6 + tick)))))

    @staticmethod
    def _ellipsoid_faces(center, radii, segments: int = 7, rings: int = 4):
        cx, cy, cz = center
        rx, ry, rz = radii
        points = []
        for ring in range(rings + 1):
            lat = -math.pi / 2 + math.pi * ring / rings
            points.append([
                (cx + rx * math.cos(lat) * math.cos(math.tau * col / segments),
                 cy + ry * math.sin(lat),
                 cz + rz * math.cos(lat) * math.sin(math.tau * col / segments))
                for col in range(segments)
            ])
        faces = []
        for ring in range(rings):
            for col in range(segments):
                nxt = (col + 1) % segments
                quad = (points[ring][col], points[ring][nxt],
                        points[ring + 1][nxt], points[ring + 1][col])
                if ring == 0:
                    faces.append(quad[1:])
                elif ring == rings - 1:
                    faces.append(quad[:3])
                else:
                    faces.append(quad)
        return faces

    @staticmethod
    def _cylinder_faces(start, end, radius: float, segments: int = 7):
        delta = tuple(end[i] - start[i] for i in range(3))
        length = math.sqrt(sum(value * value for value in delta)) or 1.0
        axis = tuple(value / length for value in delta)
        reference = (0.0, 0.0, 1.0) if abs(axis[2]) < 0.9 else (0.0, 1.0, 0.0)
        u = (axis[1] * reference[2] - axis[2] * reference[1],
             axis[2] * reference[0] - axis[0] * reference[2],
             axis[0] * reference[1] - axis[1] * reference[0])
        norm = math.sqrt(sum(value * value for value in u)) or 1.0
        u = tuple(value / norm for value in u)
        v = (axis[1] * u[2] - axis[2] * u[1],
             axis[2] * u[0] - axis[0] * u[2],
             axis[0] * u[1] - axis[1] * u[0])
        circles = []
        for origin in (start, end):
            circles.append([
                tuple(origin[i] + radius * (
                    u[i] * math.cos(math.tau * col / segments)
                    + v[i] * math.sin(math.tau * col / segments)
                ) for i in range(3))
                for col in range(segments)
            ])
        faces = [
            (circles[0][col], circles[0][(col + 1) % segments],
             circles[1][(col + 1) % segments], circles[1][col])
            for col in range(segments)
        ]
        faces.extend((tuple(circles[0]), tuple(reversed(circles[1]))))
        return faces

    def _draw_robot_mesh(self, p: QPainter, center: QPointF, height: float,
                         heading: float, accent: QColor, state: str,
                         index: int, role: str):
        active = state in {"WORKING", "THINKING", "LISTENING", "SPEAKING"}
        phase = self._phase * math.tau
        bob = math.sin(phase + index * 0.82) * 0.014
        thinking_tilt = math.sin(phase * 0.42 + index) * (
            0.035 if state == "THINKING" else 0.015
        )
        hand_lift = 0.13 + (0.075 * math.sin(phase * 2 + index)
                            if state == "WORKING" else 0)
        eye_glow = 235 if active else 170 + int(45 * abs(math.sin(phase * 0.7 + index)))
        faces: list[tuple[tuple, QColor]] = []

        def sphere(pos, radii, color, segments=7, rings=4):
            faces.extend((face, color) for face in self._ellipsoid_faces(
                pos, radii, segments, rings
            ))

        def tube(start, end, radius, color):
            faces.extend((face, color) for face in self._cylinder_faces(
                start, end, radius
            ))

        def y(value):
            return value + bob

        dark = QColor("#081e2b")
        armor = QColor("#123a4d")
        sphere((0, y(0.56), 0), (0.235, 0.26, 0.15), armor)
        sphere((0, y(0.59), 0.125), (0.15, 0.18, 0.05), dark)
        sphere((0, y(0.845) + thinking_tilt, 0), (0.18, 0.16, 0.15),
               armor, 9, 5)
        sphere((0, y(0.846) + thinking_tilt, 0.115), (0.13, 0.105, 0.055), dark)
        tube((0, y(0.70), 0), (0, y(0.76), 0), 0.07, accent)
        gaze_x = ((self._gaze_x - 0.5) * 0.07
                  if self._camera_on and self._user_detected is True else 0.0)
        for side in (-1, 1):
            eye_x = side * 0.064 + gaze_x
            sphere((eye_x, y(0.86) + thinking_tilt, 0.164),
                   (0.027, 0.021, 0.019), accent, 6, 3)
            sphere((eye_x, y(0.86) + thinking_tilt, 0.179),
                   (0.011, 0.010, 0.008), QColor(185, 255, 255, eye_glow), 6, 3)
            sphere((side * 0.153, y(0.85) + thinking_tilt, 0),
                   (0.042, 0.065, 0.065), accent, 6, 3)
        if state == "SPEAKING":
            mouth_h = 0.014 + 0.012 * abs(math.sin(phase * 4))
            sphere((0, y(0.79) + thinking_tilt, 0.169),
                   (0.035, mouth_h, 0.009), accent, 6, 3)

        for side in (-1, 1):
            shoulder = (side * 0.205, y(0.69), 0)
            elbow = (side * 0.285, y(0.56) + hand_lift * 0.38, 0.01)
            wrist = (side * (0.25 if state == "WORKING" else 0.205),
                     y(0.48) + hand_lift, 0.10 if state == "WORKING" else 0.02)
            sphere(shoulder, (0.085, 0.085, 0.095), accent)
            tube(shoulder, elbow, 0.06, armor)
            sphere(elbow, (0.06, 0.062, 0.06), accent, 6, 3)
            tube(elbow, wrist, 0.046, armor)
            sphere(wrist, (0.05, 0.042, 0.05), accent, 6, 3)
            hip = (side * 0.105, y(0.36), 0)
            knee = (side * 0.12, y(0.19), 0.015)
            ankle = (side * 0.125, y(0.045), 0.02)
            sphere(hip, (0.078, 0.075, 0.085), accent, 6, 3)
            tube(hip, knee, 0.063, armor)
            sphere(knee, (0.064, 0.055, 0.065), accent, 6, 3)
            tube(knee, ankle, 0.05, armor)
            sphere((side * 0.125, y(0.025), 0.065),
                   (0.09, 0.035, 0.12), dark)
            sphere((side * 0.205, y(0.68), -0.01),
                   (0.088, 0.075, 0.085), dark)
        sphere((0, y(0.48), 0.18), (0.046, 0.058, 0.025), accent)
        sphere((0, y(0.98) + thinking_tilt, 0),
               (0.022, 0.075, 0.022), accent, 6, 3)
        sphere((0, y(0.99) + thinking_tilt, 0.012),
               (0.035, 0.027, 0.035), accent, 6, 3)
        if role == "coding":
            sphere((0.23, y(0.72), -0.07), (0.10, 0.07, 0.035), accent)
        elif role == "research":
            for node in range(3):
                angle = phase * 0.25 + math.tau * node / 3
                sphere((math.cos(angle) * 0.24, y(0.88) + math.sin(angle) * 0.09,
                        -0.03), (0.025, 0.025, 0.025), accent, 6, 3)
        elif role == "vision":
            sphere((0, y(0.91) + thinking_tilt, 0.17),
                   (0.06, 0.06, 0.04), accent)
        elif role == "voice":
            sphere((0.22, y(0.80), 0.02), (0.035, 0.08, 0.035), accent)
        elif role == "file":
            sphere((0, y(0.58), -0.15), (0.18, 0.17, 0.07), accent)
        elif role == "automation":
            for side in (-1, 1):
                sphere((side * 0.29, y(0.73), 0), (0.10, 0.045, 0.065), accent)
        elif role == "memory":
            for node in range(3):
                angle = phase * 0.2 + math.tau * node / 3
                sphere((math.cos(angle) * 0.28, y(0.58) + math.sin(angle) * 0.10,
                        0.02), (0.024, 0.024, 0.024), accent, 6, 3)
        elif role == "system":
            for meter in range(3):
                sphere((0.17, y(0.62 - meter * 0.07), 0.13),
                       (0.035, 0.012, 0.012), accent, 6, 3)
        elif role == "security":
            sphere((0, y(0.58), 0.19), (0.12, 0.12, 0.035), accent)
        elif role == "study":
            sphere((-0.24, y(0.61), -0.04), (0.07, 0.11, 0.045), accent)

        cosine, sine = math.cos(heading), math.sin(heading)

        def project(point):
            lx, ly, lz = point
            rx = lx * cosine + lz * sine
            rz = -lx * sine + lz * cosine
            factor = 470 / max(390, 470 - rz * height)
            return QPointF(center.x() + rx * height * factor,
                           center.y() - ly * height * factor), rz * height

        rendered = []
        for vertices, color in faces:
            projected = [project(vertex) for vertex in vertices]
            points = [item[0] for item in projected]
            depth = sum(item[1] for item in projected) / len(projected)
            a, b, c = vertices[:3]
            ab = tuple(b[i] - a[i] for i in range(3))
            ac = tuple(c[i] - a[i] for i in range(3))
            normal = (ab[1] * ac[2] - ab[2] * ac[1],
                      ab[2] * ac[0] - ab[0] * ac[2],
                      ab[0] * ac[1] - ab[1] * ac[0])
            norm = math.sqrt(sum(value * value for value in normal)) or 1.0
            lighting = abs((normal[0] * -0.22 + normal[1] * 0.56
                            + normal[2] * 0.80) / norm)
            rendered.append((depth, points, color, lighting))
        rendered.sort(key=lambda item: item[0])
        self._last_mesh_face_count += len(rendered)
        for _depth, points, color, lighting in rendered:
            shaded = color.lighter(int(76 + lighting * 78))
            p.setPen(QPen(shaded, max(0.45, height * 0.006),
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            p.setBrush(shaded)
            p.drawPolygon(QPolygonF(points))

        head, _depth = project((0, y(0.845) + thinking_tilt, 0.168))
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(),
                             165 if active else 85), max(1.0, height * 0.012)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(head, height * 0.255, height * 0.19)
        if state == "SPEAKING":
            for ring in range(2):
                radius = height * (0.28 + ((self._phase * 1.5 + ring / 2) % 1) * 0.14)
                p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(),
                                      max(10, 85 - ring * 35)),
                              max(1, height * 0.009)))
                p.drawEllipse(head, radius, radius * 0.34)

    def _draw_humanoid(self, p: QPainter, agent_id: str, center: QPointF,
                       height: float, state: str, selected: bool, index: int):
        x, floor_y = center.x(), center.y()
        height = max(57.0, min(height, 91.0))
        accent = (
            QColor(C.GREEN) if state == "COMPLETED" else
            QColor(C.RED) if state in {"ERROR", "FAILED"} else
            self._AGENT_COLORS[agent_id]
        )
        active = state in {"WORKING", "THINKING", "LISTENING", "SPEAKING"}
        shadow = QRadialGradient(x, floor_y, height * 0.42)
        shadow.setColorAt(0, QColor(accent.red(), accent.green(), accent.blue(), 70))
        shadow.setColorAt(1, QColor(accent.red(), accent.green(), accent.blue(), 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(shadow)
        p.drawEllipse(QPointF(x, floor_y), height * 0.42, height * 0.10)
        self._draw_robot_mesh(
            p, center, height, self._agent_headings[agent_id],
            accent if active or selected else accent.darker(112), state, index,
            agent_id,
        )

        p.setPen(accent if active or selected else QColor(C.TEXT_MED))
        p.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
        progress = self._agents.get(agent_id, {}).get("progress")
        p.drawText(QRectF(x - 64, floor_y + 2, 128, 10),
                   Qt.AlignmentFlag.AlignCenter,
                   f"{self._agents.get(agent_id, {}).get('name', agent_id).upper()}  ·  {state}")
        if progress is not None and active:
            p.setPen(accent)
            p.drawText(QRectF(x + height * 0.29, floor_y - height * 0.72,
                              34, 10), Qt.AlignmentFlag.AlignLeft,
                       f"{progress}%")
        if selected:
            p.setPen(QPen(QColor(142, 246, 255, 170), 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(x - height * 0.42, floor_y - height * 1.1,
                                 height * 0.84, height * 1.16))
        self._hit_boxes[agent_id] = QRectF(
            x - height * 0.45, floor_y - height * 1.13,
            height * 0.9, height * 1.5,
        )

    def _draw_humanoid_flat(self, p: QPainter, agent_id: str, center: QPointF,
                       height: float, state: str, selected: bool, index: int):
        x, floor_y = center.x(), center.y()
        height = max(54.0, min(height, 190.0))
        scale = height / 150
        bob = math.sin(self._phase * math.tau + index * 0.82) * 2.1 * scale
        floor_y += bob
        color = self._color(state)
        active = state in {"WORKING", "THINKING", "LISTENING", "SPEAKING"}
        alpha = 115 if active else 62

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QRadialGradient(x, floor_y, height * 0.33))
        shadow = QRadialGradient(x, floor_y, height * 0.33)
        shadow.setColorAt(0, QColor(color.red(), color.green(), color.blue(), 65))
        shadow.setColorAt(1, QColor(color.red(), color.green(), color.blue(), 0))
        p.setBrush(shadow)
        p.drawEllipse(QPointF(x, floor_y), height * 0.34, height * 0.11)

        head_y = floor_y - height * 0.82
        head_w, head_h = height * 0.22, height * 0.25
        glow = QRadialGradient(x, head_y, head_w * 1.8)
        glow.setColorAt(0, QColor(color.red(), color.green(), color.blue(), alpha))
        glow.setColorAt(1, QColor(color.red(), color.green(), color.blue(), 0))
        p.setBrush(glow)
        p.drawEllipse(QPointF(x, head_y), head_w * 1.8, head_h * 1.6)

        # Segmented translucent armor silhouette, rendered in projected space.
        torso_top = floor_y - height * 0.67
        torso_bottom = floor_y - height * 0.35
        shoulder = height * 0.27
        p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 205),
                      max(1, scale * 1.15)))
        p.setBrush(QColor(10, 49, 67, 212))
        p.drawEllipse(QRectF(x - head_w * 0.72, head_y - head_h * 0.72,
                             head_w * 1.44, head_h * 1.44))
        p.setPen(QPen(QColor(106, 220, 242, 145), max(1, scale * 0.65)))
        p.setBrush(QColor(5, 22, 35, 240))
        p.drawEllipse(QRectF(x - head_w * 0.55, head_y - head_h * 0.55,
                             head_w * 1.1, head_h * 1.1))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color.red(), color.green(), color.blue(), 220))
        gaze = (self._gaze_x - 0.5) * 2.4 if self._camera_on and self._user_detected else 0
        for eye_side in (-1, 1):
            eye_x = x + eye_side * head_w * 0.27 + gaze * scale
            p.setBrush(QColor(color.red(), color.green(), color.blue(), 75))
            p.drawEllipse(QPointF(eye_x, head_y - head_h * 0.05),
                          5.5 * scale, 4.0 * scale)
            p.setBrush(QColor(183, 252, 255, 245 if active else 200))
            p.drawEllipse(QPointF(eye_x, head_y - head_h * 0.05),
                          2.2 * scale, 1.5 * scale)
        self._draw_line(p, QPointF(x, head_y + head_h * 0.23),
                        QPointF(x, head_y + head_h * 0.34),
                        QColor(color.red(), color.green(), color.blue(), 190), scale)
        p.setBrush(QColor(color.red(), color.green(), color.blue(), 75))
        p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 180), scale))
        torso = self._path([
            QPointF(x - shoulder * 0.58, torso_top),
            QPointF(x + shoulder * 0.58, torso_top),
            QPointF(x + shoulder * 0.43, torso_bottom),
            QPointF(x - shoulder * 0.43, torso_bottom),
        ])
        p.drawPath(torso)
        p.setBrush(QColor(9, 40, 57, 220))
        chest = self._path([
            QPointF(x - shoulder * 0.30, torso_top + 7 * scale),
            QPointF(x + shoulder * 0.30, torso_top + 7 * scale),
            QPointF(x + shoulder * 0.20, torso_bottom - 6 * scale),
            QPointF(x - shoulder * 0.20, torso_bottom - 6 * scale),
        ])
        p.drawPath(chest)
        pulse = 100 + int(100 * abs(math.sin(self._phase * 5 + index)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color.red(), color.green(), color.blue(), pulse))
        p.drawEllipse(QPointF(x, torso_top + height * 0.12), 3.2 * scale, 3.2 * scale)
        for circuit in (-1, 1):
            self._draw_line(
                p, QPointF(x, torso_top + height * 0.06),
                QPointF(x + circuit * shoulder * 0.22, torso_top + height * 0.21),
                QColor(99, 224, 243, 185), max(1, scale * 0.65))
            self._draw_line(
                p, QPointF(x + circuit * shoulder * 0.22, torso_top + height * 0.21),
                QPointF(x + circuit * shoulder * 0.12, torso_bottom - height * 0.04),
                QColor(99, 224, 243, 150), max(1, scale * 0.65))

        arm_y = torso_top + height * 0.035
        hand_y = torso_top + height * (0.22 if active else 0.18)
        for side_arm in (-1, 1):
            shoulder_pt = QPointF(x + side_arm * shoulder * 0.51, arm_y)
            elbow = QPointF(x + side_arm * shoulder * 0.79,
                            hand_y - height * 0.045)
            hand = QPointF(x + side_arm * shoulder * 0.57,
                           hand_y + math.sin(self._phase * 5 + index) * scale
                           if state == "WORKING" else hand_y)
            self._draw_line(p, shoulder_pt, elbow,
                            QColor(color.red(), color.green(), color.blue(), 190),
                            max(2, scale * 5))
            self._draw_line(p, elbow, hand,
                            QColor(116, 229, 247, 195), max(2, scale * 4))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color.red(), color.green(), color.blue(), 220))
            p.drawEllipse(hand, 3.2 * scale, 3.2 * scale)
            p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 135), scale))
            p.setBrush(QColor(5, 22, 34, 245))
            p.drawEllipse(shoulder_pt, 5.0 * scale, 5.0 * scale)

        hip_y = torso_bottom
        for leg_side in (-1, 1):
            hip = QPointF(x + leg_side * shoulder * 0.23, hip_y)
            knee = QPointF(x + leg_side * shoulder * 0.28,
                           floor_y - height * 0.18)
            ankle = QPointF(x + leg_side * shoulder * 0.33, floor_y - 3 * scale)
            self._draw_line(p, hip, knee, QColor(72, 170, 196, 210),
                            max(2, scale * 6))
            self._draw_line(p, knee, ankle, QColor(91, 201, 219, 195),
                            max(2, scale * 5))
            p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 160), scale))
            p.setBrush(QColor(8, 34, 48, 250))
            p.drawEllipse(knee, 4.1 * scale, 4.1 * scale)

        if state == "SPEAKING":
            for ring in range(3):
                radius = height * (0.23 + ((self._phase * 1.5 + ring / 3) % 1) * 0.22)
                p.setPen(QPen(QColor(color.red(), color.green(), color.blue(),
                                      max(8, 80 - ring * 18)), max(1, scale)))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(x, head_y), radius, radius * 0.42)

        if selected:
            p.setPen(QPen(QColor(142, 246, 255, 170), 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(x - height * 0.27, floor_y - height * 0.99,
                                 height * 0.54, height * 1.05))

        name = self._agents.get(agent_id, {}).get("name", self._SHORT[agent_id])
        p.setPen(color)
        p.setFont(QFont("Courier New", max(7, int(7 * scale)), QFont.Weight.Bold))
        p.drawText(QRectF(x - 68 * scale, floor_y + 4, 136 * scale, 12 * scale),
                   Qt.AlignmentFlag.AlignCenter, str(name).upper())
        p.setPen(QColor(C.TEXT_MED))
        p.setFont(QFont("Courier New", max(6, int(6 * scale))))
        progress = self._agents.get(agent_id, {}).get("progress")
        progress_text = f"{progress}%" if progress is not None else "LIVE"
        p.drawText(QRectF(x - 52 * scale, floor_y + 16 * scale, 104 * scale, 10 * scale),
                   Qt.AlignmentFlag.AlignCenter, f"{state}  ·  {progress_text}")
        self._hit_boxes[agent_id] = QRectF(
            x - height * 0.36, floor_y - height * 1.05,
            height * 0.72, height * 1.27,
        )

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        self._last_mesh_face_count = 0
        self._last_agent_heights = {}
        self._draw_room(p, w, h)
        cx = w * (0.5 + self._pan_x)
        core = QPointF(cx, h * (0.34 + self._pan_y))
        core_h = min(154, h * 0.32) * self._zoom
        self._last_core_height = core_h
        core_head = QPointF(core.x(), core.y() - core_h * 0.845)

        # The command core is a projected holographic reactor, not a dashboard icon.
        p.setPen(Qt.PenStyle.NoPen)
        reactor = QRadialGradient(core_head, core_h * 0.78)
        reactor.setColorAt(0, QColor(0, 216, 255, 85))
        reactor.setColorAt(0.46, QColor(0, 131, 194, 35))
        reactor.setColorAt(1, QColor(0, 45, 90, 0))
        p.setBrush(reactor)
        p.drawEllipse(core_head, core_h * 0.8, core_h * 0.66)
        for ring in range(3):
            radius = core_h * (0.33 + ring * 0.085)
            p.setPen(QPen(QColor(0, 218, 255, 65 + ring * 15), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(core_head, radius, radius * 0.43)
        self._draw_robot_mesh(
            p, core, core_h, 0.0, QColor(C.PRI), self._core_state,
            len(self._ORDER), "jarvis",
        )
        self._hit_boxes["jarvis"] = QRectF(
            cx - core_h * 0.4, core.y() - core_h * 1.08,
            core_h * 0.8, core_h * 1.14,
        )

        ordered_positions = {
            agent_id: self._agent_point(index, w, h)
            for index, agent_id in enumerate(self._ORDER)
        }
        active_agents = [
            agent_id for agent_id in self._ORDER
            if self._agents.get(agent_id, {}).get("status") in {"ASSIGNED", "WORKING"}
        ]
        for link_index, (source, target) in enumerate(
            zip(active_agents, active_agents[1:])
        ):
            start, end = ordered_positions[source], ordered_positions[target]
            self._draw_line(p, start, end, QColor(129, 91, 255, 82), 1.2)
            packet_t = (self._phase * 1.35 + link_index * 0.23) % 1.0
            packet = QPointF(start.x() + (end.x() - start.x()) * packet_t,
                             start.y() + (end.y() - start.y()) * packet_t)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(156, 116, 255, 210))
            p.drawEllipse(packet, 2.2, 2.2)
        for index, agent_id in enumerate(self._ORDER):
            agent = self._agents.get(agent_id, {})
            state = self._visual_state(agent)
            if agent.get("status") in {"ASSIGNED", "WORKING"}:
                target = ordered_positions[agent_id]
                self._draw_line(
                    p, core_head, target,
                    QColor(0, 197, 255, 62 if agent_id != self._selected_agent else 135))
                packet_t = (self._phase * 1.8 + index * 0.17) % 1.0
                packet = QPointF(core_head.x() + (target.x() - core_head.x()) * packet_t,
                                 core_head.y() + (target.y() - core_head.y()) * packet_t)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(self._color(state))
                p.drawEllipse(packet, 3.2, 3.2)
            if state in {"WORKING", "THINKING"}:
                self._draw_hologram(p, agent_id, ordered_positions[agent_id],
                                    max(0.62, 0.83 - self._ROOM_POSITIONS[index][1] * 0.15),
                                    self._color(state), state)

        if self._core_state == "LISTENING":
            for bar in range(17):
                bar_h = 4 + self._audio_level * 24 * (
                    0.25 + 0.75 * abs(math.sin(self._phase * 9 + bar * 0.57))
                )
                x = cx - 42 + bar * 5.2
                self._draw_line(p, QPointF(x, h - 18),
                                QPointF(x, h - 18 - bar_h), QColor(0, 217, 255, 190), 1.6)

        # Draw distant agents first so foreground bodies naturally overlap them.
        self._hit_boxes.clear()
        for index in sorted(range(len(self._ORDER)),
                            key=lambda item: self._ROOM_POSITIONS[item][1]):
            agent_id = self._ORDER[index]
            state = self._visual_state(self._agents.get(agent_id, {}))
            depth = self._ROOM_POSITIONS[index][1]
            height = (58 + depth * 4) * self._zoom
            self._last_agent_heights[agent_id] = height
            self._draw_humanoid(
                p, agent_id, ordered_positions[agent_id], height, state,
                agent_id == self._selected_agent, index,
            )

        p.setPen(QColor(C.PRI))
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        core_label = QRectF(cx - core_h * 1.2, core.y() - core_h * 0.44,
                            core_h * 1.05, 12)
        p.drawText(core_label, Qt.AlignmentFlag.AlignRight, "JARVIS CORE")
        p.setPen(QColor(C.TEXT_MED))
        p.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
        p.drawText(QRectF(core_label.x(), core_label.y() + 12,
                          core_label.width(), 11),
                   Qt.AlignmentFlag.AlignRight, f"ORCHESTRATOR  ·  {self._core_state}")
        p.setPen(QColor(107, 169, 187, 155))
        p.setFont(QFont("Courier New", 6))
        p.drawText(QRectF(10, 8, w - 20, 12),
                   Qt.AlignmentFlag.AlignLeft,
                   "COMMAND ENVIRONMENT  /  DRAG ORBIT  ·  RIGHT-DRAG PAN  ·  WHEEL ZOOM  ·  CLICK TO FOCUS")
        p.drawText(QRectF(10, h - 18, w - 20, 12),
                   Qt.AlignmentFlag.AlignRight,
                   "USER PRESENCE  ·  " + (
                       "USER DETECTED" if self._user_detected is True else
                       "NO USER DETECTED" if self._camera_on else "CAMERA OFF"
                   ))
        if self._camera_on and self._user_detected is True:
            p.setPen(QPen(QColor(C.GREEN), 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(w * 0.5 - 13, h * 0.08, 26, 26))
        p.end()

    def mousePressEvent(self, event):
        self._drag_origin = event.position()
        self._drag_start = event.position()
        if event.button() == Qt.MouseButton.LeftButton:
            for agent_id, bounds in reversed(list(self._hit_boxes.items())):
                if bounds.contains(event.position()):
                    self.focus_agent(agent_id)
                    break
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_origin is not None and self._drag_start is not None:
            delta = event.position() - self._drag_start
            buttons = event.buttons()
            if buttons & Qt.MouseButton.RightButton or (
                buttons & Qt.MouseButton.LeftButton and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ):
                self._target_pan_x = max(-0.4, min(0.4,
                    self._target_pan_x + delta.x() / max(1, self.width())))
                self._target_pan_y = max(-0.4, min(0.4,
                    self._target_pan_y + delta.y() / max(1, self.height())))
            else:
                self._target_yaw = max(-1.15, min(1.15,
                    self._target_yaw + delta.x() / max(1, self.width()) * 2.1))
            self._drag_start = event.position()
            self.update()
            event.accept()
            return
        self.setCursor(Qt.CursorShape.PointingHandCursor if any(
            bounds.contains(event.position()) for bounds in self._hit_boxes.values()
        ) else Qt.CursorShape.OpenHandCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_origin = None
        self._drag_start = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        event.accept()

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120
        self._target_zoom = max(0.72, min(1.7, self._target_zoom + steps * 0.09))
        self.update()
        event.accept()


class AgentRoomOverlay(_HudOverlay):
    task_center_requested = pyqtSignal()
    camera_requested = pyqtSignal(bool)
    _OW, _OH = 1160, 760

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"AgentRoomOverlay {{ background: rgba(0, 8, 16, 250); "
            f"border: 1px solid {C.BORDER_B}; border-radius: 6px; }}"
        )
        self.setMinimumSize(820, 540)
        self.resize(self._OW, self._OH)
        self._agents: list[dict] = []
        self._events: list[dict] = []
        self._tasks: list[dict] = []
        self._camera_enabled = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        header = QHBoxLayout()
        title = QLabel("◈  JARVIS  /  AI OPERATIONS ROOM")
        title.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        header.addWidget(title)
        header.addStretch()
        note = QLabel("LIVE STATE  ·  DRAG ORBIT  ·  RIGHT-DRAG PAN  ·  WHEEL ZOOM  ·  CLICK TO FOCUS")
        note.setFont(QFont("Courier New", 7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        header.addWidget(note)
        layout.addLayout(header)

        self.graph = _AgentRoomGraph()
        self.graph.agent_selected.connect(self._show_agent_detail)
        layout.addWidget(self.graph, stretch=1)

        # Keep structured roster/task data available for existing callers and tests,
        # but let the room itself—not a widget list—carry the primary visual state.
        self.roster = QListWidget(self)
        self.tasks = QListWidget(self)
        self.roster.hide()
        self.tasks.hide()

        detail_bar = QHBoxLayout()
        self.agent_detail = QLabel(
            "JARVIS CORE  ·  Awaiting a live task dispatch. Select an agent to inspect."
        )
        self.agent_detail.setWordWrap(True)
        self.agent_detail.setMinimumWidth(250)
        self.agent_detail.setStyleSheet(
            f"color: {C.TEXT}; background: transparent; "
            f"border-left: 2px solid {C.PRI}; padding: 4px 9px;"
        )
        self.agent_detail.setFont(QFont("Courier New", 8))
        detail_bar.addWidget(self.agent_detail, stretch=1)

        camera_panel = QFrame()
        camera_panel.setMinimumWidth(245)
        camera_panel.setStyleSheet(
            f"QFrame {{ background: rgba(3, 16, 27, 220); border: 1px solid {C.BORDER}; }}"
        )
        camera_layout = QVBoxLayout(camera_panel)
        camera_layout.setContentsMargins(6, 4, 6, 4)
        camera_layout.setSpacing(3)
        camera_title = QLabel("USER CAMERA  ·  LOCAL PRESENCE ONLY")
        camera_title.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        camera_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        camera_layout.addWidget(camera_title)
        camera_controls = QHBoxLayout()
        self.camera_preview = QLabel("CAMERA OFF")
        self.camera_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_preview.setFixedSize(116, 60)
        self.camera_preview.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: #02060b; border: 1px solid {C.BORDER};"
        )
        camera_controls.addWidget(self.camera_preview)
        status_controls = QVBoxLayout()
        self.camera_status = QLabel("CAMERA OFF · No capture is active.")
        self.camera_status.setWordWrap(True)
        self.camera_status.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        status_controls.addWidget(self.camera_status)
        buttons = QHBoxLayout()
        enable_camera = QPushButton("ENABLE CAMERA")
        disable_camera = QPushButton("DISABLE CAMERA")
        for button in (enable_camera, disable_camera):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton {{ color: {C.PRI}; background: {C.PANEL}; "
                f"border: 1px solid {C.BORDER_B}; padding: 3px 5px; font-size: 8px; }}"
                f"QPushButton:hover {{ background: {C.PRI_GHO}; }}"
            )
            buttons.addWidget(button)
        enable_camera.clicked.connect(lambda: self._request_camera(True))
        disable_camera.clicked.connect(lambda: self._request_camera(False))
        status_controls.addLayout(buttons)
        camera_controls.addLayout(status_controls, stretch=1)
        camera_layout.addLayout(camera_controls)
        detail_bar.addWidget(camera_panel)
        layout.addLayout(detail_bar)

        events_label = QLabel("LIVE EVENT STREAM  /  ACTUAL TOOL + TASK LIFECYCLE")
        events_label.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        events_label.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        layout.addWidget(events_label)
        self.events = QListWidget()
        self.events.setMaximumHeight(76)
        self.events.setMinimumHeight(48)
        self.events.setStyleSheet(
            f"color: {C.TEXT_MED}; background: rgba(3, 13, 22, 230); "
            f"border: 1px solid {C.BORDER}; "
            "font-family: 'Courier New'; font-size: 8pt;"
        )
        layout.addWidget(self.events)

        footer = QHBoxLayout()
        disclosure = QLabel(
            "Agents mirror the existing JARVIS session and enabled backend capabilities."
        )
        disclosure.setWordWrap(True)
        disclosure.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        footer.addWidget(disclosure, stretch=1)
        tasks = QPushButton("TASK CENTER")
        tasks.clicked.connect(self.task_center_requested.emit)
        close = QPushButton("CLOSE")
        close.clicked.connect(self.hide)
        for button in (tasks, close):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton {{ color: {C.PRI}; background: {C.PANEL2}; "
                f"border: 1px solid {C.BORDER_B}; padding: 5px 10px; }}"
                f"QPushButton:hover {{ background: {C.PRI_GHO}; }}"
            )
            footer.addWidget(button)
        layout.addLayout(footer)

        # These hidden list widgets are populated for compatibility with code
        # that consumes the structured roster and task snapshots.
        for heading, attr in (("AGENT STATUS", "roster"),
                              ("ACTIVE / COMPLETED / FAILED TASKS", "tasks")):
            getattr(self, attr).setObjectName(heading.replace(" ", "_"))

    def set_snapshot(self, snapshot: dict):
        self._agents = list(snapshot.get("agents", []))
        self._tasks = list(snapshot.get("tasks", []))
        self.graph.set_agents(self._agents)
        self.roster.clear()
        for agent in self._agents:
            label = f"{agent['name']:<17} {agent['status']:<10} {agent['last_action']}"
            item = QListWidgetItem(label)
            item.setToolTip(
                f"{agent['description']}\nCapabilities: {', '.join(agent['capabilities'])}\n"
                f"Current task: {agent['current_task'] or 'None'}"
            )
            color = _AgentRoomGraph._color(agent["status"])
            item.setForeground(color)
            self.roster.addItem(item)
        self.tasks.clear()
        for task in self._tasks[:8]:
            item = QListWidgetItem(
                f"{task['status']:<10} {task['progress']:>3}% · {task['task'][:42]}"
            )
            item.setToolTip(
                f"Agent: {task['agent']}\nTask: {task['task']}\n"
                f"Duration: {task['duration']:.1f}s"
            )
            self.tasks.addItem(item)
        active_agent = next(
            (agent for agent in self._agents
             if agent.get("status") in {"ASSIGNED", "WORKING", "THINKING"}),
            None,
        )
        if active_agent:
            self._show_agent_detail(active_agent)

    def _show_agent_detail(self, agent: dict):
        progress = agent.get("progress")
        progress_text = f"{progress}%" if progress is not None else "IN PROGRESS"
        task = agent.get("current_task") or agent.get("last_action") or "Standing by"
        self.agent_detail.setText(
            f"{str(agent.get('name', 'AGENT')).upper()}  ·  "
            f"{str(agent.get('status', 'IDLE')).upper()}  ·  {progress_text}\n"
            f"CURRENT TASK  /  {task}"
        )

    def _request_camera(self, enabled: bool):
        self._camera_enabled = enabled
        self.camera_status.setText(
            "CAMERA ON · Starting local presence detection..."
            if enabled else "CAMERA OFF · Stopping capture."
        )
        if not enabled:
            self.camera_preview.setPixmap(QPixmap())
            self.camera_preview.setText("CAMERA OFF")
            self.graph.set_camera_presence(False, None)
        self.camera_requested.emit(enabled)

    def set_user_camera_data(self, frame, status: str,
                             gaze_x: float = 0.5, gaze_y: float = 0.5):
        self.camera_status.setText(status)
        camera_on = status.startswith(("CAMERA ON", "USER DETECTED", "NO USER DETECTED"))
        self._camera_enabled = camera_on
        if frame:
            pixmap = QPixmap()
            pixmap.loadFromData(frame)
            if not pixmap.isNull():
                self.camera_preview.setPixmap(
                    pixmap.scaled(
                        self.camera_preview.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                self.camera_preview.setText("")
        elif not camera_on:
            self.camera_preview.setPixmap(QPixmap())
            self.camera_preview.setText("CAMERA OFF")
        detected = True if status.startswith("CAMERA ON · USER DETECTED") else (
            False if status.startswith("CAMERA ON · NO USER DETECTED") else None
        )
        self.graph.set_camera_presence(camera_on, detected, gaze_x, gaze_y)

    def add_event(self, event: dict):
        self._events.append(dict(event))
        self._events = self._events[-80:]
        self.events.clear()
        for entry in self._events[-30:]:
            stamp = time.strftime("%H:%M:%S", time.localtime(entry.get("ts", time.time())))
            actor = entry.get("agent") or "JARVIS"
            line = f"{stamp} [{actor}] {entry['type']} · {entry.get('message') or entry.get('task', '')}"
            item = QListWidgetItem(line[:180])
            self.events.addItem(item)
        self.events.scrollToBottom()

    def hideEvent(self, event):
        if self._camera_enabled:
            self._camera_enabled = False
            self.camera_requested.emit(False)
        super().hideEvent(event)


class ConfirmBanner(_HudOverlay):
    """The gate in front of an action that cannot be taken back.

    The old confirmation was a tool parameter the model filled in itself, which
    means it confirmed its own shutdown requests. This is the interface asking,
    and the answer travels from a human finger to core/confirm.py without the
    model in the loop. Nothing blocks while it is up: the assistant keeps
    talking, so this costs no latency — unlike the old gate, which spent two
    tool round trips on every power command."""

    answered = pyqtSignal(bool)
    _OW = 430

    def __init__(self, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ConfirmBanner {{
                background: rgba(14, 3, 0, 250);
                border: 1px solid {C.ACC};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        hdr = QLabel("⚠  CONFIRM")
        hdr.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(hdr)

        ttl = QLabel(title)
        ttl.setWordWrap(True)
        ttl.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(ttl)

        if detail:
            dtl = QLabel(detail)
            dtl.setWordWrap(True)
            dtl.setFont(QFont("Courier New", 8))
            dtl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            lay.addWidget(dtl)

        row = QHBoxLayout(); row.setSpacing(8)

        yes = QPushButton("▸  CONFIRM")
        yes.setFixedHeight(32)
        yes.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        yes.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.ACC};
                border: 1px solid {C.ACC}; border-radius: 3px; }}
            QPushButton:hover {{ background: rgba(255,107,0,40); }}
        """)
        yes.clicked.connect(lambda: self.answered.emit(True))
        row.addWidget(yes)

        no = QPushButton("CANCEL")
        no.setFixedHeight(32)
        no.setFont(QFont("Courier New", 9))
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        no.clicked.connect(lambda: self.answered.emit(False))
        row.addWidget(no)
        lay.addLayout(row)

        # Default focus on CANCEL: if someone hits Enter without reading, the
        # safe answer wins.
        no.setDefault(True)
        no.setFocus()


class AudioDeviceOverlay(_HudOverlay):
    """Choose which microphone JARVIS listens to and which speakers it uses.

    Both audio streams used to open with no `device=` at all, so they always
    took the OS default — which on Windows moves by itself the moment a headset
    is plugged in. 'JARVIS can't hear me' is usually 'JARVIS is listening to the
    webcam'."""

    picked = pyqtSignal()      # emitted after Apply, when something changed
    _OW = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        from core.audio_devices import list_devices, DEFAULT_LABEL
        from memory.config_manager import get_input_device, get_output_device

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            AudioDeviceOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🎧  AUDIO DEVICES")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        _combo_css = (
            f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
            f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
            f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; border: 1px solid {C.BORDER}; }}"
        )

        def _row(label: str, kind: str, current: str) -> QComboBox:
            cap = QLabel(label)
            cap.setFont(QFont("Courier New", 8))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(cap)

            box = QComboBox()
            box.setFont(QFont("Courier New", 9))
            box.setFixedHeight(30)
            box.setStyleSheet(_combo_css)
            # The list is served from a cache warmed on a background thread at
            # startup, so opening this panel never blocks the Qt thread on the
            # host audio API.
            box.addItem(DEFAULT_LABEL, "")
            for name in list_devices(kind):
                box.addItem(name, name)
            idx = box.findData(current) if current else 0
            box.setCurrentIndex(idx if idx >= 0 else 0)
            if current and idx < 0:
                # Saved device is not plugged in right now. Show it rather than
                # silently resetting the user's choice to default.
                box.addItem(f"{current}  (not connected)", current)
                box.setCurrentIndex(box.count() - 1)
            lay.addWidget(box)
            return box

        self._in_box  = _row("MICROPHONE — what JARVIS hears you with",
                             "input", get_input_device())
        lay.addSpacing(4)
        self._out_box = _row("SPEAKERS — what JARVIS talks through",
                             "output", get_output_device())

        note = QLabel("Applying reconnects the session. Your conversation is kept.")
        note.setWordWrap(True)
        note.setFont(QFont("Courier New", 7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addSpacing(6)
        lay.addWidget(note)

        row = QHBoxLayout(); row.setSpacing(8)
        ok = QPushButton("▸  APPLY")
        ok.setFixedHeight(32)
        ok.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        ok.clicked.connect(self._apply)
        row.addWidget(ok)

        cancel = QPushButton("CLOSE")
        cancel.setFixedHeight(32)
        cancel.setFont(QFont("Courier New", 9))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel.clicked.connect(self.hide)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _apply(self):
        from memory.config_manager import (
            get_input_device, get_output_device,
            save_input_device, save_output_device,
        )
        new_in  = self._in_box.currentData()  or ""
        new_out = self._out_box.currentData() or ""
        changed = (new_in != get_input_device()) or (new_out != get_output_device())
        save_input_device(new_in)
        save_output_device(new_out)
        self.hide()
        # Only rebuild the session if something actually moved — a no-op Apply
        # should not cost a reconnect.
        if changed:
            self.picked.emit()


class MemoryOverlay(_HudOverlay):
    """Everything JARVIS has stored about you, and when it learned it.

    Memory used to be a 2200-character store that deleted its oldest entries
    when full and mentioned it only on stdout. The cap is gone; this panel is
    the other half of that change — a memory you cannot inspect is a memory you
    cannot trust, and 'delete' has to be something the person can do."""

    _OW = 520

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            MemoryOverlay {{
                background: rgba(0, 6, 10, 246);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(20, 16, 20, 16)
        self._lay.setSpacing(5)
        self._rebuild()

    def _clear_layout(self):
        """Take every item out of the layout and detach it from the widget tree
        in this call.

        deleteLater() on its own is not enough: it queues destruction for the
        next event-loop pass, and until then the old rows are still children of
        this widget and still paint — which is what drew half of the previous
        panel over the new one. setParent(None) removes them from the tree now;
        deleteLater() then frees them safely."""
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # hide() stops it painting in this frame; deleteLater() frees it
                # safely afterwards. setParent(None) would also stop the paint,
                # but it turns the widget into a top-level window for the moment
                # between the two calls, which is not something to leave lying
                # around inside a click handler.
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                while sub.count():
                    si = sub.takeAt(0)
                    sw = si.widget()
                    if sw is not None:
                        sw.hide()
                        sw.deleteLater()
                sub.deleteLater()

    def _settle(self, before):
        """Size the panel to its content, re-centre it, and repaint what the old
        size covered.

        The re-size has to happen here rather than at the end of _rebuild
        because Qt has not polished the freshly-created children at that point,
        so the size hint it would read is the empty-layout one. Measured: a
        first adjustSize() returned 32 px for a panel whose content needed 155,
        and a second call — after the same widgets had been through the event
        loop — returned 155. So this runs twice: once now, once on the next
        turn, from _rebuild.

        The re-centre and the repaint are needed because the overlay is placed
        by hand and is in no layout: shrinking it leaves it off-centre and
        leaves its former pixels on screen, since nothing tells the parent that
        region changed. The repaint has to cover the union of the old and new
        rectangles."""
        self._lay.invalidate()
        self._lay.activate()
        self.updateGeometry()
        self.adjustSize()

        p = self.parentWidget()
        if p is None:
            self.update()
            return
        self.move(max(0, (p.width()  - self.width())  // 2),
                  max(0, (p.height() - self.height()) // 2))
        p.update(before.united(self.geometry()))
        self.update()

    def _rebuild(self):
        before = self.geometry()
        self._clear_layout()

        from memory.memory_manager import all_entries_for_ui

        hdr = QLabel("🧠  WHAT JARVIS REMEMBERS")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        self._lay.addWidget(sep)

        rows = all_entries_for_ui()

        cap = QLabel(f"{len(rows)} stored facts — newest first. "
                     f"Nothing here is sent anywhere; it lives in "
                     f"memory/long_term.json on this machine.")
        cap.setWordWrap(True)
        cap.setFont(QFont("Courier New", 7))
        cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._lay.addWidget(cap)

        controls = QHBoxLayout()
        add = QPushButton("ADD MEMORY")
        add.clicked.connect(self._add_memory)
        controls.addWidget(add)
        clear = QPushButton("CLEAR ALL")
        clear.clicked.connect(self._clear_all)
        controls.addWidget(clear)
        for button in (add, clear):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton {{ color: {C.PRI}; background: {C.PANEL2}; "
                f"border: 1px solid {C.BORDER}; padding: 5px; }}"
                f"QPushButton:hover {{ border-color: {C.BORDER_B}; }}"
            )
        self._lay.addLayout(controls)

        if not rows:
            empty = QLabel("Nothing stored yet.")
            empty.setFont(QFont("Courier New", 9))
            empty.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            self._lay.addWidget(empty)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFixedHeight(min(420, 34 * len(rows) + 10))
            scroll.setStyleSheet(
                f"QScrollArea {{ border: 1px solid {C.BORDER}; border-radius: 3px; "
                f"background: transparent; }}"
            )
            inner = QWidget()
            ilay  = QVBoxLayout(inner)
            ilay.setContentsMargins(6, 6, 6, 6)
            ilay.setSpacing(3)

            for r in rows:
                line = QHBoxLayout(); line.setSpacing(6)
                txt = QLabel(f"<b>{r['key'].replace('_', ' ')}</b> "
                             f"<span style='color:{C.TEXT_MED}'>— {r['value']}</span>")
                txt.setWordWrap(True)
                txt.setFont(QFont("Courier New", 8))
                txt.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
                line.addWidget(txt, 1)

                meta = QLabel(
                    f"{r['category'][:4]} · {r['source'][:6]} · "
                    f"{r['created'] or '—'} / {r['updated'] or '—'}"
                )
                meta.setFont(QFont("Courier New", 7))
                meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
                line.addWidget(meta)

                for label, callback in (
                    ("VIEW", lambda _=False, row=r: self._view_memory(row)),
                    ("EDIT", lambda _=False, row=r: self._edit_memory(row)),
                    ("ARCHIVE", lambda _=False, c=r["category"], k=r["key"]: self._archive(c, k)),
                    ("✕", lambda _=False, c=r["category"], k=r["key"]: self._forget(c, k)),
                ):
                    button = QPushButton(label)
                    button.setFixedHeight(22)
                    button.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
                    button.setCursor(Qt.CursorShape.PointingHandCursor)
                    button.setToolTip(label.title() if label != "✕" else "Delete this memory")
                    button.setStyleSheet(
                        f"QPushButton {{ color: {C.TEXT_MED}; background: transparent; "
                        f"border: 1px solid {C.BORDER}; padding: 0 4px; }}"
                        f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}"
                    )
                    button.clicked.connect(callback)
                    line.addWidget(button)

                holder = QWidget()
                holder.setLayout(line)
                ilay.addWidget(holder)

            ilay.addStretch()
            scroll.setWidget(inner)
            self._lay.addWidget(scroll)

        close = QPushButton("CLOSE")
        close.setFixedHeight(30)
        close.setFont(QFont("Courier New", 9))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close.clicked.connect(self.hide)
        self._lay.addWidget(close)

        self._settle(before)
        # …and again once Qt has polished the new children, because the size
        # hint is not final until then. Harmless when the first pass already
        # got it right: _settle is idempotent.
        QTimer.singleShot(0, lambda g=before: self._settle(g))

    def _forget(self, category: str, key: str):
        from memory.memory_manager import forget
        forget(key, category)
        # Rebuild on the NEXT event-loop turn, not inside this click handler.
        # The rebuild destroys the very ✕ button that emitted this signal, and
        # Qt is entitled to touch the sender after a slot returns; tearing it
        # down mid-emission is how a widget ends up half-alive on screen.
        QTimer.singleShot(0, self._rebuild)

    def _add_memory(self):
        category, ok = QInputDialog.getItem(
            self, "Add approved memory", "Category",
            ["preferences", "projects", "identity", "relationships", "wishes", "notes"],
            0, False,
        )
        if not ok:
            return
        key, ok = QInputDialog.getText(self, "Add approved memory", "Memory key")
        if not ok or not key.strip():
            return
        value, ok = QInputDialog.getText(
            self, "Add approved memory", "Non-sensitive information to remember"
        )
        if not ok or not value.strip():
            return
        from memory.memory_manager import remember
        result = remember(key.strip(), value.strip(), category)
        if "did not save" in result:
            QMessageBox.warning(self, "Memory not saved", result)
        QTimer.singleShot(0, self._rebuild)

    def _view_memory(self, row: dict):
        QMessageBox.information(
            self, "Memory entry",
            f"Category: {row['category']}\nKey: {row['key']}\n"
            f"Source: {row['source']}\nCreated: {row['created'] or '—'}\n"
            f"Updated: {row['updated'] or '—'}\n\n{row['value']}",
        )

    def _edit_memory(self, row: dict):
        value, accepted = QInputDialog.getText(
            self, "Edit memory", f"{row['category']} / {row['key']}",
            QLineEdit.EchoMode.Normal, row["value"],
        )
        if not accepted:
            return
        from memory.memory_manager import edit_memory_entry
        result = edit_memory_entry(row["key"], value, row["category"])
        if "not save" in result or "required" in result:
            QMessageBox.warning(self, "Memory not updated", result)
        QTimer.singleShot(0, self._rebuild)

    def _archive(self, category: str, key: str):
        from memory.memory_manager import archive_memory_entry
        archive_memory_entry(key, category)
        QTimer.singleShot(0, self._rebuild)

    def _clear_all(self):
        answer = QMessageBox.question(
            self, "Clear all memory",
            "Permanently clear all active and archived long-term memory?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        from memory.memory_manager import clear_memory
        clear_memory()
        QTimer.singleShot(0, self._rebuild)


class ClipboardPanel(QWidget):
    """Floating panel shown when text is copied — offers quick Jarvis actions."""

    action_requested = pyqtSignal(str)
    _W, _H = 326, 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ClipboardPanel {{
                background: rgba(0, 8, 14, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)
        self._clip_text = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 7)
        lay.setSpacing(4)

        hdr = QHBoxLayout(); hdr.setSpacing(4)
        icon_lbl = QLabel("◈  CLIPBOARD DETECTED")
        icon_lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        icon_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        hdr.addWidget(icon_lbl); hdr.addStretch()
        x_btn = QPushButton("✕")
        x_btn.setFixedSize(16, 16)
        x_btn.setFont(QFont("Courier New", 8))
        x_btn.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.clicked.connect(self.hide)
        hdr.addWidget(x_btn)
        lay.addLayout(hdr)

        self._preview = QLabel()
        self._preview.setFont(QFont("Courier New", 8))
        self._preview.setStyleSheet(f"""
            color: {C.TEXT}; background: {C.PANEL2};
            border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 6px;
        """)
        self._preview.setWordWrap(False)
        self._preview.setFixedHeight(28)
        lay.addWidget(self._preview)

        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        _bs = (f"QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED}; "
               f"border: 1px solid {C.BORDER}; border-radius: 2px; }}"
               f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}")
        for label, cmd_fmt in [
            ("TRANSLATE", "Translate this text to English: {text}"),
            ("SUMMARISE", "Summarise this: {text}"),
            ("EXPLAIN",   "Explain this: {text}"),
            ("FIX",       "Fix grammar and spelling: {text}"),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_bs)
            b.clicked.connect(lambda _, c=cmd_fmt: self._trigger(c))
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.hide)
        self.hide()

    def _trigger(self, cmd_fmt: str):
        if self._clip_text:
            self.action_requested.emit(cmd_fmt.format(text=self._clip_text[:800]))
        self.hide()

    def show_clipboard(self, text: str):
        self._clip_text = text
        preview = text[:58].replace('\n', ' ')
        if len(text) > 58:
            preview += "…"
        self._preview.setText(f'"{preview}"')
        self.show(); self.raise_()
        self._dismiss_timer.start(8000)


class PluginSettingsOverlay(QWidget):
    """Floating overlay — renders per-plugin settings forms.

    Fully generic: it iterates the settings schemas a plugin declared via its
    PLUGIN_SETTINGS constant (delivered by PluginRegistry.settings_schemas) and
    builds a form for each. It knows NOTHING about any specific plugin, so the
    core stays clean and plugins remain pure drop-in — install a plugin that
    declares fields (e.g. the 3D-printer suite) and its section appears here;
    install none and this panel simply says there's nothing to configure.
    """

    _test_done = pyqtSignal(str, bool, str)   # namespace, ok, message
    _OW = 460

    def __init__(self, sections: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginSettingsOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self._sections = sections or []
        self._widgets: dict[tuple, object] = {}    # (namespace, key) -> input widget
        self._types:   dict[tuple, str]    = {}     # (namespace, key) -> field type
        self._status_labels: dict[str, QLabel] = {} # namespace -> status QLabel
        self._test_done.connect(self._on_test_done)

        self._fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
                    f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(8)

        root.addWidget(self._lbl("⚙  PLUGIN SETTINGS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        root.addWidget(sep)

        if not self._sections:
            root.addWidget(self._lbl(
                "No configurable plugins are installed.\nDrop a plugin that needs "
                "settings (like the 3D-printer suite) into the plugins folder and "
                "it will show up here.", 9, color=C.TEXT_DIM))
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setStyleSheet("QScrollArea { background: transparent; }")
            inner = QWidget()
            inner.setStyleSheet("background: transparent;")
            form = QVBoxLayout(inner)
            form.setContentsMargins(0, 0, 6, 0)
            form.setSpacing(6)
            for sec in self._sections:
                self._build_section(form, sec)
            form.addStretch(1)
            scroll.setWidget(inner)
            root.addWidget(scroll, 1)

        # ── bottom buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        if self._sections:
            save_btn = QPushButton("▸  SAVE")
            save_btn.setFixedHeight(34)
            save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            save_btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
            """)
            save_btn.clicked.connect(self._save_all)
            btn_row.addWidget(save_btn)

        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(34)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _lbl(self, txt, fs=9, bold=False, color=C.PRI,
             align=Qt.AlignmentFlag.AlignLeft):
        w = QLabel(txt); w.setAlignment(align); w.setWordWrap(True)
        w.setFont(QFont("Courier New", fs,
                        QFont.Weight.Bold if bold else QFont.Weight.Normal))
        w.setStyleSheet(f"color: {color}; background: transparent;")
        return w

    def _build_section(self, form: QVBoxLayout, sec: dict):
        ns     = sec.get("namespace") or sec.get("plugin") or "plugin"
        title  = sec.get("title") or ns
        fields = sec.get("fields") or []
        values = sec.get("values") or {}

        form.addSpacing(4)
        form.addWidget(self._lbl(title, 10, True, C.PRI))

        for field in fields:
            if not isinstance(field, dict) or not field.get("key"):
                continue
            key   = field["key"]
            ftype = (field.get("type") or "text").lower()
            label = field.get("label") or key
            default = field.get("default")
            stored  = values.get(key, default)

            form.addWidget(self._lbl(label.upper(), 8, color=C.TEXT_DIM))

            if ftype == "choice":
                w = QComboBox()
                w.addItems([str(o) for o in field.get("options", [])])
                w.setFont(QFont("Courier New", 9))
                w.setFixedHeight(30)
                w.setStyleSheet(
                    f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 2px 8px; }}"
                    f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
                    f"selection-background-color: {C.PRI_GHO}; }}")
                if stored is not None:
                    w.setCurrentText(str(stored))
            elif ftype == "toggle":
                w = QPushButton()
                w.setCheckable(True)
                w.setChecked(bool(stored))
                w.setFixedHeight(28)
                w.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                w.setCursor(Qt.CursorShape.PointingHandCursor)
                self._style_toggle(w)
                w.toggled.connect(lambda _=False, b=w: self._style_toggle(b))
            else:  # text / password
                w = QLineEdit("" if stored is None else str(stored))
                w.setFont(QFont("Courier New", 10))
                w.setFixedHeight(30)
                w.setStyleSheet(self._fs)
                if field.get("placeholder"):
                    w.setPlaceholderText(str(field["placeholder"]))
                if ftype == "password":
                    w.setEchoMode(QLineEdit.EchoMode.Password)

            self._widgets[(ns, key)] = w
            self._types[(ns, key)]   = ftype
            form.addWidget(w)

        # optional test/connect action button + status line
        action = sec.get("action")
        if isinstance(action, dict) and callable(action.get("run")):
            form.addSpacing(2)
            ab = QPushButton(str(action.get("label") or "TEST"))
            ab.setFixedHeight(30)
            ab.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            ab.setCursor(Qt.CursorShape.PointingHandCursor)
            ab.setStyleSheet(f"""
                QPushButton {{ background: #00091a; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
            """)
            ab.clicked.connect(lambda _=False, n=ns: self._run_action(n))
            form.addWidget(ab)

        status = self._lbl("", 8, color=C.TEXT_DIM)
        self._status_labels[ns] = status
        form.addWidget(status)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
        form.addWidget(line)

    def _style_toggle(self, btn: QPushButton):
        on = btn.isChecked()
        btn.setText("ON" if on else "OFF")
        if on:
            btn.setStyleSheet(f"QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI}; "
                              f"border: 1px solid {C.PRI}; border-radius: 3px; }}")
        else:
            btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; "
                              f"border: 1px solid {C.BORDER}; border-radius: 3px; }}")

    # ── data ──────────────────────────────────────────────────────────────────
    def _gather(self, ns: str) -> dict:
        out = {}
        for (n, key), w in self._widgets.items():
            if n != ns:
                continue
            t = self._types.get((n, key), "text")
            if t == "choice":
                out[key] = w.currentText()
            elif t == "toggle":
                out[key] = w.isChecked()
            else:
                out[key] = w.text().strip()
        return out

    def _save_ns(self, ns: str):
        from memory.config_manager import save_plugin_config
        save_plugin_config(ns, self._gather(ns))

    def _save_all(self):
        for sec in self._sections:
            ns = sec.get("namespace") or sec.get("plugin")
            if ns:
                self._save_ns(ns)
                lbl = self._status_labels.get(ns)
                if lbl:
                    lbl.setText("Saved ✓")
                    lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")

    def _run_action(self, ns: str):
        sec = next((s for s in self._sections
                    if (s.get("namespace") or s.get("plugin")) == ns), None)
        if not sec:
            return
        run_fn = (sec.get("action") or {}).get("run")
        if not callable(run_fn):
            return
        self._save_ns(ns)                 # persist what the user typed before testing
        values = self._gather(ns)
        lbl = self._status_labels.get(ns)
        if lbl:
            lbl.setText("Testing…")
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")

        def worker():
            try:
                res = run_fn(values)
                if isinstance(res, tuple) and len(res) == 2:
                    ok, msg = bool(res[0]), str(res[1])
                else:
                    ok, msg = bool(res), str(res)
            except Exception as e:
                ok, msg = False, str(e)
            self._test_done.emit(ns, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _on_test_done(self, ns: str, ok: bool, msg: str):
        lbl = self._status_labels.get(ns)
        if not lbl:
            return
        lbl.setText(msg)
        color = C.PRI if ok else "#ff6b6b"
        lbl.setStyleSheet(f"color: {color}; background: transparent;")


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback."""

    closed = pyqtSignal()

    _OW, _OH = 400, 465

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 manual_url: str = "", expiry_secs: int = 600, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(0, 4, 12, 0.95);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._expiry          = time.time() + expiry_secs
        self._on_new_key      = None
        self._auto_login_url  = auto_login_url
        self._manual_url      = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  REMOTE ACCESS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        # ── QR code ───────────────────────────────────────────────────────────
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 10px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Scan with phone camera to connect instantly", 8, color=C.TEXT_DIM))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Or enter manually:", 7, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(QFont("Courier New", 8))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(QFont("Courier New", 28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(QFont("Courier New", 8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        new_btn = QPushButton("NEW KEY")
        new_btn.setFixedHeight(32)
        new_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("DISMISS")
        close_btn.setFixedHeight(32)
        close_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(QFont("Courier New", 8))
            self._qr_label.setStyleSheet(
                "color: #888; background: white; border-radius: 10px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(QFont("Courier New", 7))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 10px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        """Call from any thread when a phone successfully connects."""
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(QFont("Courier New", 54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            "color: #00ff88; background: #001a0d; border-radius: 10px;"
        )
        self._timer_lbl.setText("Phone connected — JARVIS ready")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url    = result[0]
                key    = result[1]
                auto   = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url     = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 8px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class MainWindow(QMainWindow):
    _log_sig        = pyqtSignal(str)
    _state_sig      = pyqtSignal(str)
    _context_sig    = pyqtSignal(str)
    _task_sig       = pyqtSignal(str, str)
    _vision_status_sig = pyqtSignal(str)
    _transcription_sig = pyqtSignal(str)
    _audio_level_sig = pyqtSignal(float)
    _voice_status_sig = pyqtSignal(str)
    _task_plan_sig = pyqtSignal(str)
    _task_action_sig = pyqtSignal(str, str)
    _agent_room_sig = pyqtSignal(object)
    _agent_event_sig = pyqtSignal(object)
    _content_sig    = pyqtSignal(str, str)   # (title, text) — thread-safe content display
    _reconfig_sig   = pyqtSignal()           # trigger setup overlay from any thread
    _camera_sig     = pyqtSignal(bytes)      # show camera frame preview (small overlay)
    _cam_stream_sig = pyqtSignal(bool)       # True=start live stream, False=stop
    _cam_frame_sig  = pyqtSignal(bytes)      # live camera frame → HUD area
    _user_camera_sig = pyqtSignal(object, str, object, object, int)
    _clipboard_sig  = pyqtSignal(str)        # clipboard text changed (thread-safe)
    _confirm_sig    = pyqtSignal(str, str)   # (title, detail) — irreversible-action gate
    _confirm_hide_sig = pyqtSignal()
    _wake_dl_sig    = pyqtSignal(bool, str)  # wake-word install finished (ok, message)
    _quiz_sig       = pyqtSignal(str, object, object)  # (topic, questions, grader)
    _quiz_hide_sig  = pyqtSignal()
    _review_sig     = pyqtSignal(str, str, object, object)  # document review payload

    def __init__(self, face_path: str):
        super().__init__()
        self._face_path = face_path

        # Load customization from config
        _cfg = _read_full_config()
        self._assistant_name: str = (_cfg.get("assistant_name") or "JARVIS").strip()
        _display = self._assistant_name.upper()

        # Apply the saved UI colour BEFORE panels/stylesheets are built
        _ui_color = (_cfg.get("ui_color") or "").strip()
        if _ui_color and _ui_color.lower() != DEFAULT_UI_COLOR:
            apply_ui_accent(_ui_color)

        self.setWindowTitle(f"{_display} — {APP_VERSION}")
        icon_path = CONFIG_DIR / "jarvis.ico"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command   = None
        self.on_remote_clicked = None   # callable: () -> (url, key) | None
        self.on_interrupt      = None   # callable: () -> None — stop JARVIS mid-speech
        self.on_task_cancel    = None   # callable: () -> None — cancel future task steps
        self.on_voice_change   = None   # callable: () -> None — rebuild session with new voice
        self.on_audio_device_change = None  # callable: () -> None — reopen audio streams
        self.on_mode_change = None  # callable: (mode: str) -> None — update Live instructions
        self._confirm_overlay  = None   # live ConfirmBanner, if one is on screen
        self.get_plugins       = None   # callable: () -> list[dict], set by JarvisLive
        self.get_plugin_settings = None # callable: () -> list[dict] settings schemas, set by JarvisLive
        self.on_wake_toggle    = None   # callable: (enable: bool) -> str, set by JarvisLive
        self.on_wake_manual    = None   # callable: () -> None — manual sleep/wake
        self.on_push_to_talk   = None   # callable: (enable: bool) -> str scope
        self.ptt_hold          = None   # callable: (held: bool) -> None — windowed chord
        self.wake_get_state    = None   # callable: () -> dict {enabled, awake, ready}
        self._muted            = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._customize_overlay: CustomizeOverlay | None = None
        self._command_overlay: CommandPalette | None = None
        self._agent_room_overlay: AgentRoomOverlay | None = None
        self._agent_room_snapshot: dict = {"agents": [], "tasks": []}
        self._agent_room_events: list[dict] = []
        self._ai_core_state = "IDLE"
        self._audio_level = 0.0
        self._vision_overlay: VisionCenterOverlay | None = None
        self._task_overlay: TaskPlannerOverlay | None = None
        self._last_task_plan = ""
        self._task_actions: list[str] = []
        self._vision_state = "VISION OFFLINE · Capture starts only when requested."
        self.network_status_provider = None

        central = _AiOsBackdrop()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._left_panel = self._build_left_panel()
        self._nav_focus_index = 0
        body.addWidget(self._left_panel, stretch=0)
        QShortcut(QKeySequence("Alt+Left"), self).activated.connect(lambda: self._focus_nav(-1))
        QShortcut(QKeySequence("Alt+Right"), self).activated.connect(lambda: self._focus_nav(1))

        # Center column: HUD + resizable content panel via QSplitter
        self.hud = HudCanvas(face_path, _display)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_panel = self._build_content_panel()
        self._quiz_panel = self._build_quiz_panel()

        # Live camera container — replaces HUD when camera stream is active
        _cam_cont = QWidget()
        _cam_cont.setStyleSheet("background: #000308;")
        _cam_v = QVBoxLayout(_cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(8, 5, 8, 5)
        _cam_title = QLabel("◈  CAMERA FEED")
        _cam_title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()
        _cam_x = QPushButton("✕  CLOSE")
        _cam_x.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _cam_x.clicked.connect(self.stop_camera_stream)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)
        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setStyleSheet("background: transparent;")
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)

        # Stack: 0 = animated HUD, 1 = live camera
        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.addWidget(self.hud)
        self._hud_cam_stack.addWidget(_cam_cont)

        hud_frame = QWidget()
        hud_frame.setStyleSheet(f"background: {C.BG}; border: 1px solid {C.BORDER};")
        hud_frame_lay = QVBoxLayout(hud_frame)
        hud_frame_lay.setContentsMargins(5, 5, 5, 5)
        hud_frame_lay.setSpacing(4)
        hud_frame_lay.addWidget(self._build_hud_top_strip())
        hud_frame_lay.addWidget(self._hud_cam_stack, stretch=1)
        hud_frame_lay.addWidget(self._build_hud_bottom_strip())

        self._center_split = QSplitter(Qt.Orientation.Vertical)
        self._center_split.setStyleSheet(f"""
            QSplitter::handle {{
                background: {C.BORDER};
                height: 4px;
            }}
            QSplitter::handle:hover {{
                background: {C.PRI_DIM};
            }}
        """)
        self._center_split.addWidget(hud_frame)
        self._center_split.addWidget(self._content_panel)
        self._center_split.addWidget(self._quiz_panel)
        self._center_split.setStretchFactor(0, 3)
        self._center_split.setStretchFactor(1, 1)
        self._center_split.setCollapsible(0, False)
        body.addWidget(self._center_split, stretch=5)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)

        root.addLayout(body, stretch=1)
        root.addWidget(self._build_footer())

        # Quick-access drawer (floating overlay, built after central widget layout is done)
        self._quick_drawer = self._build_quick_drawer()
        self._update_autostart_btn(self._check_autostart())
        from memory.config_manager import get_brief_enabled as _gbe
        self._update_brief_btn(_gbe())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metric update timer
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._context_sig.connect(self._set_context_status)
        self._task_sig.connect(self._set_task_status)
        self._vision_status_sig.connect(self._set_vision_status)
        self._transcription_sig.connect(self._show_transcription)
        self._audio_level_sig.connect(self._update_audio_level)
        self._voice_status_sig.connect(self._set_voice_status)
        self._task_plan_sig.connect(self._set_task_plan)
        self._task_action_sig.connect(self._record_task_action)
        self._agent_room_sig.connect(self._set_agent_room_data)
        self._agent_event_sig.connect(self._record_agent_event)
        self._content_sig.connect(self._show_content)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._confirm_sig.connect(self._show_confirm_banner)
        self._confirm_hide_sig.connect(self._hide_confirm_banner)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._user_camera_sig.connect(self._on_user_camera_data)
        self._clipboard_sig.connect(self._show_clipboard_panel)
        self._wake_dl_sig.connect(self._on_wake_install_done)
        self._quiz_sig.connect(self._show_quiz)
        self._quiz_hide_sig.connect(self._hide_quiz)
        self._review_sig.connect(self._show_review)
        self._cam_stop = threading.Event()
        self._cam_thread: threading.Thread | None = None
        self._camera_mode = "off"
        self._user_camera_enabled = False
        self._user_camera_generation = 0

        # Camera preview overlay (child of central widget, positioned in resizeEvent)
        self._cam_preview = _CameraPreview(self.centralWidget())

        # Clipboard panel (child of central widget, bottom-center)
        self._clipboard_panel = ClipboardPanel(self.centralWidget())
        self._clipboard_panel.action_requested.connect(self._on_clipboard_action)
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_intr = QShortcut(QKeySequence("Escape"), self)
        sc_intr.activated.connect(self._do_interrupt)
        sc_palette = QShortcut(QKeySequence("Ctrl+K"), self)
        sc_palette.activated.connect(self._open_command_palette)

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        self._cam_preview.show_frame(img_bytes)
        cw = self.centralWidget()
        pw = _CameraPreview._W
        ph = self._cam_preview.height()
        self._cam_preview.setGeometry(
            cw.width() - self._right_panel.width() - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )

    # --- Live camera stream in HUD area ------------------------------------
    def _on_cam_stream(self, start: bool) -> None:
        if start:
            self._hud_cam_stack.setCurrentIndex(1)
        else:
            self._hud_cam_stack.setCurrentIndex(0)
            self._cam_live_lbl.clear()

    def _on_cam_frame(self, data: bytes) -> None:
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
            if w > 1 and h > 1:
                self._cam_live_lbl.setPixmap(
                    px.scaled(w, h,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
                )

    def _on_user_camera_data(self, frame, status: str,
                             gaze_x, gaze_y, generation: int) -> None:
        if generation != self._user_camera_generation:
            return
        self._agent_room_overlay and self._agent_room_overlay.set_user_camera_data(
            frame, status, gaze_x or 0.5, gaze_y or 0.5
        )
        if status.startswith("CAMERA OFF"):
            self._user_camera_enabled = False
            if self._camera_mode == "user":
                self._camera_mode = "off"

    def _on_user_camera_toggle(self, enabled: bool) -> None:
        if not enabled:
            self.stop_user_camera_monitor()
            return

        if self._cam_thread and self._cam_thread.is_alive():
            if self._camera_mode == "user" and self._user_camera_enabled:
                generation = self._user_camera_generation
                self._user_camera_sig.emit(
                    None, "CAMERA ON · Local presence detection is active.",
                    0.5, 0.5, generation,
                )
                return
            self._user_camera_generation += 1
            self._user_camera_sig.emit(
                None, "CAMERA OFF · Another camera operation is active.",
                0.5, 0.5, self._user_camera_generation,
            )
            return

        self._user_camera_generation += 1
        generation = self._user_camera_generation
        self._cam_stop.clear()
        self._camera_mode = "user"
        self._user_camera_enabled = True
        self._user_camera_sig.emit(
            None, "CAMERA ON · Starting local presence detection...",
            0.5, 0.5, generation,
        )
        self._cam_thread = threading.Thread(
            target=self._cam_loop, args=("user", generation),
            daemon=True, name="user-presence-camera",
        )
        self._cam_thread.start()

    def stop_user_camera_monitor(self, wait: bool = False) -> bool:
        self._user_camera_generation += 1
        generation = self._user_camera_generation
        self._user_camera_enabled = False
        thread = self._cam_thread if self._camera_mode == "user" else None
        if thread is not None:
            self._cam_stop.set()
        self._user_camera_sig.emit(
            None, "CAMERA OFF · No capture is active.", 0.5, 0.5, generation
        )
        if thread is not None and wait and thread.is_alive():
            thread.join(timeout=2.0)
        stopped = thread is None or not thread.is_alive()
        if stopped and self._camera_mode == "user":
            self._camera_mode = "off"
        return stopped

    def start_camera_stream(self) -> None:
        if self._cam_thread and self._cam_thread.is_alive():
            if self._camera_mode == "user":
                self.stop_user_camera_monitor(wait=True)
            if self._cam_thread.is_alive():
                self._cam_stream_sig.emit(False)
                print("[Camera] Camera is already in use; vision stream was not started.")
                return
        self._cam_stop.clear()
        self._camera_mode = "vision"
        self._cam_thread = threading.Thread(
            target=self._cam_loop, args=("vision", None),
            daemon=True, name="cam-stream",
        )
        self._cam_stream_sig.emit(True)
        self._cam_thread.start()

    def _cam_loop(self, mode: str = "vision", generation: int | None = None) -> None:
        cap = None
        failure = ""
        try:
            import cv2
            cascade = None
            if mode == "user":
                cv2_data = getattr(cv2, "data", None)
                cascade_root = getattr(cv2_data, "haarcascades", None)
                if cascade_root:
                    cascade_path = Path(cascade_root) / "haarcascade_frontalface_default.xml"
                    detector = cv2.CascadeClassifier(str(cascade_path))
                    if not detector.empty():
                        cascade = detector
            # Reuse camera index detected by screen_processor (cached in api_keys.json)
            cam_idx = 0
            try:
                import json as _j
                cfg = _j.loads((CONFIG_DIR / "api_keys.json").read_text())
                cam_idx = int(cfg.get("camera_index", 0))
            except Exception:
                pass
            try:
                backend = cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            except AttributeError:
                backend = 0
            cap = cv2.VideoCapture(cam_idx, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                failure = "CAMERA OFF · Unable to access the camera."
                return
            if mode == "user":
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
            # warm-up frames
            for _ in range(5):
                cap.read()
            delay = 0.4 if mode == "user" else 0.033
            while not self._cam_stop.wait(delay) and cap.isOpened():
                ret, frame = cap.read()
                if not ret or frame is None:
                    if mode == "user":
                        failure = "CAMERA OFF · Camera frame unavailable."
                        break
                    continue
                if mode == "user":
                    detected = None
                    gaze_x = gaze_y = 0.5
                    display_frame = cv2.resize(frame, (320, 240))
                    if cascade is not None:
                        gray = cv2.cvtColor(display_frame, cv2.COLOR_BGR2GRAY)
                        faces = cascade.detectMultiScale(
                            gray, scaleFactor=1.2, minNeighbors=5,
                            minSize=(36, 36),
                        )
                        detected = len(faces) > 0
                        if detected:
                            x, y, width, height = max(
                                faces, key=lambda rect: rect[2] * rect[3]
                            )
                            gaze_x = (x + width / 2) / 320
                            gaze_y = (y + height / 2) / 240
                            cv2.rectangle(
                                display_frame, (x, y), (x + width, y + height),
                                (255, 220, 0), 1,
                            )
                        status = (
                            "CAMERA ON · USER DETECTED"
                            if detected else "CAMERA ON · NO USER DETECTED"
                        )
                    else:
                        status = "CAMERA ON · PRESENCE N/A (local detector unavailable)"
                    encoded, buf = cv2.imencode(
                        ".jpg", display_frame,
                        [cv2.IMWRITE_JPEG_QUALITY, 45],
                    )
                    if not encoded:
                        continue
                    self._user_camera_sig.emit(
                        buf.tobytes(), status, gaze_x, gaze_y, generation or 0
                    )
                else:
                    encoded, buf = cv2.imencode(
                        ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65]
                    )
                    if encoded:
                        self._cam_frame_sig.emit(buf.tobytes())
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
            if mode == "user":
                failure = f"CAMERA OFF · Presence detection unavailable ({type(e).__name__})."
        finally:
            if cap is not None:
                cap.release()
            if mode == "vision":
                self._camera_mode = "off"
                self._cam_stream_sig.emit(False)
            elif failure and generation is not None:
                self._user_camera_sig.emit(None, failure, 0.5, 0.5, generation)

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_jarvis_icon(out_path: Path) -> bool:
        """
        Render the JARVIS holographic-core icon at 4× resolution and downsample
        for crisp results at all sizes. Saves a multi-res .ico to out_path.
        Returns True on success.
        """
        try:
            import math
            import PIL.Image
            import PIL.ImageDraw
            import PIL.ImageFilter
        except ImportError:
            return False

        CYAN = (0, 220, 255)
        BLUE = (0, 110, 255)
        VIOLET = (108, 99, 255)
        DARK = (2, 7, 16)
        WHITE = (225, 248, 255)
        artwork = CONFIG_DIR / "deva_ai_os.png"
        if artwork.is_file():
            try:
                with PIL.Image.open(artwork) as source:
                    source = source.convert("RGBA")
                    side = min(source.size)
                    left = (source.width - side) // 2
                    top = (source.height - side) // 2
                    source = source.crop((left, top, left + side, top + side))
                    sizes = [256, 128, 64, 48, 32, 16]
                    frames = [
                        source.resize((size, size), PIL.Image.Resampling.LANCZOS)
                        for size in sizes
                    ]
                    frames[0].save(
                        out_path,
                        format="ICO",
                        append_images=frames[1:],
                        sizes=[(size, size) for size in sizes],
                    )
                return True
            except Exception as e:
                print(f"[Shortcut] Icon artwork could not be loaded: {e}")

        def _render(sz: int) -> PIL.Image.Image:
            S = sz * 4
            img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d   = PIL.ImageDraw.Draw(img)
            cx = cy = S // 2
            margin = S // 28
            radius = S // 5
            d.rounded_rectangle(
                [margin, margin, S - margin, S - margin],
                radius=radius,
                fill=(*DARK, 255),
                outline=(*CYAN, 170),
                width=max(2, S // 100),
            )

            glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gd = PIL.ImageDraw.Draw(glow_layer)
            core_radius = int(S * 0.14)
            gd.ellipse(
                [cx - core_radius * 2, cy - core_radius * 2,
                 cx + core_radius * 2, cy + core_radius * 2],
                fill=(*CYAN, 100),
            )
            glow_layer = glow_layer.filter(PIL.ImageFilter.GaussianBlur(S // 12))
            img = PIL.Image.alpha_composite(img, glow_layer)
            d = PIL.ImageDraw.Draw(img)

            def ring(radius: int, color, width: int, start=0, end=360):
                d.arc(
                    [cx - radius, cy - radius, cx + radius, cy + radius],
                    start, end, fill=(*color, 220), width=width,
                )

            def polygon(radius: int, color, width: int, rotation=-90):
                points = [
                    (cx + int(radius * math.cos(math.radians(rotation + i * 60))),
                     cy + int(radius * math.sin(math.radians(rotation + i * 60))))
                    for i in range(6)
                ]
                d.line(points + [points[0]], fill=(*color, 225),
                       width=width, joint="curve")

            outer = int(S * 0.40)
            ring(outer, CYAN, max(2, S // 55), 12, 166)
            ring(outer, BLUE, max(2, S // 65), 194, 350)
            polygon(int(S * 0.31), (0, 115, 160), max(2, S // 90))
            ring(int(S * 0.235), VIOLET, max(2, S // 85), 205, 330)
            ring(int(S * 0.205), CYAN, max(2, S // 65), 20, 175)

            for i in range(8):
                angle = math.radians(i * 45)
                inner_r, outer_r = int(S * 0.34), int(S * 0.39)
                d.line(
                    [cx + int(inner_r * math.cos(angle)),
                     cy + int(inner_r * math.sin(angle)),
                     cx + int(outer_r * math.cos(angle)),
                     cy + int(outer_r * math.sin(angle))],
                    fill=(*WHITE, 190), width=max(1, S // 130),
                )

            spoke_inner, spoke_outer = int(S * 0.17), int(S * 0.29)
            for i in range(6):
                angle = math.radians(i * 60 - 30)
                start = (cx + int(spoke_inner * math.cos(angle)),
                         cy + int(spoke_inner * math.sin(angle)))
                end = (cx + int(spoke_outer * math.cos(angle)),
                       cy + int(spoke_outer * math.sin(angle)))
                d.line([start, end], fill=(*CYAN, 180), width=max(2, S // 75))
                node_r = max(2, S // 42)
                d.ellipse([end[0] - node_r, end[1] - node_r,
                           end[0] + node_r, end[1] + node_r],
                          fill=(*WHITE, 235))

            d.ellipse(
                [cx - core_radius, cy - core_radius,
                 cx + core_radius, cy + core_radius],
                fill=(*CYAN, 235),
                outline=(*WHITE, 255),
                width=max(2, S // 70),
            )
            inner_core = core_radius // 2
            d.ellipse([cx - inner_core, cy - inner_core,
                       cx + inner_core, cy + inner_core], fill=(*WHITE, 255))

            return img.resize((sz, sz), PIL.Image.LANCZOS)

        try:
            sizes  = [256, 128, 64, 48, 32, 16]
            frames = [_render(s) for s in sizes]
            frames[0].save(
                out_path,
                format="ICO",
                append_images=frames[1:],
                sizes=[(s, s) for s in sizes],
            )
            return True
        except Exception as e:
            print(f"[Shortcut] ⚠️  Icon generation failed: {e}")
            return False

    @staticmethod
    def _create_lnk_windows(lnk: str, target: str, args: str,
                             work_dir: str, icon_loc: str) -> None:
        """
        Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.
        Tries win32com (pywin32) first; falls back to wscript.exe + VBScript.
        wscript.exe is a GUI-mode host — it never opens a console window.
        """
        # ── Option 1: pywin32 (pure Python COM, zero subprocess) ──────────
        try:
            from win32com.client import Dispatch   # type: ignore
            sh = Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(lnk)
            sc.TargetPath       = target
            sc.Arguments        = f'"{args}"'
            sc.WorkingDirectory = work_dir
            sc.Description      = "J.A.R.V.I.S AI Assistant"
            sc.IconLocation     = icon_loc
            sc.save()
            return
        except ImportError:
            pass

        # ── Option 2: wscript.exe + VBScript (always available on Windows,
        #    GUI-mode executable — never opens a console window) ────────────
        vbs = "\n".join([
            'Set ws = CreateObject("WScript.Shell")',
            f'Set sc = ws.CreateShortcut("{lnk}")',
            f'sc.TargetPath = "{target}"',
            f'sc.Arguments = Chr(34) & "{args}" & Chr(34)',
            f'sc.WorkingDirectory = "{work_dir}"',
            'sc.Description = "J.A.R.V.I.S AI Assistant"',
            f'sc.IconLocation = "{icon_loc}"',
            'sc.Save',
        ])
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".vbs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs)
            proc = subprocess.Popen(
                ["wscript.exe", "/nologo", tmp],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @staticmethod
    def _get_desktop_dir() -> Path:
        """
        Resolve the user's REAL desktop directory instead of assuming
        ~/Desktop, which breaks when:
          • OneDrive "Known Folder Move" relocates the desktop
            (C:/Users/x/OneDrive/Desktop) — very common on Win 10/11;
          • the XDG desktop is localized on Linux (~/Masaüstü,
            ~/Schreibtisch, ~/Bureau, …).
        Falls back to ~/Desktop only as a last resort.
        """
        home = Path.home()
        _os = platform.system()

        if _os == "Windows":
            # ── 1) SHGetKnownFolderPath(FOLDERID_Desktop) — the canonical
            #       answer; follows OneDrive redirection. No dependencies. ──
            try:
                import ctypes
                from ctypes import wintypes

                class _GUID(ctypes.Structure):
                    _fields_ = [("Data1", wintypes.DWORD),
                                ("Data2", wintypes.WORD),
                                ("Data3", wintypes.WORD),
                                ("Data4", ctypes.c_ubyte * 8)]

                # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
                fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                            (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                                 0x9A, 0x87, 0xC6, 0x41))
                buf = ctypes.c_wchar_p()
                if ctypes.windll.shell32.SHGetKnownFolderPath(
                        ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                    p = Path(buf.value)
                    ctypes.windll.ole32.CoTaskMemFree(buf)
                    if p.is_dir():
                        return p
            except Exception:
                pass

            # ── 2) Registry: User Shell Folders (may contain %VARS%) ──────
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\User Shell Folders") as key:
                    val, _t = winreg.QueryValueEx(key, "Desktop")
                p = Path(os.path.expandvars(val))
                if p.is_dir():
                    return p
            except Exception:
                pass

        elif _os == "Linux":
            # ── xdg-user-dir honours localized names (~/Masaüstü, …) ──────
            try:
                out = subprocess.run(["xdg-user-dir", "DESKTOP"],
                                     capture_output=True, text=True, timeout=5)
                p = Path(out.stdout.strip())
                if out.stdout.strip() and p != home and p.is_dir():
                    return p
            except Exception:
                pass
            try:
                cfg = home / ".config" / "user-dirs.dirs"
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("XDG_DESKTOP_DIR"):
                        val = line.split("=", 1)[1].strip().strip('"')
                        p = Path(val.replace("$HOME", str(home)))
                        if p != home and p.is_dir():
                            return p
            except Exception:
                pass

        # macOS: ~/Desktop is always the real path (localization is
        # display-only). Everything else lands here as a last resort.
        return home / "Desktop"

    def _create_desktop_shortcut(self):
        """
        Create a desktop shortcut on Windows / macOS / Linux.
        Never opens a terminal, console, or PowerShell window on any platform.
        """
        import stat as _stat
        script  = Path(__file__).resolve().parent / "main.py"
        python  = Path(sys.executable)
        desktop = self._get_desktop_dir()

        # Arc-reactor icon (.ico — also exported as .png for Linux/macOS)
        ico_path = Path(__file__).resolve().parent / "config" / "jarvis.ico"
        if not ico_path.exists():
            self._build_jarvis_icon(ico_path)

        try:
            _os = platform.system()

            # ── Windows ───────────────────────────────────────────────────────
            if _os == "Windows":
                pythonw  = python.parent / "pythonw.exe"
                target   = str(pythonw if pythonw.exists() else python)
                lnk      = str(desktop / "J.A.R.V.I.S.lnk")
                icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
                self._create_lnk_windows(lnk, target, str(script),
                                         str(script.parent), icon_loc)

            # ── macOS — proper .app bundle (no Terminal window) ───────────────
            elif _os == "Darwin":
                app     = desktop / "J.A.R.V.I.S.app"
                mac_dir = app / "Contents" / "MacOS"
                res_dir = app / "Contents" / "Resources"
                mac_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(exist_ok=True)

                # Launcher executable (bash — runs as background process,
                # macOS does NOT open Terminal for executables inside .app bundles)
                launcher = mac_dir / "JARVIS"
                launcher.write_text(
                    "#!/usr/bin/env bash\n"
                    f'cd "{script.parent}"\n'
                    f'exec "{python}" "{script}"\n'
                )
                launcher.chmod(launcher.stat().st_mode
                               | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)

                # Minimal Info.plist (required for .app recognition)
                (app / "Contents" / "Info.plist").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>CFBundleExecutable</key><string>JARVIS</string>\n'
                    '  <key>CFBundleIdentifier</key>'
                    '<string>com.jarvis.assistant</string>\n'
                    '  <key>CFBundleName</key><string>J.A.R.V.I.S</string>\n'
                    '  <key>CFBundlePackageType</key><string>APPL</string>\n'
                    '  <key>CFBundleVersion</key><string>1.0</string>\n'
                    '</dict></plist>\n'
                )

                # Optional: copy icon as .icns (skip silently if Pillow is missing)
                try:
                    import PIL.Image
                    icns = res_dir / "AppIcon.icns"
                    PIL.Image.open(ico_path).save(icns, format="ICNS")
                    # Inject icon reference into plist
                    plist = app / "Contents" / "Info.plist"
                    txt = plist.read_text()
                    plist.write_text(
                        txt.replace(
                            '</dict></plist>',
                            '  <key>CFBundleIconFile</key>'
                            '<string>AppIcon</string>\n</dict></plist>\n',
                        )
                    )
                except Exception:
                    pass  # icon is optional

            # ── Linux — .desktop file (Terminal=false, no console) ────────────
            else:
                # Export .ico → .png for better desktop integration
                png_path = ico_path.with_suffix(".png")
                if not png_path.exists() and ico_path.exists():
                    try:
                        import PIL.Image
                        PIL.Image.open(ico_path).resize(
                            (256, 256), PIL.Image.LANCZOS
                        ).save(png_path, format="PNG")
                    except Exception:
                        png_path = ico_path  # fallback to .ico

                icon_line = f"Icon={png_path}\n" if png_path.exists() else ""
                desk = desktop / "J.A.R.V.I.S.desktop"
                desk.write_text(
                    "[Desktop Entry]\n"
                    "Name=J.A.R.V.I.S\n"
                    f"Exec={python} {script}\n"
                    f"Path={script.parent}\n"
                    "Type=Application\n"
                    "Terminal=false\n"
                    "Categories=Utility;\n"
                    + icon_line
                )
                desk.chmod(desk.stat().st_mode | 0o755)

            self._log.append_log("SYS: Desktop shortcut created.")
        except Exception as e:
            self._log.append_log(f"ERR: Shortcut failed — {e}")

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw = self.centralWidget()
        self._left_panel.setFixedWidth(max(220, min(310, round(cw.width() * 0.19))))
        self._right_panel.setFixedWidth(max(270, min(340, round(cw.width() * 0.22))))
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._remote_overlay and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._customize_overlay and self._customize_overlay.isVisible():
            ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
            self._customize_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._agent_room_overlay and self._agent_room_overlay.isVisible():
            ow, oh = AgentRoomOverlay._OW, AgentRoomOverlay._OH
            self._agent_room_overlay.setGeometry(
                max(0, (cw.width() - ow) // 2),
                max(0, (cw.height() - oh) // 2),
                min(ow, cw.width()), min(oh, cw.height()),
            )
        # Camera preview — bottom-right corner of the center/HUD area
        pw = _CameraPreview._W
        ph = self._cam_preview.height() or _CameraPreview._H
        self._cam_preview.setGeometry(
            cw.width() - self._right_panel.width() - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )
        # Clipboard panel — bottom-center
        if hasattr(self, '_clipboard_panel') and self._clipboard_panel.isVisible():
            self._position_clipboard_panel()
        # Quick drawer — reposition if open
        if hasattr(self, '_quick_drawer') and self._quick_drawer.isVisible():
            self._position_quick_drawer()

    def _network_statuses(self, internet_online: bool | None,
                          ping_ms: float | None = None) -> dict[str, str]:
        internet_state = "N/A"
        if internet_online is True:
            internet_state = "UNSTABLE" if ping_ms is not None and ping_ms >= 250 else "ONLINE"
        elif internet_online is False:
            internet_state = "OFFLINE"
        statuses = {
            "JARVIS CORE": "ONLINE",
            "INTERNET": internet_state,
            "BACKEND": "N/A",
            "WEBSOCKET": "N/A",
            "AI API": "N/A",
            "CLOUD": "N/A",
            "DEVICES": "N/A",
        }
        provider = self.network_status_provider
        if callable(provider):
            try:
                live = provider()
                for key in ("BACKEND", "WEBSOCKET", "AI API", "CLOUD", "DEVICES"):
                    value = live.get(key)
                    statuses[key] = str(value).upper() if value else "N/A"
                self._network_provider_error = False
            except Exception:
                self._network_provider_error = True
        else:
            self._network_provider_error = False
        return statuses

    def _show_network_node_details(self, node: str):
        snap = _metrics.snapshot()
        statuses = self._network_statuses(
            snap.get("internet_online"), snap.get("ping_ms"))
        lines = [f"{node}  ·  {statuses.get(node, 'N/A')}"]
        if node == "INTERNET":
            ping = snap.get("ping_ms")
            down = snap.get("download_mbps")
            up = snap.get("upload_mbps")
            lines.extend((
                f"TCP RTT: {ping:.0f} ms" if ping is not None else "TCP RTT: N/A",
                f"DOWNLOAD: {down:.2f} Mbps" if down is not None else "DOWNLOAD: N/A",
                f"UPLOAD: {up:.2f} Mbps" if up is not None else "UPLOAD: N/A",
            ))
        elif node in {"AI API", "CLOUD"}:
            lines.append("Gemini Live session state from the active application backend.")
        elif node in {"BACKEND", "WEBSOCKET", "DEVICES"}:
            lines.append("State read from the running application and its existing dashboard server.")
        self._network_detail.setText("  |  ".join(lines))

    def _update_metrics(self):
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        self._bar_mem.set_value(mem, f"{mem:.0f}%")
        try:
            storage = psutil.disk_usage(str(BASE_DIR)).percent
        except Exception:
            storage = 0
        self._bar_storage.set_value(storage, f"{storage:.0f}%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        self._bar_net.set_value(net_pct, net_str)
        gpu = snap["gpu"]
        tmp = snap["tmp"]

        if hasattr(self, '_system_status_labels'):
            self._system_status_labels['CPU'].setText(f"CPU  {cpu:.0f}%")
            self._system_status_labels['RAM'].setText(f"RAM  {mem:.0f}%")
            self._system_status_labels['GPU'].setText(
                f"GPU  {gpu:.0f}%" if gpu >= 0 else "GPU  N/A")
            self._system_status_labels['STORAGE'].setText(
                f"STORAGE  {storage:.0f}%")
            if hasattr(self, '_system_status_bars'):
                self._system_status_bars['CPU'].setValue(round(cpu))
                self._system_status_bars['RAM'].setValue(round(mem))
                self._system_status_bars['GPU'].setValue(
                    round(gpu) if gpu >= 0 else 0)
                self._system_status_bars['STORAGE'].setValue(round(storage))
        download = snap.get("download_mbps")
        upload = snap.get("upload_mbps")
        ping = snap.get("ping_ms")
        internet_online = snap.get("internet_online")
        if hasattr(self, "_traffic_graph"):
            statuses = self._network_statuses(internet_online, ping)
            self._traffic_graph.add_sample(download, upload)
            self._network_graph.set_snapshot(
                statuses,
                download,
                upload,
                ping,
            )
            for key, label in self._network_status_labels.items():
                status = statuses.get(key, "N/A")
                color = _NetworkGraph._state_color(status).name()
                label.setText(status)
                label.setStyleSheet(f"color: {color}; background: transparent;")
            self._download_value.setText(
                f"{download:.2f} Mbps" if download is not None else "N/A")
            self._upload_value.setText(
                f"{upload:.2f} Mbps" if upload is not None else "N/A")
            self._ping_value.setText(f"{ping:.0f} ms" if ping is not None else "N/A")
            warning = ""
            if self._network_provider_error:
                warning = "Connection status service is unavailable."
            elif internet_online is False:
                warning = "Internet connectivity probe failed."
            elif download is None or upload is None or ping is None:
                warning = "Some network measurements are unavailable."
            self._network_warning.setText(warning)
            self._network_warning.setVisible(bool(warning))

        # GPU
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._bar_gpu.set_value(0, "N/A")

        # TMP
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
        else:
            self._bar_tmp.set_value(0, "N/A")
        if hasattr(self, '_core_temp_lbl'):
            self._core_temp_lbl.setText(f"CORE TEMP  {tmp:.0f}C" if tmp >= 0 else "CORE TEMP  N/A")
            self._audio_level_lbl.setText(
                "AUDIO  SPEAKING" if self.hud.speaking else "AUDIO  READY")
        if hasattr(self, '_weather_lbl'):
            self._weather_lbl.setText(
                f"CORE TEMP  {tmp:.0f} °C\nLOCAL SENSOR" if tmp >= 0
                else "CORE TEMP  --\nSENSOR UNAVAILABLE")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("UP  --:--")

        if hasattr(self, '_drawer_status_labels'):
            self._drawer_status_labels['CPU'].setText(f"CPU {cpu:.0f}%")
            self._drawer_status_labels['RAM'].setText(f"RAM {mem:.0f}%")
            self._drawer_status_labels['NET'].setText(f"NET {net_str}")
            self._drawer_status_labels['UP'].setText(self._uptime_lbl.text())
        if hasattr(self, '_status_labels'):
            self._status_labels['mode'].setText(f"AI {self._active_mode.upper()[:6]}")
            self._status_labels['cpu'].setText(f"CPU {cpu:.0f}%")
            self._status_labels['mem'].setText(f"RAM {mem:.0f}%")
            self._status_labels['net'].setText(f"NET {net_str}")
            self._status_labels['sec'].setText("SECURE")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"PROC  {proc_count}")
        except Exception:
            self._proc_lbl.setText("PROC  --")


    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(64)
        w.setStyleSheet(f"background: rgba(3, 9, 17, 232); border-bottom: 1px solid {C.BORDER_B};")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(10)

        def _badge(txt, color=C.TEXT_MED, size=8, bold=False):
            l = QLabel(txt)
            l.setFont(QFont("Courier New", size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        brand = QVBoxLayout()
        brand.setSpacing(0)
        self._title_lbl = _badge("◈  JARVIS / DEVA AI OS", C.PRI, 14, True)
        self._sub_lbl = _badge("AI COMMAND CENTER  ·  SECURE SESSION", C.TEXT_DIM, 7, True)
        brand.addWidget(self._title_lbl)
        brand.addWidget(self._sub_lbl)
        lay.addLayout(brand)

        self._header_status = QHBoxLayout()
        self._header_status.setSpacing(6)
        self._status_labels = {}
        for label, key, color in (
            ("AI OS", "mode", C.PRI),
            ("CPU --%", "cpu", C.ACC2),
            ("RAM --%", "mem", C.PRI),
            ("NET --", "net", C.GREEN),
            ("SECURE", "sec", C.GREEN),
        ):
            chip = QLabel(label)
            chip.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            chip.setStyleSheet(f"color: {color}; background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 10px; padding: 4px 8px;")
            chip.setToolTip({"mode": "Current operating mode", "cpu": "Processor utilisation", "mem": "Memory utilisation", "net": "Network throughput", "sec": "Security posture"}[key])
            self._header_status.addWidget(chip)
            self._status_labels[key] = chip
        self._context_badge = _badge("CTX  0", C.TEXT_MED, 7, True)
        self._context_badge.setToolTip("Recent conversation turns in this session")
        self._header_status.addWidget(self._context_badge)
        self._task_badge = _badge("TASK  IDLE", C.TEXT_MED, 7, True)
        self._task_badge.setToolTip("Current task lifecycle state")
        self._header_status.addWidget(self._task_badge)
        self._memory_badge = _badge("MEM  LOCAL", C.PRI, 7, True)
        self._memory_badge.setToolTip(
            "Long-term memory stays on this device; inspect it in Memory Center."
        )
        self._header_status.addWidget(self._memory_badge)
        lay.addLayout(self._header_status)

        nav = QHBoxLayout()
        nav.setSpacing(3)
        for label, target in (
            ("THINK", "assistant"), ("CONNECT", "connect"),
            ("AUTOMATE", "automate"), ("EVOLVE", "settings"),
        ):
            btn = QPushButton(label)
            btn.setFixedHeight(30)
            btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip({
                "assistant": "Focus command input",
                "connect": "Open the existing remote dashboard",
                "automate": "Ask the assistant about automation",
                "settings": "Open assistant customization",
            }[target])
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_MED};
                    border: 1px solid transparent; padding: 0 9px;
                }}
                QPushButton:hover {{
                    color: {C.PRI}; background: {C.PRI_GHO};
                    border-color: {C.BORDER_B};
                }}
            """)
            btn.clicked.connect(lambda _=False, key=target: self._navigate(key))
            nav.addWidget(btn)
        lay.addLayout(nav)
        lay.addStretch()

        self._state_indicator = _badge("●  AI CORE INITIALISING", C.TEXT_MED, 8, True)
        lay.addWidget(self._state_indicator)
        self._header_mic_btn = QPushButton()
        self._header_mic_btn.setFixedHeight(28)
        self._header_mic_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._header_mic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header_mic_btn.setToolTip("Toggle microphone")
        self._header_mic_btn.setStyleSheet(f"""
            QPushButton {{ color: {C.GREEN}; background: {C.PANEL2};
                border: 1px solid {C.BORDER}; padding: 0 7px; }}
            QPushButton:hover {{ border-color: {C.PRI}; color: {C.PRI}; }}
        """)
        self._header_mic_btn.clicked.connect(self._toggle_mute)
        self._style_header_mic_btn()
        lay.addWidget(self._header_mic_btn)
        self._audio_level_badge = _badge("AUDIO  --", C.TEXT_DIM, 7, True)
        self._audio_level_badge.setToolTip("Measured microphone/output waveform level")
        lay.addWidget(self._audio_level_badge)
        self._voice_status_badge = _badge("VOICE  IDLE", C.TEXT_DIM, 7, True)
        lay.addWidget(self._voice_status_badge)

        clock_col = QVBoxLayout()
        clock_col.setSpacing(1)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        clock_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont("Courier New", 7))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        clock_col.addWidget(self._date_lbl)
        lay.addLayout(clock_col)

        for label, callback in (
            ("—", self.showMinimized),
            ("□", self._toggle_window_state),
            ("×", self.close),
        ):
            button = QPushButton(label)
            button.setFixedSize(26, 26)
            button.setFont(QFont("Segoe UI", 10))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip({"—": "Minimize", "□": "Restore / maximize",
                               "×": "Close"}[label])
            button.setStyleSheet(f"""
                QPushButton {{ color: {C.TEXT_DIM}; background: transparent;
                    border: 1px solid {C.BORDER}; }}
                QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI}; }}
            """)
            button.clicked.connect(callback)
            lay.addWidget(button)

        self._drawer_btn = QPushButton("⚙")
        self._drawer_btn.setFixedSize(26, 26)
        self._drawer_btn.setFont(QFont("Courier New", 11))
        self._drawer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drawer_btn.setToolTip("Settings & Controls")
        self._drawer_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 4px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
            QPushButton:checked {{ color: {C.PRI}; border-color: {C.PRI}; background: {C.PRI_GHO}; }}
        """)
        self._drawer_btn.setCheckable(True)
        self._drawer_btn.clicked.connect(self._toggle_drawer)
        lay.addWidget(self._drawer_btn)
        return w

    def _toggle_window_state(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _dock_expand(self, button, event=None):
        if not button.isChecked():
            button.setStyleSheet(
                f"QPushButton {{ text-align: left; color: {C.PRI}; background: {C.PRI_GHO}; border: 1px solid {C.BORDER_B}; padding: 0 8px; min-width: 140px; }}"
            )

    def _dock_contract(self, button, event=None):
        if not button.isChecked():
            button.setStyleSheet(self._nav_button_style(False))

    def _focus_nav(self, delta: int):
        if not hasattr(self, "_nav_order") or not self._nav_order:
            return
        self._nav_focus_index = (self._nav_focus_index + delta) % len(self._nav_order)
        button = self._nav_order[self._nav_focus_index]
        button.setFocus()
        for key, candidate in self._nav_buttons.items():
            if candidate is button:
                self._navigate(key)
                break

    def _navigate(self, target: str):
        if target == "assistant":
            self._input.setFocus()
        elif target == "connect":
            self._open_remote()
        elif target == "automate":
            self._send_dock_command("What automation tasks can you help me with?")
        elif target == "settings":
            self._open_customize()
        elif target == "files":
            self._drop_zone._browse()
        elif target == "plugins":
            self._open_plugin_manager()
        elif target == "memory":
            self._open_memory_panel()
        elif target == "system":
            self._input.setPlaceholderText("Ask JARVIS to control a system setting…")
            self._input.setFocus()
        elif target == "voice":
            self._toggle_mute()
        elif target == "home":
            self._input.setPlaceholderText("Type a command or question…")
            self.hud.setFocus()
        if hasattr(self, "_nav_buttons"):
            for key, button in self._nav_buttons.items():
                active = key == target
                button.setChecked(active)
                button.setStyleSheet(self._nav_button_style(active))

    def _activate_mode(self, mode: str):
        allowed = {name for name in self._mode_buttons}
        if mode not in allowed:
            raise ValueError(f"Unsupported AI workspace mode: {mode}")
        self._active_mode = mode
        for name, button in self._mode_buttons.items():
            active = name == mode
            button.setChecked(active)
            button.setStyleSheet(self._nav_button_style(active))
        if hasattr(self, "_mode_status_lbl"):
            self._mode_status_lbl.setText(f"WORKSPACE  {mode.upper()}")
        if hasattr(self, "_status_labels") and "mode" in self._status_labels:
            self._status_labels["mode"].setText(f"AI {mode.upper()[:6]}")
        callback = self.on_mode_change
        if callback:
            callback(mode)

    @staticmethod
    def _nav_button_style(active: bool) -> str:
        if active:
            return (f"QPushButton {{ text-align: left; color: {C.PRI}; "
                    f"background: {C.PRI_GHO}; border: 1px solid {C.BORDER_B}; "
                    "padding: 0 8px; }")
        return (f"QPushButton {{ text-align: left; color: {C.TEXT_MED}; "
                f"background: transparent; border: 1px solid transparent; "
                "padding: 0 8px; }"
                f"QPushButton:hover {{ color: {C.PRI}; background: {C.PANEL2}; "
                f"border-color: {C.BORDER}; }}")

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))

    def _hud_strip_label(self, text: str, color: str = C.TEXT_MED) -> QLabel:
        label = QLabel(text)
        label.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        label.setStyleSheet(f"color: {color}; background: transparent; border: none;")
        return label

    def _build_hud_top_strip(self) -> QWidget:
        panel = QFrame()
        panel.setFixedHeight(34)
        panel.setStyleSheet(f"QFrame {{ background: {C.PANEL2}; border: 1px solid {C.BORDER_B}; }}")
        row = QHBoxLayout(panel)
        row.setContentsMargins(10, 3, 10, 3)
        row.setSpacing(12)
        row.addWidget(self._hud_strip_label("◉  DEVA AI CORE", C.GREEN))
        row.addWidget(self._hud_strip_label("●  LOCAL SESSION", C.TEXT_MED))
        row.addWidget(self._hud_strip_label("●  VOICE READY", C.TEXT_MED))
        row.addStretch()
        row.addWidget(self._hud_strip_label("NEURAL INTERFACE", C.PRI))
        return panel

    def _build_hud_bottom_strip(self) -> QWidget:
        panel = QFrame()
        panel.setFixedHeight(34)
        panel.setStyleSheet(f"QFrame {{ background: {C.PANEL2}; border: 1px solid {C.BORDER_B}; }}")
        row = QHBoxLayout(panel)
        row.setContentsMargins(10, 3, 10, 3)
        row.setSpacing(18)
        self._core_temp_lbl = self._hud_strip_label("CORE TEMP  --", C.ACC2)
        self._audio_level_lbl = self._hud_strip_label("AUDIO  READY", C.GREEN)
        self._hud_state_lbl = self._hud_strip_label("STATE  INITIALISING", C.PRI)
        self._mode_status_lbl = self._hud_strip_label("WORKSPACE  GENERAL", C.ACC2)
        row.addWidget(self._core_temp_lbl)
        row.addWidget(self._audio_level_lbl)
        row.addWidget(self._hud_state_lbl)
        row.addStretch()
        row.addWidget(self._mode_status_lbl)
        return panel

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setStyleSheet(f"background: rgba(3, 9, 17, 220); border-right: 1px solid {C.BORDER};")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        self._left_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: rgba(3, 9, 17, 180); border: none; }}
            QScrollBar:vertical {{ width: 5px; background: {C.DARK}; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER_B}; min-height: 24px; }}
        """)
        content = QWidget()
        content.setStyleSheet("background: rgba(3, 9, 17, 178);")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(8, 10, 8, 10)
        lay.setSpacing(5)

        def section_title(text: str) -> QLabel:
            label = QLabel(text)
            label.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            label.setStyleSheet(
                f"color: {C.PRI}; background: transparent; "
                f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
            return label

        lay.addWidget(section_title("◈  NAVIGATION"))
        nav_items = [
            ("home", "⌂", "HOME", "Return focus to the command center"),
            ("assistant", "◉", "AI CORE", "Focus the assistant command input"),
            ("voice", "◍", "VOICE", "Toggle voice and input controls"),
            ("automate", "⟳", "AUTOMATION", "Ask the assistant about automations"),
            ("files", "▣", "FILES", "Choose a file to ingest"),
            ("memory", "◌", "MEMORY", "Review stored assistant memory"),
            ("plugins", "◇", "TOOLS", "Open installed plugin controls"),
            ("system", "⌘", "SYSTEM", "Enter a system-control request"),
            ("settings", "⚙", "SETTINGS", "Open assistant settings"),
        ]
        self._nav_buttons = {}
        self._nav_order = []
        for key, icon, title, description in nav_items:
            button = QPushButton(f"{icon}   {title}")
            button.setFixedHeight(30)
            button.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setCheckable(True)
            button.setToolTip(description)
            button.setStyleSheet(self._nav_button_style(key == "home"))
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setMinimumWidth(32)
            button.setMaximumWidth(200)
            button.clicked.connect(lambda _=False, t=key: self._navigate(t))
            button.enterEvent = lambda event, b=button: self._dock_expand(b, event)
            button.leaveEvent = lambda event, b=button: self._dock_contract(b, event)
            lay.addWidget(button)
            self._nav_buttons[key] = button
            self._nav_order.append(button)

        lay.addSpacing(4)
        lay.addWidget(section_title("◈  AI WORKSPACE"))
        self._mode_buttons = {}
        self._active_mode = "General"
        for mode, icon in (
            ("General", "◉"), ("Developer", "</>"), ("Creative", "✧"),
            ("Automation", "⟳"), ("Learning", "▤"), ("Gaming", "◇"),
        ):
            button = QPushButton(f"{icon}   {mode.upper()}")
            button.setFixedHeight(25)
            button.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setCheckable(True)
            button.setToolTip(f"Select the {mode} workspace display preset")
            button.setStyleSheet(self._nav_button_style(mode == "General"))
            button.clicked.connect(lambda _=False, m=mode: self._activate_mode(m))
            lay.addWidget(button)
            self._mode_buttons[mode] = button

        lay.addSpacing(4)
        lay.addWidget(section_title("◈  SATELLITE & NETWORK"))
        lay.addWidget(_TacticalRadar())

        link_panel = QFrame()
        link_panel.setStyleSheet(f"""
            QFrame {{ background: {C.PANEL2}; border: 1px solid {C.BORDER};
                      border-radius: 4px; }}
            QLabel {{ background: transparent; border: none; }}
        """)
        link_lay = QVBoxLayout(link_panel)
        link_lay.setContentsMargins(8, 6, 8, 6)
        link_lay.setSpacing(3)
        link_title = QLabel("LOCAL SYSTEM TELEMETRY")
        link_title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        link_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        link_lay.addWidget(link_title)
        for text, color in (("PROCESS + RESOURCE SENSORS", C.TEXT_MED),
                            ("NETWORK COUNTERS  ·  LIVE", C.GREEN)):
            item = QLabel(f"●  {text}")
            item.setFont(QFont("Courier New", 7))
            item.setStyleSheet(f"color: {color}; background: transparent;")
            link_lay.addWidget(item)
        lay.addWidget(link_panel)
        lay.addSpacing(3)

        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("RAM", C.ACC2)
        self._bar_net = MetricBar("NET", C.GREEN)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_tmp = MetricBar("TMP", "#ff6688")
        self._bar_storage = MetricBar("STORAGE", C.ACC2)

        lay.addWidget(section_title("◈  LIVE SYSTEM METRICS"))
        for bar in [self._bar_cpu, self._bar_mem, self._bar_net,
                self._bar_gpu, self._bar_tmp, self._bar_storage]:
            lay.addWidget(bar)

        lay.addSpacing(4)

        info_panel = QWidget()
        info_panel.setStyleSheet(
            f"background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 4px;"
        )
        ip_lay = QVBoxLayout(info_panel)
        ip_lay.setContentsMargins(6, 5, 6, 5)
        ip_lay.setSpacing(3)

        self._uptime_lbl = QLabel("UP  --:--")
        self._uptime_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._uptime_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        ip_lay.addWidget(self._uptime_lbl)

        self._proc_lbl = QLabel("PROC  --")
        self._proc_lbl.setFont(QFont("Courier New", 8))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        ip_lay.addWidget(self._proc_lbl)

        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"OS  {os_name}")
        os_lbl.setFont(QFont("Courier New", 8))
        os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
        ip_lay.addWidget(os_lbl)

        lay.addWidget(info_panel)
        lay.addSpacing(4)

        state_row = QHBoxLayout()
        state_row.setSpacing(4)
        for txt, col in [
            ("CLIENT\nACTIVE", C.GREEN),
            ("LOCAL\nSESSION",  C.PRI),
            ("VERSION\n" + APP_PROTOCOL, C.TEXT_DIM),
        ]:
            lbl = QLabel(txt)
            lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"color: {col}; background: {C.PANEL2};"
                f"border: 1px solid {C.BORDER_A}; border-radius: 3px; padding: 4px;"
            )
            state_row.addWidget(lbl, stretch=1)
        lay.addLayout(state_row)

        scroll.setWidget(content)
        outer.addWidget(scroll)
        return w
    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_RIGHT_W)
        w.setStyleSheet(f"background: rgba(3, 9, 17, 220); border-left: 1px solid {C.BORDER};")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        self._right_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: rgba(3, 9, 17, 180); border: none; }}
            QScrollBar:vertical {{ width: 5px; background: {C.DARK}; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER_B}; min-height: 24px; }}
        """)
        content = QWidget()
        content.setStyleSheet("background: rgba(3, 9, 17, 178);")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        def _sec(txt):
            l = QLabel(f"▸ {txt}")
            l.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            l.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            return l

        network_panel = QFrame()
        self._network_panel = network_panel
        network_panel.setStyleSheet(f"""
            QFrame#networkPanel {{ background: {C.PANEL}; border: 1px solid {C.BORDER_B};
                                  border-radius: 5px; }}
            QLabel {{ background: transparent; border: none; }}
        """)
        network_panel.setObjectName("networkPanel")
        network_lay = QVBoxLayout(network_panel)
        network_lay.setContentsMargins(7, 7, 7, 7)
        network_lay.setSpacing(5)
        network_title = QLabel("◈  NETWORK INTELLIGENCE")
        network_title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        network_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        network_lay.addWidget(network_title)

        self._network_graph = _NetworkGraph()
        self._network_graph.node_activated.connect(self._show_network_node_details)
        network_lay.addWidget(self._network_graph)

        self._network_detail = QLabel("Select a node to inspect its live connection state.")
        self._network_detail.setWordWrap(True)
        self._network_detail.setMinimumHeight(27)
        self._network_detail.setFont(QFont("Courier New", 6))
        self._network_detail.setStyleSheet(f"color: {C.TEXT_MED}; background: {C.PANEL2}; border: 1px solid {C.BORDER}; padding: 4px;")
        network_lay.addWidget(self._network_detail)

        status_grid = QGridLayout()
        status_grid.setContentsMargins(0, 1, 0, 1)
        status_grid.setHorizontalSpacing(4)
        status_grid.setVerticalSpacing(2)
        self._network_status_labels = {}
        for row, key in enumerate(("INTERNET", "BACKEND", "WEBSOCKET", "AI API", "CLOUD")):
            title = QLabel(key)
            title.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
            title.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            value = QLabel("N/A")
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            value.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
            value.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            self._network_status_labels[key] = value
            status_grid.addWidget(title, row, 0)
            status_grid.addWidget(value, row, 1)
        network_lay.addLayout(status_grid)

        rates_grid = QGridLayout()
        rates_grid.setContentsMargins(0, 2, 0, 0)
        rates_grid.setHorizontalSpacing(5)
        self._download_value = QLabel("N/A")
        self._upload_value = QLabel("N/A")
        self._ping_value = QLabel("N/A")
        self._network_rate_labels = (
            ("DOWNLOAD", self._download_value, C.PRI),
            ("UPLOAD", self._upload_value, C.ACC2),
            ("PING / TCP RTT", self._ping_value, C.GREEN),
        )
        for col, (label, value, color) in enumerate(self._network_rate_labels):
            cell = QFrame()
            cell.setStyleSheet(f"QFrame {{ background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 3px; }} QLabel {{ background: transparent; border: none; }}")
            cell_lay = QVBoxLayout(cell)
            cell_lay.setContentsMargins(4, 4, 4, 4)
            cell_lay.setSpacing(2)
            cap = QLabel(label)
            cap.setFont(QFont("Courier New", 5, QFont.Weight.Bold))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            value.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            value.setStyleSheet(f"color: {color}; background: transparent;")
            cell_lay.addWidget(cap)
            cell_lay.addWidget(value)
            rates_grid.addWidget(cell, 0, col)
        network_lay.addLayout(rates_grid)

        traffic_title = QLabel("LIVE TRAFFIC  ·  DOWNLOAD / UPLOAD")
        traffic_title.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
        traffic_title.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        network_lay.addWidget(traffic_title)
        self._traffic_graph = _TrafficHistoryGraph()
        network_lay.addWidget(self._traffic_graph)

        self._network_warning = QLabel("")
        self._network_warning.setWordWrap(True)
        self._network_warning.setFont(QFont("Courier New", 6))
        self._network_warning.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        self._network_warning.hide()
        network_lay.addWidget(self._network_warning)
        lay.addWidget(network_panel)

        environment = QFrame()
        environment.setStyleSheet(f"""
            QFrame {{ background: {C.PANEL}; border: 1px solid {C.BORDER};
                      border-radius: 4px; }}
            QLabel {{ background: transparent; border: none; }}
        """)
        env_grid = QGridLayout(environment)
        env_grid.setContentsMargins(8, 5, 8, 5)
        env_grid.setHorizontalSpacing(8)
        weather = QLabel("CORE TEMP  --\nSENSOR UNAVAILABLE")
        weather.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        weather.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        self._weather_lbl = weather
        coords = QLabel("LOCATION\nNOT SHARED")
        coords.setFont(QFont("Courier New", 7))
        coords.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        env_grid.addWidget(weather, 0, 0)
        env_grid.addWidget(coords, 0, 1)
        env_grid.addWidget(_LocationMap(), 1, 0, 1, 2)
        lay.addWidget(environment)

        status_panel = QFrame()
        status_panel.setStyleSheet(f"""
            QFrame {{ background: {C.PANEL2}; border: 1px solid {C.BORDER_B};
                      border-radius: 4px; }}
            QLabel {{ background: transparent; border: none; }}
        """)
        status_grid = QGridLayout(status_panel)
        status_grid.setContentsMargins(8, 5, 8, 5)
        status_grid.setHorizontalSpacing(8)
        status_grid.setVerticalSpacing(2)
        status_title = QLabel("SYSTEM HEALTH")
        status_title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        status_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        status_grid.addWidget(status_title, 0, 0, 1, 2)
        self._system_status_labels = {}
        self._system_status_bars = {}
        for row, key in enumerate(("CPU", "RAM", "GPU", "STORAGE"), 1):
            label = QLabel(f"{key}  --")
            label.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            label.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(False)
            bar.setFixedHeight(5)
            bar.setStyleSheet(f"""
                QProgressBar {{ background: {C.BAR_BG}; border: none; }}
                QProgressBar::chunk {{ background: {C.PRI}; }}
            """)
            self._system_status_labels[key] = label
            self._system_status_bars[key] = bar
            status_grid.addWidget(label, row * 2 - 1, 0, 1, 2)
            status_grid.addWidget(bar, row * 2, 0, 1, 2)
        lay.addWidget(status_panel)

        lay.addWidget(_sec("LIVE EVENT STREAM"))
        self._log = LogWidget()
        self._log.setMinimumHeight(100)
        self._log.setMaximumHeight(240)
        lay.addWidget(self._log, stretch=1)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_sec("FILE INGESTION"))
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        self._file_hint = QLabel("No file loaded — drop or click above to upload")
        self._file_hint.setFont(QFont("Courier New", 7))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._file_hint.setWordWrap(True)
        lay.addWidget(self._file_hint)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_sec("COMMAND INPUT"))
        lay.addLayout(self._build_input_row())

        self._interrupt_btn = QPushButton("✋  INTERRUPT  [ESC]")
        self._interrupt_btn.setFixedHeight(34)
        self._interrupt_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._interrupt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._interrupt_btn.setStyleSheet(f"""
            QPushButton {{
                background: #140008; color: {C.MUTED_C};
                border: 1px solid {C.MUTED_C}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: #200010; border: 1px solid #ff6688;
            }}
            QPushButton:pressed {{
                background: #300018;
            }}
        """)
        self._interrupt_btn.clicked.connect(self._do_interrupt)
        lay.addWidget(self._interrupt_btn)

        self._mute_btn = QPushButton("🎙  MICROPHONE ACTIVE")
        self._mute_btn.setFixedHeight(30)
        self._mute_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        lay.addWidget(self._mute_btn)

        scroll.setWidget(content)
        outer.addWidget(scroll)
        return w

    def _build_quick_drawer(self) -> QWidget:
        """Floating overlay panel shown when the ⚙ header button is toggled."""
        _BTN_STYLE_PRI = f"""
            QPushButton {{
                background: #00091a; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """
        _BTN_STYLE_DIM = f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}
        """

        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(f"""
            QWidget#QuickDrawer {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-top: none;
                border-radius: 0 0 6px 6px;
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("◈ CONTROLS")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        status_panel = QFrame()
        status_panel.setStyleSheet(f"""
            QFrame {{ background: {C.PANEL2}; border: 1px solid {C.BORDER};
                      border-radius: 3px; }}
            QLabel {{ color: {C.TEXT_MED}; background: transparent; border: none; }}
        """)
        status_grid = QGridLayout(status_panel)
        status_grid.setContentsMargins(7, 5, 7, 5)
        status_grid.setHorizontalSpacing(10)
        status_grid.setVerticalSpacing(2)
        status_title = QLabel("SYSTEM SNAPSHOT")
        status_title.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        status_title.setStyleSheet(f"color: {C.PRI}; background: transparent; border: none;")
        status_grid.addWidget(status_title, 0, 0, 1, 2)
        self._drawer_status_labels = {}
        for index, key in enumerate(("CPU", "RAM", "NET", "UP")):
            label = QLabel(f"{key} --")
            label.setFont(QFont("Courier New", 7))
            self._drawer_status_labels[key] = label
            status_grid.addWidget(label, 1 + index // 2, index % 2)
        lay.addWidget(status_panel)

        remote_btn = QPushButton("◉  REMOTE CONTROL")
        remote_btn.setFixedHeight(30)
        remote_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        remote_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remote_btn.setStyleSheet(_BTN_STYLE_PRI)
        remote_btn.clicked.connect(self._open_remote)
        lay.addWidget(remote_btn)

        fs_btn = QPushButton("⛶  FULLSCREEN  [F11]")
        fs_btn.setFixedHeight(26)
        fs_btn.setFont(QFont("Courier New", 7))
        fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fs_btn.setStyleSheet(_BTN_STYLE_DIM)
        fs_btn.clicked.connect(self._toggle_fullscreen)
        lay.addWidget(fs_btn)

        sc_btn = QPushButton("⊞  CREATE DESKTOP SHORTCUT")
        sc_btn.setFixedHeight(26)
        sc_btn.setFont(QFont("Courier New", 7))
        sc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sc_btn.setStyleSheet(_BTN_STYLE_DIM)
        sc_btn.clicked.connect(self._create_desktop_shortcut)
        lay.addWidget(sc_btn)

        self._autostart_btn = QPushButton("◉  AUTO-START: OFF")
        self._autostart_btn.setFixedHeight(26)
        self._autostart_btn.setFont(QFont("Courier New", 7))
        self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autostart_btn.clicked.connect(self._toggle_autostart)
        lay.addWidget(self._autostart_btn)

        cust_btn = QPushButton("⚙  CUSTOMISE ASSISTANT")
        cust_btn.setFixedHeight(26)
        cust_btn.setFont(QFont("Courier New", 7))
        cust_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cust_btn.setStyleSheet(_BTN_STYLE_DIM)
        cust_btn.clicked.connect(self._open_customize)
        lay.addWidget(cust_btn)

        self._brief_btn = QPushButton()
        self._brief_btn.setFixedHeight(26)
        self._brief_btn.setFont(QFont("Courier New", 7))
        self._brief_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._brief_btn.clicked.connect(self._toggle_brief)
        lay.addWidget(self._brief_btn)

        # ── Wake word ──────────────────────────────────────────────────────────
        self._wake_btn = QPushButton()
        self._wake_btn.setFixedHeight(26)
        self._wake_btn.setFont(QFont("Courier New", 7))
        self._wake_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wake_btn.clicked.connect(self._toggle_wake_word)
        lay.addWidget(self._wake_btn)

        self._wake_sleep_btn = QPushButton()
        self._wake_sleep_btn.setFixedHeight(26)
        self._wake_sleep_btn.setFont(QFont("Courier New", 7))
        self._wake_sleep_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wake_sleep_btn.clicked.connect(self._tap_wake_manual)
        lay.addWidget(self._wake_sleep_btn)
        # Neutral placeholder now; the real state (which may load the model to
        # check readiness) is resolved lazily the first time the drawer opens.
        self._wake_btn.setText("🎙  WAKE WORD")
        self._wake_btn.setStyleSheet(_BTN_STYLE_DIM)
        self._wake_sleep_btn.hide()

        self._ptt_btn = QPushButton()
        self._ptt_btn.setFixedHeight(26)
        self._ptt_btn.setFont(QFont("Courier New", 7))
        self._ptt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ptt_btn.clicked.connect(self._toggle_ptt)
        lay.addWidget(self._ptt_btn)

        self._refresh_talk_btns()

        self._hud_btn = QPushButton()
        self._hud_btn.setFixedHeight(26)
        self._hud_btn.setFont(QFont("Courier New", 7))
        self._hud_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hud_btn.clicked.connect(self._toggle_hud_style)
        lay.addWidget(self._hud_btn)
        self._refresh_hud_btn()

        audio_btn = QPushButton("🎧  AUDIO DEVICES")
        audio_btn.setFixedHeight(26)
        audio_btn.setFont(QFont("Courier New", 7))
        audio_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        audio_btn.setStyleSheet(_BTN_STYLE_DIM)
        audio_btn.clicked.connect(self._open_audio_devices)
        lay.addWidget(audio_btn)

        mem_btn = QPushButton("🧠  MEMORY")
        mem_btn.setFixedHeight(26)
        mem_btn.setFont(QFont("Courier New", 7))
        mem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mem_btn.setStyleSheet(_BTN_STYLE_DIM)
        mem_btn.clicked.connect(self._open_memory_panel)
        lay.addWidget(mem_btn)

        plugin_btn = QPushButton("🧩  PLUGINS")
        plugin_btn.setFixedHeight(26)
        plugin_btn.setFont(QFont("Courier New", 7))
        plugin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        plugin_btn.setStyleSheet(_BTN_STYLE_DIM)
        plugin_btn.clicked.connect(self._open_plugin_manager)
        lay.addWidget(plugin_btn)

        settings_btn = QPushButton("⚙  PLUGIN SETTINGS")
        settings_btn.setFixedHeight(26)
        settings_btn.setFont(QFont("Courier New", 7))
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.setStyleSheet(_BTN_STYLE_DIM)
        settings_btn.clicked.connect(self._open_plugin_settings)
        lay.addWidget(settings_btn)

        w.adjustSize()
        return w

    def _toggle_drawer(self, checked: bool):
        if checked:
            self._refresh_wake_btns()   # resolve wake state on open (lazy)
            self._position_quick_drawer()
            self._quick_drawer.show()
            self._quick_drawer.raise_()
        else:
            self._quick_drawer.hide()

    def _position_quick_drawer(self):
        if not hasattr(self, '_quick_drawer'):
            return
        _W = 220
        self._quick_drawer.setFixedWidth(_W)
        self._quick_drawer.adjustSize()
        self._quick_drawer.setGeometry(12, 54, _W, self._quick_drawer.sizeHint().height())

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(5)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command or question…")
        self._input.setFont(QFont("Courier New", 9))
        self._input.setFixedHeight(30)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d14; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 7px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        send = QPushButton("▸")
        send.setFixedSize(30, 30)
        send.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def _build_content_panel(self) -> QWidget:
        """
        Collapsible panel below the HUD — shows search results, news, briefings.
        Hidden by default; appears when show_content() is called.
        """
        w = QWidget()
        w.setObjectName("ContentPanel")
        w.setStyleSheet(f"""
            QWidget#ContentPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(5)

        # ── header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(6)

        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._content_title_lbl = QLabel("BRIEFING")
        self._content_title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._content_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;"
        )
        hdr.addWidget(self._content_title_lbl)
        hdr.addStretch()

        self._content_ts_lbl = QLabel("")
        self._content_ts_lbl.setFont(QFont("Courier New", 7))
        self._content_ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._content_ts_lbl)

        dismiss = QPushButton("DISMISS  ✕")
        dismiss.setFont(QFont("Courier New", 7))
        dismiss.setFixedHeight(18)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        dismiss.clicked.connect(w.hide)
        hdr.addWidget(dismiss)
        lay.addLayout(hdr)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); lay.addWidget(sep)

        # ── text display ──────────────────────────────────────────────────────
        self._content_display = QTextEdit()
        self._content_display.setReadOnly(True)
        self._content_display.setFont(QFont("Courier New", 8))
        self._content_display.setMinimumHeight(60)
        self._content_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._content_display.setStyleSheet(f"""
            QTextEdit {{
                background: {C.DARK};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 3px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none;
            }}
        """)
        lay.addWidget(self._content_display)

        return w

    def _show_content(self, title: str, text: str):
        """Slot — runs on Qt main thread. Updates and shows the content panel."""
        import time as _time
        # The panel opens below the head, so the head looks down at it. It is a
        # tiny thing that answers "did that land?" before you read a word.
        self.hud.glance(0.0, -0.85, hold=1.3)
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setPlainText(text)
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start
        )
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 220, 120), 220])

    # ── document review ──────────────────────────────────────────────────────
    # Rendered as rich text into the content panel that already exists, rather
    # than into a panel of its own. A review is read, not clicked, so QTextEdit
    # gives scrolling, selection and copy for nothing, and the HUD gains no
    # widget it has to lay out. Severity decides colour and order here because
    # that is presentation; the plugin supplies no styling and knows no palette,
    # which is also what lets a re-theme repaint a review correctly.

    # Severity is marked by a symbol and a colour, not by a word. The findings
    # themselves are in the user's language, and "[SERIOUS]" sitting inside a
    # Turkish sentence is the kind of seam this project tries not to have —
    # while translating the tag would mean a table per language, which is worse.
    # A shape carries it in every language, and shape plus colour still reads
    # for someone who cannot separate red from amber. What the marks mean
    # arrives the way everything else does: JARVIS says it out loud.
    _REVIEW_MARKS = {"serious": ("RED", "▲"), "caution": ("ACC2", "●"), "note": ("PRI_DIM", "·")}

    @staticmethod
    def _esc(s) -> str:
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br>"))

    def _show_review(self, title: str, summary: str, findings, unclear):
        """Slot — Qt main thread. Lays a document review into the content panel."""
        e = self._esc
        parts = [f'<div style="color:{C.TEXT}; font-family:Courier New;">']

        if summary:
            parts.append(
                f'<div style="color:{C.WHITE}; border-left:2px solid {C.PRI};'
                f' padding-left:8px; margin-bottom:10px;">{e(summary)}</div>')

        for f in (findings or []):
            key, mark = self._REVIEW_MARKS.get(f.get("severity"), ("PRI_DIM", "·"))
            colour = getattr(C, key)
            parts.append(f'<div style="margin-bottom:11px;">')
            parts.append(
                f'<span style="color:{colour}; font-weight:bold;">{mark}</span> '
                f'<span style="color:{C.WHITE}; font-weight:bold;">'
                f'{e(f.get("heading"))}</span>')
            if f.get("detail"):
                parts.append(f'<div style="margin-left:12px;">{e(f["detail"])}</div>')
            if f.get("quote"):
                # The document's own wording, visually separated from the
                # explanation so the two are never mistaken for each other.
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_DIM};'
                    f' border-left:1px solid {C.BORDER}; padding-left:7px;">'
                    f'&ldquo;{e(f["quote"])}&rdquo;</div>')
            if f.get("suggestion"):
                parts.append(
                    f'<div style="margin-left:12px; color:{C.PRI};">'
                    f'&rarr; {e(f["suggestion"])}</div>')
            parts.append('</div>')

        if unclear:
            parts.append(
                f'<div style="margin-top:6px; border-top:1px solid {C.BORDER};'
                f' padding-top:7px; color:{C.TEXT_MED};">'
                'The document does not settle:</div>')
            for u in unclear:
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_MED};">'
                    f'&middot; {e(u)}</div>')
        parts.append('</div>')

        import time as _time
        self.hud.glance(0.0, -0.85, hold=1.3)
        # Left as written, not upper-cased. The other content-panel titles are
        # the app's own English labels, but this one is the document's name in
        # the user's language, and str.upper() applies English casing rules to
        # it: Turkish "Sözleşmesi" comes back "SÖZLEŞMESI", having lost the
        # dotted capital İ. Python has no locale-aware upper to reach for, and
        # imposing one language's rules on all of them is the bug, not the fix.
        self._content_title_lbl.setText((title or "Document")[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setHtml("".join(parts))
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start)
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 260, 120), 260, 0])

    # ── quiz panel ───────────────────────────────────────────────────────────
    # An interactive twin of the content panel. The plugin only ever hands over
    # questions; everything about asking, marking and reporting happens here,
    # and the finished result is pushed back into the conversation the same way
    # a dropped file is — as a message JARVIS reads and responds to. That keeps
    # the tool call short (it returns the moment the board is up) and leaves the
    # talking to the assistant, in the user's own language.

    def _quiz_btn(self, text: str, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setFont(QFont("Courier New", 8))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setMinimumHeight(24)
        edge = C.BORDER_B if primary else C.BORDER
        col = C.PRI if primary else C.TEXT_MED
        b.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL2}; color: {col};
                border: 1px solid {edge}; border-radius: 2px;
                padding: 3px 9px; text-align: left;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border-color: {C.PRI_DIM}; }}
            QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
        """)
        return b

    def _build_quiz_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("QuizPanel")
        w.setStyleSheet(f"""
            QWidget#QuizPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout(); hdr.setSpacing(6)
        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._quiz_title_lbl = QLabel("QUIZ")
        self._quiz_title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._quiz_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        hdr.addWidget(self._quiz_title_lbl)
        hdr.addStretch()

        self._quiz_count_lbl = QLabel("")
        self._quiz_count_lbl.setFont(QFont("Courier New", 7))
        self._quiz_count_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._quiz_count_lbl)

        quit_btn = QPushButton("DISMISS  ✕")
        quit_btn.setFont(QFont("Courier New", 7))
        quit_btn.setFixedHeight(18)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        quit_btn.clicked.connect(self._hide_quiz)
        hdr.addWidget(quit_btn)
        lay.addLayout(hdr)

        rule = QFrame(); rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {C.BORDER};")
        lay.addWidget(rule)

        self._quiz_q_lbl = QLabel("")
        self._quiz_q_lbl.setWordWrap(True)
        self._quiz_q_lbl.setFont(QFont("Courier New", 9))
        self._quiz_q_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._quiz_q_lbl)

        self._quiz_answers = QWidget()
        self._quiz_answers.setStyleSheet("background: transparent;")
        self._quiz_answers_lay = QVBoxLayout(self._quiz_answers)
        self._quiz_answers_lay.setContentsMargins(0, 2, 0, 0)
        self._quiz_answers_lay.setSpacing(4)
        lay.addWidget(self._quiz_answers)

        self._quiz_note_lbl = QLabel("")
        self._quiz_note_lbl.setWordWrap(True)
        self._quiz_note_lbl.setFont(QFont("Courier New", 8))
        self._quiz_note_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._quiz_note_lbl.hide()
        lay.addWidget(self._quiz_note_lbl)

        foot = QHBoxLayout()
        foot.addStretch()
        self._quiz_next_btn = self._quiz_btn("NEXT  →", primary=True)
        self._quiz_next_btn.setFixedWidth(110)
        self._quiz_next_btn.clicked.connect(self._quiz_next)
        self._quiz_next_btn.hide()
        foot.addWidget(self._quiz_next_btn)
        lay.addLayout(foot)

        self._quiz = None
        return w

    def _show_quiz(self, topic: str, questions, grader=None):
        """Slot — Qt main thread. Puts a fresh quiz on the board."""
        if not questions:
            return
        self._quiz = {
            "topic": topic or "",
            "questions": list(questions),
            "grader": grader,
            "i": 0,
            "results": [],
            "answered": False,
        }
        self._quiz_title_lbl.setText((topic or "quiz").upper()[:48])
        self.hud.glance(0.0, -0.85, hold=1.3)
        first_show = not self._quiz_panel.isVisible()
        self._quiz_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 250, 120), 0, 250])
        self._quiz_render()

    def _hide_quiz(self):
        self._quiz = None
        self._quiz_panel.hide()

    def _quiz_clear_answers(self):
        while self._quiz_answers_lay.count():
            item = self._quiz_answers_lay.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
                child.deleteLater()

    def _quiz_render(self):
        q = self._quiz["questions"][self._quiz["i"]]
        n, total = self._quiz["i"] + 1, len(self._quiz["questions"])
        self._quiz_count_lbl.setText(f"{n} / {total}")
        self._quiz_q_lbl.setText(q.get("question", ""))
        self._quiz_note_lbl.hide()
        self._quiz_next_btn.hide()
        self._quiz["answered"] = False
        self._quiz_clear_answers()

        opts = q.get("options") or []
        if opts:
            for text in opts:
                b = self._quiz_btn("   " + text)
                b.clicked.connect(lambda _=False, t=text: self._quiz_submit(t))
                self._quiz_answers_lay.addWidget(b)
        else:
            row = QWidget(); row.setStyleSheet("background: transparent;")
            h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
            field = QLineEdit()
            field.setFont(QFont("Courier New", 9))
            field.setPlaceholderText("your answer")
            field.setStyleSheet(f"""
                QLineEdit {{
                    background: {C.PANEL2}; color: {C.WHITE};
                    border: 1px solid {C.BORDER}; border-radius: 2px; padding: 4px 7px;
                }}
                QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}
            """)
            send = self._quiz_btn("ANSWER", primary=True)
            send.setFixedWidth(90)
            field.returnPressed.connect(lambda: self._quiz_submit(field.text()))
            send.clicked.connect(lambda: self._quiz_submit(field.text()))
            h.addWidget(field, stretch=1)
            h.addWidget(send)
            self._quiz_answers_lay.addWidget(row)
            field.setFocus()

    def _quiz_submit(self, given: str):
        if self._quiz is None or self._quiz["answered"]:
            return
        self._quiz["answered"] = True
        q = self._quiz["questions"][self._quiz["i"]]
        grader = self._quiz.get("grader")
        verdict = None
        if callable(grader):
            try:
                verdict = grader(q, given)
            except Exception:
                verdict = None
        self._quiz["results"].append({
            "question": q.get("question", ""),
            "type": q.get("type", ""),
            "given": str(given or "").strip(),
            "answer": q.get("answer", ""),
            "correct": verdict,
        })

        for i in range(self._quiz_answers_lay.count()):
            wdg = self._quiz_answers_lay.itemAt(i).widget()
            if wdg is not None:
                wdg.setEnabled(False)

        if verdict is True:
            mark, colour = "✓  correct", C.GREEN
        elif verdict is False:
            mark, colour = "✕  " + str(q.get("answer", "")), C.RED
        else:
            # Open answers and near-miss gap-fills are JARVIS's to judge. Saying
            # so is honest; marking it wrong here would be a guess.
            mark, colour = "…  noted — I'll go over this one with you", C.ACC2
        note = q.get("note") or ""
        self._quiz_note_lbl.setText(mark + (("\n" + note) if note else ""))
        self._quiz_note_lbl.setStyleSheet(f"color: {colour}; background: transparent;")
        self._quiz_note_lbl.show()

        last = self._quiz["i"] >= len(self._quiz["questions"]) - 1
        self._quiz_next_btn.setText("FINISH  →" if last else "NEXT  →")
        self._quiz_next_btn.show()
        self._quiz_next_btn.setFocus()

    def _quiz_next(self):
        if self._quiz is None:
            return
        if self._quiz["i"] >= len(self._quiz["questions"]) - 1:
            self._quiz_finish()
        else:
            self._quiz["i"] += 1
            self._quiz_render()

    def _quiz_finish(self):
        if self._quiz is None:
            return
        topic = self._quiz["topic"]
        results = self._quiz["results"]
        right = sum(1 for r in results if r["correct"] is True)
        unsure = sum(1 for r in results if r["correct"] is None)
        total = len(results)
        self._quiz_panel.hide()
        self._quiz = None

        self._log.append_log(f"QUIZ: {topic or 'quiz'} — {right}/{total} correct")

        # Hand it back to JARVIS as a message, not as a tool return: the tool
        # call ended minutes ago. This is the same channel a dropped file uses.
        lines = [f"[QUIZ_DONE] topic={topic or 'general'} | "
                 f"auto-marked {right}/{total} correct"
                 + (f", {unsure} still need your marking" if unsure else "")]
        for i, r in enumerate(results, 1):
            state = ("correct" if r["correct"] is True
                     else "wrong" if r["correct"] is False else "NEEDS MARKING")
            lines.append(
                f"{i}. [{r['type']}] {r['question']} | they answered: "
                f"{r['given'] or '(blank)'} | expected: {r['answer']} | {state}")
        lines.append(
            "Mark every question flagged NEEDS MARKING yourself — accept an answer "
            "that means the same thing. Then tell them how they did in their own "
            "language: the score, what they got wrong and why, in a couple of "
            "sentences. Offer another round only if it fits. "
            "Remember something only if it would still matter next week — that they "
            "are working through a subject, or keep missing the same thing. A score "
            "from one session is not worth a memory, and a memory per quiz would "
            "bury the things that are.")
        msg = "\n".join(lines)
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(72)
        w.setStyleSheet(f"background: {C.DARK}; border-top: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w); lay.setContentsMargins(10, 5, 10, 5); lay.setSpacing(4)

        dock = QHBoxLayout(); dock.setSpacing(4)
        dock_items = [
            ("⌕", "SEARCH WEB", lambda: self._quick_action_prompt(
                "Search the web for: ")),
            ("✎", "WRITE EMAIL", lambda: self._send_dock_command(
                "Help me draft an email.")),
            ("▤", "SUMMARIZE", lambda: self._send_dock_command(
                "Summarize our current conversation.")),
            ("▶", "YOUTUBE", lambda: self._send_dock_command("Open YouTube.")),
            ("⌖", "SCREENSHOT", lambda: self._send_dock_command(
                "Take a screenshot and describe what is visible.")),
            ("◉", "LIGHTS", lambda: self._send_dock_command(
                "Help me control my lights.")),
            ("⌘", "OPEN VS CODE", lambda: self._send_dock_command(
                "Open Visual Studio Code.")),
            ("▣", "FILES", self._drop_zone._browse),
            ("◇", "PLUGINS", self._open_plugin_manager),
        ]
        for symbol, label, callback in dock_items:
            btn = QPushButton(f"{symbol}\n{label}")
            btn.setMinimumWidth(66)
            btn.setFixedHeight(42)
            btn.setFont(QFont(_HUD_ICON_FONT, 7, QFont.Weight.Bold))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(label.title())
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2}; color: {C.TEXT_MED};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    padding: 2px 8px; line-height: 12px;
                }}
                QPushButton:hover {{
                    background: {C.PRI_GHO}; color: {C.PRI};
                    border-color: {C.PRI_DIM};
                }}
                QPushButton:pressed {{ background: {C.PANEL}; }}
            """)
            btn.clicked.connect(callback)
            dock.addWidget(btn, stretch=1)
        lay.addLayout(dock)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(txt); l.setFont(QFont("Courier New", 7))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        status = QHBoxLayout()
        status.addWidget(_fl("[F4] MUTE  ·  [F11] FULLSCREEN"))
        status.addStretch()
        status.addWidget(_fl("LOCAL DESKTOP  //  " + APP_PROTOCOL, C.PRI_DIM))
        lay.addLayout(status)
        return w

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"{icon}  {p.name}  ·  {size}  ·  Tell {self._assistant_name} what to do with it")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw  = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov  = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                               expiry_secs=600, parent=cw)
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — manual: {manual or url}")

    # ── Auto-start ──────────────────────────────────────────────────────────────

    def _check_autostart(self) -> bool:
        """Returns True if auto-start is currently registered on this OS."""
        try:
            if _OS == "Windows":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(key, "JARVIS_AI")
                    return True
                except FileNotFoundError:
                    return False
                finally:
                    winreg.CloseKey(key)
            elif _OS == "Darwin":
                return (Path.home() / "Library" / "LaunchAgents"
                        / "com.jarvis.assistant.plist").exists()
            else:
                return (Path.home() / ".config" / "autostart" / "jarvis.desktop").exists()
        except Exception:
            return False

    def _toggle_autostart(self):
        currently_on = self._check_autostart()
        try:
            script = str(Path(__file__).resolve().parent / "main.py")
            if _OS == "Windows":
                import winreg
                reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
                if currently_on:
                    winreg.DeleteValue(reg, "JARVIS_AI")
                else:
                    pythonw = Path(sys.executable).parent / "pythonw.exe"
                    exe = str(pythonw if pythonw.exists() else sys.executable)
                    winreg.SetValueEx(reg, "JARVIS_AI", 0, winreg.REG_SZ,
                                      f'"{exe}" "{script}"')
                winreg.CloseKey(reg)
            elif _OS == "Darwin":
                plist_dir = Path.home() / "Library" / "LaunchAgents"
                plist_dir.mkdir(parents=True, exist_ok=True)
                plist = plist_dir / "com.jarvis.assistant.plist"
                if currently_on:
                    plist.unlink(missing_ok=True)
                else:
                    plist.write_text(
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                        '<plist version="1.0"><dict>\n'
                        '  <key>Label</key><string>com.jarvis.assistant</string>\n'
                        '  <key>ProgramArguments</key><array>\n'
                        f'    <string>{sys.executable}</string>\n'
                        f'    <string>{script}</string>\n'
                        '  </array>\n'
                        '  <key>RunAtLoad</key><true/>\n'
                        '</dict></plist>\n'
                    )
            else:
                desk_dir = Path.home() / ".config" / "autostart"
                desk_dir.mkdir(parents=True, exist_ok=True)
                desk = desk_dir / "jarvis.desktop"
                if currently_on:
                    desk.unlink(missing_ok=True)
                else:
                    desk.write_text(
                        "[Desktop Entry]\n"
                        f"Name={self._assistant_name}\n"
                        f"Exec={sys.executable} {script}\n"
                        "Type=Application\nTerminal=false\n"
                        "X-GNOME-Autostart-enabled=true\n"
                    )
            enabled = not currently_on
            self._update_autostart_btn(enabled)
            self._log.append_log(
                f"SYS: Auto-start {'enabled' if enabled else 'disabled'}.")
        except Exception as e:
            self._log.append_log(f"ERR: Auto-start failed — {e}")

    def _update_autostart_btn(self, enabled: bool):
        if not hasattr(self, '_autostart_btn'):
            return
        if enabled:
            self._autostart_btn.setText("◉  AUTO-START: ON")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._autostart_btn.setText("◉  AUTO-START: OFF")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _toggle_brief(self):
        from memory.config_manager import get_brief_enabled, save_brief_enabled
        new_val = not get_brief_enabled()
        save_brief_enabled(new_val)
        self._update_brief_btn(new_val)

    # ── Wake word settings ───────────────────────────────────────────────────

    def _wake_state(self) -> dict:
        """Combined state for the two wake-word buttons. Readiness is a cheap,
        deterministic on-disk check now (see core.wake_word.is_ready), so there
        is nothing to cache — the button never flickers to a stale value."""
        if self.wake_get_state:
            try:
                s = self.wake_get_state()
                return {"ready": bool(s.get("ready")),
                        "enabled": bool(s.get("enabled")),
                        "awake": bool(s.get("awake"))}
            except Exception:
                pass
        # Before JarvisLive has wired its callback (drawer built at startup).
        ready, enabled = False, False
        try:
            from core.wake_word import is_ready
            from memory.config_manager import get_wake_word_enabled
            ready, enabled = is_ready(), get_wake_word_enabled()
        except Exception:
            pass
        return {"ready": ready, "enabled": enabled, "awake": True}

    def _refresh_wake_btns(self):
        if not hasattr(self, '_wake_btn'):
            return
        st = self._wake_state()
        _on = f"""
            QPushButton {{ background: #001a08; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: #002010; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""
        self._wake_btn.setEnabled(True)
        if not st["ready"]:
            self._wake_btn.setText("⬇  WAKE WORD: DOWNLOAD")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()
        elif st["enabled"]:
            self._wake_btn.setText("🎙  WAKE WORD: ON")
            self._wake_btn.setStyleSheet(_on)
            self._wake_sleep_btn.show()
            self._wake_sleep_btn.setText("😴  SLEEP NOW" if st["awake"] else "👂  WAKE NOW")
            self._wake_sleep_btn.setStyleSheet(_off)
        else:
            self._wake_btn.setText("🎙  WAKE WORD: OFF")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()

    def _refresh_talk_btns(self):
        """Repaint the push-to-talk row from the saved setting."""
        if not hasattr(self, "_ptt_btn"):
            return
        from core.hotkey import chord_label
        from memory.config_manager import get_push_to_talk_enabled
        _on = f"""
            QPushButton {{ background: #001a08; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: #002010; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""

        ptt = get_push_to_talk_enabled()
        self._ptt_btn.setText(f"🎚  PUSH-TO-TALK: {chord_label()}" if ptt
                              else "🎚  PUSH-TO-TALK: OFF")
        self._ptt_btn.setStyleSheet(_on if ptt else _off)
        self._ptt_btn.setToolTip(
            "Microphone stays closed until you hold the key — nothing is sent "
            "while you are not holding it." if ptt
            else "Hold a key to talk instead of streaming the mic continuously.")


    def _refresh_hud_btn(self):
        from memory.config_manager import get_hud_style
        face = get_hud_style() == "face"
        # Neither state is "off", so both read as active — this is a choice
        # between two things, not a switch with a disabled side.
        style = f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.BORDER_A}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.WHITE}; border: 1px solid {C.BORDER_B}; }}"""
        self._hud_btn.setText("🧑  HUD: ANIMATED FACE" if face
                              else "◉  HUD: REACTOR CORE")
        self._hud_btn.setStyleSheet(style)
        self._hud_btn.setToolTip(
            "An animated head that speaks your words and shows what JARVIS is "
            "doing. Tap to switch to the reactor core."
            if face else
            "A reactor core that turns with the state and moves with your voice. "
            "Tap to switch to the animated head.")

    def _toggle_hud_style(self):
        """Swap the centrepiece. Both objects stay in memory, so the change is
        instant and switching back costs nothing."""
        from memory.config_manager import get_hud_style, save_hud_style
        want = "core" if get_hud_style() == "face" else "face"
        save_hud_style(want)
        try:
            self.hud.hud_style = want
            self.hud.update()
        except Exception:
            pass
        self._refresh_hud_btn()
        self._log.append_log(
            "SYS: HUD switched to the animated face." if want == "face"
            else "SYS: HUD switched to the reactor core.")

    def _toggle_ptt(self):
        from memory.config_manager import (get_push_to_talk_enabled,
                                           save_push_to_talk_enabled)
        want = not get_push_to_talk_enabled()
        save_push_to_talk_enabled(want)
        scope = None
        if self.on_push_to_talk:
            try:
                scope = self.on_push_to_talk(want)
            except Exception as e:
                self._log.append_log(f"ERR: Push-to-talk failed — {e}")
                save_push_to_talk_enabled(False)
                want = False
        self._apply_ptt_shortcut(want and scope != "global")
        self._refresh_talk_btns()

    def _apply_ptt_shortcut(self, needed: bool):
        """Bind the chord inside the window when no global hook is available.

        On macOS and Linux there is no dependency-free way to read global key
        state, so the chord is at least live whenever this window has focus.
        Qt gives no key-release for a QShortcut, so a press latches the mic open
        and a short timer closes it; held down, auto-repeat keeps pushing that
        timer out, which behaves like holding a key.
        """
        from PyQt6.QtGui import QKeySequence, QShortcut
        from core.hotkey import qt_sequence

        if not needed:
            sc = getattr(self, "_ptt_sc", None)
            if sc is not None:
                sc.setEnabled(False)
                self._ptt_sc = None
            self._ptt_hold(False)
            return
        if getattr(self, "_ptt_sc", None) is not None:
            return

        self._ptt_release = QTimer(self)
        self._ptt_release.setSingleShot(True)
        self._ptt_release.setInterval(420)
        self._ptt_release.timeout.connect(lambda: self._ptt_hold(False))

        def _press():
            self._ptt_hold(True)
            self._ptt_release.start()

        self._ptt_sc = QShortcut(QKeySequence(qt_sequence()), self)
        self._ptt_sc.setAutoRepeat(True)
        self._ptt_sc.activated.connect(_press)

    def _ptt_hold(self, held: bool):
        """Report a windowed press/release to whoever owns the microphone."""
        cb = getattr(self, "ptt_hold", None)
        if cb:
            try:
                cb(bool(held))
            except Exception:
                pass

    def _toggle_wake_word(self):
        st = self._wake_state()
        if not st["ready"]:
            # First time: download openwakeword + model in a worker thread.
            self._wake_btn.setText("⬇  DOWNLOADING… (one-time)")
            self._wake_btn.setEnabled(False)
            def _work():
                try:
                    from core.wake_word import install_and_download
                    ok, msg = install_and_download(
                        logger=lambda m: self._log_sig.emit(f"SYS: {m}"))
                except Exception as e:
                    ok, msg = False, str(e)
                if ok and self.on_wake_toggle:
                    try:
                        self.on_wake_toggle(True)   # auto-enable after a successful download
                    except Exception:
                        pass
                self._wake_dl_sig.emit(ok, msg)
            threading.Thread(target=_work, daemon=True).start()
            return
        # Already downloaded → just flip enabled/disabled through JarvisLive.
        if self.on_wake_toggle:
            try:
                self.on_wake_toggle(not st["enabled"])
            except Exception:
                pass
        self._refresh_wake_btns()

    def _on_wake_install_done(self, ok: bool, msg: str):
        self._log_sig.emit(f"SYS: {'Wake word ready.' if ok else 'Wake word setup failed: ' + msg}")
        self._refresh_wake_btns()

    def _tap_wake_manual(self):
        if self.on_wake_manual:
            try:
                self.on_wake_manual()
            except Exception:
                pass
        self._refresh_wake_btns()

    def _update_brief_btn(self, enabled: bool):
        if not hasattr(self, '_brief_btn'):
            return
        if enabled:
            self._brief_btn.setText("☀  MORNING BRIEF: ON")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._brief_btn.setText("☀  MORNING BRIEF: OFF")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ── Customization ────────────────────────────────────────────────────────────

    def _open_customize(self):
        cfg = _read_full_config()
        if self._customize_overlay:
            self._customize_overlay.hide()
        cw = self.centralWidget()
        ov = CustomizeOverlay(
            cfg.get("assistant_name", "JARVIS") or "JARVIS",
            cfg.get("user_name", ""),
            cfg.get("ui_color", "") or DEFAULT_UI_COLOR,
            cfg.get("voice_name", ""),
            parent=cw,
        )
        ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
        oh = min(oh, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.on_preview = self._preview_ui_color
        ov.saved.connect(self._apply_name_update)
        ov.show()
        self._customize_overlay = ov

    def _preview_ui_color(self, hex_color: str):
        """Live preview — paints the whole interface the new colour (does NOT write to config)."""
        old = current_palette()
        if apply_ui_accent(hex_color):
            retheme_all_widgets(old, current_palette())

    def _apply_name_update(self, name: str, user_name: str, ui_color: str = "",
                           voice: str = ""):
        """Update all name/theme-dependent UI elements and persist to config."""
        self._assistant_name = name.strip() or "JARVIS"
        display = self._assistant_name.upper()
        self.setWindowTitle(f"{display} — {APP_VERSION}")
        if display in ("JARVIS", "J.A.R.V.I.S"):
            self._sub_lbl.setText("JARVIS  //  PERSONAL AI")
        else:
            self._sub_lbl.setText(f"{display}  //  PERSONAL AI")
        self._log._ai_name_lc = self._assistant_name.lower()
        self.hud._assistant_name = display

        color_changed = False
        if ui_color:
            old = current_palette()
            if apply_ui_accent(ui_color):
                # Live-paint the whole interface (panels, buttons, borders, HUD)
                retheme_all_widgets(old, current_palette())
                color_changed = old["PRI"] != C.PRI

        # Voice change → persist and, if it actually changed, rebuild the Live
        # session so the new voice takes effect (it's fixed at connect time).
        voice_changed = False
        if voice:
            from memory.config_manager import get_voice, save_voice
            if voice != get_voice():
                save_voice(voice)
                voice_changed = True

        try:
            data = _read_full_config()
            data["assistant_name"] = self._assistant_name
            data["user_name"] = user_name.strip()
            if ui_color:
                data["ui_color"] = ui_color.strip().lower()
            API_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
            self._log.append_log(f"SYS: Identity updated — {display}")
            if color_changed:
                self._log.append_log(f"SYS: UI colour applied — {ui_color}")
            if voice_changed:
                self._log.append_log(f"SYS: Voice set — {voice}")
        except Exception as e:
            self._log.append_log(f"ERR: Config save failed — {e}")

        if voice_changed and self.on_voice_change:
            self.on_voice_change()

    def _centre_overlay(self, ov) -> None:
        """Place a floating overlay in the middle of the HUD and show it."""
        cw = self.centralWidget()
        ov.adjustSize()
        ov.setGeometry(
            max(0, (cw.width()  - ov.width())  // 2),
            max(0, (cw.height() - ov.height()) // 2),
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()

    # ── Audio devices ────────────────────────────────────────────────────────

    def _open_audio_devices(self):
        ov = AudioDeviceOverlay(parent=self.centralWidget())
        ov.picked.connect(self._on_audio_devices_applied)
        self._centre_overlay(ov)
        self._audio_overlay = ov            # keep a reference so it isn't GC'd

    def _on_audio_devices_applied(self):
        self._log.append_log("SYS: Audio devices updated.")
        if self.on_audio_device_change:
            self.on_audio_device_change()

    # ── Memory panel ─────────────────────────────────────────────────────────

    def _open_memory_panel(self):
        ov = MemoryOverlay(parent=self.centralWidget())
        self._centre_overlay(ov)
        self._memory_overlay = ov

    def _open_agent_room(self):
        overlay = getattr(self, "_agent_room_overlay", None)
        if overlay is None:
            overlay = AgentRoomOverlay(parent=self.centralWidget())
            overlay.task_center_requested.connect(self._open_task_planner)
            overlay.camera_requested.connect(self._on_user_camera_toggle)
            self._agent_room_overlay = overlay
            overlay.set_snapshot(self._agent_room_snapshot)
            overlay.graph.set_runtime(
                getattr(self, "_ai_core_state", "IDLE"), self._audio_level
            )
            for event in self._agent_room_events:
                overlay.add_event(event)
        self._centre_overlay(overlay)

    def _open_command_palette(self):
        overlay = getattr(self, "_command_overlay", None)
        if overlay is None:
            overlay = CommandPalette(parent=self.centralWidget())
            overlay.command_selected.connect(self._run_palette_command)
            self._command_overlay = overlay
        self._centre_overlay(overlay)
        overlay.search.setFocus()

    def _run_palette_command(self, command: str):
        actions = {
            "agents": self._open_agent_room,
            "chat": lambda: self._input.setFocus(),
            "voice_start": lambda: self._set_voice_muted(False),
            "voice_stop": lambda: self._set_voice_muted(True),
            "memory": self._open_memory_panel,
            "files": self._drop_zone._browse,
            "vision": self._open_vision_center,
            "tasks": self._open_task_planner,
            "automation": lambda: self._send_dock_command(
                "What automation tasks can you help me with?"
            ),
            "network": lambda: self._right_scroll.verticalScrollBar().setValue(
                self._right_scroll.verticalScrollBar().maximum()
            ),
            "system": lambda: self._left_scroll.verticalScrollBar().setValue(0),
            "settings": self._open_customize,
            "clear_context": lambda: self._send_dock_command(
                "Please clear our current conversation context and start a fresh conversation. "
                "Do not delete long-term memory."
            ),
        }
        action = actions.get(command)
        if action is None:
            self._log.append_log(f"ERR: Unknown command-palette action: {command}")
            return
        action()

    def _set_voice_muted(self, muted: bool):
        if self._muted != muted:
            self._toggle_mute()

    def _open_vision_center(self):
        overlay = getattr(self, "_vision_overlay", None)
        if overlay is None:
            overlay = VisionCenterOverlay(parent=self.centralWidget())
            overlay.capture_requested.connect(self._request_vision_capture)
            self._vision_overlay = overlay
        overlay.set_status(self._vision_state)
        self._centre_overlay(overlay)

    def _open_task_planner(self):
        overlay = getattr(self, "_task_overlay", None)
        if overlay is None:
            overlay = TaskPlannerOverlay(parent=self.centralWidget())
            overlay.task_requested.connect(self._start_planned_task)
            overlay.interrupt_requested.connect(self._interrupt_task)
            overlay.resume_requested.connect(self._resume_task)
            overlay.cancel_requested.connect(self._cancel_task)
            self._task_overlay = overlay
            overlay.set_plan(self._last_task_plan)
            overlay.set_actions(self._task_actions)
            overlay.set_status(self._task_badge.text().removeprefix("TASK  "))
        self._centre_overlay(overlay)

    def _start_planned_task(self, prompt: str):
        self._last_task_plan = ""
        self._task_actions.clear()
        if self._task_overlay is not None:
            self._task_overlay.plan_view.clear()
            self._task_overlay.actions_view.clear()
            self._task_overlay._actions.clear()
        self._set_task_status("PLANNING", "Sending the requested plan to the live assistant.")
        if not self._send_dock_command(prompt):
            self._set_task_status("FAILED", "Assistant backend is not connected.")

    def _interrupt_task(self):
        if self._task_badge.text() not in {
            "TASK  PLANNING", "TASK  PROCESSING", "TASK  EXECUTING",
        }:
            self._set_task_status("IDLE", "There is no active task response to interrupt.")
            return
        self._do_interrupt()
        self._set_task_status(
            "PAUSED", "Assistant response interrupted; an already-running action may finish."
        )

    def _resume_task(self):
        if self._task_badge.text() != "TASK  PAUSED":
            self._set_task_status("IDLE", "There is no paused task to resume.")
            return
        prompt = (
            "Continue the current task from the conversation context. Check what has "
            "already completed before repeating actions. Do not repeat destructive or "
            "consequential actions without the existing confirmation gate."
        )
        if self._send_dock_command(prompt):
            self._set_task_status("PROCESSING", "JARVIS is continuing from current context.")
        else:
            self._set_task_status("FAILED", "Assistant backend is not connected.")

    def _cancel_task(self):
        if self._task_badge.text() not in {
            "TASK  PLANNING", "TASK  PROCESSING", "TASK  EXECUTING",
            "TASK  PAUSED", "TASK  WAITING", "TASK  ASSIGNED",
        }:
            self._set_task_status("IDLE", "There is no active task to cancel.")
            return
        if callable(self.on_task_cancel):
            self.on_task_cancel()
        else:
            self._do_interrupt()
            self._set_task_status(
                "CANCELLED", "Assistant response interrupted; running actions may finish."
            )

    def _request_vision_capture(self, prompt: str):
        if not self._send_dock_command(prompt):
            self._set_vision_status("VISION OFFLINE · Assistant backend is not connected.")

    def _set_context_status(self, status: str):
        self._context_badge.setText(f"CTX  {status}")
        self._context_badge.setStyleSheet(
            f"color: {C.GREEN if status.startswith('ACTIVE') else C.TEXT_MED}; "
            "background: transparent;"
        )

    def _set_task_status(self, state: str, detail: str = ""):
        color = C.GREEN if state == "COMPLETED" else C.ACC if state in {
            "PLANNING", "PROCESSING", "EXECUTING", "PAUSED", "ASSIGNED",
            "WAITING", "CANCELLING",
        } else C.RED if state == "FAILED" else C.TEXT_MED
        self._task_badge.setText(f"TASK  {state}")
        self._task_badge.setStyleSheet(f"color: {color}; background: transparent;")
        overlay = getattr(self, "_task_overlay", None)
        if overlay is not None:
            overlay.set_status(state, detail)

    def _set_agent_room_data(self, snapshot: dict):
        self._agent_room_snapshot = dict(snapshot)
        overlay = getattr(self, "_agent_room_overlay", None)
        if overlay is not None:
            overlay.set_snapshot(snapshot)

    def _record_agent_event(self, event: dict):
        self._agent_room_events.append(dict(event))
        self._agent_room_events = self._agent_room_events[-80:]
        overlay = getattr(self, "_agent_room_overlay", None)
        if overlay is not None:
            overlay.add_event(event)

    def _set_task_plan(self, text: str):
        self._last_task_plan = text
        overlay = getattr(self, "_task_overlay", None)
        if overlay is not None:
            overlay.set_plan(text)

    def _record_task_action(self, name: str, status: str):
        line = f"[{status}] {name}"
        if status == "RUNNING":
            self._task_actions.append(line)
        else:
            for index in range(len(self._task_actions) - 1, -1, -1):
                if self._task_actions[index].startswith("[RUNNING] "):
                    self._task_actions[index] = line
                    break
            else:
                self._task_actions.append(line)
        self._task_actions = self._task_actions[-8:]
        overlay = getattr(self, "_task_overlay", None)
        if overlay is not None:
            overlay.set_actions(self._task_actions)

    def _set_vision_status(self, status: str):
        self._vision_state = status
        overlay = getattr(self, "_vision_overlay", None)
        if overlay is not None:
            overlay.set_status(status)

    def _show_transcription(self, text: str):
        from memory.memory_manager import redact_sensitive_text
        safe_text = redact_sensitive_text(text)[:240].replace("<", "&lt;").replace(">", "&gt;")
        self._state_indicator.setToolTip(f"Latest transcription: {safe_text}")
        self._set_voice_status("TRANSCRIBING")
        self._log.append_log(f"SYS: Transcription received ({len(text)} characters).")

    def _update_audio_level(self, level: float):
        self.hud.set_audio_level(level)
        self._audio_level = max(0.0, min(1.0, float(level or 0.0)))
        overlay = getattr(self, "_agent_room_overlay", None)
        if overlay is not None:
            overlay.graph.set_runtime(
                getattr(self, "_ai_core_state", "IDLE"), self._audio_level
            )
        self._audio_level_badge.setText(f"AUDIO  {max(0.0, min(1.0, level)) * 100:.0f}%")
        self._audio_level_badge.setStyleSheet(
            f"color: {C.PRI if level > 0.02 else C.TEXT_DIM}; background: transparent;"
        )

    def _set_voice_status(self, status: str):
        self._voice_status_badge.setText(f"VOICE  {status}")
        color = C.RED if status in {"ERROR", "MUTED"} else (
            C.ACC if status in {"TRANSCRIBING", "PROCESSING", "SPEAKING"} else C.GREEN
        )
        self._voice_status_badge.setStyleSheet(f"color: {color}; background: transparent;")

    def closeEvent(self, event):
        self._user_camera_generation += 1
        self._user_camera_enabled = False
        self._cam_stop.set()
        self._cam_stream_sig.emit(False)
        super().closeEvent(event)

    # ── Irreversible-action confirmation ─────────────────────────────────────

    def _show_confirm_banner(self, title: str, detail: str):
        self._hide_confirm_banner()
        ov = ConfirmBanner(title, detail, parent=self.centralWidget())
        ov.answered.connect(self._on_confirm_answered)
        self._centre_overlay(ov)
        self._confirm_overlay = ov

    def _hide_confirm_banner(self):
        ov = getattr(self, "_confirm_overlay", None)
        if ov is not None:
            ov.hide()
            ov.deleteLater()
            self._confirm_overlay = None

    def _on_confirm_answered(self, accepted: bool):
        # Tear the banner down first: core.confirm.resolve() may be about to
        # shut the machine down, and a live widget mid-callback is not where you
        # want to be when that happens.
        self._hide_confirm_banner()
        try:
            from core.confirm import resolve
            resolve(bool(accepted))
        except Exception as e:
            self._log.append_log(f"ERR: Confirmation failed — {e}")

    def _open_plugin_manager(self):
        plugins = self.get_plugins() if self.get_plugins else []
        cw = self.centralWidget()
        ov = PluginManagerOverlay(plugins, parent=cw)
        ov.adjustSize()
        ov.setGeometry(
            (cw.width()  - ov.width())  // 2,
            (cw.height() - ov.height()) // 2,
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()
        self._plugin_manager_overlay = ov   # keep a reference so it isn't GC'd

    def _open_plugin_settings(self):
        sections = self.get_plugin_settings() if self.get_plugin_settings else []
        cw = self.centralWidget()
        ov = PluginSettingsOverlay(sections, parent=cw)
        ow = PluginSettingsOverlay._OW
        oh = min(560, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.show()
        ov.raise_()
        self._plugin_settings_overlay = ov   # keep a reference so it isn't GC'd

    # ── Clipboard intelligence ───────────────────────────────────────────────────

    def _on_clipboard_changed(self):
        try:
            text = QApplication.clipboard().text().strip()
            if len(text) >= 10:
                self._clipboard_sig.emit(text)
        except Exception:
            pass

    def _show_clipboard_panel(self, text: str):
        self._clipboard_panel.show_clipboard(text)
        self._position_clipboard_panel()

    def _position_clipboard_panel(self):
        cw = self.centralWidget()
        pw = ClipboardPanel._W
        ph = self._clipboard_panel.sizeHint().height() or ClipboardPanel._H
        x = (cw.width() - pw) // 2
        y = cw.height() - ph - 6
        self._clipboard_panel.setGeometry(x, y, pw, ph)
        self._clipboard_panel.raise_()

    def _on_clipboard_action(self, cmd: str):
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(cmd,), daemon=True).start()

    # ────────────────────────────────────────────────────────────────────────────

    def _do_interrupt(self):
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇  MICROPHONE MUTED")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #140006; color: {C.MUTED_C};
                    border: 1px solid {C.MUTED_C}; border-radius: 3px;
                }}
            """)
        else:
            self._mute_btn.setText("🎙  MICROPHONE ACTIVE")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #00140a; color: {C.GREEN};
                    border: 1px solid {C.GREEN}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #001f10; }}
            """)
        self._style_header_mic_btn()

    def _style_header_mic_btn(self):
        button = getattr(self, "_header_mic_btn", None)
        if button is None:
            return
        muted = self._muted
        button.setText("◉  MUTED" if muted else "◉  MIC")
        color = C.RED if muted else C.GREEN
        background = "#140008" if muted else C.PANEL2
        button.setStyleSheet(f"""
            QPushButton {{ color: {color}; background: {background};
                border: 1px solid {color}; padding: 0 7px; }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI}; }}
        """)

    def _send_dock_command(self, command: str):
        self._input.setText(command)
        return self._send()

    def _quick_action_prompt(self, prompt: str):
        self._input.setText(prompt)
        self._input.setFocus()
        self._input.setCursorPosition(len(prompt))

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return False
        self._input.clear()
        from memory.memory_manager import redact_sensitive_text
        self._log.append_log(f"You: {redact_sensitive_text(txt)}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()
            return True
        else:
            self._log.append_log("ERR: Assistant backend is not connected; command was not sent.")
            return False

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")
        self._ai_core_state = state
        self._audio_level = getattr(self.hud, "_audio_level", self._audio_level)
        overlay = getattr(self, "_agent_room_overlay", None)
        if overlay is not None:
            overlay.graph.set_runtime(
                state, self._audio_level
            )
        if hasattr(self, '_hud_state_lbl'):
            self._hud_state_lbl.setText(f"STATE  {state}")
        if hasattr(self, "_state_indicator"):
            color = (
                C.RED if state in ("ERROR", "MUTED")
                else C.ACC if state in ("THINKING", "ANALYZING", "EXECUTING", "SPEAKING")
                else C.PRI if state == "LISTENING"
                else C.TEXT_MED if state == "SLEEPING"
                else C.GREEN
            )
            label = "MIC MUTED" if state == "MUTED" else f"AI CORE {state}"
            self._state_indicator.setText(f"●  {label}")
            self._state_indicator.setStyleSheet(
                f"color: {color}; background: transparent;")
        voice_state = {
            "LISTENING": "LISTENING",
            "SLEEPING": "ASLEEP",
            "MUTED": "MUTED",
            "ANALYZING": "PROCESSING",
            "THINKING": "PROCESSING",
            "EXECUTING": "PROCESSING",
            "SPEAKING": "SPEAKING",
            "ERROR": "ERROR",
        }.get(state)
        if voice_state:
            self._set_voice_status(voice_state)

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 390
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._assistant_name = _read_full_config().get("assistant_name", "JARVIS") or "JARVIS"
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. {self._assistant_name} online.")


class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        if _OS == "Windows":
            mono_font = Path(r"C:\Windows\Fonts\CascadiaMono.ttf")
            if mono_font.exists():
                font_id = QFontDatabase.addApplicationFont(str(mono_font))
                families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
                if families:
                    QFont.insertSubstitution("Courier New", families[0])
            symbol_font = Path(r"C:\Windows\Fonts\seguisym.ttf")
            if symbol_font.exists():
                symbol_id = QFontDatabase.addApplicationFont(str(symbol_font))
                symbol_families = (QFontDatabase.applicationFontFamilies(symbol_id)
                                   if symbol_id >= 0 else [])
                if symbol_families:
                    global _HUD_ICON_FONT
                    _HUD_ICON_FONT = symbol_families[0]
        self._win = MainWindow(face_path)
        self.root = _RootShim(self._app)
        self._win.showMaximized()

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    @property
    def on_task_cancel(self):
        return self._win.on_task_cancel

    @on_task_cancel.setter
    def on_task_cancel(self, cb):
        self._win.on_task_cancel = cb

    @property
    def on_voice_change(self):
        return self._win.on_voice_change

    @on_voice_change.setter
    def on_voice_change(self, cb):
        self._win.on_voice_change = cb

    @property
    def on_audio_device_change(self):
        return self._win.on_audio_device_change

    @on_audio_device_change.setter
    def on_audio_device_change(self, cb):
        self._win.on_audio_device_change = cb

    @property
    def on_mode_change(self):
        return self._win.on_mode_change

    @on_mode_change.setter
    def on_mode_change(self, cb):
        self._win.on_mode_change = cb

    @property
    def network_status_provider(self):
        return self._win.network_status_provider

    @network_status_provider.setter
    def network_status_provider(self, cb):
        self._win.network_status_provider = cb

    @property
    def active_mode(self) -> str:
        return self._win._active_mode

    def show_confirm(self, title: str, detail: str) -> None:
        """Thread-safe: raise the irreversible-action gate. Called from action
        handlers running in executor threads, so it goes through a signal."""
        self._win._confirm_sig.emit(str(title)[:120], str(detail)[:300])

    def hide_confirm(self) -> None:
        """Thread-safe: take the gate down."""
        self._win._confirm_hide_sig.emit()

    @property
    def get_plugins(self):
        return self._win.get_plugins

    @get_plugins.setter
    def get_plugins(self, cb):
        self._win.get_plugins = cb

    @property
    def get_plugin_settings(self):
        return self._win.get_plugin_settings

    @get_plugin_settings.setter
    def get_plugin_settings(self, cb):
        self._win.get_plugin_settings = cb

    @property
    def on_wake_toggle(self):
        return self._win.on_wake_toggle

    @on_wake_toggle.setter
    def on_wake_toggle(self, cb):
        self._win.on_wake_toggle = cb

    @property
    def on_wake_manual(self):
        return self._win.on_wake_manual

    @on_wake_manual.setter
    def on_wake_manual(self, cb):
        self._win.on_wake_manual = cb

    @property
    def wake_get_state(self):
        return self._win.wake_get_state

    @wake_get_state.setter
    def wake_get_state(self, cb):
        self._win.wake_get_state = cb

    def set_audio_level(self, level: float) -> None:
        """Thread-safe: feed a 0.0–1.0 live audio level to the HUD waveform.
        Called from the audio threads; a plain float store is atomic under the
        GIL, so no signal/lock is needed for this cosmetic value."""
        try:
            self._win._audio_level_sig.emit(float(level))
        except (RuntimeError, TypeError, ValueError):
            return

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
        except Exception:
            pass

    @property
    def ptt_hold(self):
        return self._win.ptt_hold

    @ptt_hold.setter
    def ptt_hold(self, cb):
        self._win.ptt_hold = cb

    @property
    def on_push_to_talk(self):
        return self._win.on_push_to_talk

    @on_push_to_talk.setter
    def on_push_to_talk(self, cb):
        self._win.on_push_to_talk = cb

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: post a schedule of (level, openness, width) mouth frames
        for JARVIS's own speech. `at` is the wall-clock time the batch begins to
        sound, not the time of the call. See HudCanvas.push_visemes()."""
        try:
            self._win.hud.push_visemes(frames, hop, at)
        except Exception:
            pass

    def notify_phone_connected(self) -> None:
        self._win.notify_phone_connected()

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def set_voice_status(self, status: str):
        self._win._voice_status_sig.emit(str(status)[:20])

    def set_context_status(self, status: str):
        self._win._context_sig.emit(str(status)[:48])

    def set_task_status(self, state: str, detail: str = ""):
        self._win._task_sig.emit(str(state)[:24], str(detail)[:180])

    def set_task_plan(self, text: str):
        self._win._task_plan_sig.emit(str(text)[:2000])

    def record_task_action(self, name: str, status: str):
        self._win._task_action_sig.emit(str(name)[:80], str(status)[:16])

    def set_agent_room_data(self, snapshot: dict):
        self._win._agent_room_sig.emit(snapshot)

    def record_agent_event(self, event: dict):
        self._win._agent_event_sig.emit(dict(event))

    def open_agent_room(self):
        self._win._open_agent_room()

    def set_vision_status(self, status: str):
        self._win._vision_status_sig.emit(str(status)[:180])

    def set_transcription(self, text: str):
        self._win._transcription_sig.emit(str(text)[:240])

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the panel below the HUD."""
        self._win._content_sig.emit(title[:48], text[:4000])

    def show_quiz(self, topic: str, questions, grade=None) -> None:
        """Thread-safe: put an interactive quiz on the board.

        `grade(question, given)` decides each answer — the plugin supplies it so
        the marking rules live with the questions rather than being duplicated
        here. Returning None from it means "JARVIS should judge this one", which
        is how open answers and near-miss gap-fills are handled.

        Returns immediately: the user answers at their own pace and the finished
        result is delivered back through on_text_command.
        """
        self._win._quiz_sig.emit(str(topic or ""), list(questions or []), grade)

    def hide_quiz(self) -> None:
        """Thread-safe: clear any quiz currently on the board."""
        self._win._quiz_hide_sig.emit()

    def show_review(self, title: str, summary: str, findings, unclear=None) -> None:
        """Thread-safe: lay a document review into the panel below the HUD.

        `findings` is a list of {heading, detail, severity, quote, suggestion};
        severity is one of 'serious' / 'caution' / 'note' and decides colour and
        order here, so the caller supplies no styling of its own.
        """
        self._win._review_sig.emit(str(title or ""), str(summary or ""),
                                   list(findings or []), list(unclear or []))

    def prompt_reconfig(self):
        """Thread-safe: show the API key setup overlay (e.g. after an auth error)."""
        self._win._ready = False
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        """Thread-safe: show a webcam frame in the small overlay (screen captures)."""
        self._win._camera_sig.emit(img_bytes)

    def start_camera_stream(self) -> None:
        """Thread-safe: start live camera feed in the full HUD area."""
        self._win.start_camera_stream()

    def stop_user_camera_monitor(self, wait: bool = False) -> bool:
        """Stop optional local presence capture before vision opens the camera."""
        return self._win.stop_user_camera_monitor(wait)

    def stop_camera_stream(self) -> None:
        """Thread-safe: stop the live camera feed."""
        self._win.stop_camera_stream()

    @property
    def assistant_name(self) -> str:
        return self._win._assistant_name

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")