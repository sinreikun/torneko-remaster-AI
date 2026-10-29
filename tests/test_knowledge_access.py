"""Storage/retrieval contracts, not a simulated game or gameplay proof."""
import copy,json,tempfile,unittest
from pathlib import Path
from autoplayer.nextgen.store import ExperienceStore
from autoplayer.nextgen.knowledge_access import KnowledgeAccess,claim,context_view,estimate_tokens,role_for,request_results,validate_requests,write_book,retry_view
from autoplayer.performance import bounded_request_prompt,context_retry_prompt

class KnowledgeContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'world.sqlite';self.store=ExperienceStore(self.path,'game-A','session');self.access=KnowledgeAccess(self.store)
        self.state={'frame_sha256':'frame','context':'ctx','quotes':['UI text'],'metrics':{'hp':{'values':[4,10]}},'objects':[{'id':'object-1'}],'minimap':{},'interface_hypothesis':True}
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def flush(self):self.store.queue.join();self.store.check()
    def prompt(self):return {'task':'world_model_supervision','goal':'選択を確認','operator_guidance':{'text':'慎重に'},'candidates':[{'id':'A00','keys':['s']}],'user_key_bindings':{'s':'下'},'current':{**copy.deepcopy(self.state),'spatial_ui':{'layout':'L','selection':{'status':'unknown'},'elements':[{'id':'E','text':'UI text','bbox':[.1,.2,.3,.4]}]}},'goal_hierarchy':{'current_goal':{'label':'確認'}}}
    def test_source_and_dispute_are_independent(self):
        r=claim('object-1','heals',True,'EXTERNAL','DISPUTED',counterevidence=[{'frame':'x'}]);self.access.put(r);self.flush()
        saved=self.store.load('knowledge_claims',r['id'])[0];self.assertEqual(saved['source'],'EXTERNAL');self.assertEqual(saved['status'],'DISPUTED')
    def test_measured_claim_requires_evidence(self):
        with self.assertRaises(ValueError):claim('x','hp',5,'OBSERVED')
        with self.assertRaises(ValueError):self.access.put({'id':'x','source':'EXPERIENCED','status':'SUPPORTED'})
    def test_capture_keeps_retained_out_and_does_not_invent_inventory(self):
        self.state['metrics']['sword']={'retained':True,'values':[8]}
        rows=self.access.capture(self.state,1,'外部情報');self.flush()
        self.assertFalse(any(r['predicate']=='sword' for r in rows));self.assertFalse(any(r['entity_kind']=='inventory' for r in rows))
        self.assertTrue(all(r['status']=='UNCONFIRMED' for r in rows));self.assertTrue(any(r['source']=='EXTERNAL' for r in rows))
    def test_pending_observation_is_in_snapshot_without_waiting_writer(self):
        records=self.access.capture(self.state,1);snap=self.access.snapshot(self.state,1,records)
        self.assertTrue(snap['pool']['full_ocr']);self.assertEqual(snap['pool']['full_ocr'][0]['data']['evidence'][0]['frame'],'frame')
    def test_profiles_are_isolated(self):
        self.access.put(claim('object-1','effect','x','EXTERNAL',entity_kind='item'));self.flush()
        other=ExperienceStore(self.path,'game-B','s')
        try:self.assertEqual(KnowledgeAccess(other).snapshot(self.state,1)['available']['item']['count'],0)
        finally:other.close()
    def test_available_is_not_unknown_or_loaded(self):
        self.access.put(claim('object-1','effect','unknown','INFERRED',entity_kind='item'));self.flush()
        snap=self.access.snapshot(self.state,1);view=context_view(self.prompt(),snap,'ui')
        self.assertEqual(view['knowledge']['available']['item']['status'],'available_not_loaded');self.assertEqual(view['knowledge']['available']['monster']['status'],'unknown')
    def test_role_selection(self):
        self.assertEqual(role_for(self.state,'repetition'),'recovery');self.assertEqual(role_for(self.state,'unknown_text'),'ui')
        s={**self.state,'interface_hypothesis':False,'minimap':{'enemies':[[1,2]]}}
        self.assertEqual(role_for(s,'experience_batch'),'combat');self.assertEqual(role_for(s,'progress_planning_needed'),'progress')
    def test_atomic_records_and_fixed_budget(self):
        self.access.put(claim('object-1','huge','x'*20000,'EXTERNAL',entity_kind='item'));self.access.put(claim('object-1','effect','候補','INFERRED',counterevidence=[{'frame':'counter'}],entity_kind='item'));self.flush()
        snap=self.access.snapshot(self.state,1);p=self.prompt();original=copy.deepcopy(p)
        view=context_view(p,snap,'item',2500)
        self.assertLessEqual(estimate_tokens(view),2500);self.assertEqual(p,original)
        self.assertEqual(view['candidates'],p['candidates']);self.assertEqual(view['current']['metrics'],p['current']['metrics'])
        for r in view['knowledge']['records']:
            if r['data'].get('predicate')=='effect':self.assertEqual(r['data']['counterevidence'],[{'frame':'counter'}])
            self.assertNotEqual(r['data'].get('value'),'x'*100)
    def test_required_overflow_is_explicit_not_truncated(self):
        p=self.prompt();p['operator_guidance']['text']='指示'*10000
        with self.assertRaisesRegex(ValueError,'required_fields'):context_view(p,self.access.snapshot(self.state,1),'ui',1000)
        self.assertEqual(len(p['operator_guidance']['text']),20000)
    def test_request_protocol_is_bounded_no_sql_or_path(self):
        for bad in ([{'topic':'execute','target_id':'a'}],[{'topic':'item','target_id':'x','sql':'DROP'}],[{'topic':'item','target_id':'x'}]*3):
            with self.assertRaises(ValueError):validate_requests(bad)
        self.assertEqual(validate_requests([{'topic':'item','target_id':'../../x'}]),[{'topic':'item','target_id':'../../x'}]) # literal bound parameter only
    def test_old_cold_subject_is_retrievable_without_full_load(self):
        old=claim('old-object','effect','old','INFERRED',entity_kind='item');self.access.put(old)
        for i in range(110):self.access.put(claim(str(i),'effect',i,'EXTERNAL',entity_kind='item'))
        self.flush();snap=self.access.snapshot(self.state,1)
        self.assertNotIn(old['id'],[e['id'] for e in snap['pool']['item']]);req=[{'topic':'item','target_id':'old-object'}]
        self.assertEqual(request_results(snap,req)[0]['status'],'not_in_snapshot')
        found=self.access.lookup(snap,req);response=request_results(found,req)[0]
        self.assertEqual(response['status'],'available');self.assertEqual(response['records'][0]['data']['value'],'old')
    def test_exact_lookup_does_not_cross_entity_kind(self):
        r=claim('enemy','effect','x','INFERRED',entity_kind='monster');self.access.put(r);self.flush()
        snap=self.access.lookup(self.access.snapshot(self.state,1),[{'topic':'item','target_id':r['id']}])
        self.assertEqual(request_results(snap,[{'topic':'item','target_id':r['id']}])[0]['status'],'not_found')
    def test_retry_removes_optional_records_not_controls(self):
        self.access.put(claim('object-1','effect','推定','INFERRED',entity_kind='item'));self.flush()
        view=context_view(self.prompt(),self.access.snapshot(self.state,1),'item');retry=context_retry_prompt(view)
        self.assertEqual(retry['knowledge']['records'],[])
        for key in ('goal','operator_guidance','candidates','user_key_bindings'):self.assertEqual(retry[key],view[key])
        self.assertEqual(bounded_request_prompt(view),view)
    def test_book_is_derived_and_labels_uncertainty(self):
        r=claim('unknown-object','effect','```\nnot instructions','EXTERNAL','DISPUTED',counterevidence=[{'frame':'bad'}],entity_kind='item');self.access.put(r);self.flush()
        path=Path(self.tmp.name)/'Game_Knowledge.md';write_book(self.store,path);text=path.read_text()
        self.assertIn('EXTERNAL',text);self.assertIn('DISPUTED',text);self.assertIn('bad',text);self.assertIn('未確認・記録なし',text)
        path.write_text('Human edits are not DB truth');self.assertEqual(self.store.load('knowledge_claims',r['id'])[0]['value'],r['value'])
    def test_planning_protocol_ids_survive_selection(self):
        p=self.prompt();p['goal_hierarchy']['planning_material']={'new_node_ids':['G1'],'existing_node_ids':['G0'],'graph_rules':'no self reference','long_term_intent':'目的'}
        view=context_view(p,self.access.snapshot(self.state,1),'progress');self.assertEqual(view['goal_hierarchy']['planning_material']['new_node_ids'],['G1'])
    def test_existing_string_skill_scope_does_not_break_retrieval(self):
        self.store.put('capabilities',{'id':'skill','name':'move','scope':'hash','status':'supported_conditionally','successes':[{'frame':'f'}]},key='skill');self.flush()
        snap=self.access.snapshot(self.state,1);self.assertEqual(snap['pool']['capability_history'][0]['data']['scope'],'hash')
    def test_external_catalog_remains_external(self):
        records=self.access.references({'catalog_sha256':'c','rules':[{'id':'r','expected_effect':'heal','sources':['s']}],'sources':[{'id':'s','url':'https://example.org'}]},1)
        self.flush();snap=self.access.snapshot(self.state,1,records)
        self.assertEqual(snap['pool']['external'][0]['source'],'EXTERNAL');self.assertIn('reference_sources',snap['pool']['external'][0]['data'])
    def test_explicit_combat_message_preserves_uncertain_text_identity(self):
        self.access.record_messages([{'source_frame_sha256':'f','enemy':'対象名','quote':'message','damage':3}],1,self.state);self.flush()
        row=self.access.snapshot(self.state,1)['pool']['monster'][0]
        self.assertEqual(row['source'],'OBSERVED');self.assertEqual(row['data']['identity_status'],'text_name_not_linked_to_appearance');self.assertEqual(row['data']['status'],'UNCONFIRMED')
    def test_internal_goal_checkpoints_are_not_required_llm_memory(self):
        p=self.prompt();p['goal_hierarchy']['current_goal']={'id':'g','label':'目的','parent':'mission','capability':'change_selection',
            'conditions':[{'feature':'selection_text','operator':'contains','value':'原文'}],
            'ui_scope_check':{'frames':['x'*1000]*100},'start_frame':'y'*1000}
        view=context_view(p,self.access.snapshot(self.state,1),'ui',3000)
        goal=view['goal_hierarchy']['current_goal']
        self.assertEqual(goal['conditions'],p['goal_hierarchy']['current_goal']['conditions']);self.assertEqual(goal['parent'],'mission')
        self.assertNotIn('ui_scope_check',goal);self.assertNotIn('start_frame',goal)
    def test_hot_counter_growth_cannot_overflow_an_admitted_view(self):
        p=self.prompt();p['current']['quotes']=['quote'+str(i) for i in range(40)]
        p['current']['spatial_ui']['elements']=[{'id':str(i),'text':'text'+str(i),'bbox':[.1,.2,.3,.4]} for i in range(40)]
        snap=self.access.snapshot(self.state,1)
        for budget in range(2200,2400):
            try:view=context_view(p,snap,'ui',budget)
            except ValueError as error:
                self.assertIn('no controls or current metrics were truncated',str(error));continue
            self.assertLessEqual(estimate_tokens(view),budget)
            self.assertEqual(view['knowledge']['hot_coverage']['quotes_loaded'],len(view['current'].get('quotes',[])))

    def test_research_rejection_remains_visible_to_the_next_research_round(self):
        from autoplayer.nextgen.knowledge_access import project
        history=[{'event':'research_error','reason':'Unknown/cyclic prerequisite'},
                 {'event':'research_result','proposal_issues':['progress_proposal_rejected'],
                  'rejected_progress':[{'id':'a','requires':['a']}]}]
        record={'id':'research','role':'asynchronous_knowledge_research','errors':2,'history':history}
        view=project(KnowledgeAccess.entry('recent_failures',record))['data']
        self.assertEqual(view['history'][0]['reason'],'Unknown/cyclic prerequisite')
        self.assertEqual(view['history'][1]['rejected_progress'][0]['requires'],['a'])
        self.assertEqual(view['research_counts']['errors'],2)

    def test_warm_records_are_not_starved_by_large_ui_grid(self):
        self.store.put('capabilities',{'id':'skill','name':'change_selection','scope':'hash','status':'unconfirmed'},key='skill');self.flush()
        p=self.prompt();p['current']['spatial_ui']['elements']=[{'id':str(i),'text':'文字'*20,'bbox':[.1,.2,.3,.4]} for i in range(100)]
        view=context_view(p,self.access.snapshot(self.state,1),'ui',3000)
        self.assertTrue(any(r['id']=='skill' for r in view['knowledge']['records']));self.assertLess(view['knowledge']['hot_coverage']['spatial_elements_loaded'],100)

