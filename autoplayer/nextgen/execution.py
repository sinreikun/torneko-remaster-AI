"""Finite, evidence-checked action procedures above single-input selection.

No OS calls, LLM waits or game-specific keys. Game semantics are profile data.
"""
import copy,math,re
from collections import deque
from .state_hash import digest
from .interaction_surface import allows_field_input

class ProcedureExecutor:
    def __init__(self,store,bindings,policy):
        self.store=store;self.policy=policy;self.bindings=bindings
        self.roles={role:next((k for k,v in bindings.items() if re.search(pattern,v,re.I)),None) for role,pattern in policy.get('roles',{}).items()}
        saved=store.load('autonomy','execution_knowledge') if store else []
        self.battles=(saved[0].get('battles',{}) if saved else {});self.active=None;self.pending=None;self.history=deque(maxlen=24);self.pages={};self.inventory={};self.status={};self.scope=None;self.facing=None;self.last_review=-10000;self.review_needed=True;self.attempted={};self.described=set();self.stationary={};self.last_enemies=set();self.events_seen=deque(maxlen=128);self.pickups=deque(maxlen=16);self.control_revision=None;self.combat_pending=False;self.requirement=None;self.last_region=None;self.item_checks={};self.last_input_role=None;self.adventure_revision=0;self.dead=False;self.dirty=False;self.last_damage_step=-10000
    def death(self):
        if not self.dead:self.adventure_revision+=1
        self.dead=True;self.interrupt('death_or_result');self.pages={};self.inventory={};self.status={};self.review_needed=True;self.described=set();self.item_checks={};self.attempted={}
    def interrupt(self,reason):
        if self.active:self.history.append({'event':'procedure_interrupted','task':self.active['kind'],'reason':reason,'resume_required':True})
        self.active=None;self.pending=None;self.facing=None
    def attach(self,state,view,step):
        region=state.get('spatial',{}).get('stamp',{}).get('region');floor=state.get('floor_confirmation',{}).get('confirmed')
        # A floor transition does not destroy inventory; a confirmed death does.
        if region and self.scope!=region:
            if self.active:self.interrupt('region_changed')
            self.facing=None;self.stationary={};self.scope=region
        for k,v in state.get('metrics',{}).items():
            if v.get('values') is not None:self.status[k]={**copy.deepcopy(v),'frame':state['frame_sha256'],'step':step}
        inv=view.get('inventory',{})
        if inv.get('status')=='observed':
            self.inventory=copy.deepcopy(inv);self.pages[inv['page_signature']]=copy.deepcopy(inv['rows'])
            while len(self.pages)>self.policy.get('page_limit',4):self.pages.pop(next(iter(self.pages)))
        state['execution_ui']=view
        if self.requirement:state['execution_requirement']=copy.deepcopy(self.requirement)
        state['execution']=self.summary(step)
        return state
    def values(self,state,name,retained=True):
        metric=state.get('metrics',{}).get(name)
        if metric and not metric.get('retained'):return metric.get('values')
        if not retained:return None
        return state.get('retained',{}).get(name,{}).get('values') or self.status.get(name,{}).get('values')
    def rows(self):return [r for rows in self.pages.values() for r in rows]
    def known_item(self,purpose):
        for rule in self.policy.get('items',[]):
            if rule['purpose']!=purpose:continue
            for row in self.rows():
                known_name=(row.get('display_name') in rule.get('names',[]) or rule.get('name_pattern') and re.search(rule['name_pattern'],row.get('display_name',''))) and not row.get('identification_required')
                description=' '.join(row.get('known_description',{}).get('quotes',[]))
                described=any(re.search(pattern,description) for pattern in rule.get('description_patterns',[])) and not re.search(r'減|下が|毒|呪',description)
                if known_name or described:return row,{**rule,'basis':'current_description_inference' if not known_name else 'profile_name_rule'}
        return None
    def eligible(self,key,step):return self.attempted.get(key,-1)<=step
    def begin(self,kind,state,step,reason,item=None):
        if kind=='review':self.pages={}
        self.active={'kind':kind,'stage':'open','created':step,'deadline':step+self.policy.get('task_budget',48),'reason':reason,'pages':[],'descriptions':0,'missing':0,'item':copy.deepcopy(item),'origin':state.get('spatial',{}).get('stamp'),'before_metrics':copy.deepcopy(self.status),'sent':False}
        self.history.append({'event':'procedure_started','kind':kind,'reason':reason,'step':step})
    def finish(self,status,step,reason):
        if not self.active:return
        a=self.active;self.history.append({'event':'procedure_finished','kind':a['kind'],'status':status,'step':step,'reason':reason,'origin':a['origin']})
        token=self.task_token(a['kind'],a.get('item'));self.attempted[token]=step+128
        if a['kind']=='review':self.last_review=step;self.review_needed=False
        self.active=None;self.pending=None
        while len(self.attempted)>128:self.attempted.pop(next(iter(self.attempted)))
    @staticmethod
    def task_token(kind,item):return digest([kind,(item or ({},))[0].get('entry_id') if item else None])
    def request(self,state,keys,role,reason,step):
        keys=[k for k in keys if k]
        if not keys:return None
        request={'keys':keys,'role':role,'reason':reason,'task':self.active['kind'] if self.active else 'combat','frame':state['frame_sha256'],'stamp':copy.deepcopy(state.get('spatial',{}).get('stamp')),'deadline':self.active['deadline'] if self.active else step+1,'evidence':{'metrics':copy.deepcopy(state.get('metrics',{})),'enemies':copy.deepcopy(state.get('minimap',{}).get('enemies',[]))}}
        self.pending=copy.deepcopy(request);return request
    def role_request(self,state,role,reason,step):return self.request(state,[self.roles.get(role)],role,reason,step)
    def navigate(self,state,labels,step,row=None):
        ui=state.get('execution_ui',{});index=ui.get('selected_line');entries=ui.get('entries',[])
        selected=next((e for e in entries if e['line']==index),None)
        targets=[e for e in entries if e['line']==row.get('line_index')] if row else [e for e in entries if e['text'] in labels]
        if not selected or len(targets)!=1:return self.missing(state,step,'selection_or_target_unknown')
        target=targets[0]
        if selected['line']==target['line']:return self.role_request(state,'confirm','現在選択と目的項目を照合して決定',step)
        a,b=selected.get('box'),target.get('box')
        if not a or not b:return self.missing(state,step,'selection_geometry_unknown')
        dx,dy=b[0]-a[0],b[1]-a[1]
        role=('down' if dy>0 else 'up') if row or abs(dy)>abs(dx)*.6 else 'right' if dx>0 else 'left'
        return self.role_request(state,role,'観測したカーソルから目的項目へ一段階移動',step)
    def missing(self,state,step,reason):
        self.requirement={'reason':reason,'created':step,'deadline':step+4,'type':'observation_missing'}
        state['execution_observation_request']={'reason':reason,'full_ocr':True,'fresh':True,'selection_tracking':True}
        if self.active:
            self.active['missing']+=1
            if self.active['missing']>4:self.finish('unconfirmed',step,reason)
        return None
    def enemies(self,state,step):
        pose=state.get('spatial',{}).get('pose');out=[]
        if not pose or state.get('spatial',{}).get('status')!='ready':return out
        for d in state.get('minimap',{}).get('enemies',[]):
            delta=tuple(d);absolute=(pose[0]+d[0],pose[1]+d[1]);key=str(absolute)
            old=self.stationary.get(key,{'count':0,'step':-1})
            if old['step']!=step:self.stationary[key]={'count':old['count']+1,'step':step}
            out.append({'delta':delta,'cell':absolute,'distance':max(map(abs,d)),'stationary_candidate':self.stationary[key]['count']>=3})
        current={str(e['cell']) for e in out};self.stationary={k:v for k,v in self.stationary.items() if k in current}
        return out
    def choose(self,state,candidates,step):
        self.pending=None
        if not self.policy:return None
        hp=self.values(state,'hp',False);food=self.values(state,'fullness',False);ui=state.get('execution_ui',{});kind=ui.get('kind')
        if kind=='choice' and not ui.get('protected'):
            return self.navigate(state,self.policy['labels']['yes'],step)
        if kind=='result':
            self.death()
            return self.role_request(state,'confirm','結果画面を決定で進める',step)
        if hp and hp[0]==0:
            self.death();return None
        if hp and hp[0]>0:self.dead=False
        self.requirement=None
        if self.active and step>=self.active['deadline']:self.finish('expired',step,'finite_budget')
        enemies=self.enemies(state,step);adjacent=[e for e in enemies if e['distance']==1 and not(e['stationary_candidate'] and hp and hp[0]<hp[1]*.7 and not self.recent_incoming(step))];near=[e for e in enemies if e['distance']<=2]
        field=allows_field_input(state) and state.get('spatial',{}).get('status')=='ready' and state.get('spatial',{}).get('provider')=='measured'
        if not self.active and field:
            damage=self.expected_incoming(state)
            low=bool(hp and (hp[0]<hp[1]*self.policy.get('low_hp_ratio',.4) or damage and hp[0]<=damage*2));hungry=bool(food and food[0]<=self.policy.get('food_threshold',15))
            heal=self.known_item('heal');meal=self.known_item('food') or (self.known_item('emergency_food') if food and food[0]==0 else None)
            if low and adjacent and heal and self.eligible(self.task_token('heal',heal),step):self.begin('heal',state,step,'隣接脅威と現在HPから回復品を使用',heal)
            elif hungry and not adjacent and meal and self.eligible(self.task_token('food',meal),step):self.begin('food',state,step,'空腹による損失を防ぐ',meal)
            elif hungry and adjacent and meal and food and food[0]==0 and hp and hp[0]>max(3,hp[1]*.4):self.begin('food',state,step,'空腹による継続損失を食料で解消',meal)
            elif adjacent:
                # Low HP never falls through to frontier because a skill is absent.
                tactical=self.reduce_contacts(state,enemies,adjacent,candidates,step)
                if tactical:return tactical
                enemy=min(adjacent,key=lambda e:(e['stationary_candidate'],e['cell']))
                return self.fight(state,enemy,candidates,step)
            elif [0,0] in state.get('minimap',{}).get('items',[]) and self.item_checks.get(str(state['spatial']['pose']),-1)<=step:
                self.item_checks[str(state['spatial']['pose'])]=step+128
                self.begin('review',state,step,'品の位置へ到達。取得・満杯を所持品と新しい文章で確認')
            elif hp and hp[0]<hp[1] and not near and food and food[0]>self.policy.get('rest_food_minimum',15):
                return self.role_request(state,'rest','接近する敵がなく、満腹度を確認して一手だけ自然回復',step)
            elif low and not heal and self.last_review<step-32 and self.eligible(self.task_token('review',None),step):self.begin('review',state,step,'回復手段が未確認。有限の所持品確認')
            else:
                if hungry and not meal and self.eligible(self.task_token('review',None),step):
                    self.begin('review',state,step,'現在の空腹に対する食料を一通り確認')
                    return self.advance(state,step)
                ranged=self.known_item('ranged')
                aligned=[e for e in enemies if e['distance']>=2 and not e['stationary_candidate'] and self.clear_ray(state,e)]
                if ranged and aligned and self.eligible(self.task_token('ranged',ranged),step):
                    target=min(aligned,key=lambda e:e['distance']);direction=tuple((1 if n>0 else -1 if n<0 else 0) for n in target['delta'])
                    if self.facing!=direction:return self.orient(state,target,candidates,step)
                    self.begin('ranged',state,step,'射線を確認し、装備ターンを使わず所持品から撃つ',ranged)
                    self.active['target']=target
                    return self.advance(state,step)
                responsive=[e for e in near if not e['stationary_candidate']]
                if responsive and self.stationary[str(responsive[0]['cell'])]['count']<=self.policy.get('enemy_response_probes',2):return self.role_request(state,'rest','一マス空いた敵へ不用意に接近せず、接近反応を一手観測',step)
                seed=self.known_item('growth');strength=self.values(state,'strength')
                growth=seed if seed and strength and strength[0]==strength[1] else self.known_item('restore_strength') if seed and strength and strength[0]<strength[1] else None
                if growth and self.eligible(self.task_token(growth[1]['purpose'],growth),step):self.begin(growth[1]['purpose'],state,step,'現在ちからを確認して強化手順を実行',growth)
                elif (self.review_needed or step-self.last_review>=self.policy.get('review_period',128)) and self.eligible(self.task_token('review',None),step):self.begin('review',state,step,'開始時・取得後・周期の状態と所持品確認')
                elif not near:
                    discard=self.discard_candidate()
                    if discard and self.eligible(self.task_token('discard',discard),step):
                        self.begin('discard',state,step,'所持上限。観測済み性能で劣る非装備品だけを置く候補にする',discard)
                        return self.advance(state,step)
                    identify=self.known_item('identify');aliases=[r for r in self.rows() if r.get('identification_required')]
                    if identify and aliases and self.eligible(self.task_token('identify',identify),step):
                        self.begin('identify',state,step,'未識別品を現在の対象選択画面で鑑定する',identify);self.active['identify_target']=copy.deepcopy(aliases[0]);return self.advance(state,step)
                    upgrade=self.upgrade(state)
                    if upgrade and self.eligible(self.task_token('equip',upgrade),step):self.begin('equip',state,step,'観測した装備性能を現在値と比較',upgrade)
        # A threatened passive audit must not keep the agent in menus indefinitely.
        if self.active and adjacent and self.active['kind']=='review' and field:
            self.finish('interrupted',step,'combat_interrupt');return self.fight(state,adjacent[0],candidates,step)
        if self.active:return self.advance(state,step)
        return None
    def reduce_contacts(self,state,enemies,adjacent,candidates,step):
        if len(adjacent)<2:return None
        pose=state['spatial']['pose'];known=state['spatial'].get('map',{}).get('cells',{});blocked=set(state['spatial'].get('map',{}).get('obstacles',[]));occupied={tuple(e['cell']) for e in enemies};options=[]
        for dx,dy in ((0,-1),(1,0),(0,1),(-1,0),(-1,-1),(1,-1),(1,1),(-1,1)):
            dest=(pose[0]+dx,pose[1]+dy);tag=lambda p:f'{p[0]},{p[1]}'
            if tag(dest) not in known or tag(dest) in blocked or dest in occupied:continue
            if dx and dy and any(tag(p) not in known or tag(p) in blocked for p in ((pose[0]+dx,pose[1]),(pose[0],pose[1]+dy))):continue
            contacts=sum(max(abs(dest[i]-e['cell'][i]) for i in (0,1))<=1 for e in enemies)
            if contacts>=len(adjacent):continue
            keys=[]
            if dx:keys.append(self.roles.get('right' if dx>0 else 'left'))
            if dy:keys.append(self.roles.get('down' if dy>0 else 'up'))
            if all(keys) and any(set(a['keys'])==set(keys) for a in candidates):options.append((contacts,len(keys),keys))
        if not options:return None
        choice=min(options,key=lambda x:x[:2]);return self.request(state,choice[2],'tactical_move','複数の隣接接触を減らせる既知床へ一手移動。距離だけの逃走ではない',step)
    def orient(self,state,enemy,candidates,step):
        dx,dy=enemy['delta'];keys=[self.roles.get('face')]
        if dx:keys.append(self.roles.get('right' if dx>0 else 'left'))
        if dy:keys.append(self.roles.get('down' if dy>0 else 'up'))
        if all(keys) and any(set(a['keys'])==set(keys) for a in candidates):return self.request(state,keys,'face','対象への方向を設定。空間移動と区別',step)
        return self.missing(state,step,'orientation_binding_missing')
    @staticmethod
    def clear_ray(state,enemy):
        dx,dy=enemy['delta']
        if dx and dy and abs(dx)!=abs(dy):return False
        pose=state['spatial']['pose'];n=enemy['distance'];sx=(1 if dx>0 else -1) if dx else 0;sy=(1 if dy>0 else -1) if dy else 0
        known=state['spatial'].get('map',{}).get('cells',{});obstacles=state['spatial'].get('map',{}).get('obstacles',[])
        return all(f'{pose[0]+sx*i},{pose[1]+sy*i}' in known and f'{pose[0]+sx*i},{pose[1]+sy*i}' not in obstacles for i in range(1,n))
    def discard_candidate(self):
        if (self.inventory.get('occupancy') or {}).get('count')!=20:return None
        rows=self.rows()
        for row in rows:
            e=row.get('equipment_estimate')
            if not e or e.get('special') or row.get('equipped') is not False or row.get('identification_required'):continue
            if any(re.search(p,row['display_name']) for p in self.policy.get('capacity',{}).get('protect_patterns',[])):continue
            if any(other.get('equipment_estimate',{}).get('slot')==e['slot'] and other['equipment_estimate']['value']>e['value'] for other in rows):return row,{'purpose':'discard','command':'discard','metric':None}
        return None
    def fight(self,state,enemy,candidates,step):
        dx,dy=enemy['delta'];direction=(int(math.copysign(1,dx)) if dx else 0,int(math.copysign(1,dy)) if dy else 0)
        movement=[self.roles.get('right' if direction[0]>0 else 'left')] if direction[0] else []
        if direction[1]:movement.append(self.roles.get('down' if direction[1]>0 else 'up'))
        if self.facing!=direction:
            keys=[self.roles.get('face')]+movement
            if all(keys) and any(set(a['keys'])==set(keys) for a in candidates):return self.request(state,keys,'face','隣接対象の方向へ向く。移動・撃破とは別に検証',step)
            return self.missing(state,step,'orientation_binding_missing')
        self.combat_pending=True
        return self.role_request(state,'confirm','隣接対象へ通常攻撃。命中・被害・撃破は文章と実測で確認',step)
    def recent_incoming(self,step):return step-self.last_damage_step<=2 or any(e['step']>=step-2 and e['message'].get('kind')=='damage_received' for b in self.battles.values() for e in b['evidence'])
    def expected_incoming(self,state):
        samples=[max(b['received']) for b in self.battles.values() if len(b['received'])>=2 and b['status_values'].get('level')==self.values(state,'level') and b['status_values'].get('shield')==self.values(state,'shield')]
        return max(samples) if samples else None
    def upgrade(self,state):
        options=[]
        for row in self.rows():
            estimate=row.get('equipment_estimate');desc=' '.join(row.get('known_description',{}).get('quotes',[]))
            if not estimate or row.get('equipped') or re.search(r'呪|のろ',row['display_name']+desc):continue
            current=self.values(state,estimate['slot'])
            if current and estimate['value']>current[0] and not estimate.get('special'):
                options.append((estimate['value']-current[0],row,{'purpose':'equip','command':'equip','metric':estimate['slot'],'direction':'increase'}))
        return max(options,key=lambda x:x[0])[1:] if options else None
    def advance(self,state,step):
        a=self.active;ui=state.get('execution_ui',{});kind=ui.get('kind');field=allows_field_input(state);labels=self.policy['labels']
        if a['stage']=='identify_target':
            prompt=bool(re.search(self.policy.get('identification_prompt','(?!)'),' '.join(state.get('quotes',[]))))
            if kind!='inventory' or not prompt:return self.missing(state,step,'identification_target_prompt_unconfirmed')
            rows=ui.get('inventory',{}).get('rows',[]);wanted=a['identify_target']
            matches=[r for r in rows if r.get('id')==wanted.get('id')]
            if not matches:
                matches=[r for r in rows if r.get('display_name')==wanted.get('display_name')]
            if len(matches)!=1:return self.missing(state,step,'identification_instance_ambiguous')
            a['identification_selected_line']=matches[0]['line_index']
            return self.navigate(state,[],step,row=matches[0])
        if a['stage']=='verify':
            item=a.get('item');rule=item[1] if item else {};name=rule.get('metric');values=self.values(state,name,False) if name else None;old=a['before_metrics'].get(name,{}).get('values')
            if values and old and values[0]>old[0]:
                a['effect_observed']={'metric':name,'before':old,'after':values,'frame':state['frame_sha256'],'causality':'temporal_association_not_proven'}
                state['procedure_effect']={'verified':True,'capability':'equip' if a['kind']=='equip' else 'use_item','evidence':a['effect_observed']}
                a['stage']='closing';self.review_needed=True
                if field:self.finish('effect_observed',step,'fresh_metric_increased_causality_not_proven');return None
                return self.role_request(state,'cancel','効果の数値を記録したので画面を閉じる',step)
            if a.get('message_effect'):
                a['effect_observed']=a['message_effect'];a['stage']='closing'
                if field:self.finish('effect_observed',step,'new_explicit_message');return None
                return self.role_request(state,'cancel','実行後のメッセージを保存して戻る',step)
            if a.get('verification_steps',0)>=4:self.finish('unconfirmed',step,'effect_not_verified');return None
            a['verification_steps']=a.get('verification_steps',0)+1
            if field:return self.role_request(state,'menu','使用・装備後の能力値を確認',step)
            if kind=='main_menu':return self.role_request(state,'cancel','効果未確認を記録して主画面へ戻る',step)
            return self.role_request(state,'cancel','使用後の画面を一段階閉じて再確認',step)
        if a['stage']=='closing':
            if field:self.finish('effect_observed' if a.get('effect_observed') else 'observed_partial_inventory',step,'known_pages_recorded_unknown_pages_not_claimed');return None
            if kind:return self.role_request(state,'cancel','確認を終え一画面ずつ閉じる',step)
            return self.missing(state,step,'unexpected_screen_while_closing')
        if field:return self.role_request(state,'menu','目的の道具操作のため主メニューを開く',step)
        if kind=='main_menu':return self.navigate(state,labels['inventory'],step)
        if kind=='item_actions':
            if not a.get('item'):return self.role_request(state,'cancel','確認中に開いた使用メニューを閉じる',step)
            if not a.get('item_selected'):return self.missing(state,step,'item_popup_without_verified_entry')
            return self.navigate(state,labels.get(a['item'][1]['command'],[]),step)
        if kind!='inventory':return self.missing(state,step,'unrecognized_procedure_screen')
        inv=ui.get('inventory',{});rows=inv.get('rows',[])
        if inv.get('status')!='observed':return self.missing(state,step,'inventory_unreadable')
        signature=inv['page_signature']
        if a.get('item'):
            target=next((r for r in rows if r['entry_id']==a['item'][0]['entry_id']),None)
            if target:
                a['current_target']=copy.deepcopy(target)
                return self.navigate(state,[],step,row=target)
        else:
            selected=next((r for r in rows if r.get('selected')),None)
            unknown=[r for r in rows if not r.get('known_description') and r['entry_id'] not in self.described]
            if unknown and a['descriptions']<self.policy.get('description_limit',20):
                target=unknown[0]
                if not selected or target['entry_id']!=selected['entry_id']:
                    request=self.navigate(state,[],step,row=target)
                    # Reviewing a selected row never opens its action menu.
                    return request
                self.described.add(target['entry_id']);a['descriptions']+=1
                return self.role_request(state,'details','未確認の選択品の説明を一度だけ表示',step)
        if signature in a['pages'] or len(a['pages'])>=self.policy.get('page_limit',4):
            if a.get('item'):self.finish('unconfirmed',step,'target_not_on_observed_pages');return None
            a['stage']='closing';return self.role_request(state,'cancel','観測済みページを保存し確認の反復を終了',step)
        a['pages'].append(signature)
        return self.role_request(state,'right','別ページを一度確認。上下の項目移動と区別',step)
    def feedback(self,before,after,action,receipt,step,messages=()):
        p=self.pending;self.pending=None
        accepted=bool(p and p['frame']==before['frame_sha256'] and set(action['keys'])==set(p['keys']) and receipt.get('os_submission')=='accepted')
        if p and not accepted:self.interrupt('input_censored_or_mismatched');return
        if receipt.get('os_submission')!='accepted':return
        if not p:p={'role':'external_action','keys':action['keys']}
        if self.active and self.active.get('last_input')==[before['frame_sha256'],p['keys']]:
            self.active['repeated']=self.active.get('repeated',0)+1
            if self.active['repeated']>=3:self.finish('unconfirmed',step,'repeated_without_observed_effect')
        elif self.active:self.active['repeated']=0
        if self.active:self.active['last_input']=[before['frame_sha256'],p['keys']]
        if p['role']=='face':
            delta=[0,0]
            for role,d in [('up',(0,-1)),('down',(0,1)),('left',(-1,0)),('right',(1,0))]:
                if self.roles.get(role) in p['keys']:delta=[delta[i]+d[i] for i in (0,1)]
            self.facing=tuple(delta)
        elif any(self.roles.get(r) in p['keys'] for r in ('up','down','left','right')):self.facing=None
        a=self.active
        if a and p['role']=='confirm' and a['stage']=='identify_target':
            if before.get('execution_ui',{}).get('selected_line')==a.get('identification_selected_line'):a['stage']='verify';a['sent']=True
        elif a and p['role']=='confirm':
            kind=before.get('execution_ui',{}).get('kind')
            if kind=='inventory' and a.get('item'):
                a['item_selected']=bool(a.get('current_target') and a['current_target']['entry_id']==a['item'][0]['entry_id'])
            elif kind=='item_actions' and a.get('item_selected'):
                a['stage']='identify_target' if a['kind']=='identify' else 'verify';a['sent']=True;a['before_metrics']={**copy.deepcopy(before.get('retained',{})),**copy.deepcopy(before.get('metrics',{}))}
                self.pages={};self.inventory={};self.review_needed=True
        if a and a.get('stage')=='verify' and a.get('item'):
            metric=a['item'][1].get('metric');old=a['before_metrics'].get(metric,{}).get('values');new=after.get('metrics',{}).get(metric,{}).get('values')
            if old and new and new[0]>old[0]:
                after['procedure_effect']={'verified':True,'capability':'equip' if a['kind']=='equip' else 'use_item','metric':metric,'before':old,'after':new,'causality':'temporal_association_not_proven'}
        hp=after.get('metrics',{}).get('hp',{}).get('values');previous_hp=before.get('metrics',{}).get('hp',{}).get('values')
        if hp and previous_hp and hp[0]<previous_hp[0]:self.last_damage_step=step
        if hp and hp[0]==0:self.death()
        quotes=set(after.get('quotes',[]))-set(before.get('quotes',[]))
        for q in quotes:
            if re.search(r'拾った|ひろった|手に入れた|持てない|いっぱい',q):self.pickups.append({'quote':q,'frame':after['frame_sha256'],'step':step});self.review_needed=True
        if a and a.get('stage')=='verify':
            new_quotes=[q for q in after.get('quotes',[]) if q not in before.get('quotes',[])]
            purpose=(a.get('item') or ({},{}))[1].get('purpose')
            patterns={'discard':r'置いた|おいた','identify':r'正体|だった|とわかった|と分かった','ranged':r'ダメージを与えた|攻撃は|はずれ|外れ'}
            matching=[q for q in new_quotes if purpose in patterns and re.search(patterns[purpose],q)]
            if matching:a['message_effect']={'quotes':matching,'frame':after['frame_sha256'],'source':'new_explicit_ocr_message'}
        fresh=[m for m in messages if m.get('source_frame_sha256')!=before['frame_sha256'] and not any(m.get('quote','') and m['quote'] in re.sub(r'\s+','',q) for q in before.get('quotes',[]))]
        after['battle_observation']={'events':fresh,'frame_sha256':after['frame_sha256'],'input_role':p['role']}
        self.record_battle(before,after,fresh,step,p.get('task','unattributed_input'))
        self.last_input_role=p['role']
        self.save(step)
    def record_battle(self,before,after,messages,step,attack_mode='unknown'):
        for message in messages:
            mark=digest(message)
            if mark in self.events_seen:continue
            self.events_seen.append(mark);self.dirty=True
            signature=digest([self.values(before,'level'),self.values(before,'strength'),self.values(before,'sword'),self.values(before,'shield'),message.get('enemy'),attack_mode])
            row=self.battles.setdefault(signature,{'enemy_name':message.get('enemy'),'attack_mode':attack_mode,'status_values':{k:self.values(before,k) for k in ('level','strength','sword','shield')},'dealt':[],'received':[],'hits':0,'misses':0,'defeats':0,'evidence':[]})
            kind=message.get('kind')
            if kind=='damage_dealt':row['hits']+=1;row['dealt']=(row['dealt']+[message['damage']])[-32:]
            if kind=='damage_received':row['received']=(row['received']+[message['damage']])[-32:]
            if kind=='miss':row['misses']+=1
            if kind=='defeated_enemy':row['defeats']+=1
            row['hit_rate_observed']=row['hits']/max(1,row['hits']+row['misses']);row['evidence']=(row['evidence']+[{'message':message,'step':step,'before':before['frame_sha256'],'after':after['frame_sha256'],'relative_targets':before.get('minimap',{}).get('enemies',[]),'appearance_candidates':before.get('objects',[])[:4],'identity_link':'unconfirmed'}])[-8:]
            if self.store:
                from .knowledge_access import KnowledgeAccess,claim
                record=claim('combat:'+signature,'conditional_combat_experience',row,'EXPERIENCED',evidence=[{'frame':after['frame_sha256'],'step':step,'session':self.store.session}],scope={'profile':self.store.profile},entity_kind='monster')
                record['id']='combat:'+signature;KnowledgeAccess(self.store).put(record,step)
        while len(self.battles)>128:self.battles.pop(next(iter(self.battles)))
    def save(self,step,force=False):
        if self.store and self.dirty and (force or step%8==0):
            self.store.put('autonomy',{'battles':self.battles},step,key='execution_knowledge');self.dirty=False
    def summary(self,step):return {'active':copy.deepcopy(self.active),'adventure_revision':self.adventure_revision,'last_review':self.last_review,'review_needed':self.review_needed,'known_pages':len(self.pages),'inventory_count_observed':self.inventory.get('occupancy'),'recent':list(self.history)[-6:],'battle_models':len(self.battles),'battle_estimates':[{'enemy':b['enemy_name'],'status_values':b['status_values'],'hits':b['hits'],'misses':b['misses'],'hit_rate':b.get('hit_rate_observed'),'damage_received_max':max(b['received']) if b['received'] else None,'damage_dealt_mean':sum(b['dealt'])/len(b['dealt']) if b['dealt'] else None,'evidence_count':len(b['evidence'])} for b in list(self.battles.values())[-4:]],'requirement':self.requirement,'pickups':list(self.pickups)[-4:],'not_campaign_completion':True}
    @staticmethod
    def realize(state,request,candidates):
        if not request or request['frame']!=state['frame_sha256'] or request.get('stamp')!=state.get('spatial',{}).get('stamp'):return None
        ids=[a['id'] for a in candidates if set(a['keys'])==set(request['keys'])]
        return {'status':'ready','candidate_ids':ids,'request':request} if ids else None
