"""The desktop layer must never invent a screenshot or a display size.

Before the fix, a failed or impossible capture returned a solid gray BMP that was hashed and
saved as "desktop evidence", and the display size defaulted to 1920x1080 off Windows.
"""
import struct

import pytest

from services.desktop.screen_observer import ScreenObserver
from services.desktop.windows_backend import DesktopUnavailable, WindowsNativeBackend, encode_top_down_bmp


class _NoDesktop(WindowsNativeBackend):
    """Behaves like the real backend on a host without Windows handles."""

    def __init__(self):
        super().__init__()
        self.is_windows = False
        self._user32 = None
        self._gdi32 = None


class _FakeUser32:
    def __init__(self, w, h):
        self.size = (w, h)

    def GetSystemMetrics(self, index):
        return self.size[index]

    def __getattr__(self, name):  # GetDC/ReleaseDC/OpenDesktopW... return harmless handles
        return lambda *a, **k: 1


class _FailingGdi32:
    """Every call succeeds except GetDIBits, which returns 0 lines (capture failed)."""

    def GetDIBits(self, *a, **k):
        return 0

    def __getattr__(self, name):
        return lambda *a, **k: 1


def test_capture_without_desktop_raises_and_size_is_unknown():
    backend = _NoDesktop()
    assert backend.get_screen_dimensions() == (0, 0)
    with pytest.raises(DesktopUnavailable):
        backend.capture_screen_bmp()


def test_failed_pixel_read_raises_instead_of_gray_image():
    backend = _NoDesktop()
    backend.is_windows = True
    backend._user32 = _FakeUser32(64, 32)
    backend._gdi32 = _FailingGdi32()
    with pytest.raises(DesktopUnavailable, match="GetDIBits"):
        backend.capture_screen_bmp()


def test_zero_display_size_is_not_replaced_by_a_guess():
    backend = _NoDesktop()
    backend._user32 = _FakeUser32(0, 0)
    assert backend.get_screen_dimensions() == (0, 0)


def test_observer_writes_nothing_when_no_desktop(tmp_path):
    observer = ScreenObserver(backend=_NoDesktop(), artifact_dir=tmp_path / "evidence")
    for capture in (True, False):
        with pytest.raises(DesktopUnavailable):
            observer.observe(capture_image=capture)
    assert list((tmp_path / "evidence").iterdir()) == []


def test_bmp_header_is_top_down_and_sized():
    w, h = 3, 2
    stride = 12  # 3 px * 3 bytes = 9, padded to 12
    rows = bytes([1] * stride) + bytes([2] * stride)
    data = encode_top_down_bmp(w, h, rows)
    assert data[:2] == b"BM"
    assert struct.unpack_from("<I", data, 2)[0] == len(data)
    assert struct.unpack_from("<ii", data, 18) == (3, -2)  # negative height = row 0 drawn at the top
    assert data[54:] == rows
    with pytest.raises(ValueError):
        encode_top_down_bmp(w, h, rows[:-1])
