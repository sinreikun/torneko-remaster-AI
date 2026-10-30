"""One frame analysis cache; measured, retained and hypothesized data stay apart."""
import copy,hashlib,io,json,math,re,time,uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from PIL import Image,ImageChops
from ..controls import is_torneko
from ..knowledge import KnowledgeEngine
from ..map_sensor import MapSensor,FloorConfirmation,read_grid
from .frame_sensor import interface_frames
from .interaction_surface import corroborated_frame
from ..appearance import descriptor


from .state_hash import digest
from .identity import InterfaceIdentity
from .ui_vision import UIVision
from .ui_interactions import UIInteractionModel,text_signature
from .brief_protocol import unavailable_key_hints
from .text_evidence import collect
def numbers(metrics):return {k:v['values'] for k,v in metrics.items() if v.get('values') is not None and not v.get('retained')}

class AnonymousTracker:
    """Spatial continuity plus appearance; similarity is never a species label."""
    def __init__(self):self.prefix=uuid.uuid4().hex[:8];self.previous=None;self.tracks=[];self.serial=0;self.variants=OrderedDict();self.dirty=set()
    def observe(self,png):
        with Image.open(io.BytesIO(png)) as source:image=source.convert('RGB');small=image.resize((32,18))
        proposals=[]
        if self.previous is not None:
            delta=ImageChops.difference(small,self.previous).convert('L');points={(x,y) for y in range(3,17) for x in range(32) if delta.getpixel((x,y))>26}
            # Large camera changes do not establish continuous object identity.
            if len(points)<180:
                while points and len(proposals)<24:
                    seed=points.pop();component={seed};pending=[seed]
                    while pending:
                        x,y=pending.pop()
                        for p in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                            if p in points:points.remove(p);component.add(p);pending.append(p)
                    if not 1<=len(component)<=40:continue
                    xs,ys=zip(*component);box=[min(xs)/32,min(ys)/18,(max(xs)+1)/32,(max(ys)+1)/18]
                    crop=image.crop(tuple(round(v*(image.width if i%2==0 else image.height)) for i,v in enumerate(box)))
                    proposals.append((box,descriptor(crop)))
        previous=self.tracks;tracks=[];used=set();matched=0
        for box,vector in proposals:
            ranked=[]
            for old in previous:
                if old['id'] in used:continue
                distance=sum(abs(a-b) for a,b in zip(box,old['bbox']))/4
                appearance=min(math.sqrt(sum((a-b)**2 for a,b in zip(vector,v))/len(vector)) for v in self.variants.get(old['id'],[old['descriptor']]))
                if distance<.12 and appearance<.27:ranked.append((distance+appearance*.5,old))
            ranked.sort(key=lambda x:x[0]);continuous=bool(ranked and (len(ranked)==1 or ranked[1][0]-ranked[0][0]>.045))
            if continuous:identifier=ranked[0][1]['id'];used.add(identifier);matched+=1
            else:self.serial+=1;identifier='visual_'+self.prefix+'_'+str(self.serial)
            variants=self.variants.setdefault(identifier,[])
            if continuous or not variants:
                if not any(sum(abs(a-b) for a,b in zip(vector,v))/len(vector)<.008 for v in variants):
                    variants.append(vector);del variants[:-16];self.dirty.add(identifier)
            self.variants.move_to_end(identifier)
            while len(self.variants)>128:self.variants.popitem(last=False)
            similar=[]
            for label,samples in list(self.variants.items())[-64:]:
                if label==identifier or not samples:continue
                distance=math.sqrt(sum((a-b)**2 for a,b in zip(vector,samples[-1]))/len(vector))
                if distance<.18:similar.append({'id':label,'distance':round(distance,3),'identity_verified':False})
            similar.sort(key=lambda x:x['distance'])
            tracks.append({'similar_class_candidates':similar[:2],'id':identifier,'bbox':box,'descriptor':vector,'continuity_candidate':continuous,'confidence':.65 if continuous else .3,'identity_verified':False,'kind':'anonymous_visual_candidate','variants':len(variants)})
        self.previous=small;self.tracks=tracks
        return [{k:v for k,v in t.items() if k!='descriptor'} for t in tracks],matched/len(proposals) if proposals else None

