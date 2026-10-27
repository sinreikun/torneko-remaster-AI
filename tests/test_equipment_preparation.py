"""Recorded inventory evidence and scoped input contracts, no game execution."""
import copy,json,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from autoplayer.operational_control import OperationalControl
from autoplayer.core import Settings,Decision
from autoplayer.controls import REFERENCE
from autoplayer.world_model import Inventory
from autoplayer.inventory_audit import InventoryAudit
from test_policy import state

def line(text,y):return {'text':text,'words':[{'box':[100,y,100,24]}]}
class EquipmentPreparationTests(unittest.TestCase):
    def fixture(self):return json.loads((Path(__file__).parent/'fixtures/equipment-review-1793074833220034500.json').read_text())
    def actor(self):
        effects=Mock(bindings=REFERENCE['keys']);effects.keys.side_effect=lambda role:{'confirm':['num1'],'cancel':['num2']}.get(role,[])
        a=OperationalControl(Settings(),effects);a.inventory_audit.last=self.fixture()['audit'];a.inventory_audit.last.update(control_revision=0,guidance_revision=0)
        return a
    def current(self):return {'step':12,'control_revision':0,'floor':[2],'operator_guidance':{'revision':0,'text':''}}
    def native(self):
        n=self.fixture()['frames']['12']['ocr'];rows=self.fixture()['frames']['12']['inventory']['rows'];n['_current_inventory_rows']=rows
        i=next(i for i,l in enumerate(n['lines']) if l['text']=='青銅の盾');n['_visual_selection']={'text':'青銅の盾','line_index':i,'frame_sha256':n['frame_sha256']};return n
    def base(self):return Decision('menu','','','',.8,'continue',[],80,600)
    def receipt(self,keys,executed=True):return {'executed':executed,'observed_keys':keys,'input_receipt':{'os_submission':'accepted'},'operation':{'execution_stage':'equipment_preparation','selected_keys':keys}}
    def test_observed_zero_and_shield_description_select_item_then_exact_equip(self):
        op=self.actor();trial=op.equipment_preparation;n=self.native();c=self.current()
        d=trial.choose(op,self.base(),n,{},c,'inventory',allow_start=True);self.assertEqual(d.keys,['num1']);trial.feedback(self.receipt(['num1']),'num1')
        popup={'lines':[line('▶装備',100),line('投げる',130),line('置く',160)]}
        d=trial.choose(op,self.base(),popup,{},dict(c,step=13),'item_actions');self.assertEqual(d.keys,['num1'])
        trial.feedback(self.receipt(['num1']),'num1');self.assertIsNone(trial.active)
        self.assertIsNone(trial.choose(op,self.base(),n,{},dict(c,step=14),'inventory',allow_start=True))
    def test_rejected_input_and_control_change_cannot_confirm_another_item(self):
        op=self.actor();t=op.equipment_preparation;c=self.current();t.choose(op,self.base(),self.native(),{},c,'inventory',True)
        t.feedback(self.receipt(['num1'],False),'num1');self.assertIsNone(t.active)
        op=self.actor();t=op.equipment_preparation;t.choose(op,self.base(),self.native(),{},c,'inventory',True)
        self.assertIsNone(t.choose(op,self.base(),{}, {},dict(c,control_revision=1),'item_actions'))
    def test_no_trial_for_nonzero_ability_operator_instruction_or_missing_description(self):
        for mode in ('nonzero','guidance','description'):
            op=self.actor();n=self.native();c=self.current()
            if mode=='nonzero':op.inventory_audit.last['metrics']['shield']['values']=[4]
            if mode=='guidance':c['operator_guidance']['text']='装備を変更しない'
            if mode=='description':
                for row in n['_current_inventory_rows']:row.pop('known_description',None)
            self.assertIsNone(op.equipment_preparation.choose(op,self.base(),n,{},c,'inventory',True))
    def test_cached_description_is_not_a_new_possession(self):
        d=self.fixture();inv=Inventory();inv.description_reads=[r['known_description'] for r in d['frames']['12']['inventory']['rows'] if r.get('known_description')]
        inv.rows=copy.deepcopy(d['frames']['12']['inventory']['rows'])
        with patch.object(Inventory,'cursor_index',return_value=next(i for i,l in enumerate(d['frames']['10']['ocr']['lines']) if l['text']=='地獄耳の巻物')):
            result=inv.observe(d['frames']['10']['ocr'])
        self.assertNotIn('今いる階のモンスターがどこにいるかわかるぞ。',[r['display_name'] for r in result['rows']])
    def test_panel_is_hidden_before_page_probe_and_not_counted_as_two_pages(self):
        op=self.actor();op.inventory_audit=InventoryAudit();s=state();c={**self.current(),'region':'dungeon','interface':None,'step':0}
        op.inventory_audit.prepare(s,c,{},None)
        d=self.fixture();n=d['frames']['9']['ocr'];n['_current_inventory_rows']=d['frames']['9']['inventory']['rows']
        s['retained_state']={'last_observed_inventory':{'occupancy':{'count':7}}}
        c.update(step=9,interface='inventory');op.inventory_audit.prepare(s,c,n,None)
        action=op._inspect_inventory(self.base(),n,c);self.assertEqual(action.keys,['num7'])
        # Removing the panel reveals the other two rows; this is the same page.
        full=n['_current_inventory_rows']+[{'display_name':x,'box':[100,y,220,y+24],'known_description':{'status':'description_text_observed','quotes':['既存の説明']}} for x,y in [('大きいパン',50),('ルーラ草',80)]]
        c['step']=10;op.inventory_audit.prepare(s,c,{'_current_inventory_rows':full},op.inventory_review)
        self.assertEqual(len(op.inventory_audit.context()['pages']),1);self.assertEqual(op.inventory_audit.context()['coverage'],'observed_count_matches')
