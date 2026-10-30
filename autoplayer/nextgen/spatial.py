"""Key-free spatial evidence, bounded region maps and revision-stamped routes."""
import copy,io,time,uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from PIL import Image,ImageChops,ImageStat
from .state_hash import digest
from .interaction_surface import allows_field_input

DIRECTIONS=((0,-1),(1,0),(0,1),(-1,0))
GRID_STEPS=DIRECTIONS+((-1,-1),(1,-1),(1,1),(-1,1))
def cell(p):return tuple(map(int,p))
def tag(p):return ','.join(map(str,p))
def untag(p):return tuple(map(int,p.split(',')))
def plus(a,b):return (a[0]+b[0],a[1]+b[1])
def fingerprint(png):
    if not png:return None
    with Image.open(io.BytesIO(png)) as im:return list(im.convert('L').resize((24,18)).crop((0,4,24,18)).tobytes())
def distance(a,b):return sum(abs(x-y) for x,y in zip(a,b))/len(a) if a and b else 255

def visual_motion(before,after):
    """Translation is screen evidence, never a measured tile displacement."""
    if not before or not after:return {'status':'unknown'}
    from ..mapless_navigation import motion
    facts=motion(before,after,before);facts['neutral_source']='baseline_only_natural_motion_not_excluded'
    a,b=fingerprint(before),fingerprint(after)
    facts['difference']=distance(a,b)
    facts['black']=bool(b and sum(v<12 for v in b)/len(b)>.97)
    return facts

