"""Read-only experience prefetch overlapped with frame recognition.

Only the controller merges results; the worker cannot mutate the world model.
"""
import json,sqlite3,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from .state_hash import digest

class RulePrefetch:
    def __init__(self,path,profile):
        self.path=Path(path).resolve();self.profile=profile;self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='experience-reader');self.pending=None;self.context=None
    @staticmethod
    def keys(state,candidates):
        keys=[]
        for action in candidates:
            keys.extend([digest([state['context'],action['id']]),digest([state['context'],state['id'],action['id']])])
            if state.get('interface_hypothesis') and state.get('interface_layout'):keys.append(digest(['interface_pool_v1',state['interface_layout'],action['id']]))
        return list(dict.fromkeys(keys))[:512]
    def _read(self,keys):
        db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=.2)
        try:
            rows=db.execute('SELECT key,data FROM rules WHERE profile=? AND key IN ('+','.join('?' for _ in keys)+')',[self.profile]+keys).fetchall() if keys else []
            return keys,{key:json.loads(raw) for key,raw in rows}
        finally:db.close()
    def begin(self,state,candidates):
        if self.pending is not None:return
        self.context=(state['context'],state['id'],state.get('interface_layout'))
        self.pending=self.pool.submit(self._read,self.keys(state,candidates))
    def merge(self,state,candidates,model):
        started=time.monotonic();wanted=(state['context'],state['id'],state.get('interface_layout'))
        if self.pending is None:self.begin(state,candidates)
        if self.context!=wanted:
            self.pending.cancel();self.pending=None;self.begin(state,candidates)
        try:
            keys,records=self.pending.result()
            for key,record in records.items():
                if key not in model.rules:model.rules[key]=record
            while len(model.rules)>512:model.rules.popitem(last=False)
            model.prefetched_missing=set(keys)-set(records)
            return {'status':'merged','records':len(records),'join_ms':(time.monotonic()-started)*1000}
        except Exception as error:
            return {'status':'fallback_owner_read','reason':str(error)[:160],'join_ms':(time.monotonic()-started)*1000}
        finally:self.pending=None
    def close(self):self.pool.shutdown(wait=False,cancel_futures=True)

class ProgressLane:
    """Exclusive manager ownership for one result, joined before Meta reads it.

    The world-model lane never reads this manager. No two update jobs overlap.
    """
    def __init__(self):
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='progress-evaluation');self.pending=None
    def begin(self,manager,state,step):
        import copy
        if self.pending is not None:raise RuntimeError('Progress ownership already transferred')
        fields=('id','frame_sha256','quotes','metrics','measurement_status','interface_hypothesis','interface_observation','selection_observation')
        sample={k:copy.deepcopy(state[k]) for k in fields if k in state}
        sample['minimap']={'status':state.get('minimap',{}).get('status')}
        sample['ui_visual']={'active':state['ui_visual'].get('active')} if state.get('ui_visual') else {}
        def evaluate():return {'frame':sample['frame_sha256'],'step':step,'changes':manager.update(sample,step)}
        self.pending=self.pool.submit(evaluate)
    def merge(self,frame,step):
        if self.pending is None:raise RuntimeError('No Progress result')
        try:
            result=self.pending.result()
            if result['frame']!=frame or result['step']!=step:raise ValueError('Progress result source mismatch')
            return result['changes']
        finally:self.pending=None
    def close(self):self.pool.shutdown(wait=True,cancel_futures=True)
