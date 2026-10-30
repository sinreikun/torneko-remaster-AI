"""Controller-owned projection, validation, execution dispatch and prequential audit."""
import copy,json,time
from pathlib import Path
from .planning import PlanningLane,PlanningEngine,contains,dist,interval
from .planning_knowledge import KnowledgeProvider,EntityExperience,stats
from .state_hash import digest
from .interaction_surface import allows_field_input

class ShortPlanning:
    def __init__(self,store,executor,spatial_policy,trace_path=None,synchronous=False):
        saved=store.load('autonomy','planning_memory') if store else []
        self.memory=EntityExperience(saved[0] if saved else None);self.provider=KnowledgeProvider(executor,spatial_policy,self.memory);self.executor=executor;self.store=store
        self.engine=PlanningEngine(horizon=executor.policy.get('planning',{}).get('horizon',5));self.lane=None if synchronous else PlanningLane(self.engine);self.packet=None;self.world=None;self.intent=None;self.last_goal=None;self.replan_reason='initial';self.trace_path=Path(trace_path) if trace_path else None;self.research_tasks=[]
    def begin(self,state,candidates,step,local=None,control_revision=0,goal_revision=0,capabilities=()):
        self.world=self.provider.snapshot(state,step,local,control_revision,goal_revision,capabilities)
        proposals=self.provider.propose(state,self.world,step)
        if self.world.get('unavailable_capabilities'):
            self.research_tasks=(self.research_tasks+[{'kind':'resolve_unavailable_capability','evidence':self.world['unavailable_capabilities'][:8],'deadline_step':step+16,'success':'validated_input_binding_and_observed_effect'}])[-8:]
        if self.lane:self.lane.begin(self.world,proposals)
        else:self.packet=self.engine.select(self.world,proposals)
        state['planning_world']={'actors':copy.deepcopy(self.world['actors'])}
        state['planning']={'model_revision':self.memory.revision,'current_goal':copy.deepcopy(local),'last_audits':list(self.memory.audit)[-3:],'replan_reason':self.replan_reason,'research_tasks':self.research_tasks[-3:],'candidate_count':len(proposals),'field_waits_for_llm':False}
    def merge(self,state,step,control_revision=0,goal_revision=0):
        stamp={**state.get('spatial',{}).get('stamp',{}),'frame':state['frame_sha256'],'control_revision':control_revision}
        if self.lane:self.packet=self.lane.merge(stamp,goal_revision,self.memory.revision)
        elif stamp!=self.packet['stamp'] or goal_revision!=self.packet['goal_revision'] or self.memory.revision!=self.packet['model_revision']:raise ValueError('Stale short plan')
        selected=self.packet['selected'];self.intent=None
        if not selected:return None
        self.provider.last_plan=selected['candidate']['id'];self.last_goal=goal_revision
        self.intent=copy.deepcopy(selected['candidate']['actions'][0]) if selected['candidate']['actions'] else None
        return selected
    def dispatch(self,state,candidates,step):
        if not self.packet or not self.packet['selected']:return None
        current_stamp={**state.get('spatial',{}).get('stamp',{}),'frame':state['frame_sha256'],'control_revision':self.packet['stamp'].get('control_revision')}
        if current_stamp!=self.packet['stamp']:raise ValueError('Stale short plan')
        chosen=self.packet['selected']['candidate'];spec=chosen['execution'];e=self.executor;kind=spec['kind'];position=state.get('spatial',{}).get('pose');request=None
        if self.intent and self.intent['kind']=='face':
            target=next((a for a in self.world['actors'] if a['id']==self.intent['parameters'].get('target_id')),None)
            if target:request=e.orient(state,{'delta':[target['position'][i]-position[i] for i in (0,1)]},candidates,step)
        elif kind in ('path','spatial'):
            path=spec.get('path') or spec.get('target',{}).get('path',[])
            if len(path)>1 and position is not None:
                delta=[path[1][i]-position[i] for i in (0,1)];keys=[]
                if delta[0]:keys.append(e.roles.get('right' if delta[0]>0 else 'left'))
                if delta[1]:keys.append(e.roles.get('down' if delta[1]>0 else 'up'))
                if all(keys):request=e.request(state,keys,'planned_move','選択Planの経路を一手進め実測と照合',step)
                if request:request['task']='path'
        elif kind=='combat':
            target=next((a for a in self.world['actors'] if a['id']==spec['target_id'] and a['visible']),None)
            if target and position is not None and dist(target['position'],position)==1:request=e.fight(state,{'delta':[target['position'][i]-position[i] for i in (0,1)]},candidates,step)
        elif kind=='interact':
            request=e.role_request(state,'confirm','選択した遷移・障害の作用を有限回検証',step)
            if request:e.attempted['interaction:'+spec['target']['id']]=step+8;request['task']='transition_probe'
        elif kind=='rest':
            request=e.role_request(state,'rest','選択Planの一手だけ足踏みし、HP・敵・資源を再評価',step)
            if request:request['task']='recover'
        elif kind=='review':
            e.begin('review',state,step,'短期計画評価で状態・所持品確認を選択')
            if spec.get('at_item'):e.item_checks[str(position)]=step+128
            request=e.advance(state,step)
        elif kind=='item':
            if e.active:e.interrupt('plan_switch_to_better_supported_means')
            e.begin(spec['purpose'],state,step,'短期計画評価で用途・費用・危険を比較して選択',spec['item'])
            if spec.get('identify_target'):e.active['identify_target']=copy.deepcopy(spec['identify_target'])
            if spec.get('target_id'):e.active['target']={'cell':spec.get('cell'),'id':spec['target_id']}
            request=e.advance(state,step)
        elif kind=='continue':request=e.advance(state,step)
        elif kind=='abort':
            e.finish('interrupted',step,'short_plan_precondition_or_risk_changed')
            if not allows_field_input(state):request=e.role_request(state,'cancel','選択Planを中断し元の目標を再評価',step)
        if request:
            request['short_plan_id']=chosen['id'];request['planning_target_id']=spec.get('target_id')
            if self.intent and request.get('planning_target_id'):self.intent.setdefault('parameters',{})['target_id']=request['planning_target_id']
            request['planning_intent']=copy.deepcopy(self.intent)
        return request
    def feedback(self,before,after,request,receipt,step,messages=()):
        after['planning_adventure']=self.executor.adventure_revision
        packet=self.packet
        if not packet or not packet.get('selected'):return None
        selected=packet['selected'];prediction=selected['prediction'];first=prediction.get('first') or {};checks=[];unscored=[];violations=[]
        valid=bool(request and request.get('short_plan_id')==selected['candidate']['id'] and receipt.get('os_submission')=='accepted' and before['frame_sha256']==packet['stamp']['frame'])
        if not valid:
            record={'status':'censored','reason':'input_not_accepted_or_plan_not_executed','step':step,'plan_id':selected['candidate']['id']};self.replan_reason='censored_input'
        else:
            previous_tracks=copy.deepcopy(self.memory.tracks);self.memory.observe(after,step)
            measured=after.get('spatial',{});pose=measured.get('pose')
            if first.get('position') is not None and measured.get('pose_source')=='OBSERVED' and measured.get('stamp',{}).get('region')==packet['stamp'].get('region'):
                checks.append({'feature':'position','predicted':first['position'],'actual':pose,'match':pose==first['position'],'error':dist(pose,first['position']) if pose is not None else None})
            else:unscored.append('position')
            for name,bound in [('hp',first.get('hp')),('fullness',first.get('resource'))]:
                value=after.get('metrics',{}).get(name,{}).get('values')
                if value and bound and bound['lower'] is not None and bound['upper'] is not None:
                    actual=value[0];error=max(bound['lower']-actual,actual-bound['upper'],0)
                    checks.append({'feature':name,'predicted':bound,'actual':actual,'match':contains(bound,actual),'error':error,'width':bound['upper']-bound['lower']})
                else:unscored.append(name)
            for actor in self.memory.tracks.values():
                possible=first.get('enemy_reachability',{}).get(actor['id'])
                if possible and actor.get('visible'):
                    match=actor['position'] in possible
                    checks.append({'feature':'actor_position:'+actor['id'],'match':match,'actual':actor['position'],'candidate_count':len(possible),'prediction_incomplete':prediction['incomplete_envelope']})
            violations=[c['feature'] for c in checks if not c['match']]
            if measured.get('stamp',{}).get('region')!=packet['stamp'].get('region'):violations.append('region_changed')
            if self.last_goal!=self.world.get('goal_revision'):violations.append('goal_changed')
            record={'status':'compared','step':step,'plan_id':selected['candidate']['id'],'model_revision_before':packet['model_revision'],'checks':checks,'unscored_unknown_or_unmeasured':unscored,'violations':violations,'accuracy':sum(c['match'] for c in checks)/len(checks) if checks else None,'mean_interval_width':sum(c.get('width',0) for c in checks)/len(checks) if checks else None,'before_frame':before['frame_sha256'],'after_frame':after['frame_sha256'],'replan':True,'reason':'prediction_or_precondition_failed' if violations else 'receding_horizon_actual_state_update'}
            intent=copy.deepcopy(self.intent or {});intent={**intent.get('parameters',{}),'kind':intent.get('kind')}
            self.memory.learn(before,after,intent,messages,self.world['state_stats'],step,receipt,previous_tracks)
            record['model_revision_after']=self.memory.revision
            self.replan_reason=record['reason']
            if violations:
                self.research_tasks=(self.research_tasks+[{'kind':'explain_prediction_error','features':violations,'plan_id':record['plan_id'],'evidence':{'before':before['frame_sha256'],'after':after['frame_sha256'],'step':step},'success':'conditional_model_explains_new_observation','deadline_step':step+32}])[-8:]
        self.memory.audit.append(copy.deepcopy(record));after['planning']=self.summary(record)
        self.write_trace(step,request,record)
        if self.store and step%8==0:self.store.put('autonomy',self.memory.dump(),step,key='planning_memory')
        return record
    def summary(self,audit=None):
        selected=(self.packet or {}).get('selected') or {};candidate=selected.get('candidate',{})
        return {'selected_plan':{k:copy.deepcopy(candidate.get(k)) for k in ('id','label','goal','execution')},'evaluation':copy.deepcopy(selected.get('evaluation')),'prediction':copy.deepcopy(selected.get('prediction',{}).get('terminal')),'model_revision':self.memory.revision,'audit':copy.deepcopy(audit),'recent_audits':list(self.memory.audit)[-4:],'research_tasks':copy.deepcopy(self.research_tasks[-3:]),'replan_reason':self.replan_reason,'field_waits_for_llm':False}
    def write_trace(self,step,request,audit):
        if not self.packet:return
        candidates=[]
        for row in self.packet['candidates']:
            prediction=copy.deepcopy(row['prediction'])
            if prediction.get('first'):prediction['first'].pop('enemy_reachability',None)
            candidates.append({'plan':row['candidate'],'prediction':prediction,'evaluation':row['evaluation']})
        record={'event':'short_plan_trace','step':step,'stamp':self.packet['stamp'],'goal_revision':self.packet['goal_revision'],'model_revision':self.packet['model_revision'],'candidates':candidates,'selected':(self.packet.get('selected') or {}).get('candidate',{}).get('id'),'reason':self.packet['reason'],'input':request,'actual_and_error':audit,'replan_reason':self.replan_reason,'limits':self.packet['limits'],'planning_ms':self.packet['ms']}
        if self.store:self.store.put('planning_audit',{'stamp':record['stamp'],'selected':record['selected'],'model_revision':record['model_revision'],'evaluation':(self.packet.get('selected') or {}).get('evaluation'),'actual_and_error':audit,'replan_reason':self.replan_reason,'trace_file':str(self.trace_path) if self.trace_path else None},step)
        if self.trace_path:
            self.trace_path.parent.mkdir(parents=True,exist_ok=True)
            if self.trace_path.exists() and self.trace_path.stat().st_size>12*1024*1024:self.trace_path.replace(self.trace_path.with_suffix('.previous.jsonl'))
            with self.trace_path.open('a',encoding='utf-8') as out:out.write(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n')
    def close(self,step=0):
        if self.lane:self.lane.close()
        if self.store:self.store.put('autonomy',self.memory.dump(),step,key='planning_memory')
