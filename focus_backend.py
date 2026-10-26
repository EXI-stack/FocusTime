from __future__ import annotations

import logging
import math
import os
import platform
import shutil
import struct
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Callable

PRESETS = [15, 25, 45, 60]
DEFAULT_PRESET = 25
SOUND_ENABLED = True
SOUND_VOLUME = 0.5

STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_PAUSED = "paused"
STATE_FINISHED = "finished"

_BASE_DIR = Path(__file__).resolve().parent
_ASSETS_DIR = _BASE_DIR / "assets"
_CHIME_PATH = _ASSETS_DIR / "chime.wav"
_SAMPLE_RATE = 44100
_NOTES = ((783.99, 0.0, 0.55), (1046.5, 0.14, 0.9))
_NOTIFY_TITLE = "Focus complete"
_NOTIFY_TEXT = "Great work — time for a break."

logger = logging.getLogger("focus_backend")
logger.addHandler(logging.NullHandler())
_lock = threading.RLock()
_subscribers: list[Callable[[str, dict], None]] = []
_state = STATE_IDLE
_total_sec = DEFAULT_PRESET * 60
_remaining_sec = float(_total_sec)
_deadline = 0.0
_selected_preset_min = DEFAULT_PRESET if DEFAULT_PRESET in PRESETS else PRESETS[0]
_ticker_started = False
_chime_lock = threading.Lock()
_chime_volume: float | None = None


def _snapshot_unlocked() -> dict:
    remaining = max(0.0, _deadline - time.monotonic()) if _state == STATE_RUNNING else max(0.0, _remaining_sec)
    return {
        "state": _state,
        "remaining_sec": math.ceil(remaining),
        "total_sec": _total_sec,
        "preset_min": _selected_preset_min,
        "presets_min": list(PRESETS),
    }


def _emit(event: str, data: dict) -> None:
    with _lock:
        subscribers = tuple(_subscribers)
    for callback in subscribers:
        try:
            callback(event, dict(data))
        except Exception:
            logger.exception("Event subscriber failed: %s", event)


def _subprocess_kwargs() -> dict:
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs.update(startupinfo=startupinfo, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return kwargs


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def subscribe(cb: Callable[[str, dict], None]) -> Callable[[], None]:
    with _lock:
        if cb not in _subscribers:
            _subscribers.append(cb)

    def unsubscribe() -> None:
        with _lock:
            if cb in _subscribers:
                _subscribers.remove(cb)

    return unsubscribe


def get_state() -> dict:
    with _lock:
        return _snapshot_unlocked()


def select_preset(minutes: int) -> None:
    global _selected_preset_min, _total_sec, _remaining_sec, _state, _deadline
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes not in PRESETS:
        raise ValueError("Preset must be a positive integer from PRESETS")
    with _lock:
        if _state in (STATE_RUNNING, STATE_PAUSED):
            return
        _selected_preset_min = minutes
        _total_sec = minutes * 60
        _remaining_sec = float(_total_sec)
        _deadline = 0.0
        _state = STATE_IDLE
        snapshot = _snapshot_unlocked()
    _emit("reset", snapshot)


def start(minutes: int | None = None, *, seconds: int | None = None) -> None:
    global _state, _total_sec, _remaining_sec, _deadline
    with _lock:
        if _state in (STATE_RUNNING, STATE_PAUSED):
            return
        total = (
            int(seconds)
            if seconds is not None
            else int(minutes) * 60
            if minutes is not None
            else _selected_preset_min * 60
        )
        if total <= 0:
            return
        _total_sec = total
        _remaining_sec = float(total)
        _deadline = time.monotonic() + total
        _state = STATE_RUNNING
        snapshot = _snapshot_unlocked()
    if SOUND_ENABLED:
        threading.Thread(target=_ensure_chime_safe, name="focus-time-chime", daemon=True).start()
    _emit("started", snapshot)


def pause() -> None:
    global _state, _remaining_sec, _deadline
    with _lock:
        if _state != STATE_RUNNING:
            return
        _remaining_sec = max(0.0, _deadline - time.monotonic())
        _deadline = 0.0
        _state = STATE_PAUSED
        snapshot = _snapshot_unlocked()
    _emit("paused", snapshot)


def resume() -> None:
    global _state, _deadline
    with _lock:
        if _state != STATE_PAUSED or _remaining_sec <= 0:
            return
        _deadline = time.monotonic() + _remaining_sec
        _state = STATE_RUNNING
        snapshot = _snapshot_unlocked()
    _emit("resumed", snapshot)


def reset() -> None:
    global _state, _total_sec, _remaining_sec, _deadline
    with _lock:
        _state = STATE_IDLE
        _total_sec = _selected_preset_min * 60
        _remaining_sec = float(_total_sec)
        _deadline = 0.0
        snapshot = _snapshot_unlocked()
    _emit("reset", snapshot)


stop = reset


def set_presets(presets: list[int]) -> None:
    global PRESETS, DEFAULT_PRESET, _selected_preset_min, _state, _total_sec, _remaining_sec, _deadline
    if (
        not isinstance(presets, (list, tuple))
        or len(presets) != 4
        or any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in presets)
    ):
        raise ValueError("Exactly four positive integer presets are required")
    with _lock:
        PRESETS = [int(v) for v in presets]
        if DEFAULT_PRESET not in PRESETS:
            DEFAULT_PRESET = PRESETS[0]
        if _selected_preset_min not in PRESETS:
            _selected_preset_min = DEFAULT_PRESET
        if _state in (STATE_IDLE, STATE_FINISHED):
            _total_sec = _selected_preset_min * 60
            _remaining_sec = float(_total_sec)
            _deadline = 0.0
            _state = STATE_IDLE
        snapshot = _snapshot_unlocked()
    _emit("presets_changed", snapshot)


