"""Image measurements and user-assumption contracts, without a game simulation."""
import io
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from PIL import Image, ImageDraw
from autoplayer.exterior_screen import white_frames, assumed_change
from autoplayer.mapless_navigation import MaplessNavigation
from autoplayer.telemetry import RecognitionState

def png(image):
    out=io.BytesIO();image.save(out,format='PNG');return out.getvalue()

def block_image():return Image.new('RGB',(640,360),'#777755')

class ScreenMeasurementTests(unittest.TestCase):
    def test_rectangular_white_frame_with_dark_interior(self):
        for scale,thickness in ((1,3),(2,1),(2,2)):
            image=Image.new('RGB',(640*scale,360*scale),'#777755')
            ImageDraw.Draw(image).rectangle((50*scale+1,180*scale+1,580*scale-1,320*scale-1),fill='#101010',outline='white',width=thickness)
            result=white_frames(png(image));self.assertTrue(result['visible']);self.assertTrue(result['rectangles'])
    def test_light_surface_or_white_text_is_not_a_frame(self):
        image=block_image();d=ImageDraw.Draw(image);d.rectangle((80,80,300,230),fill='white');d.text((20,20),'HP 10/15',fill='white')
        self.assertFalse(white_frames(png(image))['visible'])
        self.assertFalse(white_frames(png(Image.new('RGB',(640,360),'white')))['visible'])
    def test_tiny_rectangle_like_logged_false_frame_is_not_interface(self):
        image=block_image();ImageDraw.Draw(image).rectangle((283,120,330,138),fill='black',outline='white',width=2)
        self.assertFalse(white_frames(png(image))['visible'])
    def test_white_frame_on_either_side_excludes_geometry_update(self):
        clear=png(block_image());image=block_image();ImageDraw.Draw(image).rectangle((40,60,400,200),fill='black',outline='white',width=3);framed=png(image)
        for before,after in ((clear,framed),(framed,clear)):
            self.assertEqual(assumed_change(before,after,before)['status'],'interface')
    def test_small_animation_is_blocked_under_user_assumption(self):
        image=block_image();after=image.copy();ImageDraw.Draw(after).rectangle((310,170,325,185),fill='red')
        self.assertEqual(assumed_change(png(image),png(after),png(image))['status'],'assumed_blocked')
    def test_large_change_is_assumed_movement_without_translation(self):
        image=block_image();after=image.copy();ImageDraw.Draw(after).rectangle((100,120,450,280),fill='#224477')
        result=assumed_change(png(image),png(after),png(image));self.assertEqual(result['status'],'assumed_moved')
        self.assertIn('user_assumption',result['basis'])
    def test_natural_change_is_measured_separately(self):
        a=png(block_image());b=png(Image.new('RGB',(640,360),'#224477'))
        self.assertEqual(assumed_change(a,b,b)['status'],'assumed_blocked')
    def test_unreadable_image_is_not_an_obstacle(self):self.assertEqual(assumed_change(b'bad',b'bad',b'bad')['status'],'unknown')

class AssumedMapTests(unittest.TestCase):
    def make(self,visible=False):
        cfg=SimpleNamespace(window_title='トルネコ',allowed_keys=['w','a','s','d','num1','num5'],key_bindings={})
        nav=MaplessNavigation(cfg)
        nav.prepare({'minimap':{'status':'unavailable'},'exterior_action_policy':{'active':True,'mode':'exit_exploration'},'white_frame_evidence':{'visible':visible}},{},{},0)
        return nav
    def record(self,nav,keys,status):
        row={'observed_keys':keys,'executed':True,'input_receipt':{'os_submission':'accepted'}}
        with patch('autoplayer.exterior_screen.assumed_change',return_value={'status':status,'basis':'user_assumption_screen_change','reason':'contract'}):
            return nav.record(b'before',b'after',b'neutral',row)
    def test_first_large_change_moves_and_preserves_previous_floor(self):
        nav=self.make();result=self.record(nav,['w'],'assumed_moved')
        self.assertEqual(nav.pose,(0,-1));self.assertEqual(nav.nodes[(0,0)]['surface'],'floor')
        self.assertEqual(len(nav.nodes),2);self.assertEqual(nav.samples,[]);self.assertFalse(nav.uncertain)
        state=RecognitionState();state.update({'mapless_navigation':result});self.assertEqual(state.grid()[(27,17)],'tentative')
    def test_first_small_change_marks_obstacle_and_allows_confirm(self):
        nav=self.make();self.record(nav,['w'],'assumed_blocked')
        self.assertEqual(nav.pose,(0,0));self.assertIn((0,-1),nav.obstacles)
        self.assertEqual(nav.propose(allow_facing=False)[0],['num1'])
        self.record(nav,['num1'],'assumed_moved');self.assertEqual(nav.pose,(0,0))
        self.assertEqual(nav.propose(allow_facing=False)[0],['w'])
        self.record(nav,['w'],'assumed_moved');self.assertEqual(nav.pose,(0,-1));self.assertNotIn((0,-1),nav.obstacles)
    def test_menu_before_or_after_does_not_create_wall_or_floor(self):
        nav=self.make();self.record(nav,['w'],'interface')
        self.assertEqual(nav.pose,(0,0));self.assertEqual(nav.obstacles,set());self.assertEqual(len(nav.nodes),1)
        self.assertFalse(self.make(visible=True).active)
    def test_unknown_image_does_not_create_wall(self):
        nav=self.make();self.record(nav,['w'],'unknown');self.assertEqual(nav.obstacles,set());self.assertTrue(nav.uncertain)
    def test_confirm_alone_never_moves_the_character_on_map(self):
        nav=self.make();self.record(nav,['num1'],'assumed_moved');self.assertEqual(nav.pose,(0,0))
    def test_assumption_map_not_relocated_by_identical_background(self):
        nav=self.make();nav.pose=(2,1);nav.landmarks={(0,0):[20]*448};nav.uncertain=True
        with patch.object(nav,'signature',return_value=[20]*448):nav.locate(b'')
        self.assertEqual(nav.pose,(2,1));self.assertTrue(nav.uncertain)

    def test_white_frame_suspends_but_preserves_monitor_floor(self):
        nav=self.make();self.record(nav,['w'],'assumed_moved')
        snapshot=nav.prepare({'minimap':{'status':'unavailable'},'exterior_action_policy':{'active':True,'mode':'close_existing_menu'},'white_frame_evidence':{'visible':True}},{},{},0)
        self.assertFalse(snapshot['active']);self.assertTrue(snapshot['display_retained'])
        state=RecognitionState();state.update({'mapless_navigation':snapshot})
        self.assertEqual(state.grid()[(27,17)],'tentative')
    def test_clear_frame_overrides_unverified_dialog_classification(self):
        from autoplayer.core import Settings
        from autoplayer.exterior_actions import ExteriorActions
        cfg=Settings();scope=ExteriorActions(cfg,MaplessNavigation(cfg))
        evidence=scope.observe({'white_frame_evidence':{'visible':False}}, {'lines':[{'text':'先ほどの会話の続きであると想定している'}]},'gameplay')
        evidence=scope.observe({'white_frame_evidence':{'visible':False}}, {'lines':[{'text':'先ほどの会話の続きであると想定している'}]},'dialog')
        self.assertEqual(evidence['mode'],'exit_exploration');self.assertIn(['w'],evidence['actions'])
