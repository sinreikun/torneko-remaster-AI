"""Spatial OCR and input-correlated UI hypotheses. Never a semantic oracle."""
import copy,hashlib,io,math,re,time,uuid
from collections import OrderedDict
from statistics import median
from PIL import Image,ImageChops,ImageFilter,ImageStat
from .state_hash import digest


def box_union(boxes):
    return [min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]

def xyxy(box,size):
    if not isinstance(box,(tuple,list)) or len(box)!=4 or any(type(x) not in (int,float) or not math.isfinite(x) for x in box):return None
    x,y,w,h=box;W,H=size
    if w<=0 or h<=0:return None
    b=[max(0.,x/W),max(0.,y/H),min(1.,(x+w)/W),min(1.,(y+h)/H)]
    return b if b[2]>b[0] and b[3]>b[1] else None

def center(b):return [(b[0]+b[2])/2,(b[1]+b[3])/2]
def distance(a,b):return math.dist(center(a),center(b))
def overlap(a,b):
    area=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return area/max(1e-8,min((a[2]-a[0])*(a[3]-a[1]),(b[2]-b[0])*(b[3]-b[1])))

def text_elements(native,size,frame,previous=()):
    out=[];unlocated=[];stamp=native.get('captured_at',time.time())
    for i,line in enumerate(native.get('lines',[])[:300]):
        words=line.get('words') or []
        positioned=[(w,xyxy(w.get('box'),size)) for w in words]
        positioned=[(w,b) for w,b in positioned if b]
        if not positioned:
            unlocated.append({'text':line.get('text','')[:240],'line':i,'reason':'box_missing'});continue
        # Word boxes are real OCR boxes. Whole-line Paddle results remain whole lines.
        for j,(word,b) in enumerate(positioned):
            text=word.get('text',line.get('text',''))
            if not isinstance(text,str) or not text.strip():continue
            key=digest([round(v*160) for v in b[:2]])
            matches=sorted([(distance(b,p['bbox']),p) for p in previous if distance(b,p['bbox'])<.025],key=lambda v:v[0])
            old=matches[0][1] if matches and (len(matches)==1 or matches[1][0]-matches[0][0]>.005) else None
            if any(p['text']==text and overlap(p['bbox'],b)>.7 for p in out):continue
            out.append({'id':'text:'+key,'text':text[:240],'bbox':b,'center':center(b),'line':i,'block':line.get('block',None),
              'confidence':word.get('confidence',line.get('confidence',native.get('confidence'))),
              'frame_sha256':frame,'observed_at':stamp,'recognition_at':line.get('recognition_at',stamp),
              'previous_id':old['id'] if old else None,'previous_match_status':'spatial_hypothesis' if old else 'unknown','previous_match_confidence':round(max(.05,.7-distance(b,old['bbox'])*20),3) if old else None,'text_changed':old['text']!=text if old else None,'kind':'ocr_text_observation','role_candidates':[{'role':'value' if re.fullmatch(r'[\d/% +.,:]+',text) else 'text','confidence':.4}]})
            if len(out)>=256:return out,unlocated[:32]
    return out,unlocated[:32]

def components(mask,limit=48):
    w,h=mask.size;data=mask.tobytes();points={i for i,v in enumerate(data) if v};out=[]
    while points and len(out)<limit:
        seed=points.pop();todo=[seed];xs=[];ys=[]
        while todo:
            p=todo.pop();x=p%w;y=p//w;xs.append(x);ys.append(y)
            for q in (p-1 if x else -1,p+1 if x<w-1 else -1,p-w,p+w):
                if q in points:points.remove(q);todo.append(q)
        if len(xs)>=2:
            b=[min(xs)/w,min(ys)/h,(max(xs)+1)/w,(max(ys)+1)/h]
            if (b[2]-b[0])*(b[3]-b[1])<.18:out.append(b)
    return out