class SpatialModel:
    def __init__(self,saved=None):
        saved=saved or {};self.regions=saved.get('regions',{});self.portals=saved.get('portals',[]);self.active=None;self.revision=0;self.pending_entry=None;self.resume_pending=True;self.calibration=saved.get('calibration',{});self.measured_scopes=saved.get('measured_scopes',{})
    def dump(self):return {'schema':1,'regions':self.regions,'portals':self.portals[-128:],'calibration':self.calibration,'measured_scopes':self.measured_scopes}
    def region(self,key,provider):
        if hasattr(self,'writable') and key in self.regions and key not in self.writable:
            self.regions[key]=copy.deepcopy(self.regions[key]);self.writable.add(key)
        if key not in self.regions:
            self.regions[key]={'id':key,'provider':provider,'cells':{},'edges':{},'obstacles':{},'probes':{},'pose':None,'anchors':[],'icons':{},'bounds':None,'topology':4,'portal_positions':{}}
        while len(self.regions)>32:
            oldest=next(k for k in self.regions if k!=key);del self.regions[oldest]
            self.measured_scopes={k:v for k,v in self.measured_scopes.items() if v!=oldest}
        return self.regions[key]
    @staticmethod
    def evidence(state,turn,source):return {'frame':state['frame_sha256'],'turn':turn,'source':source}
    def update(self,state,turn,transition=None,png=None):
        if transition and transition.get('images') and allows_field_input(state) and allows_field_input(transition.get('before',{})):
            transition=copy.deepcopy(transition);transition['motion']=visual_motion(*transition.pop('images'))
        self.revision+=1;ev=self.evidence(state,turn,'OBSERVED');grid=state.get('minimap',{});old_active=self.active;movement={'status':'not_observed'}
        ui=not allows_field_input(state)
        measured=grid.get('status') in ('observed','retained') and grid.get('player_cell') is not None
        boundary=bool(transition and transition.get('boundary',{}).get('blackout_seen'))
        fp=fingerprint(png)
        if boundary:
            self.transition_origin=old_active;self.active=None
        if ui and self.active:
            r=self.region(self.active,self.regions[self.active]['provider'])
            t=(transition or {}).get('target') or {}
            if transition and transition.get('accepted') and transition.get('intent',{}).get('role')=='interact' and t.get('kind')=='obstacle':
                fact=r['obstacles'].get(tag(t['cell']))
                if fact:fact['interaction_tried']=True;fact['interaction_effect']='opened_ui_not_movement'
            return self.view(state,turn,r,movement,'suspended_interface')
        if not measured and (not fp or max(fp)-min(fp)<16):return self.view(state,turn,None,movement,'unknown_surface')
        if measured:
            base='measured:'+str(state.get('map_domain'))+':'+str(state.get('floor_confirmation',{}).get('confirmed'))
            pose=cell(grid['player_cell']);shape=digest(sorted(plus(pose,cell(p)) for p in grid.get('known_cells',[]))) if grid.get('known_cells') else digest(grid.get('accumulated_cells',[]))
            key=self.measured_scopes.get(base,base)
            if boundary and key==old_active and self.regions[key].get('map_signature')!=shape:key=base+':entry:'+uuid.uuid4().hex[:8]
            self.measured_scopes[base]=key
            r=self.region(key,'measured');r['topology']=state.get('spatial_topology',4);pose=cell(grid['player_cell']);source='OBSERVED' if grid['status']=='observed' else 'RETAINED'
            ev=self.evidence(state,turn,source);ev['map_frame']=grid.get('source_frame_sha256',state['frame_sha256'])
            if source=='OBSERVED':r['map_signature']=shape
            points=grid.get('accumulated_cells') or [plus(pose,cell(p)) for p in grid.get('known_cells',[])]
            for p in points if source=='OBSERVED' else []:
                p=cell(p)
                if grid.get('grid_size') and not(0<=p[0]<grid['grid_size'][0] and 0<=p[1]<grid['grid_size'][1]):continue
                r['cells'][tag(p)]={'source':'OBSERVED','kind':'floor_candidate','evidence':ev}
            if source=='OBSERVED':r['cells'][tag(pose)]={'source':source,'kind':'floor_candidate','evidence':ev}
            r['pose']=list(pose);r['bounds']=grid.get('grid_size')
            r['icons']={kind:[list(plus(pose,cell(p))) for p in grid.get(kind,[])] for kind in ('items','enemies','stairs','portals')}
        else:
            if self.active and self.regions[self.active]['provider']=='measured' and not boundary:
                return self.view(state,turn,self.regions[self.active],movement,'suspended_interface' if ui else 'measurement_missing')
            key=self.active if self.active and self.regions[self.active]['provider']=='hypothesis' and not boundary else None
            if key is None and not ui and fp and not (transition and transition.get('motion',{}).get('black')):
                matches=[]
                for rid,region in self.regions.items():
                    if region['provider']!='hypothesis':continue
                    for anchor in region['anchors']:
                        d=distance(fp,anchor['image'])
                        if d<2:matches.append((d,rid,anchor['pose']))
                matches.sort();unique=matches and (len(matches)==1 or matches[0][1:]==matches[1][1:] or matches[1][0]-matches[0][0]>2)
                if matches and not unique:return self.view(state,turn,None,{'status':'ambiguous_relocalization'},'relocalization_ambiguous')
                candidate=matches[0][1] if unique else None
                # A single image never silently restores a historical coordinate system.
                if candidate and self.pending_entry==candidate:key=candidate
                elif candidate:
                    self.pending_entry=candidate
                    return self.view(state,turn,None,{'status':'relocalization_pending'},'relocalization_pending')
                if key is None:key='hypothesis:'+uuid.uuid4().hex[:16]
                r=self.region(key,'hypothesis');r['pose']=list(matches[0][2]) if candidate==key else [0,0]
                if candidate==key:movement={'status':'relocalized','source':'INFERRED','evidence':ev}
            if key is None:
                return self.view(state,turn,None,movement,'interface_or_unknown_surface')
            r=self.region(key,'hypothesis');pose=cell(r['pose'] or [0,0]);ev=self.evidence(state,turn,'HYPOTHESIS')
            if transition and not boundary and old_active==key:
                intent=transition.get('intent',{});facts=transition.get('motion',{});before=transition.get('before',{})
                eligible=not ui and allows_field_input(before) and transition.get('accepted') and intent.get('delta') in [list(d) for d in DIRECTIONS]
                if eligible and (facts.get('status')=='translated' or transition.get('assumption') and facts.get('difference',0)>8):
                    previous=pose;pose=plus(pose,cell(intent['delta']));r['pose']=list(pose)
                    r['edges'][tag(previous)+'>'+tag(pose)]={'source':'HYPOTHESIS','evidence':ev}
                    r['edges'][tag(pose)+'>'+tag(previous)]={'source':'HYPOTHESIS','evidence':ev,'requires_reverse_verification':True}
                    r['cells'][tag(previous)]={'kind':'floor_candidate','source':'HYPOTHESIS','evidence':ev}
                    movement={'status':'hypothesized_displacement','from':list(previous),'to':list(pose),'evidence':ev,'units':'virtual_input_steps_not_game_tiles'}
                elif eligible and facts.get('status')=='stationary':
                    destination=plus(pose,cell(intent['delta']));k=tag(pose)+'>'+tag(destination)
                    r['probes'][k]=r['probes'].get(k,0)+1
                    if r['probes'][k]>=2:r['obstacles'][tag(destination)]={'source':'HYPOTHESIS','evidence':ev,'from':list(pose),'direction':intent['delta']}
                    movement={'status':'no_displacement_evidence','evidence':ev,'not_backend_failure':True}
                elif eligible:movement={'status':'unknown','evidence':ev}
            r['cells'][tag(pose)]={'kind':'floor_candidate','source':'HYPOTHESIS','evidence':ev};r['icons']={}
            if fp and not ui:
                r['anchors']=[a for a in r['anchors'] if a['pose']!=list(pose)]+[{'pose':list(pose),'image':fp,'evidence':ev}];r['anchors']=r['anchors'][-16:]
        self.active=key
        if transition and transition.get('accepted') and transition.get('intent',{}).get('role')=='interact':
            t=transition.get('target') or {}
            if t.get('kind')=='obstacle' and t.get('stamp',{}).get('region')==key:
                obstacle=r['obstacles'].get(tag(t['cell']))
                if obstacle:obstacle['interaction_tried']=True;obstacle['allow_probe']=True
        if transition and measured and old_active==key:
            before=transition['before'];a=before.get('spatial',{}).get('pose');b=r['pose'];intent=transition.get('intent',{})
            if a is not None and not ui and allows_field_input(before) and transition.get('accepted') and grid['status']=='observed':
                a=cell(a);b=cell(b);delta=cell(intent.get('delta',[0,0]));expected=plus(a,delta)
                if max(abs(b[0]-a[0]),abs(b[1]-a[1]))==1:
                    r['edges'][tag(a)+'>'+tag(b)]={'source':'EXPERIENCED','evidence':ev};r['obstacles'].pop(tag(b),None)
                    if delta in GRID_STEPS:self.calibration[tag(delta)]=min(self.calibration.get(tag(delta),100000),transition.get('hold_ms',80))
                    movement={'status':'observed_displacement','from':list(a),'to':list(b),'evidence':ev}
                elif a==b and delta in GRID_STEPS:
                    k=tag(a)+'>'+tag(expected);r['probes'][k]=r['probes'].get(k,0)+1
                    if r['probes'][k]>=2 and transition.get('hold_ms',80)>=self.calibration.get(tag(delta),140):r['obstacles'][tag(expected)]={'source':'HYPOTHESIS','evidence':ev,'from':list(a),'direction':list(delta)}
                    if tag(expected) in r['obstacles']:r['obstacles'][tag(expected)]['allow_probe']=False
                    r['edges'].pop(k,None);movement={'status':'refuted_displacement','expected':list(expected),'observed':list(b),'evidence':ev}
                elif a!=b:movement={'status':'discontinuous_pose','evidence':ev}
        portal_origin=getattr(self,'transition_origin',None) or old_active
        if portal_origin and portal_origin!=key:
            previous_region=self.region(portal_origin,self.regions[portal_origin]['provider'])
            entry=cell(previous_region['pose'] or [0,0])
            if transition and transition.get('intent',{}).get('delta'):entry=plus(entry,cell(transition['intent']['delta']))
            previous_region.setdefault('portal_positions',{})[tag(entry)]={'destination':key,'source':'HYPOTHESIS','evidence':ev}
            self.portals.append({'origin':portal_origin,'destination':key,'source':'EXPERIENCED' if boundary else 'INFERRED','evidence':ev,'boundary':boundary})
            self.transition_origin=None
        for p,fact in list(r['obstacles'].items()):
            if turn-fact['evidence']['turn']>=32:del r['obstacles'][p]
        for name,limit in (('cells',4096),('edges',8192),('probes',8192),('obstacles',4096)):
            while len(r[name])>limit:del r[name][next(iter(r[name]))]
        return self.view(state,turn,r,movement,'suspended_interface' if ui else 'ready' if r['cells'] else 'measurement_missing')
    def view(self,state,turn,r,movement,status):
        return {'stamp':{'frame':state['frame_sha256'],'turn':turn,'region':r['id'] if r else None,'revision':self.revision},'provider':r['provider'] if r else None,'status':status,'pose':copy.deepcopy(r['pose']) if r else None,'pose_source':'OBSERVED' if r and r['provider']=='measured' and state.get('minimap',{}).get('status')=='observed' else 'HYPOTHESIS' if r and r['provider']=='hypothesis' else 'RETAINED','movement':movement,'region_data':copy.deepcopy(r),'portals':copy.deepcopy(self.portals[-8:])}

