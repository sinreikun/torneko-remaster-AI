"""Observed-transition/SQLite and dispatch contracts; no game simulation."""
import copy,json,tempfile,unittest
from pathlib import Path
from autoplayer.core import Settings,Decision
from autoplayer.memory import MemoryStore
from autoplayer.policy import ExperiencePolicy
from autoplayer.learned_predictions import LearnedPredictions
from autoplayer.information_exploration import InformationExploration
from autoplayer.experience_review import ExperienceReview,outcome_learning_allowed
from autoplayer.combat import CombatExperiments
from test_policy import state,receipt_row


def decision(keys=('w',)):return Decision('gameplay','','','',.9,'continue',list(keys),80,600)
def event(a='A',b='B',key='w',reward=.1,delta=(0,-1)):
    return {'status':'trained','state':a,'next_state':b,'objective':'explore','keys':[key],'reward':reward,'player_delta':list(delta),'hp_delta':0}

class PredictionTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.path=Path(t.name)/'m.sqlite';self.cfg=Settings()
        self.memory=MemoryStore(self.path,self.cfg);self.addCleanup(self.memory.close);self.policy=ExperiencePolicy(self.memory,self.cfg)
        self.model=LearnedPredictions(self.policy)
    def add(self,*events):
        for e in events:
            self.model.add(e)
            self.memory.db.execute('INSERT INTO policy_events(profile,created,data) VALUES(?,0,?)',(self.policy.profile,json.dumps(e)))
        self.memory.db.commit()
    def choice(self):
        return {'source':'learned_policy','context':{'ranking':[{'keys':[k],'automatic_candidate':True,'q':0,'trials':2} for k in ('w','a')],'no_effect_moves':{}},'selected':{'keys':['w'],'trials':2,'q':0}}
    def test_lookahead_uses_only_observed_successors_and_changes_priority(self):
        self.add(*[event(key='w',reward=.2)]*2,*[event(b='C',key='a',reward=.05,delta=(-1,0))]*2,*[event(a='C',b='D',key='a',reward=.6)]*2)
        selected,choice,context=self.model.choose(decision(),state(),self.choice())
        self.assertEqual(selected.keys,['a']);self.assertEqual(choice['source'],'learned_outcome_prediction')
        self.assertEqual(context['selected']['supported_horizon'],2)
        self.assertEqual(selected.forecast['first_expected']['player_delta'],[-1,0]);self.assertEqual(selected.forecast['horizon'],1)
    def test_cycles_and_unknown_future_do_not_inflate_returns(self):
        self.add(*[event(b='A',reward=.2)]*2)
        p=self.model.context(state())['predictions'][0]
        self.assertAlmostEqual(p['expected_return'],.2);self.assertEqual(p['supported_horizon'],1)
    def test_three_step_limit_and_two_sample_minimum(self):
        self.add(event());self.assertEqual(self.model.context(state())['predictions'],[])
        self.add(event(),*[event(a='B',b='C',reward=.1)]*2,*[event(a='C',b='D',reward=.1)]*2,*[event(a='D',b='E',reward=99)]*2)
        p=self.model.context(state())['predictions'][0]
        self.assertEqual(p['supported_horizon'],3);self.assertAlmostEqual(p['expected_return'],.196)
    def test_legacy_values_are_loaded_without_inventing_missing_coordinates(self):
        e=event();e.pop('player_delta');e.pop('hp_delta');self.add(e,e)
        reopened=LearnedPredictions(ExperiencePolicy(self.memory,self.cfg));prediction=reopened.context(state())['predictions'][0]
        self.assertEqual(prediction['trials'],2);self.assertIsNone(prediction['player_delta'])
        other=ExperiencePolicy(self.memory,Settings(window_title='Other'));self.assertEqual(LearnedPredictions(other).context(state())['predictions'],[])
    def test_negative_only_or_blocked_evidence_does_not_force_reuse(self):
        self.add(*[event(reward=-.5)]*2)
        selected,choice,_=self.model.choose(decision(),state(),self.choice());self.assertEqual(choice['source'],'learned_policy')
        self.add(*[event(key='a',reward=.9)]*2)
        _,choice,_=self.model.choose(decision(),state(),self.choice(),[['a']]);self.assertEqual(choice['source'],'learned_policy')
    def test_predictions_are_checked_against_actual_input_and_observation(self):
        self.add(event(),event());p=self.model.context(state())['predictions'][0]
        self.assertEqual(self.model.evaluate(p,event(),['w'])['status'],'matched')
        self.assertEqual(self.model.evaluate(p,event(delta=(1,0)),['w'])['status'],'mismatch')
        self.assertEqual(self.model.evaluate(p,{'status':'censored'},['w'])['status'],'unverifiable')
        self.assertEqual(self.model.evaluate(p,event(),['a'])['status'],'not_applied')
    def test_known_equipment_conditions_do_not_reuse_unknown_or_different_conditions(self):
        self.add(event(),event());s=state();s['metrics']['sword']={'values':[5]}
        self.assertEqual(self.model.context(s)['predictions'],[])
        e=dict(event(),conditions={'sword':[5]});self.add(e,e)
        self.assertEqual(self.model.context(s)['predictions'][0]['trials'],2)
        s['metrics']['sword']['values']=[6];self.assertEqual(self.model.context(s)['predictions'],[])
    def test_different_state_or_nonempirical_event_is_not_reused(self):
        self.add(event(),event());self.model.add(dict(event(),status='not_trained'))
        self.assertEqual(len(self.model.events),2);self.assertEqual(self.model.context(state('different'))['predictions'],[])
        self.assertEqual(self.model.context({'eligible':False})['status'],'unavailable')

