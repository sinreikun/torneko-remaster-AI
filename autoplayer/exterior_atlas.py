"""Bounded exterior segments, entry-image matching and portal observations."""
import hashlib
import io
import json
import sqlite3
import time
import uuid
from PIL import Image,ImageChops,ImageStat


def signature(png):
    try:
        image=Image.open(io.BytesIO(png)).convert('RGB').resize((64,36))
        return list(image.tobytes())
    except Exception:return None


def dark_signature(values):
    if not values:return False
    brightness=[max(values[i:i+3]) for i in range(0,len(values),3)]
    return sum(v<24 for v in brightness)/len(brightness)>.92 and sum(brightness)/len(brightness)<18


def similarity(a,b):
    if not a or not b or len(a)!=len(b):return 999.
    errors=[]
    for row in range(6):
        for col in range(8):
            pixels=[abs(a[(y*64+x)*3+c]-b[(y*64+x)*3+c]) for y in range(row*6,(row+1)*6) for x in range(col*8,(col+1)*8) for c in range(3)]
            errors.append(sum(pixels)/len(pixels))
    # Tolerate a few animated patches, require the rest of the layout to agree.
    return sum(sorted(errors)[:43])/43


def continuity(before,after):
    """A conservative scene-break signal, not movement distance or direction."""
    try:
        a,b=[Image.open(io.BytesIO(p)).convert('L').resize((128,72)) for p in (before,after)]
    except Exception:return {'status':'unknown'}
    delta=ImageChops.difference(a,b);values=delta.tobytes()
    fraction=sum(v>22 for v in values)/len(values);mean=sum(values)/len(values)
    if fraction<.3:return {'status':'continuous','changed_fraction':round(fraction,4),'mean_difference':round(mean,3)}
    brightness_offset=ImageStat.Stat(b).mean[0]-ImageStat.Stat(a).mean[0]
    match_image=b.point(lambda v:min(255,max(0,round(v-brightness_offset)))) if abs(brightness_offset)>8 else b
    matches=[];textured=0
    for x,y in ((26,24),(54,24),(82,24),(26,46),(54,46),(82,46)):
        patch=a.crop((x,y,x+18,y+12))
        if ImageStat.Stat(patch).stddev[0]<10:continue
        textured+=1
        candidates=[]
        for dx in range(-24,25,4):
            for dy in range(-20,21,4):
                if x+dx<0 or y+dy<0 or x+dx+18>128 or y+dy+12>72:continue
                error=ImageStat.Stat(ImageChops.difference(patch,match_image.crop((x+dx,y+dy,x+dx+18,y+dy+12)))).mean[0]
                candidates.append((error,dx,dy))
        if not candidates:continue
        error,dx,dy=min(candidates)
        refined=[]
        for u in range(dx-3,dx+4):
            for v in range(dy-3,dy+4):
                if x+u<0 or y+v<0 or x+u+18>128 or y+v+12>72:continue
                e=ImageStat.Stat(ImageChops.difference(patch,match_image.crop((x+u,y+v,x+u+18,y+v+12)))).mean[0]
                refined.append((e,u,v))
        error,dx,dy=min(refined)
        if error<10:matches.append((dx,dy,error))
    support=max((sum(abs(u-dx)<=3 and abs(v-dy)<=3 for u,v,e in matches) for dx,dy,error in matches),default=0)
    broken=fraction>.65 and mean>24 and support<3 and textured>=4 and ImageStat.Stat(b).stddev[0]>10
    return {'status':'discontinuous' if broken else 'continuous' if support>=3 else 'unknown',
        'changed_fraction':round(fraction,4),'mean_difference':round(mean,3),'coherent_patches':support,'brightness_offset':round(brightness_offset,3),
        'basis':'background_patch_correspondence_not_game_coordinates'}


def graph(nav):
    return {'revision':1,'cells':[{'cell':list(c),**v} for c,v in nav.nodes.items()],
        'edges':[{'cell':list(c),'direction':d,**{k:v[k] for k in ('to','basis','reverse_hypothesis') if k in v}} for (c,d),v in nav.edges.items()],
        'portals':[{'cell':list(c),'direction':d,**v} for (c,d),v in nav.portal_edges.items()],
        'trials':[[list(c),d,n] for (c,d),n in nav.trials.items()],'obstacles':[list(c) for c in nav.obstacles]}


