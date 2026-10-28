"""Single-input skills, scoped by observed conditions and falsifiable effects."""
import copy
from .progress import CAPABILITIES,condition_value
from .state import digest


def scope(state):
    return {'interface':state.get('interface_hypothesis'),
            'condition':state.get('interface_layout') if state.get('interface_hypothesis') else state.get('context')}

def test_effect(before,after,name,conditions=()):
    if min(before.get('quality',0),after.get('quality',0))<.5:return None
    if conditions:
        a=[condition_value(before,c) for c in conditions];b=[condition_value(after,c) for c in conditions]
        if any(v is None for v in b):return None
        if all(v is True for v in a):return None # Already true, not action success.
        return all(v is True for v in b)
    if name in ('close_interface','confirm'):
        if before.get('interface_observation',{}).get('visible') is not True:return None
        visible=after.get('interface_observation',{}).get('visible')
        return not visible if type(visible) is bool else None
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
        names=list(dict.fromkeys(['close_interface','change_selection','move','transition']+([requested] if requested in CAPABILITIES else [])))
        for name in names:
            checks=list(conditions) if name==requested else []
            outcome=test_effect(before,after,name,checks)
            # Known natural motion/UI changes cannot establish an input skill.
            feature={'move':('dx','dy'),'close_interface':('interface_changed',),'change_selection':('text_changed',),'transition':('floor_changed',),'inspect':('text_changed',)}.get(name,())
            if neutral and any(neutral.get(k) for k in feature):outcome=None
            if outcome is None:continue
            applicability=scope(before);key=digest([name,applicability,action['id'],checks])
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
        return sorted([s for s in self.skills.values() if s['name']==name and s['scope']==scope(state) and s['status']=='supported_conditionally' and (conditions is None or s['conditions']==list(conditions))],key=lambda s:-s['confidence'])
    def missing(self,names,state,conditions=()):return [n for n in names if not self.matching(n,state,conditions)]
    def priors(self,name,state,candidates,conditions=()):
        allowed={c['id'] for c in candidates}
        return [{'id':s['input']['id'],'value':min(1.5,s['confidence']),'reason':'同じ観測条件で成功した技能 '+name} for s in self.matching(name,state,conditions)[:3] if s['input']['id'] in allowed]
    def summary(self,state):
        return [{'name':s['name'],'confidence':round(s['confidence'],3),'status':s['status'],'applicable':s['scope']==scope(state),'successes':len(s['successes']),'failures':len(s['failures'])} for s in list(self.skills.values())[-24:]]
