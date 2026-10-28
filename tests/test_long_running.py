"""UI bounds and cache contracts; no simulated game or Windows input."""
import copy,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from PIL import Image,ImageFilter,ImageDraw
from autoplayer.dashboard import append_text
from autoplayer.world_model import Inventory
from autoplayer.core import Settings
from autoplayer.memory import MemoryStore
from autoplayer.combat import CombatExperiments

class LongRunningTests(unittest.TestCase):
    def test_log_remains_bounded_even_when_reading_old_lines(self):
        w=Mock();w.index.side_effect=lambda i:'40.0' if i=='@0,0' else '6000.0';w.xview.return_value=(.2,.8)
        append_text(w,'new')
        w.delete.assert_called_once_with('1.0','4801.0');w.yview.assert_called_with('1.0');w.see.assert_not_called()
    def test_main_menu_cursor_ignores_arrow_like_hud_pixels(self):
        image=Image.new('RGB',(300,220),'black');draw=ImageDraw.Draw(image)
        # Two actual white triangle shapes, only one belongs to menu entries.
        for x,y in [(60,20),(60,90)]:draw.polygon([(x,y),(x,y+19),(x+10,y+10)],fill='white')
        lines=[{'text':t,'words':[{'box':[80,y,40,20]}]} for t,y in [('HP15/15',20),('道具',90),('マップ',120),('足元',150),('その他',180)]]
        stream=io.BytesIO();image.save(stream,format='PNG')
        self.assertEqual(Inventory.cursor_line({'status':'ok','lines':lines},stream.getvalue()),'道具')
    def test_recorded_cursor_inside_ocr_box_is_detected_and_moves_toward_tools(self):
        from autoplayer.operational_control import OperationalControl
        folder=Path(__file__).parent/'fixtures'
        data=json.loads((folder/'menu-cursor-1793055833910841700.json').read_text())
        image=(folder/'menu-cursor-1793055833910841700.png').read_bytes()
        self.assertEqual(Inventory.cursor_line(data['ocr'],image),data['expected'])
        # Full path from the current visual selection to one directional input.
        import hashlib
        native=copy.deepcopy(data['ocr']);native['frame_sha256']=hashlib.sha256(image).hexdigest()
        native['_visual_selection']={'text':data['expected'],'frame_sha256':native['frame_sha256']}
        from autoplayer.controls import REFERENCE
        actor=OperationalControl(Settings(),Mock(bindings=REFERENCE['keys']),Mock())
        self.assertEqual(actor._cursor_toward(native,'道具'),'w')
        without_cursor=Image.open(io.BytesIO(image)).copy()
        ImageDraw.Draw(without_cursor).rectangle((20,65,40,100),fill='black')
        negative=io.BytesIO();without_cursor.save(negative,format='PNG')
        self.assertIsNone(Inventory.cursor_line(data['ocr'],negative.getvalue()))
        # The same recorded pixels at another capture scale remain detectable.
        resized=Image.open(io.BytesIO(image)).resize((560,220));stream=io.BytesIO();resized.save(stream,format='PNG')
        enlarged=copy.deepcopy(data['ocr'])
        for line in enlarged['lines']:
            for word in line['words']:word['box']=[v*2 for v in word['box']]
        self.assertEqual(Inventory.cursor_line(enlarged,stream.getvalue()),data['expected'])

    def test_radius_one_dilation_preserves_larger_kernel_pixels(self):
        image=Image.frombytes('L',(47,31),bytes((x*37)%256 for x in range(47*31)))
        for kernel in (3,5,7,9):
            repeated=image
            for _ in range((kernel-1)//2):repeated=repeated.filter(ImageFilter.MaxFilter(3))
            self.assertEqual(repeated.tobytes(),image.filter(ImageFilter.MaxFilter(kernel)).tobytes())
    def test_battle_summary_cache_is_independent_and_invalidated_on_new_message(self):
        with tempfile.TemporaryDirectory() as d:
            memory=MemoryStore(Path(d)/'m.sqlite',Settings())
            try:
                c=CombatExperiments(memory,Settings());queries=[];memory.db.set_trace_callback(queries.append)
                first=c.experience();first['observations'].append({'invalid':'caller mutation'})
                self.assertFalse(c.experience()['observations'])
                self.assertEqual(sum('GROUP BY enemy' in q for q in queries),1)
                row={'executed':True,'input_receipt':{'os_submission':'accepted'},'observed_keys':['num1']}
                c.observe_messages(row,{},[({'status':'ok','frame_sha256':'new','lines':[{'text':'2ポイントのダメージを与えた。'}]},'new')])
                self.assertEqual(c.experience()['observations'][0]['samples'],1)
                self.assertEqual(sum('GROUP BY enemy' in q for q in queries),2)
            finally:memory.close()
