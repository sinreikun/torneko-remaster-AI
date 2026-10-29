"""Actual logged equipment opportunity and bounded execution contracts."""
import copy,json,unittest
from pathlib import Path
from autoplayer.world_model import Inventory
from autoplayer.operational_control import OperationalControl
from autoplayer.controls import REFERENCE
from autoplayer.core import Settings,Decision
from unittest.mock import Mock

class EquipmentPendingTests(unittest.TestCase):
    def fixture(self):return json.loads((Path(__file__).parent/'fixtures/pending-equipment-1793101721235382700.json').read_text())
    def setup_case(self):
        f=self.fixture();state=copy.deepcopy(f['state']);state['retained_state']=copy.deepcopy(f['retained_state'])
        inv=Inventory('different_session');inv.description_reads=[f['old_common_description']]
        rows=copy.deepcopy(state['retained_state']['last_observed_inventory']['rows'])
        steel=next(r for r in rows if r['display_name']=='鋼鉄の盾')
        steel['known_description']=inv.known_description(steel)
        native=copy.deepcopy(f['ocr']);native['_current_inventory_rows']=rows
        selected=next(r for r in rows if r.get('selected'));index=selected['line_index']
        native['_visual_selection']={'text':native['lines'][index]['text'],'line_index':index,'frame_sha256':native['frame_sha256']}
        effects=Mock(bindings=REFERENCE['keys']);effects.keys.side_effect=lambda role:{'confirm':['num1'],'cancel':['num2'],'menu':['num0']}.get(role,[])
        actor=OperationalControl(Settings(),effects)
        current={'step':200,'floor':[2],'control_revision':0,'operator_guidance':{'revision':0,'text':''},'interface':'inventory'}
        base=Decision('menu','','','',.8,'continue',[],80,600)
        return actor,state,native,current,base,steel
    def test_actual_orange_steel_retains_only_common_base_not_individual_identification(self):
        _,_,_,_,_,steel=self.setup_case()
        self.assertTrue(steel['identification_required'])
        self.assertEqual(steel['curse_status'],'unknown')
        self.assertTrue(steel['known_description']['individual_details_unconfirmed'])
        self.assertIn('基本６',' '.join(steel['known_description']['quotes']))
        self.assertNotIn('item_id',steel['known_description'])
    def test_logged_three_to_base_six_can_start_without_recent_analysed_audit(self):
        actor,s,n,c,base,steel=self.setup_case();actor.inventory_audit.last=None
        result=actor.equipment_preparation.choose(actor,base,n,s,c,'inventory',True)
        self.assertIsNotNone(result)
        self.assertEqual(result.keys,['s'])
        self.assertEqual(actor.equipment_preparation.active['target_row']['id'],steel['id'])
        self.assertEqual(actor.equipment_preparation.active['before'],3)
        self.assertEqual(actor.equipment_preparation.active['base'],6)
    def test_pending_comparison_survives_exploration_and_requests_menu_once(self):
        actor,s,n,c,base,steel=self.setup_case()
        task=actor.equipment_preparation;task.review(actor,s,c,n)
        state=copy.deepcopy(s);state['eligible']=True
        c.update(interface=None,step=201)
        self.assertEqual(task.request(actor,base,state,c,{}).keys,['num0'])
        # A competing combat/recovery choice must not count this proposal as sent.
        self.assertEqual(task.request(actor,base,state,c,{}).keys,['num0'])
        task.feedback({'executed':True,'observed_keys':['num0'],'input_receipt':{'os_submission':'accepted'},'operation':{'selected_source':'equipment_upgrade_pending','selected_keys':['num0']}},'num1')
        self.assertIsNone(task.request(actor,base,state,c,{}))
        self.assertIn(steel['id'],task.opportunities)
    def test_generic_caution_does_not_disable_upgrade_but_explicit_hold_does(self):
        actor,s,n,c,base,_=self.setup_case();c['operator_guidance']['text']='慎重に進む'
        self.assertIsNotNone(actor.equipment_preparation.choose(actor,base,n,s,c,'inventory',True))
        actor,s,n,c,base,_=self.setup_case();c['operator_guidance']['text']='装備を変更しない'
        self.assertIsNone(actor.equipment_preparation.choose(actor,base,n,s,c,'inventory',True))
