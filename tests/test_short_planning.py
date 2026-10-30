"""Mathematical/ownership/evidence contracts, not a simulated game or gameplay proof."""
import copy,gzip,io,json,tempfile,time,unittest,zipfile
from pathlib import Path
from types import SimpleNamespace
from autoplayer.nextgen.planning import *
from autoplayer.nextgen.planning_knowledge import EntityExperience,KnowledgeProvider,hit_probability,empirical
from autoplayer.nextgen.short_planning import ShortPlanning
from autoplayer.nextgen.planning_trace import export_traces
from autoplayer.nextgen.execution import ProcedureExecutor
from autoplayer.nextgen.store import ExperienceStore
from autoplayer.controls import ACTIONS
from test_execution import frame,candidates,item,POLICY


def world(position=(3,3),actors=()):
    return {'stamp':{'frame':'f','turn':0,'region':'r','revision':1,'control_revision':0},'goal_revision':0,'model_revision':0,'position':list(position),'cells':[[x,y] for x in range(7) for y in range(7)],'blocked':[],'diagonal':True,'actors':list(actors),'hp':known(30),'hp_max':30,'resource':known(70),'current_goal':{}}
def actor(identifier='a',position=(4,3),damage=1):return {'id':identifier,'position':list(position),'visible':True,'movement':known(1),'incoming_damage':known(damage) if damage is not None else interval(),'attack_distance':known(1),'movement_and_attack_same_turn':False,'remaining_hp':interval()}
def move_plan(path,label='position'):
    return plan_candidate(label,[action('move',{'destination':list(p)},known(1),costs={'items':0}) for p in path],{'kind':'path','path':path},{'kind':'tactical_position','cell':list(path[-1])})

