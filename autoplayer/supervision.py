"""Asynchronous, scoped operational plans. Worker never touches game, OCR or DB."""
from collections import deque
from .floor_navigation import MISSIONS
import copy,hashlib,json,queue,re,threading,time
from .interfaces import compact_text,ordinary_choice,torneko_menu_kind,result_screen

SKILLS=('explore','combat','inspect_inventory','recover','interface','verify_action','exit','verify_goal')
SIGNALS=('position','damage_dealt','enemy_defeated','hp_gain','food_gain','interface_change','item_change')
TESTS=('none','movement_response','face_then_attack','door_response','item_effect')
PLAN_SCHEMA={'type':'object','additionalProperties':False,'properties':{
 'objective':{'type':'string'},'skill':{'type':'string','enum':list(SKILLS)},'reason':{'type':'string'},
 'budget_turns':{'type':'integer','minimum':2,'maximum':32},'exploration_fraction':{'type':'number','minimum':0,'maximum':.5},
 'strategy':{'type':'string','enum':['advance','compare_alternatives','avoid_repetition','gather_information']},
 'floor_objective':{'type':'string','enum':list(MISSIONS)},
 'item_decisions':{'type':'array','maxItems':4,'items':{'type':'object','additionalProperties':False,'properties':{'cell':{'type':'array','items':{'type':'integer'},'minItems':2,'maxItems':2},'decision':{'type':'string','enum':['collect','defer']},'reason':{'type':'string'},'revisit':{'type':'string','enum':['inventory_changed','after_combat','goal_changed']}},'required':['cell','decision','reason','revisit']}},
 'navigation':{'type':'string','enum':['current_goal','frontier','stairs','items']},
 'hypothesis':{'type':'string'},'test':{'type':'string','enum':list(TESTS)},'test_attempts':{'type':'integer','minimum':1,'maximum':4},
 'expected_signal':{'type':'string','enum':list(SIGNALS)},
 'interface_role':{'type':'string','enum':['none','confirm','cancel','up','left','down','right']},'interface_quote':{'type':'string'}},
 'required':['objective','skill','reason','budget_turns','exploration_fraction','strategy','navigation','floor_objective','item_decisions','hypothesis','test','test_attempts','expected_signal','interface_role','interface_quote']}

def validate_plan(value):
    if not isinstance(value,dict) or set(value)!=set(PLAN_SCHEMA['required']):raise ValueError('Invalid supervision plan fields')
    for key in ('objective','reason','hypothesis','interface_quote'):
        if not isinstance(value[key],str) or len(value[key])>240:raise ValueError('Invalid supervision text')
    if not value['objective'].strip():raise ValueError('Empty operational objective')
    if any(not re.search(r'[\u3040-\u30ff\u3400-\u9fff]',value[key]) for key in ('objective','reason')):raise ValueError('Supervisor explanations must be Japanese')
    for key in ('skill','test','expected_signal','interface_role','strategy','navigation','floor_objective'):
        if value[key] not in PLAN_SCHEMA['properties'][key]['enum']:raise ValueError('Unknown operational function')
    for key,lo,hi in (('budget_turns',2,32),('test_attempts',1,4)):
        if type(value[key]) is not int or not lo<=value[key]<=hi:raise ValueError('Invalid operational budget')
    x=value['exploration_fraction']
    if type(x) not in (int,float) or not 0<=x<=.5:raise ValueError('exploration_fractionは0〜0.5の小数で指定してください（通常0.125）。受信値: '+repr(x)[:80])
    if value['test']!='none' and not value['hypothesis'].strip():raise ValueError('Experiment needs a question')
    decisions=value['item_decisions']
    if not isinstance(decisions,list) or len(decisions)>4:raise ValueError('Invalid item decisions')
    for d in decisions:
        if not isinstance(d,dict) or set(d)!={'cell','decision','reason','revisit'}:raise ValueError('Invalid item decision fields')
        cell=d['cell']
        if not isinstance(cell,list) or len(cell)!=2 or any(type(v) is not int for v in cell) or not (0<=cell[0]<54 and 0<=cell[1]<32):raise ValueError('Invalid item cell')
        if d['decision'] not in ('collect','defer') or d['revisit'] not in ('inventory_changed','after_combat','goal_changed'):raise ValueError('Invalid item disposition')
        if not isinstance(d['reason'],str) or not d['reason'].strip() or len(d['reason'])>240:raise ValueError('Item disposition needs a reason')
    return copy.deepcopy(value)

