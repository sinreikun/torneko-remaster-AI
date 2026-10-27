"""One bounded equipment trial from observed zero ability and an item description."""
import copy,json,re,unicodedata
from .interfaces import compact_text

class EquipmentPreparation:
    def __init__(self):self.active=None;self.attempted={}
    def choose(self,actor,base,native,state,current,kind,allow_start=False):
        a=self.active;step=current['step']
        scope=[current.get('floor'),current['control_revision'],current['operator_guidance'].get('revision',0)]
        if a and (step>a['until'] or scope!=a['scope'] or kind not in ('inventory','item_actions')):self.active=None;a=None
        if not a and allow_start and kind=='inventory' and not current['operator_guidance'].get('text'):
            audit=actor.inventory_audit.last or {};observed=audit.get('metrics',{})
            if audit.get('status')!='analysed' or not 0<=step-audit.get('finished_step',-100)<=12 or audit.get('control_revision')!=current['control_revision'] or audit.get('guidance_revision')!=current['operator_guidance'].get('revision',0) or observed.get('floor',{}).get('values')!=current.get('floor'):return None
            for row in native.get('_current_inventory_rows',[]):
                if row.get('equipped') is True:continue
                desc=row.get('known_description') or {}
                if desc.get('status')!='description_text_observed':continue
                text=unicodedata.normalize('NFKC',' '.join(desc.get('quotes',[])))
                if re.search(r'呪|のろ',row['display_name']+text):continue
                for slot,label,pattern in [('shield','盾',r'防御力(?:は|が)?基本\s*(\d+)'),('sword','剣',r'攻撃力(?:は|が)?基本\s*(\d+)')]:
                    if observed.get(slot,{}).get('values')!=[0] or label not in row['display_name']+text:continue
                    match=re.search(pattern,text)
                    if not match or not 0<int(match[1])<100:continue
                    token=json.dumps([scope,row['display_name'],slot,match[1]],ensure_ascii=False)
                    if token in self.attempted:continue
                    quote=compact_text(row['quote']).lstrip('▶►▷>●→')
                    if actor._cursor_toward(native,quote,vertical=True) is None:continue
                    self.attempted[token]=step
                    while len(self.attempted)>64:self.attempted.pop(next(iter(self.attempted)))
                    a={'scope':copy.deepcopy(scope),'until':step+10,'phase':'inventory','quote':quote,'slot':slot,'base':int(match[1]),'pending':None}
                    self.active=a;break
                if a:break
        if not a:return None
        expected='inventory' if a['phase']=='inventory' else 'item_actions'
        if kind!=expected:self.active=None;return None
        target=actor._cursor_toward(native,a['quote'] if kind=='inventory' else '装備',vertical=kind=='inventory')
        if not target:self.active=None;return None
        key=actor._key('confirm') if target=='selected' else target
        a['pending']='open' if kind=='inventory' and target=='selected' else 'equip' if target=='selected' else 'navigate'
        return actor._decision(base,[key],'equipment_preparation',f"棚卸しで{a['slot']}は0、品の説明に基本値{a['base']}を観測。現在の品と装備コマンドを照合して1段階進み、能力変化を別途確認する。所持だけで装備済みとはしない。")
    def feedback(self,row,confirm):
        a=self.active
        if not a:return
        op=row.get('operation',{})
        accepted=row.get('executed') and row.get('turn_kind')!='aborted' and row.get('input_receipt',{}).get('os_submission')=='accepted' and op.get('execution_stage')=='equipment_preparation' and row.get('observed_keys')==op.get('selected_keys')
        if a['pending'] in ('open','equip') and row.get('observed_keys')!=[confirm]:accepted=False
        if not accepted:self.active=None;return
        if a['pending']=='open':a['phase']='actions'
        elif a['pending']=='equip':self.active=None
        a['pending']=None
