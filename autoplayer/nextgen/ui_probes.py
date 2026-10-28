"""Finite visual experiments scoped to the measurement being learned.

Old action history is retained; it cannot exhaust a different UI layout's
selection experiment budget. These are observation tasks, not game objectives.
"""
from .state_hash import digest

class UIProbes:
    def __init__(self,attempts=None):self.attempts=dict(attempts or {});self.last=[]
    @staticmethod
    def context(state,policy):
        ui=state.get('ui_visual',{})
        if not state.get('interface_hypothesis') and not ui.get('active'):return None
        if ui.get('selection',{}).get('status')=='supported_association':return None
        if 'selection_text_correspondence_unknown' not in ui.get('needs_observation',[]):return None
        if not ui.get('layout'):return None
        signature=['paired_origin_observer_v1',ui['layout'],ui.get('difference_threshold',18),policy.get('ui_region_ocr',0)]
        if ui.get('interaction_model',{}).get('text_flow_suspended'):signature.append('editable_ui_requires_selection')
        return digest(signature)
    @staticmethod
    def direction(candidate):
        return len(candidate.get('keys',[]))==1 and bool({m.strip().casefold() for m in candidate.get('declared_meanings',[])} & {'up','down','left','right','上移動','下移動','左移動','右移動'})
    def priors(self,state,candidates,policy):
        context=self.context(state,policy);self.last=[]
        if not context:return []
        canonical={}
        for c in candidates:
            if self.direction(c):
                key=tuple(c['keys'])
                if key not in canonical or c.get('hold_ms',0)<canonical[key].get('hold_ms',0):canonical[key]=c
        for keys,c in canonical.items():
            key=digest([context,keys]);trials=self.attempts.get(key,0)
            if trials<2 and (key in self.attempts or len(self.attempts)<256):
                self.last.append({'id':c['id'],'value':1.4-trials*.2,'reason':'現在の文字配置と観測条件で選択移動を有限再検証する。古いUI枠全体の試行回数とは別。','probe_context':context,'probe_trials':trials})
        return self.last
    def observe(self,before,action,result,receipt,policy):
        context=self.context(before,policy)
        if not context or not self.direction(action) or result.get('status')!='updated' or receipt.get('os_submission')!='accepted':return
        key=digest([context,tuple(action['keys'])])
        if key not in self.attempts and len(self.attempts)>=256:return
        self.attempts[key]=min(2,self.attempts.get(key,0)+1)
        # Never evict exhausted experiments and thereby silently restart them.
        # When capacity is full, new contexts use ordinary recovery instead.
    def summary(self):return {'direction_budgets_recorded':len(self.attempts),'capacity':256,'capacity_reached':len(self.attempts)>=256,'last_candidates':self.last,'limit_per_direction':2}
