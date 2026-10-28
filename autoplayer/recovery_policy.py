"""Remember failed recovery checks and compare fresh, observed movement options."""
import copy

def fresh(state,key):
    metric=state.get('metrics',{}).get(key,{})
    return metric.get('values') if not metric.get('retained') else None

class RecoveryPolicy:
    def __init__(self):self.checks={};self.idle={};self.floor=None
    def reset(self):self.checks.clear();self.idle.clear();self.floor=None
    @staticmethod
    def need(state):
        food=fresh(state,'fullness');hp=fresh(state,'hp')
        return 'food' if food and food[0]<=15 else 'hp' if hp and hp[0]<hp[1] else None
    @staticmethod
    def evidence(state):
        retained=state.get('retained_state',{})
        reasons=set(retained.get('refresh_reasons',[]))&{'possible_item_pickup_or_full_inventory','inventory_or_equipment_message','floor_item_needs_check','item_use_or_equipment_effect'}
        return [retained.get('inventory_token'),retained.get('revision') if reasons else None]
    def blocked(self,state):
        need=self.need(state);record=self.checks.get(need)
        return bool(record and record['evidence']==self.evidence(state))
    def record_unavailable(self,native,state,step,reason=None):
        need=self.need(state)
        if not need:return
        rows=native.get('_current_inventory_rows',[])
        names={r['display_name'] for r in rows}
        supported={'パン','大きいパン','大きなパン'} if need=='food' else {'薬草','弟切草'}
        occupancy=(state.get('retained_state',{}).get('last_observed_inventory') or {}).get('occupancy') or {}
        complete=occupancy.get('count') is not None and occupancy['count']==len(rows)
        status=reason or ('no_known_recovery_item' if complete and not names&supported else 'selection_or_coverage_unconfirmed')
        self.checks[need]={'need':need,'status':status,'evidence':self.evidence(state),'checked_step':step,'observed_names':sorted(names),'coverage':'count_matches_visible_rows' if complete else 'incomplete',
          'rule':'同じ所持品の自動回復確認は反復しない。未知品の効果を否定しない。所持品更新・取得/使用の新しい証拠で再確認し、目的を持った別の調査は許可する。'}
    @staticmethod
    def cells(state):
        m=state.get('minimap',{});pos=m.get('player_cell')
        if m.get('status')!='observed' or m.get('retained') or not pos:return None,[]
        return tuple(pos),[(pos[0]+x,pos[1]+y) for x,y in m.get('enemies',[])]
    def observe_result(self,row,step_key):
        before=row.get('policy_state',{});after=row.get('policy_after',{})
        if not row.get('executed') or row.get('dry_run') or row.get('turn_kind')=='aborted' or row.get('input_receipt',{}).get('os_submission')!='accepted':return
        p,old=self.cells(before);q,new=self.cells(after)
        floor=fresh(after,'floor')
        if not p or not q or not floor or floor!=fresh(before,'floor') or before.get('menu_evidence') or after.get('menu_evidence'):return
        effects=(row.get('action_effect_learning') or {}).get('effects',{})
        turn=effects.get('moved') is True or effects.get('new_hit_message') or row.get('observed_keys')==[step_key]
        if self.floor!=floor:self.idle={};self.floor=copy.deepcopy(floor)
        hp0=fresh(before,'hp');hp1=fresh(after,'hp')
        damaged=bool(hp0 and hp1 and hp1[0]<hp0[0])
        if damaged:self.idle={}
        if not turn:return
        self.idle={cell:self.idle.get(cell,0)+1 if cell in old and not damaged else 0 for cell in new}
    def context(self,state):
        pos,enemies=self.cells(state);hp=fresh(state,'hp');floor=fresh(state,'floor')
        passive=[list(cell) for cell in enemies if self.floor==floor and self.idle.get(cell,0)>=3 and max(abs(cell[0]-pos[0]),abs(cell[1]-pos[1]))>1]
        return {'recovery_check':copy.deepcopy(self.checks.get(self.need(state))), 'automatic_recheck_blocked':self.blocked(state),
          'avoid_initiating_combat':not hp or hp[0]/max(1,hp[1])<.75,'stationary_enemy_cells':passive,
          'retreat_rule':'等速の敵から距離を増やすだけの移動を自動退避として優先しない。階段への観測済み経路など具体的な目的、状態効果・速度差・接触数減少の証拠を必要とする。低HPだけでは逃げ続けない。',
          'rule':'HPが未確認または75%未満なら遠い敵へ自分から接近しない。静止候補は受理したターン消費行動の前後で3回以上同位置を観測したマークであり、睡眠や安全の確定ではない。隣接敵への対処は維持する。'}
    @staticmethod
    def delta(keys,bindings):
        directions={'上':(0,-1),'左':(-1,0),'下':(0,1),'右':(1,0),'up':(0,-1),'left':(-1,0),'down':(0,1),'right':(1,0)}
        values=[]
        for key in keys:
            label=bindings.get(key,'');delta=next((d for name,d in directions.items() if label.startswith(name)),None)
            if delta is None:return None
            values.append(delta)
        delta=tuple(sum(v[i] for v in values) for i in (0,1))
        return delta if values and max(map(abs,delta))==1 else None
    def allows_move(self,state,keys,bindings):
        pos,enemies=self.cells(state);hp=fresh(state,'hp');delta=self.delta(keys,bindings)
        if not pos or not enemies or hp and hp[0]/max(1,hp[1])>=.75 or not delta or not state.get('eligible'):return True
        distance=min(max(abs(e[0]-pos[0]),abs(e[1]-pos[1])) for e in enemies)
        if distance<=1:return True # Defensive repositioning/combat is evaluated separately.
        target=(pos[0]+delta[0],pos[1]+delta[1])
        return min(max(abs(e[0]-target[0]),abs(e[1]-target[1])) for e in enemies)>=distance
    def retreat(self,state,bindings,blocked):
        pos,enemies=self.cells(state);hp=fresh(state,'hp')
        if not pos or not enemies or not hp or hp[0]/max(1,hp[1])>=.5 or not state.get('eligible'):return None
        m=state['minimap'];known={tuple(p) for p in m.get('accumulated_cells',[])}|{(pos[0]+x,pos[1]+y) for x,y in m.get('known_cells',[])}
        # Equal-speed pursuers normally restore the same distance in their turn.
        # Low HP plus a farther square is not, by itself, a useful escape plan.
        hint=state.get('map_route_hint') or {};keys=hint.get('keys') or [hint.get('key')]
        stairs={(pos[0]+x,pos[1]+y) for x,y in m.get('stairs',[])}|{tuple(p) for p in state.get('floor_exploration',{}).get('stairs_cells',[])}
        if hint.get('target_kind')!='stairs' or tuple(hint.get('target_cell') or ()) not in stairs or len(keys)!=1:return None
        current=min(max(abs(e[0]-pos[0]),abs(e[1]-pos[1])) for e in enemies);options=[]
        for key in keys:
            delta=self.delta([key],bindings)
            if not delta or [key] in blocked or [key] in state.get('floor_exploration',{}).get('ineffective_actions_here',[]):continue
            target=(pos[0]+delta[0],pos[1]+delta[1])
            distance=min(max(abs(e[0]-target[0]),abs(e[1]-target[1])) for e in enemies)
            if target in known and distance>current:options.append((distance,key))
        return max(options)[1] if options else None
