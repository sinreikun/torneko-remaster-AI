"""One empirical forward model, prospective scoring and information-directed policy."""
import copy,json,math,time,re
from collections import Counter,OrderedDict,deque
from .state import digest,effects


def action_id(keys,hold):return json.dumps([list(keys),hold],separators=(',',':'))
def signature(effect,destination_context=None):
    # Missing features are absent; never substitute zero for missing OCR.
    return digest({'effects':{k:round(v,1) for k,v in effect.items() if v is not None},'destination_context':destination_context})
def snapshot(state):
    return {k:copy.deepcopy(v) for k,v in state.items() if k not in ('timing','retained','visual_vector')}
def value_at(state,path):
    if path=='node':return state['id']
    if path=='text':return '\n'.join(state['quotes'])
    if path.startswith('metric.'):
        try:
            _,name,index=path.split('.');return state['metrics'][name]['values'][int(index)]
        except (ValueError,KeyError,IndexError):return None
    return None

def target_score(state,target):
    value=value_at(state,target['feature'])
    if value is None:return None
    wanted=target['value'];op=target['operator']
    try:return float(wanted in value if op=='contains' and isinstance(value,str) else value==wanted if op=='eq' else value>=wanted if op=='ge' else value<=wanted if op=='le' else False)
    except TypeError:return None

