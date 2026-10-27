import tempfile,unittest
from pathlib import Path
from autoplayer.experience_review import ExperienceReview,outcome_learning_allowed
from autoplayer.memory import MemoryStore
from autoplayer.core import Settings
from autoplayer.fast_learning import FastLearningGate

class ExperienceTests(unittest.TestCase):
    def row(self):return {'executed':True,'action_kind':'input','learning':False,'active_input':{'source':'information_collection'},'input_receipt':{'os_submission':'accepted'},'observed_keys':['w'],'information_exploration':{'situation':'one'},'policy_learning':{'reward':-.1,'net_signals':{'position':0}},'forecast_evaluation':{'status':'mismatch'}}
    def state(self,hp):return {'observable':True,'metrics':{'hp':{'values':[hp,15]}},'frame_sha256':'frame'}
    def test_exploration_outcomes_separate_from_reports_and_diagnostics(self):
        row=self.row();self.assertTrue(outcome_learning_allowed(row));self.assertFalse(row['learning'])
        self.assertTrue(outcome_learning_allowed(dict(row,active_input={'source':'active_input_trial'})))
        for patch in [{'event':'diagnostic'}, {'kind':'separation'},{'active_input':{'source':'other'}}]:self.assertFalse(outcome_learning_allowed(dict(row,**patch)))
    def test_summary_persists_bounded_and_profile_scoped(self):
        with tempfile.TemporaryDirectory() as t:
            cfg=Settings(window_title='one');path=Path(t)/'m.db';m=MemoryStore(path,cfg);review=ExperienceReview(m)
            for _ in range(70):review.record(self.row(),self.state(15),self.state(14))
            self.assertEqual(len(review.rows),64);m.close();m=MemoryStore(path,cfg)
            review=ExperienceReview(m);context=review.context();self.assertEqual(context['retained_observations'],64)
            self.assertEqual(review.rows[-2]['metric_persistence']['hp'],1);self.assertEqual(review.rows[-5]['metric_persistence']['hp'],4)
            self.assertEqual(context['action_groups'][0]['forecast_mismatches'],64);self.assertEqual(context['recent_observations'][-1]['metric_changes']['hp']['after'],[14,15]);m.close()
            m=MemoryStore(path,Settings(window_title='two'));self.assertEqual(ExperienceReview(m).rows,[]);m.close()
    def test_unsent_dry_abort_and_missing_frame_do_not_become_experience(self):
        review=ExperienceReview()
        for patch in [{'executed':False},{'dry_run':True},{'turn_kind':'aborted'},{'input_receipt':{}}]:review.record(dict(self.row(),**patch),self.state(15),self.state(14))
        review.record(self.row(),{},self.state(14));self.assertEqual(review.rows,[])
    def test_review_budget_requires_evidence_and_32_turns(self):
        gate=FastLearningGate(128);context={'retained_observations':32,'action_groups':[{'forecast_mismatches':2}]}
        self.assertTrue(gate.evaluate(0,{}, {},{},context)['llm_requested'])
        self.assertFalse(gate.evaluate(31,{}, {},{},context)['llm_requested'])
        self.assertEqual(gate.evaluate(32,{}, {},{},context)['reason'],'accumulated_experience_review')
        self.assertFalse(gate.evaluate(33,{}, {},{},context)['llm_requested'])
