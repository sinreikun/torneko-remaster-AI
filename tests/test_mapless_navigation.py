import unittest
from unittest.mock import patch
from types import SimpleNamespace
from autoplayer.mapless_navigation import MaplessNavigation
from autoplayer.telemetry import RecognitionState

class MaplessTests(unittest.TestCase):
    def make(self):
        n=MaplessNavigation(SimpleNamespace(window_title='トルネコ',allowed_keys=['w','a','s','d','num1','num5'],key_bindings={}))
        n.prepare({'minimap':{'status':'unavailable'}},{'status':'ok','lines':[]},{},0)
        return n
    def row(self,keys):return {'observed_keys':keys,'executed':True,'input_receipt':{'os_submission':'accepted'},'hold_ms':80,'wait_ms':600}
    def record(self,n,keys,evidence):
        with patch('autoplayer.mapless_navigation.motion',return_value=evidence):return n.record(b'before',b'after',b'neutral',self.row(keys))
    def test_receipt_does_not_create_motion(self):
        n=self.make();self.record(n,['w'],{'status':'unknown'});self.assertEqual(n.pose,(0,0));self.assertTrue(n.uncertain)
    def test_camera_motion_and_reverse_route(self):
        n=self.make();self.record(n,['w'],{'status':'translated','shift':[0,6]});self.assertEqual(n.pose,(0,-1))
        for d in ('w','a','d'):n.trials[(n.pose,d)]=3
        proposal=n.propose();self.assertEqual(proposal[0],['s']);self.assertEqual(n.mode,'復路')
    def test_face_confirm_move_bounded(self):
        n=self.make();n.samples=[6,6,6]
        for _ in range(2):self.record(n,['w'],{'status':'stationary'})
        self.assertEqual(n.propose()[0],['num5','w'])
        self.record(n,['num5','w'],{});self.assertEqual(n.propose()[0],['num1'])
        self.record(n,['num1'],{});self.assertEqual(n.propose()[0],['w'])
        self.record(n,['w'],{'status':'stationary'});self.assertIsNone(n.pending);self.assertEqual(n.propose()[0],['d'])
        self.record(n,['w'],{'status':'stationary'});self.assertIsNone(n.pending)
    def test_uncalibrated_stationary_is_not_surrounded_by_obstacles(self):
        n=self.make()
        for direction in ('w','a','s','d'):
            for _ in range(3):self.record(n,[direction],{'status':'stationary'})
        self.assertEqual(n.obstacles,set());self.assertIsNone(n.pending)
        self.assertTrue(n.uncertain);self.assertEqual(n.pose,(0,0))
    def test_exterior_door_probe_skips_turn_in_place(self):
        n=self.make();n.samples=[6,6,6]
        for _ in range(2):self.record(n,['w'],{'status':'stationary'})
        self.assertEqual(n.propose(allow_facing=False)[0],['num1'])
        self.record(n,['num1'],{})
        self.assertEqual(n.propose(allow_facing=False)[0],['w'])
        self.record(n,['w'],{'status':'stationary'})
        self.assertIsNone(n.pending)
    def test_wrong_translation_invalidates_pose(self):
        n=self.make();self.record(n,['w'],{'status':'translated','shift':[6,0]});self.assertTrue(n.uncertain);self.assertEqual(n.pose,(0,0))
    def test_menu_and_real_map_suspend(self):
        n=self.make();n.prepare({'minimap':{'status':'observed'}},{},{},0);self.assertIsNone(n.propose())
    def test_manual_revision_resets(self):
        n=self.make();scope=n.scope;n.prepare({}, {},{},1);self.assertNotEqual(scope,n.scope)
    def test_monitor_separate_from_retained_map(self):
        n=self.make();s=RecognitionState();s.update({'event':'perception','mapless_navigation':n.snapshot()});self.assertEqual(s.grid()[(27,16)],'estimated_pose')
    def test_no_os_acceptance_no_motion(self):
        n=self.make();row=self.row(['w']);row['input_receipt']={};n.record(b'',b'',b'',row);self.assertEqual(len(n.nodes),1)
    def test_relocalization_requires_unique_match(self):
        n=self.make();n.landmarks={(0,0):[20]*448};n.samples=[6,6,6];n.uncertain=True
        with patch.object(n,'signature',return_value=[20]*448):n.locate(b'')
        self.assertFalse(n.uncertain)
    def test_unknown_pose_does_not_extend_old_map_after_measured_input(self):
        n=self.make();self.record(n,['w'],{'status':'stationary'});scope=n.scope
        for _ in range(2):
            self.record(n,['w'],{'status':'translated','shift':[0,6]})
            self.assertTrue(n.uncertain);self.assertEqual(n.pose,(0,0));self.assertEqual(len(n.nodes),1)
        self.record(n,['w'],{'status':'translated','shift':[0,6]})
        self.assertNotEqual(scope,n.scope);self.assertEqual(n.pose,(0,0));self.assertEqual(len(n.nodes),1)
        self.assertFalse(n.uncertain);self.assertEqual(n.samples,[6,6,6])
    def test_uncalibrated_same_view_cannot_restore_position(self):
        n=self.make();n.landmarks={(0,0):[20]*448};n.uncertain=True
        with patch.object(n,'signature',return_value=[20]*448):n.locate(b'')
        self.assertTrue(n.uncertain)
    def test_all_frontiers_closed_does_not_claim_exit(self):
        n=self.make()
        for d in ('w','a','s','d'):n.trials[(n.pose,d)]=3
        self.assertIsNone(n.propose());self.assertEqual(n.mode,'再照合')

class MotionContractTests(unittest.TestCase):
    # Numerical image registration contracts, not a simulated game.
    def png(self,image):
        import io
        out=io.BytesIO();image.save(out,format='PNG');return out.getvalue()
    def test_coherent_texture_translation(self):
        from PIL import Image, ImageChops
        import random
        from autoplayer.mapless_navigation import motion
        rng=random.Random(42);image=Image.new('L',(160,90));image.putdata([rng.randrange(256) for _ in range(14400)])
        after=ImageChops.offset(image,0,6)
        result=motion(self.png(image),self.png(after),self.png(image))
        self.assertEqual(result['status'],'translated');self.assertEqual(result['shift'],[0,6])
    def test_flat_image_not_obstacle_evidence(self):
        from PIL import Image
        from autoplayer.mapless_navigation import motion
        png=self.png(Image.new('L',(160,90),20))
        self.assertEqual(motion(png,png,png)['status'],'unknown')
    def test_natural_translation_not_input_evidence(self):
        from PIL import Image, ImageChops
        import random
        from autoplayer.mapless_navigation import motion
        rng=random.Random(42);image=Image.new('L',(160,90));image.putdata([rng.randrange(256) for _ in range(14400)])
        after=ImageChops.offset(image,0,6)
        self.assertNotEqual(motion(self.png(image),self.png(after),self.png(after))['status'],'translated')
