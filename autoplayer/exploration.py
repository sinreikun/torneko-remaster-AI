"""Session-local routes from observed single-cell transitions, never inferred walls.

Coordinates are screen estimates. Nothing here is shared across game sessions.
An unresponsive direction is an attempted probe, not proof of an obstacle.
"""
from collections import Counter, deque
import math

DELTAS={'w':(0,-1),'a':(-1,0),'s':(0,1),'d':(1,0)}


class ExplorationRoutes:
    def __init__(self):
        self.reset()

    def reset(self):
        self.space=None;self.nodes={};self.edges={};self.attempts=Counter();self.arrivals=Counter();self.max_pixels=0;self.no_effect=Counter();self.inspection_node=None;self.resource_inspected=None

    def node(self,state):
        m=state.get('minimap',{})
        floor=state.get('metrics',{}).get('floor',{}).get('values')
        if not state.get('eligible') or not floor or m.get('status')!='observed':return None
        space=(tuple(floor),tuple(m.get('analysis_size',[])),tuple(m.get('source_size',[])),m.get('cell_size'))
        pixels=len(m.get('known_cells',[])) or m.get('known_pixels',0)
        if space!=self.space or pixels<self.max_pixels*.65:self.reset();self.space=space
        self.max_pixels=max(self.max_pixels,pixels)
        cell=tuple(m['player_cell'])
        self.nodes[cell]={k:dict(v) for k,v in m.get('neighbors',{}).items()}
        if len(self.nodes)>2048:
            self.reset();self.space=space;self.nodes[cell]=m.get('neighbors',{})
        return cell

    def record(self,before,after,key,natural):
        if key not in DELTAS or natural.get('position') or natural.get('floor'):return
        a=self.node(before)
        if a is None:return
        old_space=self.space;b=self.node(after)
        if b is None or self.space!=old_space:return
        ma,mb=before['minimap'],after['minimap'];tile=ma['cell_size']
        dx=(mb['player'][0]-ma['player'][0])/tile;dy=(mb['player'][1]-ma['player'][1])/tile
        expected=DELTAS[key]
        if math.hypot(dx,dy)>1.6:
            self.reset()  # Teleport, map translation or lost tracking: do not join spaces.
            return
        self.attempts[a,key]+=1;self.arrivals[a]=max(1,self.arrivals[a]);self.arrivals[b]+=1
        if a==b:self.no_effect[a,key]+=1
        else:self.no_effect[a,key]=0
        if a!=b and abs(dx-expected[0])<.4 and abs(dy-expected[1])<.4 and b==(a[0]+expected[0],a[1]+expected[1]):
            self.edges[a,key]=b
        else:
            # A formerly observed edge may now be obstructed. Do not keep routing into it.
            self.edges.pop((a,key),None)

    def suggest(self,state,candidates):
        start=self.node(state)
        if start is None:return None
        allowed={v['keys'][0] for v in candidates}
        # Search known paths for an unattempted direction. Known blue neighbors
        # precede unobserved cells; the latter may be either walls or unexplored.
        for known,unvisited in ((True,True),(False,True),(True,False),(False,False)):
            queue=deque([(start,None,0)]);seen={start}
            while queue:
                node,first,distance=queue.popleft()
                for candidate in candidates:
                    key=candidate['keys'][0];neighbor=self.nodes[node].get(key,{})
                    if bool(neighbor.get('known'))!=known:continue
                    dx,dy=DELTAS[key];destination=(node[0]+dx,node[1]+dy)
                    if (self.arrivals[destination]==0)!=unvisited:continue
                    if self.attempts[node,key]==0:
                        return {'key':first or key,'mode':'untried_direction' if first is None else 'observed_route',
                            'target_cell':list(node),'probe_key':key,'distance':distance,
                            'note':'観測済み区間を1入力ずつ再確認。未観測セルを壁とは判定しない。'}
                for key in DELTAS:
                    if node==start and key not in allowed:continue
                    dest=self.edges.get((node,key))
                    if dest is not None and dest not in seen:
                        seen.add(dest);queue.append((dest,first or key,distance+1))
        return None

    def penalty(self,state,key):
        node=self.node(state)
        if node is None:return 0.
        dx,dy=DELTAS[key];dest=(node[0]+dx,node[1]+dy)
        return .12*min(6,self.attempts[node,key])+.08*min(6,self.arrivals[dest])

    def visits(self,state):
        node=self.node(state)
        return self.arrivals[node] if node is not None else 0
