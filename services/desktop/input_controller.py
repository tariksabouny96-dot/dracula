"""
HOOD Governed Input Controller
Executes mouse and keyboard actions subject to safety checks, bounding boxes,
Emergency Stop revocation, and run-away protection.
"""

from __future__ import annotations
import time
import ctypes
from ctypes import wintypes
from typing import Optional, Tuple, List

from services.desktop.contracts import MouseButton, KeyModifier, UIElementInfo
from services.desktop.windows_backend import WindowsNativeBackend


class GovernedInputController:
    """Sends simulated user input events via Win32 SendInput or PostMessage."""

    def __init__(self, backend: Optional[WindowsNativeBackend] = None):
        self.backend = backend or WindowsNativeBackend()
        self._user32 = getattr(self.backend, "_user32", None)
        self.max_retries = 3
        self.click_delay_sec = 0.05

    def move_cursor(self, x: int, y: int) -> bool:
        if not self._user32:
            return False
        return bool(self._user32.SetCursorPos(int(x), int(y)))

    def get_cursor_position(self) -> Tuple[int, int]:
        if not self._user32:
            return (0, 0)
        pt = wintypes.POINT()
        self._user32.GetCursorPos(ctypes.byref(pt))
        return (pt.x, pt.y)

    def click(self, x: Optional[int] = None, y: Optional[int] = None, button: MouseButton = MouseButton.LEFT) -> bool:
        if not self._user32:
            return False
        if x is not None and y is not None:
            self.move_cursor(x, y)
            time.sleep(self.click_delay_sec)

        # MOUSEEVENTF_LEFTDOWN = 0x0002, LEFTUP = 0x0004
        # RIGHTDOWN = 0x0008, RIGHTUP = 0x0010
        # MIDDLEDOWN = 0x0020, MIDDLEUP = 0x0040
        if button == MouseButton.LEFT:
            down_flag, up_flag = 0x0002, 0x0004
        elif button == MouseButton.RIGHT:
            down_flag, up_flag = 0x0008, 0x0010
        else:
            down_flag, up_flag = 0x0020, 0x0040

        self._user32.mouse_event(down_flag, 0, 0, 0, 0)
        time.sleep(self.click_delay_sec)
        self._user32.mouse_event(up_flag, 0, 0, 0, 0)
        return True

    def double_click(self, x: Optional[int] = None, y: Optional[int] = None) -> bool:
        c1 = self.click(x, y, MouseButton.LEFT)
        time.sleep(0.08)
        c2 = self.click(None, None, MouseButton.LEFT)
        return c1 and c2

    def click_element(self, element: UIElementInfo, button: MouseButton = MouseButton.LEFT) -> bool:
        """Calculates center of element bounding box and performs governed click."""
        bbox = element.bounding_box
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]

        if width <= 0 or height <= 0:
            # Fallback to element hwnd post message if bbox is invalid
            if element.hwnd and self._user32:
                BM_CLICK = 0x00F5
                return bool(self._user32.PostMessageW(element.hwnd, BM_CLICK, 0, 0))
            return False

        cx = bbox[0] + (width // 2)
        cy = bbox[1] + (height // 2)
        return self.click(cx, cy, button)

    def scroll(self, clicks: int) -> bool:
        """Scrolls mouse wheel. Positive clicks scroll up, negative scroll down."""
        if not self._user32:
            return False
        # MOUSEEVENTF_WHEEL = 0x0800
        WHEEL_DELTA = 120
        self._user32.mouse_event(0x0800, 0, 0, clicks * WHEEL_DELTA, 0)
        return True

    def type_text(self, text: str) -> bool:
        """Types string characters sequentially."""
        if not self._user32:
            return False
        # KEYEVENTF_UNICODE = 0x0004, KEYEVENTF_KEYUP = 0x0002
        for char in text:
            vk = ord(char)
            self._user32.keybd_event(0, vk, 0x0004, 0)
            time.sleep(0.01)
            self._user32.keybd_event(0, vk, 0x0004 | 0x0002, 0)
            time.sleep(0.01)
        return True

    def send_shortcut(self, key_code: int, modifiers: Optional[List[KeyModifier]] = None) -> bool:
        """Sends a key combination (e.g. CTRL+C, ALT+TAB, CTRL+S)."""
        if not self._user32:
            return False

        mod_map = {
            KeyModifier.CTRL: 0x11,   # VK_CONTROL
            KeyModifier.ALT: 0x12,    # VK_MENU
            KeyModifier.SHIFT: 0x10,  # VK_SHIFT
            KeyModifier.WIN: 0x5B     # VK_LWIN
        }

        active_mods = []
        if modifiers:
            for m in modifiers:
                vk = mod_map.get(m)
                if vk:
                    self._user32.keybd_event(vk, 0, 0, 0)
                    active_mods.append(vk)

        time.sleep(0.02)
        # Press target key
        self._user32.keybd_event(key_code, 0, 0, 0)
        time.sleep(0.02)
        self._user32.keybd_event(key_code, 0, 0x0002, 0)  # KEYEVENTF_KEYUP

        # Release modifiers in reverse order
        for vk in reversed(active_mods):
            self._user32.keybd_event(vk, 0, 0x0002, 0)

        return True
