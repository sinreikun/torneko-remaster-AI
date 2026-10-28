"""Bounded Inpas use: item, read command, explicit target prompt, result."""
import copy,json,re

class Identification:
    def __init__(self):self.active=None;self.attempted=set();self.last=None
    def choose(self,actor,base,native,state,current,blocked=()):
        kind=current.get('interface');step=current['step'];retained=state.get('retained_state',{})
        scope=[current.get('floor'),current.get('control_revision'),current.get('operator_guidance',{}).get('revision',0)]
        a=self.active
        if a and not scope[0]:scope[0]=a['scope'][0]
        if a and (scope!=a['scope'] or step>a['until']):self.active=None;a=None
        text='\n'.join(x.get('text','') for x in native.get('lines',[]))
        # A target chooser is not the normal inventory. Never use an arbitrary
        # item merely because it occupies the cursor after reading a scroll.
        target_prompt=bool(re.search(r'(?:どれ|どの道具|どのアイテム|何を).{0,16}(?:識別|鑑定)|(?:識別|鑑定)(?:する|したい|対象の).{0,12}(?:選|道具|アイテム|対象)',text))
        hp=state.get('metrics',{}).get('hp',{}).get('values');food=state.get('metrics',{}).get('fullness',{}).get('values')
        unsafe=bool(any(max(abs(x),abs(y))<=1 for x,y in current.get('contacts',[])) or hp and hp[0]/max(1,hp[1])<.3 or food and food[0]<=15)
        prohibited=bool(re.search(r'インパス.{0,12}(?:使わない|使用しない|温存)|(?:識別|鑑定).{0,8}しない',current.get('operator_guidance',{}).get('text','')))
        if unsafe or prohibited:self.active=None;return None
        rows=native.get('_current_inventory_rows',[])
        if not a:
            if kind!='inventory' or target_prompt:return None
            audit=actor.inventory_audit.context()
            pages=audit.get('pages',[])
            if not audit.get('active'):
                previous=actor.inventory_audit.last or {}
                if previous.get('control_revision')==scope[1] and previous.get('guidance_revision')==scope[2]:pages=previous.get('pages',[])
            all_rows=rows+[r for page in pages for r in page]+(retained.get('last_observed_inventory') or {}).get('rows',[])
            scroll=next((r for r in rows if r.get('display_name')=='インパスの巻物' and not r.get('identification_required')),None)
            targets=[r for r in all_rows if r.get('identification_required') and r.get('id') and r.get('display_name')!='インパスの巻物']
            if not scroll or not targets:return None
            targets.sort(key=lambda r:(not ('指輪' in r.get('display_name','')),not r.get('equipped'),r['id']))
            target=targets[0];token=json.dumps([scope,scroll.get('id'),scroll.get('quantity'),target['id']],ensure_ascii=False)
            if token in self.attempted:return None
            self.attempted.add(token)
            if len(self.attempted)>128:self.attempted={token}
            a={'scope':copy.deepcopy(scope),'until':step+32,'phase':'scroll','scroll':copy.deepcopy(scroll),'target':copy.deepcopy(target),'pages':set(),'pending':None}
            self.active=a;self.last={'status':'in_progress','target_id':target['id'],'alias':target['display_name']}
        def action(direction,pending,reason):
            key=actor._key('confirm') if direction=='selected' else direction
            if not key or [key] in blocked:return None
            a['pending']=pending
            return actor._decision(base,[key],'identification',reason)
        if a['phase']=='result':
            self.last.update(status='awaiting_identification_evidence',result_quotes=[x.get('text','') for x in native.get('lines',[]) if re.search(r'識別|鑑定|だった|であった|呪',x.get('text',''))])
            self.active=None;return None
        if a['phase']=='scroll' and kind=='inventory':
            direction=actor._cursor_toward(native,a['scroll'],vertical=True)
            if direction:return action(direction,'open' if direction=='selected' else 'move','未識別品があるため、現在行で確認したインパスの巻物を選択する。Num7は説明切替であり使用ではない。')
        elif a['phase']=='read' and kind=='item_actions':
            direction=actor._cursor_toward(native,'読む')
            if direction:return action(direction,'read' if direction=='selected' else 'move','インパスの巻物の使用方法「読む」を選ぶ。次の対象選択画面を確認してから識別する品を決定する。')
        elif a['phase']=='target' and kind=='inventory' and target_prompt:
            target=next((r for r in rows if r.get('id')==a['target']['id']),None)
            if target:
                direction=actor._cursor_toward(native,target,vertical=True)
                if direction:return action(direction,'identify' if direction=='selected' else 'move','識別対象を求める現在の画面で、未識別品のID・行・カーソルを照合して決定する。同名の別個体を選ばない。')
            signature=tuple((r.get('id'),r.get('display_name')) for r in rows)
            if rows and signature not in a['pages'] and len(a['pages'])<20:
                a['pages'].add(signature)
                key=next((k for k,v in actor.effects.bindings.items() if k in actor.cfg.allowed_keys and v.startswith('右')),None)
                return action(key,'move','識別対象の現在行が別ページにあるため、右で次ページを1回確認する。')
        self.last.update(status='unconfirmed_interface',phase=a['phase']);self.active=None
        return None
    def feedback(self,row):
        a=self.active;op=row.get('operation') or {}
        if not a or op.get('selected_source')!='identification':return
        accepted=row.get('executed') and not row.get('dry_run') and row.get('turn_kind')!='aborted' and (row.get('input_receipt') or {}).get('os_submission')=='accepted' and row.get('observed_keys')==op.get('selected_keys')
        if not accepted:self.active=None;return
        phase={'open':'read','read':'target','identify':'result'}.get(a['pending'])
        if phase:a['phase']=phase
        a['pending']=None
