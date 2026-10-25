"""Tentative screen-only odometry and frontier routing. Never actual game coordinates."""
from collections import deque
import hashlib
import io
import statistics
import uuid
from .controls import REFERENCE, is_torneko
from .interfaces import torneko_menu_kind, result_screen, PROTECTED_CONFIRMATION

DIRECTIONS={'w':(0,-1),'d':(1,0),'s':(0,1),'a':(-1,0)}

def motion(before,after,neutral):
    """Conservative multi-patch camera translation, independent of input receipt.

    Fixed-camera sprite motion is deliberately unknown rather than invented.
    All distances are normalized screen pixels, not game tiles.
    """
    from PIL import Image, ImageChops, ImageStat
    try:
        frames=[Image.open(io.BytesIO(p)).convert('L').resize((160,90)) for p in (before,after,neutral)]
    except Exception:return {'status':'unknown','reason':'unreadable_image'}
    a,b,n=frames
    votes=[];still=[]
    for x,y in ((12,24),(62,24),(112,24),(12,58),(62,58),(112,58)):
        box=(x,y,x+22,y+16);patch=a.crop(box)
        if ImageStat.Stat(patch).stddev[0]<12:continue
        def error(frame,dx,dy):
            return ImageStat.Stat(ImageChops.difference(patch,frame.crop((x+dx,y+dy,x+dx+22,y+dy+16)))).mean[0]
        base=error(b,0,0);natural=error(n,0,0)
        still.append(base<=max(2,natural*1.5))
        candidates=sorted((error(b,dx,dy),dx,dy) for dx in range(-10,11,2) for dy in range(-10,11,2))
        best,dx,dy=candidates[0]
        alternatives=[e for e,u,v in candidates if abs(u-dx)+abs(v-dy)>=4]
        if base>max(4,natural*2) and best<base*.55 and min(alternatives,default=best)>best*1.15:
            votes.append((dx,dy))
    if len(votes)>=3:
        dx=statistics.median(v[0] for v in votes);dy=statistics.median(v[1] for v in votes)
        support=sum(abs(u-dx)<=2 and abs(v-dy)<=2 for u,v in votes)
        if support>=3 and max(abs(dx),abs(dy))>=2:
            return {'status':'translated','shift':[dx,dy],'support':support,'units':'160x90_screen_pixels'}
    if len(still)>=4 and all(still):return {'status':'stationary','reason':'textured_background_stable_not_proof_of_wall'}
    return {'status':'unknown','reason':'no_coherent_background_translation'}

