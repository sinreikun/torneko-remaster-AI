"""Knowledge/observation provider and conditional entity experience for planning.

Only this adapter interprets the profile's game semantics. The planner consumes
abstract positions, effects, bounds and capabilities, never these labels/keys.
"""
import copy,math,uuid
from collections import deque
from .planning import interval,known,action,plan_candidate,dist,coord,neighbors,paths_from
from .state_hash import digest
from .interaction_surface import allows_field_input


def empirical(samples,source='EXPERIENCED',zero_lower=False):
    values=[x for x in samples if isinstance(x,(int,float))]
    return interval(min(0,min(values)) if zero_lower else min(values),max(values),source,samples=len(values),guaranteed=False) if values else interval()

def hit_probability(samples):
    if not samples:return interval()
    n=len(samples);p=sum(samples)/n;z=1.96;den=1+z*z/n;center=(p+z*z/(2*n))/den;radius=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return interval(max(0,center-radius),min(1,center+radius),'INFERRED_FROM_EVENT_GROUPS',samples=n,conditions=['conditional_independence','sampling_multiplicity_unresolved'],guaranteed=False)

def stats(state,executor):return {k:executor.values(state,k) for k in ('level','strength','sword','shield')}

class EntityExperience:
    def __init__(self,saved=None):
        saved=saved or {};self.models=saved.get('models',{});self.effects=saved.get('effects',{});self.revision=saved.get('revision',0);self.audit=deque(saved.get('audit',[]),maxlen=64);self.tracks={};self.serial=0;self.namespace=uuid.uuid4().hex[:8];self.region=None;self.last_frame=None;self.unassigned=deque(maxlen=16)
    def dump(self):return {'schema':1,'revision':self.revision,'models':self.models,'effects':self.effects,'audit':list(self.audit)}
    def observe(self,state,step):
        view=state.get('spatial',{});region=view.get('stamp',{}).get('region');domain=(region,state.get('planning_adventure',0))
        if domain!=self.region:self.tracks={};self.region=domain;self.last_frame=None
        grid=state.get('minimap',{});token=(state['frame_sha256'],grid.get('status'),grid.get('source_frame_sha256'))
        if self.last_frame==token:return list(self.tracks.values())
        self.last_frame=token
        if grid.get('status') not in ('observed','retained') or view.get('pose') is None:
            return list(self.tracks.values())
        pose=view['pose'];points=[(pose[0]+p[0],pose[1]+p[1]) for p in state['minimap'].get('enemies',[])]
        old=[t for t in self.tracks.values() if not t.get('defeated')];assigned=set();updated={}
        proposals={p:sorted((dist(p,t['position']),t['id']) for t in old if dist(p,t['position'])<=3 and step-t['last_step']<=3) for p in points}
        for p,ranking in proposals.items():
            best=ranking[0] if ranking else None
            unique=bool(best and (len(ranking)==1 or ranking[1][0]>best[0]) and sum(bool(v and v[0][1]==best[1] and v[0][0]<=best[0]) for v in proposals.values())==1 and best[1] not in assigned)
            if unique:
                t=copy.deepcopy(self.tracks[best[1]]);assigned.add(t['id']);t['previous_position']=t['position'];
                if not t.get('visible'):t['episode']['complete']=False
                t['matches']+=int(grid.get('status')=='observed' and t.get('last_fresh_frame')!=state['frame_sha256'])
                t['identity_status']='supported_continuity' if t['matches']>=2 else 'continuity_candidate'
            else:
                self.serial+=1;t={'id':f'actor:{self.namespace}:{self.serial}','matches':0,'identity_status':'ambiguous_or_new','name_votes':{},'name':None,'episode':{'hits':[],'misses':0,'conditions':None,'complete':True},'local':{},'movement_samples':[]}
            t.update(position=list(p),last_step=step,frame=grid.get('source_frame_sha256') or state['frame_sha256'],visible=True,region=region,measurement_source='OBSERVED' if grid.get('status')=='observed' else 'RETAINED')
            if grid.get('status')=='observed':t['last_fresh_frame']=state['frame_sha256']
            updated[t['id']]=t
        for t in old:
            if t['id'] not in updated and step-t['last_step']<=2 and not t.get('defeated'):
                t=copy.deepcopy(t);t['visible']=False;t['identity_status']='unobserved_not_defeated';updated[t['id']]=t
        self.tracks=dict(list(updated.items())[-32:]);return list(self.tracks.values())
    @staticmethod
    def condition_key(state_stats,region):return digest([state_stats,region])
    def model_for(self,track,state_stats,floor,mode='melee'):
        state_stats={**state_stats,'attack_mode':mode}
        name=track.get('name') if max(track.get('name_votes',{}).values(),default=0)>=2 else None
        key=digest([name,state_stats,floor]) if name else None
        local=track.get('local',{}).get(digest(state_stats),{})
        pooled=self.models.get(key,{}) if key else {}
        result={field:(pooled.get(field,[]) or local.get(field,[]))[-32:] for field in ('dealt','received','hit_results','hits_to_defeat','hp_lower','hp_upper','attack_distance')}
        result['source']='conditional_named_model' if pooled else 'current_individual_only';result['name']=name
        return result
    def learn(self,before,after,intent,messages,state_stats,step,receipt,previous_tracks=None):
        if receipt.get('os_submission')!='accepted':return
        if before.get('spatial',{}).get('stamp',{}).get('region')!=after.get('spatial',{}).get('stamp',{}).get('region') or before.get('planning_adventure',0)!=after.get('planning_adventure',0):
            self.observe(after,step);self.unassigned.append({'step':step,'reason':'region_or_adventure_transition'});return
        old=previous_tracks if previous_tracks is not None else {t['id']:copy.deepcopy(t) for t in self.tracks.values()};self.observe(after,step)
        before_actors=(before.get('planning_world') or {}).get('actors',[]);target=intent.get('target_id') if intent else None
        floor=before.get('floor_confirmation',{}).get('confirmed')
        for identifier,t in self.tracks.items():
            previous=old.get(identifier)
            if previous and t.get('visible') and t.get('measurement_source')=='OBSERVED' and t.get('identity_status')=='supported_continuity':
                displacement=dist(previous['position'],t['position']);t['movement_samples']=(t.get('movement_samples',[])+[displacement])[-16:]
                t['movement_conditions']={'last_action':intent.get('kind') if intent else None,'turn_resolution':'per_input_not_unconditionally_game_turns'}
        from .execution import ProcedureExecutor
        for m in ProcedureExecutor.group_battle_messages(messages):
            kind=m.get('kind');outgoing=kind in ('damage_dealt','miss','defeated_enemy');identifier=target if outgoing else None
            if not identifier:
                candidates=[a for a in before_actors if a.get('visible') and dist(a['position'],before['spatial']['pose'])<=1]
                if len(candidates)==1:identifier=candidates[0]['id']
            t=self.tracks.get(identifier) or old.get(identifier)
            if not t or (m.get('enemy') and t.get('name') and m['enemy']!=t['name'] and max(t.get('name_votes',{}).values(),default=0)>=2):
                self.unassigned.append({'step':step,'message':m,'reason':'ambiguous_actor_or_name'});continue
            if identifier not in self.tracks:self.tracks[identifier]=t
            name=m.get('enemy')
            if name:
                t['name_votes'][name]=t['name_votes'].get(name,0)+1
                best=max(t['name_votes'],key=t['name_votes'].get)
                if sum(n==t['name_votes'][best] for n in t['name_votes'].values())==1:t['name']=best
            if outgoing and intent.get('kind') not in ('strike','consume'):
                self.unassigned.append({'step':step,'message':m,'reason':'outgoing_effect_without_matching_action_intent'});continue
            sample_stats={**state_stats,'attack_mode':'incoming' if kind=='damage_received' else intent.get('item_name') if intent.get('kind')=='consume' else 'melee'}
            current_stats=digest(sample_stats)
            local=t.setdefault('local',{}).setdefault(current_stats,{});updated_fields=[]
            episode=t['episode'];episode['conditions']=episode['conditions'] or current_stats
            if episode['conditions']!=current_stats:episode['complete']=False
            def append(field,value):
                local[field]=(local.get(field,[])+[value])[-32:];updated_fields.append((field,value))
            if kind=='damage_dealt':
                append('hit_results',1)
                if m.get('damage') is not None:append('dealt',m['damage']);episode['hits'].append(m['damage'])
                else:episode['complete']=False
            elif kind=='miss':append('hit_results',0);episode['misses']+=1
            elif kind=='damage_received' and m.get('damage') is not None:
                append('received',m['damage'])
                previous=old.get(identifier)
                if previous and previous['position']==t['position'] and before.get('spatial',{}).get('pose')==after.get('spatial',{}).get('pose'):
                    append('attack_distance',dist(t['position'],before['spatial']['pose']))
            elif kind=='defeated_enemy':
                t['defeated']=True;t['visible']=False
                if episode['complete'] and episode['hits']:
                    append('hits_to_defeat',len(episode['hits']));append('hp_lower',sum(episode['hits'][:-1])+1);append('hp_upper',sum(episode['hits']))
            t['last_evidence']={'frame':after['frame_sha256'],'step':step,'message':m,'identity_status':t['identity_status'],'name_source':'OCR_association_not_species_truth'}
            if t.get('name') and max(t['name_votes'].values(),default=0)>=2:
                model_key=digest([t['name'],sample_stats,floor]);model=self.models.setdefault(model_key,{'name':t['name'],'conditions':sample_stats,'floor':floor,'evidence':[]})
                for field,value in updated_fields:model[field]=(model.get(field,[])+[value])[-32:]
                model['evidence']=(model['evidence']+[t['last_evidence']])[-8:]
            self.revision+=1
        if intent and intent.get('kind') in ('rest','consume','equip'):
            if not before_actors or intent['kind']!='rest':
                for name in ('hp','fullness','strength','sword','shield'):
                    a=before.get('metrics',{}).get(name,{}).get('values');b=after.get('metrics',{}).get(name,{}).get('values')
                    if a and b:
                        effect_key=digest([intent['kind'],intent.get('item_name'),name]);row=self.effects.setdefault(effect_key,{'kind':intent['kind'],'item_name':intent.get('item_name'),'metric':name,'samples':[]})
                        row['samples']=(row['samples']+[b[0]-a[0]])[-32:];row['causality']='observed_association_not_controlled_cause';self.revision+=1
        while len(self.models)>128:self.models.pop(next(iter(self.models)))
        while len(self.effects)>128:self.effects.pop(next(iter(self.effects)))

