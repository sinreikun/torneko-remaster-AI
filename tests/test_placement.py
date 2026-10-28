import unittest
from autoplayer.placement import fit_bounds

class PlacementTests(unittest.TestCase):
    def test_position_is_preserved_on_existing_monitor(self):
        saved={'x':120,'y':80,'width':1100,'height':700}
        self.assertEqual(fit_bounds(saved,[(0,0,1920,1040)]),saved)
    def test_negative_monitor_coordinates_are_preserved(self):
        saved={'x':-1500,'y':50,'width':1100,'height':700}
        self.assertEqual(fit_bounds(saved,[(0,0,1920,1040),(-1920,0,0,1040)]),saved)
    def test_removed_monitor_returns_to_visible_primary(self):
        value=fit_bounds({'x':3000,'y':2000,'width':1500,'height':920},[(0,0,1024,728)])
        self.assertGreaterEqual(value['x'],0);self.assertGreaterEqual(value['y'],0)
        self.assertLessEqual(value['x']+value['width'],1024);self.assertLessEqual(value['y']+value['height'],728)
    def test_corrupt_saved_bounds_are_ignored(self):
        self.assertIsNone(fit_bounds({'x':'bad'},[(0,0,1920,1080)]))
