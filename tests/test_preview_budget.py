"""Capture reuse/priority and bounded display costs, without a game environment."""
import unittest
from unittest.mock import Mock
from PIL import Image
from autoplayer.live_preview import PreviewBudget,PreviewFrameCache

class PreviewBudgetTests(unittest.TestCase):
    def test_viewport_bounds_avoid_full_size_transport_for_small_display(self):
        budget=PreviewBudget()
        self.assertEqual(budget.size((640,360)),(640,360))
        w,h=budget.size((3000,2000));self.assertLessEqual(w,1024);self.assertLessEqual(h,768)
    def test_slow_display_reduces_resolution_first_and_has_bounded_rate(self):
        budget=PreviewBudget()
        budget.sample(.09,.03,2)
        self.assertLess(budget.scale,1);self.assertEqual(budget.period,.05)
        for t in range(3,30):budget.sample(.2,.05,t)
        self.assertGreaterEqual(budget.scale,.55);self.assertLessEqual(budget.period,.125)
        for t in range(30,110):budget.sample(.001,.001,t)
        self.assertEqual(budget.scale,1);self.assertEqual(budget.period,.05)
    def test_only_recent_same_target_preview_can_reuse_analysis_frame(self):
        cache=PreviewFrameCache();clock=[1.];capture=Mock(side_effect=lambda hwnd:Image.new('RGB',(1600,900)))
        now=lambda:clock[0]
        first,at,sequence=cache.frame(1,capture,now,now)
        clock[0]+=.02
        reused,at2,seq2=cache.frame(1,capture,now,now,preview=True)
        self.assertIs(first,reused);self.assertEqual(at,at2);self.assertEqual(sequence,seq2)
        self.assertEqual(capture.call_count,1)
        # Input-bound observation requests always refresh, even within 40ms.
        cache.frame(1,capture,now,now,preview=False);self.assertEqual(capture.call_count,2)
        clock[0]+=.041;cache.frame(1,capture,now,now,preview=True);self.assertEqual(capture.call_count,3)
        cache.frame(2,capture,now,now,preview=True);self.assertEqual(capture.call_count,4)
        self.assertLessEqual(cache.image.width,1280)
    def test_waiting_observer_prevents_another_preview_from_acquiring_lock(self):
        from autoplayer.windows import WindowsGame
        game=WindowsGame.__new__(WindowsGame);game.capture_waiters=1;game.capture_lock=Mock();game.window_capture=Mock()
        self.assertIsNone(game.capture_preview())
        game.capture_lock.acquire.assert_not_called();game.window_capture.grab_preview.assert_not_called()
