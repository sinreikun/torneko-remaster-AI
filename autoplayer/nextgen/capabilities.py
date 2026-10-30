"""Single-input skills, scoped by observed conditions and falsifiable effects."""
import copy
from .progress import CAPABILITIES,condition_value
from .state import digest


def scope(state,name=None):
    interaction=state.get('ui_visual',{}).get('interaction_model',{})
    if interaction.get('family') and (name=='advance_interface' or interaction.get('selection_requirement')=='not_required_for_supported_text_flow'):
        return {'visual_evidence_generation':1,'interaction_evidence_generation':1,'interface':True,'condition':interaction['family']}
    return {**({'visual_evidence_generation':1} if state.get('ui_visual') else {}),'interface':state.get('interface_hypothesis'),
            'condition':state.get('interface_layout') if state.get('interface_hypothesis') else state.get('context')}

def test_effect(before,after,name,conditions=()):
    if min(before.get('quality',0),after.get('quality',0))<.5:return None
    if name=='advance_interface':
        effect=after.get('ui_visual',{}).get('interaction_effect',{})
        if effect.get('natural_confound'):return None
        return True if effect.get('text_response') else False if effect.get('no_response') else None
    if conditions:
        a=[condition_value(before,c) for c in conditions];b=[condition_value(after,c) for c in conditions]
        if any(v is None for v in b):return None
        if all(v is True for v in a):return None # Already true, not action success.
        return all(v is True for v in b)
    if name in ('close_interface','confirm'):
        if before.get('interface_observation',{}).get('visible') is not True:return None
        visible=after.get('interface_observation',{}).get('visible')
        return not visible if type(visible) is bool else None
    if name in ('change_selection','select_target') and (before.get('ui_visual') or after.get('ui_visual')):
        a=before.get('selection_observation',{});b=after.get('selection_observation',{})
        return a.get('element_id')!=b.get('element_id') if a.get('status')==b.get('status')=='supported_association' else None
    if name=='enter_text':
        evidence=after.get('ui_visual',{}).get('last_transition',{})
        # A changed text region is an editing hypothesis, not a proven item use.
        return True if evidence.get('status')=='text_region_changed' and evidence.get('editing_region',{}).get('status')=='supported_association' and after.get('ui_visual',{}).get('grid',{}).get('status')=='hypothesis' else None
    if name=='change_selection':
        a=before.get('selection_observation',{}).get('line_index');b=after.get('selection_observation',{}).get('line_index')
        return a!=b if a is not None and b is not None else None
    if name=='move':
        a=before.get('minimap',{}).get('player_cell');b=after.get('minimap',{}).get('player_cell')
        if not a or not b or before.get('map_domain')!=after.get('map_domain') or before.get('floor_confirmation',{}).get('confirmed')!=after.get('floor_confirmation',{}).get('confirmed'):return None
        return a!=b
    if name=='inspect':
        if before.get('measurement_status')!=after.get('measurement_status') or after.get('measurement_status')!='ok':return None
        return bool(set(after.get('quotes',[]))-set(before.get('quotes',[])))
    if name=='transition':
        a=before.get('floor_confirmation',{});b=after.get('floor_confirmation',{})
        return True if b.get('status')=='changed' and a.get('confirmed')!=b.get('confirmed') else None
    # Unmeasured semantic effects are not inferred from visual change alone.
    return None