class SpatialPlanner:
    @staticmethod
    def candidates(view):
        r=view.get('region_data');targets=[]
        if not r or view['status']!='ready' or r['pose'] is None:return targets
        start=cell(r['pose']);known={untag(k) for k in r['cells']};blocked={untag(k) for k in r['obstacles']}|{cell(p) for p in r['icons'].get('enemies',[])};paths={start:[start]};q=deque([start]);bounds=r['bounds']
        while q:
            a=q.popleft()
            for d in GRID_STEPS if r.get('topology')==8 else DIRECTIONS:
                b=plus(a,d)
                if d[0] and d[1] and any(p not in known or p in blocked for p in ((a[0]+d[0],a[1]),(a[0],a[1]+d[1]))):continue
                if b not in known or b in blocked or b in paths:continue
                if r['provider']=='hypothesis' and tag(a)+'>'+tag(b) not in r['edges']:continue
                paths[b]=paths[a]+[b];q.append(b)
        def add(kind,dest,path,confidence,source,probe=None):
            targets.append({'id':digest([r['id'],kind,dest,probe]),'kind':kind,'cell':list(dest),'probe':list(probe) if probe else None,'distance':len(path)-1,'path':[list(p) for p in path],'confidence':confidence,'source':source,'evidence':[r['cells'][tag(path[0])]['evidence']],'stamp':copy.deepcopy(view['stamp'])})
        for a,path in paths.items():
            neighbors=[d for d in DIRECTIONS if plus(a,d) in known]
            degree=len(neighbors)
            for d in DIRECTIONS:
                b=plus(a,d)
                if bounds and not (0<=b[0]<bounds[0] and 0<=b[1]<bounds[1]):continue
                if b in known or b in blocked:continue
                # A corridor's unknown side is not equivalent to its continuation.
                # These are ranked hypotheses, never inferred permanent walls.
                straight=degree==2 and neighbors[0]==(-neighbors[1][0],-neighbors[1][1])
                continuation=degree==1 and d==(-neighbors[0][0],-neighbors[0][1])
                confidence=.75 if continuation else .65 if degree==0 else .15 if straight else .2 if degree==1 else .25
                add('frontier',b,path+[b],confidence,'HYPOTHESIS',b)
                targets[-1]['frontier_basis']='axial_continuation' if continuation else 'isolated_unknown' if degree==0 else 'corridor_side_unverified' if straight else 'side_or_boundary_unverified'
                targets[-1]['topology_evidence']={'origin':list(a),'known_neighbor_directions':[list(v) for v in neighbors],'not_wall_confirmation':True}
        for plural,kind in (('items','item'),('enemies','enemy'),('stairs','stairs'),('portals','portal')):
            for p in r['icons'].get(plural,[]):
                p=cell(p)
                destinations=[plus(p,d) for d in (GRID_STEPS if r.get('topology')==8 else DIRECTIONS)] if kind=='enemy' else [p]
                reachable=[paths[d] for d in destinations if d in paths]
                if reachable:add(kind,p,min(reachable,key=len),.85 if view['pose_source']=='OBSERVED' else .6,'OBSERVED' if view['pose_source']=='OBSERVED' else 'RETAINED')
        for position,fact in r.get('portal_positions',{}).items():
            p=untag(position);options=[paths[p]] if p in paths else [paths[plus(p,d)]+[p] for d in DIRECTIONS if plus(p,d) in paths]
            if options:add('portal',p,min(options,key=len),.5,'HYPOTHESIS')
        # Door/obstacle candidates are not walls; Progress may request cross_obstacle.
        for p,fact in r['obstacles'].items():
            origin=cell(fact['from'])
            if origin in paths and (not fact.get('interaction_tried') or fact.get('allow_probe')):
                reprobe=bool(fact.get('allow_probe'))
                add('obstacle',untag(p),paths[origin]+[untag(p)] if reprobe else paths[origin],.3,'HYPOTHESIS',untag(p))
                targets[-1]['stage']='reprobe' if reprobe else 'interact'
        return sorted(targets,key=lambda t:(t['distance'],-t['confidence'],t['id']))[:256]

