"""Grounded relative contacts and bounded online tactical experiments.

Facing is not inferred from the last movement. A turn combination is a trial,
followed by a fresh observation and a single attack trial, never an assumed hit.
"""
import json
import math
from dataclasses import replace
from .interfaces import result_screen

MOVES={'w':(0,-1),'a':(-1,0),'s':(0,1),'d':(1,0)}


def relative_contacts(state):
    m=(state or {}).get('minimap',{})
    if m.get('status')!='observed':return {'status':'unavailable'}
    contacts=[]
    for x,y in m.get('enemies',[]):
        contacts.append({'relative_cell':[x,y],'direction':('上' if y<0 else '下' if y>0 else '')+('左' if x<0 else '右' if x>0 else ''),
            'adjacent':max(abs(x),abs(y))==1,'kind':'enemy_indicator'})
    return {'status':'observed','player_reference':[0,0],'contacts':contacts,'source_frame_sha256':(state or {}).get('frame_sha256'),
        'hp':(state or {}).get('metrics',{}).get('hp',{}).get('values'),
        'basis':'現在の画素解析による相対位置。右が+x、下が+y。向きと命中は未確認。',
        'instruction':'隣接する敵を会話相手と仮定しない。Num1は決定・攻撃でありHP回復キーではない。向き変更も攻撃も入力結果を確認し、被ダメージと敵位置の変化を別々に評価する。'}