class ForwardModel:
    def __init__(self,store):
        self.store=store;self.rules=OrderedDict();self.visits=Counter();self.recent=deque(maxlen=36);self.pending=deque(maxlen=8);self.edges={};self.hypotheses=[];self.checks=deque(maxlen=128)
        self.escape=None;self.serial=0;self.last_goal=None
        for rule in reversed(store.load('rules')):self.rules[rule['key']]=rule
        self.hypotheses=store.load('hypotheses',limit=32)
        for node,count in store.reader.execute('SELECT key,visits FROM states WHERE profile=? ORDER BY updated DESC LIMIT 2048',(store.profile,)):self.visits[node]=count
        for (raw,) in store.reader.execute('SELECT data FROM graph WHERE profile=? ORDER BY updated DESC LIMIT 2048',(store.profile,)):
            e=json.loads(raw);self.edges[(e['origin'],e['action'],e['destination'])]=e
    def _key(self,state,action):return digest([state['context'],action])
    def rule(self,state,action):
        def get(key,condition=None):
            if key not in self.rules:
                rows=self.store.load('rules',key)
                self.rules[key]=rows[0] if rows else {'key':key,'context':state['context'],'action':action,'condition_node':condition,'samples':[],'checks':[],'revisions':0}
            self.rules.move_to_end(key)
            while len(self.rules)>512:self.rules.popitem(last=False)
            return self.rules[key]
        base=get(self._key(state,action))
        # Counterexamples split a broad condition by observed node, rather than
        # treating an old association as immutable or erasing contradictory data.
        if base['revisions']>=3 and base['checks'] and sum(base['checks'][-6:])/len(base['checks'][-6:])<.5:
            return get(digest([state['context'],state['id'],action]),state['id'])
        return base
    def predict(self,state,action):
        rule=self.rule(state,action);samples=rule['samples'];counts=Counter();means={};weights=0.
        for age,item in enumerate(reversed(samples)):
            weight=.92**age*item['confidence'];counts[item['signature']]+=weight;weights+=weight
            for key,value in item['effects'].items():
                if value is not None:
                    v=means.setdefault(key,[0.,0.]);v[0]+=value*weight;v[1]+=weight
        probs={key:v/max(weights,1e-8) for key,v in counts.items()};certainty=max(probs.values(),default=0.)
        entropy=-sum(p*math.log(max(p,1e-9)) for p in probs.values());uncertainty=min(1.,1/(1+len(samples)/3)+.65*(1-certainty))
        return {'action':action,'rule_key':rule['key'],'trials':len(samples),'probabilities':probs,'expected':{k:v[0]/v[1] for k,v in means.items()},
          'uncertainty':uncertainty,'entropy':entropy,'confidence':certainty*min(1,len(samples)/6),'outcomes':[{'node':x['node'],'effects':x['effects'],'outcome':x.get('outcome')} for x in samples[-8:]],
          'prequential_accuracy':sum(rule['checks'][-16:])/len(rule['checks'][-16:]) if rule['checks'] else None}
    def candidates(self,cfg):
        keys=list(dict.fromkeys(cfg.allowed_keys));chords=[]
        # Chords come only from the user's direction bindings, not game names.
        directions={}
        for k,label in cfg.key_bindings.items():
            for name,terms in {'up':('上移動','up'),'down':('下移動','down'),'left':('左移動','left'),'right':('右移動','right')}.items():
                if label.casefold() in terms:directions[name]=k
        for a,b in (('up','left'),('up','right'),('down','left'),('down','right')):
            if a in directions and b in directions:chords.append([directions[a],directions[b]])
        modifiers=[k for k,label in cfg.key_bindings.items() if re.search(r'方向転換|補助|modifier',label,re.I) and k in keys][:4]
        chords.extend([[m,d] for m in modifiers for d in directions.values() if m!=d])
        # At most three duration hypotheses per input. Time is part of the action.
        holds=sorted({min(cfg.max_hold_ms,x) for x in (80,140,220)})
        return [{'id':action_id(k,h),'keys':k,'hold_ms':h} for k in [[x] for x in keys]+chords for h in holds]
    def choose(self,state,candidates,plan=None,weight=.6):
        started=time.monotonic();plan=plan or {};ranked=[];self.prediction_ms=0.;self.search_ms=0.
        goals=plan.get('targets',[]);current_goal=[target_score(state,t) for t in goals]
        loop_actions=set(self.escape.get('actions',[])) if self.escape else set()
        for action in candidates:
            lookup=time.monotonic();self.rule(state,action['id']);self.search_ms+=(time.monotonic()-lookup)*1000
            t=time.monotonic();p=self.predict(state,action['id']);self.prediction_ms+=(time.monotonic()-t)*1000;samples=p['outcomes'];novelty=0.;gain=0.;loss=0.;goal=0.
            if samples:
                novelty=sum(1/(1+self.visits[x['node']]) for x in samples)/len(samples)
                gain=sum(x['node'] not in self.visits for x in samples)/len(samples)
                loss=max(0.,-p['expected'].get('metric.hp.0',0))/max(1,state['metrics'].get('hp',{}).get('values',[10])[0])
                for i,target in enumerate(goals):
                    feature=target['feature']
                    if feature.startswith('metric.') and current_goal[i] is not None:
                        value=value_at(state,feature);predicted=value+p['expected'].get(feature,0)
                        temporary=copy.deepcopy(state);_,name,index=feature.split('.');temporary['metrics'][name]['values'][int(index)]=predicted
                        later=target_score(temporary,target)
                        if later is not None:goal+=target.get('weight',1)*(later-current_goal[i])
                    if feature in ('text','node'):
                        observed=[target_score(x['outcome'],target) for x in samples if x.get('outcome')]
                        if observed:goal+=target.get('weight',1)*(sum(v for v in observed if v is not None)/len(observed)-(current_goal[i] or 0))
            else:novelty=1.;gain=1.
            prior=next((x['value'] for x in plan.get('candidate_values',[]) if x['id']==action['id'] or x.get('keys')==action['keys']),0.)
            attempts=sum(x['before']==state['id'] and x['action']==action['id'] for x in self.recent)
            info=p['uncertainty']/math.sqrt(1+p['trials'])
            # Predictability reduces exploration; surprise raises it in update().
            score=goal*2+weight*(info+.25*novelty+.2*gain)+prior*.5-loss*2-.12*min(8,attempts)-action['hold_ms']*.0001
            if self.escape and action['id'] in loop_actions:score-=5
            ranked.append({'candidate':action,'prediction':p,'score':score,'information_gain_proxy':info,'novelty':novelty,'expected_goal_value':goal,'observed_loss_risk':loss,'recent_attempts':attempts})
        ranked.sort(key=lambda x:(-x['score'],x['candidate']['id']))
        best=ranked[0]
        # Bounded lookahead on observed graph only. Transit through known nodes is
        # allowed; it is not penalized merely because a floor was visited.
        if not self.escape and not state['interface_hypothesis']:
            route=self.route(state['id'],goals)
            if route:
                alternate=next((x for x in ranked if x['candidate']['id']==route),None)
                if alternate and alternate['observed_loss_risk']<.2:best=alternate;best['route_to_information']=True
        return best,ranked[:12],(time.monotonic()-started)*1000
    def route(self,origin,goals):
        desired={g['value'] for g in goals if g['feature']=='node' and g['operator']=='eq'}
        queue=deque([(origin,None,0)]);seen={origin}
        while queue:
            node,first,depth=queue.popleft()
            if depth and (node in desired or self.visits[node]<2):return first
            if depth>=4:continue
            for (a,action,b),data in self.edges.items():
                if a!=node or b in seen or data.get('confidence',0)<.6:continue
                seen.add(b);queue.append((b,first or action,depth+1))
        return None
    def observe(self,before,action,after,prediction,receipt,step,neutral=None,kind='normal',natural_confidence=.5):
        if receipt.get('os_submission')!='accepted' or kind=='aborted':
            self.pending.clear();return {'status':'censored','reason':'delivery_or_observation_incomplete'}
        effect=effects(before,after);sig=signature(effect,after['context']);rule=copy.deepcopy(self.rule(before,action['id']))
        comparable=before['quality']>=.4 and after['quality']>=.4
        probability=prediction['probabilities'].get(sig,0);match=None if not prediction['trials'] or not comparable else sig==max(prediction['probabilities'],key=prediction['probabilities'].get)
        surprise=None if match is None else min(8.,-math.log(max(.001,probability)))
        confidence=min(before['quality'],after['quality'])*natural_confidence
        if neutral:
            # Natural differences confound attribution; retain raw effects too.
            active=sum(bool(v) for v in effect.values());background=sum(bool(v) for v in neutral.values())
            confidence*=max(.2,1-background/max(1,active+background))
        rule['samples'].append({'signature':sig,'effects':effect,'node':after['id'],'confidence':confidence,'outcome':{'id':after['id'],'quotes':after['quotes'][:12],'metrics':copy.deepcopy(after['metrics'])}});rule['samples']=rule['samples'][-32:]
        if match is not None:
            rule['checks'].append(int(match));rule['checks']=rule['checks'][-64:]
            if not match:rule['revisions']+=1
        rule['status']='under_review' if match is False else 'supported_conditionally' if len(rule['checks'])>=6 and sum(rule['checks'][-8:])/len(rule['checks'][-8:])>=.75 else 'candidate'
        self.rules[rule['key']]=rule;self.rules.move_to_end(rule['key'])
        while len(self.rules)>512:self.rules.popitem(last=False)
        self.store.put('rules',rule,key=rule['key']);self.visits[before['id']]+=1;new=after['id'] not in self.visits;self.visits[after['id']]+=1
        edge={'origin':before['id'],'action':action['id'],'destination':after['id'],'confidence':min(before['quality'],after['quality']),'causal_confidence':confidence,'effects':effect,'observed_count':self.edges.get((before['id'],action['id'],after['id']),{}).get('observed_count',0)+1}
        self.edges[(before['id'],action['id'],after['id'])]=edge
        if len(self.edges)>2048:self.edges.pop(next(iter(self.edges)))
        self.store.put('graph',edge)
        self.store.put('states',{**snapshot(after),'visual_vector':after.get('visual_vector')},step,key=after['id'])
        delayed=[]
        for pending in list(self.pending):
            horizon=step-pending['step']
            if horizon in (2,4,8):
                delay={'source_step':pending['step'],'step':step,'horizon':horizon,'effects':effects(pending['before'],after),
                  'intervening_inputs':horizon,'confidence':confidence/(1+horizon),'attribution':'temporal_association_with_intervening_inputs_not_proven_cause'}
                delay['persistent_features']=[k for k,v in pending['immediate'].items() if v not in (None,0) and delay['effects'].get(k)==v]
                delay['reverted_features']=[k for k,v in pending['immediate'].items() if v not in (None,0) and delay['effects'].get(k)==0]
                delayed.append(delay);self.store.put('delayed',delay,step)
        self.pending.append({'step':step,'before':snapshot(before),'action':action['id'],'immediate':copy.deepcopy(effect)})
        event={'before':before['id'],'after':after['id'],'action':action['id'],'step':step}
        meaningful=new or any(v for k,v in effect.items() if k.startswith('metric.') or k=='floor_changed')
        self.recent.append(event);loop=None
        if self.escape:
            self.escape['attempts']+=1
            if after['id'] not in self.escape['nodes'] or meaningful or self.escape['attempts']>=8:
                loop={'event':'loop_escape','id':self.escape['id'],'reason':'new_observed_outcome' if meaningful or after['id'] not in self.escape['nodes'] else 'attempt_budget_exhausted','attempts':self.escape['attempts'],'learning':False}
                self.escape=None;self.recent.clear()
        elif not meaningful:
            seq=list(self.recent)
            for period in (1,2,3,4,6):
                n=period*(6 if period==1 else 3)
                if len(seq)<n:continue
                values=[(e['before'],e['action'],e['after']) for e in seq[-n:]]
                if all(values[i]==values[-period+i%period] for i in range(n)):
                    self.serial+=1;loop={'event':'loop_detected','id':self.serial,'period':period,'steps':[e['step'] for e in seq[-n:]],'sequence':seq[-n:],'nodes':list({e['before'] for e in seq[-n:]}),'actions':list({e['action'] for e in seq[-n:]}),'attempts':0,'learning':False}
                    self.escape=copy.deepcopy(loop);break
        if meaningful and not self.escape:self.recent.clear()
        # Hypotheses are prospective, explicit, and scoped. No caption becomes a fact.
        hypothesis_checks=[]
        for hypothesis in self.hypotheses:
            if hypothesis['context']!=before['context'] or hypothesis['candidate']!=action['id']:continue
            feature=hypothesis['feature']
            if feature not in effect or effect[feature] is None:continue
            supported=abs(effect[feature]-hypothesis['expected'])<=hypothesis['tolerance']
            hypothesis['support' if supported else 'counterexamples']+=confidence
            hypothesis['status']='contradicted' if not supported else 'supported_association'
            hypothesis['confidence']=(hypothesis['support']+1)/(hypothesis['support']+hypothesis['counterexamples']+2)
            self.store.put('hypotheses',hypothesis,key=hypothesis['id']);hypothesis_checks.append(copy.deepcopy(hypothesis))
        result={'status':'updated','effects':effect,'match':match,'surprise':surprise,'new_state':new,'uncertainty':prediction['uncertainty'],'information_gain':max(0,prediction['uncertainty']-self.predict(before,action['id'])['uncertainty']),
          'confidence':confidence,'attribution':'observed_association_not_proven_cause','rule_status':rule['status'],'condition_split_node':rule.get('condition_node'),'revisions':rule['revisions'],'loop':loop,'delayed':delayed,'hypothesis_checks':hypothesis_checks}
        self.checks.append({'match':match,'new_state':new,'loop':bool(loop and loop['event']=='loop_detected')})
        self.store.put('evaluation',result,step)
        return result
    def add_hypotheses(self,proposals,state,candidates):
        valid={a['id'] for a in candidates}
        for proposal in proposals:
            if proposal['candidate'] not in valid:continue
            identifier=digest([state['context'],proposal]);existing=next((x for x in self.hypotheses if x['id']==identifier),None)
            if existing:continue
            h={**proposal,'id':identifier,'context':state['context'],'support':0,'counterexamples':0,'confidence':.5,'status':'llm_hypothesis_unverified'}
            self.hypotheses.append(h);self.hypotheses=self.hypotheses[-32:];self.store.put('hypotheses',h,key=identifier)

