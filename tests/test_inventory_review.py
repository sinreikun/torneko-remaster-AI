"""Recorded OCR and interface contracts; no replacement game or live input."""
import copy,hashlib,io,json,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from PIL import Image
from autoplayer.world_model import Inventory
from autoplayer.operational_control import OperationalControl
from autoplayer.core import Settings,Decision
from autoplayer.controls import REFERENCE

def line(t,y=50,x=100):return {'text':t,'words':[{'box':[x,y,100,24]}]}
def image(color):
    stream=io.BytesIO();Image.new('RGB',(640,360),color).save(stream,format='PNG');return stream.getvalue()
def reading(png,extra=()):
    return {'status':'ok','frame_sha256':hashlib.sha256(png).hexdigest(),'lines':[line('▶薬草'),line('道具',250),line('詳細表示切替',280),line('整理',310),line('1/20',250,300)]+list(extra)}

class InventoryReviewTests(unittest.TestCase):
    def actor(self):return OperationalControl(Settings(),Mock(bindings=REFERENCE['keys']))
    def test_recorded_duplicate_names_have_one_selected_row_and_stable_slot_ids(self):
        data=json.loads((Path(__file__).parent/'fixtures/duplicate-herbs-1793059340143226300.json').read_text())
        n=data['ocr'];herbs=[i for i,l in enumerate(n['lines']) if l['text']=='薬草'];self.assertEqual(len(herbs),2)
        for selected in herbs:
            inv=Inventory()
            with patch.object(Inventory,'cursor_index',return_value=selected):
                a=inv.observe(n);b=inv.observe(n)
            chosen=[r for r in a['rows'] if r['selected']]
            self.assertEqual(len(chosen),1);self.assertEqual(chosen[0]['line_index'],selected)
            self.assertEqual([r['id'] for r in a['rows']],[r['id'] for r in b['rows']])
            self.assertIn('ちからの種',[r['display_name'] for r in a['rows']])
            native=copy.deepcopy(n);native['_visual_selection']={'text':'薬草','line_index':selected,'frame_sha256':n['frame_sha256']}
            self.assertEqual(self.actor()._cursor_toward(native,'薬草',vertical=True),'selected')
    def test_description_toggle_links_current_row_and_excludes_prose_from_possessions(self):
        a,b=image('black'),image('gray');before=reading(a);after=reading(b,[line('HPを25回復する。',80,400)])
        inv=Inventory();inv.observe(before)
        row={'observed_keys':['num7'],'executed':True,'input_receipt':{'os_submission':'accepted'}}
        result=inv.read_description(row,before,after,a,b)
        self.assertEqual(result['status'],'description_text_observed');self.assertEqual(result['item_name'],'薬草')
        self.assertEqual(result['quotes'],['HPを25回復する。']);self.assertFalse(result['effect_verified'])
        self.assertNotIn('HPを25回復する。',[r['display_name'] for r in inv.observe(after)['rows']])
        self.assertEqual(inv.read_description({**row,'executed':False},before,after,a,b),None)
        self.assertEqual(inv.read_description(row,before,after,b,a)['status'],'frame_mismatch_unlinked')
    def test_review_toggles_once_when_text_read_then_moves_without_closing(self):
        actor=self.actor();current={'step':1,'control_revision':0,'operator_guidance':{}}
        rows=[{'display_name':'薬草','selected':True,'box':[100,50,200,74]}, {'display_name':'弟切草','selected':False,'box':[100,80,200,104]}]
        n={'_current_inventory_rows':rows};base=Decision('menu','','','',.8,'continue',[],80,600)
        self.assertEqual(actor._inspect_inventory(base,n,current).keys,['num7'])
        row={'operation':{'selected_keys':['num7'],'selected_source':'interface'},'observed_keys':['num7'],'executed':True,'input_receipt':{'os_submission':'accepted'},'item_description_observation':{'status':'description_text_observed'}}
        actor.feedback(row)
        self.assertEqual(actor._inspect_inventory(base,n,{**current,'step':2}).keys,['s'])
    def test_same_name_at_different_rows_invalidates_stale_cursor_scope(self):
        from autoplayer.supervision import StateLedger
        from test_policy import state
        ledger=StateLedger();n=reading(image('black'));n['lines'][0]['text']='薬草';n['lines'].insert(1,line('薬草',90))
        n['_visual_selection']={'text':'薬草','line_index':0,'frame_sha256':n['frame_sha256']}
        ledger.observe(n,state(),{}, {}, {'revision':0},0,1);first=ledger.current['selection_key']
        n['_visual_selection']={'text':'薬草','line_index':1,'frame_sha256':n['frame_sha256']}
        ledger.observe(n,state(),{}, {}, {'revision':0},0,2)
        self.assertNotEqual(first,ledger.current['selection_key'])

    def test_known_description_is_reused_and_not_toggled_again(self):
        actor=self.actor();current={'step':1,'control_revision':0,'operator_guidance':{}}
        row={'display_name':'薬草','selected':True,'box':[100,50,200,74],'known_description':{'status':'description_text_observed','quotes':['HPを回復する。']}}
        base=Decision('menu','','','',.8,'continue',[],80,600)
        self.assertIsNone(actor._inspect_inventory(base,{'_current_inventory_rows':[row]},current))
    def test_saved_description_survives_restart_but_alias_does_not_cross_scope(self):
        import tempfile
        from autoplayer.world_model import WorldModel
        from autoplayer.memory import MemoryStore
        with tempfile.TemporaryDirectory() as d:
            memory=MemoryStore(Path(d)/'m.sqlite',Settings())
            try:
                world=WorldModel(memory,session='before')
                record={'status':'description_text_observed','item_name':'薬草','quotes':['HPを回復する。'],'identity_scope':'profile_display_name'}
                world.save('item_description_observation',record)
                restored=WorldModel(memory,session='after')
                self.assertEqual(restored.inventory.known_description({'display_name':'薬草'})['quotes'],record['quotes'])
                restored.inventory.description_reads.append({**record,'item_name':'赤い草','identity_scope':'inventory_alias','inventory_scope':'different_adventure'})
                self.assertIsNone(restored.inventory.known_description({'display_name':'赤い草'}))
            finally:memory.close()

    def test_unreadable_toggle_is_bounded_and_rejected_input_does_not_count(self):
        actor=self.actor();current={'step':1,'control_revision':0,'operator_guidance':{}}
        n={'_current_inventory_rows':[{'display_name':'薬草','selected':True,'box':[100,50,200,74]}]};base=Decision('menu','','','',.8,'continue',[],80,600)
        actor._inspect_inventory(base,n,current)
        actor.feedback({'operation':{'selected_keys':['num7'],'selected_source':'interface'},'observed_keys':['num7'],'executed':False,'input_receipt':{'os_submission':'rejected'}})
        self.assertEqual(actor.inventory_review['attempts'],{})
        for _ in range(2):
            self.assertEqual(actor._inspect_inventory(base,n,current).keys,['num7'])
            actor.feedback({'operation':{'selected_keys':['num7'],'selected_source':'interface'},'observed_keys':['num7'],'executed':True,'input_receipt':{'os_submission':'accepted'}})
        self.assertIsNone(actor._inspect_inventory(base,n,current))
