"""The UI's sole autonomous execution path; shell protocols remain unchanged."""
import copy,hashlib,io,json,os,threading,time
from collections import deque
from pathlib import Path
from PIL import Image,ImageChops,ImageStat
from .state import StateBuilder,digest,effects
from .store import ExperienceStore
from .model import ForwardModel,AdaptivePolicy,snapshot,target_score
from .supervisor import SemanticSupervisor
from .meta import MetaController
from ..controls import is_torneko
from ..cancellation import OperationCancelled
from ..observation_store import message_candidates
from ..map_sensor import read_grid

class NextGenController:
    def __init__(self,cfg,backend,llm,emit,journal,stop=None,memory=None,control=None,diagnostic_sink=None):
        self.cfg=cfg.validate();self.backend=backend;self.llm=llm;self.emit=emit;self.journal=Path(journal);self.control=control;self.stop=stop if stop is not None else threading.Event()
        self.cancel=control or self.stop;self.llm.activity=emit;self.llm.ocr.cancel=self.cancel
        self.builder=StateBuilder(cfg,llm.ocr);self.diagnostic_sink=diagnostic_sink;self.last={};self.durations={};self.events=[];self.previous=None;self.stream=None
        self.data=self.journal.parent.parent;self.completed=False;self.summary={};self.journal_lock=threading.RLock()
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
    def sense(self,png,full=True,step=0):
        state=self.builder.build(png,full)
        # This adapter only measures the user's map legend, never chooses actions.
        floor=state['floor_confirmation'];valid_floor=floor.get('status') in ('initialized','confirmed','changed') and floor.get('confirmed') is not None
        can_map=is_torneko(self.cfg.window_title) and 'num7' in self.cfg.allowed_keys and valid_floor and not state['interface_hypothesis']
        if can_map and hasattr(self.backend,'capture_map_display') and not self.cancel.is_set():
            started=time.monotonic();sample=self.backend.capture_map_display(self.cancel,baseline=False)
            if sample and sample.get('image'):
                measured=read_grid(sample['image'])
                if measured.get('status')=='observed':
                    measured=copy.deepcopy(measured);measured['source_frame_sha256']=hashlib.sha256(sample['image']).hexdigest();measured['observation_role']='held_map_display_measurement'
                    state=self.builder.build(png,full,measured_map=measured)
            state['timing']['map_capture_ms']=(time.monotonic()-started)*1000
        if hasattr(self.backend,'record_frame'):self.backend.record_frame(png,'observation' if step==0 else 'result')
        return state
    def wait_time(self,state,protect):
        context=state['context'];samples=self.durations.get(context,[])
        base=350 if protect else 140
        if samples:base=max(base,min(1500,int(sorted(samples)[int((len(samples)-1)*.9)]*1.15+70)))
        return min(self.cfg.max_wait_ms,base+getattr(self,'observation_policy',{}).get('settle_extra_ms',0))
    def run(self,dry_run=False):
        profile=digest([self.cfg.profile_id or self.cfg.window_title,sorted(self.cfg.allowed_keys),self.cfg.key_bindings])
        store=ExperienceStore(self.data/'world-v2.sqlite',profile,self.journal.stem);model=ForwardModel(store);adaptive=AdaptivePolicy();meta=MetaController(store,self.cfg.goal)
        self.observation_policy=meta.observer.policy;self.builder.observation_policy=self.observation_policy
        supervisor=SemanticSupervisor(self.cfg,self.llm,self.send_event,self.cancel);candidates=model.candidates(self.cfg)
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
                    if last_revision is not None and revision!=last_revision:model.pending.clear();self.previous=None
                    last_revision=revision
                    source=getattr(self.llm,'operator_guidance',None);guidance=source.snapshot() if source else {'revision':0,'text':''}
                    t=time.monotonic();image=self.backend.capture();stages['capture_ms']=(time.monotonic()-t)*1000
                    self.observation_policy=meta.observer.policy;self.builder.observation_policy=self.observation_policy
                    full=bool(step==0 or step%self.observation_policy['full_ocr_period']==0 or (self.last.get('surprise') or 0)>2 or self.last.get('loop') or self.previous and self.previous['interface_hypothesis'])
                    t=time.monotonic();before=self.builder.build(image,full);stages.update(before['timing']);stages['observation_ms']=(time.monotonic()-t)*1000
                    neutral=None;natural_confidence=.4
                    if hasattr(self.backend,'observe_turn') and (step%8==0 or before['quality']<.5):
                        t=time.monotonic();sample=self.backend.observe_turn(image,'neutral_observation',0,self.cancel)
                        if self.cancel.is_set():
                            if not self.checkpoint():break
                            continue
                        after_neutral=self.builder.build(sample.image,full)
                        neutral=effects(before,after_neutral);natural_confidence=.85;before=after_neutral;image=sample.image
                        stages['neutral_observation_ms']=(time.monotonic()-t)*1000
                    for _ in range(self.observation_policy['extra_captures']):
                        if self.cancel.wait(.08):break
                        image=self.backend.capture()
                    before=self.sense(image,full)
                    meta.prepare(before,step)
                    t=time.monotonic();requests_before=supervisor.requests
                    plan,adoption=supervisor.tick(before,candidates,step,revision,guidance,image,model,self.last,meta)
                    if adoption:
                        meta.accept(adoption['plan'],before,step)
                        model.add_hypotheses(adoption['plan']['hypotheses'],adoption['state'],candidates)
                        for interpretation in adoption['plan']['interpretations']:
                            identifier=digest([adoption['state']['frame_sha256'],interpretation])
                            store.put('interpretations',{**interpretation,'source_frame':adoption['state']['frame_sha256'],'context':adoption['state']['context'],'status':'llm_interpretation_unverified'},step,key=identifier)
                    stages['supervisor_dispatch_ms']=(time.monotonic()-t)*1000
                    plan=meta.guide(plan,before,candidates)
                    t=time.monotonic();chosen,ranking,choice_ms=model.choose(before,candidates,plan,adaptive.weight)
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
                        self.send_event(meta.nonexecution(before,step,(time.monotonic()-started)*1000))
                        self.send_event({'event':'nextgen_stale_plan','reason':'入力直前に画面が大きく変化','learning':False,'difference':difference});model.pending.clear();self.previous=None;continue
                    protect=bool(before['interface_hypothesis'] or before['minimap'].get('enemies') or any('攻撃' in self.cfg.key_bindings.get(k,'') or 'attack' in self.cfg.key_bindings.get(k,'').casefold() for k in action['keys']))
                    wait=self.wait_time(before,protect)
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
                    text_evidence=[]
                    if protect or hp_loss or combat_text:
                        for frame in message_candidates(after_image,frames,limit=2):
                            evidence=self.builder.message_evidence(frame)
                            text_evidence.append({'frame_sha256':evidence['sha'],'native':evidence['native'],'role':'message_evidence_only_not_current_state'})
                    step+=1;t=time.monotonic()
                    result=model.observe(before,action,after,prediction,receipt,step,neutral,kind,natural_confidence)
                    self.last=result
                    autonomy=meta.observe(before,action,after,result,receipt,step,(time.monotonic()-started)*1000,neutral)
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
                    row={**decision,'event':'result','step':step,'autonomy':meta.context(after),'executed':receipt.get('os_submission')=='accepted','input_receipt':receipt,'action_kind':'input','turn_kind':kind,
                      'changed':before['frame_sha256']!=after['frame_sha256'],'policy_after':{k:v for k,v in after.items() if k!='visual_vector'},'policy_state':shown_before,'ocr':copy.deepcopy(self.builder.cache.get(before['frame_sha256'],{}).get('native',{'status':'unavailable','lines':[]})),
                      'result_ocr':self.builder.cache[after['frame_sha256']]['native'],'message_evidence':text_evidence,'transition_attribution':result.get('attribution','censored'),'neutral_before':neutral,
                      'supervision':supervisor.context(step),'loop_guard':result.get('loop') or {},'forecast_evaluation':{'status':'matched' if result.get('match') is True else 'mismatch' if result.get('match') is False else 'unscored'},
                      'performance':{'total_ms':(time.monotonic()-started)*1000,'stages_ms':stages,'llm_requested':supervisor.pending is not None},
                      'nextgen':{'metrics':metrics,'prediction_before_input':{k:v for k,v in prediction.items() if k!='outcomes'},'result':result,'selected':shown_chosen,'candidates':shown_ranking,'objective':plan['objective'],'hypotheses':copy.deepcopy(model.hypotheses[-8:]),'semantic_interpretations':plan.get('interpretations',[]),'parameters':{'exploration_weight':adaptive.weight,'trial':adaptive.trial},'db':{'queued':store.queue.qsize(),'commit_ms':store.commit_ms,'written':store.written}}}
                    t=time.monotonic();store.put('transition',{'before':snapshot(before),'input':action,'receipt':receipt,'after':snapshot(after),'prediction':{k:v for k,v in prediction.items() if k!='outcomes'},'result':result,'neutral':neutral,'message_evidence':text_evidence},step)
                    for identifier in list(self.builder.tracker.dirty):
                        variants=self.builder.tracker.variants.get(identifier)
                        if variants:store.put('appearances',{'id':identifier,'variants':variants,'status':'continuous_track_exemplars_not_species'},step,key=identifier)
                    self.builder.tracker.dirty.clear()
                    stages['db_enqueue_ms']=(time.monotonic()-t)*1000;stages['db_commit_ms']=store.commit_ms
                    self.log(row);self.emit(row)
                    if hasattr(self.backend,'record_important'):self.backend.record_important(row,image,after_image)
                    self.previous=after;self.summary={'generation':2,'steps':step,'metrics':metrics,'legacy_learning_imported':False}
                except OperationCancelled:
                    model.pending.clear();self.previous=None
                    if self.control and self.control.paused.is_set() and not self.control.intervention.is_set() and not self.stop.is_set():
                        if not self.checkpoint():break
                        continue
                    raise
            if self.control and self.control.intervention.is_set():raise OperationCancelled('手動介入へ切替')
        finally:
            supervisor.close()
            try:
                try:
                    self.backend.release_all()
                    if hasattr(self.backend,'configure_fast_learning'):self.backend.configure_fast_learning(False)
                finally:store.close()
            finally:
                try:self.log({'event':'nextgen_end','steps':step,'elapsed_seconds':time.monotonic()-run_started,'llm_requests':supervisor.requests,'cycles':cycle_count,'learning':False},durable=True)
                finally:
                    with self.journal_lock:self.stream.close()
        return {'steps':step,'generation':2}