class GenericPlanningContracts(unittest.TestCase):
    def test_common_action_contains_required_semantics_and_unknowns_are_not_zero(self):
        a=action('unfamiliar_operation');self.assertTrue({'preconditions','effects','costs','risk','turns','uncertainty','capability'}<=a.keys())
        self.assertIsNone(a['turns']['upper']);self.assertIsNone(a['costs']['items'])
    def test_same_engine_evaluates_noncombat_resource_information_and_equipment(self):
        w=world();w['current_goal']={'conditions':[{'feature':'metric.hp.0','operator':'ge','value':30}]};w['hp']=known(10)
        plans=[plan_candidate('recover',[action('consume',{},known(1),effects={'hp_gain':known(20)},costs={'items':1})],{'kind':'item'},{'kind':'recover'}),plan_candidate('inspect',[action('inspect',{},known(0),effects={'information':2},costs={'items':0})],{'kind':'review'},{'kind':'inspect'}),plan_candidate('equip',[action('equip',{},known(1),effects={'metrics':{'protection':known(2)}},costs={'items':0})],{'kind':'item'},{'kind':'equipment'})]
        packet=PlanningEngine().select(w,plans);self.assertEqual(len(packet['candidates']),3)
        recovery=next(r for r in packet['candidates'] if r['candidate']['label']=='recover');self.assertEqual(recovery['prediction']['terminal']['hp']['lower'],30);self.assertEqual(recovery['evaluation']['objective_support'][0]['support'],'supported_in_model')
    def test_unknown_incoming_damage_does_not_become_zero(self):
        w=world(actors=[actor(damage=None)]);p=plan_candidate('act',[action('rest',{},known(1),costs={'items':0})],{},{});out=PlanningEngine().predict(w,p)
        self.assertIsNone(out['terminal']['hp']['lower']);self.assertIsNone(out['terminal']['damage']['upper'])
    def test_enemy_move_and_attack_are_distinct_alternatives_when_profile_says_one_action(self):
        w=world(actors=[actor(position=(5,3))]);p=plan_candidate('observe',[action('rest',{},known(1),costs={'items':0})],{},{});out=PlanningEngine().predict(w,p)
        self.assertEqual(out['terminal']['damage']['upper'],0);self.assertEqual(out['terminal']['terminal_attackers_upper'],1)
        w['actors'][0]['movement_and_attack_same_turn']=True;self.assertEqual(PlanningEngine().predict(w,p)['terminal']['damage']['upper'],1)
    def test_two_enemies_cannot_attack_from_the_same_occupied_cell(self):
        self.assertEqual(simultaneous_attackers([{(0,0)},{(0,0)}]),1)
        self.assertEqual(simultaneous_attackers([{(0,0),(1,0)},{(0,0)}]),2)
    def test_corridor_route_beats_room_corner_by_future_contact_not_one_step_count(self):
        w=world(actors=[actor(str(i),p) for i,p in enumerate(((4,3),(4,2),(4,4)))])
        w['cells']+=[[x,3] for x in range(-3,0)]
        corner=move_plan([(2,2),(1,1),(0,0)],'corner');corridor=move_plan([(2,3),(1,3),(0,3),(-1,3),(-2,3)],'corridor')
        packet=PlanningEngine().select(w,[corner,corridor]);self.assertEqual(packet['selected']['candidate']['label'],'corridor')
        rows={r['candidate']['label']:r for r in packet['candidates']};self.assertLess(rows['corridor']['prediction']['terminal']['terminal_attackers_upper'],rows['corner']['prediction']['terminal']['terminal_attackers_upper'])
    def test_intermediate_attacks_and_low_hp_affect_route_evaluation(self):
        w=world(actors=[actor(position=(4,3),damage=8)]);w['hp']=known(6)
        approach=move_plan([(3,4),(4,4)],'exposed');away=move_plan([(2,3),(1,3)],'other')
        packet=PlanningEngine().select(w,[approach,away]);rows={r['candidate']['label']:r for r in packet['candidates']}
        self.assertGreater(rows['exposed']['prediction']['terminal']['covered_damage']['upper'],rows['other']['prediction']['terminal']['covered_damage']['upper']);self.assertEqual(packet['selected']['candidate']['label'],'other')
    def test_future_occupancy_conflict_has_explicit_failure_state(self):
        w=world(actors=[actor(position=(5,3))]);p=move_plan([(4,3),(5,3)])
        out=PlanningEngine().predict(w,p);self.assertTrue(out['failure_state']['possible_path_interruptions']);self.assertIn('future_occupancy_conflict',out['uncertainty']);self.assertIsNone(out['terminal']['hp']['lower'])
    def test_unknown_turn_tail_cannot_produce_a_finite_total_damage_guarantee(self):
        w=world(actors=[actor(damage=3)]);p=plan_candidate('unknown timing',[action('face',{},interval(0,None),costs={'items':0})],{},{});out=PlanningEngine().predict(w,p)
        self.assertIsNone(out['terminal']['damage']['upper']);self.assertEqual(out['terminal']['covered_damage']['upper'],3);self.assertTrue(out['failure_state']['unmodeled_tail'])
    def test_conditional_hit_and_kill_probabilities_use_learned_ranges(self):
        a=actor();a['remaining_hp']=interval(5,8,'INFERRED');w=world(actors=[a])
        strike=action('strike',{'target_id':'a'},known(1),effects={'damage_on_hit':interval(4,4),'hit_probability':interval(.5,.75)},costs={'items':0})
        p=plan_candidate('strike twice',[strike,strike],{}, {'kind':'handle_target','target_id':'a'});out=PlanningEngine().predict(w,p);prob=out['terminal']['conditional_defeat_probability']['a']
        self.assertAlmostEqual(prob['lower'],.25);self.assertAlmostEqual(prob['upper'],.5625);self.assertFalse(prob['guaranteed'])
    def test_unknown_enemy_hp_does_not_get_invented_for_kill_prediction(self):
        w=world(actors=[actor()]);s=action('strike',{'target_id':'a'},known(1),effects={'damage_on_hit':known(4),'hit_probability':interval(.5,.9)},costs={'items':0})
        out=PlanningEngine().predict(w,plan_candidate('attack',[s,s],{}, {'target_id':'a'}));self.assertIsNone(out['terminal']['conditional_defeat_probability']['a']['upper'])
    def test_candidate_budget_keeps_different_goal_families(self):
        plans=[plan_candidate(str(i),[action('rest',{'i':i},known(1))],{}, {'kind':'movement'}) for i in range(40)]
        plans.append(plan_candidate('resource',[action('consume',{},known(1))],{}, {'kind':'resource'}));p=PlanningEngine(max_candidates=4).select(world(),plans)
        self.assertTrue(any(r['candidate']['label']=='resource' for r in p['candidates']));self.assertEqual(len(p['candidates']),4)
    def test_source_and_goal_revisions_must_match_before_merge(self):
        lane=PlanningLane();w=world();lane.begin(w,[])
        try:
            with self.assertRaises(ValueError):lane.merge({**w['stamp'],'control_revision':2},0,0)
        finally:lane.close()
    def test_reachability_limits_are_visible_not_a_claim_of_safety(self):
        engine=PlanningEngine(max_reach=2);w=world(actors=[actor()]);out=engine.predict(w,move_plan([(2,3),(1,3)]));self.assertTrue(out['incomplete_envelope'])