class RetrievalWiring(unittest.TestCase):
    def test_supervisor_has_one_extra_retrieval_round_and_applies_final_plan(self):
        import threading
        from unittest.mock import patch
        from autoplayer.core import Settings,LocalLLM
        from autoplayer.nextgen.supervisor import SemanticSupervisor
        from autoplayer.nextgen.model import ForwardModel
        from test_nextgen import measured
        cfg=Settings(window_title='generic',allowed_keys=['a']);calls=[];events=[]
        def reply(image,system,prompt,schema):
            calls.append(copy.deepcopy(prompt))
            self.assertIn('success_choice',schema['required']);self.assertNotIn('success_text',schema['properties'])
            choice=next(c for c in prompt['success_choices'] if c['test']=='state_changes')
            return json.dumps({'local_objective':'現在画面で変化を確認','reason':'現在の根拠に従う','preferred':['A00'],'discouraged':[],'success_choice':choice['id'],'review_after':16,'information_requests':[{'topic':'full_ocr','target_id':''}]})
        with tempfile.TemporaryDirectory() as folder,patch.object(LocalLLM,'_request',side_effect=reply):
            store=ExperienceStore(Path(folder)/'world.sqlite','p','s');model=ForwardModel(store);s=SemanticSupervisor(cfg,LocalLLM(cfg),events.append,threading.Event())
            try:
                state=measured();actions=model.candidates(cfg);s.tick(state,actions,0,0,{},None,model,{})
                packet=s.results.get(timeout=2);self.assertIsNone(packet['error']);self.assertEqual(len(calls),2)
                self.assertIn('information_results',calls[1]);self.assertTrue(any(x['topic']=='full_ocr' for x in calls[1]['knowledge']['records']))
                s.results.put(packet);_,adopted=s.tick(state,actions,1,0,{},None,model,{})
                self.assertIsNotNone(adopted);self.assertTrue(adopted['plan']['candidate_values']);self.assertTrue(any(e['event']=='knowledge_retrieval' for e in events))
            finally:s.close();store.close()
    def test_fresh_observation_request_is_not_a_saved_ocr_lookup(self):
        from autoplayer.nextgen.brief_protocol import decode
        from autoplayer.nextgen.supervisor import validate
        from test_nextgen import measured
        r={'local_objective':'追加観測','reason':'不足を確認','preferred':[],'discouraged':[],'success_test':'none','success_text':'','review_after':16,'observation_request':'fresh_full_ocr','information_requests':[{'topic':'full_ocr','target_id':''}]}
        plan,issues=decode(r,{'A00':'input'},[],measured(),validate)
        self.assertEqual(plan['observation_request'],'fresh_full_ocr');self.assertEqual(plan['information_requests'][0]['topic'],'full_ocr')

