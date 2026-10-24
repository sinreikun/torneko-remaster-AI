"""Every continuing autonomous turn attempts an allowed input.

Uncertainty affects duration and attribution, never creates an observation-only
turn. Physical/backend errors and explicit user stop remain outside this policy.
"""
from collections import Counter
from dataclasses import replace
import hashlib
import re
from .controls import REFERENCE,is_torneko
from .interfaces import choice_action,PROTECTED_CONFIRMATION

DANGEROUS=PROTECTED_CONFIRMATION


class ActiveInput:
    def __init__(self,cfg):self.cfg=cfg;self.attempts={}
    def select(self,decision,native=None,state=None,blocked=(),reason='',force=False,preserve_proposal=False):
        cfg=self.cfg;bindings=cfg.key_bindings or (REFERENCE['keys'] if is_torneko(cfg.window_title) else {})
        text='\n'.join(l.get('text','') for l in (native or {}).get('lines',[]))
        forbidden=decision.status=='blocked' or decision.risk=='destructive' or bool(DANGEROUS.search(text))
        allowed=[k for k in cfg.allowed_keys if k not in ('num3','num5','num7','num6') or not is_torneko(cfg.window_title)]
        if not allowed:allowed=list(cfg.allowed_keys)
        match=lambda pattern:[k for k in allowed if re.search(pattern,bindings.get(k,''),re.I)]
        confirm=match(r'決定|confirm');cancel=match(r'キャンセル|cancel')
        movement=match(r'^(上|下|左|右)|^(up|down|left|right)$')
        if not movement:movement=[k for k in ('w','a','s','d','up','left','down','right') if k in allowed]
        candidates=list(dict.fromkeys(confirm+movement+cancel+allowed))
        if forbidden:
            candidates=cancel or movement
            if not candidates:raise RuntimeError('破壊的な確認に対して許可された取消・方向キーがありません。')
        identity=(state or {}).get('id') or hashlib.sha256(re.sub(r'\s+','',text).encode()).hexdigest()[:24]
        counts=self.attempts.setdefault(identity,Counter())
        while len(self.attempts)>64:self.attempts.pop(next(iter(self.attempts)))
        recent={tuple(keys) for keys in blocked}
        proposal=decision.keys
        repeat=bool(proposal and (tuple(proposal) in recent or (counts[tuple(proposal)]>=3 and not preserve_proposal)))
        if decision.status=='complete' and not force:return decision,{}
        ordinary=(decision.status=='continue' and proposal and set(proposal)<=set(cfg.allowed_keys) and not forbidden and not repeat and not force)
        if ordinary:
            if decision.confidence>=cfg.min_confidence and decision.risk=='normal':return decision,{}
            # Retain honest confidence; the action is an experiment, not a new
            # claim that uncertain perception has become certain.
            return replace(decision,hold_ms=min(decision.hold_ms,80),wait_ms=min(decision.wait_ms,600),forecast={}),{
                'source':'uncertain_input_trial','reason':'低確信度でも提案された許可入力を短く試す。','confidence':decision.confidence,'learning':False}
        choice=choice_action(native or {},bindings,allowed) if not forbidden else None
        if choice and counts[(choice['key'],)]<3 and (choice['key'],) not in recent:key=choice['key']
        else:key=min(candidates,key=lambda k:(counts[(k,)]+(3 if (k,) in recent else 0),candidates.index(k)))
        why=('対象の根拠が不十分なため、その操作は採用せず別の許可入力を試す。' if reason and '対象' in reason else reason) or ('終了・中断・削除等の確認から取消・移動で離れる。' if forbidden else '同じ無反応の反復を避け別の許可入力を試す。' if repeat else '観察のみで待たず、許可入力を試して次の画面を取得する。')
        return replace(decision,status='continue',risk='uncertain',keys=[key],hold_ms=min(80,cfg.max_hold_ms),wait_ms=min(600,cfg.max_wait_ms),
            intent={'kind':'confirm' if key in confirm else 'explore','target_id':''},forecast={},plan=[],progress=[],memory_updates=[],goal_evidence=[],knowledge_assessments=[],progress_test={},
            subgoal='入力を試して次の状況を確認する',reason=why,goal_success='入力後の画面と数値を再取得し変化を比較する'),{
                'source':'active_input_trial','reason':why,'previous_keys':proposal,'selected_keys':[key],'state_id':identity,'prior_attempts':dict((str(k),v) for k,v in counts.items()),'learning':False}
    def sent(self,decision,native=None,state=None):
        text='\n'.join(l.get('text','') for l in (native or {}).get('lines',[]))
        identity=(state or {}).get('id') or hashlib.sha256(re.sub(r'\s+','',text).encode()).hexdigest()[:24]
        self.attempts.setdefault(identity,Counter())[tuple(decision.keys)]+=1
