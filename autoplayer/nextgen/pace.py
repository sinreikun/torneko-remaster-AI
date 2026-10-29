"""Finite runtime wait experiments. Quality proxies are not gameplay proof."""
from collections import OrderedDict,deque
import time

class PaceController:
    target_ms=1500
    def __init__(self):self.profiles=OrderedDict();self.elapsed=deque(maxlen=32);self.last={};self.tick=0
    def profile(self,key,baseline):
        if key not in self.profiles:self.profiles[key]={'wait':baseline,'samples':deque(maxlen=8),'trial':None,'cooldown':0,'failed_floor':-1}
        self.profiles.move_to_end(key)
        while len(self.profiles)>256:self.profiles.popitem(last=False)
        p=self.profiles[key]
        if p['trial'] and (self.tick>=p['trial']['deadline_tick'] or time.monotonic()>=p['trial']['deadline_time']):
            p['trial']=None;p['cooldown']=16
        return p
    def wait(self,key,baseline):
        p=self.profile(key,baseline)
        return p['trial']['wait'] if p['trial'] else p['wait']
    def invalidate_context(self,context):
        count=0
        for key,p in self.profiles.items():
            if isinstance(key,tuple) and key[0]==context and p['trial']:
                p['failed_floor']=max(p['failed_floor'],p['trial']['wait']);p['trial']=None;p['cooldown']=16;count+=1
        return count
    def observe(self,key,baseline,*,ms,quality,match,kind,accepted=True,reobserved=False):
        if not accepted:return None
        self.tick+=1;p=self.profile(key,baseline);self.elapsed.append(ms)
        sample={'ms':ms,'quality':quality,'prediction':int(match is True) if match is not None else None}
        reliable=quality>=.7 and kind in ('normal','no_change') and not reobserved
        event=None
        if p['trial']:
            t=p['trial'];t['samples'].append(sample)
            if not reliable or match is False:
                p['failed_floor']=max(p['failed_floor'],t['wait']);p['trial']=None;p['cooldown']=16
                event={'status':'rollback','reason':'quality_prediction_or_revalidation','wait_ms':p['wait']}
            elif len(t['samples'])>=8:
                old=t['baseline'];new=sum(x['ms'] for x in t['samples'])/len(t['samples'])
                old_quality=sum(x['quality'] for x in old)/len(old)
                if new<sum(x['ms'] for x in old)/len(old) and quality>=old_quality-.05:
                    p['wait']=t['wait'];event={'status':'accepted','wait_ms':p['wait']}
                else:event={'status':'rollback','reason':'no_measured_improvement','wait_ms':p['wait']}
                p['trial']=None;p['cooldown']=8;p['samples'].clear()
        else:
            p['cooldown']=max(0,p['cooldown']-1)
            if reliable and match is not False:p['samples'].append(sample)
            else:p['samples'].clear()
            if len(p['samples'])==8 and not p['cooldown']:
                candidate=max(0,int(p['wait']*.8)-10,p['failed_floor']+1)
                if candidate<p['wait']:
                    p['trial']={'wait':candidate,'baseline':list(p['samples']),'samples':[],'deadline_tick':self.tick+64,'deadline_time':time.monotonic()+120}
                    event={'status':'trial_started','old_wait_ms':p['wait'],'wait_ms':candidate,'deadline_samples':8}
        self.last={'target_ms':self.target_ms,'target_turns_per_minute':40,'rolling_turns_per_minute':60000/(sum(self.elapsed)/len(self.elapsed)) if self.elapsed and sum(self.elapsed)>0 else None,'wait_ms':p['wait'],'trial_wait_ms':p['trial']['wait'] if p['trial'] else None,'basis':'observational_quality_and_latency_proxies','throughput_limited':bool(ms>=self.target_ms)}
        return event