class EntityLearningContracts(unittest.TestCase):
    def test_unique_actor_motion_is_tracked_and_ambiguous_crossing_is_not_confirmed(self):
        m=EntityExperience();s=frame(enemies=[(1,0)]);m.observe(s,0);identifier=next(iter(m.tracks))
        m.observe(frame('b',[(2,0)]),1);self.assertIn(identifier,m.tracks)
        m=EntityExperience();m.observe(frame(enemies=[(-1,0),(1,0)]),0);old=set(m.tracks)
        m.observe(frame('b',[(0,0)]),1);self.assertTrue(any(t['identity_status']=='ambiguous_or_new' for t in m.tracks.values()));self.assertTrue(any(k not in old for k in m.tracks))
    def test_retained_map_is_not_absence_and_does_not_increase_identity_support(self):
        m=EntityExperience();s=frame(enemies=[(1,0)]);s['minimap']['status']='retained';m.observe(s,0)
        t=next(iter(m.tracks.values()));self.assertEqual(t['measurement_source'],'RETAINED');self.assertEqual(t['matches'],0)
        m.observe({**s,'frame_sha256':'b'},1);self.assertEqual(next(iter(m.tracks.values()))['matches'],0)
    def test_icon_disappearance_is_not_death(self):
        m=EntityExperience();m.observe(frame(enemies=[(1,0)]),0);m.observe(frame('b'),1)
        self.assertTrue(m.tracks);self.assertFalse(any(t.get('defeated') for t in m.tracks.values()))
    def test_targeted_damage_and_explicit_defeat_learn_conditional_encounter_bounds(self):
        m=EntityExperience();s=frame(enemies=[(1,0)]);m.observe(s,0);identifier=next(iter(m.tracks));s['planning_world']={'actors':[{'id':identifier,'position':[6,5],'visible':True}]}
        for step,kind,damage in [(1,'damage_dealt',4),(2,'damage_dealt',5),(3,'defeated_enemy',None)]:
            after=frame(str(step),[(1,0)] if kind!='defeated_enemy' else []);after['metrics']['floor']={'values':[1]}
            message={'kind':kind,'damage':damage,'enemy':'observed_name','quote':kind,'source_frame_sha256':str(step)}
            m.learn(s,after,{'kind':'strike','target_id':identifier},[message],{'power':[1]},step,{'os_submission':'accepted'});s=after;s['planning_world']={'actors':[{'id':identifier,'position':[6,5],'visible':True}]}
        rows=list(m.models.values());self.assertTrue(any(r.get('hits_to_defeat')==[2] for r in rows));self.assertTrue(any(r.get('hp_lower')==[5] and r.get('hp_upper')==[9] for r in rows))
        m.observe(frame('spawn',[(1,0)]),4);self.assertTrue(any(k!=identifier for k,t in m.tracks.items() if not t.get('defeated')))
    def test_unattributed_multi_actor_incoming_message_stays_unassigned(self):
        m=EntityExperience();s=frame(enemies=[(-1,0),(1,0)]);m.observe(s,0);s['planning_world']={'actors':[{'id':t['id'],'position':t['position'],'visible':True} for t in m.tracks.values()]}
        m.learn(s,frame('b',[(-1,0),(1,0)]),{'kind':'rest'},[{'kind':'damage_received','enemy':'unknown','damage':3}],{},1,{'os_submission':'accepted'})
        self.assertFalse(m.models);self.assertEqual(len(m.unassigned),1)
    def test_experience_reduces_unknown_prediction_and_survives_reload_without_identity_reuse(self):
        e=ProcedureExecutor(None,ACTIONS,POLICY);provider=KnowledgeProvider(e,{'grid_topology':8});s=frame(enemies=[(1,0)])
        before=provider.snapshot(s,0,{},0,0);actor_id=before['actors'][0]['id'];self.assertIsNone(before['actors'][0]['damage_on_hit']['upper'])
        s['planning_world']={'actors':before['actors']};after=frame('observed-hit',[(1,0)])
        provider.memory.learn(s,after,{'kind':'strike','target_id':actor_id},[{'kind':'damage_dealt','enemy':'observed_name','damage':4}],before['state_stats'],1,{'os_submission':'accepted'})
        learned=provider.snapshot(after,1,{},0,0);self.assertEqual(learned['actors'][0]['damage_on_hit']['upper'],4)
        saved=provider.memory.dump();restored=EntityExperience(json.loads(json.dumps(saved)))
        self.assertEqual(restored.models,provider.memory.models);self.assertEqual(restored.revision,provider.memory.revision);self.assertFalse(restored.tracks)
        self.assertIsNone(before['actors'][0]['damage_on_hit']['upper'])  # no retrospective score rewriting

    def test_negative_observed_effect_is_not_clamped_to_a_positive_gain(self):
        value=empirical([-3,-1],zero_lower=True);self.assertEqual((value['lower'],value['upper']),(-3,-1))
    def test_empirical_hit_probability_is_not_certain_after_one_hit(self):
        p=hit_probability([1]);self.assertGreater(p['lower'],0);self.assertLess(p['lower'],1);self.assertEqual(p['upper'],1);self.assertFalse(p['guaranteed'])

