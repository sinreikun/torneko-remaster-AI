"""Recorded split OCR and single-input contracts; no game simulation."""
import copy,json,unittest
from pathlib import Path
from unittest.mock import Mock
from autoplayer.interfaces import normalize_item_menu,torneko_menu_kind
from autoplayer.operational_control import OperationalControl
from autoplayer.core import Settings,Decision
from autoplayer.controls import REFERENCE
from test_supervision import proposal,ledger
from test_policy import state

class ItemActionFragmentTests(unittest.TestCase):
    def supplied(self):return json.loads((Path(__file__).parent/'fixtures/split-item-actions-1793061150308994000.json').read_text())['ocr']
    def test_recorded_adjacent_fragments_are_classified_without_rewriting_source(self):
        raw=self.supplied();unchanged=copy.deepcopy(raw);n=normalize_item_menu(raw)
        self.assertEqual(torneko_menu_kind(raw),'item_actions')
        joined=next(l for l in n['lines'] if l['text']=='置く')
        self.assertEqual(joined['ocr_fragments'],['置','く']);self.assertEqual(len(joined['words']),2)
        self.assertEqual(raw,unchanged);self.assertIs(normalize_item_menu(n),n)
    def test_distant_fragments_are_not_joined(self):
        raw=self.supplied()
        for line in raw['lines']:
            if line['text']=='く':line['words'][0]['box'][1]+=200
        self.assertFalse(any(l['text']=='置く' for l in normalize_item_menu(raw)['lines']))
        # Two independent aligned commands and a count still identify the popup.
        self.assertEqual(torneko_menu_kind(raw),'item_actions')
    def test_two_words_without_layout_or_inventory_context_are_not_a_popup(self):
        n={'status':'ok','lines':[{'text':t} for t in ('飲む','投げる')]}
        self.assertIsNone(torneko_menu_kind(n))
    def test_quoted_equipment_action_is_navigated_before_confirming(self):
        from autoplayer.supervision import StateLedger
        n=normalize_item_menu(self.supplied())
        for entry in n['lines']:
            if entry['text']=='飲む':entry['text']='装備'
        effects=Mock(bindings=REFERENCE['keys']);effects.keys.side_effect=lambda role:{'confirm':['num1'],'cancel':['num2']}.get(role,[])
        actor=OperationalControl(Settings(),effects);s=state();l=StateLedger()
        p=proposal();p.update(skill='inspect_inventory',interface_role='confirm',interface_quote='装備')
        base=Decision('menu','','','',.8,'continue',[],80,600)
        for selected,expected in [('投げる','w'),('装備','num1')]:
            index=next(i for i,line in enumerate(n['lines']) if line['text']==selected)
            n['_visual_selection']={'text':selected,'line_index':index,'frame_sha256':n['frame_sha256']}
            l.observe(n,s,{}, {}, {'revision':0},0,40)
            if not p.get('hint_consumed'):
                p.update(request_text_key=l.current['text_key'],request_selection=l.current['selection_key'],request_frame=l.current['frame_sha256'])
            d=actor.interface(base,n,s,p,l,[])
            self.assertEqual(d.keys,[expected])
    def test_inventory_request_selects_quoted_gear_instead_of_opening_current_drug(self):
        from autoplayer.supervision import StateLedger
        n={'status':'ok','frame_sha256':'current','lines':[{'text':t,'words':[{'box':[100,50+i*30,100,24]}]} for i,t in enumerate(('薬草','鋼鉄の盾','道具','詳細表示切替','整理','2/20'))]}
        n['_current_inventory_rows']=[{'quote':'薬草','display_name':'薬草'},{'quote':'鋼鉄の盾','display_name':'鋼鉄の盾'}]
        effects=Mock(bindings=REFERENCE['keys']);effects.keys.side_effect=lambda role:{'confirm':['num1'],'cancel':['num2']}.get(role,[])
        actor=OperationalControl(Settings(),effects);s=state();l=StateLedger()
        p=proposal();p.update(skill='inspect_inventory',interface_role='confirm',interface_quote='鋼鉄の盾')
        base=Decision('menu','','','',.8,'continue',[],80,600)
        for index,expected in [(0,'s'),(1,'num1')]:
            for j,label in enumerate(('薬草','鋼鉄の盾')):n['lines'][j]['text']=('▶' if j==index else '')+label
            n['_visual_selection']={'text':n['lines'][index]['text'],'line_index':index,'frame_sha256':'current'}
            l.observe(n,s,{}, {}, {'revision':0},0,1)
            if not p.get('hint_consumed'):p.update(request_text_key=l.current['text_key'],request_selection=l.current['selection_key'])
            self.assertEqual(actor.interface(base,n,s,p,l,[]).keys,[expected])

    def test_active_item_operation_is_not_replaced_by_a_new_supervisor_suggestion(self):
        from autoplayer.supervision import StateLedger
        n=normalize_item_menu(self.supplied());s=state();l=StateLedger()
        effects=Mock(bindings=REFERENCE['keys']);effects.keys.side_effect=lambda role:{'confirm':['num1'],'cancel':['num2']}.get(role,[])
        actor=OperationalControl(Settings(),effects)
        index=next(i for i,line in enumerate(n['lines']) if line['text']=='投げる')
        n['_visual_selection']={'text':'投げる','line_index':index,'frame_sha256':n['frame_sha256']}
        l.observe(n,s,{}, {}, {'revision':0},0,1)
        base=Decision('menu','','','',.8,'continue',[],80,600)
        def plan(action):
            p=proposal();p.update(skill='interface',interface_role='confirm',interface_quote=action,request_text_key=l.current['text_key'],request_selection=l.current['selection_key']);return p
        self.assertEqual(actor.interface(base,n,s,plan('飲む'),l,[]).keys,['w'])
        self.assertEqual(actor.interface(base,n,s,plan('投げる'),l,[]).keys,['w'])
        self.assertEqual(actor.execution_context()['item_action']['action'],'飲む')
    def test_current_urgent_hp_precedes_equipment_status_and_old_inspection_plan(self):
        from test_supervision import native
        s=state();s['metrics']['hp']['values']=[4,15]
        s['retained_state']={'refresh_reasons':['equipment_status_after_change']}
        effects=Mock(bindings=REFERENCE['keys']);effects.keys.side_effect=lambda role:{'confirm':['num1'],'cancel':['num2']}.get(role,[])
        actor=OperationalControl(Settings(),effects);p=proposal();p['skill']='inspect_inventory'
        d=actor.interface(Decision('menu','','','',.8,'continue',[],80,600),native('▶薬草','道具','詳細表示切替','整理','1/20'),s,p,ledger(s),[])
        self.assertEqual(d.keys,['num1']);self.assertEqual(d.execution_stage,'recovery_item')

    def test_equipment_check_requires_fresh_abilities_and_survives_inventory_only(self):
        from autoplayer.world_model import WorldModel
        from autoplayer.state_snapshot import StateSnapshot
        snapshot=StateSnapshot();snapshot.request('equipment_status_after_change',1)
        snapshot.observe({}, {'status':'observed','rows':[]},2)
        self.assertIn('equipment_status_after_change',snapshot.pending)
        world=WorldModel();world.pending_equipment_check={'before':{'shield':{'values':[0]}},'item_id':'shield','origin_step':1}
        current={k:{'values':v} for k,v in [('strength',[8,8]),('sword',[0]),('shield',[5])]}
        stale=copy.deepcopy(current);stale['shield']['retained']=True
        self.assertIsNone(world.verify_equipment(stale,'old'))
        world.state_snapshot.request('equipment_status_after_change',1)
        result=world.verify_equipment(current,'fresh')
        self.assertEqual(result['metric_changes']['shield'],{'before':[0],'after':[5]})
        self.assertIsNone(world.pending_equipment_check)
        self.assertNotIn('equipment_status_after_change',world.state_snapshot.pending)
        snapshot.observe(current,{},3)
        self.assertNotIn('equipment_status_after_change',snapshot.pending)
        world.pending_equipment_check={'before':{},'item_id':'unknown','started_tick':0};world.tick=13
        world.state_snapshot.request('equipment_status_after_change',1)
        self.assertEqual(world.verify_equipment({},'unread')['status'],'abilities_unconfirmed')
        self.assertNotIn('equipment_status_after_change',world.state_snapshot.pending)


    def test_recovery_popup_moves_to_drink_and_never_opens_menu(self):
        n=normalize_item_menu(self.supplied());s=state();s['metrics']['hp']['values']=[6,30]
        effects=Mock(bindings=REFERENCE['keys']);effects.keys.side_effect=lambda role:{'confirm':['num1'],'cancel':['num2'],'move':list('wasd'),'menu':['num0']}.get(role,[])
        actor=OperationalControl(Settings(),effects)
        l=ledger(s,step=40);p=proposal();p['skill']='recover'
        actor.item_context={'name':'薬草','phase':'actions','step':39,'control':0,'guidance':0,'floor':l.current['floor']}
        base=Decision('menu','','','',.8,'continue',[],80,600)
        for selected,expected in [('投げる','w'),('飲む','num1')]:
            index=next(i for i,line in enumerate(n['lines']) if line['text']==selected)
            n['_visual_selection']={'text':selected,'line_index':index,'frame_sha256':n['frame_sha256']}
            actor.item_context['phase']='actions'
            d=actor.interface(base,n,s,p,l,[])
            self.assertEqual(d.keys,[expected]);self.assertNotEqual(d.keys,['num0'])
        # Without the confirmed selected recovery item, leave rather than use it.
        actor.item_context=None
        self.assertEqual(actor.interface(base,n,s,p,l,[]).keys,['num2'])