def difference_regions(before,after,threshold=18):
    delta=ImageChops.difference(before,after).convert('RGB')
    r,g,b=delta.split();peak=ImageChops.lighter(r,ImageChops.lighter(g,b))
    mask=peak.point(lambda p:255 if p>threshold else 0).filter(ImageFilter.MaxFilter(3))
    fraction=sum(v>0 for v in mask.tobytes())/(mask.width*mask.height)
    return components(mask) if fraction<.35 else [],fraction

def salience(image,b):
    w,h=image.size;x0,y0,x1,y1=[round(v*(w if i%2==0 else h)) for i,v in enumerate(b)]
    if x1<=x0 or y1<=y0:return 0.
    patch=image.crop((x0,y0,x1,y1));outer=image.crop((max(0,x0-4),max(0,y0-4),min(w,x1+4),min(h,y1+4)))
    p=ImageStat.Stat(patch);o=ImageStat.Stat(outer)
    return sum(p.stddev)/3+sum(abs(a-b) for a,b in zip(p.mean,o.mean))/3

def relationships(marker,texts):
    out=[];mx,my=center(marker)
    for text in texts:
        if isinstance(text.get('text'),str) and re.fullmatch(r'\s*[▶►▷>→]+\s*',text['text']):continue
        b=text['bbox'];tx,ty=text['center'];height=max(.008,b[3]-b[1]);width=max(.008,b[2]-b[0])
        if marker[0]<=tx<=marker[2] and marker[1]<=ty<=marker[3]:kind='contains';cost=abs(mx-tx)/width*.1+abs(my-ty)/height*.1
        elif overlap(marker,b)>.5 and width<max(.008,marker[2]-marker[0])*2:kind='overlap';cost=.25
        elif abs(my-ty)<height*.8 and mx<tx and -min(height*.5,width*.35)<=b[0]-marker[2]<height*2.5:kind='left_of_text';cost=.35+max(0,b[0]-marker[2])/height*.45+abs(my-ty)/height*.45
        elif abs(my-ty)<height*.8 and mx>tx and -min(height*.5,width*.35)<=marker[0]-b[2]<height*2.5:kind='right_of_text';cost=.35+max(0,marker[0]-b[2])/height*.45+abs(my-ty)/height*.45
        elif abs(mx-tx)<width*.6 and 0<=marker[1]-b[3]<height:kind='below_text';cost=.45+abs(my-ty)/height*.45+abs(mx-tx)/width*.45
        else:continue
        out.append({'element_id':text['id'],'text':text['text'],'kind':kind,'cost':round(cost,3),'confidence':round(max(.05,.4-cost*.2),3),'status':'spatial_hypothesis'})
    return sorted(out,key=lambda x:x['cost'])[:6]

def rarity(image,b,elements):
    def mean(box):
        w,h=image.size
        patch=image.crop(tuple(round(v*(w if i%2==0 else h)) for i,v in enumerate(box)))
        return ImageStat.Stat(patch).mean if patch.width and patch.height else [0,0,0]
    signature=tuple(tuple(e['bbox']) for e in elements if isinstance(e.get('text'),str))
    cached=getattr(image,'_ui_background_cache',None)
    if cached and cached[0]==signature:base=cached[1]
    else:
        values=[mean(box) for box in signature]
        base=[median(v[i] for v in values) for i in range(3)] if len(values)>=3 else ImageStat.Stat(image).mean
        image._ui_background_cache=(signature,base)
    return sum(abs(x-y) for x,y in zip(mean(b),base))/3

