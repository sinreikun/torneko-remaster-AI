"""Evidence-backed, game-independent progression. Proposals are never facts."""
import copy
from .model import value_at
from .state import digest

CAPABILITIES=('close_interface','change_selection','confirm','move','cross_obstacle',
              'approach','acquire','verify_possession','handle_target','find_exit','transition','inspect','enter_text','select_target','advance_interface','use_item','equip','inspect_inventory','recover')

def validate_condition(c):
    if not isinstance(c,dict) or set(c)!={'feature','operator','value'}:raise ValueError('Invalid evidence condition')
    f=c['feature'];op=c['operator'];v=c['value']
    if not isinstance(f,str) or not (f in ('text','selection_text','selection_id','interface_open','map_observed') or f.startswith('metric.') and len(f.split('.'))==3):raise ValueError('Unmeasurable evidence feature')
    if op not in ('eq','ne','contains','not_contains','ge','le'):raise ValueError('Invalid evidence operator')
    if type(v) not in (str,int,float,bool) or isinstance(v,str) and not 0<len(v)<=160:raise ValueError('Invalid evidence value')
    if type(v) is float:
        import math
        if not math.isfinite(v):raise ValueError('Nonfinite evidence')
    if f in ('text','selection_text','selection_id') and (not isinstance(v,str) or op not in ('contains','not_contains','eq','ne')):raise ValueError('Invalid text evidence')
    if f in ('interface_open','map_observed') and (type(v) not in (bool,int) or v not in (0,1) or op not in ('eq','ne')):raise ValueError('Invalid interface evidence')
    if f.startswith('metric.') and (type(v) not in (int,float) or op not in ('eq','ne','ge','le')):raise ValueError('Invalid metric evidence')
    return copy.deepcopy(c)

def condition_value(state,c):
    if c['feature']=='map_observed':
        value=1 if state.get('minimap',{}).get('status')=='observed' else None
    else:value=value_at(state,c['feature'])
    if value is None:return None
    wanted=c['value'];op=c['operator']
    try:return bool(wanted in value if op=='contains' else wanted not in value if op=='not_contains' else value==wanted if op=='eq' else value!=wanted if op=='ne' else value>=wanted if op=='ge' else value<=wanted)
    except TypeError:return None

def channel(c):
    f=c['feature']
    return 'ocr' if f in ('text','selection_text','selection_id') or f.startswith('metric.') else 'map' if f=='map_observed' else 'geometry'

def validate_nodes(nodes,known=()):
    if not isinstance(nodes,list) or len(nodes)>12:raise ValueError('Progress proposal limit')
    result=[];ids=set(known)
    for n in nodes:
        required={'id','label','parent','requires','scope','conditions','counterconditions','capabilities','source'}
        if not isinstance(n,dict) or set(n)!=required:raise ValueError('Invalid progression node')
        for k in ('id','label','parent','source'):
            if not isinstance(n[k],str) or len(n[k])>240 or k in ('id','label') and not n[k]:raise ValueError('Invalid progression text')
        if n['id'] in ids:raise ValueError('Duplicate progression node')
        ids.add(n['id'])
        if n['scope'] not in ('campaign','session'):raise ValueError('Invalid progression scope')
        if not isinstance(n['requires'],list) or len(n['requires'])>8 or any(not isinstance(x,str) for x in n['requires']):raise ValueError('Invalid dependencies')
        if not isinstance(n['capabilities'],list) or len(n['capabilities'])>4 or any(c not in CAPABILITIES for c in n['capabilities']):raise ValueError('Invalid capabilities')
        for k in ('conditions','counterconditions'):
            if not isinstance(n[k],list) or len(n[k])>4 or k=='conditions' and not n[k]:raise ValueError('Evidence conditions required')
            for c in n[k]:validate_condition(c)
        if any(c in n['counterconditions'] for c in n['conditions']):raise ValueError('Identical success and counterevidence conditions')
        result.append(copy.deepcopy(n))
    all_nodes={n['id']:n for n in result}
    for n in result:
        if any(x not in ids or x==n['id'] for x in n['requires']):raise ValueError('Unknown/cyclic prerequisite')
        if n['parent'] and (n['parent'] not in ids or n['parent']==n['id']):raise ValueError('Unknown parent')
    def visit(key,path):
        if key in path:raise ValueError('Progression cycle')
        if key not in all_nodes:return
        n=all_nodes[key]
        for dep in n['requires']+([n['parent']] if n['parent'] else []):visit(dep,path|{key})
    for key in all_nodes:visit(key,set())
    return result

