"""Bounded event evidence independent of the ordinary frame storage cap."""
import hashlib
import io
import json
from pathlib import Path
from collections import deque
from .interfaces import compact_text


def brief(row):
    value={k:row.get(k) for k in ('step','scene','observed_keys','hold_ms','reason','input_receipt','turn_kind','loop_guard','battle_observations','current_evidence')}
    native=row.get('ocr') or {}
    value['ocr']={k:native.get(k) for k in ('frame_sha256','captured_at','engine','status')}
    value['ocr']['lines']=[{'text':line.get('text','')[:200]} for line in native.get('lines',[])[:40]]
    return value

class ImportantEvents:
    def __init__(self,folder,slots=16,limit=8*1024*1024):
        self.folder=Path(folder);self.slots=slots;self.limit=limit;self.sequence=0
        self.previous=deque(maxlen=2);self.last_floor=None;self.pending=None;self.kind_counts={}
    def observe(self,row,before,after):
        b=(row.get('policy_state')or{}).get('metrics',{});a=(row.get('policy_after')or{}).get('metrics',{})
        floor=a.get('floor',{}).get('values') or b.get('floor',{}).get('values')
        food0=b.get('fullness',{}).get('values');food1=a.get('fullness',{}).get('values')
        reasons=[]
        if row.get('loop_guard',{}).get('event') in ('loop_detected','loop_escape'):reasons.append('control_loop')
        if row.get('item_description_observation'):reasons.append('item_description_toggle')
        if floor and self.last_floor and floor!=self.last_floor:reasons.append('floor_changed')
        if food0 and food1 and food1[0]>food0[0]:reasons.append('fullness_increased')
        if row.get('combat_evidence_frame_role'):reasons.append('battle_text_recovered')
        if (row.get('combat_message_ocr') or {}).get('text_status')=='required_text_unconfirmed':reasons.append('battle_text_unconfirmed')
        if (row.get('floor_exploration') or {}).get('cycle_status',{}).get('detected'):reasons.append('navigation_cycle')
        if any(e.get('new_message') and e['kind']=='defeated_enemy' for e in row.get('battle_observations',[])):reasons.append('defeat_message')
        labels={compact_text(l.get('text','')).lstrip('▶►▷>→') for l in (row.get('ocr') or {}).get('lines',[])}
        if row.get('scene')=='dialog' or {'はい','いいえ'}<=labels:reasons.append('dialogue_or_choice')
        if self.pending:
            path=self.pending/'event.json'
            data=json.loads(path.read_text(encoding='utf-8'));data['following_action']=brief(row);path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
            self.pending=None
        if reasons:
            self.sequence+=1;kind=reasons[0];self.kind_counts[kind]=self.kind_counts.get(kind,0)+1
            buckets={'floor_changed':(0,4),'fullness_increased':(4,3),'defeat_message':(7,3),'dialogue_or_choice':(10,2),'item_description_toggle':(10,2),'battle_text_unconfirmed':(12,2),'battle_text_recovered':(12,2),'navigation_cycle':(14,2),'control_loop':(14,2)}
            start,count=buckets[kind] if self.slots>=16 or self.slots==12 and kind not in ('battle_text_unconfirmed','battle_text_recovered','navigation_cycle') else (0,self.slots)
            slot=self.folder/('event-%02d'%(start+(self.kind_counts[kind]-1)%count));slot.mkdir(parents=True,exist_ok=True)
            for f in slot.iterdir():f.unlink()
            data={'sequence':self.sequence,'reasons':reasons,'previous_actions':[item[0] for item in self.previous],
                'action':brief(row),'floor_before':self.last_floor or b.get('floor',{}).get('values'),'floor_after':floor,
                'food_before':food0,'food_after':food1,'capacity_events':self.slots,'limit_bytes':self.limit,
                'images_are_resized_target_captures':True,'after_evidence_role':row.get('combat_evidence_frame_role','current_result'),'current_result_frame_sha256':row.get('current_result_frame_sha256'),'combat_text_status':(row.get('combat_message_ocr') or {}).get('text_status'),'cycle_status':(row.get('floor_exploration') or {}).get('cycle_status'),'frames':[]}
            pairs=([('preceding',self.previous[-1][1])] if self.previous else [])+[('before',before),('after',after)]
            from PIL import Image
            for phase,png in pairs:
                if not png or not png.startswith(b'\x89PNG'):continue
                with Image.open(io.BytesIO(png)) as image:
                    image.thumbnail((640,360));output=io.BytesIO();image.save(output,format='PNG');encoded=output.getvalue()
                used=sum(f.stat().st_size for f in self.folder.rglob('*') if f.is_file())
                if used+len(encoded)>self.limit-65536:data.setdefault('omitted_images',[]).append(phase);continue
                name=phase+'.png';(slot/name).write_bytes(encoded)
                data['frames'].append({'phase':phase,'file':name,'source_sha256':hashlib.sha256(png).hexdigest(),'stored_sha256':hashlib.sha256(encoded).hexdigest()})
            (slot/'event.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');self.pending=slot
        if floor:self.last_floor=floor
        self.previous.append((brief(row),after))