def movement_origins(old,new,boxes,old_elements,new_elements,target_box,target_relation):
    """Match disappearing UI evidence to the candidate that appeared.

    Background motion is not an origin merely because its pixel gain is large.
    This ranks conditional spatial pairs, not recognized cursor templates.
    """
    out=[];tw=max(.001,target_box[2]-target_box[0]);th=max(.001,target_box[3]-target_box[1])
    for box in boxes:
        gain=rarity(old,box,old_elements)-rarity(new,box,new_elements)
        if gain<=3:continue
        rs=relationships(box,old_elements)
        if not rs or len(rs)>1 and rs[1]['cost']-rs[0]['cost']<=.2:continue
        relation=rs[0]
        if relation['kind']!=target_relation['kind'] or relation['element_id']==target_relation['element_id']:continue
        source=next((e for e in old_elements if e['id']==relation['element_id']),None)
        target=next((e for e in new_elements if e['id']==target_relation['element_id']),None)
        if source and target and overlap(source['bbox'],target['bbox'])>.5 and distance(source['bbox'],target['bbox'])<.025:continue
        width=max(.001,box[2]-box[0]);height=max(.001,box[3]-box[1])
        ratio=max(width/tw,tw/width,height/th,th/height)
        if ratio>3:continue
        out.append((gain/ratio,box,relation))
    return sorted(out,key=lambda x:-x[0])

def grid_hypothesis(elements):
    if len(elements)<4:return {'status':'unknown'}
    heights=[e['bbox'][3]-e['bbox'][1] for e in elements];tol=max(.008,median(heights)*.7)
    def group(axis):
        groups=[]
        for e in sorted(elements,key=lambda e:e['center'][axis]):
            value=e['center'][axis]
            if not groups or abs(value-groups[-1][0])>tol:groups.append([value,[e['id']]])
            else:groups[-1][1].append(e['id'])
        return groups
    rows=group(1);cols=group(0);occupancy=len(elements)/max(1,len(rows)*len(cols))
    if min(len(rows),len(cols))<2 or occupancy<.55:return {'status':'unknown'}
    return {'status':'hypothesis','rows':len(rows),'columns':len(cols),'occupancy':min(1,occupancy),'confidence':.45,
            'cells':[{'id':e['id'],'bbox':e['bbox'],'text':e['text'],'row':next(i for i,r in enumerate(rows) if e['id'] in r[1]),'column':next(i for i,c in enumerate(cols) if e['id'] in c[1])} for e in elements[:128]],'coordinate_source':'measured_ocr_boxes'}

