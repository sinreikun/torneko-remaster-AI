"""Bounded session-local settling margin for safe map-policy movement only."""
import math
from collections import deque


class AdaptivePacing:
    def __init__(self,max_wait_ms):
        self.cap=max_wait_ms;self.space=None;self.wait=min(600,self.cap);self.samples=deque(maxlen=12);self.streak=0

    def apply(self,decision,state,choice,stable):
        if not (stable and state and state.get('eligible') and choice and choice.get('source')=='learned_policy'
                and decision.keys in (['w'],['a'],['s'],['d']) and decision.risk=='normal'
                and state.get('objective')!='survive' and not state['minimap'].get('enemies')):return None
        space=(tuple(state['metrics'].get('floor',{}).get('values',[])),
            tuple(state['minimap'].get('source_size',[])),state['minimap'].get('cell_size'))
        if space!=self.space:
            self.space=space;self.wait=min(600,self.cap);self.samples.clear();self.streak=0
        decision.wait_ms=self.wait
        return {'wait_ms':self.wait,'samples':len(self.samples),'stable_streak':self.streak,
            'minimum_ms':min(400,self.cap),'maximum_ms':min(2000,self.cap),
            'margin_ms':250,'scope':'safe_map_movement_only','space':space}

    def record(self,trial,row,observation):
        if not trial:return None
        if not row.get('executed') or row.get('dry_run') or (row.get('input_receipt') or {}).get('os_submission')!='accepted':
            return {'status':'not_updated','next_wait_ms':self.wait}
        before=row.get('policy_state') or {};after=row.get('policy_after') or {}
        hp0=before.get('metrics',{}).get('hp',{}).get('values');hp1=after.get('metrics',{}).get('hp',{}).get('values')
        damage=bool(hp0 and hp1 and hp0[1]==hp1[1] and hp1[0]<hp0[0])
        learning=row.get('policy_learning') or {};activity=getattr(observation,'last_activity_ms',None)
        stable=bool(observation.kind=='normal' and type(activity) is int and activity>=0 and after.get('eligible')
            and not after['minimap'].get('enemies') and not damage and learning.get('status')=='trained'
            and learning.get('net_signals',{}).get('position',0)>0
            and before.get('metrics',{}).get('floor',{}).get('values')==after.get('metrics',{}).get('floor',{}).get('values'))
        if not stable:
            self.wait=min(self.cap,2000,max(1200,self.wait+300));self.streak=0;self.samples.clear()
            return {'status':'margin_restored','next_wait_ms':self.wait,'reason':'unsettled_missing_changed_context_damage_or_no_motion_not_backend_failure'}
        self.samples.append(activity);self.streak+=1
        ordered=sorted(self.samples);p90=ordered[max(0,math.ceil(len(ordered)*.9)-1)]
        target=min(self.cap,2000,max(400,p90+250,activity+250))
        if target>self.wait:self.wait=target;self.streak=0
        elif self.streak>=6:self.wait=max(target,self.wait-50);self.streak=0
        return {'status':'stable_sample','last_activity_ms':activity,'p90_activity_ms':p90,'next_wait_ms':self.wait,
            'note':'余裕250msと既存の静止判定を維持。無反応は非対応・故障の判定にしない。'}
