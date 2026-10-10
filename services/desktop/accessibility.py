"""
HOOD Accessibility Tree & UI Automation Engine
Discovers and interacts with native UI controls via semantic roles and properties,
preferring semantic selectors over coordinate clicking.
"""

from __future__ import annotations
import sys
import ctypes
from ctypes import wintypes
from typing import List, Optional, Tuple, Dict, Any

from services.desktop.contracts import UIElementInfo, UIElementRole
from services.desktop.windows_backend import WindowsNativeBackend


class AccessibilityEngine:
    """Discovers native and custom UI elements, building semantic hierarchies."""

    def __init__(self, backend: Optional[WindowsNativeBackend] = None):
        self.backend = backend or WindowsNativeBackend()
        self._user32 = getattr(self.backend, "_user32", None)

    def inspect_window_elements(self, hwnd: int) -> List[UIElementInfo]:
        """Discovers direct and child controls of a window using Win32 child enumeration."""
        if not self._user32 or hwnd <= 0:
            return []

        elements: List[UIElementInfo] = []

        def child_cb(chwnd, lparam):
            if self._user32.IsWindowVisible(chwnd):
                class_buf = ctypes.create_unicode_buffer(256)
                self._user32.GetClassNameW(chwnd, class_buf, 256)
                cls_name = class_buf.value.strip()

                length = self._user32.GetWindowTextLengthW(chwnd)
                txt = ""
                if length > 0:
                    txt_buf = ctypes.create_unicode_buffer(length + 1)
                    self._user32.GetWindowTextW(chwnd, txt_buf, length + 1)
                    txt = txt_buf.value.strip()

                rect = wintypes.RECT()
                self._user32.GetWindowRect(chwnd, ctypes.byref(rect))
                bbox = (rect.left, rect.top, rect.right, rect.bottom)

                role = self._map_class_to_role(cls_name)
                is_enabled = bool(self._user32.IsWindowEnabled(chwnd))

                elements.append(UIElementInfo(
                    hwnd=int(chwnd),
                    role=role,
                    name=txt or cls_name,
                    value=txt if role in (UIElementRole.TEXT_FIELD, UIElementRole.BUTTON) else None,
                    bounding_box=bbox,
                    is_enabled=is_enabled,
                    is_focusable=True,
                    is_visible=True,
                    class_name=cls_name,
                    control_type=role.value
                ))
            return True

        CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self._user32.EnumChildWindows(hwnd, CB(child_cb), 0)
        return elements

    def find_element(
        self,
        hwnd: int,
        role: Optional[UIElementRole] = None,
        name: Optional[str] = None,
        class_name: Optional[str] = None
    ) -> Optional[UIElementInfo]:
        """Finds first matching element within a window matching given criteria."""
        elements = self.inspect_window_elements(hwnd)
        for el in elements:
            if role and el.role != role:
                continue
            if name and name.lower() not in el.name.lower():
                continue
            if class_name and class_name.lower() not in el.class_name.lower():
                continue
            return el
        return None

    def find_all_elements(
        self,
        hwnd: int,
        role: Optional[UIElementRole] = None,
        name: Optional[str] = None
    ) -> List[UIElementInfo]:
        elements = self.inspect_window_elements(hwnd)
        matches = []
        for el in elements:
            if role and el.role != role:
                continue
            if name and name.lower() not in el.name.lower():
                continue
            matches.append(el)
        return matches

    def _map_class_to_role(self, class_name: str) -> UIElementRole:
        cls = class_name.lower()
        if "button" in cls:
            return UIElementRole.BUTTON
        elif "edit" in cls or "textbox" in cls or "richedit" in cls:
            return UIElementRole.TEXT_FIELD
        elif "combobox" in cls or "listbox" in cls or "listview" in cls:
            return UIElementRole.LIST
        elif "static" in cls or "label" in cls:
            return UIElementRole.PANE
        elif "menu" in cls:
            return UIElementRole.MENU
        elif "tab" in cls:
            return UIElementRole.TAB
        elif "scroll" in cls:
            return UIElementRole.SCROLL_CONTAINER
        elif "dialog" in cls or "#32770" in cls:
            return UIElementRole.DIALOG
        elif "chrome_renderwidgethost" in cls:
            return UIElementRole.PANE
        return UIElementRole.UNKNOWN
