"""Hierarchical supervision: acquire means, diagnose, test, and resume goals."""
import copy,time
from collections import deque
from .progress import ProgressManager,condition_value,CAPABILITIES
from .capabilities import CapabilityManager,scope
from .observer_adaptation import ObserverAdaptation
from .state import digest
from .ui_probes import UIProbes

FAILURES=('observation_missing','observation_conflict','state_identification_failure',
          'world_prediction_failure','capability_missing','skill_condition_mismatch',
          'input_not_delivered','repetition_stagnation','progress_unknown','goal_unreachable',
          'llm_measurement_mismatch','ui_observation_missing','unclassified')
OBSERVATION_FAILURES={'observation_missing','observation_conflict','state_identification_failure','world_prediction_failure','llm_measurement_mismatch','ui_observation_missing'}

class MetaController:
    def __init__(self,store,intent,reference_knowledge=""):
        self.recovery_budgets={};self.ui_probes=UIProbes();self.store=store;self.progress=ProgressManager(store);self.capabilities=CapabilityManager(store);self.observer=ObserverAdaptation(store)
        self.reference_text=(intent+"\n"+reference_knowledge).casefold()
        self.progress.intent_tag=digest(intent)
        self.intent=intent;self.local=None;self.recovery=None;self.pending_local=None;self.history=deque(maxlen=32);self.failures=[];self.attempted={};self.recent=deque(maxlen=20);self.last_scope=None;self.phase='understanding';self.proposal_requests=0;self.planning_missing=0;self.planning_issue=None;self.completed_local={};self.plan_attempts=0;self.last_proposal=-1000;self.goal_epoch=0;self.last_state={};self.last_changes=[];self.exhausted=set();self.deferred=[];self.nonexecutions=0;self.restore_local=None;self.boundary={'conditions':[],'frames':[],'armed':True}
        rows=store.load('autonomy','manager')
        if rows and rows[0].get('intent')==intent:
            saved=rows[0];self.recovery_budgets=saved.get('recovery_budgets',{});self.ui_probes=UIProbes(saved.get('ui_probe_attempts'));self.history.extend(saved.get('history',[]));self.goal_epoch=saved.get('goal_epoch',0)
            self.restore_local={'local':saved.get('local'),'scope':saved.get('scope')}
            self.boundary=saved.get('boundary',self.boundary)
            self.completed_local=saved.get('completed_local',{});self.planning_missing=saved.get('planning_missing',0);self.planning_issue=saved.get('planning_issue')
            self.attempted=saved.get('attempted',{});self.deferred=saved.get('deferred',[])[-16:]
        self.save()
    def save(self):
        self.store.put('autonomy',{'ui_probe_attempts':self.ui_probes.attempts,'intent':self.intent,'local':self.local,'recovery':self.recovery,'history':list(self.history),'goal_epoch':self.goal_epoch,'completed_local':self.completed_local,'planning_missing':self.planning_missing,'planning_issue':self.planning_issue,'session':self.store.session,'boundary':self.boundary,'attempted':self.attempted,'recovery_budgets':self.recovery_budgets,'deferred':self.deferred[-16:],'scope':scope(self.last_state) if self.last_state else None},key='manager')
    def needs_plan(self,step):
        exhausted=bool(self.local and self.recovery_exhaustion_key() in self.exhausted)
        return (not self.progress.nodes or not self.progress.available() or exhausted or self.planning_missing>0) and step-self.last_proposal>=(16 if self.planning_missing or not self.progress.nodes or exhausted else 128)
    def planning_context(self,state,step):
        c=self.context(state)
        # Capability identifiers are protocol labels, not text to find in the
        # game. Keep the identifier separate from the user-facing local goal.
        goal=c.get('current_goal')
        if goal and goal.get('origin')=='capability_acquisition':
            labels={'inspect':'現在画面から入力の作用を確認できる情報を得る','change_selection':'選択を示す要素が入力で移動するか確認する','confirm':'現在項目への決定入力の作用を確認する','move':'入力による位置の変化を確認する','advance_interface':'文章を進める入力の作用を確認する'}
            goal['label']=labels.get(goal.get('capability'),'現在の観測で必要な操作の効果を検証する')
            goal['identifier_rule']='capability/idは内部識別子。画面内文字の証拠ではない。選択対象は現在の位置付きOCRから選ぶ。'
        c['unverified_goal_literals']=[{'id':n['id'],'values':n.get('unverified_literals',[])} for n in self.progress.nodes.values() if n.get('unverified_literals')][:8]
        c['blocked_progress']=[{k:n.get(k) for k in ('id','label','conditions','counterconditions','counterevidence')} for n in self.progress.nodes.values() if n.get('status')=='disputed'][-3:]
        for node in c['blocked_progress']:node['counterevidence']=node.get('counterevidence',[])[-1:]
        c['skills']=c['skills'][-4:];c['progress']=c['progress'][-8:]
        c['observation_policy']={'settings':self.observer.policy,'trial':(self.observer.summary()['trial'] or {}).get('item')}
        if self.needs_plan(step):
            c['planning_material']={'long_term_intent':self.intent[:6000],
             'instruction':'意図から前提付き進行ノードを最大3件提案。見た/取得/所持/帰還/イベントを混同しない。資料は仮説。今検証できない条件は未確認のまま。現在の能力と場面から到達可能な目標を優先。'}
            self.last_proposal=step;self.plan_attempts+=1
        return c
    def planning_feedback(self,plan,step,error=None):
        if not plan.get('progress_nodes'):
            self.planning_issue=str(error or '進行案がありません。自己参照を避け、可視の検証条件を持つ新しいノードを提案してください。')[:500]
            self.planning_missing+=1
            self.history.append({'event':'progress_generation_missing','step':step,'failures':self.planning_missing,'next':'進行計画を明示要求し再生成'})
        else:self.planning_missing=0;self.planning_issue=None
        self.save()
    def ungrounded_literals(self,node,state):
        visible='\n'.join(state.get('quotes',[])).casefold() if state.get('measurement_status')=='ok' else ''
        internal={node.get('label',''),node.get('id',''),node.get('source','')}|set(CAPABILITIES)
        observed=[c for e in node.get('evidence',[]) for c in e.get('conditions',[])]
        return [c['value'] for c in node['conditions'] if c['feature'] in ('text','selection_text') and c['operator'] in ('contains','eq') and isinstance(c['value'],str) and c['value'] in internal and c['value'].casefold() not in visible and c['value'].casefold() not in self.reference_text and c not in observed]
    def accept(self,plan,state,step):
        if plan.get('session_boundary') and not self.boundary['conditions']:
            self.boundary={'conditions':copy.deepcopy(plan['session_boundary']),'frames':[],'armed':True}
        proposals=plan.get('progress_nodes',[])
        if proposals:
            try:
                count=self.progress.propose(proposals)
                visible='\n'.join(state.get('quotes',[])).casefold()
                for proposal in proposals:
                    n=self.progress.nodes[proposal['id']]
                    n['literal_grounding_version']=2
                    n['unverified_literals']=self.ungrounded_literals(n,state)
                    self.progress.save(n)
                self.history.append({'event':'progress_proposed','count':count,'step':step,'status':'hypotheses_not_facts'})
            except ValueError as error:
                self.planning_missing+=1;self.planning_issue=str(error);self.last_proposal=step
                self.history.append({'event':'progress_rejected','reason':str(error),'step':step})
        # A brief plan is a local suggestion, never a campaign completion report.
        targets=[{k:t[k] for k in ('feature','operator','value')} for t in plan.get('targets',[]) if t.get('feature')!='node']
        if targets and not all(condition_value(state,c) is True for c in targets):
            proposed={'id':'local:'+digest(targets),'label':plan['objective'],'conditions':targets,'capability':self.infer_capability(targets,state) if any(c['feature'] in ('interface_open','selection_text') for c in targets) else plan.get('required_capability') or self.infer_capability(targets,state),'origin':'llm_hypothesis','created':step,'deadline':step+32,'parent':(self.progress.current() or {}).get('id'),'start_frame':state['frame_sha256']}
            if any(c['feature']=='selection_text' for c in targets):proposed['ui_scope']=state.get('ui_visual',{}).get('layout')
            existing=self.pending_local or self.local
            if existing and self.recovery_goal_key(existing)==self.recovery_goal_key(proposed):
                self.history.append({'event':'equivalent_local_goal_retained','step':step,'id':existing['id'],'deadline':existing['deadline']})
            elif self.recovery:self.pending_local=proposed
            else:self.local=proposed;self.goal_epoch+=1
        self.save()
    @staticmethod
    def infer_capability(targets,state):
        if any(t['feature']=='interface_open' and t['value']==0 for t in targets):return 'close_interface'
        if any(t['feature']=='selection_text' for t in targets):return 'change_selection'
        return 'confirm' if state.get('interface_hypothesis') else 'move' if state.get('minimap',{}).get('player_cell') else 'inspect'
    @staticmethod
    def bootstrap_capability(state):
        flow=state.get('ui_visual',{}).get('interaction_model',{})
        if flow.get('selection_requirement')=='not_required_for_supported_text_flow':return 'advance_interface'
        selected=state.get('selection_observation',{});ui=state.get('ui_visual',{})
        located=bool(selected.get('status')=='supported_association' and selected.get('element_id')) if ui else selected.get('line_index') is not None
        return 'change_selection' if (state.get('interface_hypothesis') or ui.get('active')) and located else 'move' if not state.get('interface_hypothesis') and state.get('minimap',{}).get('player_cell') else 'inspect'
    def ensure_local(self,state,step):
        node=self.progress.current()
        if self.local and self.local.get('origin')=='progress_graph' and self.progress.nodes.get(self.local.get('parent'),{}).get('unverified_literals'):
            self.history.append({'event':'ungrounded_progress_goal_deferred','step':step,'id':self.local['id']})
            self.deferred.append(copy.deepcopy(self.local));self.deferred=self.deferred[-16:]
            self.local=None;self.recovery=None;self.pending_local=None
        if self.local and self.local.get('origin')=='capability_acquisition':
            expected=self.bootstrap_capability(state)
            if self.local['capability']!=expected:
                self.history.append({'event':'capability_observation_condition_mismatch','step':step,'old':self.local['capability'],'new':expected})
                self.local=None;self.recovery=None
        if self.local and not (node and self.local.get('origin')=='capability_acquisition') and (self.local.get('parent') is None or self.progress.nodes.get(self.local['parent'],{}).get('status') not in ('confirmed','disputed')):return
        if node:
            cap=next(iter(node['capabilities']),'inspect')
            self.local={'id':'node:'+node['id'],'label':node['label'],'parent':node['id'],'conditions':copy.deepcopy(node['conditions']),'capability':cap,'origin':'progress_graph','created':step,'deadline':step+48,'start_frame':state['frame_sha256']}
        else:
            cap=self.bootstrap_capability(state)
            conditions=[{'feature':'interface_open','operator':'eq','value':0}] if cap=='close_interface' else []
            self.local={'id':'ability:'+cap,'label':'現在の観測条件で操作の効果を検証する: '+cap,'parent':None,'conditions':conditions,'capability':cap,'origin':'capability_acquisition','created':step,'deadline':step+24,'start_frame':state['frame_sha256']}
        self.goal_epoch+=1
    def classify(self,state,result=None,receipt=None,plan=None):
        reasons=[];result=result or {}
        if self.nonexecutions>=3:reasons.append('state_identification_failure')
        ui=state.get('ui_visual',{})
        if ui and (state.get('interface_hypothesis') or ui.get('active')) and ui.get('needs_observation'):reasons.append('ui_observation_missing')
        if receipt and receipt.get('os_submission') not in ('accepted','unknown'):reasons.append('input_not_delivered')
        if state.get('measurement_status')!='ok' or state.get('quality',0)<.5:reasons.append('observation_missing')
        if state.get('floor_confirmation',{}).get('status')=='pending':reasons.append('observation_conflict')
        if state.get('state_identification')=='appearance_text_hypothesis' and state.get('quality',0)<.7:reasons.append('state_identification_failure')
        if result.get('match') is False:reasons.append('world_prediction_failure')
        if any(h.get('status')=='contradicted' for h in result.get('hypothesis_checks',[])):reasons.append('llm_measurement_mismatch')
        if result.get('loop') or ui.get('interaction_model',{}).get('cycle_detected'):reasons.append('repetition_stagnation')
        if len(self.recent)>=12 and len({x['node'] for x in self.recent})<=3 and not any(x['progress'] for x in self.recent):reasons.append('repetition_stagnation')
        if self.local:
            cap=self.local['capability'];conditions=self.local['conditions']
            if cap not in CAPABILITIES:reasons.append('unclassified')
            elif not ('ui_observation_missing' in reasons and cap in ('change_selection','select_target','confirm','enter_text')) and not self.capabilities.matching(cap,state,conditions):
                reasons.append('skill_condition_mismatch' if any(s['name']==cap and s['status']=='supported_conditionally' for s in self.capabilities.skills.values()) else 'capability_missing')
            if self.local.get('parent') and self.progress.nodes.get(self.local['parent'],{}).get('status')=='disputed':reasons.append('goal_unreachable')
        if self.planning_missing or not self.progress.nodes or (self.progress.current() or {}).get('unknown'):reasons.append('progress_unknown')
        elif not self.progress.available() and any(n['status']!='confirmed' for n in self.progress.nodes.values()):reasons.append('goal_unreachable')
        return list(dict.fromkeys(reasons))
    @staticmethod
    def recovery_goal_key(goal):
        # Screen/OCR jitter and renamed LLM proposals cannot renew a mission's
        # recovery budget. Only a different measurable task gets a new budget.
        return digest([goal.get('capability'),sorted(digest(c) for c in goal.get('conditions',[]))])
    def recovery_exhaustion_key(self):return self.recovery_goal_key(self.local or {})
    def new_recovery(self,state,step):
        if self.recovery or not self.failures:return
        primary=self.failures[0];kind='observer_trial' if primary in OBSERVATION_FAILURES else 'alternative_skill' if primary in ('capability_missing','skill_condition_mismatch','repetition_stagnation') else 'gather_progress_evidence' if primary in ('progress_unknown','goal_unreachable') else 'verify_backend' if primary=='input_not_delivered' else 'reidentify'
        # Rotate finite hypotheses after failed attempts; never reset the same budget.
        variants=[kind,'observer_trial','alternative_skill','replan']
        for method in dict.fromkeys(variants):
            key=digest(['recovery_budget_v2',self.recovery_goal_key(self.local),method])
            if key in self.recovery_budgets or len(self.recovery_budgets)>=128:continue
            self.recovery_budgets[key]={'step':step,'method':method,'goal':self.recovery_goal_key(self.local),'status':'started'}
            self.attempted[key]=step
            while len(self.attempted)>128:self.attempted.pop(next(iter(self.attempted)))
            self.recovery={'id':key,'failure':primary,'method':method,'resume':copy.deepcopy(self.local),'start':step,'deadline':step+8,'wall_deadline':time.monotonic()+45,'start_state':state['id'],'start_quality':state.get('quality',0),'start_ui_coverage':state.get('ui_visual',{}).get('coverage',0),'start_conditions':[condition_value(state,c) for c in self.local['conditions']],'attempted_actions':[],'success_test':'元目標の新しい成立、能力獲得、または欠測解消。画面差だけは達成にしない。'}
            if method=='observer_trial':self.observer.start(primary,step)
            if method=='replan':self.last_proposal=-1000
            self.history.append({'event':'recovery_started','step':step,'failure':primary,'method':method,'resume':self.local['id']});self.save();return
        exhausted_key=self.recovery_exhaustion_key()
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
    def check_local_ui(self,state,step):
        goal=self.local
        if not goal or goal.get('origin')!='llm_hypothesis':return
        targets=[c for c in goal.get('conditions',[]) if c['feature']=='selection_text' and c['operator'] in ('eq','contains')]
        if not targets:return
        ui=state.get('ui_visual',{});layout=ui.get('layout')
        if state.get('measurement_status')!='ok' or state.get('quality',0)<.7 or not layout or not ui.get('elements'):return
        texts=[e.get('text','') for e in ui['elements'] if isinstance(e.get('text'),str)]
        present=all(any(c['value']==t if c['operator']=='eq' else c['value'] in t for t in texts) for c in targets)
        if present:
            goal.setdefault('ui_scope',layout);goal.pop('ui_scope_check',None);return
        if goal.get('ui_scope')==layout:
            goal.pop('ui_scope_check',None);return # A scrolled/off-screen item may still belong here.
        watch=goal.get('ui_scope_check',{})
        if watch.get('layout')!=layout:watch={'layout':layout,'frames':[]}
        if state['frame_sha256'] not in watch['frames']:watch['frames'].append(state['frame_sha256'])
        goal['ui_scope_check']=watch
        if len(watch['frames'])<2:return
        saved=copy.deepcopy(goal);saved['deferred_reason']='selection_ui_scope_mismatch'
        self.deferred.append(saved);self.deferred=self.deferred[-16:]
        self.history.append({'event':'local_ui_scope_mismatch','step':step,'id':goal['id'],'frames':watch['frames'][-2:],'status':'deferred_not_disproved'})
        self.local=self.pending_local;self.pending_local=None;self.recovery=None;self.goal_epoch+=1

    def prepare(self,state,step):
        self.observer.expire()
        if self.recovery and time.monotonic()>=self.recovery['wall_deadline']:
            r=self.recovery;self.local=r['resume'];self.recovery=None
            self.history.append({'event':'recovery_finished','status':'wall_deadline_try_other_hypothesis','method':r['method'],'resume':self.local['id']})
        if self.recovery and self.recovery['method']=='observer_trial' and not self.observer.trial:self.observer.start(self.recovery['failure'],step)
        visible='\n'.join(state.get('quotes',[])).casefold()
        for n in self.progress.nodes.values():
            if n.get('literal_grounding_version',0)<2:
                n['literal_grounding_version']=2
                n['unverified_literals']=self.ungrounded_literals(n,state)
                self.progress.save(n)
        self.last_state=state;self.check_boundary(state,step);self.last_changes=self.progress.update(state,step)
        if self.restore_local:
            saved=self.restore_local
            if saved['local'] and saved['scope']==scope(state):
                self.local=copy.deepcopy(saved['local']);self.local['created']=step;self.local['deadline']=step+24
                self.history.append({'event':'goal_revalidated_after_restart','id':self.local['id']})
            self.restore_local=None
        self.check_local_ui(state,step)
        # Retire a visible target outside every currently observed UI panel.
        # Absence/unknown selection alone is not evidence against a target.
        panels=state.get('interface_observation',{}).get('rectangles',[])
        if self.local and self.local.get('origin')=='llm_hypothesis' and panels:
            for condition in self.local.get('conditions',[]):
                if condition['feature']!='selection_text' or condition['operator'] not in ('eq','contains'):continue
                matches=[e for e in state.get('ui_visual',{}).get('elements',[]) if isinstance(e.get('text'),str) and condition['value'] in e['text']]
                if matches and all(not any(r[0]<=e['center'][0]<=r[2] and r[1]<=e['center'][1]<=r[3] for r in panels) for e in matches):
                    self.deferred.append(copy.deepcopy(self.local));self.deferred=self.deferred[-16:]
                    self.history.append({'event':'local_selection_outside_ui_panels','step':step,'goal':self.local['id']})
                    self.local=None;self.recovery=None;self.pending_local=None;self.goal_epoch+=1;break
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
        # Penalize observed recurrent edges, not previously visited floor cells.
        if 'repetition_stagnation' in self.failures:
            for e in list(self.recent)[-12:]:
                if e['node']==state['id'] or e.get('origin')==state['id']:
                    values[e['action']]={'id':e['action'],'value':-2,'reason':'同じ局所結果を繰り返し、進行も新知識も得られていない経路'}
        if self.recovery and self.recovery['method'] in ('alternative_skill','replan'):

            for action in self.recovery['attempted_actions'][-6:]:values[action]={'id':action,'value':-1.5,'reason':'有限復旧中に試した入力。別候補を比較する'}
        interaction=state.get('ui_visual',{}).get('interaction_model',{})
        action_stats={a['id']:a for a in interaction.get('actions',[])}
        if 'ui_observation_missing' in self.failures:
            for candidate in candidates:
                meanings={m.strip().casefold() for m in candidate.get('declared_meanings',[])}
                if len(candidate['keys'])==1 and meanings&{'up','down','left','right','上移動','下移動','左移動','右移動'} and action_stats.get(candidate['id'],{}).get('trials',0)<2:
                    values[candidate['id']]={'id':candidate['id'],'value':.8,'reason':'方向入力の位置変化を有限回検証する。無反応の反復へ初期探索加点を与え続けない。'}
        from .ui_interactions import UIInteractionModel
        if self.local['capability'] in ('inspect','advance_interface') or not conditions:
            for value in UIInteractionModel.priors(state,candidates,explore='ui_observation_missing' in self.failures):values[value['id']]=value
        if 'ui_observation_missing' in self.failures:
            for value in self.ui_probes.priors(state,candidates,self.observer.policy):values[value['id']]=value
        from .ui_vision import first_ui_action
        for target in conditions:
            if target['feature'] in ('selection_text','selection_id') and target['operator'] in ('contains','eq'):
                action=first_ui_action(state.get('ui_visual',{}),target['value'],by_id=target['feature']=='selection_id')
                if action and any(c['id']==action for c in candidates):values[action]={'id':action,'value':1.5,'reason':'入力同期の選択グラフから目的の文字へ向かう最初の一手。次手は再観測する。'}
        plan['candidate_values']=list(values.values());plan['meta_goal_id']=self.local['id']
        return plan
    def observe(self,before,action,after,result,receipt,step,ms,neutral=None):
        self.ui_probes.observe(before,action,result,receipt,self.observer.policy)
        self.nonexecutions=0;self.last_state=after;self.check_boundary(after,step);changes=self.progress.update(after,step);local=self.local or {};conditions=local.get('conditions',[])
        skills=self.capabilities.observe(before,action,after,result,step,local.get('capability'),conditions,neutral)
        values=[condition_value(after,c) for c in conditions];previous=[condition_value(before,c) for c in conditions]
        attained=bool(values) and all(v is True for v in values) and not all(v is True for v in previous) and result.get('status')=='updated'
        learned=bool(self.capabilities.matching(local.get('capability'),after,conditions)) or bool(self.capabilities.matching(local.get('capability'),before,conditions))
        completion_key=digest([local.get('id'),scope(before),after.get('interface_layout')])
        fresh_completion=attained and completion_key not in self.completed_local
        if attained:self.completed_local[completion_key]=self.completed_local.get(completion_key,0)+1
        while len(self.completed_local)>128:self.completed_local.pop(next(iter(self.completed_local)))
        progressed=fresh_completion or any(c['status']=='confirmed' for c in changes)
        coverage=sum(v is not None for v in values)/len(values) if values else 1.
        if (after.get('interface_hypothesis') or after.get('ui_visual',{}).get('active')) and after.get('ui_visual'):
            coverage=min(coverage,after['ui_visual'].get('coverage',0))
        comparison=self.observer.observe(after,result,ms,progressed,step,coverage)
        self.recent.append({'origin':before['id'],'node':after['id'],'action':action['id'],'progress':progressed,'step':step})
        self.failures=self.classify(after,result,receipt)
        if self.recovery:
            r=self.recovery;r['attempted_actions'].append(action['id']);r['attempted_actions']=r['attempted_actions'][-12:]
            restored_measurement=r['failure'] in OBSERVATION_FAILURES and after.get('measurement_status')=='ok' and (after.get('quality',0)>r['start_quality']+.1 or r['failure']=='ui_observation_missing' and after.get('ui_visual',{}).get('coverage',0)>r.get('start_ui_coverage',0)+.15 and after.get('ui_visual',{}).get('selection',{}).get('status')=='supported_association')
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
          'deferred_goal':{k:self.deferred[-1].get(k) for k in ('id','label','parent','deferred_reason')} if self.deferred else None,
          'learning_task_status':'hypotheses_exhausted_seek_new_evidence' if self.local and self.recovery_exhaustion_key() in self.exhausted else 'finite_trial' if self.recovery else 'execution' if self.local else 'unconfirmed',
          'required_capabilities':[cap] if cap else [],'missing_capabilities':self.capabilities.missing([cap],state,self.local['conditions']) if cap and state else [],
          'failure_classes':list(self.failures),'recovery':{k:v for k,v in self.recovery.items() if k not in ('resume','attempted_actions')} if self.recovery else None,
          'observation_policy':self.observer.summary(),'skills':self.capabilities.summary(state) if state else [],
          'recovery_budget':{'used':len(self.recovery_budgets),'capacity':128,'capacity_reached':len(self.recovery_budgets)>=128,'identity':'measurable_goal_not_screen_or_proposal_id'},'ui_selection_experiment':self.ui_probes.summary(),'ui_interaction':state.get('ui_visual',{}).get('interaction_model',{}),'ui_observation':{'status':state.get('ui_visual',{}).get('selection',{}).get('status'),'needs':state.get('ui_visual',{}).get('needs_observation',[]),'coverage':state.get('ui_visual',{}).get('coverage')},'planning_issue':self.planning_issue,'planning_missing':self.planning_missing,'progress':self.progress.summary()['nodes'][-16:],'goal_epoch':self.goal_epoch,'evidence_policy':'資料/推定/保持値は現在の確定観測ではない。局所条件の成立とゲームクリアは別。'}
