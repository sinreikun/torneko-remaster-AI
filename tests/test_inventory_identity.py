"""Real OCR sequence and numerical color/transport contracts, no demo game."""
import copy,io,json,unittest
from pathlib import Path
from PIL import Image
from autoplayer.inventory_identity import review_key,orange_text
from autoplayer.world_model import Inventory
from autoplayer.live_preview import live_status

class InventoryIdentityTests(unittest.TestCase):
    def test_actual_duplicate_names_keep_distinct_row_ids_across_e_ocr_and_box_jitter(self):
        data=json.loads((Path(__file__).parent/'fixtures/duplicate-inventory-1793097861257173000.json').read_text())
        inv=Inventory();ids=None;keys=None
        for frame in data['frames']:
            native=copy.deepcopy(frame['ocr'])
            native['lines'][frame['selected_line']]['text']='▶'+native['lines'][frame['selected_line']]['text']
            rows=inv.observe(native)['rows']
            shields=sorted([r for r in rows if '青銅' in r['display_name']],key=lambda r:r['box'][1])
            self.assertEqual(len(shields),2)
            current=[r['id'] for r in shields]
            self.assertNotEqual(current[0],current[1])
            if ids is not None:self.assertEqual(current,ids)
            else:ids=current
            if keys is not None:self.assertEqual([review_key(r) for r in shields],keys)
            else:keys=[review_key(r) for r in shields]

    def test_orange_evidence_requires_colored_pixels_not_item_name(self):
        for color,expected in [((230,175,55),'orange_unidentified'),((240,240,240),'orange_not_confirmed')]:
            image=Image.new('RGB',(80,25),(12,12,12))
            for x in range(8,65):
                for y in range(6,18):image.putpixel((x,y),color)
            output=io.BytesIO();image.save(output,format='PNG')
            self.assertEqual(orange_text(output.getvalue(),[0,0,80,25])['status'],expected)

    def test_alias_description_does_not_transfer_to_another_duplicate(self):
        inv=Inventory('session');inv.description_reads=[{'status':'description_text_observed','item_name':'エメラルドの指輪','quotes':['未識別の説明'],'inventory_scope':'session','identity_scope':'inventory_alias','item_id':'one'}]
        row={'id':'two','display_name':'エメラルドの指輪','name_status':'game_alias_unidentified'}
        self.assertIsNone(inv.known_description(row))
        self.assertIsNotNone(inv.known_description({**row,'id':'one'}))

class LivePreviewTests(unittest.TestCase):
    def test_fps_counts_painted_frames_and_reports_stale_capture(self):
        self.assertIn('20.0 FPS',live_status([1.,1.05,1.1],10,1.1,10.04))
        self.assertIn('LIVE',live_status([1.,1.05,1.1],10,1.1,10.04))
        self.assertIn('取得待ち',live_status([1.,1.05,1.1],10,5,12))
        self.assertIn('0.0 FPS',live_status([1.,1.05,1.1],10,5,12))

class LivePriorityTests(unittest.TestCase):
    def test_live_capture_yields_while_gameplay_owns_capture_lock(self):
        from unittest.mock import Mock
        from autoplayer.windows import WindowsGame
        backend=WindowsGame.__new__(WindowsGame)
        backend.capture_lock=Mock();backend.capture_lock.acquire.return_value=False
        backend.window_capture=Mock();backend.ready=Mock()
        self.assertIsNone(backend.capture_preview())
        backend.window_capture.grab_preview.assert_not_called()
        backend.ready.assert_not_called()