def frame_geometry(png,with_map):
    started=time.monotonic()
    grid=read_grid(png) if with_map else {'status':'unavailable'}
    elapsed=(time.monotonic()-started)*1000
    with Image.open(io.BytesIO(png)) as source:
        image=source.convert('RGB');vector=[round(v/255,2) for v in image.resize((16,12)).tobytes()];size=list(image.size)
    return {'sha':hashlib.sha256(png).hexdigest(),'grid':grid,'vector':vector,'size':size,'map_ms':elapsed}

class StateBuilder:
    def __init__(self,cfg,ocr):
        self.geometry_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='frame-geometry');self.cfg=cfg;self.ocr=ocr;self.ocr.fast_mode=True;self.knowledge=KnowledgeEngine();self.cache=OrderedDict();self.evidence_cache=OrderedDict();self.retained={};self.map_sensor=MapSensor();self.floor=FloorConfirmation();self.tracker=AnonymousTracker();self.prototypes=OrderedDict();self.hits=0;self.serial=0;self.map_domain=uuid.uuid4().hex[:16];self.restore_candidates=[];self.restoration_checked=False;self.interfaces=InterfaceIdentity();self.observation_policy={'cache_uses':12};self.cache_uses={};self.ui_vision=UIVision();self.ui_expected=False;self.ui_interactions=UIInteractionModel()
    def close(self):self.geometry_pool.shutdown(wait=False,cancel_futures=True)
    def read_metrics(self,native):
        metrics=self.knowledge.reconcile(native)['metrics'] if is_torneko(self.cfg.window_title) else {}
        if not is_torneko(self.cfg.window_title):
            for line in native.get('lines',[]):
                match=re.fullmatch(r'\s*([^\d:：]{1,24})\s*[:：]\s*(\d{1,7})(?:\s*/\s*(\d{1,7}))?\s*',line.get('text',''))
                if match:metrics['text:'+match[1].strip()]={'values':[int(x) for x in match.groups()[1:] if x is not None],'quote':line['text'],'source':'visible_text'}
        return metrics
    def analyze(self,png,full=True):
        sha=hashlib.sha256(png).hexdigest();cached=self.cache.get(sha)
        full=full or bool(self.ui_expected and self.ui_vision.last and self.ui_vision.last.get('needs_observation'))
        uses=self.cache_uses.get(sha,0)
        force_fresh=getattr(self,'force_fresh_once',False);self.force_fresh_once=False
        policy_cached=not force_fresh and cached and uses<self.observation_policy.get('cache_uses',12)
        if policy_cached and (cached['full'] or not full):
            self.cache_uses[sha]=uses+1
            self.hits+=1;value=copy.deepcopy(cached);value['timing']={'ocr_ms':0.,'object_recognition_ms':0.,'map_geometry_ms':0.,'analysis_cache_hit':True};return value
        geometry=self.geometry_pool.submit(frame_geometry,png,is_torneko(self.cfg.window_title)) if not cached else None
        frame_started=time.monotonic();frames=cached['white_frame'] if cached else interface_frames(png);frame_ms=(time.monotonic()-frame_started)*1000
        full=full or frames.get('visible') is True or bool(self.ui_expected and self.ui_vision.last and self.ui_vision.last.get('needs_observation'))
        started=time.monotonic();self.ocr.force_fresh=bool(force_fresh or cached and not policy_cached)
        try:
            native=self.ocr.read(png) if full or not is_torneko(self.cfg.window_title) or not hasattr(self.ocr,'read_regions') else self.ocr.read_regions(png)
            if self.observation_policy.get('ui_region_ocr') and self.ui_vision.last and self.ui_vision.last.get('regions'):
                from ..ocr import read_spatial_regions
                extra=read_spatial_regions(self.ocr,png,self.ui_vision.last['regions'])
                if extra['status']=='ok':
                    native=copy.deepcopy(native);native['lines']=(native.get('lines',[])+extra['lines'])[:300];native['spatial_region_requests']=extra['regions']
        finally:self.ocr.force_fresh=False
        ocr_ms=(time.monotonic()-started)*1000;self.cache_uses[sha]=0
        self.cache_uses={k:v for k,v in self.cache_uses.items() if k in self.cache}
        if native.get('frame_sha256')!=sha:native={'status':'unavailable','lines':[],'frame_sha256':sha,'reason':'source_mismatch'}
        native=copy.deepcopy(native)
        full=full or native.get('ocr_scope')!='hud_and_messages'
        if cached:
            value=copy.deepcopy(cached);value.update(native=native,full=full)
            value['metrics']=self.read_metrics(native)
            value['timing']['ocr_ms']=ocr_ms;value['timing']['geometry_cache_hit']=True
            self.cache[sha]=copy.deepcopy(value);return value
        barrier=time.monotonic();measured=geometry.result()
        if measured['sha']!=sha:raise ValueError('Parallel frame source mismatch')
        join_ms=(time.monotonic()-barrier)*1000
        grid=measured['grid'];vector=measured['vector'];size=measured['size'];visual_ms=measured['map_ms']
        # No previous Q values, role predictions or model descriptions here.
        metrics=self.read_metrics(native)
        confirmation=self.floor.update(metrics.get('floor',{}).get('values'),sha)
        if confirmation.get('changed'):self.map_sensor.reset();self.map_domain=uuid.uuid4().hex[:16];self.restoration_checked=True
        if grid.get('status')=='observed' and confirmation.get('status') in ('initialized','confirmed','changed'):grid=self.map_sensor.accumulate(copy.deepcopy(grid),confirmation['confirmed'])
        elif confirmation.get('status')=='pending':grid={'status':'unavailable','reason':'floor_pending'}
        t=time.monotonic();objects,tracking=self.tracker.observe(png)
        value={'sha':sha,'full':full,'native':native,'metrics':metrics,'minimap':grid,'white_frame':frames,'vector':vector,'size':size,'objects':objects,'tracking':tracking,'floor_confirmation':confirmation,
          'timing':{'ocr_ms':ocr_ms,'object_recognition_ms':(time.monotonic()-t)*1000,'map_geometry_ms':visual_ms,'interface_geometry_ms':frame_ms,'parallel_geometry_join_ms':join_ms,'parallel_frame_geometry':True}}
        self.cache[sha]=copy.deepcopy(value)
        while len(self.cache)>12:self.cache.popitem(last=False)
        return value
    def message_evidence(self,png):
        # Past frames inside the same input are text evidence only. Never rewind
        # current floor confirmation, retained status, object tracks or map state.
        sha=hashlib.sha256(png).hexdigest()
        cached=self.cache.get(sha)
        if cached and cached['full']:return {'sha':sha,'native':copy.deepcopy(cached['native'])}
        if sha in self.evidence_cache:return copy.deepcopy(self.evidence_cache[sha])
        self.message_evidence_reads=getattr(self,'message_evidence_reads',0)+1
        native=self.ocr.read(png)
        if native.get('frame_sha256')!=sha:native={'status':'unavailable','lines':[],'frame_sha256':sha}
        result={'sha':sha,'native':native};self.evidence_cache[sha]=copy.deepcopy(result)
        while len(self.evidence_cache)>12:self.evidence_cache.popitem(last=False)
        return result
    def build(self,png,full=True,measured_map=None):
        started=time.monotonic();value=self.analyze(png,full);native=value['native'];metrics=value['metrics'];grid=value['minimap'];sha=value['sha']
        if measured_map is not None:grid=self.map_sensor.accumulate(copy.deepcopy(measured_map),value['floor_confirmation'].get('confirmed'))
        quotes,footer,masked=collect(native,value['size'])
        menu=corroborated_frame(value['white_frame'],native,value['size'])
        # Text is observed, "menu" remains a geometric interface hypothesis.
        body=[re.sub(r'\d+','N',re.sub(r'\s+','',q)) for q in quotes]
        ui=self.ui_vision.measure(png,native,{**self.observation_policy,'ui_surface_observed':menu or bool(masked)})
        self.ui_expected=bool(menu or ui['grid'].get('status')=='hypothesis');ui['active']=self.ui_expected
        if self.ui_expected and not ui.get('regions'):ui['regions']=copy.deepcopy(value['white_frame'].get('rectangles',[])[:4])
        self.ui_vision.last=copy.deepcopy(ui)
        selected=ui['selection'];selection=next((e['line'] for e in ui['elements'] if e['id']==selected.get('element_id')),None)
        text_key=digest([body,selected.get('element_id')])
        layout,_=self.interfaces.observe(native,None)
        selected_identity=selected.get('element_id') if selected.get('status')=='supported_association' else None
        semantic_key=digest([layout,selected_identity])
        nearest=None
        for identifier,vector in self.prototypes.items():
            distance=sum(abs(a-b) for a,b in zip(vector,value['vector']))/len(vector)
            if distance<.075 and (nearest is None or distance<nearest[0]):nearest=(distance,identifier)
        if nearest:visual=nearest[1]
        else:
            visual='view_'+digest(value['vector']);self.prototypes[visual]=value['vector']
            while len(self.prototypes)>256:self.prototypes.popitem(last=False)
        floor=value['floor_confirmation'].get('confirmed');pose=grid.get('player_cell') if grid.get('status')=='observed' else None
        if pose and not self.restoration_checked:
            known={tuple(p) for p in grid.get('accumulated_cells',[])}
            matches={}
            if len(known)>=12:
                for saved in self.restore_candidates:
                    if saved.get('floor_confirmation',{}).get('confirmed')!=floor or not saved.get('map_domain'):continue
                    old={tuple(p) for p in saved.get('minimap',{}).get('accumulated_cells',[])}
                    overlap=len(old&known)/max(1,len(old|known))
                    if overlap>=.85:matches[saved['map_domain']]=overlap
                ranked=sorted(matches.items(),key=lambda x:-x[1])
                if len(ranked)==1 or len(ranked)>1 and ranked[0][1]-ranked[1][1]>.15:self.map_domain=ranked[0][0]
            self.restoration_checked=True
        node=digest(['interface',layout,selected_identity]) if menu else digest(['grid',self.map_domain,floor,pose]) if pose else digest(['view',visual,layout,menu])
        local={k:grid.get(k,[]) for k in ('enemies','items','stairs')}
        local={k:[p for p in v if max(map(abs,p))<=3] for k,v in local.items()}
        conditions={k:v['values'] for k,v in metrics.items() if k not in ('hp','fullness','gold','experience')}
        hp=metrics.get('hp',{}).get('values');food=metrics.get('fullness',{}).get('values')
        conditions['hp_band']=int(4*hp[0]/max(1,hp[1])) if hp else None
        conditions['food_band']=int(food[0]/20) if food else None
        context=digest(['v2.1',menu,semantic_key if menu else None,visual if not pose and not menu else None,{} if menu else local,{'floor':floor,'hp_band':conditions['hp_band']} if menu else conditions])
        retained={k:{**v,'retained':True} for k,v in self.retained.items() if k not in metrics}
        for k,v in metrics.items():self.retained[k]={**copy.deepcopy(v),'source_frame':sha}
        quality=.95 if pose and metrics else .8 if native.get('status')=='ok' and quotes else .45
        state={'id':node,'context':context,'frame_sha256':sha,'observed_at':time.time(),'quotes':quotes,'metrics':metrics,'retained':retained,'minimap':grid,
          'ui_visual':ui,'selection_observation':{**selected,'line_index':selection,'frame_sha256':sha},'interface_layout':layout,'semantic_text_key':semantic_key,'footer_quotes':footer,'editable_content_candidates':masked,'visual_key':visual,'text_key':text_key,'interface_hypothesis':menu,'interface_observation':value['white_frame'],'interface_kind':'text_frame' if menu else None,'menu_evidence':menu,'eligible':bool(pose and not menu),
          'floor_confirmation':value['floor_confirmation'],'objects':value['objects'],'tracking_success':value['tracking'],'quality':quality,
          'map_domain':self.map_domain if pose else None,'visual_vector':value['vector'],'position_confidence':.95 if pose else None,'condition_evidence':{'local_map':local,'metrics':conditions},'state_identification':'minimap_measurement' if pose else 'appearance_text_hypothesis',
          'unavailable_input_hints':unavailable_key_hints(quotes,self.cfg.allowed_keys),'measurement_status':native.get('status'),'timing':value['timing']}
        self.ui_interactions.attach(state)
        self.ui_vision.last=copy.deepcopy(ui)
        if ui['active']:self.apply_ui_selection(state)
        state['timing']['state_construction_ms']=(time.monotonic()-started)*1000
        return state

    def apply_ui_selection(self,state):
        ui=state.get('ui_visual',{});selected=ui.get('selection',{})
        state['selection_observation']={**copy.deepcopy(selected),'line_index':next((e['line'] for e in ui.get('elements',[]) if e['id']==selected.get('element_id')),None),'frame_sha256':state['frame_sha256']}
        if state.get('interface_hypothesis') or ui.get('active'):
            selected_id=selected.get('element_id') if selected.get('status')=='supported_association' else None
            state['semantic_text_key']=digest([state['interface_layout'],selected_id])
            hp=state.get('metrics',{}).get('hp',{}).get('values');band=int(4*hp[0]/max(1,hp[1])) if hp else None
            state['context']=digest(['ui-context-v1',state['interface_layout'],selected_id,state.get('floor_confirmation',{}).get('confirmed'),band])
            state['id']=digest(['spatial_interface',state['interface_layout'],selected.get('element_id') if selected.get('status')=='supported_association' else None])
        return state