class KnowledgeProvider:
    def __init__(self,executor,spatial_policy,memory=None):self.executor=executor;self.policy=executor.policy;self.spatial_policy=spatial_policy;self.memory=memory or EntityExperience();self.last_plan=None
    def rules(self):return self.policy.get('planning',{})
    def turns(self,kind):
        values=self.rules().get('turns',{}).get(kind)
        return interval(values[0],values[1],'EXTERNAL_PROFILE',guaranteed=False) if values else interval(0,None)
    def effect(self,kind,item,metric):
        row=self.memory.effects.get(digest([kind,item,metric]),{});return empirical(row.get('samples',[]),zero_lower=True)
    def snapshot(self,state,step,local,control_revision,goal_revision,capabilities=()):
        state['planning_adventure']=self.executor.adventure_revision
        view=state.get('spatial',{});self.memory.observe(state,step);condition=stats(state,self.executor);floor=state.get('floor_confirmation',{}).get('confirmed');actors=[]
        for track in self.memory.tracks.values():
            if track.get('defeated'):continue
            model=self.memory.model_for(track,condition,floor);profile=self.rules().get('actor_defaults',{})
            movement=interval(*(profile.get('movement',[0,None])),'EXTERNAL_PROFILE',guaranteed=False)
            if track.get('movement_samples'):movement['upper']=max([movement['upper'] or 0]+track['movement_samples']) if movement['upper'] is not None else None
            attack=interval(*(profile.get('attack_distance',[None,None])),'EXTERNAL_PROFILE',guaranteed=False)
            if model['attack_distance']:attack['upper']=max([attack['upper'] or 0]+model['attack_distance'])
            episode=track['episode'];remaining=interval()
            if model['hp_upper'] and episode['complete']:
                dealt=sum(episode['hits']);remaining=interval(max(1,min(model['hp_lower'])-dealt),max(1,max(model['hp_upper'])-dealt),'INFERRED_CONDITIONAL',conditions=['comparable_encounter_initial_health','no_unobserved_damage_or_regeneration'],guaranteed=False)
            defense_model=self.memory.model_for(track,condition,floor,mode='incoming')
            for field in ('received','attack_distance'):model[field]=defense_model[field]
            fallback=[n for b in self.executor.battles.values() if b.get('status_values',{}).get('level')==condition.get('level') and b.get('status_values',{}).get('shield')==condition.get('shield') for n in b.get('received',[])]
            incoming=empirical(model['received'],zero_lower=True) if model['received'] else empirical(fallback,source='EXPERIENCED_UNASSIGNED_THREAT_PRIOR',zero_lower=True)
            actors.append({'id':track['id'],'position':track['position'],'visible':track.get('visible',False),'identity_status':track['identity_status'],'name':model['name'],'movement':movement,'incoming_damage':incoming,'outgoing_damage':empirical(model['dealt'],zero_lower=True),'damage_on_hit':empirical(model['dealt']),'remaining_hp':remaining,'attack_distance':attack,'movement_and_attack_same_turn':profile.get('movement_and_attack_same_turn',False),'hit_probability':hit_probability(model['hit_results']),'hits_to_defeat':empirical(model['hits_to_defeat']),'appearance_identity':'unconfirmed','conditions':condition,'source_frame':track['frame'],'measurement_source':track.get('measurement_source','UNKNOWN')})
        hp=state.get('metrics',{}).get('hp',{}).get('values');food=state.get('metrics',{}).get('fullness',{}).get('values');raw=view.get('map',{})
        return {'stamp':{**copy.deepcopy(view.get('stamp',{})),'frame':state['frame_sha256'],'control_revision':control_revision},'goal_revision':goal_revision,'model_revision':self.memory.revision,'position':view.get('pose'),'cells':[list(map(int,k.split(','))) for k in raw.get('cells',{})],'blocked':[list(map(int,k.split(','))) for k in raw.get('obstacles',[])],'diagonal':self.spatial_policy.get('grid_topology',4)==8,'actors':actors,'hp':known(hp[0]) if hp else interval(),'hp_max':hp[1] if hp else None,'resource':known(food[0]) if food else interval(),'current_goal':copy.deepcopy(local or {}),'weights':copy.deepcopy(self.rules().get('weights',{})),'last_plan_id':self.last_plan,'capabilities':list(capabilities),'metrics':{k:known(v['values'][0],source='OBSERVED') for k,v in state.get('metrics',{}).items() if v.get('values')},'state_stats':condition,'observed_frame':state['frame_sha256'],'unassigned_events':list(self.memory.unassigned)[-3:]}
    def propose(self,state,world,step):
        e=self.executor;result=[];view=state.get('spatial',{});position=world['position'];actors=[a for a in world['actors'] if a['visible']];hp=e.values(state,'hp',False);food=e.values(state,'fullness',False);local=world['current_goal']
        def add(label,actions,execution,goal,utility=1.):
            required=set()
            for a in actions:
                if a['kind'] in ('move','probe'):
                    destination=a['parameters'].get('destination')
                    if destination and position:
                        dx,dy=[destination[i]-position[i] for i in (0,1)]
                        if dx:required.add('right' if dx>0 else 'left')
                        if dy:required.add('down' if dy>0 else 'up')
                elif a['kind']=='face':required.update(('face','up','down','left','right'))
                elif a['kind']=='strike':required.add('confirm')
                elif a['kind']=='rest':required.add('rest')
                elif a['kind'] in ('interface','inspect','consume','equip'):required.update(('menu','cancel','confirm','up','down'))
            missing=sorted(role for role in required if not e.roles.get(role))
            if missing:
                world.setdefault('unavailable_capabilities',[]).append({'plan':label,'missing_bindings':missing,'status':'unexecutable_not_failed_game_effect'})
                return
            for a in actions:
                if a['kind'] in ('move','probe','face','strike','rest','inspect','interface'):a['costs']={'items':0,'source':'PROFILE_ACTION_SEMANTICS'}
                a['capability_evidence']={'status':'supported' if any(c.get('name')==a['capability'] and c.get('status')=='supported_conditionally' and c.get('applicable') for c in world.get('capabilities',[])) else 'binding_trial_or_unverified','not_proven_success':True}
            p=plan_candidate(label,actions,execution,goal,[{'frame':state['frame_sha256'],'model_revision':self.memory.revision}]);p['utility']=utility;result.append(p)
        if e.active:
            a=e.active;item=a.get('item');gain=self.effect('consume',item[0]['display_name'],'hp') if item else interval()
            selected=next((v['text'] for v in state.get('execution_ui',{}).get('entries',[]) if v['line']==state.get('execution_ui',{}).get('selected_line')),None)
            executing=item and state.get('execution_ui',{}).get('kind')=='item_actions' and selected in self.policy.get('labels',{}).get(item[1].get('command'),[])
            continuation=action('equip' if a['kind']=='equip' else 'consume',{'item_name':item[0]['display_name']},self.turns('consume'),effects={'hp_gain':gain} if item[1].get('metric')=='hp' else {},costs={'items':int(a['kind']!='equip')}) if executing else action('interface',{'task':a['kind']},self.turns('interface'),effects={'information':.2})
            remaining=[continuation]
            if a['kind']=='review':remaining.extend(action('inspect',{},self.turns('interface'),effects={'information':.2}) for _ in range(2))
            utility=self.rules().get('item_values',{}).get(a['kind'],3.)
            if a['kind']=='heal' and hp:utility*=max(0,1-hp[0]/hp[1])
            add('Continue selected procedure',remaining,{'kind':'continue'}, {'kind':'recover' if a['kind']=='heal' else 'procedure'},utility)
            add('Abort and re-evaluate',[action('interface',{'abort':True},self.turns('interface'))],{'kind':'abort'}, {'kind':'reassess'},-1.)
            if len(result)>1 and hp and hp[0]<hp[1]*.25 and a['kind']=='review':result[1]['utility']=12.
            if len(result)>1 and a['kind']=='heal' and hp and hp[0]>=hp[1]:result[1]['utility']=5.
            if not a.get('sent') and a.get('stage') not in ('verify','closing'):
                for purpose in ('heal','food'):
                    alternative=e.known_item(purpose)
                    if not alternative or a['kind']==purpose or not e.eligible(e.task_token(purpose,alternative),step):continue
                    metric='hp' if purpose=='heal' else 'fullness';effect={'hp_gain':self.effect('consume',alternative[0]['display_name'],metric)} if purpose=='heal' else {'resource_gain':self.effect('consume',alternative[0]['display_name'],metric)}
                    u=self.rules().get('item_values',{}).get(purpose,1.)
                    if purpose=='heal' and hp:u*=max(0,1-hp[0]/hp[1])
                    if purpose=='food' and food:u*=max(0,1-food[0]/100)
                    add('Switch means while preserving objective',[action('interface',{},self.turns('interface')),action('consume',{'item_name':alternative[0]['display_name']},self.turns('consume'),effects=effect,costs={'items':1})],{'kind':'item','purpose':purpose,'item':copy.deepcopy(alternative)},{'kind':'recover' if purpose=='heal' else 'resource'},u)
            return result
        if position is None or not allows_field_input(state) or view.get('status')!='ready':return result
        cells={coord(p) for p in world['cells']};blocked={coord(p) for p in world['blocked']};occupied={coord(a['position']) for a in actors};paths=paths_from(coord(position),cells,world['diagonal'],blocked|occupied,512)
        dungeon=view.get('provider')=='measured' and state.get('floor_confirmation',{}).get('confirmed') is not None
        horizon=self.rules().get('horizon',5)
        # All reachable local relocations compete with attack/recovery, not a
        # preselected retreat. Include routes to bottlenecks beyond one step.
        destinations=[]
        for dest,path in paths.items():
            if len(path)<=1:continue
            degree=len(list(neighbors(dest,cells,False,blocked)));distance=len(path)-1
            if distance<=1 or actors and degree<=2 and distance<=horizon:destinations.append((degree>2,distance,dest,path))
        for _,_,dest,path in sorted(destinations)[:10] if actors else []:
            acts=[action('move',{'destination':list(p)},self.turns('move'),effects={'position':list(p)},preconditions=['known_adjacent_floor']) for p in path[1:horizon+1]]
            add('Relocate through known terrain',acts,{'kind':'path','path':[list(p) for p in path],'destination':list(dest)}, {'kind':'tactical_position','cell':list(dest)},self.rules().get('position_value',0.))
        for actor in actors:
            if dist(actor['position'],position)>1:continue
            delta=[actor['position'][i]-position[i] for i in (0,1)];actions=[]
            if tuple(delta)!=e.facing:actions.append(action('face',{'target_id':actor['id'],'direction':delta},self.turns('face'),preconditions=['target_still_adjacent']))
            actions.extend(action('strike',{'target_id':actor['id']},self.turns('strike'),effects={'damage':actor['outgoing_damage'],'damage_on_hit':actor['damage_on_hit'],'hit_probability':actor['hit_probability']},preconditions=['target_still_adjacent','facing_target'],uncertainty=['hit_or_miss']) for _ in range(max(1,horizon-len(actions))))
            add('Engage tracked adjacent actor',actions,{'kind':'combat','target_id':actor['id'],'cell':actor['position']},{'kind':'handle_target','target_id':actor['id']},self.rules().get('combat_value',4.))
        if dungeon and (hp and hp[0]<hp[1] or any(dist(a['position'],position)==2 for a in actors)):
            gain=known(0,'HP_CAP_CONSTRAINT') if hp and hp[0]>=hp[1] else self.effect('rest',None,'hp')
            add('Advance one turn and observe',[action('rest',{},self.turns('rest'),effects={'hp_gain':gain,'information':.2}) for _ in range(1 if actors else min(3,horizon))],{'kind':'rest'},{'kind':'recover'},2. if hp and hp[0]<hp[1] and not actors else self.rules().get('observe_turn_value',.5))
        purposes=('heal','food','emergency_food','growth','restore_strength','ranged','identify') if dungeon else ()
        for purpose in purposes:
            item=e.known_item(purpose)
            if not item:continue
            if purpose=='growth':
                strength=e.values(state,'strength')
                if not strength or strength[0]!=strength[1]:continue
            if purpose=='restore_strength':
                strength=e.values(state,'strength')
                if not e.known_item('growth') or not strength or strength[0]>=strength[1]:continue
            if purpose=='ranged':
                targets=[a for a in actors if dist(a['position'],position)>1 and e.clear_ray(state,{'delta':[a['position'][i]-position[i] for i in (0,1)],'distance':dist(a['position'],position)})]
                if not targets:continue
            if purpose=='identify' and not any(r.get('identification_required') for r in e.rows()):continue
            if not e.eligible(e.task_token(purpose,item),step):continue
            metric=item[1].get('metric');effect={'information':.5 if purpose=='ranged' else 0.}
            if metric=='hp':effect['hp_gain']=self.effect('consume',item[0]['display_name'],metric)
            elif metric=='fullness':effect['resource_gain']=self.effect('consume',item[0]['display_name'],metric)
            elif metric:effect['metrics']={metric:self.effect('consume',item[0]['display_name'],metric)}
            utility=self.rules().get('item_values',{}).get(purpose,1.)
            if purpose=='heal' and hp:utility*=max(0,1-hp[0]/hp[1])
            if purpose in ('food','emergency_food') and food:utility*=max(0,1-food[0]/100)
            actions=[action('interface',{'task':purpose},self.turns('interface'),effects={'information':.1}),action('consume',{'item_name':item[0]['display_name']},self.turns('consume'),effects=effect,costs={'items':1})]
            execution={'kind':'item','purpose':purpose,'item':copy.deepcopy(item)}
            if purpose=='ranged':
                execution['target_id']=targets[0]['id'];execution['cell']=targets[0]['position']
                delta=[targets[0]['position'][i]-position[i] for i in (0,1)];direction=[(1 if n>0 else -1 if n<0 else 0) for n in delta]
                if tuple(direction)!=e.facing:actions.insert(0,action('face',{'target_id':targets[0]['id'],'direction':direction},self.turns('face')))
            if purpose=='identify':execution['identify_target']=next(r for r in e.rows() if r.get('identification_required'))
            add('Use observed resource',actions,execution,{'kind':'recover' if purpose=='heal' else 'resource'},utility)
        for kind,item in ([('equip',e.upgrade(state)),('discard',e.discard_candidate())] if dungeon else []):
            if item and e.eligible(e.task_token(kind,item),step):
                add('Change observed equipment or capacity',[action('interface',{'task':kind},self.turns('interface')),action(kind,{'item_name':item[0]['display_name']},self.turns(kind),effects={'metrics':{item[1].get('metric','capacity'):interval()}},costs={'items':int(kind=='discard')})],{'kind':'item','purpose':kind,'item':copy.deepcopy(item)},{'kind':'equipment'},self.rules().get('equipment_value',3.))
        at_item=[0,0] in state.get('minimap',{}).get('items',[]) and e.item_checks.get(str(position),-1)<=step
        needs=dungeon and (at_item or e.review_needed or step-e.last_review>=self.policy.get('review_period',128))
        if needs and e.eligible(e.task_token('review',None),step):
            add('Acquire missing status and possession evidence',[action('inspect',{},self.turns('interface'),effects={'information':2.})],{'kind':'review','at_item':at_item},{'kind':'inspect'},self.rules().get('review_value',2.))
        targets=view.get('targets',[])
        wanted={'acquire':('item',),'find_exit':('stairs','portal'),'transition':('stairs','portal'),'handle_target':('enemy',)}.get(local.get('capability'))
        for t in sorted(targets,key=lambda t:(-(self.spatial_policy.get('priorities',{}).get(t['kind'],1)),t['distance']))[:12]:
            if wanted and t['kind'] not in wanted and local.get('conditions'):continue
            if t['kind']=='enemy':continue
            if len(t['path'])<2:
                if t['kind'] in ('stairs','portal','obstacle') and e.eligible('interaction:'+t['id'],step):
                    add('Probe a bounded transition or obstacle affordance',[action('interact',{'target':t['id']},self.turns('interact'),effects={'information':.5},uncertainty=['transition_effect_unverified'])],{'kind':'interact','target':copy.deepcopy(t)},{'kind':t['kind'],'cell':t['cell']},self.spatial_policy.get('priorities',{}).get(t['kind'],1))
                continue
            steps=[action('move',{'destination':p},self.turns('move'),effects={'position':p},preconditions=['known_path_or_frontier_probe']) for p in t['path'][1:horizon+1]]
            # Unknown frontier destination remains a probe, not proven floor.
            if t['kind']=='frontier' and coord(t['path'][-1]) not in cells:
                for a in steps:
                    if a['parameters']['destination']==t['path'][-1]:a['kind']='probe';a['effects']={'information':1.};a['uncertainty']=['unknown_passability']
            utility=self.spatial_policy.get('priorities',{}).get(t['kind'],1)*self.rules().get('spatial_value',2.)
            if t['kind']=='frontier':utility*=t['confidence']
            add('Reach spatial goal',steps,{'kind':'spatial','target':copy.deepcopy(t)}, {'kind':t['kind'],'cell':t['cell'],'parent':local.get('id')},utility)
        return result
