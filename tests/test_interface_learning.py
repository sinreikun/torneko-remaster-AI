"""Observed-input and Unicode persistence contracts; no game simulation."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from autoplayer.important_events import ImportantEvents
from autoplayer.core import Settings,parse_decision
from autoplayer.active_input import ActiveInput
from autoplayer.fast_learning import FastLearningGate
from autoplayer.experience_review import ExperienceReview
import test_policy as fixtures
state=fixtures.state
receipt_row=fixtures.receipt_row
from test_agent import change

def interface(name):
    s=state(name);s.update(eligible=False,objective='interface',text_key=name,text_reliable=True,metrics={});return s

class InterfaceContracts(unittest.TestCase):
    setUp=fixtures.PolicyTests.setUp
    def test_new_text_is_information_and_loops_do_not_keep_rewarding_it(self):
        a,b=interface('A'),interface('B')
        first=self.policy.learn(a,b,receipt_row('num1'),a)
        self.assertEqual(first['interface_information_gain'],.1);self.assertFalse(first['interface_gain_is_goal_progress'])
        self.assertEqual(self.policy.learn(b,a,receipt_row('num1'),b)['interface_information_gain'],0)
        self.assertEqual(self.policy.learn(a,b,receipt_row('num1'),a)['interface_information_gain'],0)
    def test_natural_or_low_confidence_text_changes_are_not_information_reward(self):
        a,b,c=interface('A'),interface('B'),interface('C')
        self.assertEqual(self.policy.learn(a,b,receipt_row('num1'),c)['interface_information_gain'],0)
        b=interface('D');b['text_reliable']=False
        self.assertEqual(self.policy.learn(a,b,receipt_row('num1'),a)['interface_information_gain'],0)
    def test_reuses_only_positive_repeated_current_state_inputs_when_text_stable(self):
        a=interface('A');decision=parse_decision(json.dumps(change(action={'keys':[],'hold_ms':1,'wait_ms':0})),self.cfg)
        self.store.db.execute('INSERT INTO policy_actions(profile,state,objective,action,trials,q) VALUES(?,?,?,?,?,?)',(self.policy.profile,a['id'],'interface','num1',2,.05));self.store.db.commit()
        selected,report=self.policy.choose(decision,a,interface_stable=True)
        self.assertEqual(selected.keys,['num1']);self.assertEqual(report['source'],'learned_interface_policy');self.assertEqual(selected.confidence,0)
        for kw in ({'interface_stable':False},{'interface_stable':True,'blocked':[['num1']]},{'interface_stable':True,'respect_proposal':True}):
            self.assertEqual(self.policy.choose(decision,a,**kw)[1]['source'],'model_or_neutral')
    def test_missing_map_with_visible_hud_does_not_become_text_only_policy(self):
        a,b=interface('A'),interface('B');a['metrics']={'hp':{'values':[15,15]}};b['metrics']=a['metrics']
        self.assertEqual(self.policy.learn(a,b,receipt_row('num1'),a)['interface_information_gain'],0)
    def test_model_confirm_is_not_replaced_by_old_attempt_count_but_backoff_still_wins(self):
        cfg=Settings();active=ActiveInput(cfg);decision=parse_decision(json.dumps(change(confidence=.5)),cfg)
        for _ in range(4):active.sent(decision)
        selected,_=active.select(decision,preserve_proposal=True);self.assertEqual(selected.keys,['num1'])
        selected,_=active.select(decision,blocked=[['num1']],preserve_proposal=True);self.assertNotEqual(selected.keys,['num1'])
        selected,_=active.select(decision,{'lines':[{'text':'セーブ削除しますか'}]},preserve_proposal=True);self.assertNotEqual(selected.keys,['num1'])
    def test_repeated_nonspatial_text_requests_bounded_review_without_metric_changes(self):
        review=ExperienceReview();review.rows=[{'situation':'a','keys':['num1'],'metric_changes':{},'reward_proxy':-.04,'forecast_status':'not_evaluated','nonspatial':True,'text_after':'dialog'} for _ in range(16)]
        context=review.context();self.assertEqual(context['repeated_interface_observations'],8)
        gate=FastLearningGate(128);gate.evaluate(0,{}, {},{},context)
        self.assertFalse(gate.evaluate(31,{}, {},{},context)['llm_requested'])
        self.assertEqual(gate.evaluate(32,{}, {},{},context)['reason'],'accumulated_experience_review')
    def test_visible_options_are_saved_even_when_model_scene_is_unknown(self):
        with tempfile.TemporaryDirectory() as t:
            events=ImportantEvents(t);events.observe({'scene':'unknown','ocr':{'lines':[{'text':'はい'},{'text':'いいえ'}]}},b'',b'')
            self.assertTrue((Path(t)/'event-10/event.json').exists())
    def test_event_metadata_uses_utf8_even_with_cp932_defaults(self):
        read,write=Path.read_text,Path.write_text
        def cp_read(path,*args,**kw):kw.setdefault('encoding','cp932');return read(path,*args,**kw)
        def cp_write(path,data,*args,**kw):kw.setdefault('encoding','cp932');return write(path,data,*args,**kw)
        with tempfile.TemporaryDirectory() as t,patch.object(Path,'read_text',cp_read),patch.object(Path,'write_text',cp_write):
            events=ImportantEvents(t);row={'scene':'dialog','reason':'© 🧭 日本語'};events.observe(row,b'',b'');events.observe({'reason':'次の入力 ©'},b'',b'')
            content=json.loads((Path(t)/'event-10/event.json').read_bytes().decode('utf-8'))
            self.assertEqual(content['action']['reason'],row['reason']);self.assertEqual(content['following_action']['reason'],'次の入力 ©')
