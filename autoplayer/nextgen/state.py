"""One frame analysis cache; measured, retained and hypothesized data stay apart."""
import copy,hashlib,io,json,math,re,time,uuid
from collections import OrderedDict
from PIL import Image,ImageChops
from ..controls import is_torneko
from ..knowledge import KnowledgeEngine
from ..map_sensor import MapSensor,FloorConfirmation,read_grid
from .frame_sensor import interface_frames
from ..appearance import descriptor


from .state_hash import digest
from .identity import InterfaceIdentity
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

class StateBuilder:
    def __init__(self,cfg,ocr):
        self.cfg=cfg;self.ocr=ocr;self.ocr.fast_mode=True;self.knowledge=KnowledgeEngine();self.cache=OrderedDict();self.evidence_cache=OrderedDict();self.retained={};self.map_sensor=MapSensor();self.floor=FloorConfirmation();self.tracker=AnonymousTracker();self.prototypes=OrderedDict();self.hits=0;self.serial=0;self.map_domain=uuid.uuid4().hex[:16];self.restore_candidates=[];self.restoration_checked=False;self.interfaces=InterfaceIdentity()
    def analyze(self,png,full=True):
        sha=hashlib.sha256(png).hexdigest();cached=self.cache.get(sha)
        if cached and (cached['full'] or not full):
            self.hits+=1;value=copy.deepcopy(cached);value['timing']={'ocr_ms':0.,'object_recognition_ms':0.,'map_geometry_ms':0.,'analysis_cache_hit':True};return value
        frame_started=time.monotonic();frames=cached['white_frame'] if cached else interface_frames(png);frame_ms=(time.monotonic()-frame_started)*1000
        full=full or frames.get('visible') is True
        started=time.monotonic();native=self.ocr.read(png) if full or not is_torneko(self.cfg.window_title) or not hasattr(self.ocr,'read_regions') else self.ocr.read_regions(png)
        ocr_ms=(time.monotonic()-started)*1000
        if native.get('frame_sha256')!=sha:native={'status':'unavailable','lines':[],'frame_sha256':sha,'reason':'source_mismatch'}
        native=copy.deepcopy(native)
        full=full or native.get('ocr_scope')!='hud_and_messages'
        if cached:
            value=copy.deepcopy(cached);value.update(native=native,full=full)
            value['metrics']=self.knowledge.reconcile(native)['metrics'] if is_torneko(self.cfg.window_title) else cached['metrics']
            value['timing']['ocr_ms']=ocr_ms;value['timing']['geometry_cache_hit']=True
            self.cache[sha]=copy.deepcopy(value);return value
        with Image.open(io.BytesIO(png)) as source:
            image=source.convert('RGB');vector=[round(v/255,2) for v in image.resize((16,12)).tobytes()];size=list(image.size)
        t=time.monotonic();grid=read_grid(png) if is_torneko(self.cfg.window_title) else {'status':'unavailable'}
        visual_ms=(time.monotonic()-t)*1000
        # No previous Q values, role predictions or model descriptions here.
        metrics=self.knowledge.reconcile(native)['metrics'] if is_torneko(self.cfg.window_title) else {}
        if not is_torneko(self.cfg.window_title):
            for line in native.get('lines',[]):
                match=re.fullmatch(r'\s*([^\d:：]{1,24})\s*[:：]\s*(\d{1,7})(?:\s*/\s*(\d{1,7}))?\s*',line.get('text',''))
                if match:metrics['text:'+match[1].strip()]={'values':[int(x) for x in match.groups()[1:] if x is not None],'quote':line['text'],'source':'visible_text'}
        confirmation=self.floor.update(metrics.get('floor',{}).get('values'),sha)
        if confirmation.get('changed'):self.map_sensor.reset();self.map_domain=uuid.uuid4().hex[:16];self.restoration_checked=True
        if grid.get('status')=='observed' and confirmation.get('status') in ('initialized','confirmed','changed'):grid=self.map_sensor.accumulate(copy.deepcopy(grid),confirmation['confirmed'])
        elif confirmation.get('status')=='pending':grid={'status':'unavailable','reason':'floor_pending'}
        t=time.monotonic();objects,tracking=self.tracker.observe(png)
        value={'sha':sha,'full':full,'native':native,'metrics':metrics,'minimap':grid,'white_frame':frames,'vector':vector,'size':size,'objects':objects,'tracking':tracking,'floor_confirmation':confirmation,
          'timing':{'ocr_ms':ocr_ms,'object_recognition_ms':(time.monotonic()-t)*1000,'map_geometry_ms':visual_ms,'interface_geometry_ms':frame_ms}}
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
        native=self.ocr.read(png)
        if native.get('frame_sha256')!=sha:native={'status':'unavailable','lines':[],'frame_sha256':sha}
        result={'sha':sha,'native':native};self.evidence_cache[sha]=copy.deepcopy(result)
        while len(self.evidence_cache)>12:self.evidence_cache.popitem(last=False)
        return result
    def build(self,png,full=True,measured_map=None):
        started=time.monotonic();value=self.analyze(png,full);native=value['native'];metrics=value['metrics'];grid=value['minimap'];sha=value['sha']
        if measured_map is not None:grid=self.map_sensor.accumulate(copy.deepcopy(measured_map),value['floor_confirmation'].get('confirmed'))
        quotes=[x.get('text','')[:180] for x in native.get('lines',[])][:40]
        menu=value['white_frame'].get('visible') is True
        # Text is observed, "menu" remains a geometric interface hypothesis.
        body=[re.sub(r'\d+','N',re.sub(r'\s+','',q)) for q in quotes]
        selection=None
        if menu:
            if is_torneko(self.cfg.window_title):
                from ..world_model import Inventory
                selection=Inventory.cursor_index(native,png)
            else:
                explicit=[i for i,line in enumerate(quotes) if re.match(r'^\s*[▶►▷>→]',line)]
                if len(explicit)==1:selection=explicit[0]
        text_key=digest([body,selection])
        layout,selected_identity=self.interfaces.observe(native,selection)
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
          'selection_observation':{'line_index':selection,'quote':quotes[selection] if selection is not None and selection<len(quotes) else None,'frame_sha256':sha,'status':'observed_candidate' if selection is not None else 'unavailable'},'interface_layout':layout,'semantic_text_key':semantic_key,'visual_key':visual,'text_key':text_key,'interface_hypothesis':menu,'interface_kind':'text_frame' if menu else None,'menu_evidence':menu,'eligible':bool(pose and not menu),
          'floor_confirmation':value['floor_confirmation'],'objects':value['objects'],'tracking_success':value['tracking'],'quality':quality,
          'map_domain':self.map_domain if pose else None,'visual_vector':value['vector'],'position_confidence':.95 if pose else None,'condition_evidence':{'local_map':local,'metrics':conditions},'state_identification':'minimap_measurement' if pose else 'appearance_text_hypothesis',
          'measurement_status':native.get('status'),'timing':value['timing']}
        state['timing']['state_construction_ms']=(time.monotonic()-started)*1000
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
    delta['state_changed']=int(before['id']!=after['id'])
    delta['floor_changed']=int(before['floor_confirmation'].get('confirmed')!=after['floor_confirmation'].get('confirmed')) if same_floor is False and after['floor_confirmation'].get('status')=='changed' else 0
    return delta
