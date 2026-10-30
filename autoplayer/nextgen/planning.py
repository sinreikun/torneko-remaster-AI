"""Game-independent bounded short-horizon planning over uncertain action models.

No keys, item/species names, OS operations or LLM calls occur in this module.
"""
import copy,math,time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from .state_hash import digest


def interval(lower=None,upper=None,source='UNKNOWN',**extra):
    if lower is not None and upper is not None and lower>upper:raise ValueError('Reversed prediction interval')
    return {'lower':lower,'upper':upper,'source':source,'status':'unknown' if lower is None or upper is None else 'point' if lower==upper else 'range',**extra}
def known(value,source='OBSERVED'):return interval(value,value,source)
def contains(bound,value):return (bound.get('lower') is None or value>=bound['lower']) and (bound.get('upper') is None or value<=bound['upper'])
def plus_bound(a,b):return interval(a['lower']+b['lower'] if a['lower'] is not None and b['lower'] is not None else None,a['upper']+b['upper'] if a['upper'] is not None and b['upper'] is not None else None,'MODEL')
def dist(a,b):return max(abs(a[i]-b[i]) for i in (0,1))
def coord(p):return tuple(p)
def key(p):return f'{p[0]},{p[1]}'

def neighbors(p,cells,diagonal=True,blocked=()):
    for d in ((0,-1),(1,0),(0,1),(-1,0),(-1,-1),(1,-1),(1,1),(-1,1)) if diagonal else ((0,-1),(1,0),(0,1),(-1,0)):
        q=(p[0]+d[0],p[1]+d[1])
        if q not in cells or q in blocked:continue
        if d[0] and d[1] and ((p[0]+d[0],p[1]) not in cells or (p[0],p[1]+d[1]) not in cells or (p[0]+d[0],p[1]) in blocked or (p[0],p[1]+d[1]) in blocked):continue
        yield q

def paths_from(start,cells,diagonal=True,blocked=(),limit=2048):
    paths={start:[start]};q=deque([start])
    while q and len(paths)<limit:
        p=q.popleft()
        for n in neighbors(p,cells,diagonal,blocked):
            if n not in paths:paths[n]=paths[p]+[n];q.append(n)
    return paths

def simultaneous_attackers(options):
    """Maximum distinct enemies occupying distinct attack positions."""
    occupied={}
    def visit(enemy,seen):
        for p in options[enemy]:
            if p in seen:continue
            seen.add(p)
            if p not in occupied or visit(occupied[p],seen):occupied[p]=enemy;return True
        return False
    return sum(visit(enemy,set()) for enemy in range(len(options)))


def binomial_tail(n,k,p):
    if k<=0:return 1.
    if k>n:return 0.
    return sum(math.comb(n,i)*p**i*(1-p)**(n-i) for i in range(k,n+1))

def action(kind,parameters=None,turns=None,effects=None,costs=None,preconditions=(),capability=None,risk=None,uncertainty=()):
    return {'kind':kind,'parameters':parameters or {},'preconditions':list(preconditions),'effects':effects or {},'costs':costs if costs is not None else {'items':None},'risk':risk if risk is not None else {'loss':interval(),'conditions':'unverified'},'turns':turns or interval(0,None),'uncertainty':list(uncertainty),'capability':capability or kind}

def plan_candidate(label,actions,execution,goal=None,evidence=(),confidence=None):
    return {'id':digest([execution,[(a['kind'],a['parameters']) for a in actions]]),'label':label,'actions':actions,'execution':execution,'goal':goal or {},'evidence':list(evidence),'confidence':confidence,'preconditions':['same_region','same_goal','current_evidence']}

