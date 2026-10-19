"""Finite-state, SQLite and API contracts only; no rendered or simulated game."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from autoplayer.core import Settings,LocalLLM,parse_decision
from autoplayer.memory import MemoryStore
from autoplayer.policy import ExperiencePolicy
from autoplayer.improvement import ImprovementSupervisor
from test_policy import state,receipt_row
from test_agent import change

class ImprovementTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cfg=Settings().validate();self.store=MemoryStore(Path(self.tmp.name)/'memory.sqlite',self.cfg)
        self.addCleanup(self.store.close);self.policy=ExperiencePolicy(self.store,self.cfg)
        self.audit=ImprovementSupervisor(self.policy);self.llm=Mock()
    def row(self,step=1,signal=None,keys=('w',),applied='avoid_cycle',**kwargs):
        return {'step':step,'policy_state':state(),'policy_after':state('B',261.7),
            'policy_learning':{'status':'trained','net_signals':signal or {}},
            'policy_choice':{'adjustment':{'strategy':applied}},**receipt_row(keys[0] if keys else 'w'),
            'observed_keys':list(keys),**kwargs}
    def seed(self):
        for i in range(6):
            self.audit.begin_step();self.audit.consume(self.row(i+1))
        self.assertTrue(self.audit.due())
    def proposal(self,strategy='avoid_cycle',signal='coverage'):
        return {'hypothesis':'直近の往復を避けて未確認の通路を検証する','strategy':strategy,
            'evidence_steps':[1,2],'expected_signal':signal}
    def start(self,strategy='avoid_cycle'):
        self.seed();self.llm.review_strategy.return_value=self.proposal(strategy)
        result=self.audit.review(self.llm);self.assertEqual(result['status'],'trial_started')
    def test_progress_prevents_unnecessary_review(self):
        self.seed();self.audit.consume(self.row(7,{'coverage':.1}))
        self.assertFalse(self.audit.due());self.assertIsNone(self.audit.review(self.llm))
        self.llm.review_strategy.assert_not_called()
    def test_trial_reverts_if_only_movement_and_screen_change(self):
        self.start()
        for i in range(4):result=self.audit.consume(self.row(i+7,{'position':1,'visual':1}))
        self.assertEqual(result['status'],'reverted');self.assertIsNone(self.audit.adopted)
        self.assertFalse(self.audit.due())
    def test_declared_signal_only_temporary_adoption_and_expiry(self):
        self.start()
        for i in range(4):result=self.audit.consume(self.row(i+7,{'coverage':.1}))
        self.assertEqual(result['status'],'adopted_temporary')
        self.assertEqual(self.audit.adopted['remaining'],24)
        for _ in range(24):self.audit.begin_step()
        self.assertIsNone(self.audit.adjustment(state()));self.assertFalse(self.audit.due())
    def test_different_signal_does_not_validate_the_hypothesis(self):
        self.start()
        for i in range(4):result=self.audit.consume(self.row(i+7,{'food_gain':.5}))
        self.assertEqual(result['status'],'reverted')
    def test_damage_revokes_trial_and_adopted_adjustment(self):
        self.start();bad=self.row(7);bad['policy_after']['metrics']['hp']['values']=[14,15]
        self.assertEqual(self.audit.consume(bad)['status'],'reverted')
        self.audit.adopted={'proposal':self.proposal(),'floor':[1],'remaining':24,'avoid':[]}
        self.assertEqual(self.audit.consume(bad)['status'],'reverted');self.assertIsNone(self.audit.adopted)
    def test_missing_results_timeout_instead_of_infinite_review(self):
        self.start()
        for _ in range(9):self.audit.begin_step()
        self.assertIsNone(self.audit.active);self.assertIsNone(self.audit.adopted)
    def test_unsent_diagnostic_dry_and_unapplied_actions_cannot_validate(self):
        self.start()
        for extra in ({'executed':False},{'dry_run':True},{'event':'diagnostic'},
                      {'learning':False},{'turn_kind':'aborted'},
                      {'input_receipt':{'os_submission':'rejected'}},{'policy_choice':{'adjustment':None}}):
            self.audit.consume(self.row(7,{'coverage':1},**extra))
        self.assertEqual(self.audit.active['samples'],0)
    def test_no_same_strategy_for_same_stall_after_restart(self):
        self.start()
        self.audit.finish(False,'不明')
        reopened=ImprovementSupervisor(self.policy)
        reopened.recent=copy.deepcopy(self.audit.recent);reopened.tick=30
        self.llm.review_strategy.return_value=self.proposal()
        self.assertEqual(reopened.review(self.llm)['status'],'review_rejected')
        self.assertIsNone(reopened.active)
    def test_interrupted_trial_is_not_promoted_and_profile_isolated(self):
        self.start();reopened=ImprovementSupervisor(self.policy)
        self.assertIsNone(reopened.active);self.assertIsNone(reopened.adopted)
        other=ImprovementSupervisor(ExperiencePolicy(self.store,Settings(goal='別の目標').validate()))
        self.assertEqual(other.attempts,{})
    def test_untrusted_or_fabricated_proposal_and_call_budget(self):
        self.seed()
        for proposal in ({**self.proposal(),'strategy':'rewrite_code'},
                         {**self.proposal(),'evidence_steps':[999]},
                         {**self.proposal(),'safety':False}):
            self.llm.review_strategy.return_value=proposal
            self.assertEqual(self.audit.review(self.llm)['status'],'review_rejected')
            self.audit.tick+=12
        self.assertFalse(self.audit.due());self.assertEqual(self.llm.review_strategy.call_count,3)
    def test_neutral_observations_only_allow_reassessment(self):
        for i in range(6):
            self.audit.begin_step();self.audit.consume(self.row(i+1,keys=(),executed=False))
        self.assertTrue(self.audit.due())
        self.assertIn('avoid_cycle',self.audit.evidence()['unavailable_strategies'])
        self.llm.review_strategy.return_value=self.proposal('reassess')
        self.assertEqual(self.audit.review(self.llm)['status'],'trial_started')
        self.audit.finish(False,'不明');self.audit.tick+=12
        self.assertFalse(self.audit.due())
    def test_priority_adjustment_never_bypasses_hazard_or_blocked_keys(self):
        proposal=parse_decision(json.dumps(change(action={'keys':['w'],'hold_ms':80,'wait_ms':0})),self.cfg)
        adjustment={'strategy':'avoid_cycle','avoid':[['A','w']]}
        chosen,detail=self.policy.choose(proposal,state(),stable=True,adjustment=adjustment)
        self.assertNotEqual(chosen.keys,['w']);self.assertEqual(detail['adjustment'],adjustment)
        dangerous=state();dangerous['minimap']['enemies']=[(1,0)]
        chosen,detail=self.policy.choose(proposal,dangerous,stable=True,adjustment=adjustment)
        self.assertEqual(detail['source'],'model_hazard_review')
        chosen,_=self.policy.choose(proposal,state(),blocked=[[k] for k in 'wasd'],stable=True,adjustment=adjustment)
        self.assertEqual(chosen.keys,[])
    def test_floor_change_disables_adjustment(self):
        self.start();different=state();different['metrics']['floor']['values']=[2]
        self.assertIsNone(self.audit.adjustment(different))
    def test_controller_calls_reviewer_applies_trial_and_exports_evidence(self):
        from types import SimpleNamespace
        from autoplayer.core import Controller
        from autoplayer.diagnostics import export_diagnostics
        from test_agent import Backend,Script
        import zipfile
        class ReceiptBackend(Backend):
            def act(self,keys,ms,stop):
                super().act(keys,ms,stop)
                return {'os_submission':'accepted','events':[{'message':'WM_KEYDOWN'}]}
        backend=ReceiptBackend()
        backend.observe_turn=Mock(side_effect=[SimpleNamespace(image=b'current',kind='normal',changed=False,raw_change=0,masked_activity=0,duration_ms=1000),SimpleNamespace(image=b'after',kind='normal',changed=True,raw_change=.3,masked_activity=0,duration_ms=1000)])
        script=Script([change(action={'keys':['w'],'hold_ms':80,'wait_ms':0})])
        script.ocr=SimpleNamespace(read=Mock(return_value={'status':'ok','lines':[]}))
        script.review_strategy=Mock(return_value=self.proposal())
        agent=Controller(Settings(max_steps=1),backend,script,lambda row:None,Path(self.tmp.name)/'run.jsonl',memory=self.store)
        self.seed();agent.improvement.recent=copy.deepcopy(self.audit.recent);agent.improvement.tick=6
        agent.policy.observe=Mock(side_effect=[state(),state(),state('B',261.7)])
        agent.run()
        script.review_strategy.assert_called_once()
        self.assertEqual(backend.actions[0][0],['a'])
        rows=[json.loads(line) for line in agent.journal.read_text().splitlines()]
        self.assertTrue(any(r['event']=='self_improvement' for r in rows))
        result=next(r for r in reversed(rows) if r['event']=='result')
        self.assertEqual(result['self_improvement']['status'],'trial')
        self.assertEqual(rows[-1]['status'],'reverted')
        archive=Path(self.tmp.name)/'diagnostics.zip'
        export_diagnostics(Path(self.tmp.name),archive)
        with zipfile.ZipFile(archive) as z:
            summary=json.loads(z.read('manifest.json'))['databases']['memory.sqlite']
            self.assertTrue(summary['recent_improvement_events'])
            self.assertTrue(summary['improvement_state'])

    def test_missing_floor_does_not_mean_scene_transition(self):
        self.start()
        missing=self.row(7);missing['policy_state']['metrics'].pop('floor')
        self.audit.consume(missing)
        self.assertIsNotNone(self.audit.active)
        self.assertEqual(self.audit.active['floor'],[1])
        self.audit.consume(self.row(8))
        self.assertIsNotNone(self.audit.active)
        different=self.row(9);different['policy_state']['metrics']['floor']['values']=[2]
        self.assertEqual(self.audit.consume(different)['status'],'reverted')
    def test_incomplete_spatial_evidence_only_allows_reassessment(self):
        for i in range(6):
            self.audit.begin_step();row=self.row(i+1);row['policy_state']['eligible']=False
            self.audit.consume(row)
        self.assertTrue(self.audit.due())
        self.llm.review_strategy.return_value=self.proposal('explore_untried')
        self.assertEqual(self.audit.review(self.llm)['status'],'review_rejected')
        self.assertIsNone(self.audit.active)
    def test_review_respects_user_minimap_legend(self):
        self.seed();self.llm.review_strategy.return_value={**self.proposal(),'hypothesis':'青い物体の影響を評価するため操作する'}
        self.assertIn('ミニマップ',self.audit.evidence()['user_control_reference']['minimap']['note'])
        self.assertEqual(self.audit.review(self.llm)['status'],'review_rejected')
        self.assertIn('ミニマップ',self.audit.evidence()['previous_rejection'])

    def test_review_api_is_text_only_and_requests_japanese(self):
        llm=LocalLLM(self.cfg);llm._request=Mock(return_value=json.dumps(self.proposal(),ensure_ascii=False))
        self.assertEqual(llm.review_strategy({'observations':[]}),self.proposal())
        args=llm._request.call_args.args
        self.assertIsNone(args[0]);self.assertIn('日本語',args[1]);self.assertEqual(args[2]['observations'],[])