class UIVision:
    def __init__(self):
        self.frames=OrderedDict();self.layouts=OrderedDict();self.models=OrderedDict();self.tracks=[];self.last=None;self.serial=0;self.session=uuid.uuid4().hex[:12]
    def restore(self,models):
        for m in models:
            if m.get('schema')==1:self.models[m['key']]=copy.deepcopy(m)
    def _layout(self,elements):
        anchors={(round(e['center'][0]*40),round(e['center'][1]*30)) for e in elements}
        tokens={re.sub(r'[\W\d_]+','',e.get('text','').casefold()) for e in elements if isinstance(e.get('text'),str)}
        tokens={t for t in tokens if len(t)>=2}
        ranked=[]
        for k,v in self.layouts.items():
            space=len(anchors&v['anchors'])/max(1,len(anchors|v['anchors']))
            text=len(tokens&v['tokens'])/max(1,len(tokens|v['tokens']))
            if space>=.7 and (min(len(tokens),len(v['tokens']))<2 or text>=.35):ranked.append((space*.8+text*.2,k))
        ranked.sort(reverse=True)
        key=ranked[0][1] if ranked else digest(['spatial_ui_v2',sorted(anchors),sorted(tokens)])
        if key not in self.layouts:self.layouts[key]={'anchors':anchors,'tokens':tokens}
        self.layouts.move_to_end(key)
        while len(self.layouts)>64:self.layouts.popitem(last=False)
        return key
    def measure(self,png,native,policy=None):
        policy=policy or {};sha=hashlib.sha256(png).hexdigest()
        with Image.open(io.BytesIO(png)) as src:
            size=list(src.size);small=src.convert('RGB');small.thumbnail((320,180))
        previous=self.last
        texts,missing=text_elements(native,size,sha,previous.get('elements',[]) if previous else [])
        from .visual_grid import glyph_grid,selection_elements
        pixel_grid=glyph_grid(small,sha) if policy.get('ui_surface_observed') else {'status':'unknown','cells':[]}
        targets=selection_elements(texts,pixel_grid['cells']) if pixel_grid['status']=='hypothesis' else texts
        layout=self._layout(texts);model=self.models.setdefault(layout,{'key':layout,'schema':1,'relations':{},'edges':[],'checks':[],'cells':[]})
        candidates=[];change_fraction=0.
        if previous and previous['frame_sha256']!=sha and previous['frame_sha256'] in self.frames and self.frames[previous['frame_sha256']].size==small.size:
            boxes,change_fraction=difference_regions(self.frames[previous['frame_sha256']],small,policy.get('ui_diff_threshold',18))
            for b in boxes:
                nearest=sorted([(distance(b,c['bbox']),c) for c in self.tracks if distance(b,c['bbox'])<.1],key=lambda v:v[0])
                identifier=nearest[0][1]['id'] if nearest and (len(nearest)==1 or nearest[1][0]-nearest[0][0]>.015) else 'region:'+digest([layout,[round(v*100) for v in b]])
                candidates.append({'id':identifier,'bbox':b,'center':center(b),'salience':salience(small,b),'activation':rarity(small,b,texts)-rarity(self.frames[previous['frame_sha256']],b,previous['elements']),'relations':relationships(b,targets),'frame_sha256':sha,'previous_id':nearest[0][1]['id'] if nearest else None,'status':'visual_change_candidate'})
        elif previous and previous['frame_sha256']==sha:candidates=copy.deepcopy(previous['candidates'])
        # OCR symbols are candidate evidence, never immediate selection truth.
        for e in texts:
            if re.match(r'^\s*[▶►▷>→]',e['text']):candidates.append({'id':'symbol:'+e['id'],'bbox':e['bbox'],'center':e['center'],'salience':1.,'relations':relationships(e['bbox'],targets),'frame_sha256':sha,'previous_id':None,'status':'symbol_hypothesis'})
        if previous and previous['layout']==layout:
            for c in previous['candidates'][:12]:
                if rarity(small,c['bbox'],texts)>20:
                    candidates.append({**copy.deepcopy(c),'frame_sha256':sha,'activation':rarity(small,c['bbox'],texts),'relations':relationships(c['bbox'],targets or model.get('cells',[])),'status':'reobserved_visual_candidate'})
        # One region can be rediscovered and reobserved in the same frame.
        # Keep its current measurement once; stale duplicates must not crowd
        # other candidates out of the finite tracking budget.
        unique={}
        for candidate in candidates:
            key=candidate['id']
            if key not in unique:unique[key]=candidate
        candidates=list(unique.values())
        out={'frame_sha256':sha,'observed_at':native.get('captured_at',time.time()),'size':size,'layout':layout,'elements':texts,'unlocated_text':missing,
             'cells':copy.deepcopy(pixel_grid['cells'] if pixel_grid['status']=='hypothesis' else model.get('cells',[])),
             'selection_elements':copy.deepcopy(targets),
             'candidates':sorted(candidates,key=lambda c:(not bool(c.get('relations')),-c['salience']))[:32],'grid':pixel_grid if pixel_grid['status']=='hypothesis' else grid_hypothesis(texts),'change_fraction':change_fraction,
             'selection':{'status':'unknown','element_id':None,'quote':None,'confidence':0.},'needs_observation':[],
             'difference_threshold':policy.get('ui_diff_threshold',18),'graph':copy.deepcopy(model['edges'][-48:]),'hypotheses':copy.deepcopy(list(model['relations'].values())[-16:])}
        if out['grid']['status']=='unknown' and len(model.get('cells',[]))>=4:out['grid']=grid_hypothesis(model['cells'])
        for candidate in out['candidates']:
            candidate['confidence']=max([model['relations'].get(r['kind'],{}).get('confidence',.15) for r in candidate['relations']],default=.1) if candidate.get('activation',0)>3 else .1
            candidate['confidence_kind']='uncalibrated_association_hypothesis'
        self._select(out,model)
        if texts and out['selection']['status']=='unknown':out['needs_observation'].append('selection_text_correspondence_unknown')
        if missing:out['needs_observation'].append('text_position_missing')
        if not texts:out['needs_observation'].append('text_regions_unreadable')
        out['coverage']=(bool(texts)+bool(out['selection']['element_id'])+bool(model['edges']))/3
        out['regions']=self.regions(out)
        self.frames[sha]=small
        while len(self.frames)>8:self.frames.popitem(last=False)
        self.tracks=out['candidates'];self.last=copy.deepcopy(out)
        while len(self.models)>64:self.models.popitem(last=False)
        return out
    @staticmethod
    def regions(ui):
        boxes=[c['bbox'] for c in ui['candidates'][:4]]
        if ui['elements']:boxes.append(box_union([e['bbox'] for e in ui['elements']]))
        return [[max(0,b[0]-.025),max(0,b[1]-.025),min(1,b[2]+.025),min(1,b[3]+.025)] for b in boxes[:4]]
    def _select(self,ui,model):
        ui['selection']={'status':'unknown','element_id':None,'quote':None,'confidence':0.}
        scored=[]
        for c in ui['candidates']:
            if c.get('activation',0)<=3:continue
            for relation in c['relations']:
                h=model['relations'].get(relation['kind'],{});support=h.get('support',0);failure=h.get('counterexamples',0)
                confidence=min(.93,(support+1)/(support+failure+2))
                if support>=max(3,h.get('review_support',0)) and confidence>=.75 and h.get('status')!='under_review':scored.append((confidence-relation['cost']*.3,relation,c,confidence))
        unique={}
        for item in scored:
            key=item[1]['element_id']
            if key not in unique or item[0]>unique[key][0]:unique[key]=item
        scored=sorted(unique.values(),key=lambda x:-x[0])
        if scored and (len(scored)==1 or scored[0][0]-scored[1][0]>.12):
            _,r,c,confidence=scored[0];ui['selection']={'status':'supported_association','element_id':r['element_id'],'quote':r['text'],'confidence':confidence,'marker_id':c['id'],'bbox':c['bbox'],'relation':r['kind'],'frame_sha256':ui['frame_sha256']}
    def learn(self,before,after,action,receipt,step,neutral=None,neutral_regions=None):
        a=before.get('ui_visual',{});b=after.get('ui_visual',{})
        if not a or not b or a.get('frame_sha256')!=before['frame_sha256'] or b.get('frame_sha256')!=after['frame_sha256']:return {'status':'unavailable'}
        model=self.models.get(b['layout'])
        if not model or receipt.get('os_submission')!='accepted':return {'status':'censored'}
        identity=digest([self.session,step,a['frame_sha256'],b['frame_sha256'],action['id']])
        if identity in model['checks']:return {'status':'duplicate'}
        model['checks'].append(identity);model['checks']=model['checks'][-64:]
        raw={'step':step,'input':copy.deepcopy(action),'before_frame':a['frame_sha256'],'after_frame':b['frame_sha256'],'status':'unresolved','causality':'input_correlated_not_proven_cause'}
        if a['frame_sha256'] not in self.frames or b['frame_sha256'] not in self.frames:return raw
        old=self.frames[a['frame_sha256']];new=self.frames[b['frame_sha256']]
        if old.size!=new.size:return {**raw,'status':'image_size_changed'}
        boxes,fraction=difference_regions(old,new,b.get('difference_threshold',18))
        rects=before.get('interface_observation',{}).get('rectangles',[])
        panel_changes=[]
        for region in rects[:8]:
            crop=tuple(round(v*(old.width if i%2==0 else old.height)) for i,v in enumerate(region))
            if crop[2]>crop[0] and crop[3]>crop[1]:panel_changes.append(sum(ImageStat.Stat(ImageChops.difference(old.crop(crop),new.crop(crop))).mean)/3)
        raw['panel_pixel_change']=max(panel_changes,default=0.)
        if a['layout']!=b['layout'] or fraction>=.35:
            raw.update(status='visual_reorganization',change_fraction=fraction)
            model['edges'].append({'origin':a['selection'].get('element_id') or 'layout:'+a['layout'],'destination':'layout:'+b['layout'],'action':action['id'],'kind':'visual_reorganization','confidence':.3,'before_frame':a['frame_sha256'],'after_frame':b['frame_sha256'],'step':step})
            model['edges']=model['edges'][-128:];model['last_transition']=raw;b['last_transition']=raw;return raw
        if not (before.get('interface_hypothesis') or after.get('interface_hypothesis') or a.get('grid',{}).get('status')=='hypothesis' or b.get('grid',{}).get('status')=='hypothesis'):
            return {**raw,'status':'ui_context_unconfirmed'}
        bounds=after.get('interface_observation',{}).get('rectangles',[])
        unmapped=[box for box in boxes if not relationships(box,b['elements']) and (any(overlap(box,r)>.5 for r in bounds) if bounds else not b['elements'] or any(overlap(box,e['bbox'])>.2 for e in b['elements']))]
        if unmapped and b.get('grid',{}).get('coordinate_source')!='measured_contrast_components_not_ocr':
            cells=model.setdefault('cells',[])
            for box in unmapped:
                identifier='cell:'+digest([round(v*100) for v in center(box)])
                if not any(e['id']==identifier for e in cells):cells.append({'id':identifier,'bbox':box,'center':center(box),'text':None,'kind':'anonymous_cell_hypothesis','source_frames':[a['frame_sha256'],b['frame_sha256']]})
            model['cells']=cells[-128:];b['cells']=copy.deepcopy(model['cells'])
        a_elements=a.get('selection_elements',a['elements'])+(model.get('cells',[]) if a.get('grid',{}).get('coordinate_source')!='measured_contrast_components_not_ocr' else [])
        b_elements=b.get('selection_elements',b['elements'])+(model.get('cells',[]) if b.get('grid',{}).get('coordinate_source')!='measured_contrast_components_not_ocr' else [])
        if model.get('cells') and b.get('grid',{}).get('coordinate_source')!='measured_contrast_components_not_ocr':b['grid']=grid_hypothesis(model['cells']) if len(model['cells'])>=4 else b['grid']
        natural=bool(neutral_regions is None and neutral and (neutral.get('ui_local_change') or neutral.get('text_changed')))
        ranked=[]
        for box in boxes:
            gain=rarity(new,box,b['elements'])-rarity(old,box,a['elements'])
            rs=relationships(box,b_elements)
            if gain>3 and rs and (len(rs)==1 or rs[1]['cost']-rs[0]['cost']>.2):ranked.append((gain,box,rs[0]))
        ranked.sort(key=lambda x:-x[0]);choice=ranked[0] if ranked and (len(ranked)==1 or ranked[0][0]>ranked[1][0]*1.4) else None
        if natural:return {**raw,'status':'natural_change_confounded'}
        if choice:
            _,box,relation=choice;kind=relation['kind'];h=model['relations'].setdefault(kind,{'kind':kind,'support':0,'counterexamples':0,'confidence':.4,'status':'hypothesis'})
            h['support']+=1;h['confidence']=min(.93,(h['support']+1)/(h['support']+h['counterexamples']+2));h['status']='supported_association' if h['support']>=max(3,h.get('review_support',0)) and h['confidence']>=.75 else 'under_review' if h.get('review_support') else 'hypothesis'
            raw.update(status='candidate_selection_movement',to_element=relation['element_id'],to_bbox=box,relation=kind,confidence=h['confidence'])
            # A previous marker location can be inferred from the disappearing component.
            disappearing=movement_origins(old,new,boxes,a_elements,b_elements,box,relation)
            if len(disappearing)>1 and disappearing[0][0]<=disappearing[1][0]*1.4:disappearing=[]
            if neutral_regions and any(overlap(region,box)>.15 or disappearing and overlap(region,disappearing[0][1])>.15 for region in neutral_regions):
                h['support']=max(0,h['support']-1);h['confidence']=min(.93,(h['support']+1)/(h['support']+h['counterexamples']+2));h['status']='hypothesis'
                return {**raw,'status':'natural_change_confounded'}
            origins=[disappearing[0][2]] if disappearing else []
            origin=origins[0]['element_id'] if origins and (len(origins)==1 or origins[1]['cost']-origins[0]['cost']>.2) else None
            raw['from_element']=origin
            if not origin or origin==relation['element_id']:
                h['support']=max(0,h['support']-1);h['confidence']=min(.93,(h['support']+1)/(h['support']+h['counterexamples']+2));h['status']='hypothesis'
                raw.update(status='unresolved_local_change',confidence=h['confidence'])
            previous=a.get('selection',{})
            if previous.get('element_id') and origin and previous['element_id']!=origin:
                old_h=model['relations'].get(previous.get('relation'))
                if old_h:old_h['counterexamples']+=1;old_h['confidence']=max(0,old_h['confidence']-.15);old_h['status']='under_review';old_h['review_support']=old_h['support']+2
            if origin:
                edge={'origin':origin,'destination':relation['element_id'],'action':action['id'],'kind':'selection_movement_hypothesis','confidence':h['confidence'],'before_frame':a['frame_sha256'],'after_frame':b['frame_sha256'],'step':step}
                existing=None
                for known in model['edges']:
                    if known.get('kind')!='selection_movement_hypothesis' or known['origin']!=origin or known['action']!=action['id']:continue
                    if known['destination']!=relation['element_id']:
                        known['counterexamples']=known.get('counterexamples',0)+1;known['confidence']*=.65;known['status']='under_review'
                    else:existing=known
                if existing:
                    existing.update(before_frame=a['frame_sha256'],after_frame=b['frame_sha256'],step=step)
                    existing['support']=existing.get('support',1)+1
                    existing['confidence']=h['confidence']*(existing['support']+1)/(existing['support']+existing.get('counterexamples',0)+1)
                    existing['status']='supported_association' if existing['confidence']>=.75 else 'hypothesis'
                else:model['edges'].append({**edge,'support':1,'counterexamples':0,'status':'supported_association' if h['confidence']>=.75 else 'hypothesis'})
                model['edges']=model['edges'][-128:]
            b['candidates'].append({'id':'input_region:'+identity,'bbox':box,'center':center(box),'salience':salience(new,box),'activation':choice[0],'relations':relationships(box,b_elements),'frame_sha256':b['frame_sha256'],'status':'input_correlated_candidate'})
            self._select(b,model)
        elif not boxes:raw['status']='no_measured_ui_change'
        elif any(e.get('text_changed') and any(overlap(e['bbox'],box)>.3 for box in boxes) for e in b['elements']):
            changed=[e for e in b['elements'] if e.get('text_changed') and any(overlap(e['bbox'],box)>.3 for box in boxes)]
            region=changed[0];key='editing:'+region['id'];h=model['relations'].setdefault(key,{'kind':'editing_region','support':0,'counterexamples':0,'confidence':.4,'status':'hypothesis'})
            h['support']+=1;h['confidence']=min(.9,.4+h['support']*.12)
            h['status']='supported_association' if h['support']>=3 else 'hypothesis'
            raw.update(status='text_region_changed',editing_region={'element_id':region['id'],'bbox':region['bbox'],'status':h['status'],'confidence':h['confidence'],'meaning':'editing_or_value_change_unverified'})
        else:raw['status']='unexplained_local_change'
        if a['elements']!=b['elements']:raw['text_changed']=any(e.get('text_changed') for e in b['elements'])
        model['last_transition']=raw;b['graph']=copy.deepcopy(model['edges'][-48:]);b['hypotheses']=copy.deepcopy(list(model['relations'].values()));b['last_transition']=raw
        if b['selection']['status']=='unknown' and 'selection_text_correspondence_unknown' not in b['needs_observation']:b['needs_observation'].append('selection_text_correspondence_unknown')
        if b['selection']['status']=='supported_association':b['needs_observation']=[v for v in b['needs_observation'] if v!='selection_text_correspondence_unknown']
        b['coverage']=(bool(b['elements'])+bool(b['selection']['element_id'])+bool(model['edges']))/3
        self.last=copy.deepcopy(b)
        return raw