class ProgressManager:
    def __init__(self,store):
        self.store=store;self.session=store.session;self.nodes={n['id']:n for n in store.load('progress',limit=128)};self.changed=False;self.intent_tag=None
    def propose(self,nodes,origin='llm_hypothesis'):
        additions=validate_nodes(nodes,self.nodes)
        if len(self.nodes)+len(additions)>128:raise ValueError('Progress graph capacity')
        def semantic_key(n):return digest([n['scope'],n['parent'],sorted(n['requires']),sorted(digest(c) for c in n['conditions']),sorted(digest(c) for c in n['counterconditions'])])
        for n in additions:
            same=next((old for old in self.nodes.values() if old.get('status')=='disputed' and semantic_key(old)==semantic_key(n)),None)
            if same:raise ValueError('Equivalent contradicted progression conditions already exist: '+same['id']+'; inspect observed counterevidence or change prerequisites')
        for n in additions:
            n.update(status='unconfirmed',intent_tag=self.intent_tag,revision=0,origin=origin,evidence=[],counterevidence=[],session=self.session,unknown=[c['feature'] for c in n['conditions']])
            self.nodes[n['id']]=n;self.save(n)
        return len(additions)
    def save(self,n):self.store.put('progress',n,key=n['id'])
    def update(self,state,step):
        changes=[];ordered=[];visited=set()
        def visit(key):
            if key in visited or key not in self.nodes:return
            visited.add(key)
            for dependency in self.nodes[key]['requires']:visit(dependency)
            ordered.append(self.nodes[key])
        for key in self.nodes:visit(key)
        for n in ordered:
            old=copy.deepcopy(n)
            if n['scope']=='session' and n.get('session')!=self.session:
                n.update(status='unconfirmed',evidence=[],counterevidence=[],session=self.session)
            visible='\n'.join(state.get('quotes',[])).casefold() if state.get('measurement_status')=='ok' else ''
            n['unverified_literals']=[v for v in n.get('unverified_literals',[]) if v.casefold() not in visible]
            values=[condition_value(state,c) for c in n['conditions']]
            counter=[condition_value(state,c) for c in n['counterconditions']]
            n['unknown']=[c['feature'] for c,v in zip(n['conditions'],values) if v is None]+['literal_source:'+v for v in n.get('unverified_literals',[])]
            source=state['frame_sha256']
            # Only independent, current source frames count; cached OCR replays do not.
            if all(v is True for v in values) and not any(v is True for v in counter):
                if not any(e['frame']==source for e in n['evidence']):
                    n['evidence'].append({'frame':source,'step':step,'session':self.session,'channels':sorted({channel(c) for c in n['conditions']}),'conditions':copy.deepcopy(n['conditions']),'revision':n.get('revision',0),'ui_generation':1 if state.get('ui_visual') else 0});n['evidence']=n['evidence'][-8:]
            if any(v is True for v in counter):
                if not any(e['frame']==source for e in n['counterevidence']):
                    n['revision']=n.get('revision',0)+1
                    n['counterevidence'].append({'frame':source,'step':step,'session':self.session,'conditions':[c for c,v in zip(n['counterconditions'],counter) if v is True]});n['counterevidence']=n['counterevidence'][-8:]
                n['status']='disputed'
            else:
                relevant=[e for e in n['evidence'] if e.get('revision',0)==n.get('revision',0) and (not any(c['feature'] in ('selection_text','selection_id') for c in n['conditions']) or e.get('ui_generation')==1)]
                enough=len(relevant)>=2
                diverse=len({channel(c) for c in n['conditions']})>=2
                prerequisites=all(self.nodes.get(k,{}).get('status')=='confirmed' for k in n['requires'])
                n['status']='confirmed' if enough and prerequisites and (n['scope']=='session' or diverse) else 'disputed' if n.get('counterevidence') and not enough else 'supported' if relevant else 'unconfirmed'
            if n!=old:
                self.save(n)
                if old['status']!=n['status']:changes.append({'id':n['id'],'old':old['status'],'status':n['status'],'step':step})
        self.changed=bool(changes)
        return changes
    def available(self):
        return [n for n in self.nodes.values() if (self.intent_tag is None or n.get('intent_tag')==self.intent_tag) and not n.get('unverified_literals') and n['status'] not in ('confirmed','disputed') and all(self.nodes.get(k,{}).get('status')=='confirmed' for k in n['requires'])]
    def current(self):
        available=self.available();parents={n['parent'] for n in available if n['parent']}
        # Work on actionable leaves before their higher-level mission nodes.
        return next((n for n in available if n['id'] not in parents),next(iter(available),None))
    def summary(self):
        current=self.current()
        return {'current':copy.deepcopy(current),'nodes':[{'id':n['id'],'label':n['label'],'status':n['status'],'scope':n['scope'],'source':n['source']} for n in self.nodes.values()],
                'available':[n['id'] for n in self.available()],'knowledge_is_evidence':False}
    def select_spatial(self,view,local,policy=None):
        """Choose purpose, not keys; priorities are profile data, not engine law."""
        policy=policy or {};local=local or {}
        turn=view.get('stamp',{}).get('turn',0)
        deferred=getattr(self,'spatial_deferred',{})
        self.spatial_deferred={k:v for k,v in deferred.items() if v['until']>turn}
        if local.get('conditions') or view.get('status')!='ready':return None
        kinds={'acquire':['item'],'handle_target':['enemy'],'find_exit':['stairs','portal'],'transition':['stairs','portal'],'cross_obstacle':['obstacle']}.get(local.get('capability'))
        priorities=policy.get('priorities',{'frontier':1,'portal':1,'obstacle':.1})
        candidates=[t for t in view.get('targets',[]) if t['id'] not in self.spatial_deferred and (not kinds or t['kind'] in kinds) and priorities.get(t['kind'],1 if kinds else 0)>0 and t['distance']<=policy.get('max_distance',{}).get(t['kind'],100000)]
        if not candidates:return None
        previous=getattr(self,'spatial_goal',None)
        if previous is None:
            rows=self.store.load('autonomy','spatial_goal');previous=rows[0].get('target') if rows else None
        best=min(candidates,key=lambda t:(-priorities.get(t['kind'],1),int(t['kind']=='frontier' and t['confidence']<.6),t['distance']+(1-t['confidence'])*3,t['id']))
        incumbent=next((t for t in candidates if previous and t['id']==previous['id'] and t['distance']>0),None)
        if incumbent and (priorities.get(incumbent['kind'],1)>=priorities.get(best['kind'],1) or incumbent.get('stage')=='reprobe' and best['kind']=='frontier') and not(incumbent['kind']==best['kind']=='frontier' and incumbent['confidence']<.6<=best['confidence']):best=incumbent
        self.spatial_goal=copy.deepcopy(best)
        self.store.put('autonomy',{'target':best,'parent':(self.current() or {}).get('id'),'basis':'position_goal_not_item_acquisition_or_campaign_completion'},key='spatial_goal')
        return copy.deepcopy(best)

    def defer_spatial(self,target,turn,reason):
        if not hasattr(self,'spatial_deferred'):self.spatial_deferred={}
        self.spatial_deferred[target['id']]={'until':turn+8,'reason':reason}
        if len(self.spatial_deferred)>256:self.spatial_deferred.pop(next(iter(self.spatial_deferred)))
    def spatial_feedback(self,record,turn):
        target=record.get('target')
        if not target:return
        # Reaching an icon is not acquisition or a successful interaction.
        key=target['id'];old=getattr(self,'spatial_arrival_trials',{})
        if len(target.get('path',[]))==1 and record.get('receipt',{}).get('os_submission')=='accepted' and record.get('outcome')!='region_changed':
            old[key]=old.get(key,0)+1
            if old[key]>=3:self.defer_spatial(target,turn,'interaction_effect_unconfirmed');old.pop(key,None)
        else:old.pop(key,None)
        self.spatial_arrival_trials=dict(list(old.items())[-256:])
