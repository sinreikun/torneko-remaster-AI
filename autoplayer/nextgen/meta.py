"""Hierarchical supervision: acquire means, diagnose, test, and resume goals."""
import copy,time
from collections import deque
from .progress import ProgressManager,condition_value,CAPABILITIES
from .capabilities import CapabilityManager,scope
from .observer_adaptation import ObserverAdaptation
from .state import digest

FAILURES=('observation_missing','observation_conflict','state_identification_failure',
          'world_prediction_failure','capability_missing','skill_condition_mismatch',
          'input_not_delivered','repetition_stagnation','progress_unknown','goal_unreachable',
          'llm_measurement_mismatch','unclassified')
OBSERVATION_FAILURES={'observation_missing','observation_conflict','state_identification_failure','world_prediction_failure','llm_measurement_mismatch'}

class MetaController:
    def __init__(self,store,intent):
        self.store=store;self.progress=ProgressManager(store);self.capabilities=CapabilityManager(store);self.observer=ObserverAdaptation(store)
        self.progress.intent_tag=digest(intent)
        self.intent=intent;self.local=None;self.recovery=None;self.pending_local=None;self.history=deque(maxlen=32);self.failures=[];self.attempted={};self.recent=deque(maxlen=20);self.last_scope=None;self.phase='understanding';self.proposal_requests=0;self.plan_attempts=0;self.last_proposal=-1000;self.goal_epoch=0;self.last_state={};self.last_changes=[];self.exhausted=set();self.deferred=[];self.nonexecutions=0;self.restore_local=None;self.boundary={'conditions':[],'frames':[],'armed':True}
        rows=store.load('autonomy','manager')
        if rows and rows[0].get('intent')==intent:
            saved=rows[0];self.history.extend(saved.get('history',[]));self.goal_epoch=saved.get('goal_epoch',0)
            self.restore_local={'local':saved.get('local'),'scope':saved.get('scope')}
            self.boundary=saved.get('boundary',self.boundary)
            self.attempted=saved.get('attempted',{});self.deferred=saved.get('deferred',[])[-16:]
        self.save()
    def save(self):
        self.store.put('autonomy',{'intent':self.intent,'local':self.local,'recovery':self.recovery,'history':list(self.history),'goal_epoch':self.goal_epoch,'session':self.store.session,'boundary':self.boundary,'attempted':self.attempted,'deferred':self.deferred[-16:],'scope':scope(self.last_state) if self.last_state else None},key='manager')
    def needs_plan(self,step):return (not self.progress.nodes or not self.progress.available() or bool(self.exhausted)) and step-self.last_proposal>=128
    def planning_context(self,state,step):
        c=self.context(state)
        c['skills']=c['skills'][-4:];c['progress']=c['progress'][-8:]
        c['observation_policy']={'settings':self.observer.policy,'trial':(self.observer.summary()['trial'] or {}).get('item')}
        if self.needs_plan(step):
            c['planning_material']={'long_term_intent':self.intent[:6000],
             'instruction':'意図から前提付き進行ノードを最大6件提案。見た/取得/所持/帰還/イベントを混同しない。資料は仮説。今検証できない条件は未確認のまま。現在の能力と場面から到達可能な目標を優先。'}
            self.last_proposal=step;self.plan_attempts+=1
        return c
    def accept(self,plan,state,step):
        if plan.get('session_boundary') and not self.boundary['conditions']:
            self.boundary={'conditions':copy.deepcopy(plan['session_boundary']),'frames':[],'armed':True}
        proposals=plan.get('progress_nodes',[])
        if proposals:
            try:
                count=self.progress.propose(proposals);self.history.append({'event':'progress_proposed','count':count,'step':step,'status':'hypotheses_not_facts'})
            except ValueError as error:self.history.append({'event':'progress_rejected','reason':str(error),'step':step})
        # A brief plan is a local suggestion, never a campaign completion report.
        targets=[{k:t[k] for k in ('feature','operator','value')} for t in plan.get('targets',[]) if t.get('feature')!='node']
        if targets and not all(condition_value(state,c) is True for c in targets):
            proposed={'id':'local:'+digest(targets),'label':plan['objective'],'conditions':targets,'capability':plan.get('required_capability') or self.infer_capability(targets,state),'origin':'llm_hypothesis','created':step,'deadline':step+32,'parent':(self.progress.current() or {}).get('id'),'start_frame':state['frame_sha256']}
            if self.recovery:self.pending_local=proposed
            else:self.local=proposed;self.goal_epoch+=1
        self.save()
    @staticmethod
    def infer_capability(targets,state):
        if any(t['feature']=='interface_open' and t['value']==0 for t in targets):return 'close_interface'
        if any(t['feature']=='selection_text' for t in targets):return 'change_selection'
        return 'confirm' if state.get('interface_hypothesis') else 'move'
    def ensure_local(self,state,step):
        node=self.progress.current()
        if self.local and not (node and self.local.get('origin')=='capability_acquisition') and (self.local.get('parent') is None or self.progress.nodes.get(self.local['parent'],{}).get('status') not in ('confirmed','disputed')):return
        if node:
            cap=next(iter(node['capabilities']),'inspect')
            self.local={'id':'node:'+node['id'],'label':node['label'],'parent':node['id'],'conditions':copy.deepcopy(node['conditions']),'capability':cap,'origin':'progress_graph','created':step,'deadline':step+48,'start_frame':state['frame_sha256']}
        else:
            cap='change_selection' if state.get('interface_hypothesis') and state.get('selection_observation',{}).get('line_index') is not None else 'close_interface' if state.get('interface_hypothesis') else 'move'
            conditions=[{'feature':'interface_open','operator':'eq','value':0}] if cap=='close_interface' else []
            self.local={'id':'ability:'+cap,'label':'現在の観測条件で操作の効果を検証する: '+cap,'parent':None,'conditions':conditions,'capability':cap,'origin':'capability_acquisition','created':step,'deadline':step+24,'start_frame':state['frame_sha256']}
        self.goal_epoch+=1
    def classify(self,state,result=None,receipt=None,plan=None):
        reasons=[];result=result or {}
        if self.nonexecutions>=3:reasons.append('state_identification_failure')
        if receipt and receipt.get('os_submission') not in ('accepted','unknown'):reasons.append('input_not_delivered')
        if state.get('measurement_status')!='ok' or state.get('quality',0)<.5:reasons.append('observation_missing')
        if state.get('floor_confirmation',{}).get('status')=='pending':reasons.append('observation_conflict')
        if state.get('state_identification')=='appearance_text_hypothesis' and state.get('quality',0)<.7:reasons.append('state_identification_failure')
        if result.get('match') is False:reasons.append('world_prediction_failure')
        if any(h.get('status')=='contradicted' for h in result.get('hypothesis_checks',[])):reasons.append('llm_measurement_mismatch')
        if result.get('loop'):reasons.append('repetition_stagnation')
        if len(self.recent)>=12 and len({x['node'] for x in self.recent})<=3 and not any(x['progress'] for x in self.recent):reasons.append('repetition_stagnation')
        if self.local:
            cap=self.local['capability'];conditions=self.local['conditions']
            if cap not in CAPABILITIES:reasons.append('unclassified')
            elif not self.capabilities.matching(cap,state,conditions):
                reasons.append('skill_condition_mismatch' if any(s['name']==cap and s['status']=='supported_conditionally' for s in self.capabilities.skills.values()) else 'capability_missing')
            if self.local.get('parent') and self.progress.nodes.get(self.local['parent'],{}).get('status')=='disputed':reasons.append('goal_unreachable')
        if not self.progress.nodes or (self.progress.current() or {}).get('unknown'):reasons.append('progress_unknown')
        elif not self.progress.available() and any(n['status']!='confirmed' for n in self.progress.nodes.values()):reasons.append('goal_unreachable')
        return list(dict.fromkeys(reasons))
    def new_recovery(self,state,step):
        if self.recovery or not self.failures:return
        primary=self.failures[0];kind='observer_trial' if primary in OBSERVATION_FAILURES else 'alternative_skill' if primary in ('capability_missing','skill_condition_mismatch','repetition_stagnation') else 'gather_progress_evidence' if primary in ('progress_unknown','goal_unreachable') else 'verify_backend' if primary=='input_not_delivered' else 'reidentify'
        # Rotate finite hypotheses after failed attempts; never reset the same budget.
        variants=[kind,'observer_trial','alternative_skill','replan']
        for method in dict.fromkeys(variants):
            key=digest([self.local['id'],scope(state),method])
            if key in self.attempted:continue
            self.attempted[key]=step
            while len(self.attempted)>128:self.attempted.pop(next(iter(self.attempted)))
            self.recovery={'id':key,'failure':primary,'method':method,'resume':copy.deepcopy(self.local),'start':step,'deadline':step+8,'wall_deadline':time.monotonic()+45,'start_state':state['id'],'start_quality':state.get('quality',0),'start_conditions':[condition_value(state,c) for c in self.local['conditions']],'attempted_actions':[],'success_test':'元目標の新しい成立、能力獲得、または欠測解消。画面差だけは達成にしない。'}
            if method=='observer_trial':self.observer.start(primary,step)
            if method=='replan':self.last_proposal=-1000
            self.history.append({'event':'recovery_started','step':step,'failure':primary,'method':method,'resume':self.local['id']});self.save();return
        exhausted_key=digest([self.local['id'],scope(state)])
        if exhausted_key not in self.exhausted:
            self.exhausted.add(exhausted_key)
            self.history.append({'event':'recovery_hypotheses_exhausted','step':step,'goal':self.local['id'],'next':'別の達成可能な目標、または新しい証拠を必要とする再計画'})
            self.deferred.append(copy.deepcopy(self.local));self.deferred=self.deferred[-16:]
            alternative=next((n for n in self.progress.available() if n['id']!=self.local.get('parent')),None)
            if alternative:
                self.local={'id':'node:'+alternative['id'],'label':alternative['label'],'parent':alternative['id'],'conditions':copy.deepcopy(alternative['conditions']),'capability':next(iter(alternative['capabilities']),'inspect'),'origin':'alternative_goal_after_failed_recovery','created':step,'deadline':step+32,'start_frame':state['frame_sha256']}
                self.goal_epoch+=1
            self.save()
    def check_boundary(self,state,step):
        b=self.boundary
        if not b['conditions']:return False
        values=[condition_value(state,c) for c in b['conditions']]
        if all(v is True for v in values):
            if state['frame_sha256'] not in b['frames']:b['frames'].append(state['frame_sha256']);b['frames']=b['frames'][-2:]
            if b['armed'] and len(b['frames'])>=2:
                self.progress.session=self.store.session+':'+state['frame_sha256'][:16]
                b['armed']=False;self.local=None;self.recovery=None;self.pending_local=None;self.recent.clear()
                self.history.append({'event':'session_boundary_observed','step':step,'evidence':list(b['frames']),'status':'proposed_boundary_condition_observed'})
                return True
        elif any(v is False for v in values):b['frames']=[];b['armed']=True
        return False
    def nonexecution(self,state,step,ms):
        self.nonexecutions+=1
        self.observer.observe(state,{'match':None},ms,False,step)
        self.failures=self.classify(state)
        self.new_recovery(state,step)
        self.save()
        return {'event':'autonomy_update','step':step,'state':self.context(state),'reason':'input_revalidation_failed_no_input_sent'}
    def prepare(self,state,step):
        self.observer.expire()
        if self.recovery and time.monotonic()>=self.recovery['wall_deadline']:
            r=self.recovery;self.local=r['resume'];self.recovery=None
            self.history.append({'event':'recovery_finished','status':'wall_deadline_try_other_hypothesis','method':r['method'],'resume':self.local['id']})
        self.last_state=state;self.check_boundary(state,step);self.last_changes=self.progress.update(state,step)
        if self.restore_local:
            saved=self.restore_local
            if saved['local'] and saved['scope']==scope(state):
                self.local=copy.deepcopy(saved['local']);self.local['created']=step;self.local['deadline']=step+24
                self.history.append({'event':'goal_revalidated_after_restart','id':self.local['id']})
            self.restore_local=None
        self.ensure_local(state,step)
        self.failures=self.classify(state)
        if not self.recovery:self.new_recovery(state,step)
        return self.context(state)
    def guide(self,plan,state,candidates):
        plan=copy.deepcopy(plan)
        if not self.local:return plan
        conditions=self.local['conditions']
        # Only measurable local conditions enter the low-level planner.
        plan['objective']=self.local['label'];plan['targets']=[{**t,'weight':1} for t in conditions]
        skill=self.capabilities.priors(self.local['capability'],state,candidates,conditions)
        values={v['id']:v for v in plan.get('candidate_values',[])}
        for v in skill:values[v['id']]=v
        if self.recovery and self.recovery['method'] in ('alternative_skill','replan'):
            for action in self.recovery['attempted_actions'][-6:]:values[action]={'id':action,'value':-1.5,'reason':'有限復旧中に試した入力。別候補を比較する'}
        plan['candidate_values']=list(values.values());plan['meta_goal_id']=self.local['id']
        return plan
    def observe(self,before,action,after,result,receipt,step,ms,neutral=None):
        self.nonexecutions=0;self.last_state=after;self.check_boundary(after,step);changes=self.progress.update(after,step);local=self.local or {};conditions=local.get('conditions',[])
        skills=self.capabilities.observe(before,action,after,result,step,local.get('capability'),conditions,neutral)
        values=[condition_value(after,c) for c in conditions];previous=[condition_value(before,c) for c in conditions]
        attained=bool(values) and all(v is True for v in values) and not all(v is True for v in previous) and result.get('status')=='updated'
        learned=bool(self.capabilities.matching(local.get('capability'),after,conditions)) or bool(self.capabilities.matching(local.get('capability'),before,conditions))
        progressed=attained or any(c['status']=='confirmed' for c in changes)
        coverage=sum(v is not None for v in values)/len(values) if values else 1.
        comparison=self.observer.observe(after,result,ms,progressed,step,coverage)
        self.recent.append({'node':after['id'],'action':action['id'],'progress':progressed,'step':step})
        self.failures=self.classify(after,result,receipt)
        if self.recovery:
            r=self.recovery;r['attempted_actions'].append(action['id']);r['attempted_actions']=r['attempted_actions'][-12:]
            restored_measurement=r['failure'] in OBSERVATION_FAILURES and after.get('quality',0)>r['start_quality']+.1 and after.get('measurement_status')=='ok'
            recovered=attained or learned and r['failure'] in ('capability_missing','skill_condition_mismatch') or restored_measurement or progressed
            if recovered or step>=r['deadline']:
                self.history.append({'event':'recovery_finished','status':'verified_recovery' if recovered else 'expired_try_other_hypothesis','step':step,'method':r['method'],'resume':r['resume']['id']})
                self.local=r['resume'];self.recovery=None;self.goal_epoch+=1
                if self.pending_local:
                    self.local=self.pending_local;self.pending_local=None
                    self.history.append({'event':'supervisor_local_refinement','step':step,'id':self.local['id']})
        if attained:
            self.history.append({'event':'local_condition_observed','id':local.get('id'),'step':step,'not_campaign_completion':True});self.local=None;self.goal_epoch+=1
        elif self.local and step>=self.local['deadline']:
            # Keep the mission; replace the means instead of losing the objective.
            self.local['deadline']=step+24
            if not self.failures:self.failures=['unclassified']
        self.ensure_local(after,step);self.new_recovery(after,step);self.save()
        return {'event':'autonomy_update','step':step,'state':self.context(after),'progress_changes':changes,'skill_updates':skills,'observer_comparison':comparison,'recent_events':list(self.history)[-4:]}
    def context(self,state=None):
        state=state or self.last_state;node=self.progress.current();cap=self.local.get('capability') if self.local else None
        return {'phase':node['label'] if node else '進行証拠と操作能力を獲得する','current_goal':copy.deepcopy(self.local),
          'session_goal':{'id':node['id'],'label':node['label']} if node and node['scope']=='session' else None,
          'parent_goal':{'id':node['id'],'label':node['label'],'status':node['status'],'source':node['source'],'evidence':node['evidence'][-2:],'unknown':node['unknown']} if node else None,
          'learning_task_status':'hypotheses_exhausted_seek_new_evidence' if self.local and digest([self.local['id'],scope(state)]) in self.exhausted else 'finite_trial' if self.recovery else 'execution' if self.local else 'unconfirmed',
          'required_capabilities':[cap] if cap else [],'missing_capabilities':self.capabilities.missing([cap],state,self.local['conditions']) if cap and state else [],
          'failure_classes':list(self.failures),'recovery':{k:v for k,v in self.recovery.items() if k not in ('resume','attempted_actions')} if self.recovery else None,
          'observation_policy':self.observer.summary(),'skills':self.capabilities.summary(state) if state else [],
          'progress':self.progress.summary()['nodes'][-16:],'goal_epoch':self.goal_epoch,'evidence_policy':'資料/推定/保持値は現在の確定観測ではない。局所条件の成立とゲームクリアは別。'}
