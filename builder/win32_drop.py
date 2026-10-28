"""Native Windows Explorer drag-and-drop for Tk (no extra dependencies).

Uses ``WM_DROPFILES`` via :mod:`ctypes` so the frozen single-EXE builder accepts
files dropped from Explorer without shipping ``tkinterdnd2`` or similar.

Only the pure helpers (:func:`validate_dropped_paths`, :func:`classify_drop`)
are needed for unit tests.  The :class:`DropTarget` / :func:`try_enable_drop`
helpers are best-effort: they never raise and simply return ``None`` when
Tk, Windows, or the Win32 APIs are unavailable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Sequence

WM_DROPFILES = 0x0233
_GWLP_WNDPROC = -4


def is_windows() -> bool:
    return os.name == "nt"


def _strip_quotes(value: str) -> str:
    text = (value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1].strip()
    return text


def classify_drop(path: Path) -> str:
    """Return ``"iso"``, ``"folder"``, or ``"unsupported"`` for a dropped path."""
    try:
        current = Path(path)
    except Exception:
        return "unsupported"
    try:
        if current.is_dir():
            return "folder"
    except OSError:
        pass
    suffix = current.suffix.lower()
    if suffix == ".iso":
        return "iso"
    return "unsupported"


def validate_dropped_paths(paths: Sequence[str | Path]) -> tuple[Path | None, str | None]:
    """Validate raw dropped paths from Explorer.

    Returns ``(path, None)`` for exactly one supported item, otherwise
    ``(None, error_message)`` with a user-facing explanation.  Never touches
    the filesystem beyond :meth:`Path.is_dir` (via :func:`classify_drop`);
    existence/content checks stay with the caller so messages stay accurate.
    """
    cleaned: list[str] = []
    for raw in paths or []:
        text = _strip_quotes(str(raw) if raw is not None else "")
        if text:
            cleaned.append(text)
    if not cleaned:
        return None, "No file was dropped. Drop a single .iso file or the folder containing default.xex."
    if len(cleaned) > 1:
        return None, (
            f"Drop one file or folder at a time; got {len(cleaned)} items. "
            "Drop a single .iso file or the folder containing default.xex."
        )
    candidate = Path(cleaned[0])
    kind = classify_drop(candidate)
    if kind in ("iso", "folder"):
        return candidate, None
    return None, (
        f"Unsupported drop: {candidate.name or candidate}. "
        "Drop a .iso file (*.iso) or the extracted folder containing default.xex."
    )


class DropTarget:
    """Subclass a Tk top-level to receive ``WM_DROPFILES``.  Windows only."""

    def __init__(self, widget, on_drop: Callable[[list[str]], None]):
        self._widget = widget
        self._on_drop = on_drop
        self._hwnd = None
        self._old_proc = None
        self._new_proc = None
        self._enabled = False

    def attach(self) -> bool:
        import ctypes
        from ctypes import wintypes

        if not is_windows():
            return False
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        # Tk's root is a child of the native top-level wrapper window.
        child = self._widget.winfo_id()
        hwnd = user32.GetParent(child) or child
        get_proc = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        set_proc = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        get_proc.argtypes = [wintypes.HWND, ctypes.c_int]
        get_proc.restype = ctypes.c_ssize_t
        set_proc.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        set_proc.restype = ctypes.c_ssize_t
        call_proc = user32.CallWindowProcW
        call_proc.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM]
        call_proc.restype = ctypes.c_ssize_t
        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        shell32.DragAcceptFiles.restype = None
        shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                           wintypes.LPWSTR, wintypes.UINT]
        shell32.DragQueryFileW.restype = wintypes.UINT
        shell32.DragFinish.argtypes = [wintypes.HANDLE]
        shell32.DragFinish.restype = None
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                                    wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        self._old_proc = get_proc(hwnd, _GWLP_WNDPROC)
        if not self._old_proc:
            return False

        widget_ref = self._widget
        on_drop = self._on_drop

        def _wndproc(h, msg, wparam, lparam):
            if msg == WM_DROPFILES:
                paths: list[str] = []
                try:
                    count = shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
                    for index in range(count):
                        length = shell32.DragQueryFileW(wparam, index, None, 0)
                        buffer = ctypes.create_unicode_buffer(length + 1)
                        shell32.DragQueryFileW(wparam, index, buffer, length + 1)
                        paths.append(buffer.value)
                finally:
                    try:
                        shell32.DragFinish(wparam)
                    except Exception:
                        pass
                try:
                    # Defer to Tk main loop; WndProc must return quickly.
                    widget_ref.after(0, lambda items=list(paths): on_drop(items))
                except Exception:
                    pass
                return 0
            return call_proc(self._old_proc, h, msg, wparam, lparam)

        callback = WNDPROC(_wndproc)
        ctypes.set_last_error(0)
        previous = set_proc(hwnd, _GWLP_WNDPROC, ctypes.cast(callback, ctypes.c_void_p))
        if not previous and ctypes.get_last_error():
            return False
        self._hwnd = hwnd
        self._new_proc = callback
        self._set_proc = set_proc
        self._shell32 = shell32
        self._enabled = True
        shell32.DragAcceptFiles(hwnd, True)
        widget_ref.bind("<Destroy>", lambda event: self.detach() if event.widget is widget_ref else None, add="+")
        return True

    def detach(self) -> None:
        if not self._enabled:
            return
        self._shell32.DragAcceptFiles(self._hwnd, False)
        self._set_proc(self._hwnd, _GWLP_WNDPROC, self._old_proc)
        self._enabled = False


def try_enable_drop(widget, on_drop: Callable[[list[str]], None]):
    """Best-effort Explorer drop support; returns ``DropTarget`` or ``None``."""
    try:
        target = DropTarget(widget, on_drop)
        if target.attach():
            return target
    except Exception:
        pass
    return None
