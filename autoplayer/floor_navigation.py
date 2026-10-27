"""Floor-scoped exploration tasks: observed floor, measured connections, bounded probes.

Completed exploration tasks remain traversable. Unknown cells are not walls.
"""
from collections import Counter,deque
import copy

MOVES={'w':(0,-1),'d':(1,0),'s':(0,1),'a':(-1,0)}
ROUTE_MOVES={**MOVES,'w+a':(-1,-1),'w+d':(1,-1),'s+a':(-1,1),'s+d':(1,1)}
MISSIONS=('auto','find_stairs','reach_stairs','collect_visible_items','search_objective','return_with_objective')

def room_diagonal_allowed(origin,destination,floor):
    """A shared observed 3x3 room patch; corridor bends alone never suffice."""
    x,y=origin
    return any(ax<=destination[0]<=ax+2 and ay<=destination[1]<=ay+2
        and all((xx,yy) in floor for xx in range(ax,ax+3) for yy in range(ay,ay+3))
        for ax in range(x-2,x+1) for ay in range(y-2,y+1))

class FloorNavigation:
    def __init__(self,confirm_key='num1'):
        self.confirm_key=confirm_key;self.reset()
    def reset(self):
        from .item_tasks import ItemTasks
        self.item_tasks=ItemTasks()
        self.floor=None;self.space=None;self.known=set();self.visited=Counter();self.failures=Counter();self.neighbors={}
        self.motion_history=deque(maxlen=16);self.last_motion_progress=False;self.crossings=Counter();self.rechecked_edges={};self.stairs=set();self.items=set();self.enemies=set();self.position=None;self.tick=0;self.last_direction=None;self.mission='auto';self.plan_id=None
        self.exploration_task=None;self.frontier_selection={};self.movement_checks={};self.last_hint=None;self.goal={'phase':'awaiting_current_map','reason':'現在の地図と階を待つ'};self.stair_trials=Counter();self.item_attempts=Counter();self.inventory_token=None;self.fresh_floor=set();self.missing_floor=Counter();self.last_map_frame=None;self.positive_floor=Counter()
    def _valid(self,state):
        m=state.get('minimap',{});f=state.get('metrics',{}).get('floor',{})
        confirmation=state.get('floor_confirmation',{})
        return bool(state.get('eligible') and m.get('status')=='observed' and not m.get('retained') and f.get('values') and not f.get('retained') and m.get('player_cell') is not None and (m.get('accumulated_cells') or m.get('known_cells')) and confirmation.get('status') not in ('pending','unreadable'))
    def observe(self,state):
        if not self._valid(state):return False
        m=state['minimap'];floor=tuple(state['metrics']['floor']['values']);space=(floor,tuple(m.get('grid_size',[])),m.get('cell_size'),m.get('map_generation'))
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
        unknown={(pos[0]+x,pos[1]+y) for x,y in m.get('uncertain_marks',[]) if valid((pos[0]+x,pos[1]+y))}
        self.item_tasks.observe(self.items,unknown,{(pos[0]+x,pos[1]+y) for x,y in m.get('stairs',[])},pos,state.get('frame_sha256'),bool(self.enemies))
        self.known.update(self.stairs|self.items|unknown)
        # Accumulation preserves history, including old recognition errors. Two
        # independent current-map frames can dispute cached cells lacking connection evidence,
        # without deleting history or declaring a physical wall.
        fresh={(pos[0]+x,pos[1]+y) for x,y in m.get('known_cells',[])}|{pos}|self.items|self.enemies|set((pos[0]+x,pos[1]+y) for x,y in m.get('stairs',[]))
        frame=state.get('frame_sha256')
        if m.get('known_cells') and frame and frame!=self.last_map_frame:
            previously_disputed=self.disputed()
            for cell in self.known:
                if cell in fresh:
                    self.positive_floor[cell]+=1
                    if self.missing_floor[cell]<2 or self.positive_floor[cell]>=2:self.missing_floor.pop(cell,None)
                else:self.missing_floor[cell]+=1;self.positive_floor.pop(cell,None)
            reopened=previously_disputed-self.disputed()
            for (origin,key) in list(self.failures):
                relevant=[self._add(origin,ROUTE_MOVES[key])]+[self._add(origin,MOVES[k]) for k in key.split('+')]
                if any(c in reopened for c in relevant):self.failures.pop((origin,key),None)
            self.fresh_floor=fresh;self.last_map_frame=frame
        effective_floor=self.known-self.disputed()
        for cell in self.known:
            signature=tuple(self._add(cell,d) in effective_floor for d in MOVES.values())
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
    def unknown_keys(self,cell,floor=None):
        floor=self.known-self.disputed() if floor is None else floor
        out=[]
        for key,d in MOVES.items():
            p=self._add(cell,d)
            if self.dims and not (0<=p[0]<self.dims[0] and 0<=p[1]<self.dims[1]):continue
            if p not in floor and self.failures[cell,key]==0:out.append(key)
        return out
    def proven_cells(self):
        # Successful input-linked connections are geometric evidence, not a
        # reward or a reason to prefer/reject walking over the same floor.
        return {c for edge,count in self.crossings.items() if count>0 for c in edge}|({self.position} if self.position is not None else set())
    def disputed(self):
        proven=self.proven_cells()
        return {c for c,n in self.missing_floor.items() if n>=2 and c not in proven}
    def area_cell(self,cell,floor=None):
        # A filled 2x2 floor patch is area evidence, not a corridor endpoint.
        # In particular a rectangular room corner has degree two as well.
        x,y=cell;floor=self.known-self.disputed() if floor is None else floor
        return any({cell,(x+dx,y),(x,y+dy),(x+dx,y+dy)}<=floor for dx in (-1,1) for dy in (-1,1))
    def primary(self):
        # Narrow observed endpoints with an unresolved outward direction.
        # Walking over a cell neither completes nor creates an exploration task.
        floor=self.known-self.disputed()
        return {c for c in floor-self.stairs-self.items if not self.area_cell(c,floor)
                and sum(self._add(c,d) in floor for d in MOVES.values())<=1 and self.unknown_keys(c,floor)}
    def boundaries(self):
        # Rechecks are bounded corridor hypotheses, never every room perimeter.
        floor=self.known-self.disputed()
        return {c for c in floor-self.stairs-self.items if not self.area_cell(c,floor) and self.unknown_keys(c,floor)}
    def retired(self):
        # Retire only explicitly checked branches; visitation has no effect. Its floor cells stay
        # in the graph and may be used for another objective or necessary return.
        retired=set();degree={c:self.degree(c) for c in self.known};queue=deque(c for c in self.known if degree[c]<=1 and not self.unknown_keys(c) and c not in self.stairs|self.items)
        while queue:
            c=queue.popleft()
            if c in retired:continue
            retired.add(c)
            for d in MOVES.values():
                p=self._add(c,d)
                if p not in degree or p in retired:continue
                degree[p]-=1
                if degree[p]<=1 and self.degree(p)<=2 and not self.unknown_keys(p) and p not in self.stairs|self.items:queue.append(p)
        return retired
    @staticmethod
    def edge_pair(a,b):return tuple(sorted((tuple(a),tuple(b))))
    def recheck_hint(self):
        # Past traversal is conflicting evidence, not proof of present passage.
        # Only one accepted recheck per traversal evidence version, never a timer.
        if any(max(abs(x),abs(y))<=1 for x,y in self.enemies):return None
        choices=[]
        for (origin,key),failures in sorted(self.failures.items()):
            if failures<2 or key not in MOVES:continue
            dest=self._add(origin,MOVES[key]);evidence=self.crossings[self.edge_pair(origin,dest)]
            if not evidence or self.rechecked_edges.get((origin,key))==evidence:continue
            if origin not in self.fresh_floor or dest not in self.fresh_floor or origin in self.disputed() or dest in self.disputed():continue
            path=self._path({origin})
            if path is not None:choices.append((len(path[1]),origin,key,dest,evidence,path[1]))
        if not choices:return None
        _,origin,key,dest,evidence,steps=min(choices)
        route=[list(self.position)];node=self.position
        for move in steps:node=self._add(node,ROUTE_MOVES[move]);route.append(list(node))
        return {'key':steps[0] if steps else key,'keys':(steps[0] if steps else key).split('+'),
          'mode':'approach_edge_recheck' if steps else 'traversed_edge_recheck','target_kind':'route_recheck',
          'target_cell':list(origin) if steps else list(dest),'route_cells':route,'distance':len(steps),
          'edge_origin':list(origin),'edge_target':list(dest),'edge_key':key,'traversal_evidence':evidence}
    def record(self,before,after,row,natural=None):
        if not row.get('executed') or row.get('dry_run') or row.get('turn_kind')=='aborted' or (row.get('input_receipt') or {}).get('os_submission')!='accepted' or row.get('kind') in ('compatibility','separation') or str(row.get('event','')).startswith('diagnostic'):return
        hint=before.get('map_route_hint') or {}
        if hint.get('mode')=='traversed_edge_recheck' and self._valid(before) and before['metrics']['floor']['values']==list(self.floor or ()) and before['minimap']['player_cell']==hint.get('edge_origin') and row.get('observed_keys')==[hint.get('edge_key')]:
            edge=(tuple(hint['edge_origin']),hint['edge_key'])
            evidence=self.crossings[self.edge_pair(hint['edge_origin'],hint['edge_target'])]
            if evidence and evidence==hint.get('traversal_evidence'):
                self.rechecked_edges[edge]=evidence
                row['route_recheck_outcome']={'status':'sent_outcome_unverified','origin':hint['edge_origin'],'target':hint['edge_target'],'traversal_evidence':evidence}
        if not self._valid(before) or not self._valid(after):return
        if before.get('observation_mode','normal')!=after.get('observation_mode','normal') or (natural or {}).get('position') or (natural or {}).get('floor'):return
        if before['metrics']['floor']['values']!=after['metrics']['floor']['values']:return
        a=tuple(before['minimap']['player_cell']);b=tuple(after['minimap']['player_cell']);keys=row.get('observed_keys',[])
        arrival_visits=self.visited.get(b,0);known_before=len(self.known)
        self.observe(after)
        if keys==[self.confirm_key] and a==b and a in self.stairs:self.stair_trials[a]+=1
        key=next((k for k in ROUTE_MOVES if len(keys)==len(k.split('+')) and set(keys)==set(k.split('+'))),None)
        if key is None:return
        delta=ROUTE_MOVES[key]
        row['floor_navigation_outcome']={'expected_delta':list(delta),'observed_delta':[b[0]-a[0],b[1]-a[1]],'moved':b==self._add(a,delta),'arrival_visits_before':arrival_visits}
        if 'route_recheck_outcome' in row:row['route_recheck_outcome']['status']='traversal_observed' if b==self._add(a,delta) else 'not_traversed' if a==b else 'different_displacement'
        if b==self._add(a,delta):
            first_connection=not self.crossings[self.edge_pair(a,b)]
            row['floor_navigation_outcome']['new_connection']=first_connection
            self.last_motion_progress=first_connection or len(self.known)>known_before
            if self.last_motion_progress or self.motion_history and self.motion_history[-1]!=a:self.motion_history.clear()
            if not self.motion_history:self.motion_history.append(a)
            self.motion_history.append(b)
            self.crossings[self.edge_pair(a,b)]+=1
            if [b[0]-a[0],b[1]-a[1]] in [list(p) for p in before['minimap'].get('items',[])]:self.item_attempts[b]+=1
            if b in self.item_tasks.tasks:self.item_tasks.arrive(b,row.get('step'))
            self.last_direction=key;self.failures.pop((a,key),None);self.movement_checks.pop((a,key),None)
        elif a==b and not any(max(abs(x),abs(y))<=1 for x,y in before['minimap'].get('enemies',[])):
            outcome=row['floor_navigation_outcome']
            if any(s.get('menu_evidence') or s.get('white_frame_evidence',{}).get('visible') for s in (before,after)):
                outcome.update(status='interface_confounded',route_failure_counted=False)
            elif self._add(a,delta) in self.known and row.get('hold_ms',120)<120:
                self.movement_checks[a,key]={'minimum_hold_ms':120,'reason':'既知床への短い入力はゲーム受理・位置観測が未確認。経路を保留する前に長めの単発入力で確認'}
                outcome.update(status='short_input_unconfirmed',route_failure_counted=False,required_hold_ms=120)
            else:
                self.failures[a,key]+=1
                outcome.update(status='no_displacement_observed',route_failure_counted=True,
                    interpretation='観測上の無移動。物理障害物やゲーム側の入力受理を証明しない')
        elif a!=b:
            row['floor_navigation_outcome'].update(status='different_displacement',route_failure_counted=False)
    def cycle_status(self):
        history=list(self.motion_history);period=None;recent=history[-6:]
        for size in range(2,6):
            candidate=history[-size*3:]
            if len(candidate)==size*3 and len(set(candidate))>1 and all(candidate[i]==candidate[i%size] for i in range(len(candidate))):
                period=size;recent=candidate;break
        return {'detected':period is not None,'period':period,'positions':[list(p) for p in recent],
            'basis':'受理済みの実移動のみ。新しい接続や床の観測で履歴を更新。帰路そのものは通行禁止にしない。'}
    def inventory_updated(self,token,occupancy=None,fresh=True):
        self.item_tasks.inventory(token,occupancy,fresh)
        if token and self.inventory_token and token!=self.inventory_token:self.item_attempts.clear()
        if token:self.inventory_token=token
    def _path(self,targets,toward=None,tie_break=None,choices=None):
        if not targets:return None
        disputed=self.disputed();floor=self.known-disputed;start=self.position;queue=deque([(start,[])]);seen={start};best=None;best_rank=None
        while queue:
            node,path=queue.popleft()
            if node in targets:
                if toward is None and tie_break is None:return node,path
                rank=(abs(node[0]-toward[0])+abs(node[1]-toward[1]),len(path),node) if toward is not None else (len(path),*tie_break(node,path))
                if choices is not None:choices.append((node,path,rank))
                if best_rank is None or rank<best_rank:best=(node,path);best_rank=rank
            for key,d in ROUTE_MOVES.items():
                if '+' in key:
                    if not room_diagonal_allowed(node,self._add(node,d),floor):continue
                    sides=[self._add(node,MOVES[k]) for k in key.split('+')]
                    if any(p not in self.known or p in self.enemies or p in disputed for p in sides) or any(self.failures[node,k]>=2 for k in key.split('+')):continue
                nxt=self._add(node,d)
                if nxt not in self.known or nxt in seen or nxt in self.enemies or nxt in disputed:continue
                # No timer-based retry: repeated failures need new topology or a
                # successful traversal, not observation calls, to be reconsidered.
                if self.failures[node,key]>=2:continue
                seen.add(nxt);queue.append((nxt,path+[key]))
        return best
    def _frontier_rank(self,target,path):
        node=self.position;exposure=attempts=turns=0;heading=self.last_direction
        for key in path:
            attempts+=self.failures[node,key]
            node=self._add(node,ROUTE_MOVES[key])
            exposure+=any(max(abs(node[0]-e[0]),abs(node[1]-e[1]))<=1 for e in self.enemies)
            turns+=heading is not None and heading!=key;heading=key
        # Distance is ranked first by _path. These factors resolve equal-length
        # routes only; age, visitation and the order a frontier appeared do not.
        return exposure,attempts,turns,target
    def _select_frontier(self,targets,trigger):
        choices=[];candidate=self._path(targets,tie_break=self._frontier_rank,choices=choices)
        ranked=sorted(choices,key=lambda item:item[2])
        self.frontier_selection={'trigger':trigger,'origin':list(self.position),
          'rule':'到達可能な既知床で必要な移動入力数が最少。同距離なら敵への接近・無移動の試行履歴・方向変更の少なさを比較。新旧や踏破回数では選ばない。',
          'candidate_count':len(targets),'reachable_count':len(choices),
          'selected_cell':list(candidate[0]) if candidate else None,
          'candidates':[{'cell':list(node),'route_steps':rank[0],'enemy_adjacent_steps':rank[1],
             'previous_no_motion_trials':rank[2],'direction_changes':rank[3]} for node,path,rank in ranked[:40]]}
        return candidate
    def hint(self,state,preference='current_goal',mission='auto',plan_id=None,item_decisions=None):
        if not self.observe(state):return None
        if plan_id and plan_id!=self.plan_id:
            self.plan_id=plan_id
            if mission in MISSIONS:
                if mission!=self.mission:self.item_tasks.goal_changed()
                self.mission=mission
            self.item_tasks.decide(item_decisions)
        selected_mission=self.mission
        if selected_mission=='auto':
            selected_mission='collect_visible_items' if preference=='items' else 'reach_stairs' if preference=='stairs' and self.stairs else 'find_stairs'
        primary=self.primary();secondary=self.boundaries()
        target_kind='frontier';targets=primary
        if selected_mission in ('reach_stairs','return_with_objective') and self.stairs:targets=self.stairs;target_kind='stairs'
        elif selected_mission in ('collect_visible_items','search_objective') and self.item_tasks.goals(self.position):targets=self.item_tasks.goals(self.position);target_kind='items'
        elif not primary and self.stairs and selected_mission!='search_objective':targets=self.stairs;target_kind='stairs'
        elif not primary and secondary:targets=secondary;target_kind='boundary_recheck'
        # Keep the remote exploration destination across transit, supervisor
        # reviews. After a higher-priority interruption, compare distances again.
        # Revalidate against current
        # floor evidence; a cached/disputed or exhausted endpoint is not a goal.
        selection_trigger='initial'
        if self.exploration_task:
            selection_trigger='target_reached' if tuple(self.exploration_task['target_cell'])==self.position else 'frontier_changed'
        if self.exploration_task and (tuple(self.exploration_task['target_cell']) not in primary or selected_mission in ('reach_stairs','return_with_objective')):
            self.exploration_task=None
        # Items create tactical possibilities. Enemies supersede collection
        # when currently close or adjacent to the proposed collection route.
        # This geometric check does not prove remote/ranged enemies harmless.
        item_goals=self.item_tasks.goals(self.position)
        item_path=self._path(item_goals) if item_goals else None
        threats=set(self.enemies)
        if self.item_tasks.context()['needs_inventory_check']:
            threats={e for e in self.enemies if max(abs(e[0]-self.position[0]),abs(e[1]-self.position[1]))<=2}
        if item_path is not None:
            points=[self.position]
            for key in item_path[1]:points.append(self._add(points[-1],ROUTE_MOVES[key]))
            threats={e for e in self.enemies if max(abs(e[0]-self.position[0]),abs(e[1]-self.position[1]))<=2 or any(max(abs(e[0]-p[0]),abs(e[1]-p[1]))<=1 for p in points)}
        caution=state.get('recovery_policy',{})
        passive={tuple(p) for p in caution.get('stationary_enemy_cells',[])}
        threats={e for e in threats if max(abs(e[0]-self.position[0]),abs(e[1]-self.position[1]))<=1 or e not in passive and not caution.get('avoid_initiating_combat')}
        enemy_goals={self._add(e,d) for e in threats for d in ROUTE_MOVES.values()}&self.known-self.enemies
        for kind,options in (('enemy',enemy_goals),('items',item_goals),('inspect_marker',self.item_tasks.goals(self.position,'unclassified_marker'))):
            if options and self._path(options) is not None:
                targets=options;target_kind=kind;break
        if target_kind=='enemy':self.item_tasks.enemy_priority()
        if item_goals and self._path(item_goals) is None:
            for cell in item_goals:self.item_tasks.change(self.item_tasks.tasks[cell],'route_unavailable','現在の既知床と有効な辺では到達不能')
        if target_kind=='frontier' and targets:
            if self.exploration_task is None or self.exploration_task.get('status')=='interrupted':
                if self.exploration_task:selection_trigger='resume_after_interruption'
                candidate=self._select_frontier(targets,selection_trigger)
                target=candidate[0] if candidate else min(targets,key=lambda c:(abs(c[0]-self.position[0])+abs(c[1]-self.position[1]),c))
                self.exploration_task={'target_cell':list(target),'status':'navigating','reason':'現在地から最短の到達可能な先端を選択。同距離では接近リスクと方向変更を比較。移動途中は維持する'}
            targets={tuple(self.exploration_task['target_cell'])}
        path=self._path(targets);self.last_hint=None
        deferred_unreachable=False
        if path is None and target_kind in ('frontier','boundary_recheck'):
            # Other genuinely reachable frontiers come before boundary probes.
            # A blocked task is retained, but does not monopolize progress.
            alternatives=[('frontier',primary-targets)]
            if selected_mission!='search_objective':alternatives.append(('stairs',self.stairs))
            alternatives.append(('boundary_recheck',secondary))
            for kind,options in alternatives:
                toward=tuple(self.exploration_task['target_cell']) if kind=='boundary_recheck' and self.exploration_task else None
                alternate=self._select_frontier(options,'route_blocked') if kind=='frontier' else self._path(options,toward=toward)
                if alternate is not None:
                    targets=options;target_kind=kind;path=alternate;deferred_unreachable=True
                    if kind=='frontier':self.exploration_task={'target_cell':list(path[0]),'status':'navigating','reason':'通れない経路を保留し、現在地から最短の到達可能な先端を選択'}
                    break
        phase='inspect_unclassified_marker' if target_kind=='inspect_marker' else 'handle_visible_enemy' if target_kind=='enemy' else 'return_to_stairs' if target_kind=='stairs' and selected_mission=='return_with_objective' else 'reach_stairs' if target_kind=='stairs' else 'collect_visible_items' if target_kind=='items' else 'explore_frontier'
        reason='未分類マークの位置を確認し、アイテムと断定せず分類を再検討する' if target_kind=='inspect_marker' else '見えている敵に対処するため既知床上の隣接位置へ向かう' if target_kind=='enemy' else '見えている品の位置へ向かい取得結果を確認する' if target_kind=='items' else '現在地からの経路距離と状況を比較した未確認の探索先へ進む'
        if not targets:
            phase='verify_objective' if selected_mission=='search_objective' else 'review_exhausted_floor'
            reason='現在の観測範囲に未処理の探索先がない。所持品・目標・地図認識を再検討する'
        elif path is None:phase='route_unavailable';reason='既知の床で到達可能な経路がない。接触や無移動の原因を再検討する'
        else:
            target,steps=path
            if target_kind in ('items','inspect_marker'):self.item_tasks.planned(target)
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
                self.last_hint={'key':steps[0],'keys':steps[0].split('+'),'diagonal':len(steps[0].split('+'))==2,'mode':'observed_grid_route',
                    'target_cell':list(target),'distance':len(steps),'target_kind':target_kind,'route_cells':route[:1728]}
        if self.last_hint is None and phase in ('route_unavailable','review_exhausted_floor'):
            recheck=self.recheck_hint()
            if recheck:
                self.last_hint=recheck;phase='verify_failed_route'
                reason='代替経路がなく、現在も床表示がある過去の通過接続を1回だけ再検証する。通れるとは仮定しない'
        if self.exploration_task:
            task=self.exploration_task
            if target_kind=='frontier' and path is not None and list(path[0])==task['target_cell']:
                task.update(status='probing' if not path[1] else 'navigating',reason='継続目標への経路を現在の観測で再計算')
            elif target_kind=='frontier' and path is not None:
                task.update(status='interrupted',reason='現在到達可能な別の先端を先に探索。到達不能な元の目標は保持')
            elif target_kind=='boundary_recheck':
                task.update(status='detour_search',reason='目標への既知経路が使えないため、目標側の到達可能な境界から迂回を探す')
                reason=task['reason']
            elif target_kind in ('enemy','items','inspect_marker','stairs'):
                task.update(status='interrupted',reason='現在の敵・品・階段の目的を優先。未処理先端への継続目標は保持')
            elif path is None:
                task.update(status='blocked',reason='既知の有効な辺では到達不能。失敗辺を解除せず再検討が必要')
        if deferred_unreachable:reason='到達できない先端を保留し、到達可能な別の目的へ進む。'+reason
        self.goal={'cycle_review':self.cycle_status()['detected'],'priority_order':['collection_threat_response','items_and_use_learning','unprocessed_frontiers','routine_status_review'],'floor_value_rule':'踏破履歴は診断専用。探索対象は未確認の方向と調査結果で決め、経路は観測した床と接続から組む。','threat_assessment':'現在の近接と取得経路への接触。遠隔能力・敵種の安全性は未確定','item_recheck_needed':self.item_tasks.context()['needs_inventory_check'], 'mission':selected_mission,'phase':phase,'reason':reason,'target_cell':self.last_hint.get('target_cell') if self.last_hint else None,
          'frontier_selection':{k:copy.deepcopy(self.frontier_selection[k]) for k in ('trigger','origin','selected_cell','candidate_count','reachable_count') if k in self.frontier_selection},
          'needs_review':self.cycle_status()['detected'] or phase in ('route_unavailable','review_exhausted_floor','verify_stair_action','verify_objective'),
          'stairs_seen_type_unknown':bool(self.stairs),'instruction':'これは階内の局所目標。階段の上下・箱の所持・帰還成功を推定で確定しない。'}
        if self.exploration_task:self.goal['exploration_task']=copy.deepcopy(self.exploration_task)
        if self.last_hint:
            token='+'.join(self.last_hint.get('keys') or [self.last_hint['key']])
            if (self.position,token) in self.movement_checks:self.last_hint.update(self.movement_checks[self.position,token])
        if self.last_hint:self.last_hint.update(basis='floor_connections_and_direction_checks',reason=reason)
        return copy.deepcopy(self.last_hint)
    def context(self):
        if self.position is None:return {'status':'unavailable','goal':copy.deepcopy(self.goal)}
        primary=self.primary();retired=self.retired();interior={c for c in self.known if self.degree(c)==4}
        return {'movement_checks':[{'origin':list(p),'keys':k.split('+'),**copy.deepcopy(v)} for (p,k),v in sorted(self.movement_checks.items())], 'frontier_selection':copy.deepcopy(self.frontier_selection),'cycle_status':self.cycle_status(),'route_rechecks':[{'origin':list(p),'keys':k.split('+'),'traversal_evidence':v} for (p,k),v in sorted(self.rechecked_edges.items())], 'exploration_task':copy.deepcopy(self.exploration_task),'suppressed_edges':[{'origin':list(p),'keys':k.split('+'),'failures':v} for (p,k),v in sorted(self.failures.items()) if v>=2], 'item_tasks':self.item_tasks.context(),'status':'observed_floor','disputed_cached_cells':[list(p) for p in self.disputed()],
          'ineffective_actions_here':[k.split('+') for k in ROUTE_MOVES if self.failures[self.position,k]>=2],
          'suppressed_edge_count':sum(v>=2 for v in self.failures.values()),'floor':list(self.floor),'position':list(self.position),'goal':copy.deepcopy(self.goal),
          'known_count':len(self.known),'visited_count':len(self.visited),'primary_frontier_count':len(primary),'exhausted_branch_count':len(retired),
          'visited_cells':[list(p) for p in self.visited],'exploration_unnecessary_cells':[list(p) for p in interior|retired],
          'exhausted_branch_cells':[list(p) for p in retired],'frontier_cells':[list(p) for p in primary],
          'stairs_cells':[list(p) for p in self.stairs],'route_cells':copy.deepcopy((self.last_hint or {}).get('route_cells',[])),
          'frontier_rule':'細い床の先端と、入力方向の確認が残る境界を候補にする。2×2の床領域に属する角を先端にしない。経路設定は踏破や処理完了ではない。',
          'note':'無移動の方向は今回の探索試行を一旦完了するだけで永久の壁とは確定しない。探索不要は現在の探索先の優先度。通行禁止や永久の壁ではない。新しい床・目標・階で見直す。'}
