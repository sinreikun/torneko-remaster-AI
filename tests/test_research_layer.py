"""Research mailbox / evidence / synchronization contracts; no game simulator."""
import copy,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from autoplayer.core import Settings,LocalLLM
from autoplayer.nextgen.research import ResearchLayer,validate_research
from autoplayer.nextgen.meta import MetaController
from autoplayer.nextgen.store import ExperienceStore
from autoplayer.nextgen.state import StateBuilder
from test_nextgen import measured


def empty():return {'abstractions':[],'contradictions':[],'progress_nodes':[],'research_tasks':[],'applicability':[]}

class ResearchContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=ExperienceStore(Path(self.tmp.name)/'world.sqlite','p','s');self.cfg=Settings(window_title='generic',allowed_keys=['a'])
        self.meta=MetaController(self.store,'unknown game');self.state=measured();self.meta.prepare(self.state,0);self.rows=[]
        self.r=ResearchLayer(self.cfg,SimpleNamespace(),self.rows.append,threading.Event(),self.store)
    def tearDown(self):self.r.close();self.store.close();self.tmp.cleanup()
    def packet(self,answer):
        return {'answer':answer,'cancelled':False,'step':0,'frame':'original','context':self.state['context'],'domain':self.r.domain(self.state,0,{}),'goal_epoch':self.meta.goal_epoch,'mission':None,'evidence':{'E00':{'frame':'original','step':0}},'ms':1}
    def test_schema_result_cannot_smuggle_actions_or_uncited_assertions(self):
        with self.assertRaises(ValueError):validate_research({**empty(),'candidate_values':[]},{'E00':{}},[])
        bad=empty();bad['abstractions']=[{'subject':'object','statement':'meaning','evidence':['unknown']}]
        with self.assertRaises(ValueError):validate_research(bad,{'E00':{}},[])
    def test_contradiction_needs_two_distinct_evidence_references(self):
        answer=empty();answer['contradictions']=[{'subject':'rule','statement':'possible conflict','evidence':['E00','E00']}]
        with self.assertRaises(ValueError):validate_research(answer,{'E00':{},'E01':{}},[])
        answer['contradictions'][0]['evidence']=['E00','E01']
        self.assertEqual(len(validate_research(answer,{'E00':{},'E01':{}},[])['contradictions']),1)

    def test_scene_stale_reasoning_is_stored_as_inferred_but_not_executed(self):
        answer=empty();answer['abstractions']=[{'subject':'object','statement':'possible effect','evidence':['E00']}]
        answer['research_tasks']=[{'label':'check effect','capability':'confirm','conditions':[{'feature':'text','operator':'contains','value':'new evidence'}],'deadline_steps':8,'evidence':['E00']}]
        p=self.packet(answer);other=copy.deepcopy(self.state);other['context']='other';previous=copy.deepcopy(self.meta.local)
        self.r._apply(p,other,5,0,{},self.meta)
        self.assertEqual(self.meta.local,previous)
        self.assertFalse(self.rows[-1]['current_applicability']);self.assertEqual(self.rows[-1]['abstractions'],1)
        self.store.close();self.store=ExperienceStore(self.store.path,'p','s')
        facts=self.store.load('knowledge_claims');self.assertEqual(facts[0]['source'],'INFERRED');self.assertEqual(facts[0]['status'],'UNCONFIRMED');self.assertEqual(facts[0]['evidence'][0]['frame'],'original')
    def test_applicable_research_task_has_source_and_bounded_deadline(self):
        answer=empty();answer['research_tasks']=[{'label':'measure effect','capability':'confirm','conditions':[{'feature':'text','operator':'contains','value':'new evidence'}],'deadline_steps':8,'evidence':['E00']}]
        self.r._apply(self.packet(answer),self.state,5,0,{},self.meta)
        local=self.meta.pending_local or self.meta.local
        self.assertEqual(local['label'],'measure effect');self.assertLessEqual(local['deadline'],13);self.assertEqual(local['research_source']['frame'],'original')
    def test_valid_research_progress_replaces_legacy_planning_missing_state(self):
        from test_autonomy import node
        self.meta.planning_missing=5;self.meta.planning_issue='old short supervisor could not plan'
        answer=empty();answer['progress_nodes']=[node('R0_0')]
        self.r._apply(self.packet(answer),self.state,1,0,{},self.meta)
        self.assertEqual(self.meta.planning_missing,0);self.assertIsNone(self.meta.planning_issue)
        self.assertEqual(self.meta.progress.nodes['R0_0']['status'],'unconfirmed')
        self.assertEqual(self.meta.progress.nodes['R0_0']['research_source']['frame'],'original')

    def test_new_counterevidence_prevents_applying_a_stale_research_task(self):
        answer=empty();answer['research_tasks']=[{'label':'old theory','capability':'inspect','conditions':[{'feature':'text','operator':'contains','value':'next'}],'deadline_steps':8,'evidence':['E01']}]
        packet=self.packet(answer);packet['evidence']['E01']={'record_id':'r','topic':'rules','revisions':1,'record_snapshot':{'old':'evidence'}}
        old=copy.deepcopy(self.meta.local)
        self.r._apply(packet,self.state,3,0,{},self.meta,SimpleNamespace(rules={'r':{'revisions':2}}))
        self.assertEqual(self.meta.local,old);self.assertFalse(self.rows[-1]['current_applicability']);self.assertTrue(self.rows[-1]['new_counterevidence'])

    def test_unrelated_rule_revision_does_not_cancel_a_grounded_task(self):
        answer=empty();answer['research_tasks']=[{'label':'current observation','capability':'inspect','conditions':[{'feature':'text','operator':'contains','value':'next'}],'deadline_steps':8,'evidence':['E00']}]
        packet=self.packet(answer);packet['evidence']['E01']={'record_id':'unrelated','topic':'rules','revisions':0}
        self.r._apply(packet,self.state,3,0,{},self.meta,SimpleNamespace(rules={'unrelated':{'revisions':1}}))
        self.assertTrue(self.rows[-1]['current_applicability']);self.assertFalse(self.rows[-1]['new_counterevidence'])
        self.assertIn('finite_research_task',self.rows[-1]['adopted'])

    def test_invalid_progress_does_not_discard_independently_valid_research(self):
        from test_autonomy import node
        answer=empty();answer['abstractions']=[{'subject':'observation','statement':'tentative meaning','evidence':['E00']}]
        answer['progress_nodes']=[node('a',['b']),node('b',['a'])]
        validated=validate_research(answer,{'E00':{}},[])
        self.assertEqual(validated['progress_nodes'],[]);self.assertTrue(validated['proposal_issues'])
        self.assertEqual(len(validated['abstractions']),1)
        self.r._apply(self.packet(validated),self.state,3,0,{},self.meta)
        self.assertEqual(self.rows[-1]['abstractions'],1);self.assertNotIn('progress_proposal',self.rows[-1]['adopted'])
        self.assertEqual(self.r.errors,1);self.assertTrue(self.r.recent[-1]['proposal_issues'])

    def test_research_task_cannot_wait_for_its_unobserved_internal_label(self):
        answer=empty();answer['research_tasks']=[{'label':'箱の取得','capability':'inspect','conditions':[{'feature':'text','operator':'contains','value':'箱の取得'}],'deadline_steps':8,'evidence':['E00']}]
        old=copy.deepcopy(self.meta.local);self.r._apply(self.packet(answer),self.state,1,0,{},self.meta)
        self.assertEqual(self.meta.local,old);self.assertTrue(self.rows[-1]['rejected_tasks'])
        self.assertNotIn('finite_research_task',self.rows[-1]['adopted'])

    def test_changed_guidance_discards_even_historical_proposals(self):
        self.r._apply(self.packet(empty()),self.state,5,0,{'revision':1},self.meta)
        self.assertEqual(self.r.discarded,1)
    def test_slow_llm_and_retrieval_never_block_the_field_tick(self):
        entered=threading.Event();release=threading.Event()
        def reply(image,system,prompt,schema):
            self.assertIsNone(image);self.assertNotIn('candidates',prompt);self.assertNotIn('preferred',schema['properties'])
            entered.set();release.wait(2);return json.dumps(empty())
        model=SimpleNamespace(checks=[])
        with patch.object(LocalLLM,'_request',side_effect=reply):
            try:
                self.r.tick(self.state,[],0,0,{},None,model,{},self.meta)
                self.assertTrue(entered.wait(2));self.assertIsNotNone(self.r.pending)
                plan,_=self.r.tick(self.state,[],1,0,{},None,model,{},self.meta)
                self.assertEqual(plan['candidate_values'],[]);self.assertFalse(self.r.context(1)['field_waits_for_llm'])
            finally:release.set()
            packet=self.r.results.get(timeout=2);self.assertIsNone(packet['error'])
    def test_cold_lookup_is_worker_owned_and_has_only_one_extra_round(self):
        from autoplayer.nextgen.knowledge_access import claim
        record=claim('screen','ocr',['older observation'],'OBSERVED',evidence=[{'frame':'old'}])
        self.store.put('knowledge_claims',record,key=record['id']);self.store.close()
        self.store=ExperienceStore(self.store.path,'p','s');self.r.store=self.store
        calls=[]
        def reply(image,system,prompt,schema):
            calls.append(copy.deepcopy(prompt));answer=empty()
            answer['information_requests']=[{'topic':'full_ocr','target_id':record['id']}]
            return json.dumps(answer)
        with patch.object(LocalLLM,'_request',side_effect=reply):
            self.r.tick(self.state,[],0,0,{},None,SimpleNamespace(checks=[]),{},self.meta)
            packet=self.r.results.get(timeout=2)
        self.assertIsNone(packet['error']);self.assertEqual(len(calls),2)
        self.assertTrue(any(ref.get('record_id')==record['id'] for ref in packet['evidence'].values()))

    def test_active_controller_finishes_two_inputs_while_research_is_blocked(self):
        import hashlib
        from unittest.mock import Mock
        from autoplayer.nextgen.controller import NextGenController
        from autoplayer.observation import TurnObservation
        from test_nextgen import FIXTURES
        png=(FIXTURES/'quick-quiet-frame.png').read_bytes();sha=hashlib.sha256(png).hexdigest()
        cfg=Settings(window_title='generic',allowed_keys=['a'],max_steps=2,max_wait_ms=0)
        llm=LocalLLM(cfg);llm.ocr=Mock();llm.ocr.read.return_value={'status':'ok','frame_sha256':sha,'lines':[]}
        backend=Mock();backend.capture.return_value=png;backend.act.return_value={'os_submission':'accepted'}
        backend.observe_turn.return_value=TurnObservation(png,'no_change',False,0,0,0)
        entered=threading.Event();release=threading.Event();finished=threading.Event()
        def reply(*args):
            entered.set();release.wait(5);finished.set();return json.dumps(empty())
        def act(*args,**kwargs):
            self.assertTrue(entered.wait(2));return {'os_submission':'accepted'}
        backend.act.side_effect=act
        with tempfile.TemporaryDirectory() as folder,patch.object(LocalLLM,'_request',side_effect=reply):
            try:
                runner=NextGenController(cfg,backend,llm,lambda row:None,Path(folder)/'runs'/'live-contract.jsonl')
                runner.run();self.assertEqual(backend.act.call_count,2);self.assertFalse(finished.is_set())
            finally:release.set()

    def test_retrieval_latency_is_not_on_the_field_thread(self):
        entered=threading.Event();release=threading.Event();original=__import__('autoplayer.nextgen.knowledge_access',fromlist=['KnowledgeAccess']).KnowledgeAccess.snapshot
        def snapshot(reader,*args,**kwargs):
            entered.set();release.wait(2);return original(reader,*args,**kwargs)
        with patch('autoplayer.nextgen.knowledge_access.KnowledgeAccess.snapshot',side_effect=snapshot,autospec=True),patch.object(LocalLLM,'_request',return_value=json.dumps(empty())):
            try:
                self.r.tick(self.state,[],0,0,{},None,SimpleNamespace(checks=[]),{},self.meta)
                self.assertTrue(entered.wait(2))
                plan,_=self.r.tick(self.state,[],1,0,{},None,SimpleNamespace(checks=[]),{},self.meta)
                self.assertEqual(plan['candidate_values'],[])
            finally:release.set()
            self.assertIsNone(self.r.results.get(timeout=2)['error'])
    def test_unknown_current_observation_cannot_adopt_a_research_task(self):
        answer=empty();answer['research_tasks']=[{'label':'test','capability':'inspect','conditions':[{'feature':'text','operator':'contains','value':'next'}],'deadline_steps':8,'evidence':['E00']}]
        current=copy.deepcopy(self.state);current['measurement_status']='unavailable';old=copy.deepcopy(self.meta.local)
        self.r._apply(self.packet(answer),current,1,0,{},self.meta)
        self.assertEqual(self.meta.local,old);self.assertFalse(self.rows[-1]['current_applicability'])

    def test_new_research_ids_do_not_collide_with_restart_history(self):
        from test_autonomy import node
        self.meta.progress.propose([node('R0_0')])
        def reply(image,system,prompt,schema):
            key=schema['properties']['progress_nodes']['items']['properties']['id']['enum'][0]
            self.assertNotEqual(key,'R0_0');self.assertIn(key,prompt['new_node_ids'])
            answer=empty();answer['progress_nodes']=[node(key)];return json.dumps(answer)
        with patch.object(LocalLLM,'_request',side_effect=reply):
            self.r.tick(self.state,[],0,0,{},None,SimpleNamespace(checks=[]),{},self.meta)
            self.assertIsNone(self.r.results.get(timeout=2)['error'])

    def test_controller_world_and_progress_updates_overlap_before_skill_commit(self):
        import hashlib
        from unittest.mock import Mock
        from autoplayer.nextgen.controller import NextGenController
        from autoplayer.nextgen.progress import ProgressManager
        from autoplayer.nextgen.model import ForwardModel
        from autoplayer.observation import TurnObservation
        from test_nextgen import FIXTURES
        png=(FIXTURES/'quick-quiet-frame.png').read_bytes();sha=hashlib.sha256(png).hexdigest()
        cfg=Settings(window_title='generic',allowed_keys=['a'],max_steps=1,max_wait_ms=0)
        llm=LocalLLM(cfg);llm.ocr=Mock();llm.ocr.read.return_value={'status':'ok','frame_sha256':sha,'lines':[]}
        backend=Mock();backend.capture.return_value=png;backend.act.return_value={'os_submission':'accepted'}
        backend.observe_turn.return_value=TurnObservation(png,'no_change',False,0,0,0)
        progress_entered=threading.Event();world_entered=threading.Event()
        update=ProgressManager.update;observe=ForwardModel.observe
        def progression(manager,*args):
            if threading.current_thread().name.startswith('progress-evaluation'):
                progress_entered.set();self.assertTrue(world_entered.wait(2))
            return update(manager,*args)
        def world(manager,*args,**kwargs):
            world_entered.set();self.assertTrue(progress_entered.wait(2));return observe(manager,*args,**kwargs)
        local={'objective':'local','reason':'local model','targets':[],'candidate_values':[],'hypotheses':[]}
        with tempfile.TemporaryDirectory() as folder,patch.object(ResearchLayer,'tick',return_value=(local,None)),patch.object(ProgressManager,'update',progression),patch.object(ForwardModel,'observe',world):
            runner=NextGenController(cfg,backend,llm,lambda row:None,Path(folder)/'runs'/'overlap.jsonl');runner.run()
        self.assertTrue(world_entered.is_set());self.assertTrue(progress_entered.is_set())

    def test_research_history_is_bounded_and_survives_run_tail_truncation(self):
        for i in range(12):self.r.note({'event':'research_error','reason':'error'+str(i)})
        self.assertEqual(len(self.r.recent),8)
        self.store.close();self.store=ExperienceStore(self.store.path,'p','s')
        restored=ResearchLayer(self.cfg,SimpleNamespace(),lambda row:None,threading.Event(),self.store)
        try:
            self.assertEqual(len(restored.recent),8);self.assertEqual(restored.recent[-1]['reason'],'error11')
            self.assertEqual(self.store.load('autonomy','research')[0]['history'][-1]['session'],'s')
            from autoplayer.nextgen.store import diagnostic_summary
            summary=diagnostic_summary(self.store.reader)
            self.assertTrue(any(r.get('role')=='asynchronous_knowledge_research' for r in summary['autonomy']['autonomy']))
        finally:restored.close()

    def test_goal_echo_is_not_new_knowledge_but_causal_hypotheses_survive(self):
        intent='未知の行動と結果の経験を蓄積する。目標物を持ち帰るための手段を学ぶ。'
        answer=empty();answer['abstractions']=[{'subject':'label','statement':'label','evidence':['E00']},
            {'subject':'plan','statement':intent,'evidence':['E00']},
            {'subject':'operation','statement':'observed text changed after this input','evidence':['E00']}]
        validated=validate_research(answer,{'E00':{}},[],(intent,))
        self.assertEqual(len(validated['abstractions']),1);self.assertEqual(len(validated['rejected_knowledge']),2)
    def test_research_receives_action_outcomes_and_prior_learned_knowledge(self):
        from autoplayer.nextgen.knowledge_access import claim
        record=claim('rule','research_abstractions','A tentative relation with an observed outcome','INFERRED',evidence=[{'frame':'past'}])
        self.store.put('knowledge_claims',record,key=record['id'])
        unusable=claim('label','research_abstractions','label','INFERRED',evidence=[{'frame':'past'}])
        self.store.put('knowledge_claims',unusable,key=unusable['id'])
        self.store.put('transition',{'before':{'frame_sha256':'before','quotes':['first'],'metrics':{}},'input':{'keys':['a'],'hold_ms':80},
            'receipt':{'os_submission':'accepted'},'after':{'frame_sha256':'after','quotes':['second'],'metrics':{}},'result':{'status':'updated'}},1)
        self.store.queue.join();self.store.check()
        def reply(image,system,prompt,schema):
            items=list(prompt['evidence'].values())
            action=next(e['data'] for e in items if e.get('topic')=='action_experience')
            self.assertEqual(action['before']['quotes'],['first']);self.assertEqual(action['after']['quotes'],['second'])
            self.assertEqual(action['input']['keys'],['a'])
            self.assertTrue(any(e.get('topic')=='research_knowledge' and e['data']['value']==record['value'] for e in items))
            self.assertFalse(any(e.get('topic')=='research_knowledge' and e['data']['value']=='label' for e in items))
            self.assertTrue(any(e['id']==unusable['id'] for e in prompt['knowledge']['excluded_research']))
            return json.dumps(empty())
        with patch.object(LocalLLM,'_request',side_effect=reply):
            self.r.tick(self.state,[],0,0,{},None,SimpleNamespace(checks=[]),{},self.meta)
            self.assertIsNone(self.r.results.get(timeout=2)['error'])

    def test_parallel_geometry_overlaps_ocr_and_checks_frame_barrier(self):
        from PIL import Image
        import io,hashlib
        image=Image.new('RGB',(64,64),'black');out=io.BytesIO();image.save(out,format='PNG');png=out.getvalue();sha=hashlib.sha256(png).hexdigest()
        entered=threading.Event();release=threading.Event()
        from autoplayer.nextgen.state import frame_geometry
        def geometry(*args):entered.set();release.wait(2);return frame_geometry(*args)
        class Reader:
            def read(self,frame):
                if not entered.wait(2):raise AssertionError('geometry did not overlap OCR')
                release.set();return {'status':'ok','frame_sha256':sha,'lines':[]}
        b=StateBuilder(self.cfg,Reader())
        try:
            with patch('autoplayer.nextgen.state.frame_geometry',side_effect=geometry):a=b.build(png)
            self.assertEqual(a['frame_sha256'],sha);self.assertTrue(a['timing']['parallel_frame_geometry'])
        finally:release.set();b.close()

