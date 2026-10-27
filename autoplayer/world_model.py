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
    def __init__(self,scope='',confirm_key='num1'):self.confirm_key=confirm_key;self.scope=scope;self.rows=[];self.pages={};self.selected=None;self.serial=0;self.last_seen=None;self.effects=[];self.identifications={};self.age=0;self.selected_age=0;self.was_inventory=False;self.description_reads=[];self.description_text=set();self.description_panel={}
    @staticmethod
    def line_box(line):
        boxes=[w['box'] for w in line.get('words',[]) if len(w.get('box',[]))==4]
        return [min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[0]+b[2] for b in boxes),max(b[1]+b[3] for b in boxes)] if boxes else None
    @staticmethod
    def cursor_index(native,png=None):
        from .interfaces import torneko_menu_kind, MAIN_MENU_LABELS, ITEM_ACTION_LABELS, compact_text, ordinary_choice
        lines=(native or {}).get('lines',[])
        clean=lambda text:compact_text(text).lstrip('▶►▷>●→')
        visible={clean(line.get('text','')) for line in lines}
        kind=torneko_menu_kind(native)
        # Focus an active choice/action popup rather than a cursor left visible
        # on the underlying item list or main menu. No screen coordinates here.
        targets={'はい','いいえ','Yes','No','yes','no'} if ordinary_choice(native) else ITEM_ACTION_LABELS if kind=='item_actions' or {'投げる','置く'}<=visible else MAIN_MENU_LABELS if kind=='main_menu' else None
        candidates=[line for line in lines if clean(line.get('text','')) and (targets is None or clean(line.get('text','')) in targets)]
        explicit=[lines.index(line) for line in candidates if re.match(r'^[▶►▷>●→]',compact_text(line.get('text','')))]
        if len(explicit)==1:return explicit[0]
        if explicit:return None
        def bounds(line):
            boxes=[w['box'] for w in line.get('words',[]) if len(w.get('box',[]))==4]
            return (min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[0]+b[2] for b in boxes),max(b[1]+b[3] for b in boxes)) if boxes else None
        # OCR sometimes emits the triangle as its own line, detached from its
        # label. Associate only a nearby item to its right at the same height.
        detached=[]
        for arrow in lines:
            if compact_text(arrow.get('text','')) not in ('▶','►','▷','>','→'):continue
            a=bounds(arrow)
            if not a:continue
            near=[]
            for line in candidates:
                b=bounds(line)
                if not b:continue
                h=max(a[3]-a[1],b[3]-b[1]);gap=b[0]-a[2]
                if h>0 and -h*.2<=gap<=h*2 and abs((a[1]+a[3]-b[1]-b[3])/2)<=h*.4:near.append((max(0,gap),lines.index(line)))
            near.sort()
            if near and (len(near)==1 or near[0][0]+2<near[1][0]):detached.append(near[0][1])
        if len(detached)==1:return detached[0]
        if detached or not png:return None
        from PIL import Image
        from .minimap import components
        found=[]
        try:
            with Image.open(io.BytesIO(png)) as source:im=source.convert('RGB')
            pixels=im.load()
            for line in candidates:
                boxes=[w['box'] for w in line.get('words',[]) if len(w.get('box',[]))==4]
                if not boxes:continue
                x=min(b[0] for b in boxes);y=min(b[1] for b in boxes);h=max(b[1]+b[3] for b in boxes)-y
                if h<8:continue
                # OCR can merge the triangle into the selected label's box.
                # Include its leading portion; geometry must still identify a
                # unique triangle, never infer selection from the box alone.
                left=max(0,round(x-h*2));right=min(im.width,round(x+h*.8));top=max(0,round(y));bottom=min(im.height,round(y+h))
                pts={(xx,yy) for yy in range(top,bottom) for xx in range(left,right) if min(pixels[xx,yy])>180 and max(pixels[xx,yy])-min(pixels[xx,yy])<50}
                for region in components(pts):
                    xs=[p[0] for p in region];ys=[p[1] for p in region];rw=max(xs)-min(xs)+1;rh=max(ys)-min(ys)+1
                    if rh<h*.55 or not .35<=rw/rh<=1.5 or not .3<=len(region)/(rw*rh)<=.75:continue
                    # A right-pointing cursor has a mostly straight left edge;
                    # text fragments inside an expanded OCR box do not suffice.
                    left_edge=min(xs);top_edge=min(ys)
                    if sum((left_edge,yy) in region for yy in range(top_edge,top_edge+rh))/rh<.7:continue
                    rows=[0]*rh
                    for xx,yy in region:rows[yy-top_edge]+=1
                    center=max(1,len(rows)//3)
                    if sum(rows[center:-center])/max(1,len(rows)-2*center)>max(sum(rows[:center]),sum(rows[-center:]))/center*1.35:
                        found.append(lines.index(line));break
        except (OSError,ValueError):return None
        return found[0] if len(found)==1 else None

    @staticmethod
    def cursor_line(native,png=None):
        index=Inventory.cursor_index(native,png)
        return (native or {}).get('lines',[])[index].get('text','') if index is not None else None

    def observe(self,native,png=None):
        from .interfaces import torneko_menu_kind
        self.age+=1
        from .interfaces import compact_text
        text=labels(native);kind=torneko_menu_kind(native or {})
        if self.age-self.selected_age>3:self.selected=None
        for quote in text:
            match=re.fullmatch(r'[「『]?(.+?)[」』]?(?:の正体は|は)[「『]?(.+?)[」』]?(?:だった|であった|とわかった|と分かった)[。!！]?',quote)
            if match and any(r['display_name']==match[1] for r in self.rows):
                self.identifications[match[1]]={'true_name':match[2],'quote':quote,'status':'identified_message_unverified_ocr'}
        for row in self.rows:
            if row['display_name'] in self.identifications:row.update(self.identifications[row['display_name']],name_status='identified_message_unverified_ocr')
        if kind=='item_actions' and '装備' in [compact_text(t).lstrip('▶►▷>→') for t in text]:
            for row in self.rows:
                if row['id']==self.selected:row['equipment_affordance']={'action':'装備','source':'current_selected_item_actions_ocr','true_name_confirmed':False}
        if kind!='inventory':
            self.description_panel={'status':'not_visible'}
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
        item_labels={compact_text(r.get('item_name','')).lstrip('▶►▷>●→') for r in self.description_reads}|{compact_text(r['display_name']) for r in self.rows}
        stored_descriptions={compact_text(t) for r in self.description_reads if r.get('status')=='description_text_observed' and not all(compact_text(q).lstrip('▶►▷>●→') in item_labels for q in r.get('quotes',[])) for t in r.get('quotes',[])}
        rows=[];self.selected=None;cursor_index=self.cursor_index(native,png)
        cursor_box=self.line_box((native or {}).get('lines',[])[cursor_index]) if cursor_index is not None else None
        from .inventory_description import locate_description
        self.description_panel=locate_description(native,png,cursor_index,[r['display_name'] for r in self.rows])
        self.description_panel['frame_sha256']=(native or {}).get('frame_sha256')
        description_indices=set(self.description_panel.get('line_indices',[]))
        for line_index,line in enumerate((native or {}).get('lines',[])):
            if line_index in description_indices:continue
            raw=unicodedata.normalize('NFKC',line.get('text','')).strip()
            box=self.line_box(line)
            if compact_text(raw) in stored_descriptions or compact_text(raw) in self.description_text-item_labels or re.search(r'[。！？]',raw):continue
            if cursor_box and box:
                h=cursor_box[3]-cursor_box[1]
                if abs(box[0]-cursor_box[0])>h*1.3 or not h*.65<=box[3]-box[1]<=h*1.5:continue
            # Require a list entry/cursor or equipment indicator; headings and
            # arbitrary dialogue must never become possessions.
            m=re.match(r'^([▶►▷>●]|[a-zA-Z][).:：]|[0-9]+[).:：]|[EＥ]\s+)[ \t]*(.+)$',raw)
            if not m:
                if not line.get('words') or re.search(r'道具|詳細表示|整理|持ち物|装備|HP|Lv|満腹|^ちから$|剣の強さ|盾の強さ|ページ|所持金|[0-9]+\s*/\s*[0-9]+',raw) or re.fullmatch(r'\d+\s*階|\d+\s*[GＧ]',raw) or raw in ('投げる','置く','食べる','飲む','読む','振る','説明','外す') or not re.search(r'[ぁ-んァ-ン一-龥]',raw):continue
                name=raw;prefix=''
            else:name=m[2].strip();prefix=m[1]
            if not name or name in ('装備','道具','持ち物','使う','食べる','投げる','置く'):continue
            quantity=None;q=re.search(r'[×x]\s*(\d+)$',name)
            if q:quantity=int(q[1]);name=name[:q.start()].rstrip()
            from .inventory_identity import same_label,orange_text,equipped_indicator
            available=[r for values in previous.values() for r in values if r.get('page')==page and same_label(r['display_name'],name)]
            same_slot=[r for r in available if box and r.get('box') and abs((r['box'][1]+r['box'][3]-box[1]-box[3])/2)<=max(3,min(r['box'][3]-r['box'][1],box[3]-box[1])*.4)]
            old=same_slot[0] if len(same_slot)==1 else available[0] if len(available)==1 and not box and not available[0].get('box') else None
            if old:
                previous[old['display_name']].remove(old)
                if name!=old['display_name'] and same_label(name,old['display_name']):name=min((name,old['display_name']),key=len)
            color=orange_text(png,box)
            equipped_mark=equipped_indicator(png,box)
            if equipped_mark and name.startswith('巨') and (name[1:] in previous or any(compact_text(l.get('text','')).lstrip('▶►▷>●→')==name[1:] for l in (native or {}).get('lines',[]))):name=name[1:]
            alias=bool(re.search(r'未識別|未鑑定|仮名',raw) or color['status']=='orange_unidentified')
            if old:identifier=old['id']
            else:self.serial+=1;identifier=f'item_{self.scope}{self.serial:06d}'
            selected=prefix in ('▶','►','▷','>','●') or (cursor_index is not None and line_index==cursor_index)
            row={'id':identifier,'display_name':name,'game_alias':name if alias else None,'name_status':'game_alias_unidentified' if alias else 'unknown','identification_evidence':color,'identification_required':alias,'curse_status':'unknown','true_name':None,'quantity':quantity,
                'equipped':True if prefix.strip()=='E' or equipped_mark else None,'quote':raw,'selected':selected,
                'equipment_affordance':copy.deepcopy(old.get('equipment_affordance')) if old else None,'identity_status':'matched_page_row_not_verified_instance' if old else 'new_or_ambiguous','page':page,'line_index':line_index,'box':self.line_box(line)}
            if name in self.identifications and not alias:row.update(self.identifications[name],name_status='identified_message_unverified_ocr')
            known=self.known_description(row)
            if known:row['known_description']=known
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
            'description_panel':copy.deepcopy(self.description_panel),'occupancy':occupancy,'complete':complete,'selected_id':self.selected,'last_observed':self.last_seen,
            'rule':'表示名の正体は未確認。未確認ページや以前の所持品を現在所持と断定しない。'}
    @staticmethod
    def equipment_evidence(row):
        evidence=[]
        for source,text in [('display_name',row.get('display_name','')),('description_ocr',' '.join(row.get('known_description',{}).get('quotes',[])))]:
            if re.search(r'剣|つるぎ|盾|指輪|矢|装備|攻撃力|防御力',text):evidence.append({'source':source,'quote':text[:800]})
        if row.get('equipment_affordance'):evidence.append(copy.deepcopy(row['equipment_affordance']))
        return evidence

    def known_description(self,row):
        from .interfaces import compact_text
        name=compact_text(row.get('display_name',''))
        list_labels={compact_text(r.get('display_name','')).lstrip('▶►▷>●→') for r in self.rows}|{compact_text(r.get('item_name','')).lstrip('▶►▷>●→') for r in self.description_reads}
        for record in reversed(self.description_reads):
            if record.get('status')!='description_text_observed' or not record.get('quotes'):continue
            if all(compact_text(t).lstrip('▶►▷>●→') in list_labels for t in record['quotes']):continue
            if compact_text(record.get('item_name',''))!=name:continue
            if row.get('name_status')=='game_alias_unidentified' and (record.get('inventory_scope')!=self.scope or record.get('item_id') not in (None,row.get('id'))):
                # A type's base performance is common knowledge, not this
                # instance's identification, modifier or curse state.
                quotes=record.get('quotes',[])
                if re.search(r'剣|盾',name) and any(re.search(r'(?:攻撃|防御)力.*基本',unicodedata.normalize('NFKC',q)) for q in quotes):
                    common=[q for q in quotes if not re.search(r'呪|のろ|識別|鑑定|現在|[+−-]\s*\d',unicodedata.normalize('NFKC',q))]
                    if common:
                        result=copy.deepcopy(record);result.pop('item_id',None)
                        result.update(quotes=common,identity_scope='profile_common_equipment_description',knowledge_kind='common_type_description_not_individual_identification',individual_details_unconfirmed=True)
                        return result
                continue
            if record.get('identity_scope')!='profile_display_name' and record.get('inventory_scope')!=self.scope:continue
            return copy.deepcopy(record)
        return None

    def read_description(self,row,before_native,after_native,before_png,after_png):
        from .interfaces import compact_text,torneko_menu_kind
        if row.get('observed_keys')!=['num7'] or not row.get('executed') or row.get('dry_run') or row.get('turn_kind')=='aborted' or (row.get('input_receipt') or {}).get('os_submission')!='accepted':return None
        if torneko_menu_kind(before_native)!='inventory' or torneko_menu_kind(after_native)!='inventory':return None
        if any(not png or native.get('frame_sha256')!=hashlib.sha256(png).hexdigest() for native,png in ((before_native,before_png),(after_native,after_png))):return {'status':'frame_mismatch_unlinked'}
        old=self.cursor_index(before_native,before_png);new=self.cursor_index(after_native,after_png)
        if old is None or new is None:return {'status':'unlinked_selection','reason':'選択行を両画像で確定できない'}
        a=before_native['lines'][old];b=after_native['lines'][new]
        clean=lambda t:compact_text(t).lstrip('▶►▷>●→')
        box_a=self.line_box(a);box_b=self.line_box(b)
        if not box_a or not box_b or abs(box_a[1]-box_b[1])>max(2,(box_a[3]-box_a[1])*.4):return {'status':'selection_position_changed_unlinked'}
        if clean(a.get('text',''))!=clean(b.get('text','')):return {'status':'selection_changed_unlinked'}
        old_text={compact_text(l.get('text','')) for l in before_native.get('lines',[])}
        row_labels={clean(r['quote']) for r in self.rows}|{clean(r['display_name']) for r in self.rows}
        added=[l.get('text','')[:800] for l in after_native.get('lines',[]) if compact_text(l.get('text','')) not in old_text and clean(l.get('text','')) not in row_labels and len(compact_text(l.get('text','')))>=5 and compact_text(l.get('text','')) not in ('満腹度','詳細表示切替','整理') and not re.match(r'^(?:HP\s*\d|Lv\s*\d|\d)',l.get('text',''),re.I)]
        from .inventory_description import locate_description
        before_panel=locate_description(before_native,before_png,old,row_labels)
        after_panel=locate_description(after_native,after_png,new,row_labels)
        source_phase='after'
        if after_panel['status']=='located':
            changed_panel=before_panel['status']!='located' or before_panel['rectangles']!=after_panel['rectangles'] or before_panel['quotes']!=after_panel['quotes']
            added=after_panel['quotes'] if changed_panel else []
        elif before_panel['status']=='located' and after_panel['status']=='not_detected':added=before_panel['quotes'];source_phase='before'
        elif 'ambiguous' in (before_panel['status'],after_panel['status']):added=[]
        result={'status':'description_text_observed' if added else 'toggle_without_new_readable_text','item_name':clean(a.get('text','')),'selected_line_box':self.line_box(a),'quotes':added[:12],'before_frame':before_native.get('frame_sha256'),'after_frame':after_native.get('frame_sha256'),'step':row.get('step'),'source':'current_inventory_num7_toggle','effect_verified':False}
        result.update(description_layout_before=before_panel,description_layout_after=after_panel,
            description_source_phase=source_phase,description_source_frame=(before_native if source_phase=='before' else after_native).get('frame_sha256'))
        item_name=clean(a.get('text',''))
        alias=bool(re.search(r'未識別|未鑑定|仮名|^(?:赤|青|黄|緑|白|黒|紫|茶|桃|灰|銀|金|橙|水|ピンク|オレンジ)(?:い|色の|色い)?(?:草|巻物|杖|指輪)$',item_name))
        alias=alias or any(r.get('selected') and r.get('name_status')=='game_alias_unidentified' for r in self.rows)
        result['item_id']=next((r['id'] for r in self.rows if r.get('selected')),None)
        result.update(inventory_scope=self.scope,identity_scope='inventory_alias' if alias else 'profile_display_name',knowledge_kind='game_description_ocr_not_verified_effect')
        self.description_text.update(compact_text(t) for t in added)
        if len(self.description_text)>256:self.description_text=set(compact_text(t) for r in self.description_reads[-20:] for t in r.get('quotes',[]))
        self.description_reads=(self.description_reads+[result])[-40:]
        return result

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
        self.visual=VisualSense();self.inventory=Inventory(self.scope+'0_',self.confirm_key);self.floor=None;self.objects={};self.serial=0;self.tick=0;self.visited=set();self.previous_metrics={};self.latest={};self.pending_effects=[];self.delayed=[];self.result_seen=False;self.episode=0;self.last_messages=[];self.pending_equipment_check=None;self.equipment_comparisons=[]
        if self.db:
            self.db.executescript('''CREATE TABLE IF NOT EXISTS world_events(id INTEGER PRIMARY KEY,profile TEXT,session TEXT,created REAL,kind TEXT,data TEXT);
            CREATE INDEX IF NOT EXISTS world_events_profile ON world_events(profile,id);
            CREATE TABLE IF NOT EXISTS item_use_stats(profile TEXT,item_key TEXT,command TEXT,context TEXT,data TEXT,PRIMARY KEY(profile,item_key,command,context));''');self.db.commit()
            self.inventory.description_reads=[json.loads(r[0])['observation'] for r in reversed(self.db.execute("SELECT data FROM world_events WHERE profile=? AND kind='item_description_observation' ORDER BY id DESC LIMIT 40",(self.profile,)).fetchall())]
            self.equipment_comparisons=[json.loads(r[0])['observation'] for r in reversed(self.db.execute("SELECT data FROM world_events WHERE profile=? AND kind='equipment_comparison' ORDER BY id DESC LIMIT 24",(self.profile,)).fetchall())]
    def save(self,kind,data):
        if self.db:
            self.db.execute('INSERT INTO world_events(profile,session,created,kind,data) VALUES(?,?,?,?,?)',(self.profile,self.session,time.time(),kind,json.dumps({'episode':self.episode,'observation':data},ensure_ascii=False)));self.db.commit()
    def verify_equipment(self,current,frame,step=None):
        pending=self.pending_equipment_check
        if not pending:return None
        if not frame or not all(current.get(k,{}).get('values') is not None and not current[k].get('retained') for k in ('strength','sword','shield')):
            if self.tick-pending.get('started_tick',self.tick)<=12:return None
            result={**pending,'status':'abilities_unconfirmed','after_frame':frame,'reason':'装備後の能力値を観測予算内にそろえられないため、数値比較を確定せず再確認要求を解除'}
            self.equipment_comparisons=(self.equipment_comparisons+[result])[-24:];self.pending_equipment_check=None;self.state_snapshot.pending.discard('equipment_status_after_change')
            self.save('equipment_comparison',result);return result
        after={k:copy.deepcopy(current[k]['values']) for k in ('strength','sword','shield')}
        before={k:v['values'] for k,v in pending['before'].items() if v.get('values') is not None}
        result={**pending,'after':after,'metric_changes':changes(before,after),'after_frame':frame,'observed_step':step,'status':'observed_comparison','attribution':'temporal_association_not_proof_of_equipment_effect'}
        self.equipment_comparisons=(self.equipment_comparisons+[result])[-24:];self.pending_equipment_check=None;self.state_snapshot.pending.discard('equipment_status_after_change')
        self.save('equipment_comparison',result)
        return result

    def observe(self,png,native,state,neutral_png=None):
        from .interfaces import result_screen
        digest=hashlib.sha256(png).hexdigest()
        if native and native.get('frame_sha256') and native['frame_sha256']!=digest:native={'status':'frame_mismatch','lines':[]}
        self.tick+=1;state=state or {}
        m=state.get('minimap',{});floor=metrics(state).get('floor');vision=self.visual.read(png,neutral_png)
        if result_screen(native or {}):self.result_seen=True
        new_adventure=bool(self.result_seen and floor and state.get('eligible'))
        if new_adventure:
            self.last_known_metrics={};self.state_snapshot=type(self.state_snapshot)()
            self.pending_equipment_check=None
            self.episode+=1;self.inventory=Inventory(self.scope+str(self.episode)+'_',self.confirm_key);self.pending_effects=[];self.delayed=[];self.result_seen=False
        if new_adventure or (floor and self.floor and floor!=self.floor):
            self.objects={};self.visited=set();self.visual=VisualSense();self.floor_navigation.reset()
        if floor:self.floor=floor
        self.floor_navigation.observe(state)
        state['floor_exploration']=self.floor_navigation.context()
        player=m.get('player_cell');observed=m.get('status')=='observed';new=[];seen=set();current_objects=[]
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
        unresolved=frontier|self.floor_navigation.boundaries() if self.floor_navigation._valid(state) else set()
        targets={tuple(o['cell']) for o in current_objects if o.get('cell') and o['legend'] not in ('player',)}
        surveyed=known-unresolved-targets
        for key,value in state.get('metrics',{}).items():
            if key in ('strength','sword','shield','experience','max_floor','level','gold'):
                self.last_known_metrics[key]={'values':copy.deepcopy(value.get('values')),'frame_sha256':digest,'observed_at':time.time(),'status':'last_observed_not_current_confirmation'}
        if self.memory:self.memory.last_known_status=self.last_known_metrics
        self.verify_equipment(state.get('metrics',{}),digest,self.tick)
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
            'novelty':min(1.,len(new)/3+.25*(bool(player) and tuple(player) in frontier)),
            'exploration':{'known_cells':len(known),'visited_cells':len(self.visited),'frontier_cells':len(frontier),'frontier_candidates':[list(c) for c in sorted(frontier)[:40]],
                'surveyed_cell_count':len(surveyed),'surveyed_cells':[list(c) for c in sorted(surveyed)[:40]],'not_walls':True,'rule':'観測した床・接続と未確認の方向から調査対象を選ぶ。調査済みの方向は新しい根拠が得られるまで保留し、既知床は目的への経路に使う。'},
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
        description=self.inventory.read_description(row,old_ocr,new_ocr,before_png,after_png)
        if description:
            row['item_description_observation']=description;self.save('item_description_observation',description)
        effect=self.inventory.learn(row,before,after,old_ocr,new_ocr,natural,before_png)
        if effect:
            self.state_snapshot.request('item_use_or_equipment_effect',row.get('step'))
            if effect.get('command_quote','').lstrip('▶►▷>●→') in ('装備','外す','はずす'):
                self.state_snapshot.request('equipment_status_after_change',row.get('step'))
                self.pending_equipment_check={'item_id':effect['item_id'],'display_name':effect.get('display_name'),'command':effect['command_quote'],'origin_step':row.get('step'),'started_tick':self.tick,'before':{k:copy.deepcopy(v) for k,v in self.last_known_metrics.items() if k in ('strength','sword','shield')},'before_frame':(old_ocr or {}).get('frame_sha256')}
            self.save('item_effect',effect)
            self.record_item_value(effect)
        verification=self.verify_equipment(after.get('metrics',{}),(new_ocr or {}).get('frame_sha256'),row.get('step'))
        if verification:row['equipment_comparison']=verification
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
        self.save('turn',{'observation':self.latest,'result_metrics':metrics(after),'keys':row.get('observed_keys',[]),'effects':result,'route_recheck':copy.deepcopy(row.get('route_recheck_outcome')),
            'navigation_after':{k:copy.deepcopy(after['floor_exploration'].get(k)) for k in ('position','goal','exploration_task','frontier_cells','suppressed_edges','route_rechecks','frontier_rule')}})
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
        l=copy.deepcopy(self.latest)
        l.get('exploration',{}).pop('visited_cells',None)
        return {k:l[k] for k in ('metrics','metric_changes','visual','exploration','inventory','identity_rule') if k in l}|{
            'retained_state':self.state_snapshot.context(),
            'visual_battle_experience':self.battle_appearances.context(l.get('objects',[])),
            'floor_exploration':{k:v for k,v in self.floor_navigation.context().items() if k not in ('visited_cells','visited_count')},
            'last_known_metrics':copy.deepcopy(self.last_known_metrics),
            'objects':[{k:v for k,v in o.items() if k not in ('variants',)} for o in l.get('objects',[])[:20]],
            'equipment_review':{'observed_comparisons':copy.deepcopy(self.equipment_comparisons[-8:]),'verification_pending':copy.deepcopy(self.pending_equipment_check),'candidates':[{'item_id':r['id'],'display_name':r['display_name'],'equipped':r.get('equipped'),'description':r.get('known_description'),'identity_status':r.get('name_status'),'type_evidence':self.inventory.equipment_evidence(r),'may_consider_discard_after_comparison':True} for r in self.inventory.rows if self.inventory.equipment_evidence(r)],'last_observed_abilities':{k:v for k,v in self.last_known_metrics.items() if k in ('strength','sword','shield')},'rule':'候補は表示名・品に結び付いた説明文・実際の装備コマンドが根拠。正体と種類を区別し、不明名でも装備可能と観測できた品を比較する。所持だけで装備済みとせず、装備後は能力値を再取得して比較する。特殊効果と用途を含め有効な装備を選ぶ。比較して不要と判断した品は所持枠確保のため置く候補にできるが、目的品や有用な予備品を不用と仮定しない。'},'item_description_observations':copy.deepcopy(self.inventory.description_reads[-20:]),'item_effects':self.inventory.effects[-4:],'delayed_item_effects':self.delayed[-4:],'item_value_estimates':self.item_values()}
