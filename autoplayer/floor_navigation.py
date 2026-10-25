"""Floor-scoped exploration tasks: observed floor, actual visits, bounded probes.

Completed exploration tasks remain traversable. Unknown cells are not walls.
"""
from collections import Counter,deque
import copy

MOVES={'w':(0,-1),'d':(1,0),'s':(0,1),'a':(-1,0)}
ROUTE_MOVES={**MOVES,'w+a':(-1,-1),'w+d':(1,-1),'s+a':(-1,1),'s+d':(1,1)}
MISSIONS=('auto','find_stairs','reach_stairs','collect_visible_items','search_objective','return_with_objective')

class FloorNavigation:
    def __init__(self,confirm_key='num1'):
        self.confirm_key=confirm_key;self.reset()
    def reset(self):
        self.floor=None;self.space=None;self.known=set();self.visited=Counter();self.failures=Counter();self.failure_tick={};self.neighbors={}
        self.stairs=set();self.items=set();self.enemies=set();self.position=None;self.tick=0;self.last_direction=None;self.mission='auto';self.plan_id=None
        self.last_hint=None;self.goal={'phase':'awaiting_current_map','reason':'現在の地図と階を待つ'};self.stair_trials=Counter();self.item_attempts=Counter();self.inventory_token=None
    def _valid(self,state):
        m=state.get('minimap',{});f=state.get('metrics',{}).get('floor',{})
        confirmation=state.get('floor_confirmation',{})
        return bool(state.get('eligible') and m.get('status')=='observed' and not m.get('retained') and f.get('values') and not f.get('retained') and m.get('player_cell') is not None and (m.get('accumulated_cells') or m.get('known_cells')) and confirmation.get('status') not in ('pending','unreadable'))
    def observe(self,state):
        if not self._valid(state):return False
        m=state['minimap'];floor=tuple(state['metrics']['floor']['values']);space=(floor,tuple(m.get('grid_size',[])),m.get('cell_size'))
        if space!=self.space:
            self.reset();self.space=space;self.floor=floor
        self.tick+=1;pos=tuple(m['player_cell']);dims=m.get('grid_size')
        def valid(p):return len(p)==2 and all(type(v) is int for v in p) and (0<=p[0]<dims[0] and 0<=p[1]<dims[1] if dims and len(dims)==2 else max(map(abs,p))<4096)
        observed={tuple(p) for p in m.get('accumulated_cells',[]) if valid(p)}
        observed.update((pos[0]+p[0],pos[1]+p[1]) for p in m.get('known_cells',[]) if valid((pos[0]+p[0],pos[1]+p[1])))
        self.known.update(observed);self.known.add(pos)
        if len(self.known)>4096:self.reset();self.space=space;self.floor=floor;self.known=observed|{pos}
        self.stairs.update((pos[0]+x,pos[1]+y) for x,y in m.get('stairs',[]) if valid((pos[0]+x,pos[1]+y)))
        self.items={(pos[0]+x,pos[1]+y) for x,y in m.get('items',[]) if valid((pos[0]+x,pos[1]+y))}
        self.enemies={(pos[0]+x,pos[1]+y) for x,y in m.get('enemies',[]) if valid((pos[0]+x,pos[1]+y))}
        self.known.update(self.stairs|self.items)
        for cell in self.known:
            signature=tuple(self._add(cell,d) in self.known for d in MOVES.values())
            old=self.neighbors.get(cell)
            if old is not None and old!=signature:
                for key in ROUTE_MOVES:self.failures.pop((cell,key),None)
            self.neighbors[cell]=signature
        if pos!=self.position:self.visited[pos]+=1
        elif not self.visited[pos]:self.visited[pos]=1
        self.position=pos;self.dims=dims
        return True
    @staticmethod
    def _add(a,b):return (a[0]+b[0],a[1]+b[1])
    def degree(self,cell):return sum(self._add(cell,d) in self.known for d in MOVES.values())
    def unknown_keys(self,cell):
        out=[]
        for key,d in MOVES.items():
            p=self._add(cell,d)
            if self.dims and not (0<=p[0]<self.dims[0] and 0<=p[1]<self.dims[1]):continue
            if p not in self.known and self.failures[cell,key]==0:out.append(key)
        return out
    def primary(self):
        leaves={c for c in self.known if self.degree(c)<=1 and (not self.visited[c] or self.unknown_keys(c)) and c not in self.stairs and c not in self.items}
        if leaves:return leaves
        return {c for c in self.known if self.degree(c)==2 and not self.visited[c] and self.unknown_keys(c)}
    def retired(self):
        # Prune a visited dead branch for exploration only. Its floor cells stay
        # in the graph and may be used for another objective or necessary return.
        retired=set();degree={c:self.degree(c) for c in self.known};queue=deque(c for c in self.known if degree[c]<=1 and self.visited[c] and not self.unknown_keys(c) and c not in self.stairs|self.items)
        while queue:
            c=queue.popleft()
            if c in retired:continue
            retired.add(c)
            for d in MOVES.values():
                p=self._add(c,d)
                if p not in degree or p in retired:continue
                degree[p]-=1
                if degree[p]<=1 and self.visited[p] and self.degree(p)<=2 and p not in self.stairs|self.items:queue.append(p)
        return retired
    def record(self,before,after,row,natural=None):
        if not row.get('executed') or row.get('dry_run') or row.get('turn_kind')=='aborted' or (row.get('input_receipt') or {}).get('os_submission')!='accepted' or row.get('kind') in ('compatibility','separation') or str(row.get('event','')).startswith('diagnostic'):return
        if not self._valid(before) or not self._valid(after):return
        if before.get('observation_mode','normal')!=after.get('observation_mode','normal') or (natural or {}).get('position') or (natural or {}).get('floor'):return
        if before['metrics']['floor']['values']!=after['metrics']['floor']['values']:return
        a=tuple(before['minimap']['player_cell']);b=tuple(after['minimap']['player_cell']);keys=row.get('observed_keys',[])
        arrival_visits=self.visited.get(b,0)
        self.observe(after)
        if keys==[self.confirm_key] and a==b and a in self.stairs:self.stair_trials[a]+=1
        key=next((k for k in ROUTE_MOVES if len(keys)==len(k.split('+')) and set(keys)==set(k.split('+'))),None)
        if key is None:return
        delta=ROUTE_MOVES[key]
        row['floor_navigation_outcome']={'expected_delta':list(delta),'observed_delta':[b[0]-a[0],b[1]-a[1]],'moved':b==self._add(a,delta),'arrival_visits_before':arrival_visits}
        if b==self._add(a,delta):
            if [b[0]-a[0],b[1]-a[1]] in [list(p) for p in before['minimap'].get('items',[])]:self.item_attempts[b]+=1
            self.last_direction=key;self.failures.pop((a,key),None)
        elif a==b and not any(max(abs(x),abs(y))<=1 for x,y in before['minimap'].get('enemies',[])):
            self.failures[a,key]+=1;self.failure_tick[a,key]=self.tick
    def inventory_updated(self,token):
        if token and self.inventory_token and token!=self.inventory_token:self.item_attempts.clear()
        if token:self.inventory_token=token
    def _path(self,targets):
        if not targets:return None
        start=self.position;queue=deque([(start,[])]);seen={start}
        while queue:
            node,path=queue.popleft()
            if node in targets:return node,path
            for key,d in ROUTE_MOVES.items():
                if '+' in key:
                    sides=[self._add(node,MOVES[k]) for k in key.split('+')]
                    if any(p not in self.known or p in self.enemies for p in sides):continue
                nxt=self._add(node,d)
                if nxt not in self.known or nxt in seen or nxt in self.enemies:continue
                # Two failures suppress a route edge temporarily, never prove a wall.
                if self.failures[node,key]>=2 and self.tick-self.failure_tick.get((node,key),self.tick)<16:continue
                seen.add(nxt);queue.append((nxt,path+[key]))
        return None
    def hint(self,state,preference='current_goal',mission='auto',plan_id=None):
        if not self.observe(state):return None
        if plan_id and plan_id!=self.plan_id:
            self.plan_id=plan_id
            if mission in MISSIONS:self.mission=mission
        selected_mission=self.mission
        if selected_mission=='auto':
            selected_mission='collect_visible_items' if preference=='items' else 'reach_stairs' if preference=='stairs' and self.stairs else 'find_stairs'
        primary=self.primary();secondary={c for c in self.known if self.unknown_keys(c)}
        target_kind='frontier';targets=primary
        if selected_mission in ('reach_stairs','return_with_objective') and self.stairs:targets=self.stairs;target_kind='stairs'
        elif selected_mission in ('collect_visible_items','search_objective') and {p for p in self.items if not self.item_attempts[p] and p!=self.position}:targets={p for p in self.items if not self.item_attempts[p] and p!=self.position};target_kind='items'
        elif not primary and self.stairs and selected_mission!='search_objective':targets=self.stairs;target_kind='stairs'
        elif not primary and secondary:targets=secondary;target_kind='boundary_recheck'
        # Visible threats first, then visible items. Only route on observed floor;
        # enemy markers themselves are occupied, not walkable destinations.
        enemy_goals={self._add(e,d) for e in self.enemies for d in MOVES.values()}&self.known-self.enemies
        item_goals={p for p in self.items if not self.item_attempts[p] and p!=self.position}
        for kind,options in (('enemy',enemy_goals),('items',item_goals)):
            if options and self._path(options) is not None:
                targets=options;target_kind=kind;break
        path=self._path(targets);self.last_hint=None
        phase='handle_visible_enemy' if target_kind=='enemy' else 'return_to_stairs' if target_kind=='stairs' and selected_mission=='return_with_objective' else 'reach_stairs' if target_kind=='stairs' else 'collect_visible_items' if target_kind=='items' else 'explore_frontier'
        reason='見えている敵に対処するため既知床上の隣接位置へ向かう' if target_kind=='enemy' else '見えている品の位置へ向かい取得結果を確認する' if target_kind=='items' else '現在の観測から未確認の探索先へ進む'
        if not targets:
            phase='verify_objective' if selected_mission=='search_objective' else 'review_exhausted_floor'
            reason='現在の観測範囲に未処理の探索先がない。所持品・目標・地図認識を再検討する'
        elif path is None:phase='route_unavailable';reason='既知の床で到達可能な経路がない。接触や無移動の原因を再検討する'
        else:
            target,steps=path
            if not steps:
                if target_kind=='stairs':
                    if self.stair_trials[target]<2:
                        self.last_hint={'key':self.confirm_key,'mode':'stair_interaction_trial','target_cell':list(target),'distance':0,'target_kind':target_kind,'route_cells':[list(target)]}
                    else:phase='verify_stair_action';reason='階段位置での操作結果が未確認。盲目的に反復せず現在画面と操作方法を再検討する'
                elif target_kind in ('frontier','boundary_recheck'):
                    probes=self.unknown_keys(target)
                    if probes:
                        key=self.last_direction if self.last_direction in probes else probes[0]
                        self.last_hint={'key':key,'mode':'frontier_probe','target_cell':list(target),'distance':0,'target_kind':target_kind,'route_cells':[list(target)]}
            else:
                route=[list(self.position)];node=self.position
                for key in steps:node=self._add(node,ROUTE_MOVES[key]);route.append(list(node))
                backtrack=bool(self.visited[self._add(self.position,ROUTE_MOVES[steps[0]])])
                self.last_hint={'key':steps[0],'keys':steps[0].split('+'),'diagonal':len(steps[0].split('+'))==2,'mode':'backtrack_to_frontier' if backtrack and target_kind!='stairs' else 'observed_grid_route',
                    'target_cell':list(target),'distance':len(steps),'target_kind':target_kind,'route_cells':route[:1728]}
                if backtrack:reason='踏破済みの床を必要な帰路として使い、別の探索先または現在の目的へ向かう'
        self.goal={'priority_order':['visible_enemies','visible_items','unexplored','status_inventory_review'],'item_recheck_needed':bool(self.items and (self.position in self.items or any(self.item_attempts[p] for p in self.items))), 'mission':selected_mission,'phase':phase,'reason':reason,'target_cell':self.last_hint.get('target_cell') if self.last_hint else None,
          'needs_review':phase in ('route_unavailable','review_exhausted_floor','verify_stair_action','verify_objective'),
          'stairs_seen_type_unknown':bool(self.stairs),'instruction':'これは階内の局所目標。階段の上下・箱の所持・帰還成功を推定で確定しない。'}
        if self.last_hint:self.last_hint.update(basis='floor_scoped_visits_and_bounded_frontiers',reason=reason)
        return copy.deepcopy(self.last_hint)
    def context(self):
        if self.position is None:return {'status':'unavailable','goal':copy.deepcopy(self.goal)}
        primary=self.primary();retired=self.retired();interior={c for c in self.known if self.degree(c)==4}
        return {'status':'observed_floor','floor':list(self.floor),'position':list(self.position),'goal':copy.deepcopy(self.goal),
          'known_count':len(self.known),'visited_count':len(self.visited),'primary_frontier_count':len(primary),'exhausted_branch_count':len(retired),
          'visited_cells':[list(p) for p in self.visited],'exploration_unnecessary_cells':[list(p) for p in interior|retired],
          'exhausted_branch_cells':[list(p) for p in retired],'frontier_cells':[list(p) for p in primary],
          'stairs_cells':[list(p) for p in self.stairs],'route_cells':copy.deepcopy((self.last_hint or {}).get('route_cells',[])),
          'note':'無移動の方向は今回の探索試行を一旦完了するだけで永久の壁とは確定しない。探索不要は現在の探索先の優先度。通行禁止や永久の壁ではない。新しい床・目標・階で見直す。'}