class PlanningLifecycleContracts(unittest.TestCase):
    def test_prequential_audit_scores_before_learning_and_replans_on_error(self):
        e=ProcedureExecutor(None,ACTIONS,POLICY);e.review_needed=False;e.last_review=0
        with tempfile.TemporaryDirectory() as d:
            planner=ShortPlanning(None,e,{'grid_topology':8},Path(d)/'timeline.jsonl',synchronous=True)
            s=frame();target={'id':'front','kind':'frontier','path':[[5,5],[6,5]],'cell':[6,5],'distance':1,'confidence':.75,'stamp':s['spatial']['stamp']};s['spatial']['targets']=[target]
            planner.begin(s,candidates(),0);self.assertIsNotNone(planner.merge(s,0));r=planner.dispatch(s,candidates(),0)
            after=frame('after');after['spatial']['pose_source']='OBSERVED';after['spatial']['stamp']['revision']=2
            audit=planner.feedback(s,after,r,{'os_submission':'accepted'},1,[]);self.assertIn('position',audit['violations']);self.assertTrue(audit['replan']);self.assertTrue(planner.research_tasks)
            record=json.loads((Path(d)/'timeline.jsonl').read_text(encoding='utf-8').splitlines()[0]);self.assertTrue(record['candidates']);self.assertEqual(record['input']['short_plan_id'],record['selected']);self.assertIn('actual_and_error',record)
            planner.close()
    def test_unknown_features_are_not_counted_as_prediction_successes(self):
        e=ProcedureExecutor(None,ACTIONS,POLICY);planner=ShortPlanning(None,e,{'grid_topology':8},synchronous=True);s=frame();planner.begin(s,candidates(),0);planner.merge(s,0);r=planner.dispatch(s,candidates(),0)
        after=frame('after');after['metrics']={};after['spatial']['pose_source']='RETAINED';audit=planner.feedback(s,after,r,{'os_submission':'accepted'},1)
        self.assertIsNone(audit['accuracy']);self.assertTrue(audit['unscored_unknown_or_unmeasured']);planner.close()
    def test_changed_frame_cannot_execute_the_merged_plan(self):
        e=ProcedureExecutor(None,ACTIONS,POLICY);p=ShortPlanning(None,e,{'grid_topology':8},synchronous=True);s=frame();p.begin(s,candidates(),0);p.merge(s,0)
        with self.assertRaisesRegex(ValueError,'Stale short plan'):p.dispatch(frame('new-frame'),candidates(),0)
        p.close()
    def test_missing_input_binding_is_not_an_executable_plan(self):
        e=ProcedureExecutor(None,ACTIONS,POLICY);e.roles['rest']=None;provider=KnowledgeProvider(e,{'grid_topology':8});s=frame(hp=(2,15));w=provider.snapshot(s,0,{},0,0)
        plans=provider.propose(s,w,0);self.assertFalse(any(p['execution']['kind']=='rest' for p in plans));self.assertTrue(w['unavailable_capabilities'])
    def test_censored_input_does_not_train_action_model(self):
        e=ProcedureExecutor(None,ACTIONS,POLICY);p=ShortPlanning(None,e,{'grid_topology':8},synchronous=True);s=frame();p.begin(s,candidates(),0);p.merge(s,0);r=p.dispatch(s,candidates(),0)
        revision=p.memory.revision;a=p.feedback(s,frame('b'),r,{'os_submission':'censored'},1);self.assertEqual(a['status'],'censored');self.assertEqual(p.memory.revision,revision);p.close()
    def test_diagnostic_zip_contains_bounded_compressed_plan_chain(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'planning').mkdir();record={'candidates':[],'input':{'keys':['x']},'actual_and_error':{'replan':True}}
            (root/'planning/a.jsonl').write_text(json.dumps(record)+'\n',encoding='utf-8');out=io.BytesIO()
            with zipfile.ZipFile(out,'w') as z:remaining,report=export_traces(root,z,1024*1024)
            self.assertEqual(report[0]['records'],1)
            with zipfile.ZipFile(out) as z:self.assertEqual(json.loads(gzip.decompress(z.read('planning/a.jsonl.gz'))),record)
    def test_outside_hypothesis_region_does_not_start_inventory_audit(self):
        e=ProcedureExecutor(None,ACTIONS,POLICY);provider=KnowledgeProvider(e,{'grid_topology':4});s=frame();s['spatial']['provider']='hypothesis';s['metrics']={};s['floor_confirmation']={'confirmed':None}
        w=provider.snapshot(s,0,{},0,0);plans=provider.propose(s,w,0);self.assertFalse(any(p['execution']['kind'] in ('review','item') for p in plans))
    def test_control_region_change_cannot_transfer_individual_identity_or_episode(self):
        m=EntityExperience();s=frame(enemies=[(1,0)]);m.observe(s,0);identifier=next(iter(m.tracks));s['planning_world']={'actors':[{'id':identifier,'position':[6,5],'visible':True}]}
        a=frame('other',[(1,0)]);a['spatial']['stamp']['region']='other'
        m.learn(s,a,{'kind':'strike','target_id':identifier},[{'kind':'damage_dealt','enemy':'name','damage':4}],{},1,{'os_submission':'accepted'})
        self.assertNotIn(identifier,m.tracks);self.assertFalse(m.models)
    def test_existing_audit_can_switch_means_without_resetting_the_parent_goal(self):
        e=ProcedureExecutor(None,ACTIONS,POLICY);e.pages={'p':[item()]};s=frame(hp=(2,15),kind='inventory');s['execution_ui']['inventory']={'status':'observed','rows':[item()],'page_signature':'p'}
        e.begin('review',s,0,'parent objective');e.pages={'p':[item()]};provider=KnowledgeProvider(e,{'grid_topology':8});w=provider.snapshot(s,1,{'id':'parent','conditions':[]},0,2)
        plans=provider.propose(s,w,1);self.assertTrue(any(p['execution'].get('purpose')=='heal' for p in plans));self.assertEqual(w['current_goal']['id'],'parent')
    def test_same_engine_is_usable_with_an_unrelated_abstract_resource(self):
        w=world();w['metrics']={'charge':known(2)};w['current_goal']={'conditions':[{'feature':'metric.charge.0','operator':'ge','value':5}]}
        p=plan_candidate('recharge',[action('transform_resource',{},known(2),effects={'metrics':{'charge':known(3)}},costs={'items':0})],{'kind':'extension_handler'},{'kind':'charge'})
        out=PlanningEngine().select(w,[p]);self.assertEqual(out['selected']['evaluation']['objective_support'][0]['support'],'supported_in_model')
    def test_projection_does_not_use_unconfirmed_llm_hypotheses_as_numeric_facts(self):
        e=ProcedureExecutor(None,ACTIONS,POLICY);provider=KnowledgeProvider(e,{'grid_topology':8});s=frame(enemies=[(1,0)]);s['hypotheses']=[{'enemy_hp':1,'guaranteed':True}]
        w=provider.snapshot(s,0,{},0,0);self.assertIsNone(w['actors'][0]['remaining_hp']['upper'])
    def test_active_controller_routes_a_typed_observation_through_planner_before_input(self):
        # Protocol injection only: not a fabricated game or an image-recognition
        # accuracy assertion. The single capture is a previously submitted PNG.
        from unittest.mock import Mock,patch
        from autoplayer.core import Settings,LocalLLM
        from autoplayer.nextgen.controller import NextGenController
        from autoplayer.nextgen.research import ResearchLayer
        from autoplayer.observation import TurnObservation
        from test_nextgen import plan
        import hashlib
        png=(Path(__file__).parent/'fixtures/no-menu-1793151109362765000.png').read_bytes();sha=hashlib.sha256(png).hexdigest()
        cfg=Settings(window_title='torneko',allowed_keys=list(ACTIONS),key_bindings=ACTIONS,max_steps=1,max_wait_ms=0)
        llm=LocalLLM(cfg);llm.ocr=Mock();llm.ocr.read.return_value={'status':'ok','frame_sha256':sha,'lines':[{'text':'1階'},{'text':'HP15/15'}]};llm.ocr.read_regions=llm.ocr.read
        backend=Mock();backend.capture.return_value=png;backend.act.return_value={'os_submission':'accepted'};backend.observe_turn.return_value=TurnObservation(png,'normal',False,0,0,0)
        with tempfile.TemporaryDirectory() as d,patch.object(ResearchLayer,'tick',return_value=(plan(),None)):
            rows=[];root=Path(d);runner=NextGenController(cfg,backend,llm,rows.append,root/'runs'/'test.jsonl')
            def typed_observation(image,full=True,step=0,base_state=None):
                s=base_state if base_state is not None else runner.builder.build(image,full)
                s.update(interface_hypothesis=False,ui_visual={**s['ui_visual'],'active':False},floor_confirmation={'confirmed':1,'status':'confirmed'},map_domain='contract',metrics={'hp':{'values':[15,15]}})
                s['minimap']={'status':'observed','player_cell':[5,5],'accumulated_cells':[[5,5],[6,5],[7,5]],'grid_size':[54,32],'enemies':[[1,0]],'items':[]};return s
            runner.sense=typed_observation;runner.run()
            self.assertEqual(backend.act.call_count,1);self.assertEqual(set(backend.act.call_args.args[0]),{'num5','d'})
            self.assertTrue(any(r.get('event')=='short_plan_result' for r in rows));trace=list((root/'planning').glob('*.jsonl'))
            self.assertEqual(len(trace),1);record=json.loads(trace[0].read_text(encoding='utf-8').splitlines()[0]);self.assertTrue(record['candidates']);self.assertTrue(record['actual_and_error'])