class CapabilityManager:
    def __init__(self,store):self.store=store;self.skills={s['id']:s for s in store.load('capabilities',limit=256)}
    def observe(self,before,action,after,result,step,requested=None,conditions=(),neutral=None):
        if result.get('status')!='updated':return []
        changed=[]
        names=list(dict.fromkeys(['close_interface','change_selection','move','transition','advance_interface']+([requested] if requested in CAPABILITIES else [])))
        for name in names:
            checks=list(conditions) if name==requested else []
            outcome=test_effect(before,after,name,checks)
            # Known natural motion/UI changes cannot establish an input skill.
            feature={'move':('dx','dy'),'close_interface':('interface_changed',),'change_selection':('text_changed',),'transition':('floor_changed',),'inspect':('text_changed',),'advance_interface':('ui_text_content_changed',)}.get(name,())
            if neutral and any(neutral.get(k) for k in feature):outcome=None
            if outcome is None:continue
            applicability=scope(before,name);key=digest([name,applicability,action['id'],checks])
            skill=self.skills.setdefault(key,{'id':key,'name':name,'scope':applicability,'input':copy.deepcopy(action),'conditions':checks,
              'successes':[],'failures':[],'trials':0,'confidence':0.,'unknown_conditions':['未観測の画面・配置への転用'],'interrupt':['手動介入','条件不一致','観測欠測'],'causality':'observed_input_outcome_association'})
            evidence={'before':before['frame_sha256'],'after':after['frame_sha256'],'step':step,'session':self.store.session}
            bucket=skill['successes' if outcome else 'failures']
            if not any(e['before']==evidence['before'] and e['after']==evidence['after'] and e['session']==evidence['session'] for e in bucket):
                bucket.append(evidence);del bucket[:-12];skill['trials']+=1
            s=len(skill['successes']);f=len(skill['failures']);skill['confidence']=(s+1)/(s+f+2)
            skill['status']='supported_conditionally' if s>=2 and skill['confidence']>=.65 else 'under_review' if f else 'candidate'
            self.store.put('capabilities',skill,step,key=key);changed.append(key)
        while len(self.skills)>256:self.skills.pop(next(iter(self.skills)))
        return changed
    def matching(self,name,state,conditions=None):
        return sorted([s for s in self.skills.values() if s['name']==name and s['scope']==scope(state,name) and s['status']=='supported_conditionally' and (conditions is None or s['conditions']==list(conditions))],key=lambda s:-s['confidence'])
    def missing(self,names,state,conditions=()):return [n for n in names if not self.matching(n,state,conditions)]
    def priors(self,name,state,candidates,conditions=()):
        allowed={c['id'] for c in candidates}
        return [{'id':s['input']['id'],'value':min(1.5,s['confidence']),'kind':'capability_evidence','reason':'同じ観測条件で成功した技能 '+name} for s in self.matching(name,state,conditions)[:3] if s['input']['id'] in allowed]
    def summary(self,state):
        return [{'name':s['name'],'confidence':round(s['confidence'],3),'status':s['status'],'applicable':s['scope']==scope(state,s['name']),'successes':len(s['successes']),'failures':len(s['failures'])} for s in list(self.skills.values())[-24:]]
    @staticmethod
    def spatial_intent(action):
        import re
        directions={'up':(0,-1),'down':(0,1),'left':(-1,0),'right':(1,0),'上移動':(0,-1),'下移動':(0,1),'左移動':(-1,0),'右移動':(1,0)}
        meanings=[m.strip().casefold() for m in action.get('declared_meanings',[])]
        if meanings and all(m in directions for m in meanings):
            return {'role':'move','delta':[sum(directions[m][0] for m in meanings),sum(directions[m][1] for m in meanings)],'source':'user_binding_hypothesis'}
        if len(action.get('keys',[]))==1 and any(re.search(r'決定|confirm|interact',m) for m in meanings):return {'role':'interact','source':'user_binding_hypothesis'}
        return {'role':'other'}
    def realize_spatial(self,state,target,candidates):
        from .spatial import current_target
        if not current_target(state,target):return {'status':'stale','candidate_ids':[]}
        path=target['path'];ids=[];capability='move'
        if len(path)>1:
            delta=[path[1][i]-path[0][i] for i in (0,1)]
            ids=[a['id'] for a in candidates if self.spatial_intent(a).get('delta')==delta]
        elif target['kind'] in ('obstacle','portal','item','stairs'):
            capability='cross_obstacle' if target['kind']=='obstacle' else 'acquire' if target['kind']=='item' else 'transition'
            ids=[a['id'] for a in candidates if self.spatial_intent(a)['role']=='interact']
        elif target['kind']=='enemy':
            capability='handle_target'
            ids=[s['input']['id'] for s in self.matching(capability,state) if any(a['id']==s['input']['id'] for a in candidates)]
        return {'status':'ready' if ids else 'capability_missing','capability':capability,'candidate_ids':ids,'target':target,'basis':'known_skill_or_bounded_user_binding_trial_not_proven_effect'}
