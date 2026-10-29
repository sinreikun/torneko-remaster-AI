"""Bounded, single-variable runtime observation trials, never source rewriting."""
import copy,time
from collections import deque

DEFAULTS={'full_ocr_period':12,'cache_uses':12,'settle_extra_ms':0,'extra_captures':0,'stale_difference':.12,'ui_region_ocr':0,'ui_diff_threshold':18}
BOUNDS={'full_ocr_period':(1,24),'cache_uses':(0,24),'settle_extra_ms':(0,350),'extra_captures':(0,1),'stale_difference':(.06,.12),'ui_region_ocr':(0,1),'ui_diff_threshold':(8,40)}

class ObserverAdaptation:
    def __init__(self,store):
        self.store=store;self.policy=dict(DEFAULTS);self.samples=deque(maxlen=12);self.trial=None;self.history=[];self.attempted={};self.last_outcome=None
        rows=store.load('observer_policies','active')
        if rows:
            saved=rows[0];self.policy.update(saved.get('policy',{}));self.history=saved.get('history',[])[-16:]
            # A trial interrupted by shutdown is not an adopted improvement.
            if saved.get('trial'):
                self.policy=copy.deepcopy(saved['trial']['old_policy']);self.history.append({'status':'rollback','reason':'interrupted_trial','item':saved['trial']['item']})
        self.save()
    def save(self):self.store.put('observer_policies',{'policy':self.policy,'trial':self.trial,'history':self.history[-16:]},key='active')
    @staticmethod
    def aggregate(samples):
        return {k:sum(s[k] for s in samples if s.get(k) is not None)/max(1,sum(s.get(k) is not None for s in samples)) for k in ('quality','prediction','ms','progress','coverage')}
    def start(self,reason,step):
        if self.trial or len(self.samples)<4 or len({s.get('context') for s in list(self.samples)[-4:]})>1:return False
        options=[('full_ocr_period',1),('cache_uses',0),('extra_captures',1),('settle_extra_ms',200),('stale_difference',.08)]
        if reason=='ui_observation_missing':options=[('ui_region_ocr',1),('ui_diff_threshold',10),('full_ocr_period',1),('extra_captures',1),('cache_uses',0)]
        if reason in ('world_prediction_failure','llm_measurement_mismatch','repetition_stagnation'):options=[options[3],options[0],options[2],options[1],options[4]]
        options=[(item,default) for item,default in DEFAULTS.items() if self.policy[item]!=default]+options
        for item,value in options:
            key=item+':'+str(value)
            if self.policy[item]==value or step-self.attempted.get(key,-10000)<128:continue
            lo,hi=BOUNDS[item]
            if not lo<=value<=hi:continue
            self.attempted[key]=step
            self.trial={'item':item,'value':value,'reason':reason,'context':self.samples[-1].get('context'),'start':step,'deadline':step+8,'wall_deadline':time.monotonic()+45,'old_policy':copy.deepcopy(self.policy),'baseline':self.aggregate(list(self.samples)[-4:]),'samples':[]}
            self.policy[item]=value;self.save();return True
        return False
    def observe(self,state,result,ms,progress,step,coverage=1.):
        ui=state.get('ui_visual',{});flow=ui.get('interaction_model',{})
        context=(flow.get('family') or ui.get('layout'),'text_flow' if flow.get('selection_requirement')=='not_required_for_supported_text_flow' else 'selection_correspondence') if ui and (state.get('interface_hypothesis') or ui.get('active')) else state.get('context')
        sample={'context':str(context),'quality':state.get('quality',0) if state.get('measurement_status')=='ok' else 0.,
                'prediction':float(result['match']) if result.get('match') is not None else None,
                'ms':ms,'progress':float(progress),'coverage':coverage}
        self.samples.append(sample)
        trial=self.trial
        if not trial:return None
        trial['samples'].append(sample)
        old=trial['baseline'];now=self.aggregate(trial['samples'])
        hp_loss=result.get('effects',{}).get('metric.hp.0',0)
        context_changed=trial.get('context')!=sample['context']
        harmful=context_changed or (hp_loss is not None and hp_loss<0) or now['quality']<old['quality']-.15 or now['coverage']<old['coverage']-.15
        due=len(trial['samples'])>=6 or step>=trial['deadline'] or time.monotonic()>=trial['wall_deadline']
        if not harmful and not due:return None
        # Accuracy is only a proxy. Missing comparisons cannot improve it.
        scored=[s for s in trial['samples'] if s['prediction'] is not None]
        better=(now['quality']>old['quality']+.05 or now['coverage']>old['coverage']+.1 or bool(scored) and now['prediction']>old['prediction']+.1 or now['progress']>old['progress']+.1 or now['ms']<old['ms']*.85)
        acceptable=not harmful and now['quality']>=old['quality']-.03 and now['coverage']>=old['coverage']-.03 and (not scored or now['prediction']>=old['prediction']-.1) and now['ms']<=max(300,old['ms']*1.6)
        accepted=due and better and acceptable
        event={'event':'observer_trial','status':'adopted' if accepted else 'rollback','item':trial['item'],'old':trial['old_policy'][trial['item']],'tested':trial['value'],'baseline':old,'measured':now,
               'reason':'observation_context_changed' if context_changed else 'harm_or_information_loss' if harmful else 'bounded_comparison','start':trial['start'],'end':step,'causality':'uncontrolled_proxy_comparison_not_gameplay_proof'}
        if not accepted:self.policy=copy.deepcopy(trial['old_policy'])
        self.history.append(event);self.history=self.history[-16:];self.last_outcome=event;self.trial=None;self.save();self.store.put('observer_trial',event,step)
        return event
    def expire(self):
        if not self.trial or time.monotonic()<self.trial['wall_deadline']:return None
        t=self.trial;self.policy=copy.deepcopy(t['old_policy']);self.trial=None
        event={'event':'observer_trial','status':'rollback','reason':'wall_deadline_no_evaluable_improvement','item':t['item']}
        self.history.append(event);self.history=self.history[-16:];self.last_outcome=event;self.save();self.store.put('observer_trial',event)
        return event
    def effective_policy(self,state=None):
        policy=dict(self.policy)
        flow=(state or {}).get('ui_visual',{}).get('interaction_model',{})
        ui=(state or {}).get('ui_visual',{})
        if ui and (not ui.get('active') or not ui.get('needs_observation') or flow.get('selection_requirement')=='not_required_for_supported_text_flow'):policy['ui_region_ocr']=0
        return policy
    def summary(self):return {'settings':dict(self.policy),'trial':{k:v for k,v in self.trial.items() if k not in ('samples','old_policy')} if self.trial else None,'last_comparison':self.last_outcome or (self.history[-1] if self.history else None)}
