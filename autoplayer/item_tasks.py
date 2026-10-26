"""Floor-scoped marker and pickup tasks; arrival is not proof of acquisition."""
import copy,hashlib,json,re,uuid
from collections import Counter

LABELS={'classification_unresolved':'到達・マーク未分類','awaiting_evidence':'所持品確認後も取得未確定','observed':'認識済み・未回収','planned':'目的地へ移動中','marker_unclassified':'マーク未分類・確認待ち',
 'arrival_unverified':'到達・取得未確認','awaiting_inventory_check':'所持品の再確認待ち','capacity_blocked':'満杯の根拠あり・交換検討',
 'deferred':'理由を記録して保留','route_unavailable':'現在の経路では到達不能','not_visible_unresolved':'現在非表示・未解決',
 'pickup_confirmed':'取得メッセージ確認','marker_resolved':'現在は別のマップ記号'}
class ItemTasks:
    def __init__(self):
        self.scope=uuid.uuid4().hex[:12];self.tasks={};self.revision=0;self.inventory_token=None;self.capacity=None;self.last_arrival=None;self.enemy_seen=False;self.unlocated_evidence=[];self.checked={}
    def change(self,t,status,reason,evidence=None):
        if t['status']!=status or t.get('reason')!=reason:
            self.revision+=1
            if evidence is None and t.get('evidence'):t['previous_evidence']=t.pop('evidence')
        t.update(status=status,reason=reason,label=LABELS[status])
        if evidence:t['evidence']=copy.deepcopy(evidence)
    def observe(self,items,unknown,stairs,position,frame,enemies=False):
        for cell,kind in [(c,'item_indicator') for c in items]+[(c,'unclassified_marker') for c in unknown if c not in items and c!=position]:
            t=self.tasks.get(cell)
            if t is None:
                t={'cell':list(cell),'kind':kind,'status':'observed' if kind=='item_indicator' else 'marker_unclassified','attempts':0,'visible':True,'generation':1}
                self.tasks[cell]=t;self.revision+=1
            if t['status']=='pickup_confirmed' and cell!=position:
                t['generation']+=1;t['attempts']=0;self.change(t,'observed','取得後に新しいマークを再観測')
            if kind=='item_indicator':
                t['kind']='item_indicator'
                if t['status']=='classification_unresolved':t['attempts']=0;self.change(t,'observed','現在の表示で品マークを確認')
            t.update(visible=True,current_classification=kind,last_seen_frame=frame)
            if cell==position and t['kind']=='item_indicator' and t['status'] in ('observed','planned'):self.change(t,'arrival_unverified','現在位置の品表示を確認。取得結果は未確認')
            if t['status']=='not_visible_unresolved':self.change(t,'observed' if t['kind']=='item_indicator' else 'marker_unclassified','マークを再観測')
            if t['status'] in ('arrival_unverified','awaiting_evidence') and cell!=position and t['attempts']<2:
                self.change(t,'observed' if t['attempts']<2 else 'awaiting_inventory_check','到達後も表示が残る。未取得の理由を確認')
            if self.capacity and t['kind']=='item_indicator' and t['status']!='pickup_confirmed':self.change(t,'capacity_blocked','所持上限の根拠があり回収/交換を再検討',self.capacity)
        seen=items|unknown
        for cell,t in self.tasks.items():
            if self.capacity and t['kind']=='item_indicator' and t['status'] not in ('pickup_confirmed','marker_resolved'):self.change(t,'capacity_blocked','所持上限の根拠があり回収/交換を再検討',self.capacity)
            if cell not in seen:
                t['visible']=False
                if t['kind']=='unclassified_marker' and cell in stairs:self.change(t,'marker_resolved','現在のマップ凡例では階段表示')
                elif t['status'] in ('observed','planned','marker_unclassified','route_unavailable') and cell!=position:self.change(t,'not_visible_unresolved','表示が消えただけでは取得と確定しない')
            if t['status']=='deferred' and t.get('revisit')=='after_combat' and self.enemy_seen and not enemies:
                self.change(t,'observed' if t['kind']=='item_indicator' else 'marker_unclassified','敵表示がなくなったため保留を再検討')
        self.enemy_seen=enemies
    def arrive(self,cell,step):
        t=self.tasks.get(cell)
        if not t or t['status'] in ('pickup_confirmed','marker_resolved'):return
        if step is not None and self.last_arrival and self.last_arrival==[list(cell),step]:return
        self.last_arrival=[list(cell),step];t['attempts']+=1
        self.change(t,'classification_unresolved' if t['kind']=='unclassified_marker' else 'capacity_blocked' if self.capacity else 'arrival_unverified','マスへの到達のみ確認。取得/分類は未確認')
    def inventory(self,token,occupancy=None,fresh=True):
        if not fresh:return
        was_full=bool(self.capacity)
        changed=bool(token and self.inventory_token and token!=self.inventory_token)
        self.inventory_token=token or self.inventory_token
        for cell,t in self.tasks.items():
            if t['status'] in ('arrival_unverified','awaiting_inventory_check','capacity_blocked'):
                self.checked[cell]=[t['attempts'],t['generation'],self.inventory_token]
                if t['status']!='capacity_blocked':self.change(t,'awaiting_evidence','所持品を確認したが、この品の取得結果は未確定')
        if occupancy and occupancy.get('count')==20:
            self.capacity={'source':'inventory_occupancy','occupancy':copy.deepcopy(occupancy)}
        elif occupancy and occupancy.get('count',20)<20 or changed and self.capacity and occupancy is None:
            self.capacity=None
        if changed or was_full and not self.capacity:
            for t in self.tasks.values():
                if t['status'] in ('capacity_blocked','arrival_unverified','awaiting_inventory_check','awaiting_evidence','deferred') and not self.capacity and (t['status']!='deferred' or t.get('revisit')=='inventory_changed'):
                    t['attempts']=0;self.change(t,'observed' if t['kind']=='item_indicator' else 'marker_unclassified','所持内容/空きの変化で回収を再検討')
    def decide(self,decisions):
        for d in decisions or []:
            t=self.tasks.get(tuple(d.get('cell',[])))
            if not t or t['status'] in ('pickup_confirmed','marker_resolved'):continue
            t['decision']=d['decision'];t['decision_reason']=d['reason']
            if d['decision']=='defer':
                t['revisit']=d['revisit'];self.change(t,'deferred',d['reason'],{'source':'llm_plan_unverified_decision'})
            else:
                if self.capacity:continue
                retry=[self.inventory_token,t['generation']]
                if t['attempts']>=2:
                    if t.get('manual_retry_token')==retry:continue
                    t['manual_retry_token']=retry
                t['attempts']=0;self.change(t,'observed' if t['kind']=='item_indicator' else 'marker_unclassified','監督が回収/再確認を選択')
    def goal_changed(self):
        for t in self.tasks.values():
            if t['status']=='deferred' and t.get('revisit')=='goal_changed':self.change(t,'observed' if t['kind']=='item_indicator' else 'marker_unclassified','目的が変化したため保留を再検討')
    def goals(self,position,kind='item_indicator'):
        allowed=('observed','planned','route_unavailable','not_visible_unresolved','marker_unclassified')
        return {c for c,t in self.tasks.items() if c!=position and t['kind']==kind and t['status'] in allowed and t['attempts']<(2 if kind=='item_indicator' else 1)}
    def enemy_priority(self):
        for t in self.tasks.values():
            if t['status'] in ('observed','planned','marker_unclassified'):
                self.change(t,'observed' if t['kind']=='item_indicator' else 'marker_unclassified','敵への対処を優先。回収/確認は未完了')
    def planned(self,cell):
        for c,t in self.tasks.items():
            if c==cell:self.change(t,'planned','現在の目的地として選択')
            elif t['status']=='planned':self.change(t,'observed' if t['kind']=='item_indicator' else 'marker_unclassified','別の対象を先に確認')
    def feedback(self,row,before,after,old_native,new_native):
        old={l.get('text','') for l in (old_native or {}).get('lines',[])}
        quotes=[l.get('text','') for l in (new_native or {}).get('lines',[]) if l.get('text','') not in old]
        if not row.get('executed') or row.get('dry_run') or row.get('turn_kind')=='aborted' or (row.get('input_receipt') or {}).get('os_submission')!='accepted':return
        if not before.get('eligible') and not (row.get('step') is not None and self.last_arrival and self.last_arrival[1]==row['step']):return
        if str(row.get('event','')).startswith('diagnostic') or row.get('kind') in ('compatibility','separation'):return
        a=before.get('metrics',{}).get('floor',{}).get('values');b=after.get('metrics',{}).get('floor',{}).get('values')
        if a and b and a!=b:return
        m=after.get('minimap',{})
        pos=m.get('player_cell') if m.get('status')=='observed' and not m.get('retained') and after.get('floor_confirmation',{}).get('status') not in ('pending','unreadable') else None
        if not pos and row.get('step') is not None and self.last_arrival and self.last_arrival[1]==row['step']:pos=self.last_arrival[0]
        t=self.tasks.get(tuple(pos)) if pos else None
        full=next((q for q in quotes if re.search(r'(持ち物|道具|アイテム).*(いっぱい|一杯|持てない)|これ以上.*持て',q)),None)
        pickup=next((q for q in quotes if re.search(r'拾った|ひろった|手に入れた',q) and not re.search(r'拾え|ひろえ|持てない',q)),None)
        if (full or pickup) and not t:
            self.revision+=1;self.unlocated_evidence=(self.unlocated_evidence+[{'step':row.get('step'),'quote':full or pickup,'kind':'capacity_message' if full else 'pickup_message','status':'location_unconfirmed'}])[-8:]
        if full:
            self.capacity={'source':'current_ocr_message','quote':full,'step':row.get('step')}
            if t:self.change(t,'capacity_blocked','満杯メッセージを確認。所持品と利用価値を比較',self.capacity)
        elif pickup and t and t['status'] in ('arrival_unverified','awaiting_inventory_check','awaiting_evidence','classification_unresolved','not_visible_unresolved'):
            self.change(t,'pickup_confirmed','現在位置と新しい取得メッセージを照合',{'source':'current_ocr_message','quote':pickup,'step':row.get('step')})
    def context(self):
        tasks=[{**copy.deepcopy(t),'decision_active':t['status']=='deferred' and t.get('decision')=='defer','label':LABELS[t['status']]} for t in self.tasks.values()]
        pending=[t for t in tasks if t['status'] in ('arrival_unverified','awaiting_inventory_check','capacity_blocked') and self.checked.get(tuple(t['cell']))!=[t['attempts'],t['generation'],self.inventory_token] and not (t['status']=='capacity_blocked' and self.capacity and self.capacity.get('source')=='inventory_occupancy')]
        signature=hashlib.sha256(json.dumps([[t['cell'],t['status'],t['attempts']] for t in pending],sort_keys=True).encode()).hexdigest()[:16]
        return {'scope':self.scope,'revision':self.revision,'counts':dict(Counter(t['status'] for t in tasks)), 'tasks':tasks,
            'unlocated_evidence':copy.deepcopy(self.unlocated_evidence),'needs_inventory_check':bool(pending),'check_token':signature if pending else None,'capacity_evidence':copy.deepcopy(self.capacity),
            'note':'到達/表示消失/踏破済みを取得成功にしない。visibleは最後に読めた地図での表示であり、メニュー中などの現在画面の証明ではない。未分類マークはアイテムや階段と断定しない。'}
