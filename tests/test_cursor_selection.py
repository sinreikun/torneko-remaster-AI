"""Cursor geometry and OCR association contracts; no gameplay simulation."""
import copy,hashlib,io,json,unittest
from pathlib import Path
from unittest.mock import Mock
from PIL import Image,ImageDraw
from autoplayer.world_model import Inventory
from autoplayer.core import Controller

def line(text,x,y,w=60,h=24):return {'text':text,'words':[{'box':[x,y,w,h]}]}
def native(lines):return {'status':'ok','lines':lines}

class CursorSelectionTests(unittest.TestCase):
    def test_detached_choice_arrows_follow_labels_at_different_screen_positions(self):
        for x,y in ((20,20),(400,160),(1000,600)):
            n=native([line('▶',x,y,12),line('はい',x+18,y),line('いいえ',x+18,y+32)])
            self.assertEqual(Inventory.cursor_line(n),'はい')
            n['lines'][0]=line('▶',x,y+32,12)
            self.assertEqual(Inventory.cursor_line(n),'いいえ')
    def test_action_popup_has_priority_over_underlying_item_cursor(self):
        for x,y in ((80,80),(700,300)):
            n=native([line('▶薬草',20,30),line('▶',x,y,12),line('飲む',x+18,y),line('投げる',x+18,y+32),line('置く',x+18,y+64)])
            self.assertEqual(Inventory.cursor_line(n),'飲む')
            n['lines'][1]=line('▶',x,y+64,12)
            self.assertEqual(Inventory.cursor_line(n),'置く')
    def test_action_dispatch_uses_active_popup_cursor_not_underlying_selection(self):
        from autoplayer.operational_control import OperationalControl
        from autoplayer.core import Settings
        from autoplayer.controls import REFERENCE
        n=native([line('▶薬草',20,30),line('▶飲む',700,300),line('投げる',700,332),line('置く',700,364)])
        n['frame_sha256']='current'
        n['_visual_selection']={'text':Inventory.cursor_line(n),'frame_sha256':'current'}
        actor=OperationalControl(Settings(),Mock(bindings=REFERENCE['keys']))
        self.assertEqual(actor._cursor_toward(n,'飲む'),'selected')
        self.assertEqual(actor._cursor_toward(n,'置く'),'s')
    def test_recorded_pixel_cursor_can_be_translated_across_the_capture(self):
        folder=Path(__file__).parent/'fixtures'
        data=json.loads((folder/'menu-cursor-1793055833910841700.json').read_text())
        crop=Image.open(folder/'menu-cursor-1793055833910841700.png')
        for x,y in ((0,0),(450,230),(1000,600)):
            image=Image.new('RGB',(1400,900),'black');image.paste(crop,(x,y))
            n=copy.deepcopy(data['ocr'])
            for row in n['lines']:
                for word in row['words']:word['box'][0]+=x;word['box'][1]+=y
            stream=io.BytesIO();image.save(stream,format='PNG')
            self.assertEqual(Inventory.cursor_line(n,stream.getvalue()),data['expected'])

    def test_ambiguous_active_arrows_are_not_accepted(self):
        n=native([line('▶',20,20,12),line('はい',38,20),line('▶',20,52,12),line('いいえ',38,52)])
        self.assertIsNone(Inventory.cursor_line(n))
    def test_current_unknown_framed_menu_exposes_selection_without_classifying_action(self):
        image=Image.new('RGB',(640,360),'black');d=ImageDraw.Draw(image)
        d.rectangle((80,80,420,220),outline='white',width=2)
        d.polygon([(102,112),(102,131),(113,122)],fill='white')
        stream=io.BytesIO();image.save(stream,format='PNG');png=stream.getvalue()
        n=native([line('未知の項目A',120,110),line('未知の項目B',120,145)])
        n['frame_sha256']=hashlib.sha256(png).hexdigest()
        result=Controller._operational_ocr(n,png,{})
        self.assertEqual(result['_visual_selection']['text'],'未知の項目A')
        self.assertNotIn('_visual_selection',n)
        self.assertIs(Controller._operational_ocr(n,b'other-frame',{}),n)