class StateLedger:
    def __init__(self):self.current={};self.previous={};self.recent=deque(maxlen=12);self.scope=None;self.epoch=0
    def observe(self,native,state,exterior,world,guidance,revision,step):
        native=native or {};state=state or {};m=state.get('minimap',{});metrics={k:v for k,v in state.get('metrics',{}).items() if not v.get('retained')}
        map_current=m.get('status')=='observed' and not m.get('retained')
        lines=[l.get('text','')[:160] for l in native.get('lines',[])][:28] if native.get('status')=='ok' else []
        text='\n'.join(lines);kind=torneko_menu_kind(native) or result_screen(native)
        if ordinary_choice(native):kind='choice'
        overlay=None
        if not kind and state.get('white_frame_evidence',{}).get('visible'):
            from .battle_messages import read_messages
            normal_frame=state.get('metrics_source_frame_sha256') or state.get('frame_sha256')
            battle=read_messages(native,normal_frame)
            if map_current and metrics.get('floor') and metrics.get('hp') and battle:overlay='battle_message_hypothesis'
            else:kind='text_frame' if lines else 'unread_frame'
        region=exterior.get('location','unclassified')
        if region=='unclassified' and metrics.get('floor'):region='dungeon'
        floor=metrics.get('floor',{}).get('values') if not metrics.get('floor',{}).get('retained') else None
        # A changing cell or animated frame is not a change of plan domain.
        ui_signature=hashlib.sha256('\n'.join(re.sub(r'\d+','N',compact_text(t).lstrip('▶►▷>→')) for t in lines).encode()).hexdigest()[:16] if kind else None
        hp=metrics.get('hp',{}).get('values');close=min(2,sum(max(map(abs,e))<=1 for e in m.get('enemies',[]))) if map_current else 0
        scope=json.dumps([region,floor,kind,exterior.get('scene_id'),close,bool(hp and hp[0]/max(1,hp[1])<.3),ui_signature,guidance.get('revision',0),revision],ensure_ascii=False,sort_keys=True)
        if scope!=self.scope:self.epoch+=1;self.scope=scope
        visual=(native or {}).get('_visual_selection') or {}
        selected=visual.get('text') if visual.get('frame_sha256') and visual['frame_sha256']==native.get('frame_sha256') else None
        cursor=[compact_text(t).lstrip('▶►▷>→') for t in lines if re.match(r'^[▶►▷>→]',compact_text(t))]
        if selected is None and len(cursor)==1:selected=cursor[0]
        selection_identity=[compact_text(selected),visual.get('line_index')] if selected and type(visual.get('line_index')) is int else compact_text(selected) if selected else None
        selection_key=hashlib.sha256(json.dumps(selection_identity,ensure_ascii=False).encode()).hexdigest()[:16] if selected else None
        self.previous=self.current
        self.current={'step':step,'epoch':self.epoch,'scope':scope,'region':region,'floor':floor,'interface':kind,
          'text_overlay_hypothesis':overlay,'metrics':copy.deepcopy(metrics),'last_known_metrics':copy.deepcopy((world or {}).get('last_known_metrics',{})),
          'visible_map_targets':{k:copy.deepcopy(m.get(k,[]))[:16] for k in ('items','stairs')} if map_current else {},
          'floor_exploration':{k:copy.deepcopy(v) for k,v in state.get('floor_exploration',{}).items() if not k.endswith('_cells')},
          'retained_state':copy.deepcopy(state.get('retained_state',{})),
          'map_current':map_current,'player_cell':m.get('player_cell') if map_current else None,
          'contacts':copy.deepcopy(m.get('enemies',[]))[:16] if map_current else [],
          'frame_sha256':state.get('metrics_source_frame_sha256') or state.get('frame_sha256'),
          'selection_key':selection_key,'selection_text':selected,'selection_line_index':visual.get('line_index') if selected else None,'text':text,'text_key':hashlib.sha256(compact_text(text).encode()).hexdigest()[:20],
          'operator_guidance':copy.deepcopy(guidance),'control_revision':revision,
          'fact_rule':'現在の数値/位置は今回の観測。last_knownは過去の参考。敵種/所持/向き/因果は未確認。'}
        floor=self.current.get('floor_exploration',{});items=floor.get('item_tasks',{})
        if items:
            pos=m.get('player_cell') or [0,0]
            rank={'planned':0,'arrival_unverified':1,'awaiting_inventory_check':1,'capacity_blocked':1,'marker_unclassified':2,'deferred':3,'pickup_confirmed':9,'marker_resolved':9}
            tasks=sorted(items.get('tasks',[]),key=lambda t:(rank.get(t['status'],4),abs(t['cell'][0]-pos[0])+abs(t['cell'][1]-pos[1])))
            floor['item_tasks']={**items,'tasks':tasks[:8],'total_tasks':len(tasks),'summary_limited':len(tasks)>8}
        return self.current
    def record(self,row):
        self.recent.append({'step':row.get('step'),'keys':row.get('observed_keys'),
          'role':(row.get('action_effect_learning') or {}).get('role'),'effects':(row.get('action_effect_learning') or {}).get('effects'),
          'source':(row.get('operation') or {}).get('selected_source'),'progress':(row.get('operation_evaluation') or {}).get('progress'),
          'prediction':(row.get('forecast_evaluation') or {}).get('status'),'experiment':copy.deepcopy(row.get('operational_experiment')),'prediction_validation':{k:v for k,v in (row.get('prediction_validation') or {}).items() if k in ('status','needs_review','checks','rule_updates')},'ms':row.get('performance',{}).get('total_ms')})
    def snapshot(self):return {'current':copy.deepcopy(self.current),'recent':copy.deepcopy(list(self.recent))}

