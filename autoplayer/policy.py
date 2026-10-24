"""Persistent contextual TD action values and Beta estimates of *observed progress*.
This is online policy learning, not LLM weight training or proof of causality.
"""
from .experience_review import outcome_learning_allowed
import hashlib
import json
import math
import time
import io
import re
import copy
from dataclasses import replace
from .controls import ACTIONS,DIGEST,REFERENCE
from .knowledge import KnowledgeEngine
from .map_sensor import read_grid,MapSensor,FloorConfirmation
from .exploration import ExplorationRoutes
from .interfaces import compact_text,torneko_menu_kind,result_screen

VERSION=8
MOVES=('w','a','s','d')

def profile_for(settings):
    return hashlib.sha256(json.dumps([VERSION,settings.window_title.casefold(),settings.goal,DIGEST],ensure_ascii=False).encode()).hexdigest()

class ExperiencePolicy:
    def __init__(self,memory,settings):
        self.db=memory.db;self.settings=settings;self.reader=KnowledgeEngine();self.visits={};self.trajectory=[]
        # Goal and control changes isolate values; editing prose does not erase learned actions.
        self.profile=profile_for(settings)
        self.routes=ExplorationRoutes();self.map_sensor=MapSensor();self.floor_confirmation=FloorConfirmation()
        # MemoryStore lives for one UI run. Re-observation retries share these
        # measured routes; a reconnect explicitly clears them in the caller.
        cached=getattr(memory,'runtime_navigation',None)
        if cached and cached.get('profile')==self.profile:
            self.routes=cached['routes'];self.pose_anchors=cached['anchors'];self.pose_space=cached['space']
        memory.runtime_navigation={'profile':self.profile,'routes':self.routes,'anchors':getattr(self,'pose_anchors',[]),'space':getattr(self,'pose_space',None)}
        self.runtime_navigation=memory.runtime_navigation
        if cached and cached.get('profile')==self.profile:
            self.map_sensor=cached.get('map_sensor',self.map_sensor);self.floor_confirmation=cached.get('floor_confirmation',self.floor_confirmation)
        self.runtime_navigation.update(map_sensor=self.map_sensor,floor_confirmation=self.floor_confirmation)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS policy_actions(profile TEXT,state TEXT,objective TEXT,action TEXT,
            trials INTEGER NOT NULL DEFAULT 0,q REAL NOT NULL DEFAULT 0,positive INTEGER NOT NULL DEFAULT 0,
            reward_sum REAL NOT NULL DEFAULT 0,goal_trials INTEGER NOT NULL DEFAULT 0,goal_wins INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(profile,state,objective,action));
        CREATE TABLE IF NOT EXISTS policy_events(id INTEGER PRIMARY KEY,profile TEXT,created REAL,data TEXT);
        CREATE INDEX IF NOT EXISTS policy_events_profile ON policy_events(profile,id);
        CREATE TABLE IF NOT EXISTS policy_visits(profile TEXT,location TEXT,count INTEGER NOT NULL,PRIMARY KEY(profile,location));
        ''')
    def track_pose(self,minimap,floor):
        if minimap.get('status')!='observed' or not floor:return minimap
        if minimap.get('sensor')=='fixed_grid_54x32':return minimap
        space=(tuple(floor),tuple(minimap.get('source_size',[])),tuple(minimap.get('analysis_size',[])),minimap['cell_size'])
        if getattr(self,'pose_space',None)!=space:
            self.pose_space=space;self.pose_anchors=[]
        if hasattr(self,'runtime_navigation'):self.runtime_navigation.update(anchors=self.pose_anchors,space=self.pose_space)
        point=minimap['player'];radius=minimap['cell_size']*.3
        nearest=min(self.pose_anchors,key=lambda a:math.dist(point,a[0]),default=None)
        result=copy.deepcopy(minimap);result['raw_player_cell']=list(minimap['player_cell'])
        if nearest and math.dist(point,nearest[0])<=radius:
            result['player_cell']=list(nearest[1])
        else:
            if nearest:
                step=tuple(round((point[i]-nearest[0][i])/minimap['cell_size']) for i in (0,1))
                distance=math.dist(point,nearest[0])/minimap['cell_size']
                if .65<=distance<=1.4 and sum(abs(v) for v in step)==1:
                    result['player_cell']=[nearest[1][i]+step[i] for i in (0,1)]
            if len(self.pose_anchors)>=2048:self.pose_anchors.clear()
            self.pose_anchors.append((tuple(point),tuple(result['player_cell'])))
        return result

    @staticmethod
    def stable_map(first,last):
        if first.get('status')!='observed' or last.get('status')!='observed':return False
        if any(first.get(k)!=last.get(k) for k in ('source_size','analysis_size','cell_size')):return False
        tile=first['cell_size']
        return bool(math.dist(first['player'],last['player'])<=tile*.3
            and all(abs(first['bbox'][i]-last['bbox'][i])<=tile*.5 for i in range(4))
            and abs(first['known_pixels']-last['known_pixels'])<=max(8,first['known_pixels']*.03))
    def observe(self,png,native=None,map_display=False,reference=None):
        digest=hashlib.sha256(png).hexdigest()
        if native and native.get('frame_sha256') and native['frame_sha256']!=digest:native=None
        if reference is not None:
            import copy
            metrics=copy.deepcopy(reference['metrics']);confirmation=copy.deepcopy(reference.get('floor_confirmation',{}))
        else:
            metrics=self.reader.reconcile(native or {'status':'unavailable'})['metrics']
            raw_floor=metrics.get('floor',{}).get('values')
            floor_quote=compact_text(metrics.get('floor',{}).get('quote',''))
            weak_floor=any(compact_text(line.get('text',''))==floor_quote and isinstance(line.get('confidence'),(int,float)) and line['confidence']<.8 for line in (native or {}).get('lines',[]))
            if weak_floor:raw_floor=None
            tokens=[l['recognition_at'] for l in (native or {}).get('lines',[]) if compact_text(l.get('text',''))==floor_quote and l.get('recognition_at') is not None]
            confirmation=self.floor_confirmation.update(raw_floor,min(tokens) if tokens else (native or {}).get('captured_at',digest))
            confirmation['rejected_low_confidence']=weak_floor
            if confirmation['changed']:
                self.map_sensor.reset();self.routes.reset();self.visits.clear();self.trajectory.clear()
                self.pose_anchors=[];self.pose_space=None
                self.runtime_navigation.update(anchors=self.pose_anchors,space=None)
        floor_ready=confirmation['status'] in ('initialized','confirmed','changed')
        minimap=read_grid(png) if map_display and floor_ready else {'status':'unavailable','reason':'地図専用表示または階の確認待ち。'}
        if floor_ready:minimap=self.map_sensor.accumulate(minimap,confirmation['confirmed'])
        if not floor_ready:metrics.pop('floor',None)
        text='\n'.join(compact_text(line.get('text','')) for line in (native or {}).get('lines',[]))
        # A visible map may remain under a menu. Do not infer an input-ready scene from colors alone.
        menu=bool((reference or {}).get('menu_evidence') or torneko_menu_kind(native) or result_screen(native)) or any(token in text for token in ('メニュー','持ち物','どうぐ','道具','装備','設定','セーブ','選択してください','キャンセル','はい','いいえ'))
        hp=metrics.get('hp',{}).get('values');floor=metrics.get('floor',{}).get('values')
        minimap=self.track_pose(minimap,floor)
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
        if map_display:payload['observation_mode']='num7_held'
        if not eligible:payload.update(visual=visual,text_key=text_key)
        state=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:24]
        location=hashlib.sha256(json.dumps([floor,minimap.get('shape'),minimap.get('player_cell')]).encode()).hexdigest()[:24] if eligible else state
        return {'metrics_source_frame_sha256':reference.get('frame_sha256') if reference else digest,'metrics_source':'normal_capture_before_map_display' if reference else 'current_frame','frame_sha256':digest,'observable':bool(visual and text),'visual':visual,'text_key':text_key,'id':state,'location':location,'eligible':eligible,'objective':objective,'minimap':minimap,'metrics':metrics,'menu_evidence':menu,'floor_confirmation':confirmation,
            'note':'状態は画面内の地図形状・位置等の推定。ワールド座標や因果関係は未検証。'}
    def values(self,state):
        rows=self.db.execute('SELECT action,trials,q,positive,reward_sum,goal_trials,goal_wins FROM policy_actions WHERE profile=? AND state=? AND objective=?',
            (self.profile,state['id'],state['objective'])).fetchall()
        return {r[0]:dict(zip(('trials','q','positive','reward_sum','goal_trials','goal_wins'),r[1:])) for r in rows}
    def context(self,state,blocked=()):
        node=self.routes.node(state)
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
            hazard=.15 if neighbor.get('enemy') else 0.
            historical_q=.35*max(-1.,min(1.,value['q']))
            historical_backoff=.1*max(-1.,min(1.,backoff['q']))/(n+1)
            score=historical_q+exploration+spatial+historical_backoff+goal_bias-hazard
            allowed=key in MOVES and state['eligible'] and [key] not in blocked and self.routes.no_effect[node,key]<2
            ranking.append({'keys':[key],'meaning':ACTIONS[key],**value,'historical_backoff_contribution':historical_backoff,'historical_q_contribution':historical_q,'priority':round(score,5),'game_hazard_cost':hazard,'global_backoff':backoff,'verified_goal_bias':goal_bias,
                'progress_probability_estimate':round(prob,4) if n else None,'probability_samples':n,
                'probability_posterior_stddev':round(math.sqrt(prob*(1-prob)/(n+3)),4),
                'verified_goal_success_probability':round((value['goal_wins']+1)/(value['goal_trials']+2),4) if value['goal_trials'] else None,
                'automatic_candidate':allowed,'model_may_propose':True})
        ranking.sort(key=lambda v:v['priority'],reverse=True)
        m=state.get('minimap',{})
        spatial={'status':m.get('status','unavailable'),'sensor':m.get('sensor'),
            'source_frame_sha256':state.get('frame_sha256'),'floor_confirmation':state.get('floor_confirmation'),
            'dimensions':m.get('grid_size'),'position':m.get('player_cell'),
            'enemy_offsets':m.get('enemies',[]),'item_offsets':m.get('items',[]),'stair_offsets':m.get('stairs',[]),
            'neighbors':m.get('neighbors',{}),'known_cell_count':len(m.get('accumulated_cells',[])),
            'rule':'専用表示から読み取った格子座標。右が+x、下が+y。未検出は不明。形状分類は推定で、接近や通行の成功を保証しない。'}
        return {'spatial_observation':spatial,'version':VERSION,'profile':self.profile,'objective':state['objective'],'state_id':state['id'],'eligible':state['eligible'],
            'no_effect_moves':{k:self.routes.no_effect.get((tuple(state.get('minimap',{}).get('player_cell',[])),k),0) for k in MOVES},
            'navigation_instruction':'同条件で2回以上位置変化がない方向は自動巡回しない。壁や入力故障とは断定せず、現在画面から状態異常・決定待ち・入力長・所持品など別の操作仮説を検討する。',
            'ranking':ranking,'control_reference':REFERENCE,
            'note':'priorityは過去TD値を±1に制限して35%だけ反映し、現在の候補と未試行の探索加点を併用する。過去の行動名は意味や成功の証明ではない。確率は正の観測報酬の推定でありゲームクリア確率ではない。試行0は未推定。全体目標の確率は検証済み終了エピソードがある場合のみ。'}
    def choose(self,decision,state,blocked=(),stable=False,adjustment=None,respect_proposal=False):
        context=self.context(state,blocked)
        if not stable or decision.status!='continue' or decision.risk!='normal' or not state['eligible']:
            return decision,{'source':'model_or_neutral','context':context}
        if respect_proposal and decision.confidence>=self.settings.min_confidence and decision.keys not in blocked and not (len(decision.keys)==1 and context.get('no_effect_moves',{}).get(decision.keys[0],0)>=2):
            return decision,{'source':'operator_guided_model','context':context}
        food=state['metrics'].get('fullness',{}).get('values')
        node=self.routes.node(state)
        exhausted=[k for k in MOVES if self.routes.no_effect[node,k]>=2]
        if food and food[0]>15:self.routes.resource_inspected=None
        resource_key=(tuple(state['metrics'].get('floor',{}).get('values',[])),tuple(food or []))
        resource_inspection=bool(decision.scene not in ('dialog','menu','dialogue') and food and food[0]<=15 and self.routes.resource_inspected!=resource_key and not state['minimap'].get('enemies') and (not decision.keys or all(k in MOVES for k in decision.keys)))
        stuck_inspection=decision.scene not in ('dialog','menu','dialogue') and len(exhausted)==4 and (not decision.keys or all(k in MOVES for k in decision.keys))
        if resource_inspection or stuck_inspection:
            # A single known menu key, then fresh observation; never an eating macro.
            if 'num0' in self.settings.allowed_keys and ['num0'] not in blocked and (resource_inspection or not getattr(self.routes,'inspection_node',None)==node):
                self.routes.inspection_node=node
                if resource_inspection:self.routes.resource_inspected=resource_key
                return replace(decision,scene='gameplay',keys=['num0'],hold_ms=min(80,self.settings.max_hold_ms),confidence=.85,
                    reason='現在の満腹度が低いため食料の候補を確認する。' if resource_inspection else '複数方向で位置変化を観測できないため、メニューで状態・利用可能な操作を確認し直す。',
                    subgoal='所持品・状態の確認',goal_success='新しい画面で所持品と操作候補を確認する',progress=[],memory_updates=[],plan=[]),{'source':'resource_inspection' if resource_inspection else 'navigation_inspection','context':context,'learning':False}
            return replace(decision,keys=[],confidence=0.,reason='無移動を壁やBackend故障と断定せず、別の操作仮説を再検討する。'),{'source':'navigation_reassessment_needed','context':context}
        # Game-world danger is evaluated as a cost rather than a veto.
        # Respect a concrete non-movement operation; do not turn an attack into walking.
        reconsidered=None
        if decision.keys and any(key not in MOVES for key in decision.keys):
            prior=self.values(state).get(decision.keys[0],{}) if len(decision.keys)==1 else {}
            goals=[compact_text(decision.subgoal),compact_text(decision.goal_success)]
            hud_only=(decision.scene in ('gameplay','unknown') and decision.keys==['num1']
                and not state.get('menu_evidence') and not state['minimap'].get('enemies')
                and all(re.search(r'HP|満腹',s,re.I) and re.search(r'確認|把握|読む|観察',s) for s in goals)
                and not re.search(r'回復|攻撃|話|会話|決定|装備|食べ|使|購入|はい|いいえ|階段|扉|道具|メニュー|拾|敵',''.join(goals))
                and bool(state['metrics'].get('hp'))
                and ('満腹' not in ''.join(goals) or bool(state['metrics'].get('fullness'))))
            if hud_only:
                reconsidered={'keys':list(decision.keys),'source':'already_observed_hud',
                    'reason':'小目標は既に読めたHUDの確認だけで、決定入力の必要性を確認できません。現在の地図と経験から探索候補を再選択。'}
            elif (decision.scene in ('gameplay','unknown') and decision.keys[0] in ('num0','num1','num2') and not state['minimap'].get('enemies')
                    and prior.get('trials',0)>=2 and prior.get('reward_sum',0)<0):
                reconsidered={'keys':list(decision.keys),'trials':prior['trials'],'reward_sum':prior['reward_sum'],
                    'reason':'探索状態で過去の効果が乏しい非移動操作を再提案。別の未試行候補を選び直す。'}
            elif (decision.scene in ('gameplay','unknown') and decision.keys==['num1'] and state['minimap'].get('enemies')
                    and all(max(abs(x),abs(y))>1 for x,y in state['minimap']['enemies'])
                    and prior.get('trials',0)>=2 and prior.get('reward_sum',0)<0
                    and re.search(r'敵|攻撃|倒',decision.reason+' '+decision.subgoal)):
                reconsidered={'keys':['num1'],'reason':'敵表示は隣接範囲外で、この操作の効果が乏しい経験があるため、射程を仮定せず別の候補を試す。'}
            else:return decision,{'source':'model_nonmovement','context':context}
        candidates=[v for v in context['ranking'] if v['automatic_candidate'] and v['keys'][0] not in exhausted]
        if not candidates:return replace(decision,keys=[]),{'source':'neutral_no_safe_candidate','context':context}
        for candidate in candidates:
            penalty=self.routes.penalty(state,candidate['keys'][0])
            candidate['session_revisit_penalty']=penalty
            candidate['priority']=round(candidate['priority']-penalty,5)
        candidates.sort(key=lambda v:v['priority'],reverse=True)
        if adjustment:
            strategy=adjustment.get('strategy')
            for candidate in candidates:
                key=candidate['keys'][0]
                delta=(.6/(candidate['trials']+1) if strategy=='explore_untried' else
                    (-.8 if strategy=='avoid_cycle' and [state['id'],key] in adjustment.get('avoid',[]) else 0.))
                candidate['adjustment']=delta;candidate['priority']=round(candidate['priority']+delta,5)
            candidates.sort(key=lambda v:v['priority'],reverse=True)
        # Least visited actions break cold-start ties deterministically; learned Q then dominates.
        context['ranking'].sort(key=lambda v:v['priority'],reverse=True)
        hint=state.get('map_route_hint')
        route=hint if hint and any(v['keys']==[hint['key']] for v in candidates) else self.routes.suggest(state,candidates)
        selected=next((v for v in candidates if route and v['keys']==[route['key']]),candidates[0]);key=selected['keys'][0]
        reason=('地図の自位置・既知領域と、この状態で蓄積した行動価値・試行回数から優先度を選択しました。' if selected['trials'] else 'この状態では未試行の候補です。地図と安全条件を確認し、経験を蓄積するための短い移動を選択しました。')
        if adjustment:reason+=' 停滞の原因仮説を検証するため、許可された移動候補内で優先度を一時調整しています。'
        if route:reason+=' 観測済みの床の接続または移動実績から経路を選び、1入力ずつ位置を確認します。'
        caption=f"地図の黄色マーカーを解析座標{state['minimap']['player']}に観測。既知領域は{len(state['minimap'].get('known_cells',[]))}マス。格子上の推定値です。"
        chosen=replace(decision,scene='gameplay',observation=caption,keys=[key],hold_ms=min(80,self.settings.max_hold_ms),wait_ms=600,
            confidence=.8,reason=reason,subgoal='食料を探すため未確認の通路を探索する' if state['objective']=='forage' else 'ミニマップ上の未訪問位置または未確認の通路を探索する',
            goal_success='地図の自位置の移動または既知領域の増加を次の観測で確認する',
            progress=[],memory_updates=[],plan=[],goal_evidence=[],knowledge_assessments=[])
        return chosen,{'source':'learned_policy','reconsidered_nonmovement':reconsidered,'selection_basis':'exploration_route' if route else 'adjusted_td_priority','selected':selected,'context':context,'adjustment':adjustment,'exploration_route':route,
            'safety':'自位置とメニュー不在を再確認した短い移動。ゲーム内の危険は候補から除外せず、損失と発見を評価。危険時の高速連続実行はしない。'}
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
        if af and bf and af['values']==bf['values'] and a.get('known_cells') and b.get('known_cells') and a.get('cell_size')==b.get('cell_size'):
            ac={(x+a['player_cell'][0],y+a['player_cell'][1]) for x,y in a['known_cells']}
            bc={(x+b['player_cell'][0],y+b['player_cell'][1]) for x,y in b['known_cells']}
            growth=len(bc-ac)
            if growth>=3:out['coverage']=min(1.,growth/max(1,len(ac)))
        return out
    def learn(self,before,after,row,neutral_before=None):
        excluded=(str(row.get('event','')).startswith('diagnostic') or row.get('kind') in ('compatibility','separation') or not outcome_learning_allowed(row)) or not row.get('executed') or row.get('dry_run') or row.get('turn_kind')=='aborted' or row.get('action_kind')!='input'
        receipt=row.get('input_receipt')
        if excluded or not isinstance(receipt,dict) or receipt.get('os_submission')!='accepted':
            return {'status':'not_trained','reason':'未送信・無入力・中断・診断・送信受付未確認は入力の成功経験にしません。'}
        keys=row.get('observed_keys',[])
        if len(keys)!=1 or keys[0] not in ACTIONS:return {'status':'not_trained','reason':'この方策は単独の確認済みキーを対象にします。'}
        if keys==['num7']:return {'status':'not_trained','reason':'地図表示は補助観測であり、攻略の成功経験に加算しません。'}
        if not before.get('observable') or not after.get('observable'):return {'status':'censored','reason':'比較可能な画像とOCRを取得できません。成功にも失敗にも加算しません。'}
        if before.get('observation_mode','normal')!=after.get('observation_mode','normal'):
            return {'status':'censored','reason':'通常画面と専用地図表示の切替は入力成果として比較しません。'}
        action=keys[0];observed=self.signals(before,after)
        natural=self.signals(neutral_before,before) if neutral_before else {k:0. for k in observed}
        net={k:max(0.,v-natural[k]) for k,v in observed.items()}
        session_revisits=self.routes.visits(after)
        # Register departure before arrival so returning with a changed map hash
        # cannot make a previously visited screen location novel again.
        if before.get('eligible') and after.get('eligible') and before['minimap'].get('player_cell')==after['minimap'].get('player_cell'):
            session_revisits=max(1,session_revisits)
        self.routes.record(before,after,action,natural)
        # Returning to an already observed location is not exploration progress.
        # Register the departure too, so A -> B -> A cannot reward both halves.
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO policy_visits VALUES(?,?,1)',(self.profile,before['location']))
        visit=self.db.execute('SELECT count FROM policy_visits WHERE profile=? AND location=?',(self.profile,after['location'])).fetchone()
        revisits=max(visit[0] if visit else 0,session_revisits)
        novelty=0. if revisits else 1.
        route=(row.get('policy_choice') or {}).get('exploration_route') or {}
        route_transit=bool((row.get('policy_choice') or {}).get('source')=='learned_policy' and route.get('mode')=='observed_route'
            and route.get('distance',0)>0 and route.get('key')==action and net['position'])
        revisit_cost=.12*min(3,revisits) if net['position'] and not route_transit and not any(net[k] for k in ('coverage','floor','enemy_reduction','food_gain')) else 0.
        reward=.35*net['position']*novelty+.6*net['coverage']+net['floor']+.3*net['enemy_reduction']+.8*net['food_gain']-.04-revisit_cost
        hp0=before['metrics'].get('hp',{}).get('values');hp1=after['metrics'].get('hp',{}).get('values')
        if hp0 and hp1 and hp0[1]==hp1[1]:reward-=2*max(0.,hp0[0]-hp1[0])/hp0[1]
        # No positive reward for natural HP regeneration or simply remaining at full HP.
        reward=max(-1.,min(1.,reward));positive=reward>0
        current=self.values(before).get(action,{'q':0.})['q']
        next_q=max(0.,max((v['q'] for k,v in self.values(after).items() if k in MOVES and k in self.settings.allowed_keys),default=0.))
        new_q=current+.25*(reward+.85*next_q-current)
        event={'status':'trained','state':before['id'],'next_state':after['id'],'objective':before['objective'],'keys':keys,
            'reward':reward,'q_before':current,'q_after':new_q,'observed':observed,'neutral_baseline':natural,'baseline_kind':row.get('policy_baseline_kind','neutral_observation'),'net_signals':net,
            'scoring_version':VERSION,'route_transit':route_transit,'novelty_weight':novelty,'arrival_visits_before':revisits,'revisit_cost':revisit_cost,'positive_progress_proxy':positive,'attribution':'observed_association_not_causal',
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
