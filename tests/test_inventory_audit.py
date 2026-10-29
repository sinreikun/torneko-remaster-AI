"""Recorded inventory evidence and scheduler/receipt contracts; no live game."""
import copy,json,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from autoplayer.inventory_audit import InventoryAudit
from autoplayer.world_model import Inventory
from autoplayer.supervision import Supervisor
from test_supervision import ledger,proposal,reply_ready
from test_policy import state

class InventoryAuditTests(unittest.TestCase):
    def context(self,step=0,kind=None):return {'step':step,'interface':kind,'region':'dungeon','floor':[1],'control_revision':0,'operator_guidance':{'revision':0}}
    def known(self,name):return {'display_name':name,'box':[100,50,200,75],'selected':True,'known_description':{'status':'description_text_observed','quotes':['記録済みの説明']}}
    def test_start_periodic_and_emergency_deferral(self):
        a=InventoryAudit();s=state();a.prepare(s,self.context(),{},None);self.assertTrue(a.active)
        a.finish('analysed',5);a.prepare(s,self.context(132),{},None);self.assertIsNone(a.active)
        a.prepare(s,self.context(133),{},None);self.assertTrue(a.active)
        s['minimap']['enemies']=[(1,1)];a.prepare(s,self.context(134),{},None);self.assertIsNone(a.active)
        self.assertEqual(a.last['status'],'interrupted_current_context')
        b=InventoryAudit();b.prepare(s,self.context(),{},None);self.assertIsNone(b.active)
    def test_recorded_shield_was_read_and_description_can_classify_unknown_name(self):
        data=json.loads((Path(__file__).parent/'fixtures/inventory-shield-1793069488886248000.json').read_text())
        shield=next(r for r in data['inventory']['rows'] if r['display_name']=='青銅の盾')
        self.assertTrue(Inventory.equipment_evidence(shield));self.assertIsNone(shield['equipped'])
        shield['display_name']='未鑑定の品'
        self.assertTrue(any(e.get('source')=='description_ocr' for e in Inventory.equipment_evidence(shield)))
        self.assertIsNone(shield['true_name'])
        self.assertEqual(Inventory.equipment_evidence({'display_name':'ルーラ草'}),[])
        self.assertTrue(Inventory.equipment_evidence({'display_name':'未鑑定の品','equipment_affordance':{'action':'装備'}}))
    def test_all_pages_before_analysis_and_receipt_before_counting_page_change(self):
        a=InventoryAudit();s=state();a.prepare(s,self.context(),{},None)
        s['retained_state']={'last_observed_inventory':{'occupancy':{'count':2}}}
        n={'_current_inventory_rows':[self.known('薬草')]}
        a.prepare(s,self.context(1,'inventory'),n,None);self.assertTrue(a.active['page_pending'])
        a.prepare(s,self.context(2,'inventory'),n,None);self.assertEqual(a.active['stage'],'collect')
        a.active['page_submitted']=True
        n={'_current_inventory_rows':[self.known('青銅の盾')]}
        a.prepare(s,self.context(3,'inventory'),n,None)
        self.assertEqual(a.active['stage'],'analyse');self.assertEqual(len(a.context()['pages']),2)
        self.assertIn('sword',a.context()['missing_status'])
    def test_repeated_page_not_claimed_complete_and_scope_change_aborts(self):
        a=InventoryAudit();s=state();a.prepare(s,self.context(),{},None)
        n={'_current_inventory_rows':[self.known('薬草')]}
        a.prepare(s,self.context(1,'inventory'),n,None);a.active['page_submitted']=True
        a.prepare(s,self.context(2,'inventory'),n,None)
        self.assertEqual(a.context()['coverage'],'incomplete_or_page_did_not_change')
        c=self.context(3,'inventory');c['floor']=[2];a.prepare(s,c,n,None);self.assertIsNone(a.active)
    def test_analysis_requests_current_inventory_and_requires_matching_review_id(self):
        a=InventoryAudit();s=state();a.prepare(s,self.context(),{},None)
        s['retained_state']={'last_observed_inventory':{'occupancy':{'count':1}}}
        a.prepare(s,self.context(1,'inventory'),{'_current_inventory_rows':[self.known('青銅の盾')]},None)
        sup=Supervisor(lambda *args:proposal(),threading.Event());self.addCleanup(sup.close)
        l=ledger(step=1);l.current['inventory_audit']=a.context();sup.last_request=1;sup.next_review=1
        sup.tick(l,{'inventory_audit':a.context()});reply_ready(sup)
        self.assertTrue(a.awaiting_analysis(sup,1));sup.tick(l)
        self.assertFalse(a.awaiting_analysis(sup,1));self.assertEqual(a.last['status'],'analysed')
    def test_actual_page_key_requires_accepted_receipt(self):
        from autoplayer.operational_control import OperationalControl
        from autoplayer.core import Settings,Decision
        from autoplayer.controls import REFERENCE
        op=OperationalControl(Settings(),Mock(bindings=REFERENCE['keys']));s=state();op.inventory_audit.prepare(s,self.context(),{},None)
        n={'_current_inventory_rows':[self.known('青銅の盾')]};op.inventory_audit.prepare(s,self.context(1,'inventory'),n,None)
        d=op._inspect_inventory(Decision('menu','','','',.8,'continue',[],80,600),n,self.context(1,'inventory'))
        self.assertEqual(d.keys,['d'])
        row={'operation':{'selected_keys':['d'],'selected_source':'interface'},'observed_keys':['d'],'executed':False,'input_receipt':{'os_submission':'rejected'}}
        op.feedback(row);self.assertFalse(op.inventory_audit.active.get('page_submitted',False))
    def test_chosen_item_popup_gets_scoped_analysis_only_after_accepted_confirm(self):
        from autoplayer.operational_control import OperationalControl
        from autoplayer.core import Settings
        effects=Mock();effects.keys.side_effect=lambda role:['num1'] if role=='confirm' else []
        op=OperationalControl(Settings(),effects)
        pending={'item_quote':'青銅の盾','step':3,'control':0,'guidance':0,'floor':[1]}
        op.menu_followup=copy.deepcopy(pending)
        receipt={'operation':{'selected_keys':['num1'],'selected_source':'interface','execution_stage':'requested_item_action'},'observed_keys':['num1'],'executed':True,'input_receipt':{'os_submission':'accepted'}}
        op.feedback(receipt);self.assertTrue(op.menu_followup['accepted'])
        op.prepare_inventory_audit(state(),self.context(4,'item_actions'),{})
        self.assertEqual(op.inventory_audit.context()['coverage'],'selected_item_action_menu')
        self.assertEqual(op.inventory_audit.context()['selected_item_quote'],'青銅の盾')
        op.menu_followup=copy.deepcopy(pending);op.feedback({**receipt,'executed':False})
        self.assertIsNone(op.menu_followup)