def first_ui_action(ui,target,by_id=False):
    selection=ui.get('selection',{})
    if selection.get('status')!='supported_association':return None
    destinations=[e['id'] for e in ui.get('elements',[])+ui.get('cells',[]) if e['id']==target] if by_id else [e['id'] for e in ui.get('elements',[]) if isinstance(e.get('text'),str) and target in e['text']]
    if len(destinations)!=1:return None
    pending=[(selection['element_id'],None,0)];seen=set()
    while pending:
        node,first,depth=pending.pop(0)
        if node==destinations[0]:return first
        if depth>=6 or node in seen:continue
        seen.add(node)
        for edge in ui.get('graph',[]):
            if edge.get('kind')=='selection_movement_hypothesis' and edge['origin']==node and edge.get('confidence',0)>=.75:pending.append((edge['destination'],first or edge['action'],depth+1))
    return None


def paint_ui(canvas,ui,bounds):
    """Presentation only, always on the matching observation image."""
    x,y,w,h=bounds
    for e in ui.get('elements',[])[:128]:
        b=e['bbox'];coords=[x+b[0]*w,y+b[1]*h,x+b[2]*w,y+b[3]*h]
        canvas.create_rectangle(*coords,outline='#59cf9c',tags='ui_boxes')
        canvas.create_text(coords[0],coords[1],text=e.get('text','')[:20],anchor='sw',fill='#a4f7d3',font=('',8),tags='ui_boxes')
    for cell in ui.get('cells',[])[:128]:
        if cell.get('kind')=='visual_glyph_cell_hypothesis':
            b=cell['bbox'];canvas.create_rectangle(x+b[0]*w,y+b[1]*h,x+b[2]*w,y+b[3]*h,outline='#8b9cae',dash=(2,3),tags='ui_boxes')
    by_id={e['id']:e for e in ui.get('elements',[])+ui.get('cells',[])}
    for c in ui.get('candidates',[])[:12]:
        b=c['bbox'];canvas.create_rectangle(x+b[0]*w,y+b[1]*h,x+b[2]*w,y+b[3]*h,outline='#eeb94f',dash=(3,3),tags='ui_boxes')
        for r in c.get('relations',[])[:1]:
            e=by_id.get(r['element_id'])
            if e:canvas.create_line(x+c['center'][0]*w,y+c['center'][1]*h,x+e['center'][0]*w,y+e['center'][1]*h,fill='#d6b773',dash=(2,3),tags='ui_boxes')
    selected=ui.get('selection',{})
    if selected.get('status')=='supported_association' and selected.get('bbox'):
        b=selected['bbox'];canvas.create_rectangle(x+b[0]*w,y+b[1]*h,x+b[2]*w,y+b[3]*h,outline='#6ce9ff',width=3,tags='ui_boxes')
