"""
HOOD Windows Backend - Native Win32 / Desktop API Integration
Provides real ctypes-based window management, screen capture, clipboard control,
and desktop switching for Windows environments, with graceful fallbacks.
"""

from __future__ import annotations
import os
import sys
import time
import ctypes
from ctypes import wintypes
import struct
import hashlib
from typing import List, Optional, Tuple, Dict, Any
from pathlib import Path

from services.desktop.contracts import WindowInfo, WindowState


class WindowsNativeBackend:
    """Encapsulates native Win32 interactions with automatic desktop station switching."""

    def __init__(self):
        self.is_windows = sys.platform == "win32"
        self._user32 = None
        self._gdi32 = None
        self._kernel32 = None
        self._default_desktop_handle = None

        if self.is_windows:
            try:
                self._user32 = ctypes.windll.user32
                self._gdi32 = ctypes.windll.gdi32
                self._kernel32 = ctypes.windll.kernel32
                self._setup_win32_types()
                self._ensure_default_desktop()
            except Exception:
                pass

    def _setup_win32_types(self):
        if not self._kernel32 or not self._user32:
            return
        # Setup 64-bit safe argtypes
        self._kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        self._kernel32.GlobalLock.restype = ctypes.c_void_p
        self._kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        self._kernel32.GlobalUnlock.restype = wintypes.BOOL
        self._user32.GetClipboardData.argtypes = [wintypes.UINT]
        self._user32.GetClipboardData.restype = wintypes.HANDLE
        self._user32.OpenClipboard.argtypes = [wintypes.HWND]
        self._user32.OpenClipboard.restype = wintypes.BOOL
        self._user32.CloseClipboard.restype = wintypes.BOOL

    def _ensure_default_desktop(self) -> bool:
        """Ensures the calling thread is attached to the interactive Default desktop."""
        if not self._user32:
            return False
        try:
            hdef = self._user32.OpenDesktopW("Default", 0, False, 0x01FF)  # GENERIC_ALL
            if hdef:
                self._default_desktop_handle = hdef
                self._user32.SetThreadDesktop(hdef)
                return True
        except Exception:
            pass
        return False

    def enumerate_windows(self) -> List[WindowInfo]:
        """Enumerates visible titled top-level windows on the interactive desktop."""
        if not self.is_windows or not self._user32:
            return []

        self._ensure_default_desktop()
        windows: List[WindowInfo] = []

        def enum_cb(hwnd, lparam):
            if self._user32.IsWindowVisible(hwnd):
                length = self._user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    text_buf = ctypes.create_unicode_buffer(length + 1)
                    self._user32.GetWindowTextW(hwnd, text_buf, length + 1)
                    title = text_buf.value.strip()

                    class_buf = ctypes.create_unicode_buffer(256)
                    self._user32.GetClassNameW(hwnd, class_buf, 256)
                    class_name = class_buf.value.strip()

                    pid = wintypes.DWORD()
                    self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

                    rect = wintypes.RECT()
                    self._user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    bbox = (rect.left, rect.top, rect.right, rect.bottom)

                    # Determine process name if possible
                    pname = "unknown"
                    try:
                        import psutil
                        if pid.value > 0 and psutil.pid_exists(pid.value):
                            pname = psutil.Process(pid.value).name()
                    except Exception:
                        pass

                    # Filter out purely internal/system windows
                    if title and title != "Program Manager":
                        windows.append(WindowInfo(
                            hwnd=int(hwnd),
                            title=title,
                            process_id=int(pid.value),
                            process_name=pname,
                            class_name=class_name,
                            rect=bbox,
                            is_visible=True,
                            is_active=False
                        ))
            return True

        CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self._user32.EnumWindows(CB(enum_cb), 0)

        # Mark active foreground window
        active_hwnd = self.get_foreground_window_hwnd()
        for w in windows:
            if w.hwnd == active_hwnd:
                w.is_active = True

        return windows

    def get_foreground_window_hwnd(self) -> int:
        if not self._user32:
            return 0
        self._ensure_default_desktop()
        return int(self._user32.GetForegroundWindow())

    def get_foreground_window(self) -> Optional[WindowInfo]:
        hwnd = self.get_foreground_window_hwnd()
        if not hwnd:
            return None
        windows = self.enumerate_windows()
        for w in windows:
            if w.hwnd == hwnd:
                return w
        # Direct fallback for active window if not in enumerated list
        try:
            length = self._user32.GetWindowTextLengthW(hwnd)
            text_buf = ctypes.create_unicode_buffer(length + 1)
            self._user32.GetWindowTextW(hwnd, text_buf, length + 1)
            title = text_buf.value.strip() or "Active Window"

            class_buf = ctypes.create_unicode_buffer(256)
            self._user32.GetClassNameW(hwnd, class_buf, 256)
            class_name = class_buf.value.strip()

            pid = wintypes.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pname = "unknown"
            try:
                import psutil
                if pid.value > 0 and psutil.pid_exists(pid.value):
                    pname = psutil.Process(pid.value).name()
            except Exception:
                pass

            rect = wintypes.RECT()
            self._user32.GetWindowRect(hwnd, ctypes.byref(rect))
            bbox = (rect.left, rect.top, rect.right, rect.bottom)
            return WindowInfo(
                hwnd=int(hwnd),
                title=title,
                process_id=int(pid.value),
                process_name=pname,
                class_name=class_name,
                rect=bbox,
                is_visible=True,
                is_active=True
            )
        except Exception:
            return None

    def activate_window(self, hwnd: int) -> bool:
        """Brings the specified window to the foreground."""
        if not self._user32 or hwnd <= 0:
            return False
        self._ensure_default_desktop()
        try:
            # If minimized, restore it
            if self._user32.IsIconic(hwnd):
                self._user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            else:
                self._user32.ShowWindow(hwnd, 5)  # SW_SHOW
            self._user32.SetForegroundWindow(hwnd)
            return True
        except Exception:
            return False

    def minimize_window(self, hwnd: int) -> bool:
        if not self._user32 or hwnd <= 0:
            return False
        self._ensure_default_desktop()
        try:
            return bool(self._user32.ShowWindow(hwnd, 6))  # SW_MINIMIZE
        except Exception:
            return False

    def maximize_window(self, hwnd: int) -> bool:
        if not self._user32 or hwnd <= 0:
            return False
        self._ensure_default_desktop()
        try:
            return bool(self._user32.ShowWindow(hwnd, 3))  # SW_MAXIMIZE
        except Exception:
            return False

    def restore_window(self, hwnd: int) -> bool:
        if not self._user32 or hwnd <= 0:
            return False
        self._ensure_default_desktop()
        try:
            return bool(self._user32.ShowWindow(hwnd, 9))  # SW_RESTORE
        except Exception:
            return False

    def close_window(self, hwnd: int) -> bool:
        """Sends WM_CLOSE message to gracefully ask window to close."""
        if not self._user32 or hwnd <= 0:
            return False
        self._ensure_default_desktop()
        try:
            WM_CLOSE = 0x0010
            return bool(self._user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))
        except Exception:
            return False

    def get_screen_dimensions(self) -> Tuple[int, int]:
        if not self._user32:
            return (1920, 1080)
        w = self._user32.GetSystemMetrics(0)  # SM_CXSCREEN
        h = self._user32.GetSystemMetrics(1)  # SM_CYSCREEN
        return (w if w > 0 else 1920, h if h > 0 else 1080)

    def capture_screen_bmp(self) -> bytes:
        """Captures the current screen into an uncompressed 24-bit BMP byte stream."""
        if not self.is_windows or not self._user32 or not self._gdi32:
            return self._generate_fallback_bmp(1536, 864)

        self._ensure_default_desktop()
        w, h = self.get_screen_dimensions()

        hdc_screen = self._user32.GetDC(0)
        hdc_mem = self._gdi32.CreateCompatibleDC(hdc_screen)
        hbm = self._gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        self._gdi32.SelectObject(hdc_mem, hbm)

        # BitBlt SRCCOPY = 0x00CC0020
        self._gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, 0, 0, 0x00CC0020)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ('biSize', wintypes.DWORD),
                ('biWidth', wintypes.LONG),
                ('biHeight', wintypes.LONG),
                ('biPlanes', wintypes.WORD),
                ('biBitCount', wintypes.WORD),
                ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD),
                ('biXPelsPerMeter', wintypes.LONG),
                ('biYPelsPerMeter', wintypes.LONG),
                ('biClrUsed', wintypes.DWORD),
                ('biClrImportant', wintypes.DWORD),
            ]

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h  # top-down DIB
        bmi.biPlanes = 1
        bmi.biBitCount = 24
        bmi.biCompression = 0

        stride = ((w * 3 + 3) // 4) * 4
        image_size = stride * h
        pixel_buf = ctypes.create_string_buffer(image_size)

        lines = self._gdi32.GetDIBits(hdc_mem, hbm, 0, h, pixel_buf, ctypes.byref(bmi), 0)

        # Cleanup GDI handles
        self._gdi32.DeleteObject(hbm)
        self._gdi32.DeleteDC(hdc_mem)
        self._user32.ReleaseDC(0, hdc_screen)

        if lines > 0:
            # Prepend standard BMP header
            file_size = 54 + image_size
            bmp_header = struct.pack('<2sIHHI', b'BM', file_size, 0, 0, 54)
            dib_header = struct.pack('<IIIHHIIIIII', 40, w, h, 1, 24, 0, image_size, 2835, 2835, 0, 0)
            return bmp_header + dib_header + pixel_buf.raw
        else:
            return self._generate_fallback_bmp(w, h)

    def _generate_fallback_bmp(self, width: int, height: int) -> bytes:
        row_bytes = width * 3
        padding = (4 - (row_bytes % 4)) % 4
        stride = row_bytes + padding
        image_size = stride * height
        file_size = 54 + image_size
        bmp_header = struct.pack('<2sIHHI', b'BM', file_size, 0, 0, 54)
        dib_header = struct.pack('<IIIHHIIIIII', 40, width, height, 1, 24, 0, image_size, 2835, 2835, 0, 0)
        row = bytearray([120, 120, 120] * width + [0] * padding)
        buf = bytearray()
        for _ in range(height):
            buf.extend(row)
        return bmp_header + dib_header + bytes(buf)

    # --- Clipboard Management ---

    def read_clipboard_text(self) -> str:
        if not self._user32 or not self._kernel32:
            return ""
        self._ensure_default_desktop()
        CF_UNICODETEXT = 13
        if not self._user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""

        text = ""
        if self._user32.OpenClipboard(0):
            try:
                hglb = self._user32.GetClipboardData(CF_UNICODETEXT)
                if hglb:
                    pdata = self._kernel32.GlobalLock(hglb)
                    if pdata:
                        text = ctypes.c_wchar_p(pdata).value or ""
                        self._kernel32.GlobalUnlock(hglb)
            finally:
                self._user32.CloseClipboard()
        return text

    def write_clipboard_text(self, text: str) -> bool:
        if not self._user32 or not self._kernel32:
            return False
        self._ensure_default_desktop()
        CF_UNICODETEXT = 13
        success = False
        if self._user32.OpenClipboard(0):
            try:
                self._user32.EmptyClipboard()
                text_bytes = (text + "\0").encode("utf-16le")
                size = len(text_bytes)
                GMEM_MOVEABLE = 0x0002
                hmem = self._kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
                if hmem:
                    pdata = self._kernel32.GlobalLock(hmem)
                    if pdata:
                        ctypes.memmove(pdata, text_bytes, size)
                        self._kernel32.GlobalUnlock(hmem)
                        self._user32.SetClipboardData(CF_UNICODETEXT, hmem)
                        success = True
            finally:
                self._user32.CloseClipboard()
        return success