def effects(before,after):
    a=numbers(before['metrics']);b=numbers(after['metrics']);delta={}
    for k in a.keys()&b.keys():
        for i,(x,y) in enumerate(zip(a[k],b[k])):delta[f'metric.{k}.{i}']=y-x
    p=before['minimap'].get('player_cell');q=after['minimap'].get('player_cell')
    same_floor=before['floor_confirmation'].get('confirmed')==after['floor_confirmation'].get('confirmed') and after['floor_confirmation'].get('status')!='pending'
    if p and q and same_floor:delta.update(dx=q[0]-p[0],dy=q[1]-p[1])
    delta['interface_changed']=int(before['interface_hypothesis']!=after['interface_hypothesis'])
    delta['text_changed']=int(before.get('semantic_text_key',before['text_key'])!=after.get('semantic_text_key',after['text_key'])) if before['measurement_status']==after['measurement_status']=='ok' else None
    delta['editable_content_changed']=int(before.get('editable_content_candidates',[])!=after.get('editable_content_candidates',[])) if before['measurement_status']==after['measurement_status']=='ok' else None
    ua=before.get('ui_visual',{});ub=after.get('ui_visual',{})
    if ua and ub:
        delta['ui_text_content_changed']=int(text_signature(before)!=text_signature(after)) if before.get('measurement_status')==after.get('measurement_status')=='ok' else None
        delta['ui_local_change']=int(ua['frame_sha256']!=ub['frame_sha256'] and bool(ub.get('candidates')))
        sa=ua.get('selection',{});sb=ub.get('selection',{})
        delta['ui_selection_changed']=int(sa['element_id']!=sb['element_id']) if sa.get('status')==sb.get('status')=='supported_association' else None
    delta['state_changed']=int(before['id']!=after['id'])
    delta['floor_changed']=int(before['floor_confirmation'].get('confirmed')!=after['floor_confirmation'].get('confirmed')) if same_floor is False and after['floor_confirmation'].get('status')=='changed' else 0
    return delta
