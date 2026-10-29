"""Bounded lookahead over observed transitions, never imagined game states."""
from collections import Counter,deque
from dataclasses import replace
import copy,json

CONDITIONS=('level','strength','sword','shield')

class LearnedPredictions:
    def __init__(self,policy):
        self.policy=policy;self.events=deque(maxlen=1024)
        rows=policy.db.execute('SELECT data FROM policy_events WHERE profile=? ORDER BY id DESC LIMIT 1024',(policy.profile,)).fetchall()
        for row in reversed(rows):
            try:self.add(json.loads(row[0]))
            except (ValueError,TypeError):continue
    def add(self,event):
        if event.get('status')=='trained' and event.get('objective') in ('explore','forage','survive') and event.get('state') and event.get('next_state') and len(event.get('keys',[]))==1:
            self.events.append(copy.deepcopy(event))
    def groups(self,state):
        groups={}
        conditions={key:state.get('metrics',{}).get(key,{}).get('values') for key in CONDITIONS}
        for event in self.events:
            if {key:event.get('conditions',{}).get(key) for key in CONDITIONS}!=conditions:continue
            key=(event['state'],event['objective'],tuple(event['keys']))
            groups.setdefault(key,deque(maxlen=32)).append(event)
        return groups
    def _estimate(self,groups,state,objective,depth,seen):
        estimates=[]
        for (origin,goal,keys),events in groups.items():
            if origin!=state or goal!=objective or len(events)<2:continue
            rewards=[e['reward'] for e in events];future=0.;used_depth=1
            for successor,count in Counter(e['next_state'] for e in events).items():
                if depth>1 and successor not in seen:
                    following=self._estimate(groups,successor,goal,depth-1,seen|{successor})
                    if following:
                        best=max(following,key=lambda p:p['expected_return'])
                        future+=count/len(events)*best['expected_return'];used_depth=max(used_depth,1+best['supported_horizon'])
            deltas=[tuple(e['player_delta']) for e in events if e.get('player_delta') is not None]
            dominant,count=Counter(deltas).most_common(1)[0] if deltas else (None,0)
            hp=[e['hp_delta'] for e in events if e.get('hp_delta') is not None]
            estimates.append({'keys':list(keys),'trials':len(events),'mean_reward':sum(rewards)/len(rewards),
                'reward_range':[min(rewards),max(rewards)],'expected_return':sum(rewards)/len(rewards)+.6*future,
                'supported_horizon':used_depth,'requested_horizon':depth,'player_delta':list(dominant) if dominant is not None else None,
                'delta_samples':len(deltas),'delta_agreement':count/len(deltas) if deltas else None,
                'hp_delta_range':[min(hp),max(hp)] if hp else None,'hp_samples':len(hp),
                'next_states_observed':len({e['next_state'] for e in events}),
                'note':'同じ観測状態の実測関連性。未観測の先は価値0で打ち切り、ループは展開しない。成功確率や因果確定ではない。'})
        return estimates
    def context(self,state):
        if not state or not state.get('eligible'):return {'status':'unavailable','predictions':[]}
        estimates=self._estimate(self.groups(state),state['id'],state['objective'],3,{state['id']})
        return {'status':'observed_transitions' if estimates else 'insufficient_experience','predictions':sorted(estimates,key=lambda p:p['expected_return'],reverse=True)[:8],
            'state':state['id'],'source':'profile_scoped_policy_events','max_horizon':3,'retained_events':len(self.events),
            'instruction':'実測のある候補の結果と数手先の価値を比較する。未知の先は未推定であり安全・成功を仮定しない。現在の画面や作戦指示を優先し、外れた予測は次の観測で修正する。'}
    def choose(self,decision,state,choice,blocked=()):
        context=self.context(state)
        if (choice or {}).get('source')!='learned_policy' or decision.status!='continue' or decision.risk!='normal':return decision,choice,context
        # Keep current route feasibility and revisit penalties. Predictions may
        # compare only actions still offered by the current grounded policy.
        ranking=(choice.get('context') or {}).get('ranking',[])
        candidates=[]
        for prediction in context['predictions']:
            if choice.get('exploration_route') and prediction['keys']!=decision.keys:continue
            rank=next((r for r in ranking if r['keys']==prediction['keys'] and r.get('automatic_candidate')),None)
            if not rank or prediction['keys'] in blocked:continue
            if (choice.get('context',{}).get('no_effect_moves',{}).get(prediction['keys'][0],0)>=2):continue
            score=.65*max(-1,min(1,prediction['expected_return']))+.35*max(-1,min(1,rank.get('q',0)))-self.policy.routes.penalty(state,prediction['keys'][0])
            candidates.append((score,prediction,rank))
        if not candidates:return decision,choice,context
        score,prediction,rank=max(candidates,key=lambda row:row[0])
        # Unknown actions remain available; a negative-only model cannot impose
        # arbitrary old behavior on a new state.
        if score<=.02 or prediction['expected_return']<=0:return decision,choice,context
        expected={}
        if prediction['delta_samples']>=2 and prediction['delta_agreement']>=.75:expected['player_delta']=prediction['player_delta']
        hp=state.get('metrics',{}).get('hp',{}).get('values')
        if hp and prediction['hp_samples']>=3:expected['hp_min']=max(0,hp[0]+min(0,prediction['hp_delta_range'][0])-1)
        chosen=replace(decision,keys=list(prediction['keys']),intent={},forecast={'horizon':1,'first_expected':expected,'continuations':[]},
            reason=f"この観測状態の実測{prediction['trials']}件から、観測済みの最大{prediction['supported_horizon']}手先を比較し、期待評価{prediction['expected_return']:.3f}の入力を選ぶ。次の一手の結果で予測を照合する。",
            subgoal='実測経験から予測した進行を1手ずつ検証する',plan=[],memory_updates=[],progress=[],goal_evidence=[],knowledge_assessments=[],progress_test={})
        context['selected']=prediction
        return chosen,{'source':'learned_outcome_prediction','selected':{**rank,**prediction},'context':choice.get('context',{}),'prediction':prediction,'selection_basis':'observed_transition_lookahead'},context
    def evaluate(self,prediction,event,actual_keys):
        if not prediction or prediction.get('keys')!=actual_keys:return {'status':'not_applied'}
        if event.get('status')!='trained':return {'status':'unverifiable','reason':event.get('reason')}
        lo,hi=prediction['reward_range'];checks=[lo-.1<=event['reward']<=hi+.1]
        if prediction.get('delta_samples',0)>=2 and prediction.get('delta_agreement',0)>=.75:
            if event.get('player_delta') is None:return {'status':'unverifiable','reason':'現在の前後位置を比較できません。'}
            checks.append(event['player_delta']==prediction['player_delta'])
        return {'status':'matched' if all(checks) else 'mismatch','expected':prediction,'actual_reward':event['reward'],
            'actual_player_delta':event.get('player_delta'),'actual_hp_delta':event.get('hp_delta'),
            'note':'評価するのは最初の1手だけ。数手先の攻略成功を確認した扱いにしない。'}
