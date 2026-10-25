"""Grounded relative contacts and bounded online tactical experiments.

Facing is not inferred from the last movement. A turn combination is a trial,
followed by a fresh observation and a single attack trial, never an assumed hit.
"""
import json
import math
import time
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


def requires_message_ocr(row,state,cfg):
    if row.get('dry_run') or not row.get('executed') or row.get('turn_kind')=='aborted':return False
    if row.get('combat_experiment') or (row.get('operation') or {}).get('skill')=='combat':return True
    state=state or {}
    if state.get('minimap',{}).get('enemies'):return True
    from .interfaces import torneko_menu_kind
    if torneko_menu_kind(row.get('ocr') or {})=='item_actions':return True
    if not state.get('eligible') or state.get('menu_evidence'):return False
    import re
    from .controls import REFERENCE
    bindings=cfg.key_bindings or REFERENCE['keys']
    attacks={k for k,v in bindings.items() if re.search(r'攻撃|矢|attack|arrow',v,re.I)}
    return bool(attacks.intersection(row.get('observed_keys',[])))


class CombatExperiments:
    def __init__(self,memory,cfg):
        self.db=memory.db;self.profile=memory.profile;self.cfg=cfg;self.pending=None;self.recent_losses={}
        self.db.execute('''CREATE TABLE IF NOT EXISTS combat_actions(profile TEXT,context TEXT,action TEXT,
            trials INTEGER,reward REAL,PRIMARY KEY(profile,context,action))''');self.db.commit()
        self.db.execute('''CREATE TABLE IF NOT EXISTS combat_observations(id INTEGER PRIMARY KEY,
            profile TEXT,created REAL,kind TEXT,enemy TEXT,damage INTEGER,data TEXT)''');self.db.commit()
        self.db.execute('CREATE TABLE IF NOT EXISTS combat_message_reads(id INTEGER PRIMARY KEY,profile TEXT,created REAL,data TEXT)');self.db.commit()
        self.last_messages=set()

    def observe_messages(self,row,before_native,samples,extra_events=()):
        from .battle_messages import read_messages
        if row.get('dry_run') or not row.get('executed') or row.get('turn_kind')=='aborted' or (row.get('input_receipt') or {}).get('os_submission')!='accepted':return []
        audit=row.get('combat_message_ocr')
        if audit:
            audit['policy_learning']={k:v for k,v in (row.get('policy_learning') or {}).items() if k in ('status','reward','net_signals','reason')}
            audit['status_evidence']={side:{k:v for k,v in (row.get(side) or {}).get('metrics',{}).items() if k in ('hp','level','strength','sword','shield')} for side in ('policy_state','policy_after')}
            with self.db:self.db.execute('INSERT INTO combat_message_reads(profile,created,data) VALUES(?,?,?)',
                (self.profile,time.time(),json.dumps(audit,ensure_ascii=False)))
        previous={e['quote'] for e in read_messages(before_native,(row.get('ocr') or {}).get('frame_sha256'))}|self.last_messages
        observed={}
        for native,digest in samples:
            for event in read_messages(native,digest):observed.setdefault(event['quote'],event)
        for event in extra_events:observed.setdefault(event['quote'],event)
        names={e['enemy'] for e in observed.values() if e['kind']=='defeated_enemy'}
        for event in observed.values():
            candidate=event['enemy'].lstrip('ー')
            if event['enemy']!=candidate and candidate in names:
                event['raw_enemy']=event['enemy'];event['enemy']=candidate;event['name_evidence']='同じ操作後に読めた撃破名と一致する先頭のOCR揺れだけを統合'
        self.last_messages=set(observed)
        events=[]
        for quote,event in observed.items():
            event={**event,'new_message':quote not in previous,'observed_keys':row.get('observed_keys',[]),'step':row.get('step'),
                'session':(audit or {}).get('session'),'action_result_frame_sha256':(audit or {}).get('source_frame_sha256'),
                'status_evidence':{k:v for k,v in (row.get('policy_state') or {}).get('metrics',{}).items() if k in ('hp','level','strength','sword','shield')},
                'equipment':{'strength':None,'sword':None,'shield':None},
                'note':'画面メッセージの読取り。装備条件・敵の残HP・因果は未確認。同文の継続表示は追加試行に数えない。'}
            events.append(event)
            if not event['new_message']:continue
            with self.db:self.db.execute('INSERT INTO combat_observations(profile,created,kind,enemy,damage,data) VALUES(?,?,?,?,?,?)',
                (self.profile,time.time(),event['kind'],event['enemy'],event.get('damage'),json.dumps(event,ensure_ascii=False)))
        return events

    def experience(self):
        rows=self.db.execute('SELECT enemy,kind,COUNT(*),AVG(damage),MIN(damage),MAX(damage) FROM combat_observations WHERE profile=? GROUP BY enemy,kind ORDER BY MAX(id) DESC LIMIT 12',(self.profile,)).fetchall()
        actions=self.db.execute('SELECT context,action,trials,reward FROM combat_actions WHERE profile=? ORDER BY trials DESC LIMIT 12',(self.profile,)).fetchall()
        return {'source':'historical_battle_messages','action_values':[{'context':c,'action':a,'trials':n,'mean_observed_reward':r/max(1,n)} for c,a,n,r in actions],'observations':[dict(zip(('enemy','event','samples','mean_damage','min_damage','max_damage'),r)) for r in rows],
            'note':'過去のメッセージ別の記述統計。現在の敵種・残HP・装備・勝率・命中率を確定しない。未確認の攻撃を命中や空振りとして数えない。'}

    def choose(self,decision,state,stable,blocked=()):
        if not (stable and state and state.get('eligible') and decision.status=='continue' and decision.risk=='normal'):
            self.pending=None;return decision,None
        enemies=[tuple(e) for e in state['minimap'].get('enemies',[]) if max(abs(e[0]),abs(e[1]))==1]
        if not enemies:self.pending=None;return decision,None
        if decision.scene in ('dialog','menu'):return decision,None
        cardinal=[e for e in enemies if sum(abs(v) for v in e)==1]
        if not cardinal:return decision,None  # Diagonal targeting stays with the observed model plan.
        target=sorted(cardinal)[0];direction=next(k for k,v in MOVES.items() if v==target)
        context=json.dumps(['cardinal-contact-v2',direction,sorted(self.cfg.allowed_keys)])
        cell=list(state['minimap']['player_cell'])
        stage=None;action=None;keys=None;orientation_cost=0.;continuation_basis=None
        if self.pending:
            pending=self.pending;self.pending=None
            if pending['cell']==cell and tuple(pending['target'])==target and pending['context']==context and pending['floor']==state['metrics'].get('floor',{}).get('values') and pending['frame_size']==state['minimap'].get('source_size') and ['num1'] not in blocked and 'num1' in self.cfg.allowed_keys:
                stage='attack_trial';action='orient_attack';keys=['num1']
                orientation_cost=pending.get('orientation_hp_loss_fraction',0.)
                continuation_basis=pending.get('continuation_basis','orientation_input_and_current_contact')
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
                recent_cost=.2*min(3,self.recent_losses.get((context,name),0))
                ranked.append((max(-.5,min(.5,reward/max(1,n)))+.12*math.sqrt(math.log(total+2)/(n+1))-recent_cost,name,ks,phase))
            # Stable ties allow an initial orientation trial. Thereafter measured
            # value and a bounded exploration bonus compete without forcing
            # every untried movement ahead of a useful attack.
            ranked.sort(key=lambda r:r[0],reverse=True)
            _,action,keys,stage=ranked[0]
        trial={'context':context,'action':action,'stage':stage,'keys':list(keys),'target':list(target),'cell':cell,'floor':state['metrics'].get('floor',{}).get('values'),'frame_size':state['minimap'].get('source_size'),
            'orientation_verified':False,'past_trials':stats.get(action,(0,0))[0],'orientation_hp_loss_fraction':orientation_cost,'continuation_basis':continuation_basis,
            'prediction':{'samples':stats.get(action,(0,0))[0],'mean_observed_reward':stats.get(action,(0,0))[1]/max(1,stats.get(action,(0,0))[0]),'scope':'同じ方向の隣接表示に対する操作の関連性。現在の敵種や勝率は未確認。'}}
        reason=('隣接敵の相対方向へNum5と方向キーによる向き変更を試し、位置・HPを再観測する。向き変更の成功は未確認。' if stage=='orient_trial' else
            ('直前の命中メッセージと同じ自位置の隣接敵を再観測。向き直しを挟まず攻撃を1回試し、結果を再確認する。' if continuation_basis=='new_hit_message_and_current_contact' else '向き変更入力後も同じ自位置と隣接敵を観測。攻撃を1回試し、命中を仮定せず敵位置とHPの変化を評価する。') if stage=='attack_trial' else
            '隣接敵に対する未試行の短い移動、または観測結果の良い移動を試し、敵との相対位置とHPを再確認する。')
        return replace(decision,scene='gameplay',keys=keys,hold_ms=min(80,self.cfg.max_hold_ms),wait_ms=min(800,self.cfg.max_wait_ms),
            confidence=.85,observation='自分を原点とする隣接敵の相対位置を画素解析で観測。',reason=reason,
            subgoal='隣接敵に対する操作と結果を検証して進行する',goal_success='自位置・敵との相対位置・HPの変化を次の観測で比較する。',
            forecast={'horizon':1,'first_expected':{'player_delta':[0,0]} if stage in ('orient_trial','attack_trial') else {'player_delta':list(MOVES[keys[0]])},'continuations':[]},
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
        if trial['stage']=='orient_trial' and same and remains and not defeated and hp1 and hp1[0]>0:
            self.pending={**trial,'orientation_hp_loss_fraction':damage}
            return {'status':'awaiting_attack_trial','orientation_verified':False,'note':'向き変更の効果は未確認。次の新しい観測で同じ対象がいる場合だけ単発攻撃を試す。'}
        messages=[e for e in row.get('battle_observations',[]) if e.get('new_message')]
        explicit_kill=any(e['kind']=='defeated_enemy' for e in messages)
        explicit_hit=any(e['kind']=='damage_dealt' for e in messages)
        if not fresh and not defeated and not explicit_kill and not explicit_hit:return {'status':'censored','battle_messages':messages}
        moved=fresh and not same
        disappeared=fresh and same and not remains
        distances=[max(abs(x),abs(y)) for x,y in m.get('enemies',[])] if fresh else []
        separation=bool(moved and distances and min(distances)>1)
        total_damage=damage+trial.get('orientation_hp_loss_fraction',0.)
        reward=.1*separation+.6*explicit_kill+.15*explicit_hit-2*total_damage-.04-(.8 if defeated else 0)
        loss_key=(trial['context'],trial['action'])
        self.recent_losses[loss_key]=self.recent_losses.get(loss_key,0)+1 if reward<0 else 0
        while len(self.recent_losses)>64:self.recent_losses.pop(next(iter(self.recent_losses)))
        with self.db:self.db.execute('INSERT INTO combat_actions VALUES(?,?,?,1,?) ON CONFLICT(profile,context,action) DO UPDATE SET trials=trials+1,reward=reward+excluded.reward',
            (self.profile,trial['context'],trial['action'],reward))
        # A newly read hit allows another single attack only while a fresh map
        # still has this contact at this position. This is not sprite facing proof.
        if trial['stage']=='attack_trial' and same and remains and explicit_hit and not explicit_kill and not defeated:
            self.pending={**trial,'orientation_hp_loss_fraction':0.,'continuation_basis':'new_hit_message_and_current_contact'}
        predicted=trial.get('prediction',{})
        return {'status':'updated','stage':trial['stage'],'action':trial['action'],'reward':reward,'hp_loss_fraction':damage,'sequence_hp_loss_fraction':total_damage,
            'prediction':predicted,'prediction_error':reward-predicted['mean_observed_reward'] if predicted.get('samples') else None,
            'enemy_indicator_disappeared':bool(disappeared),'defeat_result':defeated,'attribution':'observed_association_not_proven_cause',
            'separation_observed':separation,'moved':bool(moved),'hp_comparable':bool(hp0 and hp1 and hp0[1]==hp1[1]),
            'battle_messages':messages,'scoring_version':2,
            'note':'移動だけ・敵表示の消失だけでは戦闘成功に加点しない。メッセージとHPと距離変化を分離。命中・撃破メッセージもOCRの未検証読取り。'}
