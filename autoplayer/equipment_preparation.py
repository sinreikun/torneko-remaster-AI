"""Bounded equipment comparisons from current descriptions and observed ability."""
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
            observed=copy.deepcopy(observed)
            observed.update(state.get('retained_state',{}).get('last_observed_metrics',{}))
            observed.update({k:v for k,v in state.get('metrics',{}).items() if v.get('values') is not None and not v.get('retained')})
            rows=native.get('_current_inventory_rows',[]);all_rows=[r for page in audit.get('pages',[]) for r in page];candidates=[]
            for row in rows:
                if row.get('equipped') is True:continue
                option=self.option(row)
                if not option:continue
                slot,estimate,special=option
                values=observed.get(slot,{}).get('values')
                if not values or observed.get(slot,{}).get('retained') or estimate<=values[0]:continue
                # A higher number does not automatically outweigh a special
                # effect. Let the supervisor compare those contextual choices.
                equipped=[r for r in (all_rows or rows) if r.get('equipped') is True and self.option(r) and self.option(r)[0]==slot]
                if values[0]>0 and (not equipped or special or any(self.option(r)[2] for r in equipped)):continue
                if any(re.search(r'呪|のろ',r.get('display_name','')+' '.join(r.get('known_description',{}).get('quotes',[]))) for r in equipped):continue
                token=json.dumps([scope,row['display_name'],slot,estimate,values[0]],ensure_ascii=False)
                if token in self.attempted:continue
                quote=compact_text(row['quote']).lstrip('▶►▷>●→')
                if actor._cursor_toward(native,row,vertical=True) is None:continue
                candidates.append((estimate-values[0],estimate,quote,slot,values[0],token,copy.deepcopy(row)))
            if candidates:
                _,estimate,quote,slot,baseline,token,target_row=max(candidates,key=lambda x:x[:6])
                self.attempted[token]=step
                while len(self.attempted)>64:self.attempted.pop(next(iter(self.attempted)))
                a={'scope':copy.deepcopy(scope),'until':step+10,'phase':'inventory','quote':quote,'target_row':target_row,'slot':slot,'base':estimate,'before':baseline,'pending':None}
                self.active=a
        if not a:return None
        expected='inventory' if a['phase']=='inventory' else 'item_actions'
        if kind!=expected:self.active=None;return None
        target=actor._cursor_toward(native,a.get('target_row',a['quote']) if kind=='inventory' else '装備',vertical=kind=='inventory')
        if not target:self.active=None;return None
        key=actor._key('confirm') if target=='selected' else target
        a['pending']='open' if kind=='inventory' and target=='selected' else 'equip' if target=='selected' else 'navigate'
        return actor._decision(base,[key],'equipment_preparation',f"棚卸しで{a['slot']}は{a['before']}、品の説明と表示補正から比較候補値{a['base']}。現在の品と装備コマンドを照合して1段階進み、能力変化を別途確認する。所持だけで装備済みとはしない。")
    @staticmethod
    def option(row):
        desc=row.get('known_description') or {}
        if desc.get('status')!='description_text_observed':return None
        name=unicodedata.normalize('NFKC',row.get('display_name',''))
        text=unicodedata.normalize('NFKC',' '.join(desc.get('quotes',[])))
        if re.search(r'呪|のろ',name+text):return None
        special=bool(re.search(r'満腹|空腹|半分|特殊|錆|さび|毒|炎|ドラゴン|会心|必ず|2回|二回',text))
        modifiers=re.findall(r'([+-]\s*\d+)',name)
        if re.search(r'[+-]\s*[?？]',name) or len(modifiers)>1:return None
        adjustment=int(modifiers[0].replace(' ','')) if modifiers else 0
        for slot,label,pattern in [('shield','盾',r'防御力(?:は|が)?(?:の)?基本(?:値)?\s*(\d+)'),('sword','剣',r'攻撃力(?:は|が)?(?:の)?基本(?:値)?\s*(\d+)')]:
            match=re.search(pattern,text)
            if match and (label in name+text or row.get('equipment_affordance')) and 0<int(match[1])<100:
                return slot,int(match[1])+adjustment,special
        return None

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