class ExperiencePrefetchContracts(unittest.TestCase):
    def test_prefetch_is_profile_scoped_and_never_overwrites_newer_model_rules(self):
        from collections import OrderedDict
        from autoplayer.nextgen.field_pipeline import RulePrefetch
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'資料 # rules.sqlite';store=ExperienceStore(path,'p','s');state=measured();actions=[{'id':'a'},{'id':'b'}]
            keys=RulePrefetch.keys(state,actions);store.put('rules',{'key':keys[0],'marker':'old'},key=keys[0]);store.close()
            other=ExperienceStore(path,'other','s');other.put('rules',{'key':keys[1],'marker':'other-profile'},key=keys[1]);other.close()
            reader=RulePrefetch(path,'p');model=SimpleNamespace(rules=OrderedDict({keys[0]:{'marker':'new-owner'}}))
            try:
                reader.begin(state,actions);result=reader.merge(state,actions,model)
                self.assertEqual(result['status'],'merged');self.assertEqual(model.rules[keys[0]]['marker'],'new-owner')
                self.assertNotIn(keys[1],model.rules);self.assertIn(keys[1],model.prefetched_missing)
            finally:reader.close()
    def test_progress_lane_has_exclusive_ownership_and_source_checked_commit(self):
        from autoplayer.nextgen.field_pipeline import ProgressLane
        entered=threading.Event();release=threading.Event()
        class Manager:
            def update(self,state,step):entered.set();release.wait(2);return [{'id':'g','status':'supported'}]
        lane=ProgressLane();s=measured()
        try:
            lane.begin(Manager(),s,1);self.assertTrue(entered.wait(2))
            with self.assertRaises(RuntimeError):lane.begin(Manager(),s,2)
            # Simultaneous world-model work can proceed before the join.
            release.set();changes=lane.merge(s['frame_sha256'],1);self.assertEqual(changes[0]['status'],'supported')
            lane.begin(Manager(),s,2)
            with self.assertRaisesRegex(ValueError,'source mismatch'):lane.merge('wrong frame',2)
        finally:release.set();lane.close()

    def test_geometry_from_a_different_frame_cannot_cross_the_barrier(self):
        import io
        from PIL import Image
        from unittest.mock import Mock
        from autoplayer.nextgen.state import frame_geometry
        out=io.BytesIO();Image.new('RGB',(32,32)).save(out,format='PNG');png=out.getvalue()
        reader=Mock();reader.read.return_value={'status':'ok','lines':[]}
        b=StateBuilder(Settings(window_title='generic'),reader)
        value=frame_geometry(png,False);value['sha']='wrong'
        try:
            with patch('autoplayer.nextgen.state.frame_geometry',return_value=value),self.assertRaisesRegex(ValueError,'source mismatch'):b.build(png)
        finally:b.close()
