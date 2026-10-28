"""Typed semantic protocol tests, including failure classes reported by 0.24.1."""
import copy,json,tempfile,unittest
from pathlib import Path
from autoplayer.core import Settings
from autoplayer.nextgen.protocol import wire_protocol,decode_parts
from autoplayer.nextgen.supervisor import SCHEMA,validate
from autoplayer.nextgen.model import ForwardModel,action_id
from autoplayer.nextgen.store import ExperienceStore
from test_nextgen import measured,plan

class WireTests(unittest.TestCase):
    def setUp(self):
        self.state=measured();self.candidates=[{'id':action_id([k],h),'keys':[k],'hold_ms':h} for k in ('a','b') for h in (80,140,220)]
        self.wire,self.aliases,self.schema=wire_protocol(SCHEMA,self.candidates,self.state)
    def test_wire_uses_simple_enumerated_ids_and_preserves_internal_action_identity(self):
        self.assertTrue(all(w['id'].startswith('A') and '[' not in w['id'] for w in self.wire))
        self.assertEqual(self.schema['properties']['candidate_values']['items']['properties']['id']['enum'],list(self.aliases))
        p=plan();p['candidate_values']=[{'id':self.wire[0]['id'],'value':1,'reason':'現在の選択を確認する'}]
        value,issues=decode_parts(p,self.aliases,self.candidates,self.state,validate)
        self.assertFalse(issues);self.assertEqual(value['candidate_values'][0]['id'],self.candidates[0]['id'])
        self.assertIn('anyOf',self.schema['properties']['targets']['items'])
        self.assertEqual(self.schema['properties']['candidate_values']['items']['properties']['reason']['minLength'],1)
        self.assertEqual(self.schema['properties']['objective']['maxLength'],500)
        self.assertEqual(self.schema['properties']['interpretations']['maxItems'],0)
    def test_invalid_text_goal_does_not_discard_valid_candidate_and_is_not_coerced(self):
        p=plan();p['targets']=[{'feature':'text','operator':'ge','value':1,'weight':1},{'feature':'metric.hp.0','operator':'ge','value':12,'weight':1}]
        p['candidate_values']=[{'id':self.wire[0]['id'],'value':1,'reason':'候補を試す'}]
        value,issues=decode_parts(p,self.aliases,self.candidates,self.state,validate)
        self.assertEqual(len(issues),1);self.assertEqual(value['targets'],[p['targets'][1]])
        self.assertEqual(len(value['candidate_values']),1)
    def test_invented_candidate_cannot_become_input_even_in_a_partially_valid_plan(self):
        p=plan();p['candidate_values']=[{'id':'powershell','value':1,'reason':'不正'},{'id':self.wire[1]['id'],'value':.5,'reason':'許可候補'}]
        value,issues=decode_parts(p,self.aliases,self.candidates,self.state,validate)
        self.assertEqual(len(issues),1);self.assertEqual(len(value['candidate_values']),1)
        self.assertEqual(value['candidate_values'][0]['id'],self.aliases[self.wire[1]['id']])
    def test_bad_header_or_array_type_still_rejects_reply(self):
        for p in ({**plan(),'review_after':0},{**plan(),'targets':'anything'},{k:v for k,v in plan().items() if k!='targets'}):
            with self.assertRaises(ValueError):decode_parts(p,self.aliases,self.candidates,self.state,validate)
    def test_cursor_interface_starts_with_single_keys_not_all_diagonal_and_modifier_variants(self):
        cfg=Settings(window_title='generic',allowed_keys=['a','b','c'],key_bindings={'a':'上移動','b':'左移動','c':'方向転換'})
        with tempfile.TemporaryDirectory() as folder:
            store=ExperienceStore(Path(folder)/'w.sqlite','p','s');model=ForwardModel(store)
            try:
                state=measured();state['interface_hypothesis']=True
                selected,ranking,_=model.choose(state,model.candidates(cfg))
                self.assertTrue(all(len(item['candidate']['keys'])==1 for item in ranking))
                chord=next(c for c in model.candidates(cfg) if len(c['keys'])==2)
                p=plan();p['candidate_values']=[{'id':chord['id'],'value':2,'reason':'組合せの検証'}]
                _,ranking,_=model.choose(state,model.candidates(cfg),p)
                self.assertTrue(any(c['candidate']['id']==chord['id'] for c in ranking))
            finally:store.close()

    def test_async_worker_adopts_typed_target_and_decoded_candidate(self):
        import threading
        from unittest.mock import patch
        from autoplayer.core import LocalLLM
        from autoplayer.nextgen.supervisor import SemanticSupervisor
        cfg=Settings(window_title='generic',allowed_keys=['a']);events=[]
        def reply(image,system,prompt,schema):
            self.assertEqual(prompt['candidates'][0]['id'],'A00')
            p=plan();p['candidate_values']=[{'id':'A00','value':1,'reason':'表示を確認する'}]
            p['targets']=[{'feature':'metric.hp.0','operator':'ge','value':12,'weight':1}]
            return json.dumps(p)
        with tempfile.TemporaryDirectory() as folder,patch.object(LocalLLM,'_request',side_effect=reply):
            store=ExperienceStore(Path(folder)/'w.sqlite','p','s');model=ForwardModel(store)
            supervisor=SemanticSupervisor(cfg,LocalLLM(cfg),events.append,threading.Event());candidates=model.candidates(cfg)
            try:
                supervisor.tick(self.state,candidates,0,0,{},None,model,{})
                packet=supervisor.results.get(timeout=2);supervisor.results.put(packet)
                adopted_plan,adopted=supervisor.tick(self.state,candidates,1,0,{},None,model,{})
                self.assertIsNotNone(adopted);self.assertEqual(supervisor.errors,0)
                self.assertEqual(adopted_plan['candidate_values'][0]['keys'],['a'])
                self.assertEqual(adopted_plan['targets'][0]['value'],12)
            finally:supervisor.close();store.close()
