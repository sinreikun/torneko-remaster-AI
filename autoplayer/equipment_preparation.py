"""Bounded equipment comparisons from current descriptions and observed ability."""
import copy,json,re,unicodedata
from .interfaces import compact_text

class EquipmentPreparation:
    def __init__(self):self.reset()
    def reset(self):self.active=None;self.attempted={};self.opportunities={};self.requested=set();self.request_token=None;self.scope=None
    @staticmethod
    def prohibited(current):
        return bool(re.search(r'(?:装備|盾|剣).{0,12}(?:変更しない|替えない|交換しない|固定|維持)',current.get('operator_guidance',{}).get('text','')))
    def review(self,actor,state,current,native):
        scope=[current.get('floor'),current['control_revision'],current['operator_guidance'].get('revision',0)]
        # A menu can temporarily lack a floor OCR; keep the known domain,
        # never treat unreadability as a new floor or a successful exchange.
        if not scope[0] and self.scope:scope[0]=self.scope[0]
        if self.scope!=scope:self.opportunities={};self.requested=set();self.active=None;self.scope=copy.deepcopy(scope)
        if self.prohibited(current):return
        retained=state.get('retained_state',{})
        rows=native.get('_current_inventory_rows') or (retained.get('last_observed_inventory') or {}).get('rows',[])
        audit=actor.inventory_audit.last or {}
        observed={**copy.deepcopy(audit.get('metrics',{})),**copy.deepcopy(retained.get('last_observed_metrics',{}))}
        observed.update({k:v for k,v in state.get('metrics',{}).items() if v.get('values') is not None and not v.get('retained')})
        all_rows=rows+[r for page in audit.get('pages',[]) for r in page if (r.get('id') not in {x.get('id') for x in rows} if r.get('id') else r not in rows)]
        for row in rows:
            option=self.option(row)
            if not option:continue
            slot,estimate,special=option;values=observed.get(slot,{}).get('values')
            identifier=row.get('id') or row['display_name']
            if row.get('equipped') or values and estimate<=values[0]:
                self.opportunities.pop(identifier,None)
                if self.active and self.active['target_row'].get('id')==row.get('id'):self.active=None
                continue
            if not values:continue
            equipped=[r for r in all_rows if r.get('equipped') and self.option(r) and self.option(r)[0]==slot]
            if values[0]>0 and (not equipped or special or any(self.option(r)[2] for r in equipped)):continue
            token=json.dumps([scope,identifier,slot,estimate,values[0]],ensure_ascii=False)
            if token in self.attempted:continue
            self.opportunities[identifier]={'target_row':copy.deepcopy(row),'slot':slot,'base':estimate,'before':values[0],'token':token,'scope':copy.deepcopy(scope),'status':'comparison_pending','individual_details_unconfirmed':bool(row.get('identification_required'))}
        while len(self.opportunities)>20:self.opportunities.pop(next(iter(self.opportunities)))
    def request(self,actor,base,state,current,native):
        self.review(actor,state,current,native)
        if current.get('interface') or not state.get('eligible') or self.prohibited(current):return None
        pending=[x for x in self.opportunities.values() if x['token'] not in self.attempted and x['token'] not in self.requested]
        if not pending:return None
        item=max(pending,key=lambda x:x['base']-x['before']);key=actor._key('menu')
        if not key:return None
        self.request_token=item['token']
        return actor._decision(base,[key],'equipment_upgrade_pending','より有効な装備の比較が未実行。探索へ戻っても候補を保持し、主メニューの能力と現在の品の行を再確認して交換を進める。')

    def choose(self,actor,base,native,state,current,kind,allow_start=False):
        a=self.active;step=current['step']
        scope=[current.get('floor') or (self.scope or [None])[0],current['control_revision'],current['operator_guidance'].get('revision',0)]
        if a and (step>a['until'] or scope!=a['scope'] or kind not in ('inventory','item_actions')):self.active=None;a=None
        if not a and allow_start and kind=='inventory' and not self.prohibited(current):
            self.review(actor,state,current,native)
            rows=native.get('_current_inventory_rows',[])
            candidates=[x for x in self.opportunities.values() if x['token'] not in self.attempted and any(r.get('id')==x['target_row'].get('id') for r in rows)]
            candidates=[x for x in candidates if actor._cursor_toward(native,x['target_row'],vertical=True) is not None]
            if candidates:
                item=max(candidates,key=lambda x:(x['base']-x['before'],x['base']))
                self.attempted[item['token']]=step
                while len(self.attempted)>64:self.attempted.pop(next(iter(self.attempted)))
                a={**copy.deepcopy(item),'until':step+16,'phase':'inventory','quote':compact_text(item['target_row']['quote']).lstrip('▶►▷>●→'),'pending':None}
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
        op=row.get('operation') or {}
        accepted=row.get('executed') and not row.get('dry_run') and row.get('turn_kind')!='aborted' and (row.get('input_receipt') or {}).get('os_submission')=='accepted' and row.get('observed_keys')==op.get('selected_keys')
        if op.get('selected_source')=='equipment_upgrade_pending' and accepted and self.request_token:
            self.requested.add(self.request_token)
        a=self.active
        if not a:return
        accepted=accepted and op.get('execution_stage')=='equipment_preparation'
        if a['pending'] in ('open','equip') and row.get('observed_keys')!=[confirm]:accepted=False
        if not accepted:
            self.attempted.pop(a['token'],None);self.active=None;return
        if a['pending']=='open':a['phase']='actions'
        elif a['pending']=='equip':
            identifier=a['target_row'].get('id') or a['target_row']['display_name']
            if identifier in self.opportunities:self.opportunities[identifier]['status']='sent_awaiting_status_verification'
            self.active=None
        a['pending']=None
