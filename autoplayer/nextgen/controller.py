"""The UI's sole autonomous execution path; shell protocols remain unchanged."""
import copy,hashlib,io,json,os,threading,time
from collections import deque
from pathlib import Path
from PIL import Image,ImageChops,ImageStat
from .state import StateBuilder,digest,effects
from .store import ExperienceStore
from .model import ForwardModel,AdaptivePolicy,snapshot,target_score
from .research import ResearchLayer
from .field_pipeline import RulePrefetch,ProgressLane
from .meta import MetaController
from .pace import PaceController
from .spatial import SpatialLane,trace as spatial_trace
from .execution import ProcedureExecutor
from .dungeon_observer import DungeonObserver
from .interaction_surface import allows_field_input
from .short_planning import ShortPlanning
from ..controls import is_torneko
from ..cancellation import OperationCancelled
from ..observation_store import message_candidates
from ..map_sensor import read_grid


def load_policies(title):
    # Distributed JSON is UTF-8 regardless of the user's Windows ANSI code page.
    if not is_torneko(title):return {},{}
    root=Path(__file__).resolve().parents[2]/'knowledge'
    return tuple(json.loads((root/name).read_text(encoding='utf-8')) for name in ('torneko-spatial-policy.json','torneko-execution-policy.json'))

class NextGenController:
    def __init__(self,cfg,backend,llm,emit,journal,stop=None,memory=None,control=None,diagnostic_sink=None):
        self.cfg=cfg.validate();self.backend=backend;self.llm=llm;self.emit=emit;self.journal=Path(journal);self.control=control;self.stop=stop if stop is not None else threading.Event()
        self.cancel=control or self.stop;self.llm.activity=emit;self.llm.ocr.cancel=self.cancel
        self.builder=StateBuilder(cfg,llm.ocr);self.diagnostic_sink=diagnostic_sink;self.last={};self.pace=PaceController();self.durations={};self.events=[];self.previous=None;self.stream=None
        self.data=self.journal.parent.parent;self.completed=False;self.summary={};self.last_pace_tick=time.monotonic();self.journal_lock=threading.RLock()
    def checkpoint(self):
        if self.control and getattr(self.control,'reconnect_requested',None) and self.control.reconnect_requested.is_set():
            from ..recovery import TargetUnavailable
            raise TargetUnavailable('接続更新要求。保留中の操作を破棄して再接続します。')
        if self.control and self.control.intervention.is_set():raise OperationCancelled('手動介入へ切替')
        return self.control.checkpoint() if self.control else not self.stop.is_set()
    def log(self,value,durable=False):
        with self.journal_lock:
            if self.stream is None or self.stream.closed:return
            self.stream.write(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n');self.stream.flush()
            if durable:os.fsync(self.stream.fileno())
    def send_event(self,row):self.log(row);self.emit(row)
    @staticmethod
    def difference(a,b):
        with Image.open(io.BytesIO(a)) as x,Image.open(io.BytesIO(b)) as y:
            return ImageStat.Stat(ImageChops.difference(x.convert('RGB').resize((32,24)),y.convert('RGB').resize((32,24)))).mean[0]/255
    def sense(self,png,full=True,step=0,base_state=None):
        state=base_state if base_state is not None and base_state.get('frame_sha256')==hashlib.sha256(png).hexdigest() else self.builder.build(png,full)
        if is_torneko(self.cfg.window_title):state['input_surface_observation']=DungeonObserver.surface(state,self.builder.cache.get(state['frame_sha256'],{}).get('native',{}))
        # This adapter only measures the user's map legend, never chooses actions.
        floor=state['floor_confirmation'];valid_floor=floor.get('status') in ('initialized','confirmed','changed') and floor.get('confirmed') is not None
        can_map=is_torneko(self.cfg.window_title) and 'num7' in self.cfg.allowed_keys and valid_floor and allows_field_input(state) and state.get('minimap',{}).get('status')!='observed'
        if can_map and base_state is not None and self.previous and self.previous.get('minimap',{}).get('status')=='observed' and floor.get('confirmed')==self.previous.get('floor_confirmation',{}).get('confirmed') and getattr(self,'previous_image',None) and self.difference(png,self.previous_image)<.01:
            retained=copy.deepcopy(self.previous['minimap']);retained['status']='retained';retained['retained_from_frame']=self.previous['frame_sha256']
            state['minimap']=retained;state['map_domain']=self.previous.get('map_domain');can_map=False
        if can_map and hasattr(self.backend,'capture_map_display') and not self.cancel.is_set():
            started=time.monotonic();sample=self.backend.capture_map_display(self.cancel,baseline=False)
            if sample and sample.get('image'):
                measured=read_grid(sample['image'])
                if measured.get('status')=='observed':
                    measured=copy.deepcopy(measured);measured['source_frame_sha256']=hashlib.sha256(sample['image']).hexdigest();measured['observation_role']='held_map_display_measurement'
                    state=self.builder.build(png,full,measured_map=measured)
            state['timing']['map_capture_ms']=(time.monotonic()-started)*1000
        if hasattr(self.backend,'record_frame'):self.backend.record_frame(png,'observation' if step==0 else 'result')
        if is_torneko(self.cfg.window_title):state['input_surface_observation']=DungeonObserver.surface(state,self.builder.cache.get(state['frame_sha256'],{}).get('native',{}))
        return state
    def wait_time(self,state,protect):
        context=state['context'];samples=self.durations.get(context,[])
        base=350 if protect else 140
        if samples:base=max(base,min(1500,int(sorted(samples)[int((len(samples)-1)*.9)]*1.15+70)))
        key=(context,tuple(getattr(self,'pace_keys',())),bool(protect))
        return min(self.cfg.max_wait_ms,self.pace.wait(key,base)+getattr(self,'observation_policy',{}).get('settle_extra_ms',0))
    def run(self,dry_run=False):
        spatial_policy,execution_policy=load_policies(self.cfg.window_title)
        profile=digest([self.cfg.profile_id or self.cfg.window_title,sorted(self.cfg.allowed_keys),self.cfg.key_bindings])
        store=ExperienceStore(self.data/'world-v2.sqlite',profile,self.journal.stem);model=ForwardModel(store);adaptive=AdaptivePolicy();meta=MetaController(store,self.cfg.goal,self.cfg.knowledge)
        self.observation_policy=meta.observer.policy;self.builder.observation_policy=self.observation_policy
        prefetch=RulePrefetch(store.path,profile);progress_lane=ProgressLane()
        spatial_saved=store.load('autonomy','spatial_model');spatial_lane=SpatialLane(spatial_saved[0].get('model') if spatial_saved else None)
        executor=ProcedureExecutor(store,{k:v for k,v in self.cfg.key_bindings.items() if k in self.cfg.allowed_keys},execution_policy)
        descriptions=store.load('autonomy','inventory_descriptions')
        dungeon_observer=DungeonObserver(store.session+':0',descriptions[0].get('records',[]) if descriptions else []) if execution_policy else None
        short_planner=ShortPlanning(store,executor,spatial_policy,self.data/'planning'/f'{store.session}.jsonl') if dungeon_observer else None
        previous_boundary_tracking=getattr(getattr(self.backend,'observer',None),'track_scene_boundaries',False)
        if hasattr(self.backend,'configure_scene_boundaries'):self.backend.configure_scene_boundaries(True)
        supervisor=ResearchLayer(self.cfg,self.llm,self.send_event,self.cancel,store);candidates=model.candidates(self.cfg,execution_policy.get('roles'))
        self.builder.ui_vision.restore(store.load('ui_models',limit=64))
        self.builder.ui_interactions.restore(store.load('ui_interactions',limit=64))
        for appearance in store.load('appearances',limit=128):self.builder.tracker.variants[appearance['id']]=appearance['variants']
        self.builder.restore_candidates=store.load('states')
        for saved in self.builder.restore_candidates:
            if saved.get('visual_key') and saved.get('visual_vector'):self.builder.prototypes[saved['visual_key']]=saved['visual_vector']
        self.journal.parent.mkdir(parents=True,exist_ok=True);step=0;run_started=time.monotonic();llm_turns=0;cycle_count=0;accuracy=deque(maxlen=128);last_revision=None;local_steps=0
        self.stream=self.journal.open('a',encoding='utf-8')
        if hasattr(self.backend,'configure_fast_learning'):self.backend.configure_fast_learning(True)
        try:
            self.send_event({'event':'nextgen_start','generation':2,'profile':profile,'legacy_learning_imported':False,'learning':False})
            while self.checkpoint() and (not self.cfg.max_steps or step<self.cfg.max_steps):
                try:
                    started=time.monotonic();stages={};store.check();revision=getattr(self.control,'revision',0)
                    if last_revision is not None and revision!=last_revision:
                        model.pending.clear();self.previous=None;executor.interrupt('control_revision');executor.pages={};executor.review_needed=True
                    last_revision=revision
                    source=getattr(self.llm,'operator_guidance',None);guidance=source.snapshot() if source else {'revision':0,'text':''}
                    if self.previous:prefetch.begin(self.previous,candidates)
                    t=time.monotonic();image=self.backend.capture();stages['capture_ms']=(time.monotonic()-t)*1000
                    self.observation_policy=meta.observer.effective_policy(self.previous);self.builder.observation_policy=self.observation_policy
                    full=bool(executor.active or executor.review_needed and execution_policy or getattr(meta,'fresh_observation_requested',False) or step==0 or step%self.observation_policy['full_ocr_period']==0 or (self.last.get('surprise') or 0)>2 or self.last.get('loop') or self.previous and self.previous['interface_hypothesis'])
                    if getattr(meta,'fresh_observation_requested',False):
                        self.builder.force_fresh_once=True;meta.fresh_observation_requested=False
                    for _ in range(self.observation_policy['extra_captures']):
                        if self.cancel.wait(.08):break
                        image=self.backend.capture()
                    t=time.monotonic();before=self.builder.build(image,full);stages.update(before['timing']);stages['observation_ms']=(time.monotonic()-t)*1000
                    neutral=None;neutral_regions=None;natural_confidence=.4
                    if hasattr(self.backend,'observe_turn') and (step%8==0 or before['quality']<.5):
                        t=time.monotonic();sample=self.backend.observe_turn(image,'neutral_observation',0,self.cancel)
                        if self.cancel.is_set():
                            if not self.checkpoint():break
                            continue
                        after_neutral=self.builder.build(sample.image,full)
                        neutral=effects(before,after_neutral)
                        neutral_regions=([c['bbox'] for c in after_neutral.get('ui_visual',{}).get('candidates',[]) if c.get('status')=='visual_change_candidate'] if before['frame_sha256']!=after_neutral['frame_sha256'] else [])
                        if after_neutral.get('ui_visual',{}).get('change_fraction',0)>=.35:neutral_regions=[[0,0,1,1]]
                        natural_confidence=.85;before=after_neutral;image=sample.image
                        stages['neutral_observation_ms']=(time.monotonic()-t)*1000
                    sense_started=time.monotonic();before=self.sense(image,full,base_state=before)
                    stages['pre_input_sense_ms']=(time.monotonic()-sense_started)*1000
                    stages['pre_input_map_capture_ms']=before['timing'].get('map_capture_ms',0)
                    before['spatial_topology']=spatial_policy.get('grid_topology',4)
                    spatial_lane.begin(before,step,revision,png=image)
                    prefetch.begin(before,candidates)
                    if step==0:
                        from .knowledge_access import KnowledgeAccess
                        KnowledgeAccess(store).capture(before,0,self.cfg.knowledge)
                    meta.prepare(before,step)
                    spatial_join=time.monotonic();spatial_packet=spatial_lane.merge(before['frame_sha256'],step,getattr(self.control,'revision',0))
                    before['spatial']=spatial_packet['view'];stages['spatial_before_join_ms']=(time.monotonic()-spatial_join)*1000
                    procedure=None;planning_started=False
                    if dungeon_observer:
                        view=dungeon_observer.observe(before,self.builder.cache[before['frame_sha256']]['native'],image)
                        executor.attach(before,view,step)
                        procedure=executor.service(before,candidates,step)
                        if not procedure:
                            short_planner.begin(before,candidates,step,meta.local,revision,meta.goal_epoch,meta.capabilities.summary(before));planning_started=True
                        else:short_planner.packet=None;short_planner.replan_reason='emergency_or_result_interrupt'
                        before['execution']=executor.summary(step)
                        if dungeon_observer.inventory.scope!=store.session+':'+str(executor.adventure_revision):dungeon_observer.reset(store.session+':'+str(executor.adventure_revision))
                        if before.get('execution_observation_request'):meta.fresh_observation_requested=True
                    t=time.monotonic();requests_before=supervisor.requests
                    plan,_=supervisor.tick(before,candidates,step,revision,guidance,image,model,self.last,meta)
                    stages['research_mailbox_and_snapshot_ms']=(time.monotonic()-t)*1000;stages['llm_wait_ms']=0.
                    plan=meta.guide(plan,before,candidates)
                    if planning_started:
                        selected=short_planner.merge(before,step,getattr(self.control,'revision',0),meta.goal_epoch)
                        if selected:
                            procedure=short_planner.dispatch(before,candidates,step)
                            before['planning']=short_planner.summary()
                            chosen_spec=selected['candidate']['execution']
                            if chosen_spec.get('target'):
                                before['spatial']['selected']=chosen_spec['target'];plan['spatial_target']=chosen_spec['target']
                            if not procedure:
                                self.builder.force_fresh_once=True;meta.fresh_observation_requested=True
                                short_planner.feedback(before,before,None,{'os_submission':'not_sent'},step)
                                self.send_event({'event':'short_plan_not_executed','step':step,'planning':short_planner.summary()});continue
                    if dungeon_observer:
                        before['spatial']['targets']=[t for t in before['spatial'].get('targets',[]) if not(t['kind']=='enemy' and executor.stationary.get(str(tuple(t['cell'])),{}).get('count',0)>=3) and not(t['kind']=='item' and len(t['path'])==1)]
                    if not planning_started and not procedure and not executor.active and not meta.recovery:
                        target=meta.progress.select_spatial(before['spatial'],meta.local,spatial_policy)
                        if target:
                            before['spatial']['selected']=target
                            realization=meta.capabilities.realize_spatial(before,target,candidates)
                            plan['spatial_realization']=realization;plan['spatial_target']=target
                            plan['objective']='Spatial '+target['kind']+' → '+str(target['cell'])
                            if realization['status']=='capability_missing':
                                meta.fresh_observation_requested=True
                                meta.progress.defer_spatial(target,step,'capability_missing')
                                task={'status':'capability_missing','capability':realization['capability'],'target':target,'deadline_turn':step+8,'success':'observed_effect_not_input_submission','source':'spatial_requirement'}
                                store.put('spatial_tasks',task,step,key=target['id'])
                                plan['spatial_learning_task']=task;before['spatial']['learning_task']=task
                                self.send_event({'event':'spatial_capability_missing','step':step,**task})
                    if not procedure and executor.active and before.get('execution_observation_request'):
                        self.builder.force_fresh_once=True
                        self.send_event({'event':'procedure_observation_requested','step':step,'state':executor.summary(step),'request':before['execution_observation_request']})
                        continue
                    if procedure:
                        realization=executor.realize(before,procedure,candidates)
                        if realization:
                            plan['execution_realization']=realization;plan['objective']=procedure['reason'];plan['targets']=[]
                        else:
                            executor.interrupt('unavailable_bound_input');meta.fresh_observation_requested=True
                    before['spatial']['targets']=[{k:v for k,v in t.items() if k!='path'} for t in before['spatial'].get('targets',[])]
                    retrieval=prefetch.merge(before,candidates,model);stages['experience_prefetch_join_ms']=retrieval['join_ms']
                    if retrieval['status']!='merged':self.send_event({'event':'experience_prefetch',**retrieval,'learning':False})
                    reserved=spatial_policy.get('field_reserved_meanings',[]) if not before['interface_hypothesis'] and not procedure else []
                    execution_candidates=[a for a in candidates if not any(m in reserved for m in a.get('declared_meanings',[]))] or candidates
                    t=time.monotonic();chosen,ranking,choice_ms=model.choose(before,execution_candidates,plan,adaptive.weight)
                    stages['action_selection_ms']=max(0,choice_ms-model.search_ms-model.prediction_ms);stages['experience_search_ms']=model.search_ms;stages['world_prediction_ms']=model.prediction_ms;stages['experience_search_and_prediction_ms']=(time.monotonic()-t)*1000
                    action=chosen['candidate'];prediction=chosen['prediction']
                    def brief_choice(item):
                        return {**{k:v for k,v in item.items() if k!='prediction'},'prediction':{k:v for k,v in item['prediction'].items() if k not in ('outcomes','probabilities')}}
                    shown_chosen=brief_choice(chosen);shown_ranking=[brief_choice(x) for x in ranking]
                    shown_before={k:v for k,v in before.items() if k!='visual_vector'}
                    decision={'event':'decision','step':step+1,'scene':'observed_interface_hypothesis' if before['interface_hypothesis'] else 'observed_state',
                      'subgoal':plan['objective'],'reason':f"経験 {prediction['trials']}件 / 情報獲得見込 {chosen['information_gain_proxy']:.3f} / 目標価値 {chosen['expected_goal_value']:.3f} / 観測損失リスク {chosen['observed_loss_risk']:.3f}。"+plan['reason'],'observation':'現在の画面・OCRと結果予測から単発入力を比較','observed_keys':action['keys'],'keys':action['keys'],'hold_ms':action['hold_ms'],'confidence':before['quality'],
                      'policy_state':shown_before,'action_prediction':{'expected':prediction['expected']},'execution_stage':'experience_model_selection','generation':2,'operator_guidance':guidance,'supervision':supervisor.context(step),'autonomy':meta.context(before),
                      'nextgen':{'current':{'observed':before['metrics'],'retained':before['retained'],'state_kind':before['state_identification']},'objective':plan['objective'],'hypotheses':model.hypotheses[-8:],'selected':shown_chosen,'candidates':shown_ranking}}
                    self.send_event(decision)
                    if dry_run:
                        self.cancel.wait(.15);step+=1;continue
                    if not self.checkpoint():break
                    if revision!=getattr(self.control,'revision',0) or source and source.snapshot()['revision']!=guidance['revision']:model.pending.clear();continue
                    # Revalidate after OCR/semantic scheduling. Do not execute a stale screen plan.
                    t=time.monotonic();fresh=self.backend.capture();difference=self.difference(image,fresh);stages['image_difference_ms']=(time.monotonic()-t)*1000
                    if difference>self.observation_policy['stale_difference']:
                        self.pace.invalidate_context(before['context'])
                        self.send_event(meta.nonexecution(before,step,(time.monotonic()-started)*1000))
                        self.send_event({'event':'nextgen_stale_plan','reason':'入力直前に画面が大きく変化','learning':False,'difference':difference});model.pending.clear();self.previous=None;continue
                    protect=bool(procedure or executor.active or before['interface_hypothesis'] or before['minimap'].get('enemies') or any('攻撃' in self.cfg.key_bindings.get(k,'') or 'attack' in self.cfg.key_bindings.get(k,'').casefold() for k in action['keys']))
                    self.pace_keys=tuple(sorted(action['keys']));wait=self.wait_time(before,protect)
                    request={'event':'input_intent','generation':2,'step':step+1,'action':action,'before_frame':before['frame_sha256'],'prediction':prediction,'control_revision':revision,'learning':False}
                    self.log(request,durable=True)
                    receipt={'os_submission':'not_sent'};observer=getattr(self.backend,'observer',None)
                    if observer is not None:observer.protect_text=protect;observer.capture_evidence=True
                    t=time.monotonic()
                    try:
                        receipt=self.backend.act(action['keys'],action['hold_ms'],self.cancel)
                        if not isinstance(receipt,dict):receipt={'os_submission':'unknown','reason':'backend_receipt_missing'}
                        stages['input_ms']=(time.monotonic()-t)*1000
                        self.log({'event':'input_submitted','step':step+1,'action':action,'receipt':receipt,'learning':False},durable=True)
                        t=time.monotonic()
                        if hasattr(self.backend,'observe_turn'):
                            observation=self.backend.observe_turn(image,'nextgen',wait,self.cancel);after_image=observation.image;kind=observation.kind;frames=getattr(observation,'evidence_frames',[])
                        else:
                            self.cancel.wait(wait/1000);after_image=self.backend.capture();kind='normal';frames=[]
                        stages['input_response_observation_ms']=(time.monotonic()-t)*1000
                    finally:
                        if observer is not None:observer.protect_text=False;observer.capture_evidence=False
                        self.backend.release_all()
                    if self.cancel.is_set():
                        self.log({'event':'input_censored','step':step+1,'receipt':receipt,'reason':'interrupted_after_submission','learning':False},durable=True)
                        model.pending.clear()
                        if not self.checkpoint():break
                        continue
                    t=time.monotonic();after=self.sense(after_image,full or protect,step+1);stages['result_observation_ms']=(time.monotonic()-t)*1000
                    for name in ('ocr_ms','object_recognition_ms','map_geometry_ms','interface_geometry_ms','state_construction_ms','map_capture_ms'):stages[name]=stages.get(name,0)+after['timing'].get(name,0)
                    stages['llm_background_ms']=supervisor.last_ms
                    hp0=before['metrics'].get('hp',{}).get('values');hp1=after['metrics'].get('hp',{}).get('values');hp_loss=bool(hp0 and hp1 and hp1[0]<hp0[0])
                    from ..battle_messages import read_messages
                    combat_text=bool(read_messages(self.builder.cache[after['frame_sha256']]['native'],after['frame_sha256'])) if is_torneko(self.cfg.window_title) else False
                    if (hp_loss or combat_text) and not(full or protect):after=self.builder.build(after_image,True,measured_map=after['minimap'] if after['minimap'].get('status')=='observed' else None)
                    text_evidence=[];evidence_started=time.monotonic();evidence_reads=getattr(self.builder,'message_evidence_reads',0)
                    if protect or hp_loss or combat_text:
                        for frame in message_candidates(after_image,frames,limit=2):
                            evidence=self.builder.message_evidence(frame)
                            text_evidence.append({'frame_sha256':evidence['sha'],'native':evidence['native'],'role':'message_evidence_only_not_current_state'})
                    stages['message_evidence_ms']=(time.monotonic()-evidence_started)*1000
                    evidence_info={'retained_frames':len(text_evidence),'ocr_reads':getattr(self.builder,'message_evidence_reads',0)-evidence_reads}
                    step+=1;t=time.monotonic()
                    ui_receipt=receipt if kind!='aborted' and revision==getattr(self.control,'revision',0) else {'os_submission':'censored'}
                    ui_transition=self.builder.ui_vision.learn(before,after,action,ui_receipt,step,neutral,neutral_regions)
                    self.builder.apply_ui_selection(after)
                    if dungeon_observer:after['input_surface_observation']=DungeonObserver.surface(after,self.builder.cache[after['frame_sha256']]['native'])
                    ui_model=self.builder.ui_vision.models.get(after.get('ui_visual',{}).get('layout'))
                    if ui_model:store.put('ui_models',ui_model,step,key=ui_model['key'])
                    meta.check_boundary(after,step)
                    transition={'before':{k:copy.deepcopy(before.get(k)) for k in ('spatial','interface_hypothesis','frame_sha256','input_surface_observation')},'intent':meta.capabilities.spatial_intent(action),'accepted':ui_receipt.get('os_submission')=='accepted' and kind!='aborted','hold_ms':action['hold_ms'],'target':plan.get('spatial_target'),'boundary':getattr(observation,'scene_boundary',{}) if hasattr(self.backend,'observe_turn') else {},'images':(image,after_image) if before.get('spatial',{}).get('provider')=='hypothesis' else None,'assumption':spatial_policy.get('mapless_screen_change_assumption',False)}
                    after['spatial_topology']=spatial_policy.get('grid_topology',4)
                    spatial_lane.begin(after,step,revision,transition,after_image)
                    progress_lane.begin(meta.progress,after,step)
                    try:
                        result=model.observe(before,action,after,prediction,receipt,step,neutral,kind,natural_confidence)
                        interaction=self.builder.ui_interactions.observe(before,action,after,result,ui_receipt,step,ui_transition,neutral)
                    finally:
                        progress_join=time.monotonic();progress_changes=progress_lane.merge(after['frame_sha256'],step)
                        stages['parallel_progress_join_ms']=(time.monotonic()-progress_join)*1000
                    interaction_model=self.builder.ui_interactions.model(before)
                    if interaction_model:store.put('ui_interactions',interaction_model,step,key=interaction_model['key'])
                    self.builder.ui_vision.last=copy.deepcopy(after.get('ui_visual',{}))
                    spatial_join=time.monotonic();spatial_packet=spatial_lane.merge(after['frame_sha256'],step,getattr(self.control,'revision',0))
                    after['spatial']=spatial_packet['view'];stages['spatial_after_join_ms']=(time.monotonic()-spatial_join)*1000
                    if plan.get('spatial_learning_task'):after['spatial']['learning_task']=copy.deepcopy(plan['spatial_learning_task'])
                    selected=next((t for t in after['spatial'].get('targets',[]) if t['id']==(plan.get('spatial_target') or {}).get('id')),None)
                    if selected:after['spatial']['selected']=copy.deepcopy(selected)
                    after['spatial']['targets']=[{k:v for k,v in t.items() if k!='path'} for t in after['spatial'].get('targets',[])]
                    spatial_record=spatial_trace(before,after,plan.get('spatial_target'),action,ui_receipt)
                    meta.progress.spatial_feedback(spatial_record,step)
                    if dungeon_observer:
                        view=dungeon_observer.observe(after,self.builder.cache[after['frame_sha256']]['native'],after_image)
                        executor.attach(after,view,step)
                        battle_events=[]
                        for entry in text_evidence:battle_events.extend(read_messages(entry['native'],entry['frame_sha256']))
                        battle_events.extend(read_messages(self.builder.cache[after['frame_sha256']]['native'],after['frame_sha256']))
                        before['battle_message_observation']=read_messages(self.builder.cache[before['frame_sha256']]['native'],before['frame_sha256'])
                        executor.feedback(before,after,action,ui_receipt,step,battle_events)
                        requested_skill='handle_target' if procedure and procedure['task']=='combat' and procedure['role']=='confirm' else 'inspect_inventory' if procedure and procedure['task']=='review' else 'equip' if procedure and procedure['task']=='equip' else 'use_item' if procedure and executor.active and executor.active.get('item') else None
                        if requested_skill:meta.capabilities.observe(before,action,after,result,step,requested_skill,neutral=neutral)
                        after['execution']=executor.summary(step)
                        if short_planner and short_planner.packet:
                            audit=short_planner.feedback(before,after,procedure,ui_receipt,step,after.get('battle_observation',{}).get('events',[]))
                            self.send_event({'event':'short_plan_result','step':step,'planning':short_planner.summary(audit)})
                        if dungeon_observer.inventory.scope!=store.session+':'+str(executor.adventure_revision):dungeon_observer.reset(store.session+':'+str(executor.adventure_revision))
                        if step%8==0:store.put('autonomy',{'records':dungeon_observer.inventory.description_reads},step,key='inventory_descriptions')
                        self.send_event({'event':'procedure_update','step':step,'state':after['execution'],'input':procedure,'battle_messages':battle_events,'control_receipt':ui_receipt})
                    if step%8==0 or spatial_record['outcome']=='region_changed':store.put('autonomy',{'model':spatial_packet['saved']},step,key='spatial_model')
                    store.put('spatial_trace',spatial_record,step)
                    self.send_event({'event':'spatial_trace','step':step,**spatial_record})
                    self.last=result
                    if text_evidence:
                        from .knowledge_access import KnowledgeAccess
                        KnowledgeAccess(store).record_messages(text_evidence,step,after)
                    from .knowledge_access import KnowledgeAccess
                    KnowledgeAccess(store).capture(after,step)
                    autonomy=meta.observe(before,action,after,result,receipt,step,(time.monotonic()-started)*1000,neutral,progress_changes=progress_changes)
                    self.send_event(autonomy)
                    if result.get('match') is not None:accuracy.append(float(result['match']))
                    trial=adaptive.update(result)
                    if trial:store.put('parameter_trial',trial,step);self.send_event(trial)
                    stages['learning_update_ms']=(time.monotonic()-t)*1000
                    duration=stages['input_response_observation_ms'];samples=self.durations.setdefault(before['context'],[])
                    if kind=='normal' and hasattr(self.backend,'observe_turn') and observation.last_activity_ms is not None:
                        samples.append(observation.last_activity_ms);del samples[:-24]
                    if len(self.durations)>256:self.durations.pop(next(iter(self.durations)))
                    if supervisor.pending:llm_turns+=1
                    if supervisor.requests==requests_before:local_steps+=1
                    if result.get('loop'):
                        cycle_count+=int(result['loop']['event']=='loop_detected');self.send_event(result['loop']);supervisor.next_review=step
                    goals=[target_score(after,target) for target in plan['targets']];known_goals=[x for x in goals if x is not None]
                    metrics={'prediction_accuracy':sum(accuracy)/len(accuracy) if accuracy else None,'novelty':1/(1+model.visits[after['id']]),'uncertainty':prediction['uncertainty'],
                      'information_gain':result.get('information_gain'),'repetition_rate':sum(x['loop'] for x in model.checks)/len(model.checks) if model.checks else 0.,
                      'llm_intervention_rate':supervisor.requests/step,'decision_ms':choice_ms,'observation_ms':stages['observation_ms']+stages['result_observation_ms'],
                      'goal_progress':sum(known_goals)/len(known_goals) if known_goals else None,'tracking_success':after['tracking_success'],'position_confidence':after['position_confidence'],
                      'local_action_fraction':local_steps/step,'goal_evaluation':'observed_local_conditions_not_game_completion'}
                    pace_tick=time.monotonic();pace_ms=(pace_tick-self.last_pace_tick)*1000;self.last_pace_tick=pace_tick
                    pace_key=(before['context'],self.pace_keys,bool(protect))
                    pace_event=self.pace.observe(pace_key,350 if protect else 140,ms=pace_ms,quality=min(before['quality'],after['quality']),match=result.get('match'),kind=kind,accepted=receipt.get('os_submission')=='accepted')
                    if pace_event:self.send_event({'event':'pace_trial','step':step,'scope':list(pace_key),'result':pace_event,'evaluation':self.pace.last})
                    row={**decision,'event':'result','step':step,'autonomy':meta.context(after),'executed':receipt.get('os_submission')=='accepted','input_receipt':receipt,'action_kind':'input','turn_kind':kind,
                      'changed':before['frame_sha256']!=after['frame_sha256'],'policy_after':{k:v for k,v in after.items() if k!='visual_vector'},'policy_state':shown_before,'ocr':copy.deepcopy(self.builder.cache.get(before['frame_sha256'],{}).get('native',{'status':'unavailable','lines':[]})),
                      'result_ocr':self.builder.cache[after['frame_sha256']]['native'],'message_evidence':text_evidence,'ui_transition':ui_transition,'ui_interaction':interaction,'transition_attribution':result.get('attribution','censored'),'neutral_before':neutral,
                      'supervision':supervisor.context(step),'loop_guard':result.get('loop') or {},'forecast_evaluation':{'status':'matched' if result.get('match') is True else 'mismatch' if result.get('match') is False else 'unscored'},
                      'performance':{'total_ms':(time.monotonic()-started)*1000,'stages_ms':stages,'pace':copy.deepcopy(self.pace.last),'llm_requested':supervisor.pending is not None,'message_evidence':evidence_info,'synchronization':{'input_source_frame':before['frame_sha256'],'result_source_frame':after['frame_sha256'],'commit_order':'input_result_then_parallel_world_progress_then_skill_meta','research_blocks_input':False}},
                      'spatial':spatial_record,'nextgen':{'metrics':metrics,'prediction_before_input':{k:v for k,v in prediction.items() if k!='outcomes'},'result':result,'selected':shown_chosen,'candidates':shown_ranking,'objective':plan['objective'],'hypotheses':copy.deepcopy(model.hypotheses[-8:]),'semantic_interpretations':plan.get('interpretations',[]),'parameters':{'exploration_weight':adaptive.weight,'trial':adaptive.trial},'db':{'queued':store.queue.qsize(),'commit_ms':store.commit_ms,'written':store.written}}}
                    t=time.monotonic();store.put('transition',{'before':snapshot(before),'input':action,'receipt':receipt,'after':snapshot(after),'prediction':{k:v for k,v in prediction.items() if k!='outcomes'},'result':result,'neutral':neutral,'message_evidence':text_evidence},step)
                    for identifier in list(self.builder.tracker.dirty):
                        variants=self.builder.tracker.variants.get(identifier)
                        if variants:store.put('appearances',{'id':identifier,'variants':variants,'status':'continuous_track_exemplars_not_species'},step,key=identifier)
                    self.builder.tracker.dirty.clear()
                    stages['db_enqueue_ms']=(time.monotonic()-t)*1000;stages['db_commit_ms']=store.commit_ms
                    self.log(row);self.emit(row)
                    self.emit({'event':'ui_vision_frame','image':after_image,'ui':copy.deepcopy(after.get('ui_visual',{}))})
                    if hasattr(self.backend,'record_important'):self.backend.record_important(row,image,after_image)
                    self.previous=after;self.previous_image=after_image;self.summary={'generation':2,'steps':step,'metrics':metrics,'legacy_learning_imported':False}
                except ValueError as error:
                    if str(error) not in ('Stale Spatial result','Spatial stamp mismatch','Stale procedure result','Stale short plan'):raise
                    model.pending.clear();self.previous=None;executor.interrupt('stale_evidence')
                    self.send_event({'event':'spatial_discarded','reason':str(error),'step':step});continue
                except OperationCancelled:
                    model.pending.clear();self.previous=None
                    if self.control and self.control.paused.is_set() and not self.control.intervention.is_set() and not self.stop.is_set():
                        if not self.checkpoint():break
                        continue
                    raise
            if self.control and self.control.intervention.is_set():raise OperationCancelled('手動介入へ切替')
        finally:
            supervisor.close();self.builder.close();prefetch.close();progress_lane.close();spatial_lane.close()
            if short_planner:short_planner.close(step)
            store.put('autonomy',{'model':spatial_lane.model.dump()},step,key='spatial_model')
            executor.save(step,force=True)
            if dungeon_observer:store.put('autonomy',{'records':dungeon_observer.inventory.description_reads},step,key='inventory_descriptions')
            try:
                try:
                    self.backend.release_all()
                    if hasattr(self.backend,'configure_fast_learning'):self.backend.configure_fast_learning(False)
                    if hasattr(self.backend,'configure_scene_boundaries'):self.backend.configure_scene_boundaries(previous_boundary_tracking)
                finally:
                    store.close()
                    try:
                        from .knowledge_access import write_book
                        path=write_book(store,self.data/'knowledge'/profile/'Game_Knowledge.md')
                        self.send_event({'event':'knowledge_book','path':path,'status':'exported'})
                    except Exception as error:
                        self.send_event({'event':'knowledge_book','status':'export_failed','reason':str(error)[:300]})
            finally:
                try:self.log({'event':'nextgen_end','steps':step,'elapsed_seconds':time.monotonic()-run_started,'llm_requests':supervisor.requests,'cycles':cycle_count,'learning':False},durable=True)
                finally:
                    with self.journal_lock:self.stream.close()
        return {'steps':step,'generation':2}