class ClaimPreservation(unittest.TestCase):
    def test_repeated_import_cannot_erase_dispute_and_different_values_survive(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ExperienceStore(Path(folder)/'world.sqlite','p','s');access=KnowledgeAccess(store)
            try:
                a=claim('x','effect',1,'EXTERNAL','DISPUTED',counterevidence=[{'frame':'bad'}]);b=claim('x','effect',1,'EXTERNAL');c=claim('x','effect',2,'EXTERNAL')
                access.put(a);access.put(b);access.put(c);store.queue.join();store.check()
                rows=store.load('knowledge_claims');self.assertEqual(len(rows),2)
                disputed=next(r for r in rows if r['value']==1);self.assertEqual(disputed['status'],'DISPUTED');self.assertEqual(disputed['counterevidence'],[{'frame':'bad'}])
            finally:store.close()
    def test_fresh_flag_bypasses_both_state_and_ocr_cache_once(self):
        import hashlib
        from unittest.mock import Mock
        from autoplayer.core import Settings
        from autoplayer.nextgen.state import StateBuilder
        from test_nextgen import FIXTURES
        png=(FIXTURES/'quick-quiet-frame.png').read_bytes();sha=hashlib.sha256(png).hexdigest();ocr=Mock();ocr.read.return_value={'status':'ok','frame_sha256':sha,'lines':[]}
        fresh=[]
        result=ocr.read.return_value
        def read(*args,**kwargs):fresh.append(bool(ocr.force_fresh));return result
        ocr.read.side_effect=read
        b=StateBuilder(Settings(window_title='generic'),ocr);b.build(png,True);n=ocr.read.call_count;b.build(png,True);self.assertEqual(ocr.read.call_count,n)
        b.force_fresh_once=True;b.build(png,True);self.assertGreater(ocr.read.call_count,n);self.assertTrue(fresh[-1]);self.assertFalse(ocr.force_fresh);n=ocr.read.call_count;b.build(png,True);self.assertEqual(ocr.read.call_count,n)
    def test_stored_rules_and_ui_history_expose_experience_not_only_ids(self):
        from autoplayer.nextgen.knowledge_access import project
        rule=KnowledgeAccess.entry('rules',{'key':'r','context':'c','action':'input','samples':[{'effects':{'state_changed':1},'confidence':.4}],'checks':[0,1],'status':'under_review'})
        out=project(rule)['data'];self.assertEqual(out['recent_samples'][0]['effects']['state_changed'],1);self.assertEqual(out['prediction_checks'],[0,1])
        ui=project(KnowledgeAccess.entry('ui_history',{'key':'u','relations':{'left':{'support':2,'counterexamples':1}},'edges':[{'origin':'a','destination':'b'}]}))
        self.assertEqual(ui['data']['relations']['left']['counterexamples'],1);self.assertEqual(ui['data']['edges'][0]['destination'],'b')

class TaskBudgetContracts(unittest.TestCase):
    def snapshot(self):
        topics=['progress_node','recent_failures','observer_history','hypotheses']
        pool={t:[] for t in topics}
        return {'pool':pool,'available':{t:{'status':'available_not_loaded','count':1} for t in topics}}
    def prompt(self):
        return {'task':'world_model_supervision','goal':'現在の目標','candidates':[{'id':'A'+str(i),'keys':['a']} for i in range(20)],'current':{'interface_hypothesis':True,'quotes':['候補A','候補B'],'spatial_ui':{'selection':{'status':'unknown'},'elements':[{'id':str(i),'text':'候補'+str(i),'bbox':[.1,.2,.3,.4]} for i in range(80)]}},'goal_hierarchy':{'current_goal':{'label':'現在の目標'}}}
    def test_progress_evidence_and_intent_are_loaded_before_large_visual_detail(self):
        snap=self.snapshot();node={'id':'G1','label':'次段階','conditions':[{'feature':'text','operator':'contains','value':'証拠'}],'counterconditions':[{'feature':'text','operator':'contains','value':'反証'}],'status':'disputed','evidence':[{'frame':str(i),'conditions':[{'value':'証拠'}]} for i in range(30)],'counterevidence':[{'frame':str(i),'conditions':[{'value':'反証'}]} for i in range(30)]}
        snap['pool']['progress_node']=[KnowledgeAccess.entry('progress_node',node)]
        p=self.prompt();p['goal_hierarchy']['planning_material']={'long_term_intent':'長期の目的','new_node_ids':['G2'],'existing_node_ids':['G1']}
        v=context_view(p,snap,'progress',3076)
        self.assertEqual(v['goal_hierarchy']['planning_material']['long_term_intent'],'長期の目的');self.assertTrue(any(r['topic']=='progress_node' for r in v['knowledge']['records']))
        record=next(r for r in v['knowledge']['records'] if r['topic']=='progress_node');self.assertTrue(record['data']['counterevidence']);self.assertEqual(record['data']['counterconditions'],node['counterconditions']);self.assertLessEqual(estimate_tokens(v),3076)
    def test_recovery_gets_both_failure_and_observer_evidence_and_spatial_ui(self):
        snap=self.snapshot();snap['pool']['recent_failures']=[KnowledgeAccess.entry('recent_failures',{'id':'M','attempted':{str(i):'large'*100 for i in range(100)},'recovery':{'failure':'ui_observation_missing','method':'observe','resume':{'id':'goal','conditions':['big']*100}},'history':[{'event':'recovery_finished','status':'expired'}]})]
        snap['pool']['observer_history']=[KnowledgeAccess.entry('observer_history',{'id':'O','policy':{'full_ocr_period':1},'history':[{'status':'rollback','baseline':{'quality':.8},'measured':{'quality':.4}}]})]
        p=self.prompt();p['planning_feedback']={'last_protocol_error':'選択目標の原文が空です'};v=context_view(p,snap,'recovery',3076)
        topics={r['topic'] for r in v['knowledge']['records']};self.assertTrue({'recent_failures','observer_history'}<=topics)
        self.assertTrue(v['current']['spatial_ui']['elements']);self.assertEqual(v['goal_hierarchy']['last_protocol_error'],'選択目標の原文が空です')
        obs=next(r for r in v['knowledge']['records'] if r['topic']=='observer_history');self.assertEqual(obs['data']['history'][0]['measured']['quality'],.4)
    def test_equivalent_blocked_goals_share_conditions_without_losing_ids(self):
        p=self.prompt();n={'id':'G1','conditions':[{'feature':'text','operator':'contains','value':'a'}],'counterconditions':[],'counterevidence':[{'frame':'f','conditions':[{'value':'a'}]}]}
        p['goal_hierarchy']['blocked_progress']=[n,{**n,'id':'G2'}];v=context_view(p,self.snapshot(),'progress',3076)
        self.assertEqual(len(v['goal_hierarchy']['blocked_progress']),1);self.assertEqual(v['goal_hierarchy']['blocked_progress'][0]['equivalent_ids'],['G1','G2'])
    def test_ui_replanning_does_not_lose_progress_to_duplicate_blocked_goals(self):
        snap=self.snapshot();node={'id':'G1','label':'証拠を得る','conditions':[{'feature':'text','operator':'contains','value':'項目'}],'counterconditions':[{'feature':'text','operator':'contains','value':'反証'}],'counterevidence':[{'frame':'f','conditions':[{'value':'反証'}]}]}
        snap['pool']['progress_node']=[KnowledgeAccess.entry('progress_node',node)]
        p=self.prompt();p['goal_hierarchy']['planning_material']={'long_term_intent':'最終目的を保ちつつ現状を理解する','new_node_ids':['G2']}
        p['goal_hierarchy']['blocked_progress']=[{**node,'id':str(i)} for i in range(10)]
        v=context_view(p,snap,'ui',2500);self.assertTrue(any(r['topic']=='progress_node' for r in v['knowledge']['records']));self.assertLessEqual(estimate_tokens(v),2500)

class SupervisorRepairContracts(unittest.TestCase):
    def test_invalid_selection_is_repaired_once_without_adopting_bad_target(self):
        import threading
        from unittest.mock import patch
        from autoplayer.core import Settings,LocalLLM
        from autoplayer.nextgen.supervisor import SemanticSupervisor
        from autoplayer.nextgen.model import ForwardModel
        from test_nextgen import measured
        cfg=Settings(window_title='generic',allowed_keys=['a']);calls=[];events=[]
        bad={'local_objective':'確認','reason':'観測する','preferred':['A00'],'discouraged':[],'success_test':'selection_matches','success_text':'inspect','review_after':16}
        good={**bad,'success_test':'none','success_text':''}
        def reply(image,system,prompt,schema):
            calls.append(prompt)
            return json.dumps(bad if len(calls)==1 else good)
        with tempfile.TemporaryDirectory() as folder,patch.object(LocalLLM,'_request',side_effect=reply):
            store=ExperienceStore(Path(folder)/'w.sqlite','p','s');model=ForwardModel(store);supervisor=SemanticSupervisor(cfg,LocalLLM(cfg),events.append,threading.Event())
            try:
                supervisor.tick(measured(),model.candidates(cfg),0,0,{},None,model,{})
                packet=supervisor.results.get(timeout=2);self.assertIsNone(packet['error']);self.assertEqual(len(calls),2);self.assertIn('validation_feedback',calls[1]);self.assertEqual(packet['plan']['targets'],[])
                self.assertTrue(any(e['event']=='supervisor_repair' for e in events))
            finally:supervisor.close();store.close()
    def test_bootstrap_goal_labels_do_not_use_internal_enum_as_screen_target(self):
        from autoplayer.nextgen.meta import MetaController
        from test_nextgen import measured
        with tempfile.TemporaryDirectory() as folder:
            store=ExperienceStore(Path(folder)/'w.sqlite','p','s')
            try:
                meta=MetaController(store,'長期目的');meta.prepare(measured(),0);context=meta.planning_context(measured(),0)
                self.assertNotIn('inspect',context['current_goal']['label']);self.assertEqual(context['current_goal']['capability'],'inspect');self.assertIn('内部識別子',context['current_goal']['identifier_rule'])
            finally:store.close()