class MaplessNavigation:
    def __init__(self,cfg):
        self.cfg=cfg;self.revision=None;self.reset('session_start')
        bindings=cfg.key_bindings or (REFERENCE['keys'] if is_torneko(cfg.window_title) else {})
        self.keys={}
        for direction,label in zip(DIRECTIONS,('上','右','下','左')):
            self.keys[direction]=next((k for k,v in bindings.items() if (v.startswith(label+'移動') or v.casefold()=={'w':'up','a':'left','s':'down','d':'right'}[direction]) and k in cfg.allowed_keys),None)
        self.confirm=next((k for k,v in bindings.items() if ('決定' in v or 'confirm' in v.lower()) and k in cfg.allowed_keys),None)
        self.face='num5' if is_torneko(cfg.window_title) and 'num5' in cfg.allowed_keys else None
    def reset(self,reason):
        self.scope=uuid.uuid4().hex[:12];self.pose=(0,0);self.nodes={(0,0):{'visits':1}};self.edges={};self.trials={};self.obstacles=set();self.samples=[];self.pending=None;self.interacted=set();self.uncertain=False;self.mode='校正中';self.active=False;self.reason=reason;self.last={};self.landmarks={};self.assumed_mode=False;self.assumption_history=False;self.real_map_available=False;self.frame_evidence={};self.last_movement={};self.last_interaction={};self.region_label=None;self.portal_edges={}
    @staticmethod
    def signature(png):
        try:
            from PIL import Image
            return list(Image.open(io.BytesIO(png)).convert('L').resize((32,18)).crop((0,4,32,18)).getdata())
        except Exception:return None
    def locate(self,png):
        if self.assumption_history:return
        signature=self.signature(png)
        if signature is None:return
        if self.uncertain:
            if len(self.samples)<3:return
            matches=[c for c,s in self.landmarks.items() if sum(abs(a-b) for a,b in zip(s,signature))/len(s)<1.5]
            if len(matches)==1:
                self.pose=matches[0];self.uncertain=False;self.reason='unique_previous_view_relocalized';self.pending=None
        else:self.landmarks[self.pose]=signature
    def prepare(self,state,native,world,revision):
        if self.revision is not None and revision!=self.revision:self.reset('manual_control_or_pause')
        self.revision=revision
        if (state or {}).get('floor_confirmation',{}).get('changed') or world.get('new_adventure'):self.reset('confirmed_region_change')
        region=(state or {}).get('exterior_action_policy',{}).get('location')
        if self.region_label in ('outside','dungeon') and region in ('outside','dungeon') and region!=self.region_label:
            self.reset('current_displays_region_changed')
        self.region_label=region
        text='\n'.join(l.get('text','') for l in (native or {}).get('lines',[]))
        # Any independent text outside the HUD can be dialogue. Suspend routing,
        # not the game; the existing interface/LLM policy remains responsible.
        dialogue=any(sum(c.isalpha() for c in l.get('text',''))>=8 and not any(t in l.get('text','') for t in ('HP','満腹','Lv')) for l in (native or {}).get('lines',[]))
        self.active=bool((state or {}).get('minimap',{}).get('status')!='observed' and not torneko_menu_kind(native) and not result_screen(native) and not PROTECTED_CONFIRMATION.search(text) and not dialogue and all(self.keys.values()))
        scope=(state or {}).get('exterior_action_policy',{})
        self.assumed_mode=bool(is_torneko(self.cfg.window_title) and scope.get('active') and scope.get('mode')=='exit_exploration')
        self.frame_evidence=(state or {}).get('white_frame_evidence',{})
        self.real_map_available=(state or {}).get('minimap',{}).get('status')=='observed'
        if self.assumed_mode:self.assumption_history=True
        if self.assumed_mode:
            self.active=bool((state or {}).get('minimap',{}).get('status')!='observed' and all(self.keys.values()) and self.frame_evidence.get('visible') is False)
            self.reason='user_assumption_ready' if self.active else 'white_frame_or_image_unavailable'
        if is_torneko(self.cfg.window_title) and self.frame_evidence.get('visible'):
            self.active=False;self.reason='white_frame_geometry_suspended'
        if region=='dungeon':self.active=False
        if not self.active:self.pending=None
        return self.snapshot()
    def propose(self,allow_facing=True):
        if not self.active or self.uncertain:return None
        if self.pending:
            direction,stage=self.pending
            if stage=='face' and self.face and allow_facing:return [self.face,self.keys[direction]],'相互作用前の向き確認'
            if stage in ('face','confirm') and self.confirm:return [self.confirm],'通過できない方向への決定入力を1回確認'
            return [self.keys[direction]],'決定入力後の通過再確認'
        # BFS through observed edges to the nearest untried frontier. Revisit
        # only when it is transit to a frontier, never as an exploration reward.
        queue=deque([(self.pose,[])]);seen={self.pose};portals=[]
        while queue:
            node,path=queue.popleft()
            for (origin,direction),data in self.portal_edges.items():
                if origin==node and direction in self.keys:portals.append((data.get('visits',1),len(path),path,direction))
            for d,delta in DIRECTIONS.items():
                dest=(node[0]+delta[0],node[1]+delta[1])
                if dest not in self.nodes and self.trials.get((node,d),0)<3 and (node,d) not in self.portal_edges:
                    if path:self.mode='復路';return [self.keys[path[0]]],'未訪問分岐への既知経路（再訪）'
                    self.mode='未訪問優先';return [self.keys[d]],'出口探索のため未訪問方向を試す'
            for d,delta in DIRECTIONS.items():
                dest=(node[0]+delta[0],node[1]+delta[1])
                if (node,d) in self.edges and dest not in seen:seen.add(dest);queue.append((dest,path+[d]))
        if portals:
            visits,distance,path,direction=min(portals,key=lambda p:(p[0],p[1]))
            self.mode='既知の場面切替へ';return [self.keys[path[0] if path else direction]],'未探索分岐を確認できないため、記録済みの別マップへの接続へ向かう'
        self.mode='再照合';return None
    def record(self,before,after,neutral,row,same_frame=True):
        keys=row.get('observed_keys',[])
        if self.assumption_history or self.assumed_mode or row.get('exterior_action_policy',{}).get('active'):
            comparable=bool(same_frame and row.get('executed') and row.get('turn_kind')!='aborted' and (row.get('input_receipt') or {}).get('os_submission')=='accepted')
            return self.record_assumed(before,after,neutral,row,allow_update=bool(self.active and self.assumed_mode and comparable),comparable=comparable)
        if not self.active:return self.snapshot()
        if not same_frame or not row.get('executed') or row.get('turn_kind')=='aborted' or (row.get('input_receipt') or {}).get('os_submission')!='accepted':
            self.pending=None;self.uncertain=True;self.reason='input_or_frame_not_comparable';return self.snapshot()
        previous=self.pose;stage=self.pending
        if stage and len(keys)==2 and self.face in keys:
            self.pending=(stage[0],'confirm');self.mode='相互作用確認';return self.snapshot()
        if stage and keys==[self.confirm]:
            self.pending=(stage[0],'move');self.mode='相互作用確認';return self.snapshot()
        direction=next((d for d,k in self.keys.items() if keys==[k]),None)
        if direction is None:
            self.pending=None
            # Untracked movement combinations invalidate relative position.
            if any(k in keys for k in self.keys.values()):self.uncertain=True
            return self.snapshot()
        evidence=motion(before,after,neutral);self.last={'before_sha256':hashlib.sha256(before).hexdigest(),'after_sha256':hashlib.sha256(after).hexdigest(),'motion':evidence,'keys':keys,'hold_ms':row.get('hold_ms'),'wait_ms':row.get('wait_ms'),'from':list(previous)}
        edge=(previous,direction);self.trials[edge]=self.trials.get(edge,0)+1
        delta=DIRECTIONS[direction]
        if evidence['status']=='translated':
            dx,dy=evidence['shift'];along=-(dx*delta[0]+dy*delta[1]);cross=abs(dx*delta[1]-dy*delta[0])
            if along<2 or cross>2:self.uncertain=True;self.reason='translation_conflicts_with_input'
            elif self.samples and abs(along-statistics.median(self.samples))>max(2,statistics.median(self.samples)*.3):self.uncertain=True;self.reason='movement_unit_mismatch'
            else:
                self.samples=(self.samples+[along])[-12:]
                if self.uncertain:
                    # A measurable input after an unmeasurable one does not
                    # recover the old coordinate. Recalibrate and begin a new
                    # relative origin, without inventing a link to old cells.
                    self.reason='movement_calibrating_after_unknown_pose'
                    if len(self.samples)>=3:
                        samples=list(self.samples);last=self.last;old_scope=self.scope
                        self.reset('reanchored_after_unobserved_movement')
                        self.active=True;self.samples=samples;self.last=last
                        self.last.update(previous_scope=old_scope,pose_update='new_relative_origin_no_link_to_previous_map')
                    self.last['to']=list(self.pose)
                    return self.snapshot()
                dest=(previous[0]+delta[0],previous[1]+delta[1]);self.pose=dest
                self.nodes.setdefault(dest,{'visits':0})['visits']+=1
                self.edges[edge]={'to':list(dest),'evidence':self.last}
                inverse=next(d for d,v in DIRECTIONS.items() if v==(-delta[0],-delta[1]));self.edges[(dest,inverse)]={'to':list(previous),'reverse_hypothesis':True}
                self.obstacles.discard(dest);self.pending=None
        elif evidence['status']=='stationary':
            if len(self.samples)<3:
                # Stable background is not a collision sensor. Before camera
                # motion has been calibrated it cannot establish blocked cells
                # or justify a door-probe sequence at this estimated coordinate.
                self.uncertain=True;self.pending=None;self.reason='stationary_without_movement_calibration'
                self.last['to']=list(self.pose)
                return self.snapshot()
            if stage and stage[1]=='move':self.trials[edge]=3;self.pending=None;self.edges.pop(edge,None)
            elif self.trials[edge]>=2:
                self.obstacles.add((previous[0]+delta[0],previous[1]+delta[1]))
                self.pending=(direction,'face') if self.confirm and edge not in self.interacted else None
                if self.pending:self.interacted.add(edge)
        else:self.uncertain=True;self.pending=None;self.reason='movement_unobservable'
        if len(self.nodes)>1024:self.uncertain=True;self.reason='bounded_map_limit'
        self.last['to']=list(self.pose)
        return self.snapshot()
    def record_assumed(self,before,after,neutral,row,allow_update=True,comparable=True):
        from .exterior_screen import assumed_change
        keys=row.get('observed_keys',[])
        direction=next((d for d,k in self.keys.items() if keys==[k]),None)
        action_kind='movement' if direction else 'confirm' if keys==[self.confirm] else 'other'
        evidence=assumed_change(before,after,neutral)
        if evidence.get('white_frames'):self.frame_evidence=evidence['white_frames'][-1]
        previous=self.pose
        current={'step':row.get('step'),'action_kind':action_kind,'before_sha256':hashlib.sha256(before).hexdigest(),
            'after_sha256':hashlib.sha256(after).hexdigest(),'keys':list(keys),'hold_ms':row.get('hold_ms'),'wait_ms':row.get('wait_ms'),
            'from':list(previous),'to':list(previous),'input_pair_comparable':comparable,
            'screen_change':{k:evidence.get(k) for k in ('large_change','changed_fraction','natural_fraction','large_change_threshold')},
            'white_frame_observation':{'frames':evidence.get('white_frames',[]),'transition':evidence.get('frame_transition','unknown')},
            'movement':{'status':'not_applicable','success':None},'motion':{'status':'not_movement'}}
        self.last=current
        if action_kind!='movement':
            if action_kind=='other' and any(k in keys for k in self.keys.values() if k):self.uncertain=True
            if action_kind=='confirm':self.last_interaction=current
            if self.pending and action_kind=='confirm' and allow_update and evidence['status'] not in ('interface','unknown'):
                self.pending=(self.pending[0],'move');self.mode='通過再確認'
            else:self.pending=None
            if evidence['status']=='interface':self.active=False;self.reason='interface_response_not_movement'
            return self.snapshot()
        self.last_movement=current
        current['motion']=evidence
        if not comparable:
            current['movement']={'status':'not_observed','success':None,'reason':'input_pair_not_comparable'}
            self.pending=None;self.uncertain=True;return self.snapshot()
        if evidence['status'] in ('unknown','interface'):
            current['movement']={'status':'suspended_interface' if evidence['status']=='interface' else 'unknown','success':None}
            self.pending=None;self.active=False
            if evidence['status']=='unknown':self.uncertain=True
            self.reason=evidence['reason'];return self.snapshot()
        if not allow_update:
            current['movement']={'status':'not_observed','success':None,'reason':'movement_estimation_not_active'}
            self.pending=None;self.uncertain=True;return self.snapshot()
        current['movement']={'status':evidence['status'],'success':evidence['status']=='assumed_moved'}
        delta=DIRECTIONS[direction];dest=(previous[0]+delta[0],previous[1]+delta[1]);edge=(previous,direction)
        if self.uncertain:
            # Resume the user's relative-step model from a new origin rather
            # than joining it to coordinates whose input history was lost.
            last=self.last;self.reset('new_origin_for_assumed_steps');self.active=True;self.assumed_mode=True;self.assumption_history=True;self.last=last;self.last_movement=last
            previous=self.pose;dest=(previous[0]+delta[0],previous[1]+delta[1]);edge=(previous,direction)
            self.last['from']=list(previous)
        self.trials[edge]=self.trials.get(edge,0)+1
        self.nodes[previous]['surface']='floor'
        if evidence['status']=='assumed_moved':
            self.pose=dest;self.uncertain=False;self.nodes.setdefault(dest,{'visits':0,'surface':'floor'})['visits']+=1
            self.edges[edge]={'to':list(dest),'basis':evidence['basis'],'evidence':self.last}
            inverse=next(d for d,v in DIRECTIONS.items() if v==(-delta[0],-delta[1]))
            self.edges[(dest,inverse)]={'to':list(previous),'reverse_hypothesis':True,'basis':evidence['basis']}
            self.obstacles.discard(dest);self.pending=None;self.reason='large_change_assumed_step'
        else:
            self.obstacles.add(dest);self.edges.pop(edge,None);self.reason='small_change_assumed_obstacle'
            if self.pending and self.pending[1]=='move':self.trials[edge]=3;self.pending=None
            elif self.confirm and edge not in self.interacted:
                self.interacted.add(edge);self.pending=(direction,'confirm');self.mode='通過確認'
            else:self.trials[edge]=3;self.pending=None
        self.last['to']=list(self.pose)
        if len(self.nodes)>1024:self.uncertain=True;self.reason='bounded_map_limit'
        return self.snapshot()
    def snapshot(self):
        return {'source':'tentative_screen_route','active':self.active,'display_retained':self.assumption_history and not self.active and not self.real_map_available,'scope':self.scope,'pose':list(self.pose),'pose_uncertain':self.uncertain,'movement_basis':'user_assumption_screen_change' if self.assumption_history else 'background_translation','white_frame_evidence':self.frame_evidence,'unit':'移動入力1回＝仮の1マス' if self.assumption_history else '仮の入力移動単位（実マス未保証）','calibration':'利用者指定の仮定（校正不要）' if self.assumption_history else '校正中' if len(self.samples)<3 else '反復変位を照合済み','mode':'再定位が必要' if self.uncertain else self.mode,'local_goal':'現在領域の出口を探す（全体目標は維持）','reason':self.reason,'cells':[{'cell':list(c),'visits':v['visits'],'surface':v.get('surface','passage_hypothesis')} for c,v in self.nodes.items()],'obstacles':[list(c) for c in sorted(self.obstacles)],'edges':[{'from':list(c),'direction':d,**v} for (c,d),v in self.edges.items()],'portals':[{'cell':list(c),'direction':d,**v} for (c,d),v in self.portal_edges.items()],'last':self.last,'last_movement':self.last_movement,'last_interaction':self.last_interaction,'pending':list(self.pending) if self.pending else None}