class SpatialLane:
    def __init__(self,saved=None):
        self.model=SpatialModel(saved);self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='spatial-model');self.pending=None;self.revision=0;self.region=None
    def begin(self,state,turn,control_revision=0,transition=None,png=None):
        if self.pending is not None:raise RuntimeError('Spatial ownership not joined')
        state=copy.deepcopy({k:v for k,v in state.items() if k in ('frame_sha256','map_domain','floor_confirmation','minimap','interface_hypothesis','ui_visual','spatial','spatial_topology','input_surface_observation')})
        state['ui_visual']={'active':state.get('ui_visual',{}).get('active',False)}
        self.expected=(state['frame_sha256'],turn,self.region,self.revision,control_revision)
        base=self.expected;working=copy.copy(self.model);working.regions=dict(self.model.regions);working.writable=set();working.portals=copy.deepcopy(self.model.portals);working.calibration=dict(self.model.calibration);working.measured_scopes=dict(self.model.measured_scopes)
        def job():
            view=working.update(state,turn,transition,png);view['targets']=SpatialPlanner.candidates(view)
            r=view.pop('region_data',None)
            view['map']={'cells':{k:v['source'] for k,v in r['cells'].items()},'obstacles':list(r['obstacles']),'icons':copy.deepcopy(r['icons']),'units':'map_cells' if r['provider']=='measured' else 'virtual_input_steps'} if r else {}
            return {'base':base,'view':view,'saved':working.dump(),'model':working}
        self.pending=self.pool.submit(job)
    def merge(self,frame,turn,control_revision=0):
        try:
            result=self.pending.result();expected=(frame,turn,self.region,self.revision,control_revision)
            if result['base']!=expected:raise ValueError('Stale Spatial result')
            view=result['view']
            if view['stamp']['frame']!=frame or view['stamp']['turn']!=turn or view['stamp']['revision']!=self.revision+1:raise ValueError('Spatial stamp mismatch')
            self.model=result.pop('model');self.revision=view['stamp']['revision'];self.region=view['stamp']['region'];return result
        finally:self.pending=None
    def close(self):self.pool.shutdown(wait=True,cancel_futures=True)

def current_target(state,target):return bool(target and state.get('spatial',{}).get('stamp')==target.get('stamp') and target['stamp']['frame']==state['frame_sha256'])
def trace(before,after,target,action,receipt):
    v=after.get('spatial',{});same=bool(target and target['stamp']['region']==v.get('stamp',{}).get('region'))
    path=target.get('path',[]) if target else []
    reached=same and bool(path) and v.get('pose')==path[-1]
    return {'before':before.get('spatial',{}).get('stamp'),'start':before.get('spatial',{}).get('pose'),'target':target,'input':action,'receipt':receipt,'after':v.get('stamp'),'observed_movement':v.get('movement'),'pose':v.get('pose'),'outcome':'region_changed' if target and not same else 'approached' if reached and target['kind']=='enemy' else 'reached_position_not_goal_completion' if reached else 'refuted' if v.get('movement',{}).get('status')=='refuted_displacement' else 'pending','not_campaign_completion':True}