def _ticker_loop() -> None:
    global _state, _remaining_sec
    while True:
        finish = False
        tick_data = None
        snapshot = None
        with _lock:
            if _state == STATE_RUNNING:
                remaining = _deadline - time.monotonic()
                if remaining <= 0:
                    _state = STATE_FINISHED
                    _remaining_sec = 0.0
                    finish = True
                    snapshot = _snapshot_unlocked()
                else:
                    _remaining_sec = remaining
                    tick_data = {"remaining_sec": remaining, "total_sec": _total_sec}
        if finish and snapshot is not None:
            try:
                notified = _notify(_NOTIFY_TITLE, _NOTIFY_TEXT)
            except Exception:
                logger.exception("System notification failed")
                notified = False
            snapshot["notified"] = notified
            _emit("finished", snapshot)
            if SOUND_ENABLED:
                threading.Thread(target=_play_sound, name="focus-time-sound", daemon=True).start()
            time.sleep(0.1)
        elif tick_data is not None:
            _emit("tick", tick_data)
            time.sleep(0.1)
        else:
            time.sleep(0.2)


def _ensure_ticker() -> None:
    global _ticker_started
    with _lock:
        if _ticker_started:
            return
        _ticker_started = True
        threading.Thread(target=_ticker_loop, name="focus-time-ticker", daemon=True).start()


def _ensure_chime_safe() -> None:
    try:
        _ensure_chime()
    except Exception:
        logger.exception("Failed to prepare sound")


def _synthesize_chime(path: str, volume: float) -> None:
    length = int(_SAMPLE_RATE * 1.2)
    buffer = [0.0] * length
    for freq, start_time, duration in _NOTES:
        start_idx = int(_SAMPLE_RATE * start_time)
        for i in range(int(_SAMPLE_RATE * duration)):
            index = start_idx + i
            if index >= length:
                break
            t = i / _SAMPLE_RATE
            envelope = math.exp(-4.5 * t) * (1.0 - math.exp(-300.0 * t))
            buffer[index] += (math.sin(2 * math.pi * freq * t) + 0.3 * math.sin(4 * math.pi * freq * t)) * envelope
    peak = max((abs(v) for v in buffer), default=1.0) or 1.0
    frames = b"".join(
        struct.pack("<h", max(-32768, min(32767, int(v / peak * volume * 32767)))) for v in buffer
    )
    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(_SAMPLE_RATE)
        wav_file.writeframes(frames)


def _ensure_chime() -> Path:
    global _chime_volume
    volume = max(0.0, min(1.0, float(SOUND_VOLUME)))
    with _chime_lock:
        if _CHIME_PATH.exists() and _chime_volume == volume:
            return _CHIME_PATH
        _ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        _synthesize_chime(str(_CHIME_PATH), volume)
        _chime_volume = volume
        return _CHIME_PATH


def _play_sound() -> None:
    if not SOUND_ENABLED:
        return
    try:
        path = _ensure_chime()
        system = platform.system()
        if system == "Windows":
            import winsound

            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        elif system == "Darwin" and shutil.which("afplay"):
            subprocess.Popen(["afplay", str(path)], **_subprocess_kwargs())
        else:
            for player in ("paplay", "aplay"):
                if shutil.which(player):
                    subprocess.Popen([player, str(path)], **_subprocess_kwargs())
                    break
    except Exception:
        logger.exception("Failed to play sound")


def _notify(title: str, text: str) -> bool:
    system = platform.system()
    try:
        if system == "Windows":
            return _windows_toast(title, text)
        if system == "Darwin":
            script = f'display notification "{_escape_applescript(text)}" with title "{_escape_applescript(title)}"'
            subprocess.Popen(["osascript", "-e", script], **_subprocess_kwargs())
            return True
        if system == "Linux" and shutil.which("notify-send"):
            subprocess.Popen(["notify-send", title, text], **_subprocess_kwargs())
            return True
    except Exception:
        logger.exception("Notification failed")
    return False


def _windows_toast(title: str, text: str) -> bool:
    if not shutil.which("powershell"):
        return False
    title_xml = _xml_escape(title)
    text_xml = _xml_escape(text)
    script = f"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast><visual><binding template=\"ToastText02\"><text id=\"1\">{title_xml}</text><text id=\"2\">{text_xml}</text></binding></visual></toast>')
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Focus Time').Show($toast)
"""
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            **_subprocess_kwargs(),
        )
        return True
    except Exception:
        logger.exception("Windows notification failed")
        return False


_ensure_ticker()