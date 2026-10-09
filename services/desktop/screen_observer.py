"""
HOOD Screen Observer & Privacy Scrubber
Captures screen regions, generates SHA256 integrity hashes, discovers elements,
and redacts sensitive credentials/patterns from logs and observations.
"""

from __future__ import annotations
import os
import hashlib
from typing import Optional, List, Tuple
from pathlib import Path
from datetime import datetime, timezone

from packages.logging.redactor import redact_string
from services.desktop.contracts import ScreenObservation, WindowInfo, UIElementInfo
from services.desktop.windows_backend import DesktopUnavailable, WindowsNativeBackend
from services.desktop.accessibility import AccessibilityEngine


class ScreenObserver:
    """Observes the visual desktop state while strictly sanitizing secrets."""

    def __init__(
        self,
        backend: Optional[WindowsNativeBackend] = None,
        accessibility: Optional[AccessibilityEngine] = None,
        artifact_dir: Optional[Path] = None
    ):
        self.backend = backend or WindowsNativeBackend()
        self.accessibility = accessibility or AccessibilityEngine(self.backend)
        self.artifact_dir = (artifact_dir or (Path.cwd() / "artifacts" / "desktop_evidence")).resolve()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def observe(self, capture_image: bool = True) -> ScreenObservation:
        """Captures full screen observation, active window, and semantic elements.

        Raises ``DesktopUnavailable`` when there is no real desktop to observe; nothing is
        written and no stand-in observation is produced."""
        w, h = self.backend.get_screen_dimensions()
        if w <= 0 or h <= 0:
            raise DesktopUnavailable("No Windows desktop is available on this host to observe")
        active_window = self.backend.get_foreground_window()

        elements: List[UIElementInfo] = []
        if active_window and active_window.hwnd > 0:
            elements = self.accessibility.inspect_window_elements(active_window.hwnd)

        screenshot_path = None
        img_hash = ""
        if capture_image:
            bmp_bytes = self.backend.capture_screen_bmp()
            img_hash = hashlib.sha256(bmp_bytes).hexdigest()
            fname = f"screen_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{img_hash[:8]}.bmp"
            out_file = self.artifact_dir / fname
            with open(out_file, "wb") as f:
                f.write(bmp_bytes)
            screenshot_path = str(out_file)

        # Sanitize any element names or text values
        redaction_count = 0
        has_redacted = False
        for el in elements:
            if el.name:
                cleaned = redact_string(el.name)
                if cleaned != el.name:
                    el.name = cleaned
                    has_redacted = True
                    redaction_count += 1
            if el.value:
                cleaned_val = redact_string(el.value)
                if cleaned_val != el.value:
                    el.value = cleaned_val
                    has_redacted = True
                    redaction_count += 1

        return ScreenObservation(
            screen_width=w,
            screen_height=h,
            active_window=active_window,
            screenshot_path=screenshot_path,
            image_hash_sha256=img_hash,
            discovered_elements=elements,
            has_redacted_secrets=has_redacted,
            redaction_count=redaction_count
        )

    def compute_state_hash(self) -> str:
        """Computes rapid SHA256 digest of current foreground window and discovered elements."""
        active = self.backend.get_foreground_window()
        if not active:
            return hashlib.sha256(b"no_window").hexdigest()
        elements = self.accessibility.inspect_window_elements(active.hwnd)
        summary = f"{active.hwnd}:{active.title}:{active.rect}:" + ";".join(
            f"{e.role}:{e.name}:{e.bounding_box}" for e in elements
        )
        return hashlib.sha256(summary.encode("utf-8")).hexdigest()
