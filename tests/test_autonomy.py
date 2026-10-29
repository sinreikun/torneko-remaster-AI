"""Upper-layer contracts, not a mock game and not proof of gameplay success."""
import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from autoplayer.nextgen.store import ExperienceStore,diagnostic_summary
from autoplayer.nextgen.meta import MetaController,FAILURES
from autoplayer.nextgen.progress import ProgressManager,validate_nodes,condition_value
from autoplayer.nextgen.capabilities import CapabilityManager
from autoplayer.nextgen.observer_adaptation import ObserverAdaptation,DEFAULTS
from autoplayer.nextgen.model import action_id,ForwardModel
from autoplayer.nextgen.research import ResearchLayer
from autoplayer.nextgen.supervisor import SemanticSupervisor,validate
from autoplayer.nextgen.brief_protocol import schema_for,decode
from autoplayer.core import Settings,LocalLLM
from test_nextgen import measured


def state(frame='f1',menu=False,text='観測原文'):
    s=measured(frame);s.update(frame_sha256=frame,quotes=[text],interface_hypothesis=menu,interface_layout='layout',interface_observation={'visible':menu},map_domain='domain')
    return s

def condition(feature='text',value='目標物',operator='contains'):return {'feature':feature,'operator':operator,'value':value}
def node(id='see',requires=(),scope='session',conditions=None):
    return {'id':id,'label':id,'parent':'','requires':list(requires),'scope':scope,'conditions':conditions or [condition()], 'counterconditions':[], 'capabilities':['verify_possession'],'source':'reference hypothesis'}

def result(match=True):return {'status':'updated','match':match,'effects':{},'hypothesis_checks':[]}

class AutonomyContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'world.sqlite';self.store=ExperienceStore(self.path,'generic','run')
        self.action={'id':action_id(['a'],80),'keys':['a'],'hold_ms':80}
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def test_stalled_unconditioned_ui_gets_finite_close_task_and_resumes_original_goal(self):
        m=MetaController(self.store,'intent');before=state(menu=True);m.prepare(before,0)
        original=copy.deepcopy(m.local);m.recovery=None;m.failures=['repetition_stagnation'];m.new_recovery(before,1)
        self.assertEqual(m.recovery['method'],'close_unproductive_interface')
        guided=m.guide({'objective':'original','targets':[]},before,[self.action])
        self.assertEqual(guided['targets'][0]['feature'],'interface_open')
        after=state('closed',menu=False)
        m.observe(before,self.action,after,result(),{'os_submission':'accepted'},2,100)
        self.assertEqual(m.local['id'],original['id'])
        self.assertTrue(any(e.get('method')=='close_unproductive_interface' and e.get('status')=='verified_recovery' for e in m.history))

    def test_recovery_is_not_abandoned_when_selection_bootstrap_changes(self):
        m=MetaController(self.store,'intent');a=state(menu=True);m.prepare(a,0)
        m.recovery=None;m.failures=['repetition_stagnation'];m.new_recovery(a,1)
        identifier=m.recovery['id'];original=m.local['id']
        b=state('other',menu=True);b['selection_observation']={'line_index':0}
        m.prepare(b,2)
        self.assertEqual(m.recovery['id'],identifier);self.assertEqual(m.local['id'],original)
        self.assertEqual(m.guide({'targets':[]},b,[self.action])['targets'][0]['feature'],'interface_open')

    def test_bootstrap_switch_requires_two_distinct_observation_frames(self):
        m=MetaController(self.store,'intent');m.ensure_local(state(),0);original=m.local['id']
        b=state('b',menu=True);b['selection_observation']={'line_index':0}
        m.ensure_local(b,1);m.ensure_local(b,2)
        self.assertEqual(m.local['id'],original)
        c=copy.deepcopy(b);c['frame_sha256']='c';m.ensure_local(c,3)
        self.assertEqual(m.local['capability'],'change_selection')

    def test_progress_requires_independent_evidence_and_prerequisites(self):
        p=ProgressManager(self.store);p.propose([node(),node('return',['see'],conditions=[condition(value='帰還確認')])])
        self.assertEqual(p.current()['id'],'see')
        p.update(state('1',text='目標物'),1);self.assertEqual(p.nodes['see']['status'],'supported')
        p.update(state('1',text='目標物'),2);self.assertEqual(p.nodes['see']['status'],'supported')
        p.update(state('2',text='目標物'),3);self.assertEqual(p.nodes['see']['status'],'confirmed');self.assertEqual(p.current()['id'],'return')
        self.assertEqual(p.nodes['return']['status'],'unconfirmed') # Seeing is not returning.
    def test_later_stage_evidence_cannot_bypass_unconfirmed_prerequisites(self):
        p=ProgressManager(self.store)
        p.propose([node('first',conditions=[condition(value='first evidence')]),node('later',['first'],conditions=[condition(value='later evidence')])])
        for i in range(2):p.update(state('early'+str(i),text='later evidence'),i)
        self.assertEqual(p.nodes['later']['status'],'supported')
        self.assertNotIn('later',[n['id'] for n in p.available()])
        for i in range(2):p.update(state('first'+str(i),text='first evidence'),i+2)
        self.assertEqual(p.nodes['first']['status'],'confirmed')
        p.update(state('next',text='later evidence'),4)
        self.assertEqual(p.nodes['later']['status'],'confirmed')
        # Counterevidence to an earlier stage blocks the dependent again.
        p.nodes=dict(reversed(list(p.nodes.items()))) # Restore order must not affect gating.
        p.nodes['first']['counterconditions']=[condition(value='retracted')]
        p.update(state('bad',text='retracted'),5)
        self.assertNotEqual(p.nodes['later']['status'],'confirmed')

    def test_campaign_needs_multiple_channels_and_counterevidence_blocks(self):
        p=ProgressManager(self.store);n=node(scope='campaign');p.propose([n])
        for i in range(3):p.update(state(str(i),text='目標物'),i)
        self.assertEqual(p.nodes['see']['status'],'supported')
        other=node('event',scope='campaign',conditions=[condition(),condition('interface_open',1,'eq')]);other['counterconditions']=[condition(value='未達成')]
        p.propose([other,node('next',['event'])])
        for i in range(2):p.update(state('m'+str(i),True,'目標物'),i)
        self.assertEqual(p.nodes['event']['status'],'confirmed')
        p.update(state('bad',True,'未達成'),3);self.assertEqual(p.nodes['event']['status'],'disputed');self.assertNotIn('next',[n['id'] for n in p.available()])
    def test_missing_is_unknown_and_cycles_and_executable_features_rejected(self):
        self.assertIsNone(condition_value(state(),condition('metric.absent.0',1,'ge')))
        with self.assertRaises(ValueError):validate_nodes([node('a',['b']),node('b',['a'])])
        with self.assertRaises(ValueError):validate_nodes([node(conditions=[condition('__import__(os)',1,'eq')])])
    def test_identical_success_and_counterevidence_is_rejected_atomically(self):
        p=ProgressManager(self.store);bad=node();bad['counterconditions']=copy.deepcopy(bad['conditions'])
        with self.assertRaisesRegex(ValueError,'Identical success'):p.propose([node('valid'),bad])
        self.assertFalse(p.nodes)

    def test_visible_hud_selection_goal_is_deferred_without_deleting_history(self):
        m=MetaController(self.store,'hud');s=state(menu=True,text='HUD')
        s['interface_observation']['rectangles']=[[.1,.3,.8,.8]]
        s['ui_visual']={'elements':[{'text':'HUD','center':[.5,.1]}]}
        m.local={'id':'bad','origin':'llm_hypothesis','capability':'change_selection','conditions':[condition('selection_text','HUD')],'parent':None,'deadline':32}
        m.prepare(s,0)
        self.assertNotEqual(m.local['id'],'bad');self.assertEqual(m.deferred[-1]['id'],'bad')

    def test_saved_internal_goal_label_is_not_a_game_text_target(self):
        m=MetaController(self.store,'goal');n=node('internal',conditions=[condition(value='internal instruction')]);n['label']='internal instruction'
        m.progress.propose([n]);m.progress.nodes['internal']['literal_grounding_version']=1
        m.local={'id':'node:internal','parent':'internal','origin':'progress_graph','capability':'inspect','conditions':n['conditions'],'deadline':32}
        m.prepare(state('now',menu=True,text='actual screen'),0)
        self.assertTrue(m.progress.nodes['internal']['unverified_literals']);self.assertNotEqual(m.local['id'],'node:internal')
        self.assertFalse(m.progress.available());self.assertTrue(m.needs_plan(20))
        m.prepare(state('later',menu=True,text='internal instruction'),1)
        self.assertFalse(m.progress.nodes['internal']['unverified_literals'])
        self.assertEqual(m.local['parent'],'internal')
    def test_external_or_previously_observed_label_is_not_quarantined(self):
        m=MetaController(self.store,'goal',reference_knowledge='expected label')
        n=node('ref',conditions=[condition(value='expected label')]);n['label']='expected label'
        self.assertFalse(m.ungrounded_literals(n,state(text='other')))
        n['label']='observed label';n['conditions']=[condition(value='observed label')];n['evidence']=[{'conditions':copy.deepcopy(n['conditions'])}]
        self.assertFalse(m.ungrounded_literals(n,state(text='other')))

    def test_ui_goal_is_deferred_only_after_independent_new_scope_observations(self):
        m=MetaController(self.store,'scope');s=state(menu=True,text='different screen')
        s['ui_visual']={'layout':'new','elements':[{'text':'other'}]}
        goal={'id':'select','label':'original intent','parent':'parent','origin':'llm_hypothesis','ui_scope':'old',
            'conditions':[condition('selection_text','target')],'capability':'change_selection','deadline':32}
        m.local=copy.deepcopy(goal)
        m.check_local_ui(s,1);m.check_local_ui(s,2);self.assertIsNotNone(m.local) # Cached frame is not two proofs.
        s['frame_sha256']='independent';m.check_local_ui(s,3)
        self.assertIsNone(m.local);self.assertEqual(m.deferred[-1]['parent'],'parent')
        self.assertEqual(m.context()['deferred_goal']['label'],'original intent')
        m.local=copy.deepcopy(goal);s['ui_visual']['layout']='old'
        for i in range(3):s['frame_sha256']=str(i);m.check_local_ui(s,i)
        self.assertIsNotNone(m.local) # Missing text in the same UI can be off-page.
        s['ui_visual']['layout']='new';s['quality']=.2
        for i in range(3):s['frame_sha256']='bad'+str(i);m.check_local_ui(s,i)
        self.assertIsNotNone(m.local)
    def test_legacy_ui_goal_gets_scope_when_target_is_observed(self):
        m=MetaController(self.store,'legacy');s=state(menu=True,text='target')
        s['ui_visual']={'layout':'current','elements':[{'text':'target'}]}
        m.local={'id':'select','origin':'llm_hypothesis','conditions':[condition('selection_text','target')]}
        m.check_local_ui(s,0);self.assertEqual(m.local['ui_scope'],'current')

    def test_leaf_goal_before_parent_and_no_fixed_game_names(self):
        p=ProgressManager(self.store);parent=node('mission');child=node('obtain');child['parent']='mission';p.propose([parent,child])
        self.assertEqual(p.current()['id'],'obtain')
    def test_skill_acquisition_reuse_counterexample_and_context_mismatch(self):
        c=CapabilityManager(self.store)
        for i in range(2):c.observe(state('a'+str(i),True),self.action,state('b'+str(i),False),result(),i)
        self.assertTrue(c.matching('close_interface',state('now',True)))
        self.assertTrue(c.priors('close_interface',state('now',True),[self.action]))
        self.assertFalse(c.matching('close_interface',state('now',False)))
        for i in range(4):c.observe(state('fail'+str(i),True),self.action,state('stuck'+str(i),True),result(),i)
        self.assertFalse(c.matching('close_interface',state('now',True)))
    def test_missing_and_natural_changes_do_not_become_skills(self):
        c=CapabilityManager(self.store);a=state('a',True);b=state('b',False)
        c.observe(a,self.action,b,{'status':'censored'},1)
        self.assertFalse(c.skills)
        c.observe(a,self.action,b,result(),2,neutral={'interface_changed':1})
        self.assertFalse(c.skills)
        c.observe(a,self.action,b,result(),3,'acquire')
        self.assertFalse(any(s['name']=='acquire' for s in c.skills.values()))
    def test_missing_capability_generates_finite_task_and_alternate_hypothesis(self):
        m=MetaController(self.store,'長期の意図');s=state('one',True);m.prepare(s,0)
        original=copy.deepcopy(m.local);self.assertIn('capability_missing',m.failures);self.assertIsNotNone(m.recovery)
        method=m.recovery['method']
        for i in range(1,9):m.observe(s,self.action,s,result(),{'os_submission':'accepted'},i,100)
        self.assertEqual(m.local['id'],original['id']);self.assertNotEqual(m.recovery['method'],method)
        self.assertTrue(any(e.get('status')=='expired_try_other_hypothesis' for e in m.history))
    def test_recovery_success_resumes_original_mission(self):
        m=MetaController(self.store,'達成');m.progress.propose([node(conditions=[condition(value='完了')])]);a=state('a',True);m.prepare(a,0)
        original=m.local['id'];self.assertEqual(m.recovery['resume']['id'],original)
        m.observe(a,self.action,state('b',True,'完了'),result(),{'os_submission':'accepted'},1,100)
        self.assertTrue(any(e.get('status')=='verified_recovery' and e['resume']==original for e in m.history))
        self.assertNotEqual(m.progress.nodes['see']['status'],'confirmed')
    def test_failure_taxonomy_distinguishes_os_no_reaction_and_unknown(self):
        m=MetaController(self.store,'達成');s=state();m.prepare(s,0)
        fs=m.classify(s,{'match':False,'loop':{'event':'loop_detected'},'hypothesis_checks':[{'status':'contradicted'}]}, {'os_submission':'failed'})
        self.assertTrue({'world_prediction_failure','repetition_stagnation','llm_measurement_mismatch','input_not_delivered'}<=set(fs))
        self.assertNotIn('input_not_delivered',m.classify(s,result(),{'os_submission':'accepted'}));self.assertIn('unclassified',FAILURES)
    def test_observation_shortage_starts_trial_and_worsening_rolls_back(self):
        m=MetaController(self.store,'達成');s=state();s['quality']=.3;s['measurement_status']='unavailable'
        for i in range(4):m.observer.observe(s,result(None),100,False,i)
        m.prepare(s,4);self.assertIn('observation_missing',m.failures);self.assertIsNotNone(m.observer.trial)
        self.assertNotEqual(m.observer.policy,DEFAULTS)
        bad=result();bad['effects']={'metric.hp.0':-1}
        e=m.observer.observe(s,bad,200,False,5);self.assertEqual(e['status'],'rollback');self.assertEqual(m.observer.policy,DEFAULTS)
    def test_observation_improvement_is_compared_and_adopted(self):
        o=ObserverAdaptation(self.store);s=state();s['quality']=.4
        for i in range(4):o.observe(s,result(False),100,False,i)
        self.assertTrue(o.start('observation_missing',4));s['quality']=.9
        for i in range(5,11):event=o.observe(s,result(True),110,False,i)
        self.assertEqual(event['status'],'adopted');self.assertEqual(event['baseline']['quality'],.4);self.assertGreater(event['measured']['quality'],.8)
    def test_no_improvement_and_restart_rollback(self):
        o=ObserverAdaptation(self.store);s=state()
        for i in range(4):o.observe(s,result(),100,False,i)
        o.start('observation_missing',4)
        for i in range(5,11):event=o.observe(s,result(),100,False,i)
        self.assertEqual(event['status'],'rollback')
        o.start('observation_missing',150);self.store.queue.join();restored=ObserverAdaptation(self.store)
        self.assertEqual(restored.policy,DEFAULTS);self.assertEqual(restored.history[-1]['reason'],'interrupted_trial')
    def test_progress_and_skills_persist_and_diagnostics_include_upper_layer(self):
        p=ProgressManager(self.store);p.propose([node()]);p.update(state('one',text='目標物'),1)
        self.store.queue.join();loaded=ProgressManager(self.store);self.assertEqual(loaded.nodes['see']['status'],'supported')
        self.assertIn('progress',diagnostic_summary(self.store.reader)['autonomy'])
    def test_llm_plan_proposals_are_validated_but_not_facts(self):
        aliases={'A00':self.action['id']};reply={'local_objective':'選択を確認','reason':'観測を比較','preferred':['A00'],'discouraged':[],'success_test':'interface_closed','success_text':'','review_after':16,'required_capability':'close_interface','progress_nodes':[node()]}
        plan,_=decode(reply,aliases,[self.action],state(),validate);m=MetaController(self.store,'達成');m.accept(plan,state(menu=True),0)
        self.assertEqual(m.progress.nodes['see']['status'],'unconfirmed');self.assertEqual(m.local['capability'],'close_interface')
        self.assertNotIn('progress_nodes',schema_for(aliases)['properties']);self.assertIn('progress_nodes',schema_for(aliases,True)['properties'])
    def test_long_intent_is_only_in_bounded_planning_material(self):
        m=MetaController(self.store,'長期意図'*100);m.prepare(state(),0)
        initial=m.planning_context(state(),0);usual=m.planning_context(state(),1)
        self.assertIn('planning_material',initial);self.assertNotIn('planning_material',usual)
        self.assertNotIn('長期意図',json.dumps(usual,ensure_ascii=False))
    def test_plan_changes_real_action_prior_without_new_inputs(self):
        m=MetaController(self.store,'達成');c=m.capabilities
        for i in range(2):c.observe(state('a'+str(i),True),self.action,state('b'+str(i)),result(),i)
        s=state('now',True);m.prepare(s,3);m.recovery=None
        m.accept({'objective':'閉じる効果を検証','targets':[{'feature':'interface_open','operator':'eq','value':0}],'required_capability':'close_interface'},s,3)
        base={'objective':'fallback','targets':[],'candidate_values':[]}
        # The generic bootstrap uses interface closure, whose condition is checked per input.
        for i in range(2):c.observe(state('c'+str(i),True),self.action,state('d'+str(i)),result(),i,'close_interface',m.local['conditions'])
        plan=m.guide(base,s,[self.action]);self.assertEqual(plan['candidate_values'][0]['id'],self.action['id']);self.assertEqual(plan['targets'][0]['value'],0)
    def test_boundary_resets_expedition_not_permanent_progress_or_skills(self):
        m=MetaController(self.store,'達成');m.progress.propose([node(),node('permanent',scope='campaign',conditions=[condition(),condition('interface_open',1,'eq')])])
        for i in range(2):m.progress.update(state('goal'+str(i),True,'目標物'),i)
        self.assertEqual(m.progress.nodes['see']['status'],'confirmed')
        m.boundary={'conditions':[condition(value='冒険終了')],'frames':[],'armed':True}
        m.prepare(state('end1',True,'冒険終了'),3);self.assertEqual(m.progress.nodes['see']['status'],'confirmed')
        m.prepare(state('end2',True,'冒険終了'),4)
        self.assertEqual(m.progress.nodes['see']['status'],'unconfirmed');self.assertEqual(m.progress.nodes['permanent']['status'],'confirmed')
    def test_disputed_progress_can_recover_with_fresh_evidence(self):
        p=ProgressManager(self.store);n=node();n['counterconditions']=[condition(value='反証')];p.propose([n])
        p.update(state('bad',text='反証'),1);self.assertEqual(p.nodes['see']['status'],'disputed')
        for i in range(2):p.update(state('new'+str(i),text='目標物'),i+2)
        self.assertEqual(p.nodes['see']['status'],'confirmed');self.assertTrue(p.nodes['see']['counterevidence'])
    def test_observer_policy_actually_refreshes_ocr_on_identical_pixels(self):
        import hashlib
        from autoplayer.nextgen.state import StateBuilder
        png=(Path(__file__).parent/'fixtures'/'quick-quiet-frame.png').read_bytes();sha=hashlib.sha256(png).hexdigest()
        reader=Mock();reader.read.side_effect=[{'status':'ok','frame_sha256':sha,'lines':[{'text':'HP: 10/20'}]},{'status':'ok','frame_sha256':sha,'lines':[{'text':'HP: 11/20'}]}]
        builder=StateBuilder(Settings(window_title='generic'),reader);a=builder.build(png)
        builder.observation_policy={'cache_uses':0};b=builder.build(png)
        self.assertEqual(reader.read.call_count,2);self.assertEqual(b['metrics']['text:HP']['values'],[11,20]);self.assertFalse(reader.force_fresh)
    def test_supervisor_refinement_is_not_lost_inside_recovery(self):
        m=MetaController(self.store,'達成');s=state(menu=True);m.prepare(s,0)
        p={'objective':'選択項目を合わせる','targets':[{'feature':'selection_text','operator':'contains','value':'目的の項目','weight':1}], 'required_capability':'change_selection'}
        m.accept(p,s,1);self.assertIsNotNone(m.pending_local)
        for i in range(1,9):m.observe(s,self.action,s,result(),{'os_submission':'accepted'},i,100)
        self.assertEqual(m.local['capability'],'change_selection')
    def test_real_capture_replay_emits_and_persists_autonomous_lifecycle(self):
        import hashlib
        from autoplayer.nextgen.controller import NextGenController
        from autoplayer.observation import TurnObservation
        from autoplayer.session import SessionControl
        png=(Path(__file__).parent/'fixtures'/'quick-quiet-frame.png').read_bytes();sha=hashlib.sha256(png).hexdigest()
        cfg=Settings(window_title='generic',allowed_keys=['a'],max_steps=3,max_wait_ms=0)
        llm=LocalLLM(cfg);llm.ocr=Mock();llm.ocr.read.return_value={'status':'ok','frame_sha256':sha,'lines':[{'text':'HP: 10/20'}]}
        backend=Mock();backend.capture.return_value=png;backend.act.return_value={'os_submission':'accepted'};backend.observe_turn.return_value=TurnObservation(png,'no_change',False,0,0,0)
        control=SessionControl();rows=[]
        with patch.object(ResearchLayer,'tick',return_value=({'objective':'検証','reason':'経験照合','targets':[],'candidate_values':[],'hypotheses':[]},None)):
            runner=NextGenController(cfg,backend,llm,rows.append,Path(self.tmp.name)/'runs'/'replay.jsonl',control.stop,control=control)
            runner.run()
        events=[r for r in rows if r.get('event')=='autonomy_update'];self.assertEqual(len(events),3)
        self.assertTrue(all(r['state']['current_goal'] for r in events));self.assertEqual(backend.act.call_count,3)
        with sqlite3.connect(Path(self.tmp.name)/'world-v2.sqlite') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM autonomy').fetchone()[0],1)
    def test_recovery_hypotheses_do_not_restart_forever(self):
        m=MetaController(self.store,'達成');s=state(menu=True);m.prepare(s,0)
        for i in range(1,49):m.observe(s,self.action,s,result(),{'os_submission':'accepted'},i,100)
        self.assertIsNone(m.recovery);self.assertTrue(m.exhausted)
        self.assertEqual(len([e for e in m.history if e['event']=='recovery_started']),4) # close/alternative/observer/replan, no duplicate variant
    def test_recovery_budget_survives_screen_churn_goal_rename_and_restart(self):
        m=MetaController(self.store,'budget');s=state(menu=True);m.prepare(s,0)
        goal=copy.deepcopy(m.local);methods=[m.recovery['method']]
        for i in range(1,7):
            m.recovery=None;m.local=copy.deepcopy(goal);m.local['id']='renamed:'+str(i)
            changed=state(str(i),menu=i%2==0);changed['context']='ocr-context:'+str(i);changed['interface_layout']='layout:'+str(i)
            m.failures=['capability_missing'];m.new_recovery(changed,i)
            if m.recovery:methods.append(m.recovery['method'])
        self.assertEqual(len(methods),3);self.assertEqual(len(set(methods)),3)
        m.save();self.store.close();self.store=ExperienceStore(self.path,'generic','restart')
        restored=MetaController(self.store,'budget');restored.local=goal;restored.failures=['capability_missing']
        restored.new_recovery(state('restart'),20)
        self.assertIsNone(restored.recovery);self.assertTrue(restored.exhausted)
        # A different measurable task can still run recovery.
        restored.local={**goal,'conditions':[condition(value='different evidence')]}
        restored.new_recovery(s,21);self.assertIsNotNone(restored.recovery)
    def test_equivalent_supervisor_goal_does_not_renew_deadline(self):
        m=MetaController(self.store,'goal');s=state(menu=True)
        proposal={'objective':'first','targets':[condition(value='unseen')],'required_capability':'confirm'}
        m.accept(proposal,s,1);old=copy.deepcopy(m.local)
        m.accept({**proposal,'objective':'renamed'},s,20)
        self.assertEqual(m.local,old)
        m.failures=['capability_missing'];m.new_recovery(s,21)
        m.accept(proposal,s,22);self.assertIsNone(m.pending_local)
    def test_old_exhausted_goal_does_not_force_reviews_for_new_task(self):
        m=MetaController(self.store,'review');m.progress.propose([node()]);m.last_proposal=0
        old={'capability':'inspect','conditions':[]};m.exhausted.add(m.recovery_goal_key(old))
        m.local={'capability':'verify_possession','conditions':[condition()]}
        self.assertFalse(m.needs_plan(20))
        m.local=old;self.assertTrue(m.needs_plan(20))

    def test_full_recovery_budget_does_not_evict_exhausted_tasks(self):
        m=MetaController(self.store,'capacity');m.recovery_budgets={str(i):{'method':'replan'} for i in range(128)}
        m.prepare(state(menu=True),0)
        self.assertIsNone(m.recovery);self.assertEqual(len(m.recovery_budgets),128)
        self.assertTrue(m.context()['recovery_budget']['capacity_reached'])

    def test_nonexecution_is_classified_and_observation_trial_has_wall_deadline(self):
        m=MetaController(self.store,'達成');s=state();m.prepare(s,0)
        for i in range(4):m.nonexecution(s,0,100)
        self.assertIn('state_identification_failure',m.failures)
        self.assertTrue(m.observer.start('observation_missing',0))
        m.observer.trial['wall_deadline']=0
        event=m.observer.expire();self.assertEqual(event['status'],'rollback');self.assertEqual(m.observer.policy,DEFAULTS)
    def test_changed_user_intent_does_not_activate_an_old_mission(self):
        m=MetaController(self.store,'意図A');m.progress.propose([node()]);self.store.queue.join()
        other=MetaController(self.store,'意図B')
        self.assertIsNone(other.progress.current());self.assertIn('see',other.progress.nodes)
