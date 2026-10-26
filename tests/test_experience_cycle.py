"""Numeric observation pairs and prospective-validation contracts; no game."""
import copy,json,tempfile,unittest
from pathlib import Path
from autoplayer.core import Settings
from autoplayer.memory import MemoryStore
from autoplayer.action_effects import ActionEffects
from autoplayer.experience_cycle import ExperienceCycle,maturity
from test_policy import state,receipt_row

class CycleTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.memory=MemoryStore(Path(tmp.name)/'m.sqlite',Settings());self.addCleanup(self.memory.close)
        self.effects=ActionEffects(self.memory,Settings());self.cycle=ExperienceCycle(self.effects)
        self.a=state();self.b=state('B',261.7);self.a['frame_sha256']='before';self.b['frame_sha256']='after';self.step=0
    def run_pair(self,after=None,natural=None,keys=None):
        self.step+=1;keys=keys or ['d'];forecast=self.cycle.predict(self.a,keys,self.step)
        row=receipt_row(observed_keys=keys,action_prediction=forecast,step=self.step,operation_evaluation={'new_location':True})
        after=after or self.b
        row['action_effect_learning']=self.effects.record(row,self.a,after,natural or {})
        result=self.cycle.record(row,self.a,after,natural or {})
        return forecast,result
    def train(self,n=8):
        for _ in range(n):self.run_pair()
    def test_first_observations_are_not_counted_as_successful_predictions(self):
        for _ in range(3):
            forecast,result=self.run_pair();self.assertNotIn('player_delta',forecast['expected']);self.assertFalse(result['checks'])
        forecast=self.cycle.predict(self.a,['d'])
        self.assertEqual(forecast['expected']['player_delta']['status'],'candidate')
    def test_supported_requires_future_checks_and_persists_across_reload(self):
        self.train();p=self.cycle.predict(self.a,['d'])
        self.assertEqual(p['expected']['player_delta']['status'],'supported')
        resumed=ExperienceCycle(self.effects);self.assertEqual(resumed.predict(self.a,['d'])['expected']['player_delta']['value'],[1,0])
        self.assertGreater(resumed.context(self.a)['metrics']['prospective_checks'],0)
    def test_counterexamples_suspend_then_new_consistent_data_require_revalidation(self):
        self.train();f,a=self.run_pair(self.a);self.assertEqual(a['status'],'mismatch');self.assertTrue(a['needs_review'])
        self.run_pair(self.a);p=self.cycle.predict(self.a,['d']);self.assertNotIn('player_delta',p['expected'])
        self.assertTrue(self.cycle.questions)
        # HP-only matches must not resolve a movement contradiction.
        self.run_pair(self.a);self.assertTrue(self.cycle.questions)
        for _ in range(5):self.run_pair(self.a)
        p=self.cycle.predict(self.a,['d']);self.assertEqual(p['expected']['player_delta']['status'],'candidate')
        for _ in range(5):self.run_pair(self.a)
        self.assertEqual(self.cycle.predict(self.a,['d'])['expected']['player_delta']['status'],'supported')
        field=self.cycle.load(p['local_key'])['fields']['player_delta']
        self.assertEqual(field['counterexamples'],2);self.assertIn('previous_validation',field)
    def test_missing_map_and_natural_metric_change_are_censored_not_failures(self):
        self.train();bad=copy.deepcopy(self.b);bad['eligible']=False;bad['minimap']={'status':'unavailable'}
        p,r=self.run_pair(bad,{'metric_changes':{'hp':{'before':[14,15],'after':[15,15]}}})
        self.assertNotIn('player_delta',r['observed']);self.assertNotIn('hp_change',r['observed']);self.assertFalse(r['checks'])
    def test_excluded_input_and_replaced_keys_do_not_train(self):
        p=self.cycle.predict(self.a,['d']);r=receipt_row('a',action_prediction=p)
        self.assertEqual(self.cycle.record(r,self.a,self.b)['status'],'excluded')
        r=receipt_row('d',action_prediction=p,dry_run=True)
        self.assertEqual(self.cycle.record(r,self.a,self.b)['status'],'excluded')
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM experience_rules_v1').fetchone()[0],0)
    def test_planned_route_is_distinct_from_unlearned_rule_and_triggers_bounded_review(self):
        self.a['map_route_hint']={'key':'d','keys':['d'],'mode':'observed_grid_route'}
        p,r=self.run_pair(self.a);self.assertEqual(p['status'],'unestimated');self.assertFalse(r['planned_route_matched']);self.assertFalse(r['needs_review'])
        p,r=self.run_pair(self.a);self.assertTrue(r['needs_review'])
        p,r=self.run_pair(self.a);self.assertFalse(r['needs_review'])
        resumed=ExperienceCycle(self.effects);self.assertTrue(resumed.questions)
    def test_local_context_does_not_reuse_a_different_floor_or_equipment(self):
        self.train();p=self.cycle.predict(self.a,['d'])
        other=copy.deepcopy(self.a);other['metrics']['floor']['values']=[2]
        self.assertNotEqual(self.cycle.predict(other,['d'])['local_key'],p['local_key'])
        self.a['minimap']['enemies']=[[1,0]];self.a['metrics']['sword']={'values':[4]}
        a=self.cycle.keys(self.a,['num1']);self.a['metrics']['sword']['values']=[5]
        self.assertNotEqual(a,self.cycle.keys(self.a,['num1']))
    def test_visual_and_item_hypotheses_are_scoped_not_promoted_to_names(self):
        from types import SimpleNamespace
        world={'frame_sha256':'before','objects':[{'legend':'enemy_indicator','appearance_memory':{'class_id':'anonymous_1'}}]}
        inv=SimpleNamespace(rows=[{'id':'item1','display_name':'青い草'}],selected='item1',scope='episode1')
        self.cycle.prepare(self.a,world,inv)
        scope=self.a['_experience_scope'];self.assertFalse(scope['visual_identity_verified']);self.assertEqual(scope['selected_item'],['instance','item1'])
        self.cycle.prepare(self.a,world,inv,source_frame='other');self.assertFalse(self.a['_experience_scope']['visual_candidates'])
    def test_diagonal_observations_are_evaluated_as_the_dispatched_chord(self):
        self.a['map_route_hint']={'key':'s+d','keys':['s','d'],'mode':'observed_grid_route'}
        after=copy.deepcopy(self.b);after['minimap']['player_cell'][1]+=1
        p,r=self.run_pair(after,keys=['s','d'])
        self.assertEqual(p['planned_delta'],[1,1]);self.assertTrue(r['planned_route_matched'])

    def test_damage_and_item_effects_require_matching_observational_conditions(self):
        a=copy.deepcopy(self.a);a['_experience_scope']={'visual_candidates':['anonymous'],'selected_item':['instance','item1']}
        row={'action_effect_learning':{'effects':{}},'battle_observations':[{'kind':'damage_dealt','damage':3,'new_message':True,'source_frame_sha256':'after'}],
            'world_learning':{'item_effect':{'confounds':[],'metric_changes':{'hp':{'before':[10,15],'after':[12,15]}}}}}
        values,_=self.cycle.observed(row,a,self.b,{})
        self.assertEqual(values['damage_dealt'],3);self.assertEqual(values['item_hp'],2)
        row['world_learning']['item_effect']['confounds']=['level_change'];row['battle_observations'][0]['new_message']=False
        values,_=self.cycle.observed(row,a,self.b,{})
        self.assertNotIn('damage_dealt',values);self.assertNotIn('item_hp',values)

    def test_input_timing_and_changed_source_frame_do_not_share_validation(self):
        first=self.cycle.predict(self.a,['d'],timing={'hold_ms':40,'wait_ms':300})
        second=self.cycle.predict(self.a,['d'],timing={'hold_ms':80,'wait_ms':300})
        self.assertNotEqual(first['base_key'],second['base_key'])
        row=receipt_row('d',action_prediction=second,input_source_frame_sha256='other')
        self.assertEqual(self.cycle.record(row,self.a,self.b)['status'],'excluded')
    def test_retained_floor_is_not_current_spatial_evidence(self):
        self.a['metrics']['floor']['retained']=True
        p,r=self.run_pair()
        self.assertNotIn('player_delta',r['observed']);self.assertNotIn('hp_change',r['observed'])

    def test_full_hp_item_conditions_and_maximum_hp_effect_are_separate(self):
        self.a['_experience_scope']={'selected_item':['ocr_identified_name','薬草']};self.a['_operational_interface']='item_actions'
        full=self.cycle.keys(self.a,['num1']);wounded=copy.deepcopy(self.a);wounded['metrics']['hp']['values']=[10,15]
        self.assertNotEqual(full,self.cycle.keys(wounded,['num1']))
        r={'world_learning':{'item_effect':{'confounds':[],'metric_changes':{'hp':{'before':[15,15],'after':[16,16]}}}}}
        values,_=self.cycle.observed(r,self.a,self.b,{})
        self.assertEqual(values['item_max_hp'],1);self.assertNotIn('item_hp',values)

    def test_questions_for_other_floor_are_retained_but_not_active_tasks(self):
        self.a['map_route_hint']={'key':'d','keys':['d'],'mode':'observed_grid_route'}
        self.run_pair(self.a);self.assertTrue(self.cycle.context(self.a)['open_questions'])
        other=copy.deepcopy(self.a);other['metrics']['floor']['values']=[2]
        self.assertFalse(self.cycle.context(other)['open_questions']);self.assertTrue(self.cycle.questions)
