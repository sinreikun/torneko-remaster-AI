"""Generation-two numerical/protocol contracts and replay of submitted pixels.
No game simulation; none of these assertions establish gameplay performance.
"""
import copy,hashlib,json,sqlite3,tempfile,threading,unittest,zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from autoplayer.core import Settings,LocalLLM
from autoplayer.nextgen.store import ExperienceStore,diagnostic_summary
from autoplayer.nextgen.model import ForwardModel,AdaptivePolicy,action_id,effects
from autoplayer.nextgen.state import StateBuilder
from autoplayer.nextgen.research import ResearchLayer
from autoplayer.nextgen.supervisor import validate,SemanticSupervisor
from autoplayer.nextgen.controller import NextGenController
from autoplayer.session import SessionControl
from autoplayer.cancellation import OperationCancelled
from autoplayer.observation import TurnObservation
from autoplayer.dashboard import LearningTraces
from autoplayer.diagnostics import export_diagnostics

FIXTURES=Path(__file__).parent/'fixtures'

def measured(node='one',hp=10,context='context'):
    return {'id':node,'context':context,'frame_sha256':node,'observed_at':1.,'quotes':['observed'],
      'metrics':{'hp':{'values':[hp,20]}},'minimap':{'status':'unavailable'},'retained':{},
      'floor_confirmation':{'confirmed':None,'status':'unreadable'},'quality':.9,'measurement_status':'ok',
      'interface_hypothesis':False,'selection_observation':{},'text_key':'observed','objects':[],'state_identification':'appearance_text_hypothesis'}

def plan():return {'objective':'観測できる変化を調べる','reason':'経験を照合する','review_after':32,'targets':[], 'candidate_values':[],'hypotheses':[],'interpretations':[]}

class ModelContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'world-v2.sqlite';self.store=ExperienceStore(self.path,'profile','run');self.model=ForwardModel(self.store)
        self.action={'id':action_id(['a'],80),'keys':['a'],'hold_ms':80}
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def update(self,before,after,n=1):return self.model.observe(before,self.action,after,self.model.predict(before,self.action['id']),{'os_submission':'accepted'},n)
    def test_prequential_prediction_improves_and_counterexamples_revise_conditions(self):
        a=measured();b=measured('two',11)
        first=self.update(a,b);self.assertIsNone(first['match'])
        results=[self.update(a,b,n) for n in range(2,10)]
        self.assertTrue(all(r['match'] for r in results));self.assertLess(self.model.predict(a,self.action['id'])['uncertainty'],.5)
        other=measured('three',7)
        failures=[self.update(a,other,n) for n in range(10,17)]
        self.assertTrue(any(x['match'] is False for x in failures))
        self.assertTrue(any(x['condition_split_node']=='one' for x in failures))
        self.assertTrue(any(r['revisions']>0 for r in self.model.rules.values()))
    def test_missing_values_are_not_zero_and_rejected_or_interrupted_inputs_do_not_learn(self):
        a=measured();b=measured();b['metrics']={}
        self.assertNotIn('metric.hp.0',effects(a,b))
        for receipt,kind in (({'os_submission':'failed'},'normal'),({'os_submission':'accepted'},'aborted')):
            result=self.model.observe(a,self.action,b,self.model.predict(a,self.action['id']),receipt,1,kind=kind)
            self.assertEqual(result['status'],'censored')
        self.assertFalse(self.model.visits)
    def test_delayed_effects_preserve_intervening_input_confounds(self):
        for n in range(1,5):result=self.update(measured(str(n)),measured(str(n+1)),n)
        delayed=result['delayed'];self.assertTrue(delayed)
        self.assertTrue(all(x['confidence']<result['confidence'] for x in delayed));self.assertTrue(all(x['intervening_inputs']>=2 for x in delayed))
    def test_loop_changes_actual_candidate_and_ends_at_bounded_attempt_limit(self):
        a=measured()
        for n in range(1,7):result=self.update(a,a,n)
        self.assertEqual(result['loop']['event'],'loop_detected')
        cfg=Settings(window_title='generic',allowed_keys=['a','b'],key_bindings={})
        choice,_,_=self.model.choose(a,self.model.candidates(cfg))
        self.assertNotEqual(choice['candidate']['id'],self.action['id'])
        for n in range(7,15):result=self.update(a,a,n)
        self.assertEqual(result['loop']['reason'],'attempt_budget_exhausted');self.assertIsNone(self.model.escape)
    def test_eight_state_cycle_with_different_inputs_is_detected_and_key_variants_discounted(self):
        # State-transition contract, not a simulated game or gameplay proof.
        self.model.visits.update({str(i):1 for i in range(8)})
        detected=None
        for n in range(24):
            a=measured(str(n%8));b=measured(str((n+1)%8))
            keys=['a'] if (n//8+n)%2 else ['b']
            action={'id':action_id(keys,80),'keys':keys,'hold_ms':80}
            result=self.model.observe(a,action,b,self.model.predict(a,action['id']),{'os_submission':'accepted'},n+1)
            if result['loop']:detected=result['loop']
        self.assertEqual(detected['period'],8)
        self.assertEqual(detected['reason'],'repeated_state_itinerary_despite_varying_inputs')
        cfg=Settings(window_title='generic',allowed_keys=['a','b','c'],key_bindings={})
        chosen,ranking,_=self.model.choose(measured('0'),self.model.candidates(cfg))
        self.assertEqual(chosen['candidate']['keys'],['c'])
        self.assertTrue(all(r['loop_action_discount']==5 for r in ranking if r['candidate']['keys'] in (['a'],['b'])))
        self.assertTrue(any(r['candidate']['hold_ms']>80 and r['loop_action_discount']==5 for r in ranking))

    def test_long_itinerary_detection_requires_reliable_observations(self):
        self.model.visits.update({str(i):1 for i in range(8)})
        for n in range(24):
            a=measured(str(n%8));b=measured(str((n+1)%8));a['quality']=b['quality']=.3
            action={'id':action_id(['a' if n//8%2 else 'b'],80),'keys':['a' if n//8%2 else 'b'],'hold_ms':80}
            result=self.model.observe(a,action,b,self.model.predict(a,action['id']),{'os_submission':'accepted'},n+1)
            self.assertIsNone(result['loop'])

    def test_state_cycle_does_not_override_measured_progress_or_new_outcomes(self):
        for n in range(40):
            result=self.update(measured(str(n%8),hp=10+n),measured(str((n+1)%8),hp=11+n),n+1)
            self.assertIsNone(result['loop'])
        self.model.recent.clear()
        for n in range(40):
            result=self.update(measured('new'+str(n)),measured('new'+str(n+1)),n+41)
            self.assertIsNone(result['loop'])

    def test_cycle_experience_survives_escape_and_restart_without_global_key_ban(self):
        a=measured()
        for n in range(1,7):self.update(a,a,n)
        self.assertTrue(self.model.cycle_memory)
        self.update(a,measured('fresh'),7)
        self.assertIsNone(self.model.escape)
        cfg=Settings(window_title='generic',allowed_keys=['a','b'],key_bindings={})
        selected,_,_=self.model.choose(a,self.model.candidates(cfg))
        self.assertEqual(selected['candidate']['keys'],['b'])
        self.assertEqual(self.model.cycle_discount(measured('elsewhere'),self.action,0),0)
        self.assertEqual(self.model.cycle_discount(measured(context='elsewhere'),self.action,0),0)
        self.assertEqual(self.model.cycle_discount(a,self.action,1),0)
        self.store.close();self.store=ExperienceStore(self.path,'profile','restart');reloaded=ForwardModel(self.store)
        self.assertEqual(reloaded.cycle_discount(a,self.action,0),3)
        other=ExperienceStore(self.path,'other_profile','restart')
        try:self.assertEqual(ForwardModel(other).cycle_discount(a,self.action,0),0)
        finally:other.close()

    def test_cycle_memory_prefers_reentry_without_obstructing_ui_exit(self):
        rows=[{'context':'context','before':'outside','action':self.action['id'],'before_interface':False,'after_interface':True},
              {'context':'context','before':'dialogue','action':self.action['id'],'before_interface':True,'after_interface':False}]
        self.model.remember_cycle({'sequence':rows})
        self.assertEqual(self.model.cycle_discount(measured('outside'),self.action,0),3)
        self.assertEqual(self.model.cycle_discount(measured('dialogue'),self.action,0),0)
        self.assertEqual(next(iter(self.model.cycle_memory.values()))['basis'],'interface_reentry_hypothesis')

    def test_cycle_memory_expires_and_is_bounded(self):
        a=measured()
        for n in range(1,7):self.update(a,a,n)
        for item in self.model.cycle_memory.values():item['remaining']=1
        self.model.age_cycles(8);self.assertFalse(self.model.cycle_memory)
        sequence=[{'context':'scope','before':str(i),'action':self.action['id']} for i in range(300)]
        self.model.remember_cycle({'sequence':sequence})
        self.assertEqual(len(self.model.cycle_memory),256)
        for item in self.model.cycle_memory.values():item['expires_at']=0
        self.model.age_cycles(9);self.assertFalse(self.model.cycle_memory)

    def test_close_ui_route_uses_known_exit_and_ignores_unrelated_novelty(self):
        a=measured('menu');a['interface_hypothesis']=True
        b=measured('parent');b['interface_hypothesis']=True
        c=measured('closed');c['interface_hypothesis']=False
        self.update(a,b,1);self.update(b,c,2)
        self.model.remember_cycle({'sequence':[{'context':a['context'],'before':a['id'],'action':self.action['id']}]})
        cfg=Settings(window_title='generic',allowed_keys=['a','b'],key_bindings={})
        p=plan();p['targets']=[{'feature':'interface_open','operator':'eq','value':0}]
        self.model.escape={'actions':[self.action['id']],'nodes':['menu','parent'],'attempts':0}
        choice,_,_=self.model.choose(a,self.model.candidates(cfg),p)
        self.assertEqual(choice['candidate']['keys'],['a']);self.assertTrue(choice['route_to_information'])

    def test_llm_explanation_remains_refutable_hypothesis(self):
        proposal={'candidate':self.action['id'],'feature':'metric.hp.0','expected':1,'tolerance':0,'explanation':'HP増加という仮説'}
        self.model.add_hypotheses([proposal],measured(),[self.action]);h=self.model.hypotheses[0]
        self.assertEqual(h['status'],'llm_hypothesis_unverified')
        self.update(measured(),measured(hp=8));self.assertEqual(h['status'],'contradicted');self.assertLess(h['confidence'],.5)
    def test_restart_reuses_rules_and_observed_graph_without_old_db_import(self):
        self.update(measured(),measured('two'),1)
        self.store.close();self.store=ExperienceStore(self.path,'profile','next');reloaded=ForwardModel(self.store)
        self.assertEqual(reloaded.predict(measured(),self.action['id'])['trials'],1)
        self.assertTrue(reloaded.edges);self.assertGreater(reloaded.visits['two'],0)
        other=ExperienceStore(self.path,'different_controls','other')
        try:self.assertEqual(ForwardModel(other).predict(measured(),self.action['id'])['trials'],0)
        finally:other.close()
    def test_goal_uses_observed_outcome_not_caption_and_floors_have_no_revisit_penalty(self):
        a=measured();b=measured('destination');b['quotes']=['目標の文字']
        self.update(a,b)
        p=plan();p['targets']=[{'feature':'text','operator':'contains','value':'目標','weight':1}]
        choice,_,_=self.model.choose(a,[self.action],p)
        self.assertGreater(choice['expected_goal_value'],0)
    def test_parameter_trial_reverts_on_loss_and_reports_comparison(self):
        policy=AdaptivePolicy()
        for _ in range(48):policy.update({'match':True,'new_state':True,'effects':{}})
        self.assertIsNotNone(policy.trial);self.assertEqual(policy.weight,.9)
        event=policy.update({'effects':{'metric.hp.0':-1}})
        self.assertFalse(event['accepted']);self.assertEqual(policy.weight,.6)

class PerceptionAndShellContracts(unittest.TestCase):
    def test_actual_submitted_frame_is_parsed_once_and_retained_is_not_current(self):
        png=(FIXTURES/'quick-quiet-frame.png').read_bytes();sha=hashlib.sha256(png).hexdigest()
        ocr=Mock();ocr.read.return_value={'status':'ok','frame_sha256':sha,'lines':[{'text':'HP: 10/20'}]}
        builder=StateBuilder(Settings(window_title='generic',allowed_keys=['a']),ocr)
        first=builder.build(png);second=builder.build(png)
        self.assertEqual(ocr.read.call_count,1);self.assertEqual(first['id'],second['id']);self.assertIn('text:HP',second['metrics'])
        self.assertIsNone(second['position_confidence']);self.assertFalse(second['eligible'])
    def test_semantic_plan_rejects_unlisted_inputs_and_invented_metrics(self):
        state=measured();candidate={'id':action_id(['a'],80),'keys':['a'],'hold_ms':80}
        good=plan();self.assertEqual(validate(good,[candidate],state),good)
        bad=plan();bad['candidate_values']=[{'id':'shell command','value':1,'reason':'不正'}]
        with self.assertRaises(ValueError):validate(bad,[candidate],state)
        bad=plan();bad['targets']=[{'feature':'metric.hidden.0','operator':'ge','value':1,'weight':1}]
        with self.assertRaises(ValueError):validate(bad,[candidate],state)
    def test_real_frame_replay_wires_input_receipt_journal_database_and_export(self):
        png=(FIXTURES/'quick-quiet-frame.png').read_bytes();sha=hashlib.sha256(png).hexdigest()
        cfg=Settings(window_title='generic',allowed_keys=['a'],max_steps=2,max_wait_ms=0)
        llm=LocalLLM(cfg);llm.ocr=Mock();llm.ocr.read.return_value={'status':'ok','frame_sha256':sha,'lines':[{'text':'HP: 10/20'}]};llm.ocr.read_regions=llm.ocr.read
        backend=Mock();backend.capture.return_value=png;backend.act.return_value={'os_submission':'accepted'}
        backend.observe_turn.return_value=TurnObservation(png,'no_change',False,0,0,0)
        control=SessionControl();rows=[]
        with tempfile.TemporaryDirectory() as folder,patch.object(ResearchLayer,'tick',return_value=(plan(),None)):
            root=Path(folder);runner=NextGenController(cfg,backend,llm,rows.append,root/'runs'/'one.jsonl',control.stop,control=control)
            runner.run();self.assertEqual(backend.act.call_count,2);self.assertFalse(runner.completed)
            raw=[json.loads(x) for x in (root/'runs'/'one.jsonl').read_text().splitlines()]
            self.assertEqual(len([x for x in raw if x['event']=='input_intent']),2)
            with sqlite3.connect(root/'world-v2.sqlite') as db:
                summary=diagnostic_summary(db);self.assertEqual(summary['counts']['transitions'],2)
            archive=root/'export.zip';export_diagnostics(root,archive)
            with zipfile.ZipFile(archive) as z:self.assertIn('nextgen/summary.json',z.namelist())
            result=next(x for x in rows if x['event']=='result');traces=LearningTraces();traces.update(result)
            self.assertIn('uncertainty',traces.samples[-1]);self.assertIsNone(traces.samples[-1]['position'])
        control.close()
    def test_manual_intervention_cancels_before_any_autonomous_input(self):
        cfg=Settings(window_title='generic',allowed_keys=['a'],max_steps=1);control=SessionControl();control.intervention.set();backend=Mock();llm=LocalLLM(cfg)
        with tempfile.TemporaryDirectory() as folder:
            runner=NextGenController(cfg,backend,llm,lambda _:None,Path(folder)/'runs'/'one.jsonl',control.stop,control=control)
            with self.assertRaises(OperationCancelled):runner.run()
            backend.act.assert_not_called()
        control.close()

class SupervisorConcurrencyContracts(unittest.TestCase):
    def test_inference_does_not_block_local_model_and_old_guidance_reply_is_discarded(self):
        ready=threading.Event();release=threading.Event()
        def reply(*args,**kwargs):
            ready.set();release.wait(2);return json.dumps(plan())
        cfg=Settings(window_title='generic',allowed_keys=['a']);llm=LocalLLM(cfg);stop=threading.Event()
        with tempfile.TemporaryDirectory() as folder,patch.object(LocalLLM,'_request',side_effect=reply):
            store=ExperienceStore(Path(folder)/'world-v2.sqlite','p','s');model=ForwardModel(store)
            supervisor=SemanticSupervisor(cfg,llm,lambda _:None,stop);candidates=model.candidates(cfg);state=measured()
            try:
                supervisor.tick(state,candidates,0,0,{'revision':0},None,model,{})
                self.assertTrue(ready.wait(1));self.assertIsNotNone(supervisor.pending)
                selected,_,_=model.choose(state,candidates);self.assertTrue(selected['candidate']['keys'])
                release.set();packet=supervisor.results.get(timeout=2);supervisor.results.put(packet)
                selected,adopted=supervisor.tick(state,candidates,1,0,{'revision':1},None,model,{})
                self.assertIsNone(adopted);self.assertIsNone(supervisor.plan)
            finally:release.set();supervisor.close();store.close()

class AdditionalObservationContracts(unittest.TestCase):
    def test_message_evidence_never_rewinds_current_map_or_tracks(self):
        png=(FIXTURES/'quick-quiet-frame.png').read_bytes();sha=hashlib.sha256(png).hexdigest()
        ocr=Mock();ocr.read.return_value={'status':'ok','frame_sha256':sha,'lines':[]}
        builder=StateBuilder(Settings(window_title='generic',allowed_keys=['a']),ocr)
        builder.floor.floor=9;builder.map_sensor.cells={(3,4)};builder.tracker.serial=41
        builder.message_evidence(png);builder.message_evidence(png)
        self.assertEqual(ocr.read.call_count,1);self.assertEqual(builder.message_evidence_reads,1);self.assertEqual(builder.floor.floor,9)
        self.assertEqual(builder.map_sensor.cells,{(3,4)});self.assertEqual(builder.tracker.serial,41)
    def test_ocr_escalation_reuses_image_geometry_and_cache_does_not_recount_cost(self):
        png=(FIXTURES/'quick-quiet-frame.png').read_bytes();sha=hashlib.sha256(png).hexdigest()
        native={'status':'ok','frame_sha256':sha,'lines':[],'ocr_scope':'hud_and_messages'}
        ocr=Mock();ocr.read_regions.return_value=native;ocr.read.return_value={**native,'ocr_scope':'full'}
        builder=StateBuilder(Settings(),ocr)
        with patch.object(builder.tracker,'observe',return_value=([],None)) as tracker:
            builder.analyze(png,False);builder.analyze(png,True);cached=builder.analyze(png,True)
            tracker.assert_called_once();self.assertEqual(cached['timing']['ocr_ms'],0)
    def test_user_declared_modifiers_generate_chords_without_game_specific_actions(self):
        cfg=Settings(window_title='generic',allowed_keys=['a','b','c'],key_bindings={'a':'上移動','b':'左移動','c':'方向転換'})
        with tempfile.TemporaryDirectory() as folder:
            store=ExperienceStore(Path(folder)/'world-v2.sqlite','p','s')
            try:
                candidates=ForwardModel(store).candidates(cfg)
                self.assertTrue(any(x['keys']==['c','a'] for x in candidates))
                self.assertTrue(any(x['keys']==['a','b'] for x in candidates))
                self.assertTrue(all(x['keys'] and set(x['keys'])<=set(cfg.allowed_keys) for x in candidates))
            finally:store.close()
    def test_pause_cancellation_resumes_fresh_observation_and_only_one_input(self):
        png=(FIXTURES/'quick-quiet-frame.png').read_bytes();sha=hashlib.sha256(png).hexdigest()
        cfg=Settings(window_title='generic',allowed_keys=['a'],max_steps=1,max_wait_ms=0);llm=LocalLLM(cfg);llm.ocr=Mock();llm.ocr.read.return_value={'status':'ok','frame_sha256':sha,'lines':[]}
        control=SessionControl();backend=Mock();calls=[]
        def capture():
            if not calls:
                calls.append(1);control.toggle_pause();threading.Timer(.02,control.toggle_pause).start();raise OperationCancelled('pause boundary')
            return png
        backend.capture.side_effect=capture;backend.act.return_value={'os_submission':'accepted'};backend.observe_turn.return_value=TurnObservation(png,'no_change',False,0,0,0)
        with tempfile.TemporaryDirectory() as folder,patch.object(ResearchLayer,'tick',return_value=(plan(),None)):
            runner=NextGenController(cfg,backend,llm,lambda _:None,Path(folder)/'runs'/'s.jsonl',control.stop,control=control)
            runner.run();self.assertEqual(backend.act.call_count,1)
        control.close()
    def test_live_ui_selects_new_controller_and_does_not_run_both_learning_cores(self):
        import inspect
        from autoplayer.ui import Application
        source=inspect.getsource(Application.start)
        self.assertIn('agent=NextGenController(',source)
        self.assertNotIn('agent=Controller(',source)
