"""Layout grouping and bounded response-transfer contracts, not gameplay tests."""
import json,tempfile,unittest
from pathlib import Path
import test_policy as fixtures
from test_agent import change
from autoplayer.core import Settings,parse_decision
from autoplayer.policy import ExperiencePolicy
from autoplayer.memory import MemoryStore
from autoplayer.interface_responses import layout_group
from autoplayer.information_exploration import InformationExploration


def native(text):return {'status':'ok','lines':[{'text':text,'confidence':.99,'words':[{'box':[230,502,179,38]}]}]}
def state(text):
    s=fixtures.state(text);s.update(eligible=False,objective='interface',text_key=text,text_reliable=True,metrics={})
    s['interface_layout_group']=layout_group(native(text),[1280,720],{})
    return s

class TransferTests(unittest.TestCase):
    setUp=fixtures.PolicyTests.setUp
    def record(self,a,b,key='num1',**patch):
        before=state(a);return self.policy.interface_responses.record(before,state(b),dict(fixtures.receipt_row(key),**patch),before)
    def train(self):
        for a,b in [('A','B'),('C','D'),('E','F')]:self.record(a,b)
    def test_layout_shares_prose_but_separates_options_and_missing_evidence(self):
        group=layout_group(native('家老「うむ！'),[1280,720],{})
        self.assertEqual(group,layout_group(native('火炎草という草があるっ！'),[1280,720],{}))
        self.assertNotEqual(group['id'],layout_group(native('はい'),[1280,720],{})['id'])
        self.assertIsNone(layout_group(native('文章'),[1280,720],{'hp':{'values':[15,15]}}))
        bad=native('文章');bad['lines'][0]['confidence']=.7
        self.assertIsNone(layout_group(bad,[1280,720],{}));self.assertIsNone(layout_group(native('文章'),[],{}))
        bad['lines'][0]['confidence']=float('nan');self.assertIsNone(layout_group(bad,[1280,720],{}))
        bad=native('文章');bad['lines'][0]['words'][0]['box']=[float('nan'),502,179,38]
        self.assertIsNone(layout_group(bad,[1280,720],{}))
    def test_coverage_survives_new_prose_without_claiming_full_comprehension(self):
        model=InformationExploration(self.cfg);d=parse_decision(json.dumps(change()),self.cfg)
        self.assertEqual(model.situation(d,native('文章1'),state('文章1')),model.situation(d,native('文章2'),state('文章2')))
        self.assertNotEqual(model.situation(d,native('文章1'),state('文章1')),model.situation(d,native('はい'),state('はい')))
    def test_positive_response_transfers_to_new_text_with_honest_confidence(self):
        self.train();d=parse_decision(json.dumps(change(action={'keys':[],'hold_ms':1,'wait_ms':0})),self.cfg)
        selected,report=self.policy.choose(d,state('unseen'),interface_stable=True)
        self.assertEqual(selected.keys,['num1']);self.assertEqual(selected.confidence,0)
        self.assertEqual(report['selected']['source_text_states'],3);self.assertFalse(report['selected']['goal_success_verified'])
        self.assertEqual(report['selected']['status'],'unverified_layout_transfer')
    def test_two_same_text_failures_or_backoff_or_options_stop_transfer(self):
        self.train();model=self.policy.interface_responses
        self.assertEqual(model.candidates(state('new'),['num1'],[['num1']]),[])
        self.assertEqual(model.candidates(state('はい'),['num1'],[]),[])
        self.record('new','new');self.record('new','new')
        self.assertEqual(model.candidates(state('new'),['num1'],[]),[])
    def test_natural_change_diagnostics_and_unknown_submission_do_not_train(self):
        model=self.policy.interface_responses;a=state('A')
        model.record(a,state('B'),fixtures.receipt_row('num1'),state('typing'))
        self.record('A','B',event='diagnostic');self.record('A','B',input_receipt={})
        self.assertEqual(model.samples(a['interface_layout_group']['id'],'num1'),[])
    def test_one_source_or_one_effect_does_not_justify_transfer(self):
        for _ in range(3):self.record('A','B')
        self.assertEqual(self.policy.interface_responses.candidates(state('new'),['num1'],[]),[])
    def test_policy_feedback_records_the_observed_response(self):
        a,b=state('A'),state('B');result=self.policy.learn(a,b,fixtures.receipt_row('num1'),a)
        self.assertEqual(result['interface_response']['status'],'recorded')
        self.assertTrue(result['interface_response']['text_response'])
    def test_response_window_is_bounded_and_persists(self):
        for i in range(40):self.record('文'+str(i),'文'+str(i+1))
        group=state('x')['interface_layout_group']['id']
        self.assertEqual(len(self.policy.interface_responses.samples(group,'num1')),32)
        self.store.close();self.store=MemoryStore(self.path,self.cfg)
        model=ExperiencePolicy(self.store,self.cfg).interface_responses
        self.assertEqual(len(model.samples(group,'num1')),32)
        from dataclasses import replace
        other=ExperiencePolicy(self.store,replace(self.cfg,allowed_keys=['num1']))
        self.assertEqual(other.interface_responses.samples(group,'num1'),[])
