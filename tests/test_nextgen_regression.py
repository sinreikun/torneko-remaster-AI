"""Regressions from the submitted 0.24.0 run; no game simulation."""
import copy,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from autoplayer.core import LocalLLM,Settings
from autoplayer.nextgen.identity import InterfaceIdentity
from autoplayer.nextgen.model import ForwardModel
from autoplayer.nextgen.store import ExperienceStore
from autoplayer.nextgen.supervisor import SemanticSupervisor
from test_nextgen import measured,plan

class RegressionTests(unittest.TestCase):
    def test_all_101_real_main_menu_samples_share_layout_despite_26_old_keys(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/interface-noise-1793147045094047100.json').read_text())
        identity=InterfaceIdentity();layouts=[]
        for case in fixture['cases']:
            layout,_=identity.observe({'lines':[{'text':q} for q in case['quotes']]});layouts.append(layout)
        self.assertEqual(len(layouts),101);self.assertEqual(len(set(layouts)),1)
        self.assertEqual(len({c['previous_text_key'] for c in fixture['cases']}),26)
    def test_duplicate_labels_keep_selected_row_identity_but_scope_ignores_cursor(self):
        identity=InterfaceIdentity();native={'lines':[{'text':'薬草','words':[{'box':[100,100,40,20]}]},{'text':'薬草','words':[{'box':[100,140,40,20]}]}]}
        layout,a=identity.observe(native,0);other,b=identity.observe(native,1)
        self.assertEqual(layout,other);self.assertNotEqual(a,b)
    def test_unrelated_menu_layout_remains_separate(self):
        identity=InterfaceIdentity();a,_=identity.observe({'lines':[{'text':t} for t in ('道具','マップ','足元','その他')]})
        b,_=identity.observe({'lines':[{'text':t} for t in ('はじめから','つづきから','終了')]})
        self.assertNotEqual(a,b)
    def test_semantic_request_survives_temporary_layout_change_and_reuses_matching_reply(self):
        ready=threading.Event();release=threading.Event()
        def reply(*a,**k):ready.set();release.wait(2);return json.dumps(plan())
        cfg=Settings(window_title='generic',allowed_keys=['a']);state=measured();state.update(interface_hypothesis=True,interface_layout='menuA')
        other=copy.deepcopy(state);other['interface_layout']='menuB';other['text_key']='other'
        with tempfile.TemporaryDirectory() as folder,patch.object(LocalLLM,'_request',side_effect=reply):
            store=ExperienceStore(Path(folder)/'w.sqlite','p','s');model=ForwardModel(store);supervisor=SemanticSupervisor(cfg,LocalLLM(cfg),lambda _:None,threading.Event());actions=model.candidates(cfg)
            try:
                supervisor.tick(state,actions,0,0,{},None,model,{});self.assertTrue(ready.wait(1))
                supervisor.tick(other,actions,1,0,{},None,model,{})
                self.assertFalse(supervisor.pending['cancel'].is_set())
                release.set();packet=supervisor.results.get(timeout=2);supervisor.results.put(packet)
                _,adopted=supervisor.tick(other,actions,2,0,{},None,model,{})
                self.assertIsNone(adopted)
                _,adopted=supervisor.tick(state,actions,3,0,{},None,model,{})
                self.assertIsNotNone(adopted);self.assertEqual(supervisor.adoptions,1)
            finally:release.set();supervisor.close();store.close()
    def test_cancelled_inference_is_discarded_not_counted_as_model_error(self):
        cfg=Settings(window_title='generic',allowed_keys=['a']);supervisor=SemanticSupervisor(cfg,LocalLLM(cfg),lambda _:None,threading.Event())
        supervisor.last=0;supervisor.results.put({'cancelled':True,'error':'OperationCancelled','scope':'old','ms':4})
        with tempfile.TemporaryDirectory() as folder:
            store=ExperienceStore(Path(folder)/'w.sqlite','p','s')
            try:supervisor.tick(measured(),ForwardModel(store).candidates(cfg),1,0,{},None,ForwardModel(store),{})
            finally:supervisor.close();store.close()
        self.assertEqual(supervisor.errors,0);self.assertEqual(supervisor.discarded,1)
    def test_duration_exploration_does_not_triple_first_pass_before_any_prediction_check(self):
        cfg=Settings(window_title='generic',allowed_keys=['a','b'])
        with tempfile.TemporaryDirectory() as folder:
            store=ExperienceStore(Path(folder)/'w.sqlite','p','s');model=ForwardModel(store)
            try:
                selected,ranking,_=model.choose(measured(),model.candidates(cfg))
                self.assertTrue(all(x['candidate']['hold_ms']==80 for x in ranking))
                action=selected['candidate']
                for step in (1,2):model.observe(measured(),action,measured(),model.predict(measured(),action['id']),{'os_submission':'accepted'},step)
                _,ranking,_=model.choose(measured(),model.candidates(cfg))
                self.assertTrue(any(x['candidate']['hold_ms']>80 for x in ranking))
            finally:store.close()