class ExteriorAtlas:
    def __init__(self,memory=None,path=None,profile='session'):
        self.db=memory.db if memory else sqlite3.connect(str(path) if path else ':memory:')
        self.owned=not bool(memory);self.profile=memory.profile if memory else profile
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS exterior_maps_v1(profile TEXT,id TEXT,data TEXT,updated REAL,PRIMARY KEY(profile,id));
            CREATE TABLE IF NOT EXISTS exterior_entries_v1(profile TEXT,map_id TEXT,id TEXT,signature TEXT,pose TEXT,image BLOB,updated REAL,PRIMARY KEY(profile,map_id,id));
            CREATE TABLE IF NOT EXISTS exterior_portals_v1(profile TEXT,id TEXT,data TEXT,updated REAL,PRIMARY KEY(profile,id));
        ''')
        self.map_id=None;self.entry_id=None;self.reused=False;self.awaiting=False;self.portal=None;self.latest={};self.previous_region=None;self.last_frame=None;self.nav_scope=None
    def save(self,nav):
        if not self.map_id:return
        self.db.execute('INSERT OR REPLACE INTO exterior_maps_v1 VALUES(?,?,?,?)',(self.profile,self.map_id,json.dumps(graph(nav)),time.time()));self.db.commit()
    def _entry(self,image,neutral,nav):
        a,b=signature(image),signature(neutral)
        if not a or not b or dark_signature(a) or dark_signature(b) or similarity(a,b)>4:
            return {'ready':False,'status':'awaiting_stable_entry'}
        candidates=[]
        for mid,eid,raw,pose in self.db.execute('SELECT map_id,id,signature,pose FROM exterior_entries_v1 WHERE profile=?',(self.profile,)):
            if self.portal and mid==self.portal.get('origin_map'):continue
            try:saved=json.loads(raw);entry_pose=json.loads(pose)
            except (ValueError,TypeError):continue
            if not isinstance(saved,list) or len(saved)!=len(a) or not all(type(v) is int and 0<=v<=255 for v in saved) or not isinstance(entry_pose,list) or len(entry_pose)!=2 or not all(type(v) is int for v in entry_pose):continue
            score=max(similarity(a,saved),similarity(b,saved))
            if score<=4:candidates.append((score,mid,eid,entry_pose))
        candidates.sort();chosen=candidates[0] if candidates and (len(candidates)==1 or candidates[1][0]-candidates[0][0]>=2) else None
        if chosen:
            score,mid,eid,pose=chosen
            row=self.db.execute('SELECT data FROM exterior_maps_v1 WHERE profile=? AND id=?',(self.profile,mid)).fetchone()
            if row:
                try:
                    saved=json.loads(row[0])
                    if saved.get('revision')!=1 or not 1<=len(saved['cells'])<=1024:raise ValueError('unsupported atlas graph')
                    nodes={tuple(c['cell']):{k:v for k,v in c.items() if k!='cell'} for c in saved['cells']}
                    edges={(tuple(e['cell']),e['direction']):{k:v for k,v in e.items() if k not in ('cell','direction')} for e in saved['edges']}
                    trials={(tuple(c),d):min(n,1) for c,d,n in saved['trials']}
                    obstacles={tuple(c) for c in saved['obstacles']}
                    if not all(len(c)==2 and all(type(v) is int for v in c) for c in nodes):raise ValueError('invalid atlas coordinates')
                    nav.reset('saved_entry_image_match');nav.nodes=nodes;nav.edges=edges;nav.trials=trials;nav.obstacles=obstacles
                    nav.portal_edges={(tuple(p['cell']),p['direction']):{k:v for k,v in p.items() if k not in ('cell','direction')} for p in saved.get('portals',[])}
                    nav.pose=tuple(pose);nav.nodes.setdefault(nav.pose,{'visits':1,'surface':'floor'})
                    self.map_id=mid;self.entry_id=eid;self.reused=True
                except (ValueError,TypeError,KeyError):chosen=None
        if not chosen or self.map_id is None:
            self.map_id=uuid.uuid4().hex[:16];self.entry_id=uuid.uuid4().hex[:12];self.reused=False;nav.reset('new_exterior_entry')
            thumb=Image.open(io.BytesIO(image)).convert('RGB');thumb.thumbnail((320,180));out=io.BytesIO();thumb.save(out,format='PNG')
            self.db.execute('INSERT INTO exterior_entries_v1 VALUES(?,?,?,?,?,?,?)',(self.profile,self.map_id,self.entry_id,json.dumps(a),json.dumps([0,0]),out.getvalue(),time.time()))
        nav.active=True;nav.assumed_mode=True;nav.assumption_history=True;nav.region_label='outside';nav.uncertain=False;nav.nodes[nav.pose]['surface']='floor'
        self.save(nav)
        if self.portal:
            self.portal.update(status='resolved',destination_map=self.map_id,destination_entry=self.entry_id,destination_pose=list(nav.pose))
            self._link_origin()
            self.db.execute('INSERT OR REPLACE INTO exterior_portals_v1 VALUES(?,?,?,?)',(self.profile,self.portal['id'],json.dumps(self.portal),time.time()));self.db.commit();self.portal=None
        self.awaiting=False;self.last_frame=image;self.nav_scope=nav.scope;self._bound()
        return {'ready':True,'status':'restored_entry' if self.reused else 'new_entry','entry_match_score':chosen[0] if chosen and self.reused else None}
    def prepare(self,image,neutral,state,nav):
        scope=state.get('exterior_action_policy',{});region=scope.get('location')
        if region=='dungeon':
            if self.portal:
                self.portal.update(status='resolved',destination_region='dungeon')
                self._link_origin()
                self.db.execute('INSERT OR REPLACE INTO exterior_portals_v1 VALUES(?,?,?,?)',(self.profile,self.portal['id'],json.dumps(self.portal),time.time()));self.db.commit()
            self.portal=None;self.awaiting=False;self.map_id=None;self.entry_id=None;self.previous_region=region
            self.latest={'ready':True,'status':'dungeon_handoff'};return self.context()
        if self.previous_region=='dungeon' or self.map_id and self.nav_scope and nav.scope!=self.nav_scope:self.map_id=None;self.entry_id=None
        self.previous_region=region
        if not scope.get('active'):self.latest={'ready':True,'status':'inactive_region'};return self.context()
        visibility=state.get('white_frame_evidence',{}).get('visible')
        if visibility is True:
            nav.active=False;self.latest={'ready':True,'status':'entry_interface_pending' if self.awaiting else 'interface'};return self.context()
        if dark_signature(signature(image)) or dark_signature(signature(neutral)):
            if self.map_id is None and not self.awaiting:
                nav.active=False;self.latest={'ready':True,'status':'unclassified_dark_start'};return self.context()
            if not self.awaiting:self.start_boundary(image,image,nav,{'trigger':'dark_current_frame'})
            nav.active=False;self.latest={'ready':False,'status':'awaiting_visible_entry'};return self.context()
        if visibility is not False:
            self.latest={'ready':not self.awaiting,'status':'unreadable_entry'};return self.context()
        if self.map_id and not self.awaiting and self.last_frame and continuity(self.last_frame,image)['status']=='discontinuous':
            self.start_boundary(self.last_frame,image,nav,{'trigger':'between_turn_background_discontinuity'})
        if self.map_id is None or self.awaiting:self.latest=self._entry(image,neutral,nav)
        else:self.latest={'ready':True,'status':'active_segment'}
        if not self.latest['ready']:nav.active=False
        return self.context()
    def _link_origin(self):
        origin=self.portal.get('origin_map') if self.portal else None
        if not origin:return
        row=self.db.execute('SELECT data FROM exterior_maps_v1 WHERE profile=? AND id=?',(self.profile,origin)).fetchone()
        if not row:return
        saved=json.loads(row[0])
        for edge in saved.get('portals',[]):
            if edge.get('transition_id')==self.portal['id']:
                edge.update({k:self.portal[k] for k in ('destination_map','destination_entry','destination_region') if k in self.portal})
        self.db.execute('UPDATE exterior_maps_v1 SET data=?,updated=? WHERE profile=? AND id=?',(json.dumps(saved),time.time(),self.profile,origin))
    def start_boundary(self,before,after,nav,evidence):
        self.portal={'id':uuid.uuid4().hex[:16],'status':'awaiting_entry','origin_map':self.map_id,'origin_pose':list(nav.pose),
            'before_sha256':hashlib.sha256(before).hexdigest(),'after_sha256':hashlib.sha256(after).hexdigest(),'evidence':evidence}
        keys=evidence.get('keys',[])
        direction=next((d for d,key in nav.keys.items() if keys==[key]),None)
        if direction:
            prior=nav.portal_edges.get((nav.pose,direction),{})
            nav.portal_edges[(nav.pose,direction)]={'visits':prior.get('visits',0)+1,'transition_id':self.portal['id']}
        self.save(nav)
        self.db.execute('INSERT OR REPLACE INTO exterior_portals_v1 VALUES(?,?,?,?)',(self.profile,self.portal['id'],json.dumps(self.portal),time.time()));self.db.commit()
        self.map_id=None;self.entry_id=None;self.awaiting=True;nav.reset('exterior_scene_boundary');nav.active=False
        self.latest={'ready':False,'status':'scene_boundary','boundary':self.portal}
    def before_input(self,image,nav):
        if not self.map_id or not self.last_frame:return False
        from .exterior_screen import white_frames
        if white_frames(image).get('visible') is not False:return False
        evidence={'trigger':'scene_boundary_before_input','keys':[]}
        if dark_signature(signature(image)):
            evidence['basis']='dark_input_baseline'
        else:
            result=continuity(self.last_frame,image)
            if result['status']!='discontinuous':return False
            evidence['continuity']=result
        self.start_boundary(self.last_frame,image,nav,evidence);return True
    def after_input(self,before,after,row,nav,temporal=None):
        if not self.map_id or not row.get('executed') or row.get('turn_kind')=='aborted' or (row.get('input_receipt') or {}).get('os_submission')!='accepted':return False
        temporal=temporal or {};boundary={'step':row.get('step'),'keys':row.get('observed_keys',[]),'temporal':temporal}
        from .exterior_screen import white_frames
        after_frame=white_frames(after)
        temporal_black=temporal.get('dark_seen') and (after_frame.get('visible') is not True or temporal.get('blackout_seen') or not temporal.get('sampled'))
        if temporal_black or dark_signature(signature(after)) and after_frame.get('visible') is not True:
            boundary['trigger']='dark_during_input_response';self.start_boundary(before,after,nav,boundary);return True
        if white_frames(before).get('visible') is not False or white_frames(after).get('visible') is not False:return False
        result=continuity(before,after)
        if result['status']=='discontinuous':
            boundary.update(trigger='background_discontinuity',continuity=result);self.start_boundary(before,after,nav,boundary);return True
        self.last_frame=after
        return False
    def checkpoint(self,nav,row=None):
        if self.map_id and self.nav_scope and nav.scope!=self.nav_scope:self.map_id=None;self.entry_id=None
        if self.map_id and (row is None or row.get('step',0)%4==0):self.save(nav)
    def _bound(self):
        ids=[r[0] for r in self.db.execute('SELECT id FROM exterior_maps_v1 WHERE profile=? ORDER BY updated DESC LIMIT -1 OFFSET 32',(self.profile,))]
        for mid in ids:
            self.db.execute('DELETE FROM exterior_entries_v1 WHERE profile=? AND map_id=?',(self.profile,mid));self.db.execute('DELETE FROM exterior_maps_v1 WHERE profile=? AND id=?',(self.profile,mid))
        self.db.execute('DELETE FROM exterior_portals_v1 WHERE profile=? AND id NOT IN (SELECT id FROM exterior_portals_v1 WHERE profile=? ORDER BY updated DESC LIMIT 128)',(self.profile,self.profile));self.db.commit()
    def context(self):return {**self.latest,'map_id':self.map_id,'entry_id':self.entry_id,'reused':self.reused,'awaiting_entry':self.awaiting,'basis':'entry_image_match_hypothesis','stored_map_limit':32}
    def close(self,nav):
        self.checkpoint(nav)
        if self.owned:self.db.close()
