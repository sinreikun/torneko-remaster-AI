"""Conditioned empirical effects and bounded experiments, not semantic truth."""
from collections import Counter,deque
import copy,hashlib,json,re,time
from .controls import REFERENCE,is_torneko
from .interfaces import compact_text,has_text_content

class ActionEffects:
    def __init__(self,memory,cfg):
        self.db=memory.db;self.cfg=cfg;self.profile=hashlib.sha256(json.dumps([memory.profile,cfg.allowed_keys,cfg.key_bindings],sort_keys=True).encode()).hexdigest()
        self.bindings=cfg.key_bindings or (REFERENCE['keys'] if is_torneko(cfg.window_title) else {})
        self.db.executescript('''CREATE TABLE IF NOT EXISTS action_effects_v1(profile TEXT,context TEXT,action TEXT,data TEXT,updated REAL,PRIMARY KEY(profile,context,action));
        CREATE TABLE IF NOT EXISTS operational_experiments_v1(profile TEXT,context TEXT,test TEXT,data TEXT,updated REAL,PRIMARY KEY(profile,context,test));''');self.db.commit()
        self.cache={};self.experiments={}
    def keys(self,role):
        patterns={'move':r'^(上|左|下|右)移動|^(up|left|down|right)$','face':r'方向転換','attack':r'攻撃|attack','confirm':r'決定|confirm','cancel':r'キャンセル|cancel','menu':r'メニュー|menu','step':r'足踏み','arrow':r'矢|shoot'}
        return [k for k,v in self.bindings.items() if k in self.cfg.allowed_keys and re.search(patterns[role],v,re.I)]
    def role(self,keys,state):
        if len(keys)==2 and any(k in self.keys('face') for k in keys) and any(k in self.keys('move') for k in keys):return 'orientation_candidate'
        if len(keys)==2 and set(keys) in ({'w','a'},{'w','d'},{'s','a'},{'s','d'}) and not (state.get('_operational_interface') or state.get('interface_kind') or state.get('menu_evidence') or state.get('white_frame_evidence',{}).get('visible')):return 'movement_candidate'
        if len(keys)!=1:return 'chord'
        key=keys[0]
        white=state.get('white_frame_evidence',{})
        interface=bool(state.get('_operational_interface') or state.get('interface_kind') or white.get('visible') is True or 'visible' not in white and state.get('menu_evidence'))
        if key in self.keys('move'):return 'cursor_candidate' if interface else 'movement_candidate'
        if key in self.keys('confirm'):return 'confirmation_candidate' if interface else 'attack_or_interaction_candidate'
        if key in self.keys('cancel'):return 'cancel_candidate' if interface else 'dash_candidate'
        for role in ('menu','arrow','step'):
            if key in self.keys(role):return role+'_candidate'
        return 'unclassified_input'
    def context_features(self,state,keys):
        role=self.role(keys,state);m=state.get('minimap',{});metrics=state.get('metrics',{})
        interface=role in ('cursor_candidate','confirmation_candidate','cancel_candidate')
        direction=keys[-1] if keys else ''
        nearby=sorted([list(p) for p in m.get('enemies',[]) if max(map(abs,p))<=2])
        feature={'role':role,'region':state.get('exterior_action_policy',{}).get('location','unclassified'),
          'interface':state.get('interface_kind') if interface else None,'map_observed':m.get('status')=='observed',
          'neighbor':m.get('neighbors',{}).get(direction,{}).get('known') if role=='movement_candidate' else None,'contacts':nearby,
          'conditions':{k:metrics.get(k,{}).get('values') for k in ('level','strength','sword','shield')} if nearby or role not in ('movement_candidate','orientation_candidate') else {},
          'layout':(state.get('interface_layout_group') or {}).get('id') if interface else None}
        # Unclassified UI states do not share outcomes just because they have a box.
        if interface and not feature['layout']:feature['text']=state.get('text_key')
        return feature
    def context_key(self,state,keys):
        return hashlib.sha256(json.dumps(self.context_features(state,keys),sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
    def samples(self,state,keys):
        context=self.context_key(state,keys);action=json.dumps(keys);key=(context,action)
        if key not in self.cache:
            row=self.db.execute('SELECT data FROM action_effects_v1 WHERE profile=? AND context=? AND action=?',(self.profile,*key)).fetchone()
            self.cache[key]=json.loads(row[0]) if row else []
            while len(self.cache)>256:self.cache.pop(next(iter(self.cache)))
        return context,self.cache[key]
    def estimate(self,state,keys):
        context,samples=self.samples(state,keys);comparable=[s for s in samples if s['comparable']]
        n=len(comparable);changed=sum(s.get('text_changed') is True for s in comparable);hits=sum(s.get('new_hit_message') is True for s in comparable);kills=sum(s.get('new_defeat_message') is True for s in comparable);moved=sum(s['moved'] is True for s in comparable);no_effect=sum(s['no_effect'] is True for s in comparable)
        mean=sum(s['value'] for s in comparable)/n if n else 0.
        return {'context':context,'role':self.role(keys,state),'samples':n,'recorded':len(samples),
          'text_change_rate':changed/n if n else None,'new_hit_message_rate':hits/n if n else None,'new_defeat_message_rate':kills/n if n else None,'move_rate':moved/n if n else None,'no_effect_rate':no_effect/n if n else None,'mean_observed_value':mean,
          'association':'supported' if n>=3 and max(moved,no_effect,changed,hits,kills)/n>=.75 else 'unresolved',
          'note':'似た観測条件の作用の記述統計。通行可否・操作意味・因果の確定やゲームクリア確率ではない。'}
    def record(self,row,before,after,neutral=None,after_native=None):
        if (not row.get('executed') or row.get('dry_run') or row.get('turn_kind')=='aborted' or row.get('action_kind')!='input' or row.get('kind') in ('compatibility','separation') or
            (row.get('input_receipt') or {}).get('os_submission')!='accepted' or str(row.get('event','')).startswith('diagnostic')):return {'status':'excluded'}
        keys=list(row.get('observed_keys',[]));context,samples=self.samples(before,keys);role=self.role(keys,before)
        b=before.get('minimap',{});a=after.get('minimap',{});bm=before.get('metrics',{});am=after.get('metrics',{})
        natural=neutral or {};same_mode=before.get('observation_mode','normal')==after.get('observation_mode','normal')
        spatial=bool(before.get('eligible') and after.get('eligible') and b.get('status')=='observed' and a.get('status')=='observed' and not b.get('retained') and not a.get('retained') and same_mode and b.get('player_cell') is not None and a.get('player_cell') is not None and bm.get('floor',{}).get('values')==am.get('floor',{}).get('values') and not natural.get('position') and not natural.get('floor'))
        delta=[a['player_cell'][i]-b['player_cell'][i] for i in (0,1)] if spatial else None
        text_pair=bool(before.get('text_reliable') and after.get('text_reliable') and same_mode and before.get('text_key') and after.get('text_key'))
        text0=before.get('text_key');text1=after.get('text_key')
        if after_native is not None:
            native=row.get('ocr') or {}
            def usable(value,source):
                return has_text_content(value) and all(isinstance(l.get('confidence'),(int,float)) and l['confidence']>=.8 for l in value.get('lines',[]) if compact_text(l.get('text',''))) and (not value.get('frame_sha256') or not source or value['frame_sha256']==source)
            text_pair=usable(native,before.get('metrics_source_frame_sha256') or before.get('frame_sha256')) and usable(after_native,after.get('metrics_source_frame_sha256') or after.get('frame_sha256'))
            def key(value):return hashlib.sha256(compact_text(''.join(l.get('text','') for l in value.get('lines',[])))[:2000].encode()).hexdigest()[:16]
            text0=key(native);text1=key(after_native)
            row['operational_text_pair']={'before':text0,'after':text1,'usable':bool(text_pair),'source':'normal_capture_OCR_not_map_display_text'}
        if natural.get('text_key') and natural['text_key']!=text0:text_pair=False
        # Stable HUD text cannot establish that a movement/attack did nothing.
        if role in ('movement_candidate','orientation_candidate','attack_or_interaction_candidate','arrow_candidate','step_candidate','dash_candidate'):text_pair=False
        text_change=text0!=text1 if text_pair else None
        hp0=bm.get('hp',{}).get('values');hp1=am.get('hp',{}).get('values')
        hp_change=hp1[0]-hp0[0] if hp0 and hp1 and hp0[1]==hp1[1] else None
        food0=bm.get('fullness',{}).get('values');food1=am.get('fullness',{}).get('values')
        food_change=food1[0]-food0[0] if food0 and food1 else None
        messages=[e for e in row.get('battle_observations',[]) if e.get('new_message')]
        hit=any(e['kind']=='damage_dealt' for e in messages);kill=any(e['kind']=='defeated_enemy' for e in messages)
        comparable=spatial or text_pair or hit or kill
        moved=delta!=[0,0] if delta is not None else None
        no_effect=delta==[0,0] and not hit and not kill and hp_change in (None,0) if spatial else text_change is False if text_pair else None
        value=.18*bool(moved)+.15*hit+.6*kill-.04*bool(no_effect)-2*max(0,-(hp_change or 0))/max(1,hp0[1] if hp0 else 1)
        def net_gain(key,change):
            observation=(natural.get('metric_changes') or {}).get(key,{})
            old=observation.get('before');new=observation.get('after')
            baseline=max(0,new[0]-old[0]) if isinstance(old,list) and isinstance(new,list) and old and new else 0
            return max(0,(change or 0)-baseline)
        effects={'hp_gain_net':net_gain('hp',hp_change),'food_gain_net':net_gain('fullness',food_change),'player_delta':delta,'moved':moved,'text_changed':text_change,'hp_change':hp_change,'food_change':food_change,
          'new_hit_message':hit,'new_defeat_message':kill,'no_effect':no_effect,'comparable':bool(comparable),'value':value}
        if role=='orientation_candidate':effects.update(no_effect=None,value=0.)
        sample={**effects,'before_frame':before.get('frame_sha256'),'after_frame':after.get('frame_sha256')}
        samples[:]=(samples+[sample])[-32:]
        with self.db:
            self.db.execute('INSERT INTO action_effects_v1 VALUES(?,?,?,?,?) ON CONFLICT(profile,context,action) DO UPDATE SET data=excluded.data,updated=excluded.updated',
              (self.profile,context,json.dumps(keys),json.dumps(samples),time.time()))
            self.db.execute('DELETE FROM action_effects_v1 WHERE profile=? AND rowid NOT IN (SELECT rowid FROM action_effects_v1 WHERE profile=? ORDER BY updated DESC LIMIT 1024)',(self.profile,self.profile))
        return {'status':'recorded','context':context,'role':role,'effects':effects,'attribution':'conditioned_association_not_causal_proof'}
    def experiment(self,plan,state):
        if plan.get('test','none')=='none':return None
        # Purpose is a user-visible question; changing its wording cannot reset
        # the trial budget for the same test and measured context.
        context=hashlib.sha256(json.dumps([self.context_key(state,[]),state.get('minimap',{}).get('player_cell'),state.get('metrics',{}).get('floor',{}).get('values'),state.get('exterior_action_policy',{}).get('scene_id')],sort_keys=True).encode()).hexdigest()[:24];test=plan['test'];key=(context,test)
        if key not in self.experiments:
            row=self.db.execute('SELECT data FROM operational_experiments_v1 WHERE profile=? AND context=? AND test=?',(self.profile,*key)).fetchone()
            self.experiments[key]=json.loads(row[0]) if row else {'attempts':0,'matches':0,'contradictions':0,'inconclusive':0}
        return {'context':context,'test':test,'question':plan.get('hypothesis',''),'expected_signal':plan['expected_signal'],'limit':min(4,plan['test_attempts']),**self.experiments[key]}
    def record_experiment(self,experiment,evaluation,row):
        if not experiment or not row.get('executed') or row.get('dry_run') or row.get('turn_kind')=='aborted' or (row.get('input_receipt') or {}).get('os_submission')!='accepted':return None
        key=(experiment['context'],experiment['test']);data=self.experiments[key]
        data['attempts']+=1
        comparable=evaluation.get('comparable',False);matched=evaluation.get('expected_signal_observed',False)
        data['matches' if matched else 'contradictions' if comparable else 'inconclusive']+=1
        while len(self.experiments)>256:self.experiments.pop(next(iter(self.experiments)))
        with self.db:
            self.db.execute('INSERT INTO operational_experiments_v1 VALUES(?,?,?,?,?) ON CONFLICT(profile,context,test) DO UPDATE SET data=excluded.data,updated=excluded.updated',
              (self.profile,*key,json.dumps(data),time.time()))
            self.db.execute('DELETE FROM operational_experiments_v1 WHERE profile=? AND rowid NOT IN (SELECT rowid FROM operational_experiments_v1 WHERE profile=? ORDER BY updated DESC LIMIT 256)',(self.profile,self.profile))
        return {**experiment,**data,'status':'supported_association' if data['matches']>=2 else 'contradicted' if data['contradictions']>=2 else 'inconclusive','exhausted':data['attempts']>=experiment['limit']}
