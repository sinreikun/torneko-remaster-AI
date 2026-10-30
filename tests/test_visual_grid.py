"""Image lattice primitives, not a substitute game."""
import copy,unittest
from PIL import Image,ImageDraw
from autoplayer.nextgen.visual_grid import glyph_grid,selection_elements

class VisualGridContracts(unittest.TestCase):
    def image(self,offset=(0,0),inverse=False):
        image=Image.new('RGB',(320,180),'white' if inverse else 'black');draw=ImageDraw.Draw(image)
        for row in range(5):
            for col in range(6):
                x=45+col*28+offset[0];y=45+row*20+offset[1]
                draw.rectangle((x,y,x+6,y+8),outline='black' if inverse else 'white',width=2)
        return image
    def test_cells_are_measured_and_anonymous_under_translation_and_inversion(self):
        for offset,inverse in [((0,0),False),((8,6),False),((0,0),True)]:
            g=glyph_grid(self.image(offset,inverse),'frame')
            self.assertEqual(g['status'],'hypothesis');self.assertGreaterEqual(len(g['cells']),24)
            self.assertTrue(all(c['text'] is None and c['source_frames']==['frame'] for c in g['cells']))
            self.assertEqual(g['coordinate_source'],'measured_contrast_components_not_ocr')
    def test_empty_and_non_lattice_do_not_create_cells(self):
        self.assertEqual(glyph_grid(Image.new('RGB',(320,180),'gray'),'f')['status'],'unknown')
    def test_tall_ocr_block_is_retained_as_raw_text_but_not_assigned_to_one_cell(self):
        cells=glyph_grid(self.image(),'f')['cells'];b=cells[0]['bbox']
        tall={'id':'column','text':'unseparated column','bbox':[b[0],b[1],b[2],b[1]+.5],'center':[b[0],b[1]+.25]}
        texts=[tall];original=copy.deepcopy(texts);targets=selection_elements(texts,cells)
        self.assertEqual(texts,original);self.assertNotIn('column',[e['id'] for e in targets]);self.assertTrue(all(e['text'] is None for e in targets))
    def test_input_can_link_anonymous_cells_without_splitting_column_text(self):
        import hashlib,io
        from autoplayer.nextgen.ui_vision import UIVision
        vision=UIVision();previous=None;events=[];state=None
        for i in range(7):
            row=i%5;image=self.image();d=ImageDraw.Draw(image);y=45+row*20;d.polygon([(31,y),(36,y+4),(31,y+8)],fill='white')
            out=io.BytesIO();image.save(out,format='PNG');png=out.getvalue();sha=hashlib.sha256(png).hexdigest()
            native={'captured_at':i,'lines':[{'text':'unseparated','words':[{'text':'unseparated','box':[45,45,7,90]}]}]}
            ui=vision.measure(png,native,{'ui_surface_observed':True});state={'id':'ui','context':'ui','frame_sha256':sha,'ui_visual':ui,'interface_hypothesis':True,'interface_observation':{'rectangles':[[.05,.1,.9,.95]]}}
            if previous:events.append(vision.learn(previous,state,{'id':'down','keys':['x'],'hold_ms':80},{'os_submission':'accepted'},i))
            previous=state
        self.assertTrue(any(e.get('status')=='candidate_selection_movement' for e in events))
        self.assertEqual(state['ui_visual']['selection']['status'],'supported_association')
        self.assertTrue(state['ui_visual']['selection']['element_id'].startswith('pixel_cell:'));self.assertIsNone(state['ui_visual']['selection']['quote'])
    def test_anonymous_position_can_drive_capability_learning_without_ocr_row_index(self):
        from autoplayer.nextgen.meta import MetaController
        from autoplayer.nextgen.capabilities import test_effect
        from test_nextgen import measured
        a=measured();a.update(interface_hypothesis=True,selection_observation={'status':'supported_association','element_id':'pixel_cell:A','line_index':None},ui_visual={'active':True})
        b=copy.deepcopy(a);b['selection_observation']['element_id']='pixel_cell:B'
        self.assertEqual(MetaController.bootstrap_capability(a),'change_selection');self.assertTrue(test_effect(a,b,'change_selection',[]))
        a['selection_observation']['status']='unknown';self.assertEqual(MetaController.bootstrap_capability(a),'inspect')
