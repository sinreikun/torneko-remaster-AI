"""Native capture contracts; no desktop fallback and no demo game."""
import ctypes
import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace
from autoplayer.window_capture import WindowCapture
from autoplayer.diagnostics import InputDiagnostics,DiagnosticStore
from autoplayer.session import SessionControl
import tempfile
from pathlib import Path

class NativeCaptureTests(unittest.TestCase):
    def api(self,printed=True):
        capture=WindowCapture.__new__(WindowCapture)
        pixels=ctypes.create_string_buffer(bytes([20,70,30,255])*32*32)
        def rect(hwnd,pointer):pointer._obj.right=32;pointer._obj.bottom=32;return True
        def dib(source,info,usage,pointer,section,offset):pointer._obj.value=ctypes.addressof(pixels);return 3
        capture.u=SimpleNamespace(IsHungAppWindow=Mock(return_value=False),GetClientRect=Mock(side_effect=rect),GetDC=Mock(return_value=1),ReleaseDC=Mock(),PrintWindow=Mock(return_value=printed))
        capture.g=SimpleNamespace(CreateCompatibleDC=Mock(return_value=2),CreateDIBSection=Mock(side_effect=dib),SelectObject=Mock(return_value=4),DeleteObject=Mock(),DeleteDC=Mock(),GdiFlush=Mock())
        return capture,pixels
    def test_prints_exact_client_without_desktop_pixels_or_border_crop(self):
        capture,pixels=self.api();image=capture.grab(10)
        self.assertEqual(image.size,(32,32));self.assertEqual(image.getpixel((0,0)),(30,70,20))
        capture.u.PrintWindow.assert_called_once_with(10,2,3)
        capture.g.SelectObject.assert_any_call(2,4);capture.g.DeleteObject.assert_called_once_with(3)
        capture.g.DeleteDC.assert_called_once_with(2);capture.u.ReleaseDC.assert_called_once_with(10,1)
    def test_failed_print_releases_handles_without_falling_back(self):
        capture,pixels=self.api(False)
        with self.assertRaises(RuntimeError):capture.grab(10)
        capture.g.DeleteObject.assert_called_once_with(3);capture.g.DeleteDC.assert_called_once_with(2)
        capture.u.ReleaseDC.assert_called_once_with(10,1)
    def test_hung_target_never_allocates_capture_resources(self):
        capture,pixels=self.api();capture.u.IsHungAppWindow.return_value=True
        with self.assertRaises(RuntimeError):capture.grab(10)
        capture.u.GetDC.assert_not_called()