class PlanningEngine:
    def __init__(self,horizon=5,max_candidates=24,max_reach=96):
        self.horizon=horizon;self.max_candidates=max_candidates;self.max_reach=max_reach
    def predict(self,world,plan):
        cells={coord(p) for p in world.get('cells',[])};blocked={coord(p) for p in world.get('blocked',[])};diagonal=world.get('diagonal',False)
        position=coord(world['position']) if world.get('position') is not None else None
        actors=copy.deepcopy(world.get('actors',[])[:12]);reach={a['id']:{coord(a['position'])} for a in actors if a.get('position') is not None}
        hp=copy.deepcopy(world.get('hp',interval()));resource=copy.deepcopy(world.get('resource',interval()));turns=known(0);damage=known(0);covered_damage=known(0);consumed=0;unknown=[];timeline=[];exposure=0;peak=0;truncated=len(world.get('actors',[]))>12;optimistic_dead=set();pessimistic_dead=set();target_damage={};strike_counts={};expected_outgoing={};defeat_probability={};information=0.;progress=0.;effects={};first=None;resource_unknown=False;metrics=copy.deepcopy(world.get('metrics',{}));blocked_paths=[]
        for index,a in enumerate(plan['actions'][:self.horizon]):
            duration=a['turns'];turns=plus_bound(turns,duration)
            if duration['upper'] is None:unknown.append('action_duration');rounds=min(self.horizon,1)
            else:rounds=min(self.horizon,max(0,math.ceil(duration['upper'])))
            if position is not None and a['kind']=='move':
                dest=coord(a['parameters']['destination'])
                if dest not in neighbors(position,cells,diagonal,blocked):unknown.append('movement_precondition');break
                if any(dest in positions for positions in reach.values()):
                    blocked_paths.append({'destination':list(dest),'position_if_blocked':list(position)});unknown.append('future_occupancy_conflict')
                position=dest
            target=a['parameters'].get('target_id')
            if a['kind']=='strike' and target:
                on_hit=a['effects'].get('damage_on_hit',a['effects'].get('damage',interval()));prob=a['effects'].get('hit_probability',interval())
                effect=interval(0,on_hit['upper'],'CONDITIONAL_HIT_OR_MISS')
                strike_counts[target]=strike_counts.get(target,0)+1
                expected=interval(on_hit['lower']*prob['lower'] if on_hit['lower'] is not None and prob['lower'] is not None else None,on_hit['upper']*prob['upper'] if on_hit['upper'] is not None and prob['upper'] is not None else None,'CONDITIONAL_EXPECTATION')
                expected_outgoing[target]=plus_bound(expected_outgoing.get(target,known(0)),expected)
                previous=target_damage.get(target,known(0));target_damage[target]=plus_bound(previous,effect)
                enemy=next((e for e in actors if e['id']==target),None)
                if enemy:
                    remaining=enemy.get('remaining_hp',interval());total=target_damage[target]
                    if all(v is not None for v in (remaining['lower'],remaining['upper'],on_hit['lower'],on_hit['upper'],prob['lower'],prob['upper'])) and on_hit['lower']>0 and on_hit['upper']>0:
                        defeat_probability[target]=interval(binomial_tail(strike_counts[target],math.ceil(remaining['upper']/on_hit['lower']),prob['lower']),binomial_tail(strike_counts[target],math.ceil(remaining['lower']/on_hit['upper']),prob['upper']),'CONDITIONAL_BINOMIAL',conditions=['independent_hits','stable_damage','comparable_encounter_hp','no_unobserved_healing'],guaranteed=False)
                    else:defeat_probability[target]=interval()
                    if remaining['lower'] is not None and total['upper'] is not None and total['upper']>=remaining['lower']:optimistic_dead.add(target)
                    if remaining['upper'] is not None and total['lower'] is not None and total['lower']>=remaining['upper']:pessimistic_dead.add(target)
                if effect['upper'] is None:unknown.append('target_damage')
                progress+=a.get('goal_value',.2)
            healing=a['effects'].get('hp_gain')
            if healing:
                hp=plus_bound(hp,healing)
                maximum=world.get('hp_max')
                if maximum is not None:
                    hp['lower']=min(maximum,hp['lower']) if hp['lower'] is not None else None;hp['upper']=min(maximum,hp['upper']) if hp['upper'] is not None else maximum
            if 'resource_gain' in a['effects']:resource=plus_bound(resource,a['effects']['resource_gain'])
            if a['costs'].get('items') is None:resource_unknown=True;unknown.append('resource_cost')
            else:consumed+=a['costs']['items']
            information+=a['effects'].get('information',0.)
            for _ in range(rounds):
                options=[];upper_losses=[]
                for enemy in actors:
                    identifier=enemy['id']
                    if identifier not in reach or identifier in pessimistic_dead:continue
                    if enemy.get('movement',{}).get('guaranteed') is False:unknown.append('actor_motion_model_unverified')
                    if enemy.get('attack_distance',{}).get('guaranteed') is False:unknown.append('actor_attack_model_unverified')
                    speed=enemy.get('movement',interval(0,None));speed_upper=speed['upper']
                    if speed_upper is not None and speed_upper>3:truncated=True
                    if speed_upper is None:
                        unknown.append('enemy_movement');truncated=True;speed_upper=1
                    current=reach[identifier];attack_positions=set(current)
                    for _ in range(min(3,max(0,math.ceil(speed_upper)))):
                        expanded=current|{n for p in current for n in neighbors(p,cells,diagonal,blocked) if n!=position}
                        if len(expanded)>self.max_reach:truncated=True;expanded=set(sorted(expanded,key=lambda p:dist(p,position) if position else 0)[:self.max_reach])
                        current=expanded
                    reach[identifier]=current
                    attack=enemy.get('attack_distance',interval());r=attack['upper']
                    if r is None:unknown.append('enemy_attack_condition');r=1;truncated=True
                    possible={p for p in (current if enemy.get('movement_and_attack_same_turn') else attack_positions) if position is not None and dist(p,position)<=r and p!=position}
                    if possible:options.append(possible);upper_losses.append(enemy.get('incoming_damage',interval())['upper'])
                simultaneous=simultaneous_attackers(options);exposure+=simultaneous;peak=max(peak,simultaneous)
                # The reachability envelope includes wait/approach alternatives;
                # no guaranteed attack or hit is asserted by the lower bound.
                if simultaneous and any(v is None for v in upper_losses):loss=interval(0,None);unknown.append('incoming_damage')
                else:loss=interval(0,sum(sorted(upper_losses,reverse=True)[:simultaneous]) if upper_losses else 0,'MODEL')
                damage=plus_bound(damage,loss);covered_damage=plus_bound(covered_damage,loss)
                hp=interval(hp['lower']-loss['upper'] if hp['lower'] is not None and loss['upper'] is not None else None,hp['upper'],'MODEL')
            if duration['upper'] is None and actors:
                damage=interval(0,None,'UNBOUNDED_ACTION_TIMING');hp['lower']=None;unknown.append('unbounded_timing_harm')
            if blocked_paths:hp['lower']=None;unknown.append('blocked_path_damage_unknown')
            for name,value in a['effects'].get('metrics',{}).items():
                effects[name]=copy.deepcopy(value);metrics[name]=plus_bound(metrics.get(name,interval()),value)
            snapshot={'depth':index+1,'position':list(position) if position is not None else None,'hp':copy.deepcopy(hp),'turns':copy.deepcopy(turns),'damage':copy.deepcopy(damage),'simultaneous_attackers_upper':peak,'enemy_candidates':{k:[list(p) for p in sorted(v)[:8]] for k,v in reach.items()},'possible_defeats':sorted(optimistic_dead),'supported_defeats_in_model':sorted(pessimistic_dead),'resource':copy.deepcopy(resource)}
            if first is None:
                first=copy.deepcopy(snapshot);first['enemy_reachability']={k:[list(p) for p in v] for k,v in reach.items()}
            timeline.append(snapshot)
        available=list(neighbors(position,cells,diagonal,blocked|{p for v in reach.values() for p in v})) if position is not None else []
        degree=len(list(neighbors(position,cells,False,blocked))) if position is not None else 0
        terminal_attack_options=[]
        for enemy in actors:
            if enemy['id'] not in reach or enemy['id'] in pessimistic_dead:continue
            radius=enemy.get('attack_distance',{}).get('upper') or 1
            terminal_attack_options.append({p for p in reach[enemy['id']] if position is not None and p!=position and dist(p,position)<=radius})
        terminal_pressure=simultaneous_attackers(terminal_attack_options)
        known_retreats=len(available);target_cell=plan.get('goal',{}).get('cell');distance=dist(position,target_cell) if position is not None and target_cell is not None else None
        if plan.get('goal',{}).get('kind')!='tactical_position' and target_cell is not None and world.get('position') is not None and position is not None:progress+=dist(world['position'],target_cell)-distance
        success=known(1,'MODEL_POSITION_ONLY') if distance==0 and plan['goal'].get('kind') in ('frontier','position') else interval(0,1,'UNCONFIRMED')
        if plan.get('goal',{}).get('target_id') in defeat_probability:success=defeat_probability[plan['goal']['target_id']]
        return {'first':first,'timeline':timeline,'terminal':{'position':list(position) if position is not None else None,'hp':hp,'resource':resource,'turns':turns,'damage':damage,'covered_damage':covered_damage,'simultaneous_attackers_upper':peak,'attack_opportunities_upper':exposure,'terminal_attackers_upper':terminal_pressure,'known_retreat_options':known_retreats,'degree':degree,'items_consumed':interval(consumed,None if resource_unknown else consumed,'MODEL'),'metrics':metrics,'information_opportunity':information,'goal_progress_proxy':progress,'goal_success':success,'goal_distance':distance,'effects':effects,'possible_defeats':list(optimistic_dead),'expected_outgoing_damage':expected_outgoing,'conditional_defeat_probability':defeat_probability},'uncertainty':sorted(set(unknown+list(plan.get('uncertainty',[])))),'incomplete_envelope':truncated,'failure_state':{'hp_can_be_nonpositive':hp['lower'] is None or hp['lower']<=0,'known_retreats':known_retreats,'possible_path_interruptions':blocked_paths,'model_validity_unverified':any('unverified' in u for u in unknown),'unmodeled_tail':truncated or any(a['turns']['upper'] is None for a in plan['actions'])},'semantics':'conditional_reachability_envelope_not_exact_game_ai'}
    def evaluate(self,world,plan,prediction):
        t=prediction['terminal'];hp=world.get('hp',interval());weights=world.get('weights',{})
        damage=t['damage']['upper'];covered=t['covered_damage']['upper'];risk_proxy=damage if damage is not None else covered;low=hp['lower'];lethal=risk_proxy is not None and low is not None and risk_proxy>=low
        progress=t['goal_progress_proxy'];kind=plan['goal'].get('kind');goal_value=plan.get('utility',1.)
        # These are explicit selection heuristics, never substituted predictions.
        components={'goal_progress':progress*weights.get('progress',1.4),'objective':goal_value,'exposure':-t['attack_opportunities_upper']*weights.get('exposure',1.4),'terminal_pressure':-t['terminal_attackers_upper']*weights.get('terminal_pressure',2.),'simultaneous_contact':-max(0,t['simultaneous_attackers_upper']-1)*weights.get('multi_contact',3.),'known_damage':-(risk_proxy/max(1,low))*weights.get('damage',8.) if risk_proxy is not None and low is not None else 0.,'lethal_possible':-weights.get('lethal',40.) if lethal else 0.,'future_options':min(4,t['known_retreat_options'])*weights.get('options',.4),'consumption':-t['items_consumed']['lower']*weights.get('consumption',.4),'possible_defeat':len(t['possible_defeats'])*weights.get('defeat',2.),'information':t['information_opportunity']*weights.get('information',.7),'uncertainty':-len(prediction['uncertainty'])*weights.get('uncertainty',.15),'path_interruption':-len(prediction['failure_state']['possible_path_interruptions'])*weights.get('path_interruption',2.),'incomplete':-1. if prediction['incomplete_envelope'] else 0.,'time':-t['turns']['lower']*.08 if t['turns']['lower'] is not None else 0.}
        objective=[]
        predicted_metrics={**t.get('metrics',{}),'hp':t['hp'],'fullness':t['resource']}
        for c in world.get('current_goal',{}).get('conditions',[]):
            pieces=c.get('feature','').split('.')
            if len(pieces)!=3 or pieces[0]!='metric' or pieces[2]!='0':
                objective.append({'condition':c,'support':'unknown'});continue
            bound=predicted_metrics.get(pieces[1],interval());target=c.get('value');op=c.get('operator')
            if not isinstance(target,(int,float)):
                objective.append({'condition':c,'support':'unknown','reason':'non_numeric_metric_condition'});continue
            supported=bound['lower'] is not None and bound['lower']>=target if op=='ge' else bound['upper'] is not None and bound['upper']<=target if op=='le' else bound['lower']==bound['upper']==target if op=='eq' else False
            objective.append({'condition':c,'predicted':bound,'support':'supported_in_model' if supported else 'unknown_or_not_satisfied'})
        components['conditional_success_proxy']=sum((b['lower']+b['upper'])/2 for b in t.get('conditional_defeat_probability',{}).values() if b['lower'] is not None and b['upper'] is not None)*weights.get('conditional_success',4.)
        components['current_objective']=sum(c['support']=='supported_in_model' for c in objective)*weights.get('current_objective',4.)
        if kind=='recover' and low is not None and world.get('hp_max'):
            after=t['hp']['lower'];components['recovery']=max(0,(after-low)/world['hp_max'])*8 if after is not None else 0.
        if world.get('last_plan_id')==plan['id']:components['continuity']=.3
        return {'score':sum(components.values()),'components':components,'known_prefix_risk_is_not_a_total_upper_bound':damage is None,'unknown_is_not_zero_damage':damage is None,'objective_support':objective,'comparison_basis':'heuristic_over_conditional_prediction_ranges'}
    def select(self,world,candidates):
        started=time.monotonic();rows=[]
        groups={}
        for c in candidates:groups.setdefault(c.get('goal',{}).get('kind','unknown'),deque()).append(c)
        chosen=[]
        while groups and len(chosen)<self.max_candidates:
            for kind in list(groups):
                if len(chosen)>=self.max_candidates:break
                chosen.append(groups[kind].popleft())
                if not groups[kind]:del groups[kind]
        for candidate in chosen:
            prediction=self.predict(world,candidate);evaluation=self.evaluate(world,candidate,prediction)
            rows.append({'candidate':candidate,'prediction':prediction,'evaluation':evaluation})
        rows.sort(key=lambda r:(-r['evaluation']['score'],r['candidate']['id']))
        return {'stamp':copy.deepcopy(world['stamp']),'goal_revision':world.get('goal_revision'),'model_revision':world.get('model_revision',0),'selected':rows[0] if rows else None,'candidates':rows,'reason':'compare_goal_effects_cost_risk_future_options_and_uncertainty','ms':(time.monotonic()-started)*1000,'limits':{'horizon':self.horizon,'candidates':self.max_candidates,'reach_per_enemy':self.max_reach,'omitted_candidates':max(0,len(candidates)-self.max_candidates)}}

class PlanningLane:
    def __init__(self,engine=None):self.engine=engine or PlanningEngine();self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='short-planning');self.pending=None
    def begin(self,world,candidates):
        if self.pending:raise RuntimeError('Planning lane not joined')
        world=copy.deepcopy(world);candidates=copy.deepcopy(candidates);self.expected=(world['stamp'],world.get('goal_revision'),world.get('model_revision',0))
        self.pending=self.pool.submit(self.engine.select,world,candidates)
    def merge(self,stamp,goal_revision,model_revision):
        try:
            result=self.pending.result()
            if (stamp,goal_revision,model_revision)!=self.expected:raise ValueError('Stale short plan')
            return result
        finally:self.pending=None
    def close(self):self.pool.shutdown(wait=True,cancel_futures=True)
