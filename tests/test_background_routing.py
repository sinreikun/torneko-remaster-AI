"""Window-addressed input must never fall back to global keyboard injection."""
import unittest
from types import SimpleNamespace
from autoplayer.windows import WindowsGame

class BackgroundRoutingTests(unittest.TestCase):
    def test_key_up_uses_original_window_and_scan_flags(self):
        calls=[]
        backend=WindowsGame.__new__(WindowsGame)
        backend.hwnd=10; backend.input_target=20; backend.held={0x44}
        backend.u=SimpleNamespace(MapVirtualKeyW=lambda *_:32,
                                  PostMessageW=lambda *args:calls.append(args) or True)
        backend._send(0x44)
        backend.hwnd=30
        backend.release_all()
        self.assertEqual([x[:3] for x in calls],[(20,0x100,0x44),(20,0x101,0x44)])
        self.assertEqual(calls[0][3],1|(32<<16))
        self.assertEqual(calls[1][3],1|(32<<16)|(1<<30)|(1<<31))
        self.assertIsNone(backend.input_target)
        self.assertEqual(backend.held,set())

    def test_failed_key_up_retains_target_for_retry(self):
        backend=WindowsGame.__new__(WindowsGame)
        backend.hwnd=10;backend.input_target=20;backend.held={0x44}
        backend.u=SimpleNamespace(MapVirtualKeyW=lambda *_:32,PostMessageW=lambda *_:False)
        with self.assertRaises(RuntimeError): backend.release_all()
        self.assertEqual(backend.held,{0x44})
        self.assertEqual(backend.input_target,20)
