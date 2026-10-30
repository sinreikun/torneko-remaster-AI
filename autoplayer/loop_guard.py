"""Detect repeated measured states, preserve evidence and force bounded replanning."""
from collections import deque
import copy,hashlib,json,re

class LoopGuard:
    def __init__(self):self.history=deque(maxlen=36);self.scope=None;self.pending=None;self.serial=0;self.cooldown=0
    @staticmethod
    def signature(state,native):
        m=state.get('minimap',{});interface=state.get('interface_kind') or state.get('_operational_interface')
        if not interface and state.get('menu_evidence'):interface='menu_unclassified'
        selection=next(((i,r.get('display_name')) for i,r in enumerate( (state.get('retained_state',{}).get('last_observed_inventory') or {}).get('rows',[])) if r.get('selected')),None)
        body=[line.get('text','') for line in (native or {}).get('lines',[]) if not line.get('words') or any(w.get('box',[0,100])[1]>100 for w in line.get('words',[]))]
        text=hashlib.sha256('\n'.join(body).encode()).hexdigest()[:12] if interface or not m.get('player_cell') else None
        return (tuple(m.get('player_cell') or ()),interface,selection,text)
    @staticmethod
    def changed_value(before,after):
        for key in ('hp','fullness','level','strength','sword','shield','experience','gold','floor'):
            a=before.get('metrics',{}).get(key,{});b=after.get('metrics',{}).get(key,{})
            if a.get('values') is not None and b.get('values') is not None and not a.get('retained') and not b.get('retained') and a['values']!=b['values']:
                if key!='fullness' or b['values'][0]>a['values'][0]:return True
        return False
    def record(self,row,before,after,native):
        if not row.get('executed') or row.get('dry_run') or (row.get('input_receipt') or {}).get('os_submission')!='accepted':return None
        floor=after.get('metrics',{}).get('floor',{}).get('values') or before.get('metrics',{}).get('floor',{}).get('values') or (self.scope or [None])[0]
        op=row.get('operation') or {}
        try:control=json.loads(op.get('scope','null'))[-2:]
        except (ValueError,TypeError,IndexError):control=[row.get('operator_guidance_revision',0)]
        atlas=(row.get('mapless_navigation') or {}).get('atlas') or {}
        scope=[floor,control,atlas.get('map_id')]
        # Plan IDs, captions and transient OCR failures must not reset detection.
        if self.scope!=scope:self.history.clear();self.pending=None;self.scope=copy.deepcopy(scope)
        meaningful=self.changed_value(before,after) or any(x.get('new_message') and x.get('kind') in ('damage_dealt','damage_received','defeated_enemy') for x in row.get('battle_observations',[]))
        meaningful=meaningful or (row.get('item_description_observation') or {}).get('status')=='description_text_observed'
        old={x.get('text','') for x in (row.get('ocr') or {}).get('lines',[])}
        meaningful=meaningful or any(x.get('text','') not in old and re.search(r'拾った|手に入|装備した|食べた|飲んだ|読んだ',x.get('text','')) for x in (native or {}).get('lines',[]))
        a=before.get('minimap',{});b=after.get('minimap',{})
        if a.get('status')==b.get('status')=='observed' and a.get('player_cell') and b.get('player_cell'):
            def absolute(m):return {tuple(p) for p in m.get('accumulated_cells',[])}
            meaningful=meaningful or bool(absolute(b)-absolute(a))
        if meaningful:
            self.history.clear();self.cooldown=max(0,self.cooldown-1)
            return self.consume('new_observed_effect_during_escape; re-evaluate') if self.pending else None
        state=self.signature(after,native)
        entry={'step':row.get('step'),'keys':list(row.get('observed_keys',[])),'state':state,'before_frame':(row.get('ocr') or {}).get('frame_sha256'),'after_frame':after.get('frame_sha256')}
        self.history.append(entry)
        if self.pending:
            self.pending['attempts']+=1
            p=state[0]
            if self.pending['kind']=='menu' and not state[1] and after.get('eligible'):return self.consume('menu_closed; original_global_goal_preserved')
            if self.pending['kind']=='spatial' and p and list(p) not in self.pending['positions']:return self.consume('left_repeating_cells; escape_observed')
            if self.pending['attempts']>=8:return self.consume('bounded_escape_attempts_exhausted; request_new_supervision')
            return None
        if self.cooldown:self.cooldown-=1;return None
        values=[x['state'] for x in self.history]
        for period in (1,2,3,4,6):
            repeats=6 if period==1 else 3
            n=period*repeats
            if len(values)<n or any(values[-n+i]!=values[-period+i%period] for i in range(n)):continue
            entries=list(self.history)[-n:];self.serial+=1
            positions=sorted({tuple(x['state'][0]) for x in entries if len(x['state'][0])==2})
            event={'event':'loop_detected','id':self.serial,'period':period,'steps':[x['step'] for x in entries],
              'positions':[list(p) for p in positions],'sequence':copy.deepcopy(entries),'kind':'menu' if any(x['state'][1] for x in entries) else 'spatial' if positions else 'unclassified',
              'rule':'位置変化だけを進展扱いしない。床・所持品・能力は消去しない。','learning':False}
            self.pending={**copy.deepcopy(event),'attempts':0};self.history.clear()
            return event
        return None
    def consume(self,reason):
        event={'event':'loop_escape','id':self.pending['id'] if self.pending else None,'reason':reason,'learning':False}
        self.pending=None;self.cooldown=3;return event
    def context(self):return copy.deepcopy(self.pending)
