"""Capture reuse/priority and bounded display costs, without a game environment."""
import unittest
from unittest.mock import Mock
from PIL import Image
from autoplayer.live_preview import PreviewBudget,PreviewFrameCache

class PreviewBudgetTests(unittest.TestCase):
    def test_viewport_bounds_avoid_full_size_transport_for_small_display(self):
        budget=PreviewBudget()
        self.assertEqual(budget.size((640,360)),(640,360))
        w,h=budget.size((3000,2000));self.assertLessEqual(w,1280);self.assertLessEqual(h,960)
    def test_slow_display_preserves_resolution_and_has_bounded_rate(self):
        budget=PreviewBudget()
        budget.sample(.09,.03,2)
        self.assertEqual(budget.size((1000,700)),(1000,700));self.assertGreater(budget.period,.05)
        for t in range(3,30):budget.sample(.2,.05,t)
        self.assertEqual(budget.size((1000,700)),(1000,700));self.assertLessEqual(budget.period,.125)
        for t in range(30,110):budget.sample(.001,.001,t)
        self.assertEqual(budget.scale,1);self.assertEqual(budget.period,.05)
    def test_preview_never_reuses_analysis_frame(self):
        cache=PreviewFrameCache();clock=[1.];capture=Mock(side_effect=lambda hwnd:Image.new('RGB',(1600,900)))
        now=lambda:clock[0]
        first,at,sequence=cache.frame(1,capture,now,now)
        clock[0]+=.02
        reused,at2,seq2=cache.frame(1,capture,now,now,preview=True)
        self.assertIsNot(first,reused);self.assertNotEqual(sequence,seq2)
        self.assertEqual(capture.call_count,2)
        live,live_at,live_seq=cache.frame(1,capture,now,now,preview=True)
        self.assertIs(live,reused);self.assertEqual(live_at,at2);self.assertEqual(live_seq,seq2)
        self.assertEqual(capture.call_count,2)
        # Input-bound observation requests always refresh, even within 40ms.
        cache.frame(1,capture,now,now,preview=False);self.assertEqual(capture.call_count,3)
        clock[0]+=.041;cache.frame(1,capture,now,now,preview=True);self.assertEqual(capture.call_count,4)
        cache.frame(2,capture,now,now,preview=True);self.assertEqual(capture.call_count,5)
        self.assertLessEqual(cache.image.width,1280)
    def test_waiting_observer_prevents_another_preview_from_acquiring_lock(self):
        from autoplayer.windows import WindowsGame
        game=WindowsGame.__new__(WindowsGame);game.capture_waiters=1;game.capture_lock=Mock();game.window_capture=Mock()
        self.assertIsNone(game.capture_preview())
        game.capture_lock.acquire.assert_not_called();game.window_capture.grab_preview.assert_not_called()

    def test_preview_holds_live_frame_when_observation_owns_capture(self):
        from autoplayer.ui import Application
        app=Application.__new__(Application)
        app.preview_done=Mock()
        app.preview_done.is_set.side_effect=[False,True]
        app.backend=Mock()
        app.backend.capture_preview.return_value=None
        app.backend.last_frame=b'map-only observation'
        app.backend.last_capture_at=9999999999
        live=(object(),1.)
        app.frame_slot=live
        app.preview_loop()
        self.assertIs(app.frame_slot,live)
        app.backend.capture.assert_not_called()
