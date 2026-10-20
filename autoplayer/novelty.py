"""Persistent semantic novelty and bounded, grounded interface exploration.

New outcomes are observations, not proof of input causality or goal success.
"""
import hashlib
import json
import re
import time
import math
from dataclasses import replace
from .interfaces import compact_text,torneko_menu_kind
from .controls import is_torneko


class NoveltyExplorer:
    def __init__(self,memory,cfg):
        self.db=memory.db;self.profile=memory.profile;self.cfg=cfg;self.map_epoch=time.time_ns()
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS novelty_states(profile TEXT,state TEXT,visits INTEGER,
            PRIMARY KEY(profile,state));
        CREATE TABLE IF NOT EXISTS novelty_actions(profile TEXT,state TEXT,action TEXT,trials INTEGER,
            discoveries INTEGER,reward REAL,last_used REAL,PRIMARY KEY(profile,state,action));
        CREATE TABLE IF NOT EXISTS novelty_outcomes(profile TEXT,state TEXT,action TEXT,destination TEXT,
            count INTEGER,PRIMARY KEY(profile,state,action,destination));
        ''')

    def state(self,native,policy=None):
        if not native or native.get('status')!='ok':return None
        if policy and policy.get('eligible'):
            m=policy['minimap']
            payload=['map',self.map_epoch,policy['metrics'].get('floor',{}).get('values'),m.get('source_size'),m.get('cell_size'),m.get('player_cell')]
        else:
            # Ignore HUD counters, OCR word spacing, line order, timestamps and
            # raw animations. Do not reward a changing fullness/HP digit as a new screen.
            labels=sorted({compact_text(l.get('text','')) for l in native.get('lines',[])})
            labels=[s for s in labels if s and not re.search(r'HP|Lv|満腹|腹度|経験値|\d',s,re.I)]
            if not labels:return None
            kind=torneko_menu_kind(native)
            if kind:
                labels=[s for s in labels if s in {'道具','どうぐ','マップ','足元','その他','詳細表示切替'} or re.match(r'^[▶►▷>→]',s)]
            payload=['interface',kind,labels]
        return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:24]

    def choose_menu(self,decision,native,blocked=()):
        kind=torneko_menu_kind(native)
        if not (is_torneko(self.cfg.window_title) and kind and decision.status=='continue' and decision.risk=='normal'):
            return decision,None
        # Keep grounded confirm/use/dialogue proposals with the existing model
        # and safety checks. Never invent an item use or destructive operation.
        if decision.keys and any(k not in ('w','a','s','d','num0','num2') for k in decision.keys):return decision,None
        signature=self.state(native)
        if not signature:return decision,None
        stats={r[0]:r[1:] for r in self.db.execute('SELECT action,trials,discoveries,reward,last_used FROM novelty_actions WHERE profile=? AND state=?',(self.profile,signature))}
        transitions={r[0]:(r[1],r[2]) for r in self.db.execute('SELECT action,SUM(count),SUM(CASE WHEN destination<>state THEN count ELSE 0 END) FROM novelty_outcomes WHERE profile=? AND state=? GROUP BY action',(self.profile,signature))}
        candidates=[]
        for key in ('w','a','s','d','num0','num2'):
            if key not in self.cfg.allowed_keys or [key] in blocked:continue
            n,new,reward,last=stats.get(key,(0,0,0.,0.))
            samples,changes=transitions.get(key,(0,0))
            mean=reward/max(1,n);change_rate=changes/max(1,samples)
            candidates.append({'key':key,'trials':n,'discoveries':new,'mean_reward':mean,'last_used':last,
                'transition_samples':samples,'observed_state_change_rate':change_rate,
                'priority':mean+.3*change_rate+.1/math.sqrt(n+1)})
        if not candidates:return decision,None
        # Only zero-trial candidates receive absolute exploration priority.
        # Once tried, observed effects and reward outrank balanced trial counts.
        candidates.sort(key=lambda c:(c['trials']>0,-c['priority'],c['last_used']))
        selected=candidates[0]
        reason=('現在のOCRでメニューを確認。この画面で未試行の許可済みキーを1回試して再観測します。' if selected['trials']==0 else
            '候補はすべて試行済み。試行回数の均等化ではなく、観測された状態変化と報酬を優先して次の状態へ進む操作を選びます。')
        return replace(decision,scene='menu',keys=[selected['key']],confidence=.85,hold_ms=min(80,self.cfg.max_hold_ms),wait_ms=min(600,self.cfg.max_wait_ms),
            observation='現在のOCRにメニュー固有の複数項目があります。',subgoal='未経験の画面または操作結果を観測する',reason=reason,
            goal_success='操作後のメニュー項目・選択表示・場面の変化を観測する。',plan=[],progress=[],memory_updates=[],goal_evidence=[],knowledge_assessments=[],progress_test={}),{
                'source':'novelty_exploration','state':signature,'kind':kind,'selected':selected,'candidates':candidates,
                'note':'固定のキャンセル復帰ではなく未試行優先。無反応を故障や壁と断定しない。'}

    def repeated_menu_opening(self,native,policy,decision):
        """Do not let a repeatedly observed menu cycle override fresh map exploration."""
        if decision.keys!=['num0'] or not policy or not policy.get('eligible') or decision.status!='continue' or decision.risk!='normal':return None
        source=self.state(native,policy)
        if not source:return None
        rows=self.db.execute('SELECT destination,count FROM novelty_outcomes WHERE profile=? AND state=? AND action=?',(self.profile,source,'num0')).fetchall()
        repeats=sum(count for _,count in rows if count>=2)
        if repeats<2:return None
        return {'reason':'同じ探索状態からメニューを開く遷移を2回以上観測済み。現在の安定した地図から未試行の移動を優先する。',
            'state':source,'observed_menu_openings':repeats,'attribution':'observed_association_not_proven_cause'}

    def record(self,row,before_native,after_native,neutral_policy=None):
        if not row.get('executed') or row.get('dry_run') or row.get('turn_kind')=='aborted' or (row.get('input_receipt') or {}).get('os_submission')!='accepted':
            return {'status':'not_updated'}
        keys=row.get('observed_keys',[])
        if len(keys)!=1 or keys==['num7']:return {'status':'not_updated'}
        before=self.state(before_native,row.get('policy_state'));after=self.state(after_native,row.get('policy_after'))
        if not before or not after:return {'status':'censored','reason':'比較可能なOCRがない'}
        natural=bool((row.get('neutral_before') or {}).get('raw_change',0)>.01)
        if neutral_policy and row.get('policy_state'):
            from .policy import ExperiencePolicy
            natural=natural or any(ExperiencePolicy.signals(neutral_policy,row['policy_state'])[k] for k in ('position','coverage','floor'))
        known=self.db.execute('SELECT visits FROM novelty_states WHERE profile=? AND state=?',(self.profile,after)).fetchone()
        outcome=self.db.execute('SELECT count FROM novelty_outcomes WHERE profile=? AND state=? AND action=? AND destination=?',(self.profile,before,keys[0],after)).fetchone()
        new_state=after!=before and not known and not natural
        new_outcome=after!=before and not outcome and not natural
        reward=0. if natural else (1. if new_state else (.15 if new_outcome else -.1))
        hp0=(row.get('policy_state') or {}).get('metrics',{}).get('hp',{}).get('values')
        hp1=(row.get('policy_after') or {}).get('metrics',{}).get('hp',{}).get('values')
        if hp0 and hp1 and hp0[1]==hp1[1]:reward-=2*max(0,hp0[0]-hp1[0])/hp0[1]
        with self.db:
            for state in {before,after}:
                self.db.execute('INSERT INTO novelty_states VALUES(?,?,1) ON CONFLICT(profile,state) DO UPDATE SET visits=visits+1',(self.profile,state))
            self.db.execute('INSERT INTO novelty_actions VALUES(?,?,?,1,?,?,?) ON CONFLICT(profile,state,action) DO UPDATE SET trials=trials+1,discoveries=discoveries+excluded.discoveries,reward=reward+excluded.reward,last_used=excluded.last_used',
                (self.profile,before,keys[0],int(new_state),reward,time.time()))
            if not natural:
                self.db.execute('INSERT INTO novelty_outcomes VALUES(?,?,?,?,1) ON CONFLICT(profile,state,action,destination) DO UPDATE SET count=count+1',(self.profile,before,keys[0],after))
        return {'status':'updated','objective':'novelty_first','state':before,'destination':after,'new_state':new_state,'new_outcome':new_outcome,
            'reward':reward,'natural_change_possible':natural,'attribution':'observed_association_not_proven_cause','game_success_verified':False}

    def context(self):
        recent=self.db.execute('SELECT state,action,trials,discoveries,reward FROM novelty_actions WHERE profile=? ORDER BY last_used DESC LIMIT 6',(self.profile,)).fetchall()
        return {'objective':'novelty_first','recent_actions':[dict(zip(('state','key','trials','discoveries','reward_sum'),r)) for r in recent],
            'instruction':'当面は未経験の画面・場所・操作結果への到達を最優先。既知の反復より未試行の候補を選ぶ。ゲーム内の危険は発見と損失を比較し一律に除外しない。観測の関連を因果の確定やクリアと混同しない。'}
