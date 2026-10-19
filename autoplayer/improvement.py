"""Bounded hypothesis trials. Model proposals never judge their own success."""
import hashlib
import json
import re
import time

STRATEGIES=('explore_untried','avoid_cycle','reassess')
SIGNALS=('coverage','floor','food_gain','enemy_reduction')
REVIEW_SCHEMA={'type':'object','additionalProperties':False,'properties':{
    'hypothesis':{'type':'string'},'strategy':{'type':'string','enum':list(STRATEGIES)},
    'evidence_steps':{'type':'array','items':{'type':'integer'}},
    'expected_signal':{'type':'string','enum':list(SIGNALS)}},
    'required':['hypothesis','strategy','evidence_steps','expected_signal']}

class ImprovementSupervisor:
    def __init__(self,policy):
        self.policy=policy;self.db=policy.db;self.profile=policy.profile
        self.recent=[];self.tick=0;self.next_review=6;self.calls=0;self.active=None;self.adopted=None
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS improvement_events(id INTEGER PRIMARY KEY,profile TEXT,created REAL,data TEXT);
        CREATE INDEX IF NOT EXISTS improvement_profile ON improvement_events(profile,id);
        CREATE TABLE IF NOT EXISTS improvement_state(profile TEXT PRIMARY KEY,data TEXT);
        ''')
        row=self.db.execute('SELECT data FROM improvement_state WHERE profile=?',(self.profile,)).fetchone()
        self.attempts={}
        if row:
            saved=json.loads(row[0]);self.attempts=saved.get('attempts',{});self.adopted=saved.get('adopted')
            if saved.get('active'):self.event('reverted',reason='再起動で中断された検証は採用しません。',trial=saved['active'])
        self.save()
    def save(self):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO improvement_state VALUES(?,?)',(self.profile,json.dumps(
                {'attempts':self.attempts,'active':self.active,'adopted':self.adopted},ensure_ascii=False)))
    def event(self,status,**data):
        value={'status':status,**data}
        with self.db:self.db.execute('INSERT INTO improvement_events(profile,created,data) VALUES(?,?,?)',
            (self.profile,time.time(),json.dumps(value,ensure_ascii=False)))
        return value
    def begin_step(self):
        self.tick+=1
        if self.active:
            self.active['elapsed']+=1
            if self.active['elapsed']>8:self.finish(False,'8判断以内に比較可能な結果が揃わず撤回。')
        elif self.adopted:
            self.adopted['remaining']-=1
            if self.adopted['remaining']<=0:
                self.event('expired',reason='採用期間24判断が終了。通常の方策へ戻します。');self.adopted=None
        self.save()
    def signature(self):
        # Ignore model captions and blue-shape jitter when recognizing the same loop.
        locations=sorted({json.dumps([r.get('floor'),r.get('cell')],sort_keys=True) for r in self.recent[-6:]})
        return hashlib.sha256(json.dumps(locations).encode()).hexdigest()[:24]
    def due(self):
        rows=self.recent[-6:]
        return (len(rows)==6 and self.tick>=self.next_review and self.calls<3 and not self.active and not self.adopted
            and all(r['progress']==0 for r in rows) and (sum(r['comparable'] for r in rows)>=2 or sum(r['observable'] for r in rows)>=2)
            and len(self.evidence()['unavailable_strategies'])<len(STRATEGIES))
    def evidence(self):
        return {'task':'self_improvement_review','overall_goal':self.policy.settings.goal,'trigger':'直近6結果で独立した進捗信号がない。故障や失敗の確定ではない。',
            'observations':self.recent[-6:],'unavailable_strategies':list(dict.fromkeys(self.attempts.get(self.signature(),[])+(['explore_untried','avoid_cycle'] if sum(r['comparable'] for r in self.recent[-6:])<2 else []))),
            'strategies':{'explore_untried':'安全条件を満たす移動候補の未試行加点を一時的に増やす',
                'avoid_cycle':'同じ観測状態で直近に実行した移動候補の優先度を一時的に下げる',
                'reassess':'現在画像・OCRを読み直す観点を仮説として攻略LLMに提示する。観察事実の上書きは禁止'},
            'limits':'一度に1仮説。最大4比較可能入力・8判断。生の画面差分や往復移動を改善指標にしない。日本語で回答。'}
    def review(self,llm):
        if not self.due():return None
        self.calls+=1;self.next_review=self.tick+12
        evidence=self.evidence();signature=self.signature()
        try:
            proposal=llm.review_strategy(evidence)
            if not isinstance(proposal,dict) or set(proposal)!=set(REVIEW_SCHEMA['required']):raise ValueError('提案の形式が不正です。')
            if proposal['strategy'] not in STRATEGIES or proposal['expected_signal'] not in SIGNALS:raise ValueError('許可されない調整です。')
            text=proposal['hypothesis']
            if not isinstance(text,str) or not 4<=len(text)<=600 or not re.search('[ぁ-んァ-ヶ一-龥]',text):raise ValueError('日本語の有限な仮説が必要です。')
            steps=proposal['evidence_steps'];available={r['step'] for r in evidence['observations']}
            if not isinstance(steps,list) or not 1<=len(steps)<=6 or any(type(s) is not int or s not in available for s in steps):raise ValueError('履歴に存在する根拠が必要です。')
            if proposal['strategy'] in evidence['unavailable_strategies']:raise ValueError('現在の証拠では許可されない、または再試行済みの調整です。')
            tried=self.attempts.setdefault(signature,[])
            if proposal['strategy'] in tried:raise ValueError('同じ状況で試した調整の反復は保留します。')
            tried.append(proposal['strategy'])
            # Keep the persistent rejection ledger bounded.
            if len(self.attempts)>128:self.attempts.pop(next(iter(self.attempts)))
            self.active={'proposal':proposal,'signature':signature,'floor':self.recent[-1]['floor'],
                'elapsed':0,'samples':0,'score':0.,'baseline':sum(r['progress'] for r in self.recent[-6:])/6,
                'avoid':[[r['state'],r['keys'][0]] for r in self.recent[-6:] if len(r['keys'])==1]}
            metrics=getattr(llm,'request_metrics',None)
            result=self.event('trial_started',trial=self.active,evidence=evidence,inference_metrics=metrics[-1:] if isinstance(metrics,list) else [])
        except (ValueError,KeyError,TypeError,RuntimeError,OSError) as error:
            result=self.event('review_rejected',reason=str(error)[:600],evidence=evidence)
        self.save();return result
    def context(self):
        current=self.active or self.adopted
        return {'status':'trial' if self.active else ('adopted_temporary' if self.adopted else 'normal'),
            'proposal':current['proposal'] if current else None,'review_calls':self.calls,'maximum_review_calls':3,
            'note':'仮説は未確認のモデル提案。画像・OCR・安全条件に優先しない。採用は観測上の関連であり因果の証明ではない。'}
    def adjustment(self,state):
        current=self.active or self.adopted
        if not current or state['metrics'].get('floor',{}).get('values')!=current['floor']:return None
        return {'strategy':current['proposal']['strategy'],'avoid':current['avoid']}
    def finish(self,adopt,reason):
        trial=self.active
        if not trial:return None
        if adopt:self.adopted={**trial,'remaining':24}
        self.active=None;self.next_review=self.tick+12
        result=self.event('adopted_temporary' if adopt else 'reverted',reason=reason,trial=trial,
            attribution='observed_association_not_causal')
        self.save();return result
    def consume(self,row):
        if row.get('dry_run') or row.get('turn_kind')=='aborted' or str(row.get('event','')).startswith('diagnostic'):return None
        before=row.get('policy_state') or {};after=row.get('policy_after') or {};learning=row.get('policy_learning') or {}
        comparable=(learning.get('status')=='trained' and row.get('executed') and row.get('learning') is not False
            and row.get('action_kind')=='input' and (row.get('input_receipt') or {}).get('os_submission')=='accepted')
        net=learning.get('net_signals',{}) if comparable else {}
        progress=sum(max(0.,net.get(k,0.)) for k in SIGNALS)
        hp0=before.get('metrics',{}).get('hp',{}).get('values');hp1=after.get('metrics',{}).get('hp',{}).get('values')
        damage=bool(hp0 and hp1 and hp0[1]==hp1[1] and hp1[0]<hp0[0])
        record={'step':row['step'],'state':before.get('id'),'floor':before.get('metrics',{}).get('floor',{}).get('values'),
            'cell':before.get('minimap',{}).get('player_cell'),'keys':row.get('observed_keys',[]),
            'comparable':bool(comparable),'observable':bool(before.get('observable')),'progress':progress,'signals':net,'hp_loss':damage,
            'map_status':before.get('minimap',{}).get('status'),'menu_evidence':before.get('menu_evidence'),
            'hud':{k:v.get('values') for k,v in before.get('metrics',{}).items() if isinstance(v,dict)},
            'model_reports_unverified':{'scene':(row.get('scene_observation') or {}).get('scene_type'),
                'subgoal':str((row.get('llm_proposal') or row).get('subgoal',''))[:180],
                'goal_success':str((row.get('llm_proposal') or row).get('goal_success',''))[:180],
                'response_issue':str(row.get('model_response_issue',''))[:180]}}
        self.recent=(self.recent+[record])[-12:]
        result=None
        current=self.active or self.adopted
        if current and record['floor']!=current['floor']:
            if self.active:result=self.finish(False,'検証前の場面条件が変わったため撤回。')
            else:self.adopted=None;result=self.event('expired',reason='適用階層が変わりました。')
        elif current and damage:
            if self.active:result=self.finish(False,'HP減少を観測したため直ちに撤回。因果は未確定。')
            else:self.adopted=None;result=self.event('reverted',reason='採用後にHP減少を観測。通常方策へ復帰。')
        elif self.active and comparable and (self.active['proposal']['strategy']=='reassess' or
                ((row.get('policy_choice') or {}).get('adjustment') or {}).get('strategy')==self.active['proposal']['strategy']):
            self.active['samples']+=1
            signal=self.active['proposal']['expected_signal']
            self.active['score']+=max(0.,net.get(signal,0.))
            if self.active['samples']>=4:
                adopt=self.active['score']/4>self.active['baseline'] and self.active['score']>0
                result=self.finish(adopt,'事前指定の進捗信号を中立観測と比較。短期の観測結果として期限付き採用。' if adopt else '4入力で指定指標の改善を確認できず撤回。')
        self.save();return result
