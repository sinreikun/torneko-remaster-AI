"""Bounded, interruptible receding-horizon control; no multi-key macro playback."""
import copy
import json
from dataclasses import replace

MOVES={'w':(0,-1),'a':(-1,0),'s':(0,1),'d':(1,0)}

def validate_forecast(value,cfg):
    if not isinstance(value,dict):raise ValueError('forecast must be an object')
    if not value:return {}
    horizon=value.get('horizon',1)
    if type(horizon) is not int or not 1<=horizon<=8:raise ValueError('invalid forecast horizon')
    steps=value.get('continuations',[])
    if not isinstance(steps,list) or len(steps)>7:raise ValueError('invalid forecast steps')
    out=[]
    for step in steps:
        if not isinstance(step,dict):raise ValueError('invalid forecast step')
        keys=step.get('keys')
        if not isinstance(keys,list) or len(keys)>2 or any(not isinstance(k,str) or k not in cfg.allowed_keys for k in keys) or len(set(keys))!=len(keys):raise ValueError('invalid forecast keys')
        if any(set(keys)>=set(pair) for pair in [('w','s'),('a','d'),('up','down'),('left','right')]):raise ValueError('opposite forecast keys')
        if type(step.get('hold_ms')) is not int or not 1<=step['hold_ms']<=cfg.max_hold_ms:raise ValueError('invalid forecast hold')
        if type(step.get('wait_ms')) is not int or not 0<=step['wait_ms']<=cfg.max_wait_ms:raise ValueError('invalid forecast wait')
        expected=step.get('expected',{})
        validate_expected(expected)
        # Continuations must have an independently testable prediction.
        if not expected:raise ValueError('forecast requires expected observation')
        out.append({k:copy.deepcopy(step[k]) for k in ('keys','hold_ms','wait_ms','expected')})
    first=value.get('first_expected',{});validate_expected(first)
    return {'horizon':horizon,'first_expected':first,'continuations':out}

def validate_expected(e):
    if not isinstance(e,dict) or set(e)-{'player_delta','hp_min','text_appears','text_disappears'}:raise ValueError('invalid expected fields')
    if 'player_delta' in e and (not isinstance(e['player_delta'],list) or len(e['player_delta'])!=2 or any(type(v) is not int or abs(v)>1 for v in e['player_delta'])):raise ValueError('invalid expected delta')
    if 'hp_min' in e and (type(e['hp_min']) is not int or not 0<=e['hp_min']<=9999):raise ValueError('invalid expected hp')
    for k in ('text_appears','text_disappears'):
        if k in e and (not isinstance(e[k],str) or not e[k].strip() or len(e[k])>100):raise ValueError('invalid expected text')

def snapshot(state):
    s=state or {};m=s.get('minimap',{});metrics=s.get('metrics',{})
    return {'floor':metrics.get('floor',{}).get('values'),'hp':metrics.get('hp',{}).get('values'),
        'fullness':metrics.get('fullness',{}).get('values'),'cell':m.get('player_cell'),
        'enemies':m.get('enemies',[]),'items':m.get('items',[]),'stairs':m.get('stairs',[]),
        'menu':s.get('menu_evidence',False),'eligible':s.get('eligible',False)}

