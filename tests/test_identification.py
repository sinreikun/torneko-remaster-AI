"""Item-operation contracts, not a simulated game."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from autoplayer.identification import Identification
from autoplayer.controls import REFERENCE

class IdentificationTests(unittest.TestCase):
    def setUp(self):
        self.actor=SimpleNamespace(effects=SimpleNamespace(bindings=REFERENCE['keys']),cfg=SimpleNamespace(allowed_keys=list(REFERENCE['keys'])),inventory_audit=SimpleNamespace(last=None,context=lambda:{}),_key=lambda role:{'confirm':'num1','cancel':'num2'}.get(role),_cursor_toward=Mock(return_value='selected'),_decision=lambda base,keys,source,reason:SimpleNamespace(keys=keys,reason=reason))
        self.scroll={'id':'scroll','display_name':'インパスの巻物','quantity':1}
        self.ring={'id':'ring','display_name':'エメラルドの指輪','identification_required':True}
        self.current={'step':1,'floor':[3],'control_revision':0,'operator_guidance':{'revision':0},'interface':'inventory'}
        self.tool=Identification();self.state={}
    def choose(self,kind='inventory',text='',rows=None):
        self.current.update(interface=kind,step=self.current['step']+1)
        return self.tool.choose(self.actor,None,{'lines':[{'text':text}],'_current_inventory_rows':rows if rows is not None else [self.scroll,self.ring]},self.state,self.current)
    def accepted(self,keys=['num1']):
        self.tool.feedback({'executed':True,'observed_keys':keys,'input_receipt':{'os_submission':'accepted'},'operation':{'selected_source':'identification','selected_keys':keys}})
    def read(self):
        self.choose();self.accepted();self.choose('item_actions');self.accepted()
    def test_scroll_then_read_then_explicit_target_then_result_is_unverified(self):
        self.assertEqual(self.choose().keys,['num1']);self.assertEqual(self.actor._cursor_toward.call_args.args[1]['id'],'scroll');self.accepted()
        self.assertEqual(self.choose('item_actions').keys,['num1']);self.assertEqual(self.actor._cursor_toward.call_args.args[1],'読む');self.accepted()
        self.assertEqual(self.choose(text='どの道具を識別しますか？').keys,['num1']);self.assertEqual(self.actor._cursor_toward.call_args.args[1]['id'],'ring');self.accepted()
        self.assertIsNone(self.choose(None,'エメラルドの指輪はちからの指輪だった。',[]))
        self.assertEqual(self.tool.last['status'],'awaiting_identification_evidence');self.assertTrue(self.tool.last['result_quotes'])
    def test_normal_inventory_after_read_does_not_confirm_another_item(self):
        self.read();self.actor._cursor_toward.reset_mock()
        self.assertIsNone(self.choose());self.actor._cursor_toward.assert_not_called()
        self.assertIsNone(self.choose())
    def test_duplicate_name_is_not_same_target(self):
        self.read();other={**self.ring,'id':'other'}
        d=self.choose(text='識別するアイテムを選んでください',rows=[other]);self.assertNotEqual(d.keys,['num1'])
    def test_no_unknown_items_does_not_waste_scroll(self):
        self.assertIsNone(self.choose(rows=[self.scroll,{**self.ring,'identification_required':False}]))
    def test_danger_and_operator_preserve_scroll(self):
        self.current['contacts']=[[1,0]];self.assertIsNone(self.choose())
        self.current['contacts']=[];self.current['operator_guidance']['text']='インパスは温存';self.assertIsNone(self.choose())
    def test_unaccepted_read_cannot_advance_to_target(self):
        self.choose();self.accepted();self.choose('item_actions')
        self.tool.feedback({'executed':False,'operation':{'selected_source':'identification'}})
        self.assertIsNone(self.tool.active)
    def test_scope_change_cancels_pending_target(self):
        self.read();self.current['floor']=[4]
        self.assertIsNone(self.choose(text='どれを識別しますか',rows=[self.ring]))

    def test_description_is_not_target_selection_prompt(self):
        self.read();self.actor._cursor_toward.reset_mock()
        self.assertIsNone(self.choose(text='インパスの巻物などで識別したら候補に選択できるアイテムが増えるぞ。'))
        self.actor._cursor_toward.assert_not_called()
    def test_real_log_scroll_page_and_previous_unknown_ring_allow_selection(self):
        import json
        from pathlib import Path
        from autoplayer.operational_control import OperationalControl
        f=json.loads((Path(__file__).parent/'fixtures/inpas_observation.json').read_text())
        self.actor.inventory_audit.context=lambda:{'active':True,'pages':[f['8']['rows']]}
        self.actor._cursor_toward=lambda native,target,vertical=False:OperationalControl._cursor_toward(self.actor,native,target,vertical)
        native={**f['11']['ocr'],'_current_inventory_rows':f['11']['rows']}
        selected=next(r for r in f['11']['rows'] if r['selected'])
        native['_visual_selection']={'frame_sha256':native['frame_sha256'],'text':selected['quote'],'line_index':selected['line_index']}
        d=self.tool.choose(self.actor,None,native,self.state,self.current)
        self.assertIsNotNone(d)
        self.assertEqual(self.tool.active['target']['display_name'],'エメラルドの指輪')
        self.assertEqual(self.tool.active['scroll']['display_name'],'インパスの巻物')

    def test_read_menu_description_cannot_substitute_for_target_screen(self):
        self.read();self.actor._cursor_toward.reset_mock()
        self.assertIsNone(self.choose('item_actions','識別するアイテムを選びます'))
        self.actor._cursor_toward.assert_not_called()
