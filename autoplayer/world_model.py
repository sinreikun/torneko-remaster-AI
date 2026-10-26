"""Screen-only observations. IDs carry no inferred game meaning.
Appearance, map legend, OCR names and causal hypotheses are separate fields.
"""
import copy
import hashlib
import io
import json
import math
import re
import time
import unicodedata
from collections import deque


def labels(native):return [unicodedata.normalize('NFKC',l.get('text','')).strip() for l in (native or {}).get('lines',[])] if (native or {}).get('status')=='ok' else []
def metrics(state):return {k:v.get('values') for k,v in (state or {}).get('metrics',{}).items()}
def changes(a,b):return {k:{'before':a.get(k),'after':b.get(k)} for k in set(a)|set(b) if a.get(k)!=b.get(k)}

class VisualSense:
    def __init__(self):self.previous=None;self.natural=[0.]*192;self.pitch_samples=[];self.pitch=None
    def read(self,png,neutral_png=None):
        from PIL import Image
        def grid(data):
            with Image.open(io.BytesIO(data)) as im:return list(im.convert('L').crop((0,im.height*.18,im.width,im.height)).resize((16,12)).tobytes())
        try:
            current=grid(png);natural=grid(neutral_png) if neutral_png else None
        except (OSError,ValueError):return {'status':'unavailable','scene_change':True}
        if natural:
            self.natural=[max(old*.8,abs(x-y)) for old,x,y in zip(self.natural,current,natural)]
        activity=[] if self.previous is None else [abs(x-y)>max(14,n*2) for x,y,n in zip(current,self.previous,self.natural)]
        fraction=sum(activity)/max(1,len(activity));self.previous=current
        return {'status':'observed','scene_change':fraction>.45,'unexpected_fraction':round(fraction,4),
            'natural_activity':round(sum(n>14 for n in self.natural)/192,4),'tile_pitch':self.pitch,
            'rule':'画素変化は動作・種類・移動の確定ではない。中立観測のアニメーションを差し引いた変化。'}
    def regions(self,png,neutral_png):
        """Anonymous animated patches, never automatic NPC/enemy labels."""
        if not self.pitch or not neutral_png:return []
        from PIL import Image,ImageChops,ImageStat
        try:
            with Image.open(io.BytesIO(png)) as im:a=im.convert('RGB').resize((320,round(im.height*320/im.width)))
            with Image.open(io.BytesIO(neutral_png)) as im:b=im.convert('RGB').resize(a.size)
        except (OSError,ValueError):return []
        t=320*self.pitch;out=[]
        for dy in range(-3,4):
            for dx in range(-4,5):
                cx=a.width*.5+dx*t;cy=a.height*.48+dy*t
                box=(round(cx-t*.4),round(cy-t*.4),round(cx+t*.4),round(cy+t*.4))
                if box[0]<0 or box[1]<a.height*.2 or box[2]>a.width or box[3]>a.height:continue
                score=sum(ImageStat.Stat(ImageChops.difference(a.crop(box),b.crop(box))).mean)/3
                if score>4:out.append({'offset':[dx,dy],'bbox_normalized':[box[0]/a.width,box[1]/a.height,box[2]/a.width,box[3]/a.height],
                    'appearance':hashlib.sha256(a.crop(box).resize((8,8)).tobytes()).hexdigest()[:16],'activity':round(score,2)})
        return sorted(out,key=lambda p:-p['activity'])[:12]

    def correlate(self,before_png,after_png,delta):
        from PIL import Image,ImageChops,ImageStat
        if sum(abs(v) for v in delta)!=1:return
        try:
            with Image.open(io.BytesIO(before_png)) as im:a=im.convert('L').resize((160,90))
            with Image.open(io.BytesIO(after_png)) as im:b=im.convert('L').resize((160,90))
        except (OSError,ValueError):return
        axis=0 if delta[0] else 1
        # Fixed borders/HUD and the player are not registration evidence.
        boxes=[(25,24,65,65),(95,24,135,65)] if axis==1 else [(30,20,130,36),(30,58,130,74)]
        def error(shift):
            dx=shift if axis==0 else 0;dy=shift if axis==1 else 0
            errors=[]
            for box in boxes:
                shifted=tuple(v+(dx if i%2==0 else dy) for i,v in enumerate(box))
                errors.append(ImageStat.Stat(ImageChops.difference(a.crop(box),b.crop(shifted))).mean[0])
            return sum(errors)/len(errors)
        scores=[(error(s),s) for s in range(-14,15) if abs(s)>=3];best,shift=min(scores);base=error(0)
        if base<=4 or best>=base*.7 or shift*delta[axis]>=0:return
        self.pitch_samples=(self.pitch_samples+[abs(shift)/160])[-12:]
        if len(self.pitch_samples)>=3:
            mid=sorted(self.pitch_samples)[len(self.pitch_samples)//2]
            if sum(abs(v-mid)<.009 for v in self.pitch_samples)>=3:self.pitch=mid

class Inventory:
    def __init__(self,scope='',confirm_key='num1'):self.confirm_key=confirm_key;self.scope=scope;self.rows=[];self.pages={};self.selected=None;self.serial=0;self.last_seen=None;self.effects=[];self.identifications={};self.age=0;self.selected_age=0;self.was_inventory=False
    @staticmethod
    def cursor_line(native,png=None):
        explicit=[s for s in labels(native) if re.match(r'^[▶►▷>●]',s)]
        if len(explicit)==1:return explicit[0]
        if explicit or not png:return None
        from PIL import Image
        from .minimap import components
        found=[]
        try:
            with Image.open(io.BytesIO(png)) as source:im=source.convert('RGB')
            for line in (native or {}).get('lines',[]):
                boxes=[w['box'] for w in line.get('words',[]) if len(w.get('box',[]))==4]
                if not boxes:continue
                x=min(b[0] for b in boxes);y=min(b[1] for b in boxes);h=max(b[1]+b[3] for b in boxes)-y
                if h<8:continue
                left=max(0,round(x-h*2));right=min(im.width,round(x));top=max(0,round(y));bottom=min(im.height,round(y+h))
                pts={(xx,yy) for yy in range(top,bottom) for xx in range(left,right) if min(im.getpixel((xx,yy)))>180 and max(im.getpixel((xx,yy)))-min(im.getpixel((xx,yy)))<50}
                for region in components(pts):
                    xs=[p[0] for p in region];ys=[p[1] for p in region];rw=max(xs)-min(xs)+1;rh=max(ys)-min(ys)+1
                    if rh<h*.55 or not .35<=rw/rh<=1.5 or not .3<=len(region)/(rw*rh)<=.75:continue
                    rows=[sum(yy==cy for xx,yy in region) for cy in range(min(ys),max(ys)+1)]
                    center=max(1,len(rows)//3)
                    if sum(rows[center:-center])/max(1,len(rows)-2*center)>max(sum(rows[:center]),sum(rows[-center:]))/center*1.35:
                        found.append(line.get('text',''));break
        except (OSError,ValueError):return None
        return found[0] if len(found)==1 else None

    def observe(self,native,png=None):
        from .interfaces import torneko_menu_kind
        self.age+=1
        text=labels(native);kind=torneko_menu_kind(native or {})
        if self.age-self.selected_age>3:self.selected=None
        for quote in text:
            match=re.fullmatch(r'[「『]?(.+?)[」』]?(?:の正体は|は)[「『]?(.+?)[」』]?(?:だった|であった|とわかった|と分かった)[。!！]?',quote)
            if match and any(r['display_name']==match[1] for r in self.rows):
                self.identifications[match[1]]={'true_name':match[2],'quote':quote,'status':'identified_message_unverified_ocr'}
        for row in self.rows:
            if row['display_name'] in self.identifications:row.update(self.identifications[row['display_name']],name_status='identified_message_unverified_ocr')
        if kind!='inventory':
            self.was_inventory=False
            return {'status':'not_visible','last_observed':self.last_seen,'complete':False,'selected_id':self.selected,'rows':[]}
        if not self.was_inventory:self.pages={}
        self.was_inventory=True
        page=None
        for s in text:
            m=re.fullmatch(r'(?:ページ\s*)?(\d+)\s*/\s*(\d+)(?:\s*ページ)?',s) if 'ページ' in s else None
            if m:page=[int(m[1]),int(m[2])]
        previous={}
        for row in self.rows:previous.setdefault(row['display_name'],[]).append(row)
        rows=[];self.selected=None;cursor=self.cursor_line(native,png)
        for line in (native or {}).get('lines',[]):
            raw=unicodedata.normalize('NFKC',line.get('text','')).strip()
            # Require a list entry/cursor or equipment indicator; headings and
            # arbitrary dialogue must never become possessions.
            m=re.match(r'^([▶►▷>●]|[a-zA-Z][).:：]|[0-9]+[).:：]|[EＥ]\s+)[ \t]*(.+)$',raw)
            if not m:
                if not line.get('words') or re.search(r'道具|詳細表示|整理|持ち物|装備|HP|Lv|満腹|ちから|剣の強さ|盾の強さ|ページ|所持金|[0-9]+\s*/\s*[0-9]+',raw) or re.fullmatch(r'\d+\s*階|\d+\s*[GＧ]',raw) or raw in ('投げる','置く','食べる','飲む','読む','振る','説明','外す') or not re.search(r'[ぁ-んァ-ン一-龥]',raw):continue
                name=raw;prefix=''
            else:name=m[2].strip();prefix=m[1]
            if not name or name in ('装備','道具','持ち物','使う','食べる','投げる','置く'):continue
            quantity=None;q=re.search(r'[×x]\s*(\d+)$',name)
            if q:quantity=int(q[1]);name=name[:q.start()].rstrip()
            candidates=previous.get(name,[]);old=candidates.pop(0) if len(candidates)==1 else None
            if old:identifier=old['id']
            else:self.serial+=1;identifier=f'item_{self.scope}{self.serial:06d}'
            selected=prefix in ('▶','►','▷','>','●') or (cursor is not None and raw==cursor)
            row={'id':identifier,'display_name':name,'game_alias':name if re.search(r'未識別|未鑑定|仮名',raw) else None,'name_status':'game_alias_unidentified' if re.search(r'未識別|未鑑定|仮名',raw) else 'unknown','true_name':None,'quantity':quantity,
                'equipped':True if prefix.strip()=='E' else None,'quote':raw,'selected':selected,
                'identity_status':'matched_display_name' if old else 'new_or_ambiguous','page':page}
            if name in self.identifications:row.update(self.identifications[name],name_status='identified_message_unverified_ocr')
            rows.append(row)
            if selected:self.selected=identifier;self.selected_age=self.age
        occupancy=None;counts=set();height=int.from_bytes(png[20:24],'big') if png and png.startswith(b'\x89PNG') and len(png)>=24 else None
        for line in (native or {}).get('lines',[]):
            value=line.get('text','')
            if re.search(r'HP|Lv|ちから|満腹',value,re.I):continue
            boxes=[w['box'] for w in line.get('words',[]) if len(w.get('box',[]))==4]
            if height and boxes and max(b[1]+b[3] for b in boxes)<height*.18:continue
            for match in re.finditer(r'(?<!\d)(\d{1,2})\s*/\s*20(?!\d)',value):
                if int(match[1])<=20:counts.add(int(match[1]))
        if len(counts)==1:occupancy={'count':next(iter(counts)),'capacity':20,'source':'current_inventory_ocr','unverified_ocr':True}
        empty=any(re.fullmatch(r'(?:道具|持ち物|所持品)?\s*[（(]?0\s*/\s*20[)）]?',s) for s in text)
        if not rows and not empty:
            return {'status':'unreadable_entries','rows':[],'last_observed_rows':copy.deepcopy(self.rows),'last_observed':self.last_seen,'complete':False}
        self.rows=rows;self.last_seen=(native or {}).get('captured_at')
        if page and 1<=page[0]<=page[1]<=100:self.pages[page[0]]={'total':page[1],'rows':copy.deepcopy(rows)}
        complete=bool(page and all(i in self.pages and self.pages[i]['total']==page[1] for i in range(1,page[1]+1)))
        return {'status':'observed' if rows or empty else 'unreadable_entries','rows':copy.deepcopy(rows),'page':page,
            'occupancy':occupancy,'complete':complete,'selected_id':self.selected,'last_observed':self.last_seen,
            'rule':'表示名の正体は未確認。未確認ページや以前の所持品を現在所持と断定しない。'}
    def learn(self,row,before,after,old_ocr,new_ocr,natural,before_png=None):
        text=labels(new_ocr);old_text=labels(old_ocr)
        selection=self.cursor_line(old_ocr,before_png)
        command=next((v for v in ('装備','はずす','外す','食べる','飲む','読む','使う','振る','投げる','置く') if selection and v in selection),None)
        if row.get('observed_keys',row.get('keys',[]))!=[self.confirm_key]:return None
        if not self.selected or not command or not row.get('executed') or row.get('turn_kind')=='aborted' or (row.get('input_receipt') or {}).get('os_submission')!='accepted':return None
        # A menu merely containing "eat" does not prove that command was chosen.
        selection=self.cursor_line(old_ocr,before_png)
        if not selection or command not in selection:return None
        diff=changes(metrics(before),metrics(after));confounds=[k for k,v in (natural or {}).items() if v]
        new_messages=[s for s in text if s not in old_text]
        item=next((r for r in self.rows if r['id']==self.selected),{})
        evidence={'item_id':self.selected,'display_name':item.get('display_name'),'true_name':item.get('true_name'),'name_status':item.get('name_status'),'command_quote':selection,'observed_metrics_before':metrics(before),'observed_metrics_after':metrics(after),'metric_changes':diff,'messages':new_messages[:12],
            'confounds':confounds+(['level_change'] if 'level' in diff else []),'attribution':'temporal_association_not_causal','utility':'unresolved' if not diff else 'context_dependent',
            'equipment_effect':{k:v for k,v in diff.items() if k in ('strength','sword','shield')} if command in ('装備','はずす','外す') else {},
            'not_a_failure':not diff,'hp_ratio':metrics(before).get('hp'),'fullness':metrics(before).get('fullness')}
        self.effects=(self.effects+[evidence])[-64:]
        return evidence

class WorldModel:
    def __init__(self,memory=None,session='session'):
        self.db=memory.db if memory else None;self.profile=memory.profile if memory else 'session';self.session=session
        from .appearance import AppearanceBank
        self.appearances=AppearanceBank(self.db,self.profile)
        from .battle_appearance import BattleAppearanceMemory
        self.battle_appearances=BattleAppearanceMemory(self.db,self.profile)
        from .state_snapshot import StateSnapshot
        self.state_snapshot=StateSnapshot()
        self.memory=memory;self.last_known_metrics=copy.deepcopy(getattr(memory,'last_known_status',{}))
        self.scope=hashlib.sha256((self.profile+session).encode()).hexdigest()[:8]+'_'
        cfg=getattr(memory,'settings',getattr(memory,'cfg',None))
        bindings=getattr(cfg,'key_bindings',{}) or {}
        self.confirm_key=next((k for k,v in bindings.items() if re.search(r'決定|confirm',v,re.I)),'num1')
        from .floor_navigation import FloorNavigation
        self.floor_navigation=FloorNavigation(self.confirm_key)
        self.visual=VisualSense();self.inventory=Inventory(self.scope+'0_',self.confirm_key);self.floor=None;self.objects={};self.serial=0;self.tick=0;self.visited=set();self.previous_metrics={};self.latest={};self.pending_effects=[];self.delayed=[];self.result_seen=False;self.episode=0;self.last_messages=[]
        if self.db:
            self.db.executescript('''CREATE TABLE IF NOT EXISTS world_events(id INTEGER PRIMARY KEY,profile TEXT,session TEXT,created REAL,kind TEXT,data TEXT);
            CREATE INDEX IF NOT EXISTS world_events_profile ON world_events(profile,id);
            CREATE TABLE IF NOT EXISTS item_use_stats(profile TEXT,item_key TEXT,command TEXT,context TEXT,data TEXT,PRIMARY KEY(profile,item_key,command,context));''');self.db.commit()
    def save(self,kind,data):
        if self.db:
            self.db.execute('INSERT INTO world_events(profile,session,created,kind,data) VALUES(?,?,?,?,?)',(self.profile,self.session,time.time(),kind,json.dumps({'episode':self.episode,'observation':data},ensure_ascii=False)));self.db.commit()
    def observe(self,png,native,state,neutral_png=None):
        from .interfaces import result_screen
        digest=hashlib.sha256(png).hexdigest()
        if native and native.get('frame_sha256') and native['frame_sha256']!=digest:native={'status':'frame_mismatch','lines':[]}
        self.tick+=1;state=state or {};m=state.get('minimap',{});floor=metrics(state).get('floor');vision=self.visual.read(png,neutral_png)
        if result_screen(native or {}):self.result_seen=True
        new_adventure=bool(self.result_seen and floor and state.get('eligible'))
        if new_adventure:
            self.last_known_metrics={};self.state_snapshot=type(self.state_snapshot)()
            self.episode+=1;self.inventory=Inventory(self.scope+str(self.episode)+'_',self.confirm_key);self.pending_effects=[];self.delayed=[];self.result_seen=False
        if new_adventure or (floor and self.floor and floor!=self.floor):
            self.objects={};self.visited=set();self.visual=VisualSense();self.floor_navigation.reset()
        if floor:self.floor=floor
        self.floor_navigation.observe(state)
        state['floor_exploration']=self.floor_navigation.context()
        player=m.get('player_cell');new_cell=bool(player and tuple(player) not in self.visited);observed=m.get('status')=='observed';new=[];seen=set();current_objects=[]
        if observed and player:
            self.visited.add(tuple(player))
            for category,field in [('player','player'),('enemy_indicator','enemies'),('item_indicator','items'),('stair_indicator','stairs')]:
                offsets=[(0,0)] if field=='player' else m.get(field,[])
                for dx,dy in offsets:
                    cell=(player[0]+dx,player[1]+dy)
                    candidates=[o for o in self.objects.values() if o['id'] not in seen and o['legend']==category and (category=='player' or self.tick-o['last_tick']<=2) and (category=='player' or max(abs(cell[i]-o['cell'][i]) for i in (0,1))<=(1 if category=='enemy_indicator' else 0))]
                    exact=[o for o in candidates if o['cell']==list(cell)]
                    matched=exact[0] if len(exact)==1 else candidates[0] if len(candidates)==1 else None
                    if matched is None:
                        self.serial+=1;identifier=f'object_{self.scope}{self.serial:06d}'
                        matched={'id':identifier,'legend':category,'identity':'unconfirmed','name':None,'species':None,'attitude':'unknown','facing':'unknown','animation':'unknown','variants':[]}
                        self.objects[identifier]=matched;new.append(identifier)
                    matched.update(cell=list(cell),last_tick=self.tick,offset=[dx,dy]);seen.add(matched['id'])
                    # Correlation is deliberately a hypothesis until screen grid
                    # registration has repeated motion support. Never a species label.
                    pitch=self.visual.pitch
                    if pitch:
                        try:
                            from PIL import Image
                            with Image.open(io.BytesIO(png)) as im:
                                t=im.width*pitch;cx=im.width*.5+dx*t;cy=im.height*.48+dy*t
                                if 0<=cx<im.width and 0<=cy<im.height:
                                    patch=im.convert('RGB').crop((cx-t*.45,cy-t*.45,cx+t*.45,cy+t*.45)).resize((8,8))
                                    matched['appearance_memory']=self.appearances.observe(patch,(matched.get('appearance_memory') or {}).get('class_id'),(matched.get('hypotheses') or {}).get('facing','unknown'))
                                    variant=hashlib.sha256(patch.tobytes()).hexdigest()[:16]
                                    if variant not in matched['variants']:matched['variants']=(matched['variants']+[variant])[-64:]
                                    matched['screen_association']={'status':'hypothesis','bbox':[round(cx-t/2),round(cy-t/2),round(cx+t/2),round(cy+t/2)],'basis':'background_shift_and_player_center_prior'}
                        except (OSError,ValueError):pass
                    visible=copy.deepcopy(matched);visible['appearance_variant_count']=len(visible.pop('variants',[]));current_objects.append(visible)
        for patch in self.visual.regions(png,neutral_png):
            if any(o.get('offset')==patch['offset'] for o in current_objects):continue
            cell=[player[i]+patch['offset'][i] for i in (0,1)] if observed and player else None
            matches=[o for o in self.objects.values() if o['legend']=='unclassified_region' and self.tick-o['last_tick']<=2 and ((cell is not None and o.get('cell')==cell) or (cell is None and o.get('cell') is None and o.get('offset')==patch['offset']))]
            if len(matches)==1:obj=matches[0]
            else:
                self.serial+=1;identifier=f'object_{self.scope}{self.serial:06d}'
                obj={'id':identifier,'legend':'unclassified_region','identity':'unconfirmed','name':None,'species':None,'attitude':'unknown','facing':'unknown','animation':'unknown','variants':[]}
                self.objects[identifier]=obj;new.append(identifier)
            obj.update(cell=cell,offset=patch['offset'],last_tick=self.tick,screen_association={'status':'hypothesis','bbox_normalized':patch['bbox_normalized'],'basis':'registered_grid_animated_region'})
            try:
                from PIL import Image
                with Image.open(io.BytesIO(png)) as im:
                    box=[v*(im.width if i%2==0 else im.height) for i,v in enumerate(patch['bbox_normalized'])]
                    obj['appearance_memory']=self.appearances.observe(im.crop(box),(obj.get('appearance_memory') or {}).get('class_id'),(obj.get('hypotheses') or {}).get('facing','unknown'))
            except (OSError,ValueError):pass
            if patch['appearance'] not in obj['variants']:obj['variants']=(obj['variants']+[patch['appearance']])[-64:]
            visible=copy.deepcopy(obj);visible['appearance_variant_count']=len(visible.pop('variants',[]));current_objects.append(visible)
        known={tuple(c) for c in m.get('accumulated_cells',[])}
        frontier=self.floor_navigation.primary() if self.floor_navigation._valid(state) else set()
        targets={tuple(o['cell']) for o in current_objects if o.get('cell') and o['legend'] not in ('player',)}
        surveyed=known-frontier-targets
        for key,value in state.get('metrics',{}).items():
            if key in ('strength','sword','shield','experience','max_floor','level','gold'):
                self.last_known_metrics[key]={'values':copy.deepcopy(value.get('values')),'frame_sha256':digest,'observed_at':time.time(),'status':'last_observed_not_current_confirmation'}
        if self.memory:self.memory.last_known_status=self.last_known_metrics
        now_metrics=metrics(state);delta=changes(self.previous_metrics,now_metrics);self.previous_metrics=copy.deepcopy(now_metrics)
        inv=self.inventory.observe(native,png)
        self.state_snapshot.observe(state.get('metrics',{}),inv,self.tick,bool(m.get('enemies')) if m.get('status')=='observed' else None)
        state['retained_state']=self.state_snapshot.context()
        self.floor_navigation.inventory_updated(self.state_snapshot.inventory_token,(self.state_snapshot.inventory or {}).get('occupancy'),inv.get('status')=='observed')
        item_state=self.floor_navigation.item_tasks.context()
        if item_state['needs_inventory_check']:self.state_snapshot.request('floor_item_needs_check',item_state['scope']+item_state['check_token'])
        state['retained_state']=self.state_snapshot.context()
        current_messages=[s for s in labels(native) if not re.search(r'HP|Lv|満腹|\d+\s*階|ちから|剣の強さ|盾の強さ|^\d+[GＧ%％]?$|^G$',s,re.I)]
        new_messages=[s for s in current_messages if s not in self.last_messages]
        self.last_messages=current_messages
        self.latest={'frame_sha256':digest,'map_frame_sha256':state.get('frame_sha256'),'tick':self.tick,'new_adventure':new_adventure,'episode':self.episode,'metrics':now_metrics,'metric_changes':delta,
            'new_messages':new_messages[:12],'visual':vision,'scene_change':vision.get('scene_change',True),'objects':current_objects,'new_objects':new,
            'novelty':min(1.,len(new)/3+.15*new_cell+.25*(bool(player) and tuple(player) in frontier)),
            'exploration':{'known_cells':len(known),'visited_cells':len(self.visited),'frontier_cells':len(frontier),'frontier_candidates':[list(c) for c in sorted(frontier)[:40]],
                'surveyed_cell_count':len(surveyed),'surveyed_cells':[list(c) for c in sorted(surveyed)[:40]],'not_walls':True,'rule':'未踏または未処理の先端を探索候補とし、探索完了の枝は優先度を下げる。探索不要は通行禁止ではなく、帰路・階段・回収のため再利用できる。'},
            'inventory':inv,'identity_rule':'数値IDは識別用。画像から正体・敵種・非敵対NPCを確定しない。姿の変化は別個体の証明ではない。'}
        return copy.deepcopy(self.latest)
    def navigation_hint(self,state,preference='current_goal',mission='auto',plan_id=None,item_decisions=None):
        hint=self.floor_navigation.hint(state,preference,mission,plan_id,item_decisions)
        state['floor_exploration']=self.floor_navigation.context()
        return hint

    def annotate(self,report,native):
        """Model names never become internal IDs or verified classifications."""
        text='\n'.join(labels(native));accepted=[]
        for item in (report or {}).get('entities',[])[:20]:
            obj=self.objects.get(item.get('object_id')) if isinstance(item,dict) else None
            if obj is None:continue
            entry={k:str(item.get(k,'unknown'))[:160] for k in ('kind_hypothesis','species_hypothesis','attitude_hypothesis','facing','action_hypothesis')}
            entry['status']='model_hypothesis_unverified'
            quote=item.get('name_quote','')
            if isinstance(quote,str) and quote.strip() and quote in text:entry['ocr_quote']=quote
            obj['hypotheses']=entry;accepted.append({'id':obj['id'],**entry})
        if accepted:self.save('object_hypotheses',accepted)
        return accepted

    def feedback(self,before,after,row,old_ocr,new_ocr,natural,before_png,after_png):
        after_map=after.get('minimap',{})
        self.state_snapshot.observe(after.get('metrics',{}),{},self.tick+1,bool(after_map.get('enemies')) if after_map.get('status')=='observed' else None)
        self.state_snapshot.feedback(before,after,old_ocr,new_ocr,row.get('step'))
        after['retained_state']=self.state_snapshot.context()
        row['retained_state']=copy.deepcopy(after['retained_state'])
        self.floor_navigation.record(before,after,row,natural)
        self.floor_navigation.item_tasks.feedback(row,before,after,old_ocr,new_ocr)
        item_state=self.floor_navigation.item_tasks.context()
        if item_state['needs_inventory_check']:self.state_snapshot.request('floor_item_needs_check',item_state['scope']+item_state['check_token'])
        marker=(item_state['scope'],item_state['revision'])
        if getattr(self,'saved_item_marker',None)!=marker:
            self.save('floor_item_tasks',item_state);self.saved_item_marker=marker
        if self.floor_navigation._valid(after):self.navigation_hint(after)
        after['floor_exploration']=self.floor_navigation.context()
        row['floor_exploration']=copy.deepcopy(after['floor_exploration'])
        a=(before or {}).get('minimap',{}).get('player_cell');b=(after or {}).get('minimap',{}).get('player_cell')
        if a and b and metrics(before).get('floor')==metrics(after).get('floor') and row.get('executed'):
            self.visual.correlate(before_png,after_png,[b[i]-a[i] for i in (0,1)])
        effect=self.inventory.learn(row,before,after,old_ocr,new_ocr,natural,before_png)
        if effect:
            self.state_snapshot.request('item_use_or_equipment_effect',row.get('step'))
            self.save('item_effect',effect)
            self.record_item_value(effect)
        later=[]
        for p in list(self.pending_effects):
            p['remaining']-=1
            if p['remaining']<4:
                later.append({'item_id':p['effect']['item_id'],'lag_turns':4-p['remaining'],
                    'metrics':metrics(after),'messages':labels(new_ocr)[:8],'intervening_keys':row.get('observed_keys',[]),
                    'attribution':'delayed_association_with_intervening_actions'})
            if p['remaining']<=0:self.pending_effects.remove(p)
        if effect:self.pending_effects.append({'effect':effect,'remaining':4})
        if later:self.save('delayed_item_effect',later);self.delayed=(self.delayed+later)[-8:]
        adjacent=[o for o in self.latest.get('objects',[]) if o.get('legend')=='enemy_indicator' and max(abs(v) for v in o.get('offset',[99,99]))==1]
        combat_links=[{'object_candidate':adjacent[0]['id'] if len(adjacent)==1 else None,'message':event,'status':'message_association_unverified','orientation':'unknown'} for event in row.get('battle_observations',[])]
        visual_links=self.battle_appearances.record(self.latest,row,before,after,self.appearances)
        if combat_links:self.save('object_combat_association',combat_links)
        result={'visual_battle_links':visual_links,'combat_links':combat_links,'item_effect':effect,'delayed_effects':later,'screen_tile_pitch':self.visual.pitch}
        after['retained_state']=self.state_snapshot.context();row['retained_state']=copy.deepcopy(after['retained_state'])
        self.save('turn',{'observation':self.latest,'result_metrics':metrics(after),'keys':row.get('observed_keys',[]),'effects':result})
        return result
    def record_item_value(self,effect):
        if not self.db:return
        key='identified:'+effect['true_name'] if effect.get('true_name') else 'instance:'+effect['item_id']
        hp=effect.get('hp_ratio');food=effect.get('fullness')
        context=json.dumps([round(hp[0]/hp[1]*3) if hp and hp[1] else None,food[0]//25 if food else None])
        args=(self.profile,key,effect['command_quote'],context)
        old=self.db.execute('SELECT data FROM item_use_stats WHERE profile=? AND item_key=? AND command=? AND context=?',args).fetchone()
        stats=json.loads(old[0]) if old else {'trials':0,'confounded_trials':0,'dimensions':{},'status':'observed_association_not_causal'}
        stats['trials']+=1
        if effect.get('confounds'):stats['confounded_trials']+=1
        else:
            pairs={k:{'before':v,'after':effect.get('observed_metrics_after',{}).get(k)} for k,v in effect.get('observed_metrics_before',{}).items()}
            for k,v in pairs.items():
                if k not in ('hp','fullness','sword','shield','strength') or not v.get('before') or not v.get('after'):continue
                delta=v['after'][0]-v['before'][0];d=stats['dimensions'].setdefault(k,{'samples':0,'sum':0,'min':delta,'max':delta})
                d.update(samples=d['samples']+1,sum=d['sum']+delta,min=min(d['min'],delta),max=max(d['max'],delta))
        self.db.execute('INSERT INTO item_use_stats VALUES(?,?,?,?,?) ON CONFLICT(profile,item_key,command,context) DO UPDATE SET data=excluded.data',(*args,json.dumps(stats,ensure_ascii=False)));self.db.commit()
    def item_values(self):
        if not self.db:return []
        out=[]
        for item in self.inventory.rows[:20]:
            key='identified:'+item['true_name'] if item.get('true_name') else 'instance:'+item['id']
            for command,context,data in self.db.execute('SELECT command,context,data FROM item_use_stats WHERE profile=? AND item_key=? LIMIT 4',(self.profile,key)):
                out.append({'item_id':item['id'],'command':command,'context':json.loads(context),'estimate':json.loads(data)})
        return out[:12]

    def context(self):
        l=self.latest
        return {k:l[k] for k in ('metrics','metric_changes','visual','exploration','inventory','identity_rule') if k in l}|{
            'retained_state':self.state_snapshot.context(),
            'visual_battle_experience':self.battle_appearances.context(l.get('objects',[])),
            'floor_exploration':self.floor_navigation.context(),
            'last_known_metrics':copy.deepcopy(self.last_known_metrics),
            'objects':[{k:v for k,v in o.items() if k not in ('variants',)} for o in l.get('objects',[])[:20]],
            'item_effects':self.inventory.effects[-4:],'delayed_item_effects':self.delayed[-4:],'item_value_estimates':self.item_values()}
