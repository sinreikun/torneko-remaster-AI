"""Prospective prediction checks and revisable conditional rules, not LLM training."""
from collections import Counter,deque
import copy,hashlib,json,statistics,time

REVISION=1
FIELDS=('player_delta','hp_change','food_change','damage_dealt','damage_received','item_hp','item_max_hp','item_fullness','item_strength','item_sword','item_shield')

def same_floor_values(before,after):return before.get('metrics',{}).get('floor',{}).get('values')==after.get('metrics',{}).get('floor',{}).get('values')

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
def maturity(field):
    checks=field.get('checks',[]);recent=checks[-8:]
    if len(recent)>=2 and sum(not x for x in recent[-4:])>=2:return 'suspended'
    if recent and not recent[-1]:return 'under_review'
    if len(checks)>=5 and sum(recent)/len(recent)>=.8:return 'supported'
    return 'candidate' if len(field.get('values',[]))>=3 else 'insufficient'

class ExperienceCycle:
    def __init__(self,effects):
        self.effects=effects;self.db=effects.db;self.profile=effects.profile;self.cache={};self.last_timing=None;self.questions=deque(maxlen=24)
        self.planned_failures=0;self.recent=deque(maxlen=32)
        self.db.executescript('''CREATE TABLE IF NOT EXISTS experience_rules_v1(profile TEXT,rule_key TEXT,data TEXT,updated REAL,PRIMARY KEY(profile,rule_key));
            CREATE TABLE IF NOT EXISTS experience_checks_v1(id INTEGER PRIMARY KEY,profile TEXT,created REAL,data TEXT);''');self.db.commit()
        self.recent.extend(json.loads(r[0]) for r in reversed(self.db.execute('SELECT data FROM experience_checks_v1 WHERE profile=? ORDER BY id DESC LIMIT 32',(self.profile,)).fetchall()))
        for row in self.recent:
            if row.get('resolved_local_key'):self.questions=deque((q for q in self.questions if q.get('local_key')!=row['resolved_local_key']),maxlen=24)
            if row.get('question'):self.questions.append(row['question'])
    def prepare(self,state,world=None,inventory=None,source_frame=None):
        world=world or {};source_frame=source_frame or state.get('metrics_source_frame_sha256') or state.get('frame_sha256')
        current=source_frame is not None and world.get('frame_sha256')==source_frame
        classes=[]
        if current:
            for obj in world.get('objects',[]):
                if obj.get('legend')=='enemy_indicator':
                    a=obj.get('appearance_memory') or {};classes.append(a.get('similar_class_candidate') or a.get('class_id') or 'unknown')
        selected=next((r for r in getattr(inventory,'rows',[]) if r.get('id')==getattr(inventory,'selected',None)),None)
        item=['ocr_identified_name',selected['true_name']] if selected and selected.get('true_name') else ['instance',selected['id']] if selected else None
        state['_experience_scope']={'visual_candidates':sorted(set(classes)),'visual_identity_verified':False,'selected_item':item,
            'local_run':getattr(inventory,'scope',None),'episode':world.get('episode'),'timing':copy.deepcopy(self.last_timing)}
    def keys(self,state,keys):
        extra=state.get('_experience_scope',{});role=self.effects.role(keys,state)
        scope={'timing':extra.get('timing'),'revision':REVISION,'effect_context':self.effects.context_key(state,keys),'keys':keys,
            'visual_candidates':extra.get('visual_candidates',[]) if state.get('minimap',{}).get('enemies') else [],
            'selected_item':extra.get('selected_item') if role in ('confirmation_candidate','menu_candidate') else None}
        if scope['selected_item']:
            hp=state.get('metrics',{}).get('hp',{});food=state.get('metrics',{}).get('fullness',{})
            values=hp.get('values') if not hp.get('retained') else None;fv=food.get('values') if not food.get('retained') else None
            scope['item_status']={'hp_full':values[0]==values[1] if values and len(values)>1 else None,'missing_hp_band':(values[1]-values[0])//5 if values and len(values)>1 else None,'food_band':fv[0]//20 if fv else None}
        base=digest(scope)
        local=digest([base,extra.get('local_run'),extra.get('episode'),state.get('metrics',{}).get('floor',{}).get('values'),
            state.get('minimap',{}).get('player_cell'),state.get('text_key') if not state.get('eligible') else None])
        return base,local
    def load(self,key):
        if key not in self.cache:
            row=self.db.execute('SELECT data FROM experience_rules_v1 WHERE profile=? AND rule_key=?',(self.profile,key)).fetchone()
            self.cache[key]=json.loads(row[0]) if row else {'revision':0,'samples':0,'fields':{}}
            while len(self.cache)>128:self.cache.pop(next(iter(self.cache)))
        return self.cache[key]
    @staticmethod
    def expected(field):
        values=field.get('values',[])
        if len(values)<3 or maturity(field)=='suspended':return None
        if isinstance(values[-1],list):
            value,count=Counter(tuple(v) for v in values).most_common(1)[0]
            return {'kind':'exact','value':list(value),'agreement':count/len(values)} if count/len(values)>=.75 else None
        # Report width and point error too: a wide range is not a useful success.
        ordered=sorted(values);trim=1 if len(ordered)>=10 else 0
        return {'kind':'range','min':ordered[trim],'max':ordered[-1-trim], 'point':statistics.median(values),'width':ordered[-1-trim]-ordered[trim]}
    def predict(self,state,keys,step=0,timing=None):
        if timing is not None:
            self.last_timing={'hold_bucket_ms':round(timing['hold_ms']/20)*20,'wait_bucket_ms':int(timing['wait_ms']//200)*200}
            state.setdefault('_experience_scope',{})['timing']=copy.deepcopy(self.last_timing)
        base,local=self.keys(state,list(keys));rules=[(local,self.load(local)),(base,self.load(base))];expected={};audits={}
        for key,rule in rules:
            audits[key]={field:{**value,'rule_key':key,'rule_revision':rule['revision'],'status':maturity(evidence),'samples':len(evidence['values'])} for field,evidence in rule['fields'].items() if (value:=self.expected(evidence)) is not None}
        for field in FIELDS:
            for key,rule in rules:
                evidence=rule['fields'].get(field,{})
                value=self.expected(evidence)
                if value is not None:
                    expected[field]={**value,'rule_key':key,'rule_revision':rule['revision'],'status':maturity(evidence),'samples':len(evidence['values'])};break
                if maturity(evidence)=='suspended':break
        hint=state.get('map_route_hint') or {};planned=None
        from .floor_navigation import ROUTE_MOVES
        token=next((k for k in ROUTE_MOVES if set(k.split('+'))==set(keys) and len(k.split('+'))==len(keys)),None)
        if hint and hint.get('mode')!='frontier_probe' and token and (hint.get('keys') or [hint.get('key')])==list(keys):planned=list(ROUTE_MOVES[token])
        return {'schema_revision':REVISION,'keys':list(keys),'step':step,'before_frame':state.get('metrics_source_frame_sha256') or state.get('frame_sha256'),
            'base_key':base,'local_key':local,'expected':expected,'rule_predictions':audits,'planned_delta':planned,'scope':copy.deepcopy(state.get('_experience_scope',{})),
            'observed_conditions':self.effects.context_features(state,keys),'status':'empirical_prediction' if expected else 'unestimated','rule':'入力前に固定した予測。計画上の移動と実測の規則を区別し、成功や因果を保証しない。'}
    def adjustment(self,state,keys):
        prediction=self.predict(state,keys);trusted={k:v for k,v in prediction['expected'].items() if v['status']=='supported'}
        delta=trusted.get('player_delta',{}).get('value');hp=trusted.get('hp_change',{}).get('point')
        return max(-.2,min(.1,(-.15 if delta==[0,0] else 0)+(-.1 if hp is not None and hp<0 else 0))), {'rule_status':'supported_only','expected':trusted}
    def observed(self,row,before,after,natural):
        effects=(row.get('action_effect_learning') or {}).get('effects',{});values={};omitted={}
        fresh_floor=bool(before.get('metrics',{}).get('floor',{}).get('values') and same_floor_values(before,after) and not any(s.get('metrics',{}).get('floor',{}).get('retained') or s.get('floor_confirmation',{}).get('status') in ('pending','unreadable') for s in (before,after)))
        if fresh_floor and effects.get('player_delta') is not None:values['player_delta']=effects['player_delta']
        else:omitted['player_delta']='位置/階/表示モード/自然変化を比較できない'
        same_mode=before.get('observation_mode','normal')==after.get('observation_mode','normal')
        same_floor=before.get('metrics',{}).get('floor',{}).get('values')==after.get('metrics',{}).get('floor',{}).get('values')
        for field,metric in (('hp_change','hp'),('food_change','fullness')):
            value=effects.get(field)
            if value is not None and same_mode and same_floor and fresh_floor and not any(s.get('metrics',{}).get(metric,{}).get('retained') for s in (before,after)) and not natural.get('floor') and not (natural.get('metric_changes') or {}).get(metric):values[field]=value
            else:omitted[field]='現在値欠測・上限/階変化または自然変化を分離できない'
        events=[e for e in row.get('battle_observations',[]) if e.get('new_message') and e.get('source_frame_sha256')]
        scoped=before.get('_experience_scope',{}).get('visual_candidates')
        for kind in ('damage_dealt','damage_received'):
            candidates=[e.get('damage') for e in events if e.get('kind')==kind]
            if scoped and len(candidates)==1 and type(candidates[0]) is int:values[kind]=candidates[0]
            else:omitted[kind]='対象の外見条件か単一の新しい数値メッセージが未確認'
        item=(row.get('world_learning') or {}).get('item_effect') or {}
        if before.get('_experience_scope',{}).get('selected_item') and item and not item.get('confounds'):
            for metric in ('hp','fullness','strength','sword','shield'):
                change=item.get('metric_changes',{}).get(metric,{})
                a=change.get('before');b=change.get('after')
                if metric=='hp' and a and b and len(a)>1 and len(b)>1:values['item_max_hp']=b[1]-a[1]
                if a and b and (metric not in ('hp','strength') or len(a)>1 and len(b)>1 and a[1]==b[1]):values['item_'+metric]=b[0]-a[0]
        return values,omitted
    @staticmethod
    def compare(prediction,actual):
        if prediction['kind']=='exact':return prediction['value']==actual,sum(abs(a-b) for a,b in zip(prediction['value'],actual))
        return prediction['min']<=actual<=prediction['max'],abs(actual-prediction['point'])
    def record(self,row,before,after,natural=None):
        forecast=row.get('action_prediction') or {};natural=natural or {}
        valid=row.get('executed') and not row.get('dry_run') and row.get('turn_kind')!='aborted' and row.get('action_kind')=='input' and (row.get('input_receipt') or {}).get('os_submission')=='accepted'
        if not valid or str(row.get('event','')).startswith('diagnostic') or row.get('kind') in ('compatibility','separation') or forecast.get('keys')!=row.get('observed_keys') or forecast.get('before_frame') and row.get('input_source_frame_sha256') and forecast['before_frame']!=row['input_source_frame_sha256']:
            return {'status':'excluded','reason':'未受理/診断/中断/入力変更を規則の検証にしない','needs_review':False}
        values,omitted=self.observed(row,before,after,natural);checks=[];updates=[]
        for predictions in forecast.get('rule_predictions',{}).values():
          for name,prediction in predictions.items():
            if name in values:
                matched,error=self.compare(prediction,values[name]);checks.append({'field':name,'matched':matched,'error':error,'predicted':prediction,'observed':values[name]})
        planned=forecast.get('planned_delta');planned_match=planned==values.get('player_delta') if planned is not None and 'player_delta' in values else None
        if planned_match is not None:self.planned_failures=0 if planned_match else self.planned_failures+1
        important=any(not c['matched'] and c['predicted']['status']=='supported' for c in checks) or planned_match is False and self.planned_failures==2
        for key in dict.fromkeys([forecast.get('base_key'),forecast.get('local_key')]):
            if not key:continue
            rule=self.load(key);rule['observed_conditions']=copy.deepcopy(forecast.get('observed_conditions',{}));rule['scope_kind']='local' if key==forecast.get('local_key') else 'general'
            for field,value in values.items():
                evidence=rule['fields'].setdefault(field,{'values':[],'checks':[],'observations':0,'counterexamples':0});old=maturity(evidence)
                check=next((c for c in checks if c['field']==field and c['predicted']['rule_key']==key),None)
                if check:
                    evidence['checks']=(evidence['checks']+[check['matched']])[-32:]
                    if not check['matched']:evidence['counterexamples']=evidence.get('counterexamples',0)+1;evidence['last_failure']=evidence.get('observations',0)+1
                evidence['values']=(evidence['values']+[copy.deepcopy(value)])[-32:];evidence['observations']=evidence.get('observations',0)+1
                stable=evidence['values'][-6:]
                if old=='suspended' and len(stable)==6 and evidence['observations']-evidence.get('last_failure',evidence['observations'])>=6 and ((Counter(tuple(v) for v in stable).most_common(1)[0][1]>=5) if isinstance(stable[-1],list) else max(stable)-min(stable)<=max(2,abs(statistics.median(stable))*.5)):
                    evidence['previous_validation']={'checks':len(evidence['checks']),'matched':sum(evidence['checks']),'values':copy.deepcopy(evidence['values'])};evidence['checks']=[];evidence['values']=copy.deepcopy(stable)
                new=maturity(evidence)
                if new!=old:
                    rule['revision']+=1;updates.append({'field':field,'from':old,'to':new,'scope':'local' if key==forecast['local_key'] else 'general'})
            if values:
                rule['samples']+=1
                self.db.execute('INSERT INTO experience_rules_v1 VALUES(?,?,?,?) ON CONFLICT(profile,rule_key) DO UPDATE SET data=excluded.data,updated=excluded.updated',(self.profile,key,json.dumps(rule,ensure_ascii=False),time.time()))
        mismatches=[c for c in checks if not c['matched']]
        if mismatches or planned_match is False:
            question={'before_frame':forecast.get('before_frame'),'after_frame':after.get('metrics_source_frame_sha256') or after.get('frame_sha256'),'observed_conditions':forecast.get('observed_conditions'), 'base_key':forecast.get('base_key'),'local_key':forecast.get('local_key'),'step':row.get('step'),'keys':row.get('observed_keys'),'prediction_errors':mismatches[:4],'planned_delta':planned,'observed_delta':values.get('player_delta'),
                'question':'入力成立・観測/対象の誤認・条件の違い・確率変動・規則の誤りを切り分ける。必要な短い検証を選ぶ。','status':'unresolved_association_not_causality'}
            self.questions.append(question)
        verified={c['field'] for c in checks if c['matched'] and c['predicted']['rule_key']==forecast.get('local_key') and maturity(self.load(c['predicted']['rule_key'])['fields'][c['field']])=='supported'}
        def resolved_question(q):
            fields={c['field'] for c in q.get('prediction_errors',[])}
            return q.get('local_key')==forecast.get('local_key') and not mismatches and (bool(fields) and fields<=verified or not fields and q.get('planned_delta') is not None and planned_match is True)
        remaining=[q for q in self.questions if not resolved_question(q)];resolved=len(remaining)<len(self.questions)
        self.questions=deque(remaining,maxlen=24)
        wanted=set(forecast.get('expected',{}))|({'player_delta'} if planned is not None else set())
        changed_conditions=[k for k in ('level','strength','sword','shield') if before.get('metrics',{}).get(k,{}).get('values') is not None and after.get('metrics',{}).get(k,{}).get('values') is not None and before['metrics'][k]['values']!=after['metrics'][k]['values']]
        cause_evidence={'cause_confirmed':False,'os_submission':'accepted','game_response_confirmed':False,
            'missing_expected_fields':sorted(wanted&set(omitted)),'observed_condition_changes':changed_conditions,
            'natural_change_recorded':bool(natural.get('position') or natural.get('floor') or natural.get('metric_changes')),
            'possible_causes_if_mismatch':['入力がゲームに反映されていない','認識/対象の誤り','未観測の条件差','確率変動','経験則の誤り'] if mismatches or planned_match is False else []}
        result={'cause_evidence':cause_evidence,'status':'mismatch' if mismatches or planned_match is False else 'matched' if checks or planned_match is True else 'learning_candidate' if values else 'unverifiable',
            'checks':checks,'planned_route_matched':planned_match,'observed':values,'censored':omitted,'rule_updates':updates,'needs_review':important,
            'note':'欠測は失敗でない。実測関連の予測を検証しており、ゲームの規則や攻略成功の証明ではない。'}
        summary={'schema_revision':REVISION,'local_run':forecast.get('scope',{}).get('local_run'),'before_frame':forecast.get('before_frame'),'after_frame':after.get('metrics_source_frame_sha256') or after.get('frame_sha256'),'keys':row.get('observed_keys'),'step':row.get('step'),'status':result['status'],'checks':len(checks),'matches':sum(c['matched'] for c in checks),
            'field_errors':[{'field':c['field'],'rule_key':c['predicted']['rule_key'],'matched':c['matched'],'error':c['error']} for c in checks],
            'question':self.questions[-1] if (mismatches or planned_match is False) and self.questions else None,'resolved_local_key':forecast.get('local_key') if resolved else None,
            'explicit_defeats':sum(e.get('kind')=='defeated_enemy' and bool(e.get('new_message')) for e in row.get('battle_observations',[])),
            'explicit_hits':sum(e.get('kind')=='damage_dealt' and bool(e.get('new_message')) for e in row.get('battle_observations',[])),
            'explicit_misses':sum(e.get('kind')=='miss' and bool(e.get('new_message')) for e in row.get('battle_observations',[])),
            'floor_change':bool(before.get('metrics',{}).get('floor',{}).get('values') and after.get('metrics',{}).get('floor',{}).get('values') and not same_floor_values(before,after)),'new_location':bool((row.get('operation_evaluation') or {}).get('new_location')),
            'hp_loss':max(0,-values.get('hp_change',0)),'item_effect':bool((row.get('world_learning') or {}).get('item_effect'))}
        self.recent.append(summary)
        with self.db:
            self.db.execute('INSERT INTO experience_checks_v1(profile,created,data) VALUES(?,?,?)',(self.profile,time.time(),json.dumps(summary,ensure_ascii=False)))
            self.db.execute('DELETE FROM experience_checks_v1 WHERE profile=? AND id NOT IN (SELECT id FROM experience_checks_v1 WHERE profile=? ORDER BY id DESC LIMIT 2048)',(self.profile,self.profile))
            self.db.execute('DELETE FROM experience_rules_v1 WHERE profile=? AND rowid NOT IN (SELECT rowid FROM experience_rules_v1 WHERE profile=? ORDER BY updated DESC LIMIT 2048)',(self.profile,self.profile))
        result['label']={'matched':'予測と観測が一致','mismatch':'予測と観測の不一致を確認','learning_candidate':'予測は未推定・経験則の候補を収集中','unverifiable':'比較できる観測が不足'}[result['status']]
        result['metrics']=self.metrics()
        return result
    def metrics(self):
        recent=list(self.recent);checks=sum(r['checks'] for r in recent);errors={}
        for row in recent:
            for e in row.get('field_errors',[]):errors.setdefault(e['field'],[]).append(e['error'])
        hits=sum(r.get('explicit_hits',0) for r in recent);misses=sum(r.get('explicit_misses',0) for r in recent)
        return {'window_inputs':len(recent),'prospective_checks':checks,
            'prediction_match_rate':sum(r['matches'] for r in recent)/checks if checks else None,
            'mean_error_by_field':{k:sum(v)/len(v) for k,v in errors.items()},
            'checks_by_field':{k:{'checks':len(v),'match_rate':sum(e['matched'] for r in recent for e in r.get('field_errors',[]) if e['field']==k)/len(v)} for k,v in errors.items()},
            'explicit_hits':hits,'explicit_misses':misses,'observed_hit_rate':hits/(hits+misses) if hits+misses else None,
            'explicit_defeats':sum(r.get('explicit_defeats',0) for r in recent),'observed_floor_changes':sum(r.get('floor_change',False) for r in recent),
            'observed_new_locations':sum(r['new_location'] for r in recent),'observed_hp_loss':sum(r['hp_loss'] for r in recent),
            'recorded_item_use_outcomes':sum(r['item_effect'] for r in recent),'causal_improvement_verified':False}
    def context(self,state):
        rules=[]
        for keys in ([k] for k in self.effects.keys('move')+self.effects.keys('attack')+self.effects.keys('arrow')):
            p=self.predict(state,keys)
            if p['expected']:rules.append({'keys':keys,'expected':p['expected']})
        return {'schema_revision':REVISION,'evidence_layers':{'current':'今回の画像/OCR/地図','retained':'最後に確認した値。現在の証明ではない','hypotheses':'外見候補・LLMの解釈','rules':'条件付き実測。入力前の予測を後の観測で検証'},
            'rules':rules[:8],'open_questions':[q for q in self.questions if q.get('local_key')==self.keys(state,q.get('keys',[]))[1]][-4:],
            'metrics':self.metrics(),
            'instruction':'damage_dealtは明示された命中時のダメージで、外れや欠測を0として混ぜない。observed_hit_rateは直近の読めた文章の集計で現在敵の確率や勝率ではない。情報不足は未推定。反例が出た規則は見直し、状況に必要な疑問だけ検証する。予測一致率や保存件数をクリア率にしない。'}
