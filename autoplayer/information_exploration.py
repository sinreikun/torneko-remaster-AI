"""Persistent, situation-specific input coverage, separate from goal/reward."""
import hashlib,json,re
from dataclasses import replace
from .controls import is_torneko
from .interfaces import compact_text,torneko_menu_kind,result_screen,main_menu_context
from .active_input import DANGEROUS


class InformationExploration:
    def __init__(self,cfg,memory=None):
        self.cfg=cfg;self.db=memory.db if memory else None;self.profile=memory.profile if memory else 'session';self.cache={}
        if self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS input_coverage_v1(profile TEXT,situation TEXT,action TEXT,data TEXT,PRIMARY KEY(profile,situation,action))');self.db.commit()
    def actions(self):
        allowed=set(self.cfg.allowed_keys)
        actions=[[k] for k in self.cfg.allowed_keys if not (is_torneko(self.cfg.window_title) and k in ('num3','num5','num7'))]
        if is_torneko(self.cfg.window_title):
            actions += [['num5',k] for k in ('w','a','s','d') if {'num5',k}<=allowed]
            actions += [[a,b] for a,b in (('w','a'),('w','d'),('s','a'),('s','d')) if {a,b}<=allowed]
        return actions or [[k] for k in self.cfg.allowed_keys]
    def situation(self,decision,native,state):
        state=state or {};m=state.get('minimap',{});hp=state.get('metrics',{}).get('hp',{}).get('values')
        interface=torneko_menu_kind(native) or result_screen(native)
        scene=interface or ('gameplay' if state.get('eligible') else 'unclassified')
        if scene=='gameplay':
            features={'scene':scene,'contacts':sorted([list(p) for p in m.get('enemies',[]) if max(map(abs,p))<=1]),
                'map_observed':m.get('status')=='observed','hp_band':min(3,int(hp[0]/hp[1]*4)) if hp and hp[1] else None,
                'adjacent_known':{k:bool(v.get('known')) for k,v in m.get('neighbors',{}).items()}}
        elif scene=='main_menu':
            features={'scene':scene,**main_menu_context(native)}
        elif scene=='unclassified' and state.get('interface_layout_group'):
            features={'source':'ocr_layout_coverage_v1','layout':state['interface_layout_group']['id']}
        else:
            text=[compact_text(l.get('text','')) for l in (native or {}).get('lines',[])]
            features={'scene':scene,'labels':[re.sub(r'\d+','N',t)[:100] for t in text if not re.search(r'HP|満腹|Lv|^\d+[階G%]$',t,re.I)][:12]}
        return hashlib.sha256(json.dumps(features,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:24]
    def stats(self,situation,action):
        key=(situation,tuple(action))
        if key not in self.cache:
            row=self.db.execute('SELECT data FROM input_coverage_v1 WHERE profile=? AND situation=? AND action=?',(self.profile,situation,json.dumps(action))).fetchone() if self.db else None
            self.cache[key]=json.loads(row[0]) if row else {'sent':0,'observed':0,'outcomes':[]}
            while len(self.cache)>2048:self.cache.pop(next(iter(self.cache)))
        return self.cache[key]
    def context(self,decision,native,state):
        situation=self.situation(decision,native,state);rows=[]
        for action in self.actions():
            data=self.stats(situation,action);needed=4 if len(data['outcomes'])>1 else 2
            rows.append({'keys':action,'observed':data['observed'],'required':needed,'sent':data['sent']})
        return {'situation':situation,'initial_coverage_ready':all(r['observed']>=r['required'] for r in rows),
            'actions':rows,'rule':'情報収集は全体目標への手段。未試行・観測不足の操作を優先する。回数は初期情報の目安で、理解・成功・因果確定ではない。'}
    def choose(self,decision,native,state,blocked=()):
        context=self.context(decision,native,state)
        text='\n'.join(l.get('text','') for l in (native or {}).get('lines',[]))
        if decision.status!='continue' or DANGEROUS.search(text) or result_screen(native) or re.search(r'はい|いいえ|ロード中|読み込み中',text):return decision,context
        if context['initial_coverage_ready']:return decision,context
        candidates=[r for r in context['actions'] if r['observed']<r['required']]
        # Temporary no-response backoff changes priority, never removes all input.
        recent={tuple(k) for k in blocked}
        chosen=min(candidates,key=lambda r:(r['observed']>0,r['observed']/r['required'],tuple(r['keys']) in recent,r['sent']))
        context.update(selected=chosen['keys'],source='information_collection',learning=False)
        return replace(decision,keys=chosen['keys'],hold_ms=min(80,self.cfg.max_hold_ms),wait_ms=min(600,self.cfg.max_wait_ms),
            intent={'kind':'explore','target_id':''},forecast={},plan=[],progress=[],memory_updates=[],goal_evidence=[],knowledge_assessments=[],progress_test={},
            subgoal='操作と反応を比較して全体目標に使う情報を集める',goal_success='実際の送信キーと入力後の観測を対応付けて記録する',
            reason='全体目標を維持し、この状況で未試行または反応の観測が不足する操作を短く試して情報を集める。'),context
    def record(self,context,row,before,after,old_native,new_native,after_image):
        if not row.get('executed') or row.get('dry_run'):return {'status':'not_recorded'}
        action=row.get('observed_keys',[])
        if not action:return {'status':'not_recorded'}
        situation=context['situation'];data=self.stats(situation,action);data['sent']+=1
        hold=row.get('hold_ms')
        if isinstance(hold,int):
            data['hold_ms_range']=[min(hold,data.get('hold_ms_range',[hold,hold])[0]),max(hold,data.get('hold_ms_range',[hold,hold])[1])]
        # Only a captured post-input frame provides a response observation. An
        # unchanged frame is useful data, not an input-backend failure.
        observed=bool(after_image)
        if observed:
            data['observed']+=1
            a=(before or {}).get('metrics',{});b=(after or {}).get('metrics',{})
            delta={k:[a[k].get('values'),b[k].get('values')] for k in a.keys()&b.keys() if a[k].get('values')!=b[k].get('values')}
            labels=[compact_text(l.get('text','')) for l in (new_native or {}).get('lines',[])][:12]
            player0=(before or {}).get('minimap',{}).get('player_cell');player1=(after or {}).get('minimap',{}).get('player_cell')
            movement=[player1[i]-player0[i] for i in (0,1)] if player0 and player1 and a.get('floor',{}).get('values')==b.get('floor',{}).get('values') else None
            excess_activity=row.get('raw_change',0)>(row.get('neutral_before') or {}).get('raw_change',0)+.01
            signature=hashlib.sha256(json.dumps({'metrics':delta,'player_delta':movement,'above_neutral_activity':excess_activity,'interface':torneko_menu_kind(new_native),'labels':labels},ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:16]
            if signature not in data['outcomes']:data['outcomes']=(data['outcomes']+[signature])[-4:]
        if self.db:
            self.db.execute('INSERT INTO input_coverage_v1 VALUES(?,?,?,?) ON CONFLICT(profile,situation,action) DO UPDATE SET data=excluded.data',(self.profile,situation,json.dumps(action),json.dumps(data)));self.db.commit()
        return {'status':'response_observed' if observed else 'sent_only','situation':situation,'keys':action,**data,
            'attribution':'temporal_association_not_proven_cause','natural_observation':row.get('neutral_before'),'goal_success_verified':False}