class CombatExperiments:
    def __init__(self,memory,cfg):
        self.db=memory.db;self.profile=memory.profile;self.cfg=cfg;self.pending=None
        self.db.execute('''CREATE TABLE IF NOT EXISTS combat_actions(profile TEXT,context TEXT,action TEXT,
            trials INTEGER,reward REAL,PRIMARY KEY(profile,context,action))''');self.db.commit()

    def choose(self,decision,state,stable,blocked=()):
        if not (stable and state and state.get('eligible') and decision.status=='continue' and decision.risk=='normal'):
            self.pending=None;return decision,None
        enemies=[tuple(e) for e in state['minimap'].get('enemies',[]) if max(abs(e[0]),abs(e[1]))==1]
        if not enemies:self.pending=None;return decision,None
        if decision.scene in ('dialog','menu'):return decision,None
        cardinal=[e for e in enemies if sum(abs(v) for v in e)==1]
        if not cardinal:return decision,None  # Diagonal targeting stays with the observed model plan.
        target=sorted(cardinal)[0];direction=next(k for k,v in MOVES.items() if v==target)
        context=json.dumps(['cardinal-contact-v1',direction,sorted(self.cfg.allowed_keys)])
        cell=list(state['minimap']['player_cell'])
        stage=None;action=None;keys=None
        if self.pending:
            pending=self.pending;self.pending=None
            if pending['cell']==cell and tuple(pending['target'])==target and pending['context']==context and pending['floor']==state['metrics'].get('floor',{}).get('values') and pending['frame_size']==state['minimap'].get('source_size') and ['num1'] not in blocked and 'num1' in self.cfg.allowed_keys:
                stage='attack_trial';action='orient_attack';keys=['num1']
        stats={r[0]:(r[1],r[2]) for r in self.db.execute('SELECT action,trials,reward FROM combat_actions WHERE profile=? AND context=?',(self.profile,context))}
        candidates=[];total=sum(v[0] for v in stats.values())
        if stage is None:
            if all(k in self.cfg.allowed_keys for k in ('num5',direction,'num1')) and ['num5',direction] not in blocked and ['num1'] not in blocked:
                candidates.append(('orient_attack',['num5',direction],'orient_trial'))
            # Short movement experiments remain available, including game risk.
            for key,delta in sorted(MOVES.items(),key=lambda item:-math.dist(item[1],target)):
                if key in self.cfg.allowed_keys and [key] not in blocked:candidates.append(('step_'+key,[key],'movement_trial'))
            if not candidates:return decision,None
            ranked=[]
            for name,ks,phase in candidates:
                n,reward=stats.get(name,(0,0.))
                ranked.append((n==0,reward/max(1,n)+.2*math.sqrt(math.log(total+2)/(n+1)),name,ks,phase))
            # Stable ordering gives the orientation experiment its first trial;
            # later untried alternatives and measured effects determine choice.
            ranked.sort(key=lambda r:(r[0],r[1]),reverse=True)
            _,_,action,keys,stage=ranked[0]
        trial={'context':context,'action':action,'stage':stage,'keys':list(keys),'target':list(target),'cell':cell,'floor':state['metrics'].get('floor',{}).get('values'),'frame_size':state['minimap'].get('source_size'),
            'orientation_verified':False,'past_trials':stats.get(action,(0,0))[0]}
        reason=('隣接敵の相対方向へNum5と方向キーによる向き変更を試し、位置・HPを再観測する。向き変更の成功は未確認。' if stage=='orient_trial' else
            '向き変更入力後も同じ自位置と隣接敵を観測。攻撃を1回試し、命中を仮定せず敵位置とHPの変化を評価する。' if stage=='attack_trial' else
            '隣接敵に対する未試行の短い移動、または観測結果の良い移動を試し、敵との相対位置とHPを再確認する。')
        return replace(decision,scene='gameplay',keys=keys,hold_ms=min(80,self.cfg.max_hold_ms),wait_ms=min(800,self.cfg.max_wait_ms),
            confidence=.85,observation='自分を原点とする隣接敵の相対位置を画素解析で観測。',reason=reason,
            subgoal='隣接敵に対する操作と結果を検証して進行する',goal_success='自位置・敵との相対位置・HPの変化を次の観測で比較する。',
            plan=[],progress=[],memory_updates=[],goal_evidence=[],knowledge_assessments=[],progress_test={}),trial

    def record(self,trial,row,after_native):
        if not trial:return None
        self.pending=None
        if not row.get('executed') or row.get('dry_run') or row.get('turn_kind')=='aborted' or row.get('action_kind')!='input' or row.get('observed_keys')!=trial['keys'] or (row.get('input_receipt') or {}).get('os_submission')!='accepted':return {'status':'not_updated'}
        before=row.get('policy_state') or {};after=row.get('policy_after') or {}
        hp0=before.get('metrics',{}).get('hp',{}).get('values');hp1=after.get('metrics',{}).get('hp',{}).get('values')
        damage=max(0,hp0[0]-hp1[0])/hp0[1] if hp0 and hp1 and hp0[1]==hp1[1] else 0.
        m=after.get('minimap',{});fresh=bool(after.get('eligible') and m.get('status')=='observed' and after['metrics'].get('floor',{}).get('values')==trial['floor'] and m.get('source_size')==trial['frame_size'])
        remains=fresh and trial['target'] in [list(e) for e in m.get('enemies',[])]
        same=fresh and trial['cell']==m.get('player_cell')
        defeated=result_screen(after_native)=='defeat_result'
        if trial['stage']=='orient_trial' and same and remains and not damage:
            self.pending=dict(trial)
            return {'status':'awaiting_attack_trial','orientation_verified':False,'note':'向き変更の効果は未確認。次の新しい観測で同じ対象がいる場合だけ単発攻撃を試す。'}
        if not hp0 or (not fresh and not defeated):return {'status':'censored'}
        moved=fresh and not same
        disappeared=fresh and same and not remains
        reward=.2*bool(moved)+.6*bool(disappeared)-2*damage-.02-(.8 if defeated else 0)
        with self.db:self.db.execute('INSERT INTO combat_actions VALUES(?,?,?,1,?) ON CONFLICT(profile,context,action) DO UPDATE SET trials=trials+1,reward=reward+excluded.reward',
            (self.profile,trial['context'],trial['action'],reward))
        return {'status':'updated','stage':trial['stage'],'action':trial['action'],'reward':reward,'hp_loss_fraction':damage,
            'enemy_indicator_disappeared':bool(disappeared),'defeat_result':defeated,'attribution':'observed_association_not_proven_cause',
            'note':'敵表示の消失は撃破・命中の確定ではない。被ダメージ・移動・表示変化を分離して比較。'}
