"""OCR/rectangle coordinate contracts; not images of a replacement game."""
import hashlib,unittest
from pathlib import Path
from unittest.mock import patch
from autoplayer.inventory_description import locate_description
from autoplayer.world_model import Inventory
from test_inventory_review import image,line

class DescriptionLayoutTests(unittest.TestCase):
    def test_panel_can_be_above_below_left_or_right_of_selected_item(self):
        png=image('black');w,h=640,360
        cases=[([.2,.15,.8,.4],(.3,.7)),([.2,.6,.8,.85],(.3,.25)),([.1,.3,.4,.7],(.7,.5)),([.6,.3,.9,.7],(.3,.5))]
        for rect,(sx,sy) in cases:
            with self.subTest(rect=rect):
                native={'lines':[line('▶薬草',int(h*sy),int(w*sx)),line('体力を回復する効果',int(h*(rect[1]+.03)),int(w*(rect[0]+.03)))]}
                frames={'status':'observed','rectangles':[[.05,.05,.95,.95],rect]}
                with patch('autoplayer.exterior_screen.white_frames',return_value=frames):
                    found=locate_description(native,png,0,['薬草'])
                self.assertEqual(found['status'],'located');self.assertEqual(found['line_indices'],[1]);self.assertEqual(found['rectangles'],[rect])
    def test_multiple_unrelated_panels_remain_ambiguous(self):
        native={'lines':[line('▶薬草',160,260),line('回復に関する説明',80,80),line('別枠の未確定テキスト',260,80)]}
        frames={'status':'observed','rectangles':[[.05,.1,.4,.35],[.05,.65,.4,.95]]}
        with patch('autoplayer.exterior_screen.white_frames',return_value=frames):
            result=locate_description(native,image('black'),0,['薬草'])
        self.assertEqual(result['status'],'ambiguous');self.assertEqual(result['quotes'],[])
    def test_item_labels_inside_other_frames_are_not_description(self):
        native={'lines':[line('▶薬草',200,100),line('大きいパン',80,300)]}
        with patch('autoplayer.exterior_screen.white_frames',return_value={'status':'observed','rectangles':[[.4,.1,.9,.4]]}):
            result=locate_description(native,image('black'),0,['薬草','大きいパン'])
        self.assertEqual(result['status'],'not_detected')
    def test_closing_panel_keeps_before_text_with_its_source_frame(self):
        a,b=image('black'),image('gray')
        before={'status':'ok','frame_sha256':hashlib.sha256(a).hexdigest(),'lines':[line('▶薬草',200),line('体力を回復する効果',80,300),line('道具',250),line('1/20',280),line('詳細表示切替',310),line('整理',335)]}
        after={'status':'ok','frame_sha256':hashlib.sha256(b).hexdigest(),'lines':[line('▶薬草',200),line('道具',250),line('1/20',280),line('詳細表示切替',310),line('整理',335)]}
        inv=Inventory();inv.rows=[{'display_name':'薬草','quote':'薬草'}]
        op={'observed_keys':['num7'],'executed':True,'input_receipt':{'os_submission':'accepted'}}
        frames=[{'status':'observed','rectangles':[[.4,.1,.9,.4]]},{'status':'observed','rectangles':[]}]
        with patch('autoplayer.exterior_screen.white_frames',side_effect=frames):
            result=inv.read_description(op,before,after,a,b)
        self.assertEqual(result['status'],'description_text_observed');self.assertEqual(result['quotes'],['体力を回復する効果'])
        self.assertEqual(result['description_source_phase'],'before');self.assertEqual(result['description_source_frame'],before['frame_sha256'])
    def test_unknown_description_in_detected_panel_does_not_become_a_possession(self):
        png=image('black');n={'status':'ok','lines':[line('▶薬草',200),line('まだ知らない品の説明文',80,300),line('道具',250),line('1/20',280),line('詳細表示切替',310),line('整理',335)]}
        frames={'status':'observed','rectangles':[[.4,.1,.9,.4]]}
        with patch('autoplayer.exterior_screen.white_frames',return_value=frames):result=Inventory().observe(n,png)
        self.assertEqual([r['display_name'] for r in result['rows']],['薬草']);self.assertEqual(result['description_panel']['status'],'located')
    def test_unchanged_box_does_not_prove_the_toggle_worked(self):
        png=image('black');n={'status':'ok','frame_sha256':hashlib.sha256(png).hexdigest(),'lines':[line('▶薬草',200),line('体力を回復する効果',80,300),line('道具',250),line('1/20',280),line('詳細表示切替',310),line('整理',335)]}
        inv=Inventory();inv.rows=[{'display_name':'薬草','quote':'薬草'}]
        with patch('autoplayer.exterior_screen.white_frames',return_value={'status':'observed','rectangles':[[.4,.1,.9,.4]]}):
            result=inv.read_description({'observed_keys':['num7'],'executed':True,'input_receipt':{'os_submission':'accepted'}},n,n,png,png)
        self.assertEqual(result['status'],'toggle_without_new_readable_text');self.assertEqual(result['quotes'],[])