class ReuseAndCombatTests(unittest.TestCase):
    def test_good_experience_is_retained_with_periodic_exploration(self):
        cfg=Settings(window_title='Other',allowed_keys=['w','a']);info=InformationExploration(cfg)
        s={'experience_preference':{'keys':['w'],'trials':3,'expected_value':.4}}
        native={'status':'ok','lines':[{'text':'操作画面'}]}
        results=[info.choose(decision(),native,s)[1] for _ in range(8)]
        self.assertTrue(all(r.get('retained_experience') for r in results[:7]));self.assertTrue(results[7].get('selected'))
        s['experience_preference']['expected_value']=-.4
        self.assertFalse(info.choose(decision(),native,s)[1].get('retained_experience'))
    def test_combat_experience_saved_separately_from_model_reports(self):
        row=receipt_row('num1',learning=False,combat_experiment={'keys':['num1']},combat_evaluation={'status':'updated','reward':.7},battle_observations=[{'kind':'defeated_enemy'}])
        self.assertTrue(outcome_learning_allowed(row));self.assertFalse(outcome_learning_allowed(dict(row,event='diagnostic')))
        review=ExperienceReview();self.assertEqual(review.record(row,state(),state())['status'],'recorded')
        self.assertEqual(review.rows[0]['combat_evaluation']['reward'],.7)
        self.assertFalse(outcome_learning_allowed(dict(row,observed_keys=['w'])))
    def test_successful_attack_continues_only_with_current_contact(self):
        with tempfile.TemporaryDirectory() as t:
            cfg=Settings();m=MemoryStore(Path(t)/'m.sqlite',cfg)
            try:
                c=CombatExperiments(m,cfg);s=state();s['minimap']['enemies']=[(0,1)]
                _,orient=c.choose(decision(),s,True)
                c.record(orient,receipt_row(observed_keys=orient['keys'],policy_state=s,policy_after=s),{})
                attack,trial=c.choose(decision(),s,True)
                row=receipt_row('num1',policy_state=s,policy_after=s,battle_observations=[{'kind':'damage_dealt','new_message':True}])
                c.record(trial,row,{});again,trial=c.choose(decision(),s,True)
                self.assertEqual(again.keys,['num1']);self.assertEqual(trial['past_trials'],1)
                row['battle_observations'].append({'kind':'defeated_enemy','new_message':True});c.record(trial,row,{})
                self.assertIsNone(c.pending)
                c=CombatExperiments(m,cfg);_,next_trial=c.choose(decision(),s,True)
                self.assertEqual(next_trial['action'],'orient_attack') # learned positive beats untried walking
            finally:m.close()

class PredictionDispatchTests(unittest.TestCase):
    def test_persisted_prediction_reaches_actual_input_and_is_evaluated(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from autoplayer.core import Controller
        from test_agent import Backend,Script,change
        class ReceiptBackend(Backend):
            def act(self,keys,ms,stop):
                super().act(keys,ms,stop)
                return {'os_submission':'accepted','events':[{'message':'WM_KEYDOWN'}]}
        with tempfile.TemporaryDirectory() as t:
            cfg=Settings(max_steps=1);memory=MemoryStore(Path(t)/'m.sqlite',cfg)
            try:
                policy=ExperiencePolicy(memory,cfg)
                for _ in range(3):memory.db.execute('INSERT INTO policy_events(profile,created,data) VALUES(?,0,?)',(policy.profile,json.dumps(event(b='C',key='a',reward=.15,delta=(-1,0)))))
                memory.db.commit()
                backend=ReceiptBackend();backend.observe_turn=Mock(return_value=SimpleNamespace(image=b'current',kind='normal',changed=True,raw_change=.1,masked_activity=0,duration_ms=1000))
                script=Script([change(scene='gameplay',action={'keys':['w'],'hold_ms':80,'wait_ms':0})]);script.ocr=SimpleNamespace(read=Mock(return_value={'status':'ok','lines':[{'text':'HP15/15'}]}))
                agent=Controller(cfg,backend,script,lambda _:None,Path(t)/'run',memory=memory)
                before=state();after=state('C',249.7)
                agent.policy.observe=Mock(side_effect=[before,before,after]);agent.novelty=None;agent.improvement=None
                agent.policy.choose=Mock(side_effect=lambda d,*args,**kwargs:(d,{'source':'learned_policy','selected':{'trials':0},'context':{'ranking':[{'keys':['a'],'automatic_candidate':True,'q':.2,'trials':3}],'no_effect_moves':{}}}))
                agent.run();rows=[json.loads(line) for line in agent.journal.read_text().splitlines()]
                row=next(r for r in rows if r.get('event')=='result')
                self.assertEqual(backend.actions[0][0],['a']);self.assertEqual(row['policy_choice']['source'],'learned_outcome_prediction')
                self.assertTrue(row['information_exploration']['retained_experience']);self.assertTrue(row['learning_connection']['retained_candidate_has_experience'])
                self.assertEqual(row['forecast_evaluation']['expected']['player_delta'],[-1,0]);self.assertIn(row['learned_prediction_evaluation']['status'],('matched','mismatch'))
                self.assertEqual(row['policy_learning']['player_delta'],[-1,0])
            finally:memory.close()