class Cancel:
    def __init__(self,*events):self.events=events
    def is_set(self):return any(e is not None and e.is_set() for e in self.events)

class Supervisor:
    def __init__(self,review,stop,emit=None,review_interval=None):
        self.review=review;self.stop=stop;self.emit=emit or (lambda _:None)
        self.review_interval=max(2,int(review_interval)) if review_interval is not None else None
        self.closed=threading.Event();self.inflight=None;self.result=queue.Queue(maxsize=1);self.plan=None
        self.next_review=0;self.errors=0;self.requests=0;self.adoptions=0;self.discarded=0;self.last_reason='initial';self.last_error=None
        self.last_request=-100;self.last_signature=None;self.stall=0;self.history=deque(maxlen=12);self.last_completed=None;self.plan_stats={};self.review_ms=None
    def bootstrap(self,current):
        hp=current.get('metrics',{}).get('hp',{}).get('values');food=current.get('metrics',{}).get('fullness',{}).get('values')
        skill='interface' if current.get('interface') else 'exit' if current.get('region')=='outside' else 'recover' if hp and hp[0]/max(1,hp[1])<.3 or food and food[0]<=15 else 'combat' if any(max(map(abs,e))<=1 for e in current.get('contacts',[])) else 'explore'
        return {'objective':'現在の観測と経験から全体目標へ進める','skill':skill,'reason':'監督回答までの観測に基づく暫定運用',
          'budget_turns':8,'exploration_fraction':.125,'strategy':'advance','navigation':'current_goal','floor_objective':'auto','item_decisions':[],'hypothesis':'','test':'none','test_attempts':1,'expected_signal':'damage_dealt' if skill=='combat' else 'interface_change' if skill=='interface' else 'food_gain' if skill=='recover' and food and food[0]<=15 else 'hp_gain' if skill=='recover' else 'position',
          'interface_role':'none','interface_quote':'','origin':'local_bootstrap','scope':current['scope'],'adopted_step':current['step'],
          'expires_step':current['step']+8,'request_selection':current.get('selection_key'),'request_text_key':current['text_key'],'request_frame':current.get('frame_sha256'),'id':'bootstrap-'+str(current['epoch']),'anchor_floor':copy.deepcopy(current.get('floor'))}
    def tick(self,ledger,extra=None,image=None):
        current=ledger.current;step=current['step'];scope=current['scope']
        if self.closed.is_set() or self.stop.is_set():return None
        if self.inflight and self.inflight['scope']!=scope:self.inflight['cancel'].set()
        try:result=self.result.get_nowait()
        except queue.Empty:result=None
        if result is not None:
            request=self.inflight;self.inflight=None
            self.last_completed={'audit_id':request.get('inventory_audit_id') if request else None,'error':bool(result.get('error'))}
            if request is None or request['cancel'].is_set() or request['scope']!=scope or step-request['step']>64:
                self.discarded+=1;self.last_reason='stale_scope_or_age';self.next_review=step
            elif result.get('error'):
                self.errors+=1;self.last_error=result['error'];self.last_reason='review_error';self.next_review=step+min(32,4*2**min(3,self.errors-1))
            else:
                self.review_ms=result.get('elapsed_ms')
                try:
                    plan=validate_plan(result['plan'])
                    if plan['skill']=='interface' and current.get('map_current') and not current.get('interface'):
                        raise ValueError('現在は地図を観測した通常画面で、会話/選択肢/メニューの根拠がないため画面操作計画を採用しない。')
                except (ValueError,KeyError,TypeError) as error:
                    self.last_completed['error']=True
                    self.errors+=1;self.last_error=str(error);self.last_reason='invalid_review';self.next_review=step+8
                else:
                    self.errors=0;self.last_error=None;self.adoptions+=1;self.stall=0
                    self.plan={**plan,'origin':'llm_supervisor','scope':scope,'adopted_step':step,'expires_step':step+plan['budget_turns'],
                      'inventory_audit_id':request.get('inventory_audit_id'),'request_selection':request.get('selection_key'),'request_text_key':request['text_key'],'request_frame':request.get('frame_sha256'),'id':'plan-'+str(self.adoptions),'anchor_floor':copy.deepcopy(current.get('floor'))}
                    self.next_review=step+(self.review_interval or max(2,plan['budget_turns']//2));self.last_reason='review_adopted'
                    self.history.append({'event':'adopted','id':self.plan['id'],'step':step,'skill':plan['skill']});self.emit({'event':'supervisor_plan','plan':copy.deepcopy(self.plan)})
        if self.plan and self.plan['scope']!=scope and self.plan['skill'] in ('recover','inspect_inventory'):
            old=json.loads(self.plan['scope']);new=json.loads(scope)
            known_ui={'main_menu','inventory','item_actions','carry_inventory'}
            anchor=self.plan.get('anchor_floor')
            compatible=old[-2:]==new[-2:] and (new[1] is None or anchor is None or anchor==new[1]) and (new[2] in known_ui or old[2] in known_ui and new[2] is None)
            if compatible:self.plan['scope']=scope
        if not self.plan or self.plan['scope']!=scope or step>=self.plan['expires_step']:
            changed_domain=not self.plan or self.plan['scope']!=scope
            self.plan=self.bootstrap(current)
            if (changed_domain or self.review_interval is None) and self.last_reason not in ('review_error','invalid_review') and step-self.last_request>=2:self.next_review=min(step,self.next_review)
        # State mismatch is handled locally immediately. Reviews are bounded,
        # with one active request and no backlog of old screenshots.
        signature=(scope,self.plan['id'])
        if self.stall>=(8 if self.plan['skill']=='recover' and not current.get('contacts') else 4) and signature!=self.last_signature and step-self.last_request>=4:
            self.next_review=min(self.next_review,step);self.last_signature=signature;self.last_reason='prediction_or_progress_review'
        if current.get('floor_exploration',{}).get('goal',{}).get('needs_review') and step-self.last_request>=4:
            self.next_review=min(self.next_review,step);self.last_reason='floor_objective_review'
        known_fast_ui=current.get('interface') in ('choice','defeat_result','high_scores','main_menu','inventory','item_actions','carry_inventory')
        audit_context=current.get('inventory_audit',{})
        audit_due=audit_context.get('active') and audit_context.get('stage')=='analyse'
        ui_review_due=bool(audit_due) or not known_fast_ui or step-self.last_request>=8
        if not self.inflight and step>=self.next_review and ui_review_due:
            snapshot=ledger.snapshot();snapshot.update(copy.deepcopy((extra() if callable(extra) else extra) or {}));snapshot['review_feedback']={'last_error':self.last_error,'reason':self.last_reason};snapshot['active_plan']=copy.deepcopy(self.plan);snapshot['plan_performance']=copy.deepcopy(list(self.plan_stats.values())[-8:])
            request={'scope':scope,'step':step,'inventory_audit_id':audit_context.get('id') if audit_due else None,'text_key':current['text_key'],'selection_key':current.get('selection_key'),'frame_sha256':current.get('frame_sha256'),'cancel':threading.Event()}
            self.inflight=request;self.requests+=1;self.last_request=step
            def work():
                started=time.monotonic()
                try:value={'plan':self.review(snapshot,image,Cancel(self.stop,self.closed,request['cancel'])),'elapsed_ms':round((time.monotonic()-started)*1000)}
                except Exception as error:value={'error':str(error)[:300]}
                if not self.closed.is_set():
                    try:self.result.put_nowait(value)
                    except queue.Full:pass
            threading.Thread(target=work,name='gamepilot-supervisor',daemon=True).start()
        return self.plan
    def observe_result(self,row):
        evaluation=row.get('operation_evaluation',{})
        if row.get('executed'):
            operation=row.get('operation') or {};identifier=operation.get('plan_id','unknown')
            statistics=self.plan_stats.setdefault(identifier,{'plan_id':identifier,'skill':operation.get('skill'),'inputs':0,'progress_observations':0,'preparations':0,'no_effect':0,'predictions':0,'prediction_matches':0,'total_ms':0})
            statistics['inputs']+=1;statistics['progress_observations']+=int(bool(evaluation.get('progress')))
            statistics['preparations']+=int(bool(evaluation.get('preparation')))
            statistics['no_effect']+=int((row.get('action_effect_learning') or {}).get('effects',{}).get('no_effect') is True)
            status=(row.get('forecast_evaluation') or {}).get('status');statistics['predictions']+=int(status in ('matched','mismatch'));statistics['prediction_matches']+=int(status=='matched')
            statistics['total_ms']+=row.get('performance',{}).get('total_ms',0)
            while len(self.plan_stats)>12:self.plan_stats.pop(next(iter(self.plan_stats)))
            self.stall=0 if evaluation.get('progress') else self.stall+1
            if not self.errors and (row.get('prediction_validation') or {}).get('needs_review') and row.get('step',0)-self.last_request>=4:
                self.next_review=min(self.next_review,row.get('step',0));self.last_reason='empirical_rule_review'
            if not self.errors and (row.get('forecast_evaluation') or {}).get('status')=='mismatch':self.next_review=min(self.next_review,row.get('step',0));self.last_reason='prediction_mismatch'
    def context(self,current):
        return {'review_interval':self.review_interval,'enabled':True,'plan':copy.deepcopy(self.plan),'remaining':max(0,(self.plan or {}).get('expires_step',0)-current['step']),
          'review_in':max(0,self.next_review-current['step']),'review_running':self.inflight is not None,'requests':self.requests,'adoptions':self.adoptions,
          'review_elapsed_ms':self.review_ms,'plan_performance':copy.deepcopy(self.plan_stats.get((self.plan or {}).get('id'),{})),
          'request_metrics':copy.deepcopy(getattr(getattr(self.review,'__self__',None),'supervision_request_metrics',[])),
          'discarded':self.discarded,'unproductive_inputs':self.stall,'reason':self.last_reason,'error':self.last_error}
    def close(self):
        self.closed.set()
        if self.inflight:self.inflight['cancel'].set()
