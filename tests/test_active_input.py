"""Input-selection/stop contracts. No simulated game or claim of gameplay success."""
import json,tempfile,threading,unittest
from pathlib import Path
from autoplayer.active_input import ActiveInput
from autoplayer.core import Controller,Settings,decision_schema,parse_decision
from autoplayer.grounding import reconcile
from test_agent import Backend,Script,change

class ActiveInputTests(unittest.TestCase):
    def test_no_input_proposals_always_select_and_rotate_allowed_keys(self):
        cfg=Settings();active=ActiveInput(cfg);chosen=[]
        for _ in range(18):
            proposal=parse_decision(json.dumps(change(confidence=0,action={'keys':[],'hold_ms':1,'wait_ms':0})),cfg)
            d,report=active.select(proposal,{},None,blocked=[[k] for k in cfg.allowed_keys])
            self.assertTrue(d.keys);self.assertTrue(set(d.keys)<=set(cfg.allowed_keys));self.assertLessEqual(d.hold_ms,80)
            self.assertEqual(d.confidence,0);self.assertFalse(report['learning']);active.sent(d);chosen.append(tuple(d.keys))
        self.assertGreater(len(set(chosen)),3)
        self.assertFalse(any('num7' in keys or 'num6' in keys for keys in chosen))
    def test_unknown_dialog_0131_proposal_is_sent_despite_half_confidence(self):
        cfg=Settings();d=parse_decision(json.dumps(change(scene='unknown',confidence=.5,subgoal='このダンジョンをやりなおしますか？')),cfg)
        result,report=ActiveInput(cfg).select(d,{'status':'ok','lines':[{'text':'このダンジョンをやりなおしますか？'},{'text':'いいえ'},{'text':'はい'}]})
        self.assertEqual(result.keys,['num1']);self.assertEqual(result.confidence,.5)
        self.assertEqual(report['source'],'uncertain_input_trial')
    def test_missing_target_becomes_alternative_trial_not_indefinite_wait(self):
        cfg=Settings();d=parse_decision(json.dumps(change(scene='gameplay',intent={'kind':'approach','target_id':'missing'})),cfg)
        held,guard=reconcile(d,{'objects':[]},{},{},cfg)
        self.assertEqual(held.keys,[])
        selected,report=ActiveInput(cfg).select(held,reason=guard['reason'])
        self.assertTrue(selected.keys);self.assertFalse(report['learning']);self.assertEqual(selected.intent['target_id'],'')
    def test_destructive_confirmation_cancels_instead_of_confirming(self):
        cfg=Settings();d=parse_decision(json.dumps(change()),cfg)
        d,report=ActiveInput(cfg).select(d,{'status':'ok','lines':[{'text':'セーブデータを削除しますか？'},{'text':'はい'}]})
        self.assertEqual(d.keys,['num2']);self.assertEqual(d.status,'continue')
    def test_custom_game_uses_only_its_configured_keys(self):
        cfg=Settings(window_title='Other',allowed_keys=['z','x'],key_bindings={'z':'決定','x':'キャンセル'})
        active=ActiveInput(cfg);d=parse_decision(json.dumps(change(action={'keys':[],'hold_ms':1,'wait_ms':0})),cfg)
        for _ in range(10):
            selected,_=active.select(d);self.assertTrue(set(selected.keys)<={'z','x'});active.sent(selected)
    def test_only_completed_schema_branch_allows_empty_action(self):
        for branch in decision_schema(Settings())['oneOf']:
            keys=branch['properties']['action']['properties']['keys']
            forecast_keys=branch['properties']['forecast']['properties']['continuations']['items']['properties']['keys']
            self.assertEqual(forecast_keys['minItems'],1)
            if branch['properties']['status']['enum']==['complete']:self.assertEqual(keys['maxItems'],0)
            else:self.assertEqual(keys['minItems'],1);self.assertNotIn('blocked',branch['properties']['status']['enum'])
    def test_release_and_no_input_after_actual_send_failure(self):
        class Rejected(Backend):
            def act(self,*args):raise RuntimeError('OS submission rejected')
        b=Rejected()
        with tempfile.TemporaryDirectory() as t:
            agent=Controller(Settings(max_steps=1),b,Script([change(confidence=.1)]),lambda _:None,Path(t)/'run')
            with self.assertRaisesRegex(RuntimeError,'OS submission'):agent.run()
        self.assertEqual(b.actions,[]);self.assertEqual(b.releases,1)
    def test_stop_still_wins_over_inference_failure_fallback(self):
        stop=threading.Event();b=Backend()
        class Failed:
            def decide(self,*args):stop.set();raise ValueError('invalid model response')
        with tempfile.TemporaryDirectory() as t:
            Controller(Settings(max_steps=1),b,Failed(),lambda _:None,Path(t)/'run',stop).run()
        self.assertEqual(b.actions,[]);self.assertEqual(b.releases,1)