class Anticipation:
    def __init__(self,memory=None):
        self.db=memory.db if memory else None;self.profile=memory.profile if memory else 'session'
        if self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS forecast_stats(profile TEXT,context TEXT,trials INTEGER,matches INTEGER,PRIMARY KEY(profile,context))')
            self.db.commit()
        self.anchor=None;self.queue=[];self.remaining=0;self.expected={};self.last=None;self.context_key=None;self.reason='initial_analysis'
        self.baseline=None;self.latest={};self.local_trials={};self.awaiting=False
    @property
    def active(self):return self.anchor is not None and self.remaining>0
    def cancel(self,reason):
        self.anchor=None;self.queue=[];self.remaining=0;self.reason=reason
    def indicators(self,state,world):
        s=snapshot(state);hp=s['hp'];distance=min((max(abs(x),abs(y)) for x,y in s['enemies']),default=None);enemy=distance is not None and distance<=4
        threat=0 if distance is None else .9 if distance<=2 else .65 if distance<=4 else .3
        tension=max(1-min(1,hp[0]/hp[1]) if hp and hp[1] else .7,threat)
        food=s.get('fullness')
        if food:tension=max(tension,.7 if food[0]<=5 else .35 if food[0]<=15 else 0)
        novelty=(world or {}).get('novelty',1)
        key=json.dumps([bool(enemy),round(tension*3),bool(s['menu']),bool(novelty>.5)])
        row=self.db.execute('SELECT trials,matches FROM forecast_stats WHERE profile=? AND context=?',(self.profile,key)).fetchone() if self.db else self.local_trials.get(key)
        n,hits=row or (0,0);reliability=(hits+1)/(n+2)
        budget=1 if tension>=.65 else min(8,2+round(6*reliability))
        if novelty>.5:budget=min(budget,2)
        if n<3:budget=min(budget,3)
        return {'tension':round(tension,3),'novelty':novelty,'prediction_reliability':round(reliability,3),'trials':n,'max_turns':budget,'nearest_enemy_cells':distance,'context':key}
    def ready(self,state,world,blocked=(),stagnant=0,reassess=False):
        self.latest=self.indicators(state,world);s=snapshot(state)
        if not self.active:return False
        b=self.baseline or {}
        reason=('floor_or_reading_changed' if not s['floor'] or s['floor']!=b.get('floor') else
            'new_interface' if s['menu']!=b.get('menu') else
            'damage' if s['hp'] and b.get('hp') and s['hp'][0]<b['hp'][0] else
            'resource_urgency' if s.get('fullness') and b.get('fullness') and any(s['fullness'][0]<=limit<b['fullness'][0] for limit in (15,5,0)) else
            'missing_current_observation' if not s['eligible'] else
            'unexpected_visual_transition' if (world or {}).get('scene_change') else
            'new_text' if (world or {}).get('new_messages') else
            'new_object' if (world or {}).get('new_objects') else
            'enemy_contact' if any(max(abs(x),abs(y))<=4 for x,y in s['enemies']) else
            'blocked_or_stagnant' if blocked or stagnant>=2 or reassess else None)
        if reason:self.cancel(reason);return False
        return True
    def begin(self,decision,state,world):
        self.latest=self.indicators(state,world);self.context_key=self.latest['context']
        f=getattr(decision,'forecast',{}) or {};budget=min(f.get('horizon',self.latest['max_turns']),self.latest['max_turns'])
        self.anchor=copy.deepcopy(decision);self.queue=copy.deepcopy(f.get('continuations',[]))
        self.remaining=max(0,budget-1);self.expected=copy.deepcopy(f.get('first_expected',{}));self.baseline=snapshot(state)
        self.reason='new_analysis';self.awaiting=True
        if decision.status!='continue' or decision.risk!='normal':self.cancel('noncontinuable_analysis')
    def next(self):
        decision=copy.deepcopy(self.anchor);planned=bool(self.queue)
        if planned:
            step=self.queue.pop(0);decision=replace(decision,keys=step['keys'],hold_ms=step['hold_ms'],wait_ms=step['wait_ms'],
                reason='直前の観測と予測条件を確認した条件付き計画の次の1手。',plan=[],progress=[],memory_updates=[],goal_evidence=[],knowledge_assessments=[])
            self.expected=step['expected']
        else:self.expected={}
        self.remaining-=1;self.awaiting=True
        return decision,planned
    def expect_action(self,decision,state):
        # Learn predictions for local navigation too. A replaced LLM action must
        # never be evaluated against the prediction of the original action.
        self.last=snapshot(state)
        if len(decision.keys)==1 and decision.keys[0] in MOVES:
            self.expected.setdefault('player_delta',list(MOVES[decision.keys[0]]))
    def feedback(self,before,after,row,ocr_before,ocr_after,neutral):
        if not self.awaiting:return {'status':'not_evaluated'}
        self.awaiting=False;s=snapshot(after);a=snapshot(before);checks=[]
        accepted=(row.get('input_receipt') or {}).get('os_submission')=='accepted'
        if not row.get('executed') or not accepted or row.get('turn_kind')=='aborted':
            self.cancel('input_not_completed');return {'status':'censored','reason':self.reason}
        e=self.expected
        if 'player_delta' in e:
            checks.append(None if not a['cell'] or not s['cell'] or not a['floor'] or a['floor']!=s['floor'] else [s['cell'][i]-a['cell'][i] for i in (0,1)]==e['player_delta'])
        if 'hp_min' in e:checks.append(s['hp'][0]>=e['hp_min'] if s['hp'] else None)
        old='\n'.join(l.get('text','') for l in (ocr_before or {}).get('lines',[]));text='\n'.join(l.get('text','') for l in (ocr_after or {}).get('lines',[]))
        for k in ('text_appears','text_disappears'):
            if k in e:
                checks.append(None if (ocr_after or {}).get('status')!='ok' or (ocr_before or {}).get('status')!='ok' else
                    (e[k] in text and e[k] not in old) if k=='text_appears' else (e[k] in old and e[k] not in text))
        natural=bool((neutral or {}).get('position') or (neutral or {}).get('floor'))
        match=all(checks) if checks and None not in checks and not natural else None
        if match is not None and self.context_key:
            if self.db:
                self.db.execute('INSERT INTO forecast_stats VALUES(?,?,1,?) ON CONFLICT(profile,context) DO UPDATE SET trials=trials+1,matches=matches+excluded.matches',(self.profile,self.context_key,int(match)));self.db.commit()
            else:
                n,h=self.local_trials.get(self.context_key,(0,0));self.local_trials[self.context_key]=(n+1,h+int(match))
        if match is not True:self.cancel('prediction_mismatch' if match is False else 'prediction_unverifiable')
        if s['floor']!=a['floor'] or s['menu']!=a['menu'] or (s['hp'] and a['hp'] and s['hp'][0]<a['hp'][0]):self.cancel('state_interrupt')
        self.baseline=s
        return {'status':'matched' if match is True else 'mismatch' if match is False else 'unverifiable',
            'expected':copy.deepcopy(e),'actual':s,'remaining':self.remaining,'reason':self.reason,
            'indicators':self.latest,'attribution':'prediction_association_not_causality'}