class AdaptivePolicy:
    """Compare bounded exploration-weight trials; rollback on measured worsening."""
    def __init__(self):self.weight=.6;self.samples=deque(maxlen=24);self.trial=None;self.turns=0
    def update(self,result):
        self.turns+=1
        loss=result.get('effects',{}).get('metric.hp.0',0)<0
        sample={'utility':float(result.get('new_state',False))+.5*float(result.get('match') is True)-float(bool(result.get('loop'))),'loss':loss}
        self.samples.append(sample)
        if self.trial:
            self.trial['samples'].append(sample)
            if loss or len(self.trial['samples'])>=12:
                score=sum(x['utility'] for x in self.trial['samples'])/len(self.trial['samples'])
                accepted=not loss and score>self.trial['baseline']+.05
                event={'event':'parameter_trial','parameter':'exploration_weight','old':self.trial['old'],'tested':self.weight,'baseline':self.trial['baseline'],'measured':score,'accepted':accepted,'reason':'observed_loss_rollback' if loss else 'window_comparison','causality':'uncontrolled_short_trial'}
                if not accepted:self.weight=self.trial['old']
                self.trial=None;return event
        elif self.turns%48==0 and len(self.samples)>=12:
            self.trial={'old':self.weight,'baseline':sum(x['utility'] for x in self.samples)/len(self.samples),'samples':[]};self.weight=.9 if self.weight<.8 else .45
        return None
