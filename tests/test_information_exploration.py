"""Coverage/persistence and dispatch contracts, not a simulated game."""
import json,tempfile,unittest
from pathlib import Path
from autoplayer.core import Settings,Controller,parse_decision
from autoplayer.information_exploration import InformationExploration
from autoplayer.memory import MemoryStore
from test_agent import Backend,Script,change

class InformationTests(unittest.TestCase):
    def setUp(self):
        self.cfg=Settings(window_title='Other',allowed_keys=['a','b'],goal='箱を持ち帰る').validate()
        self.native={'status':'ok','lines':[{'text':'操作画面'}]};self.state={'metrics':{},'minimap':{},'eligible':False}
        self.decision=parse_decision(json.dumps(change(action={'keys':['b'],'hold_ms':80,'wait_ms':0})),self.cfg)
    def record(self,model,context,keys,after=None,executed=True,dry=False,image=b'captured'):
        return model.record(context,{'executed':executed,'dry_run':dry,'observed_keys':keys,'neutral_before':{'raw_change':0}},self.state,self.state,self.native,after or self.native,image)
    def test_collects_different_inputs_then_returns_to_original_goal_proposal(self):
        model=InformationExploration(self.cfg);selected=[]
        for _ in range(4):
            d,c=model.choose(self.decision,self.native,self.state);selected.append(d.keys);self.record(model,c,d.keys)
        self.assertEqual(selected,[['a'],['b'],['a'],['b']])
        d,c=model.choose(self.decision,self.native,self.state)
        self.assertTrue(c['initial_coverage_ready']);self.assertIs(d,self.decision)
        self.assertEqual(self.cfg.goal,'箱を持ち帰る')
    def test_information_coverage_does_not_override_a_menu_or_owned_step(self):
        from dataclasses import replace
        model=InformationExploration(self.cfg)
        menu={'status':'ok','lines':[{'text':t} for t in ('道具','マップ','足元','その他')]}
        decision,context=model.choose(self.decision,menu,self.state)
        self.assertIs(decision,self.decision);self.assertEqual(context['deferred_reason'],'current_menu_or_owned_operation')
        owned=replace(self.decision,execution_stage='requested_item_action')
        self.assertIs(model.choose(owned,self.native,self.state)[0],owned)

    def test_scene_and_action_labels_do_not_define_observed_situation(self):
        from dataclasses import replace
        model=InformationExploration(self.cfg)
        a=model.situation(self.decision,self.native,self.state)
        b=model.situation(replace(self.decision,scene='gameplay',subgoal='攻撃で勝利',reason='必ず回復'),self.native,self.state)
        self.assertEqual(a,b)
    def test_different_context_is_not_marked_covered_by_old_actions(self):
        model=InformationExploration(self.cfg);c=model.context(self.decision,self.native,self.state)
        self.record(model,c,['a']);other={'status':'ok','lines':[{'text':'別の操作画面'}]}
        self.assertEqual(model.context(self.decision,other,self.state)['actions'][0]['observed'],0)
    def test_disagreeing_results_need_more_observations_without_claiming_cause(self):
        model=InformationExploration(self.cfg);c=model.context(self.decision,self.native,self.state)
        self.record(model,c,['a'],{'lines':[{'text':'変化1'}]});r=self.record(model,c,['a'],{'lines':[{'text':'変化2'}]})
        c=model.context(self.decision,self.native,self.state)
        self.assertEqual(c['actions'][0]['required'],4);self.assertFalse(c['initial_coverage_ready'])
        self.assertEqual(r['attribution'],'temporal_association_not_proven_cause');self.assertFalse(r['goal_success_verified'])
    def test_unsent_dry_and_missing_result_do_not_satisfy_coverage(self):
        model=InformationExploration(self.cfg);c=model.context(self.decision,self.native,self.state)
        self.record(model,c,['a'],executed=False);self.record(model,c,['a'],dry=True)
        r=self.record(model,c,['a'],image=b'');self.assertEqual(r['observed'],0);self.assertEqual(r['sent'],1)
    def test_observations_survive_restart_and_remain_profile_scoped(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'memory.sqlite';memory=MemoryStore(path,self.cfg)
            model=InformationExploration(self.cfg,memory);c=model.context(self.decision,self.native,self.state);self.record(model,c,['a']);memory.close()
            memory=MemoryStore(path,self.cfg)
            try:self.assertEqual(InformationExploration(self.cfg,memory).context(self.decision,self.native,self.state)['actions'][0]['observed'],1)
            finally:memory.close()
            other=Settings(window_title='Different',allowed_keys=['a','b'],goal=self.cfg.goal).validate();memory=MemoryStore(path,other)
            try:self.assertEqual(InformationExploration(other,memory).context(self.decision,self.native,self.state)['actions'][0]['observed'],0)
            finally:memory.close()
    def test_only_remaining_coverage_candidate_does_not_override_backoff(self):
        model=InformationExploration(self.cfg);situation=model.situation(self.decision,self.native,self.state)
        model.stats(situation,['a']).update(observed=3,sent=3,outcomes=['one','two'])
        model.stats(situation,['b']).update(observed=2,sent=2,outcomes=['one'])
        selected,context=model.choose(self.decision,self.native,self.state,[['a']])
        self.assertEqual(selected.keys,['b']);self.assertTrue(context['temporarily_deferred'])
    def test_all_temporarily_blocked_candidates_still_produce_input(self):
        d,c=InformationExploration(self.cfg).choose(self.decision,self.native,self.state,[['a'],['b']]);self.assertTrue(d.keys)
    def test_controller_records_actual_trials_without_changing_overall_goal(self):
        class Receipt(Backend):
            def act(self,keys,*args):super().act(keys,*args);return {'events':[{'message':'WM_KEYDOWN'}]}
        cfg=Settings(window_title='Other',allowed_keys=['a','b'],goal='箱を持ち帰る',max_steps=5).validate()
        script=Script([change(action={'keys':['b'],'hold_ms':80,'wait_ms':0})]*5);backend=Receipt()
        with tempfile.TemporaryDirectory() as t:
            memory=MemoryStore(Path(t)/'memory.sqlite',cfg)
            try:
                c=Controller(cfg,backend,script,lambda _:None,Path(t)/'run',memory=memory);c.run()
                rows=[json.loads(l) for l in c.journal.read_text().splitlines() if json.loads(l)['event']=='result']
                self.assertEqual([a[0] for a in backend.actions[:4]],[['a'],['b'],['a'],['b']])
                self.assertTrue(rows[-1]['information_exploration']['initial_coverage_ready'])
                self.assertTrue(all(r['input_coverage']['status']=='response_observed' for r in rows))
                self.assertEqual(cfg.goal,'箱を持ち帰る')
            finally:memory.close()
