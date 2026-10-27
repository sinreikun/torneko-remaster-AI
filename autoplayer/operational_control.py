"""Single decision authority for bounded skills; fresh evidence before every input."""
from dataclasses import replace
import copy,json,math,re
from .interfaces import PROTECTED_CONFIRMATION,choice_action,ordinary_choice,torneko_menu_kind,result_screen,compact_text
from .controls import is_torneko

class OperationalControl:
    def __init__(self,cfg,effects,cycle=None):
        self.cfg=cfg;self.effects=effects;self.cycle=cycle;self.inspections={};self.trials={};self.sequence=0;self.last_selection={};self.item_context=None;self.last_inspection_step=-1000;self.inspected_revision=None;self.inspection_until=-1;self.inventory_seen_step=-1000;self.recovery_request=None;self.healing_trials={};self.healing_unsafe={};self.inventory_review=None;self.review_pending=None;self.item_action_request=None
        from .inventory_audit import InventoryAudit
        self.inventory_audit=InventoryAudit()
        from .strength_growth import StrengthGrowth
        self.strength_growth=StrengthGrowth()
        self.menu_followup=None
        from .equipment_preparation import EquipmentPreparation
        self.equipment_preparation=EquipmentPreparation()
        from .recovery_policy import RecoveryPolicy
        self.recovery_policy=RecoveryPolicy()
    def prepare_inventory_audit(self,state,current,native):
        self.inventory_audit.prepare(state,current,native,self.inventory_review)
        follow=self.menu_followup
        if not follow:return
        hp=current.get('metrics',{}).get('hp',{}).get('values');food=current.get('metrics',{}).get('fullness',{}).get('values')
        if self.recovery_request or any(max(abs(x),abs(y))<=1 for x,y in current.get('contacts',[])) or hp and hp[0]/max(1,hp[1])<.3 or food and food[0]<=15:
            self.menu_followup=None;return
        valid=current['step']-follow['step']<=4 and current['control_revision']==follow['control'] and current['operator_guidance'].get('revision',0)==follow['guidance'] and (not follow['floor'] or not current.get('floor') or follow['floor']==current['floor'])
        if not valid:self.menu_followup=None;return
        if follow.get('accepted') and current.get('interface')=='item_actions':
            self.inventory_audit.analyse_interface(current)
            self.inventory_audit.active['selected_item_quote']=follow['item_quote'];self.menu_followup=None
    def _decision(self,base,keys,source,reason,expected=None):
        return replace(base,keys=list(keys),hold_ms=min(80,self.cfg.max_hold_ms),wait_ms=min(600,self.cfg.max_wait_ms),confidence=.8,risk='normal',
          intent={},forecast={'horizon':1,'first_expected':expected or {},'continuations':[]},plan=[],progress=[],memory_updates=[],goal_evidence=[],knowledge_assessments=[],progress_test={},
          execution_stage=source,reason=reason,goal_success='次の観測で位置・HP・敵・現在画面の変化を別々に確認する。')
    def _key(self,role):
        keys=self.effects.keys(role)
        return keys[0] if keys else None
    def interface(self,base,native,state,plan,ledger,blocked):
        self.review_pending=None
        from .interfaces import normalize_item_menu
        native=normalize_item_menu(native)
        text='\n'.join(l.get('text','') for l in (native or {}).get('lines',[]));confirm=self._key('confirm');cancel=self._key('cancel')
        if PROTECTED_CONFIRMATION.search(text) and cancel:return self._decision(base,[cancel],'protected_confirmation','現在の保護確認から取消で離れる。')
        choice=choice_action(native or {},self.effects.bindings,self.cfg.allowed_keys)
        if not choice and ordinary_choice(native):
            toward=self._cursor_toward(native,'はい')
            if toward:choice={'key':confirm if toward=='selected' else toward,'reason':'現在のカーソルの画素仮説と項目配置を照合し、はいへ1段階進める。'}
        if ordinary_choice(native):
            selected_no=any(re.fullmatch(r'[▶►▷>→](?:いいえ|No)',compact_text(l.get('text','')),re.I) for l in (native or {}).get('lines',[]))
            visual=(native or {}).get('_visual_selection') or {}
            selected_no=selected_no or bool(visual.get('frame_sha256') and visual['frame_sha256']==(native or {}).get('frame_sha256') and compact_text(visual.get('text','')).casefold() in ('いいえ','no'))
            if not choice and selected_no:
                directions=[k for k,v in self.effects.bindings.items() if k in self.effects.keys('move') and re.match(r'^右|^下|^左|^上|^(right|down|left|up)$',v,re.I)]
                if directions:
                    scope=ledger.current['scope'];key=min(directions,key=lambda k:self.trials.get((scope,(k,)),0))
                    return self._decision(base,[key],'ordinary_choice_probe','現在はいいえを選択。はいへの方向は未確定なので方向を1回試し、選択を読み直す。')
            key=choice['key'] if choice else confirm
            if key:return self._decision(base,[key],'ordinary_choice',choice['reason'] if choice else '通常の選択肢を確認。取消で戻らず決定を1回試し、選択結果を確認する。')
        outcome=result_screen(native)
        if outcome:self.recovery_policy.reset();self.recovery_request=None;self.item_context=None;self.healing_trials.clear();self.healing_unsafe.clear()
        if outcome and confirm:
            key=confirm if [confirm] not in blocked else cancel or confirm
            return self._decision(base,[key],'result_screen','現在の結果表示を1回進め、再挑戦画面を確認する。')
        kind=torneko_menu_kind(native)
        skill=plan['skill']
        step=ledger.current['step'];request=self.recovery_request;current=ledger.current
        if request and (step>request['until'] or request['control']!=current['control_revision'] or request['guidance']!=current['operator_guidance'].get('revision',0) or request['floor'] and current['floor'] and request['floor']!=current['floor']):
            self.recovery_request=None;request=None
        hp=state.get('metrics',{}).get('hp',{});food=state.get('metrics',{}).get('fullness',{})
        hp_values=hp.get('values');food_values=food.get('values')
        urgent=bool(hp_values and not hp.get('retained') and hp_values[0]/max(1,hp_values[1])<.3 or food_values and not food.get('retained') and food_values[0]<=15)
        if hp_values and not hp.get('retained') and hp_values[0]>=hp_values[1] and food_values and not food.get('retained') and food_values[0]>15:
            self.recovery_request=None;request=None
            if skill=='recover':skill='explore'
        if request or urgent:skill='recover'
        recovery_blocked=self.recovery_policy.blocked(state)
        if recovery_blocked and skill=='recover':
            self.recovery_request=None;skill='explore';self.inspection_until=-1

        if not recovery_blocked and (step<=self.inspection_until or self.inventory_audit.active and self.inventory_audit.active['stage']=='collect') and skill!='recover':skill='inspect_inventory'
        if kind!='inventory' or self.inventory_review and (self.inventory_review['control']!=current['control_revision'] or self.inventory_review['guidance']!=current['operator_guidance'].get('revision',0) or self.inventory_review.get('floor')!=current.get('floor')):self.inventory_review=None
        if self.inventory_review and step<=self.inventory_review['until'] and self.inventory_review['control']==current['control_revision'] and self.inventory_review['guidance']==current['operator_guidance'].get('revision',0) and skill!='recover':skill='inspect_inventory'
        if kind=='inventory':self.inventory_seen_step=step;self.inspection_until=-1
        if kind=='main_menu' and skill=='inspect_inventory' and not self.inventory_audit.active and (step-self.inventory_seen_step<24 or self.last_inspection_step>=0 and self.inspection_until<step<self.last_inspection_step+24):skill='explore'
        # A supervisor UI hint is a single, scoped role, never a macro. Exact
        # current text and quoted premise must still agree with the request.
        role=plan.get('interface_role','none');quote=plan.get('interface_quote','')
        hint_current=plan.get('request_text_key')==ledger.current.get('text_key') and bool(quote and compact_text(quote) in compact_text(text))
        if ledger.current.get('selection_key'):
            hint_current=hint_current and plan.get('request_selection')==ledger.current['selection_key']
        else:hint_current=hint_current and plan.get('request_frame')==ledger.current.get('frame_sha256')
        if not text:hint_current=plan.get('request_frame')==ledger.current.get('frame_sha256') and role!='none'
        if recovery_blocked and kind=='main_menu' and compact_text(quote)=='道具' and plan['skill']!='inspect_inventory':hint_current=False
        current_interface=bool(kind or ledger.current.get('interface'))
        if current_interface or hint_current and role!='none' and not state.get('eligible'):
            retained=state.get('retained_state',{})
            selected_rows=native.get('_current_inventory_rows') or (retained.get('last_observed_inventory') or {}).get('rows',[])
            seed_selected=any(r.get('selected') and r.get('display_name')=='ちからの種' for r in selected_rows) or (self.menu_followup or {}).get('item_quote')=='ちからの種'
            strength=state.get('metrics',{}).get('strength',{})
            values=strength.get('values')
            if kind=='item_actions' and seed_selected and (not values or len(values)!=2 or values[0]!=values[1] or strength.get('retained')) and cancel:
                return self._decision(base,[cancel],'strength_seed_requires_status','種を消費する前に現在のちからを確認する。低下中は毒消し草で戻してから使い、未確認の最大値を仮定しない。')
            if kind in ('inventory','item_actions') and skill=='recover':
                self.equipment_preparation.active=None
                target=self._recovery_item(native,state,kind,current)
                if target:
                    self.item_action_request=None
                    return self._decision(base,[target],'recovery_item','現在読めた品名・選択行・使用法を照合して回復を1段階進める。別の使用方法を決定しない。')
                self.recovery_policy.record_unavailable(native,state,step,'item_action_unconfirmed' if kind=='item_actions' else None)
                self.recovery_request=None
                check=self.recovery_policy.checks.get(self.recovery_policy.need(state),{})
                detail='所持数と表示行を照合し、現在の一覧に既知の回復品がないことを確認した。' if check.get('status')=='no_known_recovery_item' else '一覧全体または選択位置が未確認。回復品なしとは断定しない。'
                if cancel:return self._decision(base,[cancel],'recovery_check_finished',detail+'同じ所持品での自動確認を打ち切り、退避・自然回復・必要な対処を比較する。')
            if kind=='inventory' and 'equipment_status_after_change' in state.get('retained_state',{}).get('refresh_reasons',[]) and cancel:
                return self._decision(base,[cancel],'equipment_status_check','装備変更後の能力値を先に読み取るため、一覧から主メニューへ戻る。')
            if self.equipment_preparation.active:
                equipment=self.equipment_preparation.choose(self,base,native,state,current,kind)
                if equipment:return equipment
            if kind not in ('inventory','item_actions') or self.item_action_request and self.item_action_request.get('kind')!=kind:self.item_action_request=None
            menu_signature=tuple(sorted(compact_text(l.get('text','')).lstrip('▶►▷>●→') for l in native.get('lines',[])))
            request=self.item_action_request
            if request and (step>request['until'] or request['control']!=current['control_revision'] or request['guidance']!=current['operator_guidance'].get('revision',0) or request['text_key']!=menu_signature):
                self.item_action_request=None;request=None
            if kind in ('inventory','item_actions') and request is None and hint_current and not plan.get('hint_consumed') and role=='confirm':
                from .interfaces import ITEM_ACTION_LABELS
                action=compact_text(quote).lstrip('▶►▷>→')
                rows=native.get('_current_inventory_rows',[])
                item_labels={compact_text(r.get('quote','')).lstrip('▶►▷>●→') for r in rows}|{compact_text(r.get('display_name','')) for r in rows}
                allowed=action in ITEM_ACTION_LABELS if kind=='item_actions' else action in item_labels
                if not allowed:
                    if cancel:return self._decision(base,[cancel],'unconfirmed_item_action','決定する使用方法の引用を確定できず、取消で一覧へ戻る。')
                else:
                    request={'kind':kind,'action':action,'until':step+4,'control':current['control_revision'],'guidance':current['operator_guidance'].get('revision',0),'text_key':menu_signature}
                    self.item_action_request=request;plan['hint_consumed']=True
            if kind in ('inventory','item_actions') and request:
                target=self._cursor_toward(native,request['action'],vertical=kind=='inventory')
                if target:
                    if target=='selected':
                        if kind=='inventory':self.menu_followup={'item_quote':request['action'],'step':step,'control':current['control_revision'],'guidance':current['operator_guidance'].get('revision',0),'floor':copy.deepcopy(current.get('floor'))}
                        self.item_action_request=None
                    return self._decision(base,[confirm if target=='selected' else target],'requested_item_action','現在の使用方法の引用とカーソルを照合し、依頼された操作へ1段階進む。')
                self.item_action_request=None
                if cancel:return self._decision(base,[cancel],'unconfirmed_item_action','目的の使用方法と選択位置を照合できず、決定せず一覧へ戻る。')
            if hint_current and not plan.get('hint_consumed'):
                patterns={'up':r'^上|^up$','left':r'^左|^left$','down':r'^下|^down$','right':r'^右|^right$'}
                key=self._key(role) if role in ('confirm','cancel') else next((k for k,v in self.effects.bindings.items() if k in self.cfg.allowed_keys and re.search(patterns.get(role,r'(?!)'),v,re.I)),None)
                if key and [key] not in blocked:
                    plan['hint_consumed']=True
                    return self._decision(base,[key],'scoped_interface_hint','現在の同じ表示と引用を確認し、監督役が指定した役割の入力を1回試す。')
            if kind=='inventory' and skill!='recover' and role=='none':
                equipment=self.equipment_preparation.choose(self,base,native,state,current,kind,allow_start=True)
                if equipment:return equipment
            if kind=='main_menu' and skill in ('inspect_inventory','recover'):
                target=self._cursor_toward(native,'道具')
                if target and confirm:return self._decision(base,[confirm if target=='selected' else target],'inventory_navigation','現在のメニュー項目とカーソルから道具へ1段階進める。')
                if skill=='recover':
                    probe=self._recovery_cursor_probe(ledger)
                    if probe:return self._decision(base,[probe],'recovery_cursor_probe','回復は未完了。現在のメニューで方向入力を1回試し、カーソルを再観測する。未確認項目は決定しない。')
            if kind=='inventory' and skill=='inspect_inventory':
                review=self._inspect_inventory(base,native,current)
                if review:return review
            if kind and cancel:
                if skill=='recover':self.recovery_request=None
                return self._decision(base,[cancel],'close_known_menu','回復は未完了。現在の品・使用法・カーソルを確定できず、盲目的に使用せずメニューを閉じて再検討する。' if skill=='recover' else '表示中の品への説明確認の試行を終了し、既知のメニューを閉じる。未読・未表示ページを確認済みとはしない。' if kind=='inventory' and skill=='inspect_inventory' else '必要な確認を記録し、既知のメニューを閉じて進行へ戻る。')
            if confirm and not kind and current_interface:return self._decision(base,[confirm],'dialogue_advance','現在の文章枠を決定入力で1回進め、次の表示を確認する。')
        return None
    def _inspect_inventory(self,base,native,current):
        from .world_model import Inventory
        if 'num7' not in self.cfg.allowed_keys:return None
        rows=(native or {}).get('_current_inventory_rows',[])
        selected=[r for r in rows if r.get('selected')]
        if len(selected)!=1 or not selected[0].get('box'):return None
        step=current['step']
        if self.inventory_review is None:
            self.inventory_review={'until':step+(96 if self.inventory_audit.active else 64),'max_moves':80 if self.inventory_audit.active else 40,'control':current['control_revision'],'guidance':current['operator_guidance'].get('revision',0),'floor':copy.deepcopy(current.get('floor')),'seen':set(),'attempts':{},'moves':0}
        review=self.inventory_review
        if step>review['until'] or review['moves']>=review.get('max_moves',40):return None
        from .inventory_identity import review_key
        key=review_key
        for candidate in rows:
            if candidate.get('known_description',{}).get('status')=='description_text_observed':review['seen'].add(key(candidate))
        item=selected[0];token=key(item)
        # Num7 is a toggle, not an idempotent open command. At most two
        # accepted toggles per row allow an initially visible panel to reopen.
        if token not in review['seen'] and review['attempts'].get(token,0)<2 and 'num7' in self.cfg.allowed_keys:
            self.review_pending={'key':'num7','token':token,'action':'description'}
            return self._decision(base,['num7'],'inventory_description','現在選択中の品の説明表示を1回切り替え、同じ行の説明文を取得する。開閉を連打せず入力後に再観測する。')
        choices=[r for r in rows if r.get('box') and key(r) not in review['seen'] and review['attempts'].get(key(r),0)<2]
        choices.sort(key=lambda r:abs(r['box'][1]-item['box'][1]))
        if not choices:
            signature=tuple(r['display_name'] for r in rows)
            descriptions={compact_text(t) for r in rows for t in r.get('known_description',{}).get('quotes',[])}
            panel=any(compact_text(l.get('text','')) in descriptions for l in native.get('lines',[]))
            measured=native.get('_description_panel') or {}
            panel=panel or measured.get('status')=='located' and measured.get('frame_sha256')==native.get('frame_sha256')
            if self.inventory_audit.active and panel and signature not in review.get('hidden_panels',set()):
                self.review_pending={'key':'num7','token':signature,'action':'hide_panel'}
                return self._decision(base,['num7'],'inventory_uncover_rows','選択行に応じた位置の説明を閉じ、隠れた所持品の行を確認するためNum7を1回入力し、一覧と所持数を読み直す。')
            audit=self.inventory_audit.active
            if audit and audit['stage']=='collect' and audit['page_pending']:
                key_input=next((k for k,v in self.effects.bindings.items() if k in self.cfg.allowed_keys and v.startswith('右')),None)
                if key_input:
                    self.review_pending={'key':key_input,'token':token,'action':'page'}
                    return self._decision(base,[key_input],'inventory_review_page','左右で次の所持品ページを1回確認し、同一ページなら未確認のまま調査を終える。')
            return None
        direction='下' if choices[0]['box'][1]>item['box'][1] else '上'
        key_input=next((k for k,v in self.effects.bindings.items() if k in self.cfg.allowed_keys and v.startswith(direction)),None)
        if key_input:
            self.review_pending={'key':key_input,'token':token,'action':'move'}
            return self._decision(base,[key_input],'inventory_review_navigation','未確認の品の行へ上下で1段階進み、現在のカーソル位置を再観測する。')
        return None

    def execution_context(self):
        request=self.item_action_request or {};item=self.item_context or {};review=self.inventory_review or {}
        return {'recovery_active':self.recovery_request is not None,'recovery_checks':copy.deepcopy(self.recovery_policy.checks),
          'equipment_preparation':{k:copy.deepcopy(v) for k,v in (self.equipment_preparation.active or {}).items() if k in ('quote','slot','phase','until')},
          'item_action':{k:request[k] for k in ('kind','action','until') if k in request},
          'recovery_item':{k:item[k] for k in ('name','phase','step') if k in item},
          'inventory_audit':self.inventory_audit.context(),
          'description_review':{'active':bool(review),'until':review.get('until'),'completed_rows':len(review.get('seen',[]))},
          'rule':'有効な操作は同じ対象で続ける。危機・対象/画面/制御設定の変化・期限超過で中断する。'}

    def _recovery_cursor_probe(self,ledger):
        scope=ledger.current['scope']
        # Two distinct navigation probes per scope, not blind confirmation/use.
        for direction in ('左','上'):
            key=next((k for k,v in self.effects.bindings.items() if k in self.effects.keys('move') and v.startswith(direction)),None)
            if key and self.trials.get((scope,(key,)),0)==0:return key
        return None
    def _cursor_toward(self,native,target,vertical=False):
        from .world_model import Inventory
        from .interfaces import normalize_item_menu
        native=normalize_item_menu(native)
        InventoryBox=Inventory.line_box
        target_row=target if isinstance(target,dict) else None
        if target_row:
            matches=[r for r in native.get('_current_inventory_rows',[]) if r.get('id')==target_row.get('id')]
            if len(matches)!=1:return None
            target_line=matches[0].get('line_index');target=compact_text(matches[0].get('quote','')).lstrip('▶►▷>●→')
        else:target_line=None
        lines=(native or {}).get('lines',[]);selected=[];targets=[]
        visual=(native or {}).get('_visual_selection') or {}
        observed=compact_text(visual.get('text','')) if visual.get('frame_sha256') and visual['frame_sha256']==(native or {}).get('frame_sha256') else ''
        index=visual.get('line_index') if observed else None
        for line_index,line in enumerate(lines):
            label=compact_text(line.get('text',''));prefix=bool(re.match(r'^[▶►▷>→]',label));clean=label.lstrip('▶►▷>→')
            chosen=(line_index==index and label==observed) if type(index) is int else label==observed if observed else prefix
            if chosen:selected.append(line)
            if (line_index==target_line if target_row else clean==target or clean.startswith(target+'(')):targets.append(line)
        if len(selected)!=1 or not targets:return None
        if selected[0] in targets:return 'selected'
        # Duplicate names are distinct rows. Move toward the nearest observed
        # row with that label, without claiming which instance it is.
        if len(targets)>1:
            origin=InventoryBox(selected[0])
            choices=[(abs(InventoryBox(t)[1]-origin[1]),t) for t in targets if origin is not None and InventoryBox(t) is not None]
            if not choices:return None
            choices.sort(key=lambda v:v[0])
            if len(choices)>1 and choices[0][0]==choices[1][0]:return None
            targets=[choices[0][1]]
        def pos(line):
            boxes=[w.get('box') for w in line.get('words',[]) if len(w.get('box',[]))==4]
            return (min(b[0] for b in boxes),min(b[1] for b in boxes)) if boxes else None
        a=pos(selected[0]);b=pos(targets[0])
        if a is None or b is None:return None
        dx,dy=b[0]-a[0],b[1]-a[1]
        if vertical and dy==0:return None
        direction=('下' if dy>0 else '上') if vertical else ('右' if dx>0 else '左') if abs(dx)>abs(dy) else ('下' if dy>0 else '上')
        return next((k for k,v in self.effects.bindings.items() if k in self.cfg.allowed_keys and (v.startswith(direction) or v.lower()=={'上':'up','下':'down','左':'left','右':'right'}[direction])),None)
    def _recovery_item(self,native,state,kind,current):
        hp=state.get('metrics',{}).get('hp',{}).get('values');food=state.get('metrics',{}).get('fullness',{}).get('values')
        lines=[compact_text(l.get('text','')).lstrip('▶►▷>→') for l in (native or {}).get('lines',[])]
        if kind=='inventory':
            names=('パン','大きいパン','大きなパン') if food and food[0]<=15 else ('薬草','弟切草') if hp and hp[0]/max(1,hp[1])<.4 else ()
            for name in names:
                # Exact visible name only, including its displayed quantity.
                label=next((compact_text(r['quote']).lstrip('▶►▷>→') for r in (native or {}).get('_current_inventory_rows',[]) if r.get('display_name')==name),None) or next((x for x in lines if re.fullmatch(re.escape(name)+r'(?:[×x]\d+|\(\d+\))?',x)),None)
                if label:
                    target=self._cursor_toward(native,label,vertical=True)
                    if target:
                        if target=='selected':self.item_context={'name':name,'phase':'opening','step':current['step'],'control':current['control_revision'],'guidance':current['operator_guidance'].get('revision',0),'floor':current['floor']}
                        return self._key('confirm') if target=='selected' else target
        else:
            item=self.item_context
            if not item or item['phase']!='actions' or current['step']-item['step']>3 or item['control']!=current['control_revision'] or item['guidance']!=current['operator_guidance'].get('revision',0) or item['floor'] and current['floor'] and item['floor']!=current['floor']:return None
            action='食べる' if item['name'] in ('パン','大きいパン','大きなパン') else '飲む'
            target=self._cursor_toward(native,action)
            if target:
                if target=='selected':item['phase']='using'
                else:item['_navigation']=True
                return self._key('confirm') if target=='selected' else target
        return None
    @staticmethod
    def authorized_recheck(state,candidate):
        if candidate.get('source')!='floor_navigation':return False
        route=candidate.get('detail',{}).get('route',{});pos=state.get('minimap',{}).get('player_cell')
        if not isinstance(pos,(list,tuple)) or len(pos)!=2 or any(type(v) is not int for v in pos):return False
        if route.get('mode')!='traversed_edge_recheck' or route.get('target_kind')!='route_recheck' or route.get('edge_origin')!=pos or type(route.get('traversal_evidence')) is not int or route['traversal_evidence']<=0:return False
        key=route.get('edge_key');d={'w':(0,-1),'s':(0,1),'a':(-1,0),'d':(1,0)}.get(key)
        return bool(d and candidate['decision'].keys==[key] and route.get('edge_target')==[pos[0]+d[0],pos[1]+d[1]])

    def natural_recovery(self,base,state,current,stable,blocked):
        m=state.get('minimap',{});metrics=state.get('metrics',{})
        hp=metrics.get('hp',{}).get('values');food=metrics.get('fullness',{}).get('values');floor=metrics.get('floor',{}).get('values')
        key=self._key('step')
        if not (stable and state.get('eligible') and m.get('status')=='observed' and not m.get('retained') and m.get('player_cell') is not None and floor and hp and food and 0<hp[0]<hp[1] and food[0]>15 and key and [key] not in blocked):return None
        passive={tuple(p) for p in self.recovery_policy.context(state)['stationary_enemy_cells']}
        pos=m.get('player_cell');active=[e for e in m.get('enemies',[]) if (pos[0]+e[0],pos[1]+e[1]) not in passive or max(map(abs,e))<=2]
        if active or current.get('interface') or state.get('menu_evidence') or state.get('white_frame_evidence',{}).get('visible') or any(metrics.get(k,{}).get('retained') for k in ('hp','fullness','floor')):return None
        scope=json.dumps([floor,m['player_cell'],current['control_revision'],current['operator_guidance'].get('revision',0)])
        trial=json.dumps([scope,hp])
        if scope in self.healing_unsafe or self.healing_trials.get(trial,0)>=8:return None
        d=self._decision(base,[key],'natural_recovery','現在の観測に活動中と判断した近い敵がなく満腹度に余裕がある。静止マークがあっても安全は未確定。満腹度を消費して回復品を温存する足踏みを1回入力。敵の手番も進むため、HP・敵の接近・満腹度を読み直す。',{'player_delta':[0,0]})
        return d,{'scope':scope,'trial':trial,'hp_before':list(hp),'accepted_without_gain_limit':8}

    def select(self,base,state,native,ledger,plan,policy,predictions,combat,mapless,exterior,blocked,stable,guidance):
        candidates=[];pc=None;combat_trial=None;prediction_context={};self.sequence+=1
        state['recovery_policy']=self.recovery_policy.context(state)
        def offer(decision,source,skill,score,detail=None):
            if decision is not None and (decision.keys or decision.status=='complete'):
                if not ledger.current.get('interface') and not self.recovery_policy.allows_move(state,decision.keys,self.effects.bindings):return
                priority=105 if source=='strength_growth' else 88 if source=='recovery_retreat' else 100 if source=='interface' else 95 if source=='natural_recovery' else 90 if source=='inventory_inspection' and skill=='recover' else 85 if source=='inventory_inspection' and ((detail or {}).get('tactical_inventory') or (detail or {}).get('equipment_followup')) else 80 if source=='combat' else 75 if source=='inventory_inspection' and ((detail or {}).get('before_combat') or (detail or {}).get('periodic_audit')) else 65 if source=='inventory_inspection' and (detail or {}).get('item_followup') else 70 if source=='floor_navigation' and (detail or {}).get('route',{}).get('target_kind')=='enemy' else 60 if source=='floor_navigation' and (detail or {}).get('route',{}).get('target_kind') in ('items','inspect_marker') else 50 if source=='floor_navigation' else 45 if source=='inventory_inspection' else 40
                candidates.append({'decision':decision,'source':source,'skill':skill,'score':score,'priority':priority,'detail':detail or {}})
        quote=plan.get('interface_quote','')
        if plan['skill']=='verify_goal' and quote and quote in ledger.current.get('text','') and re.search(r'持ち帰|帰還|クリア|おめでとう',quote):
            complete=replace(base,status='complete',keys=[],goal_evidence=[quote],goal_success=quote,subgoal='現在の帰還/達成表示を独立に検証する')
            operation={'authority':'operational_control','plan_id':plan['id'],'objective':plan['objective'],'skill':'verify_goal','selected_source':'completion_check','expected_signal':'interface_change','scope':ledger.current['scope'],'candidates':[],'experiment':None}
            return complete,{'source':'operational_completion_check'},None,{},operation
        growth=self.strength_growth.choose(self,base,native,state,ledger.current,blocked) if is_torneko(self.cfg.window_title) else None
        interface=None if growth else self.interface(base,native,state,plan,ledger,blocked)
        if growth:
            self.review_pending=None
            offer(growth,'strength_growth','inspect_inventory',10.)
        if interface:offer(interface,'interface','interface',10.)
        context=exterior.context
        current=ledger.current
        if not interface and context.get('active'):
            route=mapless.propose(allow_facing=False) if context.get('mode')=='exit_exploration' else None
            if route:offer(self._decision(base,route[0],'exterior_route',route[1]),'exterior_route','exit',2.)
            for keys in context.get('actions',[]):
                estimate=self.effects.estimate(state,keys);count=self.trials.get((current['scope'],tuple(keys)),0)
                score=.4+max(-.3,min(.3,estimate['mean_observed_value']))-.08*min(6,count)
                if keys and keys[0] in self.effects.keys('move'):score+=.1
                offer(self._decision(base,keys,'exterior_probe','現在の出口探索の許可候補から、実測と未検証の疑問を比較する。'),'exterior_probe','exit',score,estimate)
        elif not interface:
            hp=state.get('metrics',{}).get('hp',{}).get('values');food=state.get('metrics',{}).get('fullness',{}).get('values')
            urgent=bool(hp and hp[0]/max(1,hp[1])<.3 or food and food[0]<=15)
            if plan['skill']=='recover' or urgent or hp and hp[0]<hp[1]:
                natural=self.natural_recovery(base,state,current,stable,blocked)
                if natural:offer(natural[0],'natural_recovery','recover',2.4,natural[1])
            retreat=self.recovery_policy.retreat(state,self.effects.bindings,blocked)
            if retreat:offer(self._decision(base,[retreat],'recovery_retreat','低HPだが距離だけを目的に逃げない。観測済み階段への具体的な経路を1マス進み、敵・HP・目的地までの経路を再確認する。逃げ切れるとは仮定しない。'),'recovery_retreat','recover',2.3)
            if state.get('eligible') and combat:
                d,trial=combat.choose(base,state,stable,blocked)
                if trial:offer(d,'combat','combat',2.,{'trial':trial})
            retained=state.get('retained_state',{});revision=retained.get('revision')
            enemies=state.get('minimap',{}).get('enemies',[])
            prepare_combat=bool(retained.get('before_combat_check_needed') and enemies and not any(max(abs(x),abs(y))<=1 for x,y in enemies))
            wants_inventory=(plan['skill']=='inspect_inventory' or plan['skill']=='recover' and bool(hp and hp[0]<hp[1] or food and food[0]<=15)) or urgent or bool(self.inventory_audit.active) or bool(retained.get('refresh_reasons')) or state.get('floor_exploration',{}).get('goal',{}).get('item_recheck_needed',False) or state.get('floor_exploration',{}).get('goal',{}).get('needs_review',False)
            recovery_blocked=self.recovery_policy.blocked(state)
            if recovery_blocked and plan['skill']!='inspect_inventory' and not self.inventory_audit.active and not retained.get('refresh_reasons') and not state.get('floor_exploration',{}).get('goal',{}).get('item_recheck_needed'):wants_inventory=False
            inspection_scope=(current['scope'],'food' if food and food[0]<=15 else 'health' if urgent else 'status')
            equipment_followup='equipment_status_after_change' in retained.get('refresh_reasons',[])
            item_followup=state.get('floor_exploration',{}).get('goal',{}).get('item_recheck_needed',False)
            if wants_inventory and self._key('menu') and not current.get('interface') and current['step']-self.last_inspection_step>=(1 if equipment_followup else 4 if urgent and not recovery_blocked or item_followup else 24) and (urgent and not recovery_blocked or self.inventory_audit.active or revision is None or revision!=self.inspected_revision):
                offer(self._decision(base,[self._key('menu')],'inventory_inspection','現在の不足情報を得るため道具・状態を一度確認する。'),'inventory_inspection','recover' if urgent and not recovery_blocked else 'inspect_inventory',2.2 if urgent else 1.8,{'periodic_audit':bool(self.inventory_audit.active),'before_combat':prepare_combat,'equipment_followup':equipment_followup,'tactical_inventory':bool(enemies and plan['skill']=='inspect_inventory'),'item_followup':state.get('floor_exploration',{}).get('goal',{}).get('item_recheck_needed',False)})
            hint=state.get('map_route_hint')
            if hint and state.get('eligible') and not any(c['skill']=='combat' for c in candidates):
                keys=hint.get('keys') or [hint.get('key')]
                if keys and all(k in self.cfg.allowed_keys for k in keys):
                    caption='通過実績と無移動の食い違う経路を再検証する' if hint.get('target_kind')=='route_recheck' else '未分類マークを確認する' if hint.get('target_kind')=='inspect_marker' else '見えている敵へ対処する位置に向かう' if hint.get('target_kind')=='enemy' else '観測済み階段へ向かい操作結果を確認する' if hint.get('target_kind')=='stairs' else '観測済みの品の位置へ向かう' if hint.get('target_kind')=='items' else '観測済みの接続で別の未確認方向へ向かう' if hint.get('mode')=='backtrack_to_frontier' else '未処理の探索先を確認する'
                    offer(replace(self._decision(base,keys,'floor_navigation',hint.get('reason','現在の階内目標への経路を1入力ずつ確認する。')),subgoal=caption),
                        'floor_navigation','combat' if hint.get('target_kind')=='enemy' else 'explore',1.85,{'route':hint})
            if policy and state.get('eligible') and not any(c['skill']=='combat' for c in candidates):
                d,choice=policy.choose(base,state,blocked,stable,None,bool(guidance.get('text')))
                if predictions:d,choice,prediction_context=predictions.choose(d,state,choice,blocked)
                pc=choice
                selected=(choice or {}).get('selected') or {}
                score=.8+max(-.3,min(.3,selected.get('expected_return',selected.get('q',0))))
                if choice.get('exploration_route'):score+=.35
                offer(d,'policy','explore',score,{'choice':choice})
            if policy and state.get('eligible') and pc and not any(c['skill']=='combat' for c in candidates):
                for rank in (pc.get('context') or {}).get('ranking',[]):
                    keys=rank.get('keys',[])
                    if not rank.get('automatic_candidate') or keys in blocked or not keys or keys[0] not in self.effects.keys('move'):continue
                    if (pc.get('context',{}).get('no_effect_moves') or {}).get(keys[0],0)>=2:continue
                    score=.55+.2*max(-1,min(1,rank.get('priority',0)))-policy.routes.penalty(state,keys[0])
                    offer(self._decision(base,keys,'movement_alternative','現在の移動候補を、過去の作用と再訪コストで比較して選ぶ。'),'movement_alternative','explore',score,{'rank':rank,'context':pc.get('context',{})})
            if not state.get('eligible') and not current.get('interface'):
                # Unknown states have short, purpose-labelled probes rather than
                # waiting for the supervisor or sweeping every key combination.
                actions=[[k] for k in self.effects.keys('move')+self.effects.keys('confirm')]
                if not actions:actions=[[k] for k in self.cfg.allowed_keys if k not in ('num3','num5','num7')]
                for keys in actions:
                    n=self.trials.get((current['scope'],tuple(keys)),0)
                    offer(self._decision(base,keys,'unknown_probe','現在の場面で進行を可能にする入力と反応の仮説を短く確認する。'),'unknown_probe','verify_action',.4-.1*min(5,n))
        # Existing measured route/skill is an actual candidate, not a suggestion
        # that a later coverage sweep can silently replace.
        experiment=self.effects.experiment(plan,state)
        experiment_active=bool(experiment and experiment['attempts']<experiment['limit'])
        if experiment_active:
            test=experiment['test']
            relevant=[c for c in candidates if (test=='face_then_attack' and c['source']=='combat') or
                (test=='movement_response' and c['decision'].keys[0] in self.effects.keys('move')) or
                (test=='door_response' and c['decision'].keys==[self._key('confirm')] and current.get('region')=='outside') or
                (test=='item_effect' and c['source'] in ('inventory_inspection','interface'))]
            if relevant:
                for c in relevant:c['score']+=.25
            else:experiment_active=False
        preferred=plan['skill']
        pos=state.get('minimap',{}).get('player_cell')
        for c in candidates:
            keys=c['decision'].keys
            if c['skill']==preferred:c['score']+=.15
            if c['decision'].keys in blocked:c['score']-=1.
            c['estimate']=self.effects.estimate(state,c['decision'].keys)
            if self.cycle:
                adjustment,c['rule_evidence']=self.cycle.adjustment(state,c['decision'].keys);c['score']+=adjustment
            # Recent empirical no-effect changes relative priority; no positive
            # reward threshold is required to use the least bad known option.
            if c['estimate']['samples']>=2:c['score']+=max(-.3,min(.3,c['estimate']['mean_observed_value']))
            if plan.get('strategy')=='avoid_repetition' and c['estimate']['samples']>=2 and c['estimate']['no_effect_rate'] is not None:c['score']-=.4*c['estimate']['no_effect_rate']
            cycle=state.get('floor_exploration',{}).get('cycle_status',{})
            if cycle.get('detected') and c['source'] in ('policy','movement_alternative','targeted_probe') and pos:
                delta={'w':(0,-1),'s':(0,1),'a':(-1,0),'d':(1,0)}.get(keys[0]) if len(keys)==1 else None
                if delta and [pos[0]+delta[0],pos[1]+delta[1]] in cycle.get('positions',[]):c['score']-=1.5
            if plan.get('strategy')=='gather_information' and c['estimate']['samples']<2:c['score']+=.1
        # Purposeful exploration compares legal single moves; never forces a
        # menu, facing or diagonal sweep just to complete key coverage.
        fraction=max(plan['exploration_fraction'],.25) if plan.get('strategy')=='compare_alternatives' else plan['exploration_fraction'];period=max(2,round(1/fraction)) if fraction else None
        if period and self.sequence%period==0 and state.get('eligible') and not state['minimap'].get('enemies') and not interface:
            for key in self.effects.keys('move'):
                if [key] in blocked:continue
                estimate=self.effects.estimate(state,[key])
                if estimate['samples']>=2 and estimate['no_effect_rate'] is not None and estimate['no_effect_rate']>=.75:continue
                route_cost=policy.routes.penalty(state,key) if policy and key in ('w','a','s','d') else 0
                if estimate['samples']<2:
                    offer(self._decision(base,[key],'targeted_probe','未解決の移動作用を、現在の通路と実績を確認して1回検証する。'),'targeted_probe','verify_action',.95-route_cost,estimate)
        ineffective={frozenset(k) for k in state.get('floor_exploration',{}).get('ineffective_actions_here',[])}
        candidates=[c for c in candidates if c['source'] not in ('policy','movement_alternative','targeted_probe','floor_navigation') or frozenset(c['decision'].keys) not in ineffective or self.authorized_recheck(state,c)]
        if not candidates:
            hp=state.get('metrics',{}).get('hp',{});enemies=state.get('minimap',{}).get('enemies',[])
            defensive=bool(state.get('eligible') and not current.get('interface') and hp.get('values') and not hp.get('retained') and hp['values'][0]<hp['values'][1] and enemies and min(max(map(abs,e)) for e in enemies)>1 and self._key('step'))
            key=self._key('step') if defensive else self._key('confirm') or self.cfg.allowed_keys[0]
            source='defensive_turn' if defensive else 'bounded_probe'
            reason='確認済みの退路・有効な回復品を選べず、敵へ接近せず足踏みを1回入力する。敵の手番も進むため、被害と接近を再観測する。安全や回復成功は未確認。' if defensive else '進行可能な入力仮説を短く試して現在の反応を取得する。'
            offer(self._decision(base,[key],source,reason),source,'recover' if defensive else 'verify_action',0.)
        chosen=max(candidates,key=lambda c:(c['priority'],c['score']));d=chosen['decision'];source=chosen['source']
        self.trials[current['scope'],tuple(d.keys)]=self.trials.get((current['scope'],tuple(d.keys)),0)+1
        while len(self.trials)>256:self.trials.pop(next(iter(self.trials)))
        if source=='inventory_inspection' and chosen['skill']=='recover':
            self.recovery_request={'until':current['step']+20,'control':current['control_revision'],'guidance':current['operator_guidance'].get('revision',0),'floor':current['floor']}
        if source=='inventory_inspection':
            self.inspections[inspection_scope]=current['step'];self.last_inspection_step=current['step'];self.inspected_revision=state.get('retained_state',{}).get('revision');self.inspection_until=current['step']+8
        while len(self.inspections)>64:self.inspections.pop(next(iter(self.inspections)))
        if source=='combat':combat_trial=chosen['detail']['trial'];pc={'source':'combat_experiment','context':policy.context(state) if policy else {}}
        elif source=='floor_navigation':
            context=policy.context(state) if policy else {}
            pc={'source':'floor_navigation','exploration_route':chosen['detail']['route'],'context':context,
                'selected':next((v for v in context.get('ranking',[]) if v.get('keys')==d.keys),{}),'selection_basis':'floor_task_route'}
        elif source=='policy':pc=chosen['detail']['choice']
        elif source=='movement_alternative':pc={'source':'learned_policy','selected':chosen['detail']['rank'],'context':chosen['detail']['context'],'selection_basis':'operational_relative_comparison'}
        else:pc={'source':'operational_'+source}
        if experiment_active and chosen not in relevant:experiment_active=False
        if pc.get('source')!='learned_outcome_prediction' and prediction_context.get('selected'):
            prediction_context['not_adopted']=prediction_context.pop('selected')
        operation={'recovery_policy':self.recovery_policy.context(state),'authority':'operational_control','plan_id':plan['id'],'objective':plan['objective'],'skill':chosen['skill'],'selected_source':source,
          'floor_exploration':copy.deepcopy(state.get('floor_exploration',{}).get('goal',{})),'preferred_skill':plan['skill'],'strategy':plan.get('strategy'),'navigation':plan.get('navigation'),'selected_priority':chosen['priority'],'selected_score':round(chosen['score'],4),'candidate_count':len(candidates),
          'healing_check':copy.deepcopy(chosen['detail']) if source=='natural_recovery' else None,'expected_signal':'hp_gain' if source=='natural_recovery' else plan['expected_signal'],'experiment':experiment if experiment_active else None,
          'rule_evidence':chosen.get('rule_evidence'),'prediction':chosen.get('estimate',self.effects.estimate(state,d.keys)),
          'candidates':[{'source':c['source'],'keys':c['decision'].keys,'score':round(c['score'],4),'priority':c['priority'],'skill':c['skill']} for c in candidates][:12],
          'execution_stage':d.execution_stage or source,'selected_keys':list(d.keys),'scope':current['scope'],'selection_rule':'現在の条件を満たす候補を一度だけ比較。後段の総当たりで上書きしない。'}
        self.last_selection=operation
        return d,pc,combat_trial,prediction_context,operation
    def feedback(self,row):
        self.strength_growth.feedback(row)
        if row.get('policy_state') and row.get('policy_after'):self.recovery_policy.observe_result(row,next((k for k,v in self.effects.bindings.items() if '足踏み' in v),None))
        if self.equipment_preparation.active:self.equipment_preparation.feedback(row,self._key('confirm'))
        e=(row.get('action_effect_learning') or {}).get('effects',{});op=row.get('operation',{})
        values={'position':e.get('moved') is True,'damage_dealt':bool(e.get('new_hit_message')),'enemy_defeated':bool(e.get('new_defeat_message')),
          'hp_gain':e.get('hp_gain_net',0)>0,'food_gain':e.get('food_gain_net',0)>0,'interface_change':e.get('text_changed') is True,
          'item_change':bool((row.get('world_learning') or {}).get('item_effect'))}
        prepared=(row.get('combat_evaluation') or {}).get('status')=='awaiting_attack_trial'
        # Interface information may be useful without being dungeon progress.
        policy=row.get('policy_learning') or {};net=policy.get('net_signals') or {}
        nav=row.get('mapless_navigation') or {}
        new_connection=bool((row.get('floor_navigation_outcome') or {}).get('new_connection'))
        assumed_connection=bool((row.get('movement_result') or {}).get('success') is True and not nav.get('pose_uncertain') and (nav.get('last') or {}).get('new_connection'))
        floor_progress=bool(net.get('floor'));coverage_progress=bool(net.get('coverage'))
        route_progress=bool(op.get('selected_source')=='floor_navigation' and (row.get('floor_navigation_outcome') or {}).get('moved') and (row.get('map_route_hint') or (row.get('policy_state') or {}).get('map_route_hint') or {}).get('distance',0)>0)
        progress=route_progress or new_connection or assumed_connection or floor_progress or coverage_progress or any(values[k] for k in ('damage_dealt','enemy_defeated','hp_gain','food_gain','item_change'))

        actual=row.get('observed_keys',[]);applied=actual==op.get('selected_keys') and row.get('executed') and (row.get('input_receipt') or {}).get('os_submission')=='accepted' and row.get('turn_kind')!='aborted'
        if self.menu_followup and not self.menu_followup.get('accepted'):
            if applied and actual==[self._key('confirm')] and op.get('execution_stage')=='requested_item_action':self.menu_followup['accepted']=True
            else:self.menu_followup=None
        pending=self.review_pending;self.review_pending=None
        if pending and self.inventory_review and applied and actual==[pending['key']] and op.get('selected_source')=='interface':
            review=self.inventory_review;review['moves']+=1
            if pending['action']=='hide_panel':review.setdefault('hidden_panels',set()).add(pending['token'])
            if pending['action']=='page' and self.inventory_audit.active:self.inventory_audit.active['page_submitted']=True
            if pending['action']=='description':
                token=pending['token'];review['attempts'][token]=review['attempts'].get(token,0)+1
                if (row.get('item_description_observation') or {}).get('status')=='description_text_observed' or review['attempts'][token]>=2:review['seen'].add(token)
        healing=op.get('healing_check')
        if healing and applied and op.get('selected_source')=='natural_recovery':
            self.healing_trials[healing['trial']]=self.healing_trials.get(healing['trial'],0)+1
            hp=(row.get('policy_after') or {}).get('metrics',{}).get('hp',{})
            value=hp.get('values');old=healing['hp_before']
            if value and not hp.get('retained') and value[1]==old[1] and value[0]<old[0]:self.healing_unsafe[healing['scope']]=True
            for cache in (self.healing_trials,self.healing_unsafe):
                while len(cache)>64:cache.pop(next(iter(cache)))
        evaluation={'progress':bool(progress and applied),'comparable':bool(e.get('comparable') and applied and not prepared),
          'expected_signal_observed':bool(values.get(op.get('expected_signal')) and applied), 'signals':values,'new_connection':new_connection,'route_progress':route_progress,'assumed_connection':assumed_connection,'preparation':prepared,
          'sequence_evaluation':copy.deepcopy(row.get('combat_evaluation')),'scope':'observed_effects_not_goal_success','selection_applied':bool(applied)}
        item=self.item_context
        phase=item.get('phase') if item else None
        if item and phase in ('opening','using'):
            if applied and actual==[self._key('confirm')] and op.get('selected_source')=='interface':
                if item['phase']=='opening':item.update(phase='actions',step=row.get('step',0))
                else:self.item_context=None
            else:self.item_context=None
        elif item and phase=='actions':
            if not (item.pop('_navigation',False) and applied and op.get('selected_source')=='interface'):self.item_context=None
        row['operation_evaluation']=evaluation
        row['operational_experiment']=self.effects.record_experiment(op.get('experiment'),evaluation,row) if applied else None
        return evaluation
