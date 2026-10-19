"""Persistent contextual TD action values and Beta estimates of *observed progress*.
This is online policy learning, not LLM weight training or proof of causality.
"""
import hashlib
import json
import math
import time
import io
import re
from dataclasses import replace
from .controls import ACTIONS,DIGEST,REFERENCE
from .knowledge import KnowledgeEngine
from .minimap import read_minimap

VERSION=2
MOVES=('w','a','s','d')

def profile_for(settings):
    return hashlib.sha256(json.dumps([VERSION,settings.window_title.casefold(),settings.goal,DIGEST],ensure_ascii=False).encode()).hexdigest()

class ExperiencePolicy:
    def __init__(self,memory,settings):
        self.db=memory.db;self.settings=settings;self.reader=KnowledgeEngine();self.visits={};self.trajectory=[]
        # Goal and control changes isolate values; editing prose does not erase learned actions.
        self.profile=profile_for(settings)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS policy_actions(profile TEXT,state TEXT,objective TEXT,action TEXT,
            trials INTEGER NOT NULL DEFAULT 0,q REAL NOT NULL DEFAULT 0,positive INTEGER NOT NULL DEFAULT 0,
            reward_sum REAL NOT NULL DEFAULT 0,goal_trials INTEGER NOT NULL DEFAULT 0,goal_wins INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(profile,state,objective,action));
        CREATE TABLE IF NOT EXISTS policy_events(id INTEGER PRIMARY KEY,profile TEXT,created REAL,data TEXT);
        CREATE INDEX IF NOT EXISTS policy_events_profile ON policy_events(profile,id);
        CREATE TABLE IF NOT EXISTS policy_visits(profile TEXT,location TEXT,count INTEGER NOT NULL,PRIMARY KEY(profile,location));
        ''')
    def observe(self,png,native=None):
        digest=hashlib.sha256(png).hexdigest()
        if native and native.get('frame_sha256') and native['frame_sha256']!=digest:native=None
        minimap=read_minimap(png);metrics=self.reader.reconcile(native or {'status':'unavailable'})['metrics']
        text='\n'.join(line.get('text','') for line in (native or {}).get('lines',[]))
        # A visible map may remain under a menu. Do not infer an input-ready scene from colors alone.
        menu=any(token in text for token in ('メニュー','持ち物','どうぐ','装備','設定','セーブ','選択してください','キャンセル','はい','いいえ'))
        hp=metrics.get('hp',{}).get('values');floor=metrics.get('floor',{}).get('values')
        eligible=minimap.get('status')=='observed' and bool(hp and floor) and not menu
        food=metrics.get('fullness',{}).get('values')
        objective='survive' if hp and hp[0]/hp[1]<.3 else ('forage' if food and food[0]<20 else 'explore')
        visual=[]
        if png.startswith(b'\x89PNG\r\n\x1a\n'):
            from PIL import Image
            try:
                with Image.open(io.BytesIO(png)) as image:visual=[v//32 for v in image.convert('L').resize((16,12)).tobytes()]
            except OSError:pass
        text_key=hashlib.sha256(re.sub(r'\s+','',text)[:2000].encode()).hexdigest()[:16]
        if not eligible:objective='interface'
        payload={'floor':floor,'hp_band':int(4*hp[0]/hp[1]) if hp else None,'food_band':food[0]//20 if food else None,'map_shape':minimap.get('shape'),
            'player_cell':minimap.get('player_cell'),'neighbors':minimap.get('neighbors'),'enemies':minimap.get('enemies'),'menu_evidence':menu}
        if not eligible:payload.update(visual=visual,text_key=text_key)
        state=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:24]
        location=hashlib.sha256(json.dumps([floor,minimap.get('shape'),minimap.get('player_cell')]).encode()).hexdigest()[:24] if eligible else state
        return {'frame_sha256':digest,'observable':bool(visual and text),'visual':visual,'text_key':text_key,'id':state,'location':location,'eligible':eligible,'objective':objective,'minimap':minimap,'metrics':metrics,'menu_evidence':menu,
            'note':'状態は画面内の地図形状・位置等の推定。ワールド座標や因果関係は未検証。'}
    def values(self,state):
        rows=self.db.execute('SELECT action,trials,q,positive,reward_sum,goal_trials,goal_wins FROM policy_actions WHERE profile=? AND state=? AND objective=?',
            (self.profile,state['id'],state['objective'])).fetchall()
        return {r[0]:dict(zip(('trials','q','positive','reward_sum','goal_trials','goal_wins'),r[1:])) for r in rows}
    def context(self,state,blocked=()):
        values=self.values(state)
        prior={r[0]:{'q':r[1]/max(1,r[2]),'trials':r[2]} for r in self.db.execute('SELECT action,SUM(q*trials),SUM(trials) FROM policy_actions WHERE profile=? AND objective=? GROUP BY action',(self.profile,state['objective']))}
        total=sum(v['trials'] for v in values.values());ranking=[]
        for key in self.settings.allowed_keys:
            if key not in ACTIONS:continue
            value=values.get(key,{'trials':0,'q':0.,'positive':0,'reward_sum':0.,'goal_trials':0,'goal_wins':0})
            n=value['trials'];prob=(value['positive']+1)/(n+2)
            neighbor=state['minimap'].get('neighbors',{}).get(key,{})
            exploration=.16*math.sqrt(math.log(total+2)/(n+1))
            # Unknown map cells are *not* called walls. Experience may lower their value.
            spatial=.04 if neighbor.get('known') else 0.
            backoff=prior.get(key,{'q':0.,'trials':0})
            goal_bias=.1*((value['goal_wins']+1)/(value['goal_trials']+2)-.5) if value['goal_trials'] else 0.
            score=value['q']+exploration+spatial+.1*backoff['q']/(n+1)+goal_bias
            allowed=key in MOVES and state['eligible'] and not neighbor.get('enemy') and [key] not in blocked
            ranking.append({'keys':[key],'meaning':ACTIONS[key],**value,'priority':round(score,5),'global_backoff':backoff,'verified_goal_bias':goal_bias,
                'progress_probability_estimate':round(prob,4) if n else None,'probability_samples':n,
                'probability_posterior_stddev':round(math.sqrt(prob*(1-prob)/(n+3)),4),
                'verified_goal_success_probability':round((value['goal_wins']+1)/(value['goal_trials']+2),4) if value['goal_trials'] else None,
                'automatic_candidate':allowed,'model_may_propose':True})
        ranking.sort(key=lambda v:v['priority'],reverse=True)
        return {'version':VERSION,'profile':self.profile,'objective':state['objective'],'state_id':state['id'],'eligible':state['eligible'],
            'ranking':ranking,'control_reference':REFERENCE,
            'note':'priorityは実経験のTD値＋未試行の探索加点。確率は正の観測報酬の推定でありゲームクリア確率ではない。試行0は未推定。全体目標の確率は検証済み終了エピソードがある場合のみ。'}
    def choose(self,decision,state,blocked=(),stable=False,adjustment=None):
        context=self.context(state,blocked)
        if not stable or decision.status!='continue' or decision.risk!='normal' or not state['eligible']:
            return decision,{'source':'model_or_neutral','context':context}
        # Enemy indicators forbid blind movement; leave attack/escape to a guarded model plan.
        if any(abs(x)+abs(y)<=2 for x,y in state['minimap'].get('enemies',[])) or state['objective']=='survive':
            return decision,{'source':'model_hazard_review','context':context}
        # Respect a concrete non-movement operation; do not turn an attack into walking.
        if decision.keys and any(key not in MOVES for key in decision.keys):
            return decision,{'source':'model_nonmovement','context':context}
        candidates=[v for v in context['ranking'] if v['automatic_candidate']]
        if not candidates:return replace(decision,keys=[]),{'source':'neutral_no_safe_candidate','context':context}
        if adjustment:
            strategy=adjustment.get('strategy')
            for candidate in candidates:
                key=candidate['keys'][0]
                delta=(.6/(candidate['trials']+1) if strategy=='explore_untried' else
                    (-.8 if strategy=='avoid_cycle' and [state['id'],key] in adjustment.get('avoid',[]) else 0.))
                candidate['adjustment']=delta;candidate['priority']=round(candidate['priority']+delta,5)
            candidates.sort(key=lambda v:v['priority'],reverse=True)
        # Least visited actions break cold-start ties deterministically; learned Q then dominates.
        selected=candidates[0];key=selected['keys'][0]
        reason=('地図の自位置・既知領域と、この状態で蓄積した行動価値・試行回数から優先度を選択しました。' if selected['trials'] else 'この状態では未試行の候補です。地図と安全条件を確認し、経験を蓄積するための短い移動を選択しました。')
        if adjustment:reason+=' 停滞の原因仮説を検証するため、許可された移動候補内で優先度を一時調整しています。'
        caption=f"地図の黄色マーカーを解析座標{state['minimap']['player']}に観測。青い既知領域は{state['minimap']['known_pixels']}画素。画面内の推定値です。"
        chosen=replace(decision,scene='gameplay',observation=caption,keys=[key],hold_ms=min(80,self.settings.max_hold_ms),wait_ms=600,
            confidence=.8,reason=reason,subgoal='食料を探すため未確認の通路を探索する' if state['objective']=='forage' else 'ミニマップ上の未訪問位置または未確認の通路を探索する',
            goal_success='地図の自位置の移動または既知領域の増加を次の観測で確認する',
            progress=[],memory_updates=[],plan=[],goal_evidence=[],knowledge_assessments=[])
        return chosen,{'source':'learned_policy','selected':selected,'context':context,'adjustment':adjustment,
            'safety':'同じ自位置を中立観測で再確認し、メニュー文字・近傍の敵・低HPがない場合の短い移動のみ。'}
    @staticmethod
    def signals(before,after):
        a,b=before.get('minimap',{}),after.get('minimap',{})
        out={'position':0.,'coverage':0.,'floor':0.,'enemy_reduction':0.,'visual':0.,'food_gain':0.}
        av,bv=before.get('visual',[]),after.get('visual',[])
        if av and len(av)==len(bv) and before.get('text_key')!=after.get('text_key'):
            changed=sum(abs(x-y)>1 for x,y in zip(av,bv))/len(av)
            if changed>.04:out['visual']=changed
        food0=before.get('metrics',{}).get('fullness',{}).get('values');food1=after.get('metrics',{}).get('fullness',{}).get('values')
        if food0 and food1:out['food_gain']=max(0.,food1[0]-food0[0])/100
        af,bf=before.get('metrics',{}).get('floor'),after.get('metrics',{}).get('floor')
        if af and bf and af['values']!=bf['values']:out['floor']=1.
        if a.get('status')!='observed' or b.get('status')!='observed':return out
        cell=max(a['cell_size'],b['cell_size'])
        aligned=all(abs(a['bbox'][i]-b['bbox'][i])<=cell*.5 for i in (0,1))
        if aligned:
            distance=math.dist(a['player'],b['player'])/cell
            out['position']=min(1.,distance) if distance>=.65 else 0.
            out['enemy_reduction']=float(len(b['enemies'])<len(a['enemies']))
        growth=b['known_pixels']-a['known_pixels']
        if growth>max(8,a['known_pixels']*.03):out['coverage']=min(1.,growth/max(1,a['known_pixels']))
        return out
    def learn(self,before,after,row,neutral_before=None):
        excluded=(str(row.get('event','')).startswith('diagnostic') or row.get('kind') in ('compatibility','separation') or row.get('learning') is False) or not row.get('executed') or row.get('dry_run') or row.get('turn_kind')=='aborted' or row.get('action_kind')!='input'
        receipt=row.get('input_receipt')
        if excluded or not isinstance(receipt,dict) or receipt.get('os_submission')!='accepted':
            return {'status':'not_trained','reason':'未送信・無入力・中断・診断・送信受付未確認は入力の成功経験にしません。'}
        keys=row.get('observed_keys',[])
        if len(keys)!=1 or keys[0] not in ACTIONS:return {'status':'not_trained','reason':'この方策は単独の確認済みキーを対象にします。'}
        if not before.get('observable') or not after.get('observable'):return {'status':'censored','reason':'比較可能な画像とOCRを取得できません。成功にも失敗にも加算しません。'}
        action=keys[0];observed=self.signals(before,after)
        natural=self.signals(neutral_before,before) if neutral_before else {k:0. for k in observed}
        net={k:max(0.,v-natural[k]) for k,v in observed.items()}
        # Returning to an already observed location is not exploration progress.
        # Register the departure too, so A -> B -> A cannot reward both halves.
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO policy_visits VALUES(?,?,1)',(self.profile,before['location']))
        visit=self.db.execute('SELECT count FROM policy_visits WHERE profile=? AND location=?',(self.profile,after['location'])).fetchone()
        revisits=visit[0] if visit else 0
        novelty=0. if revisits else 1.
        revisit_cost=.12*min(3,revisits) if net['position'] and not any(net[k] for k in ('coverage','floor','enemy_reduction','food_gain')) else 0.
        reward=.35*net['position']*novelty+.6*net['coverage']+net['floor']+.3*net['enemy_reduction']+.8*net['food_gain']+(.15*net['visual']*novelty if not before['eligible'] else 0)-.04-revisit_cost
        hp0=before['metrics'].get('hp',{}).get('values');hp1=after['metrics'].get('hp',{}).get('values')
        if hp0 and hp1 and hp0[1]==hp1[1]:reward-=2*max(0.,hp0[0]-hp1[0])/hp0[1]
        # No positive reward for natural HP regeneration or simply remaining at full HP.
        reward=max(-1.,min(1.,reward));positive=reward>0
        current=self.values(before).get(action,{'q':0.})['q']
        next_q=max(0.,max((v['q'] for k,v in self.values(after).items() if k in MOVES and k in self.settings.allowed_keys),default=0.))
        new_q=current+.25*(reward+.85*next_q-current)
        event={'status':'trained','state':before['id'],'next_state':after['id'],'objective':before['objective'],'keys':keys,
            'reward':reward,'q_before':current,'q_after':new_q,'observed':observed,'neutral_baseline':natural,'net_signals':net,
            'novelty_weight':novelty,'arrival_visits_before':revisits,'revisit_cost':revisit_cost,'positive_progress_proxy':positive,'attribution':'observed_association_not_causal',
            'note':'無反応の小さなコストは優先度の再検討用。壁・Backend故障・ゲーム側拒否の確定ではない。'}
        with self.db:
            self.db.execute('INSERT INTO policy_actions(profile,state,objective,action,trials,q,positive,reward_sum) VALUES(?,?,?,?,1,?,?,?) ON CONFLICT(profile,state,objective,action) DO UPDATE SET trials=trials+1,q=excluded.q,positive=positive+excluded.positive,reward_sum=reward_sum+excluded.reward_sum',
                (self.profile,before['id'],before['objective'],action,new_q,int(positive),reward))
            self.db.execute('INSERT INTO policy_visits VALUES(?,?,1) ON CONFLICT(profile,location) DO UPDATE SET count=count+1',(self.profile,after['location']))
            self.db.execute('INSERT INTO policy_events(profile,created,data) VALUES(?,?,?)',(self.profile,time.time(),json.dumps(event,ensure_ascii=False)))
        self.trajectory.append((before['id'],before['objective'],action));self.trajectory=self.trajectory[-500:]
        return event
    def finish_verified(self,evidence):
        if not evidence.get('achieved'):return
        self.finish_episode(True,evidence)
    def finish_episode(self,won,evidence):
        if not self.trajectory:return
        # Timeout/manual stop is censored, never a failed goal episode.
        with self.db:
            recent={}
            for distance,action_key in enumerate(reversed(self.trajectory)):recent.setdefault(action_key,distance)
            for (state,objective,action),distance in recent.items():
                terminal_return=(1. if won else -1.)*(.97**distance)
                self.db.execute('UPDATE policy_actions SET goal_trials=goal_trials+1,goal_wins=goal_wins+?,q=q+.1*(?-q) WHERE profile=? AND state=? AND objective=? AND action=?',(int(won),terminal_return,self.profile,state,objective,action))
            self.db.execute('INSERT INTO policy_events(profile,created,data) VALUES(?,?,?)',(self.profile,time.time(),json.dumps({'event':'verified_goal_episode','won':won,'evidence':evidence},ensure_ascii=False)))

        self.trajectory.clear()
