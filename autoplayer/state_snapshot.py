"""Overwrite observed state; retain missing fields as explicitly past observations."""
import copy,hashlib,json,re

class StateSnapshot:
    def __init__(self):
        self.metrics={};self.inventory=None;self.revision=0;self.pending=set();self.in_combat=False;self.inventory_token=None;self.last_events={};self.encounters=0
    def request(self,reason,event_id=None):
        if reason not in self.pending or event_id is not None and self.last_events.get(reason)!=event_id:
            self.pending.add(reason);self.revision+=1;self.last_events[reason]=event_id
    def observe(self,metrics,inventory,tick,enemies=None):
        for k,v in metrics.items():
            if v.get('values') is not None and not v.get('retained'):
                old=self.metrics.get(k,{}).get('values')
                if old is not None and old!=v['values'] and k in ('level','strength','sword','shield'):self.request('ability_changed',json.dumps([k,v['values']]))
                self.metrics[k]={**copy.deepcopy(v),'last_observed_tick':tick,'status':'last_observed'}
        if all(metrics.get(k,{}).get('values') is not None and not metrics[k].get('retained') for k in ('strength','sword','shield')):self.pending.discard('equipment_status_after_change')
        if enemies and not self.in_combat:
            self.encounters+=1
            if any(k not in self.metrics for k in ('level','strength','sword','shield')):self.request('before_combat_missing_abilities',self.encounters)
        if enemies is not None:self.in_combat=enemies
        if inventory.get('status')=='observed':
            self.inventory=copy.deepcopy(inventory);self.inventory['last_observed_tick']=tick
            signature=[(r.get('display_name'),r.get('quantity'),r.get('equipped')) for r in inventory.get('rows',[])]
            self.inventory_token=hashlib.sha256(json.dumps(signature,ensure_ascii=False).encode()).hexdigest()
            self.pending.intersection_update({'equipment_status_after_change'})
    def feedback(self,before,after,old_native,new_native,event_id=None):
        m=before.get('minimap',{});a=m.get('player_cell');b=after.get('minimap',{}).get('player_cell')
        if a and b and a!=b and before.get('metrics',{}).get('floor',{}).get('values')==after.get('metrics',{}).get('floor',{}).get('values'):
            if [b[0]-a[0],b[1]-a[1]] in [list(p) for p in m.get('items',[])]:self.request('possible_item_pickup_or_full_inventory',event_id)
        old={l.get('text','') for l in (old_native or {}).get('lines',[])}
        new=[l.get('text','') for l in (new_native or {}).get('lines',[]) if l.get('text','') not in old]
        if any(re.search(r'拾った|ひろった|持ち物.{0,8}いっぱい|装備した|装備を外|使い切|なくなった',t) for t in new):self.request('inventory_or_equipment_message',event_id)
    def context(self):
        return {'revision':self.revision,'refresh_reasons':sorted(self.pending),
            'last_observed_metrics':copy.deepcopy(self.metrics),'last_observed_inventory':copy.deepcopy(self.inventory),
            'inventory_token':self.inventory_token,'before_combat_check_needed':'before_combat_missing_abilities' in self.pending,
            'rule':'現在画面で読めた値を優先して上書き。非表示の保持値は最終観測であり現在のHP/所持の証明ではない。確認は必要な変化時だけ、同じ理由で連打しない。'}
