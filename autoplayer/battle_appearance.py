"""Appearance/message associations, never visual species declarations."""
import copy,json,time
from collections import Counter,defaultdict

class BattleAppearanceMemory:
    def __init__(self,db=None,profile='session'):
        self.db=db;self.profile=profile;self.recent=[]
        if db:
            db.execute('''CREATE TABLE IF NOT EXISTS battle_appearance_links(
                profile TEXT,event_key TEXT,created REAL,data TEXT,PRIMARY KEY(profile,event_key))''');db.commit()
    def record(self,observation,row,before,after,bank):
        if not row.get('executed') or row.get('dry_run') or row.get('turn_kind')=='aborted' or (row.get('input_receipt') or {}).get('os_submission')!='accepted':return []
        if str(row.get('event','')).startswith('diagnostic') or row.get('kind') in ('compatibility','separation'):return []
        objects=[o for o in observation.get('objects',[]) if o.get('legend')=='enemy_indicator']
        frame_current=not row.get('input_source_frame_sha256') or row['input_source_frame_sha256']==observation.get('frame_sha256')
        if not frame_current:objects=[]
        trial=row.get('combat_experiment') or {};links=[]
        for event in row.get('battle_observations',[]):
            if not event.get('new_message') or not event.get('source_frame_sha256') or not event.get('enemy'):continue
            candidates=objects
            # A requested target narrows outgoing evidence only when the actual
            # attack trial was dispatched. Incoming damage may be another enemy.
            if event.get('kind') in ('damage_dealt','miss','defeated_enemy') and trial.get('stage')=='attack_trial' and row.get('observed_keys')==trial.get('keys'):
                candidates=[o for o in objects if o.get('offset')==trial.get('target')]
            visual=[]
            for obj in candidates[:16]:
                appearance=obj.get('appearance_memory') or {};label=appearance.get('class_id')
                samples=[(pose,vector) for cls,pose,vector in bank.samples if cls==label]
                visual.append({'object_id':obj['id'],'offset':copy.deepcopy(obj.get('offset')),
                    'appearance':copy.deepcopy(appearance),'screen_association':copy.deepcopy(obj.get('screen_association')),
                    'descriptor':copy.deepcopy(samples[-1][1]) if samples else None,
                    'pose':samples[-1][0] if samples else 'unknown','variant_count':obj.get('appearance_variant_count',0)})
            single=len(visual)==1 and visual[0]['descriptor'] is not None
            association={'status':'single_visual_candidate_ocr_name_unverified' if single else 'ambiguous_visual_candidates' if len(visual)>1 else 'visual_evidence_unavailable',
                'step':row.get('step'),'session':(row.get('combat_message_ocr') or {}).get('session'),
                'observed_keys':copy.deepcopy(row.get('observed_keys',[])),
                'before_frame_sha256':observation.get('frame_sha256'),'visual_frame_current':frame_current,
                'message_frame_sha256':event['source_frame_sha256'],'message':copy.deepcopy(event),'candidates':visual,
                'status_before':copy.deepcopy(before.get('metrics',{})),'status_after':copy.deepcopy(after.get('metrics',{})),
                'identity_verified':False,'rule':'外見/位置とOCRの対応候補。類似だけで同種と確定せず、アニメーション/向き/背景の違いと複数敵を考慮する。'}
            key=json.dumps([association['session'],row.get('step'),event['source_frame_sha256'],event['kind'],event['quote']],ensure_ascii=False)
            if self.db:
                with self.db:
                    saved=self.db.execute('INSERT OR IGNORE INTO battle_appearance_links VALUES(?,?,?,?)',(self.profile,key,time.time(),json.dumps(association,ensure_ascii=False))).rowcount
                    self.db.execute('DELETE FROM battle_appearance_links WHERE profile=? AND rowid NOT IN (SELECT rowid FROM battle_appearance_links WHERE profile=? ORDER BY created DESC LIMIT 2048)',(self.profile,self.profile))
                if not saved:continue
            self.recent.append(association);self.recent=self.recent[-64:];links.append(association)
        return copy.deepcopy(links)
    def context(self,objects):
        data=[json.loads(r[0]) for r in self.db.execute('SELECT data FROM battle_appearance_links WHERE profile=? ORDER BY created DESC LIMIT 256',(self.profile,))] if self.db else self.recent
        current=[]
        for obj in objects[:16]:
            if obj.get('legend')!='enemy_indicator':continue
            appearance=obj.get('appearance_memory') or {};label=appearance.get('class_id');similar=appearance.get('similar_class_candidate')
            matches=[]
            for row in data:
                if row['status']!='single_visual_candidate_ocr_name_unverified':continue
                candidate=row['candidates'][0]['appearance'].get('class_id')
                if candidate and candidate in (label,similar):matches.append(row)
            names=Counter(r['message']['enemy'] for r in matches);groups=defaultdict(list)
            for r in matches:
                message=r['message'];metrics=r['status_before']
                relevant=('level','strength','sword') if message['kind']=='damage_dealt' else ('level','shield')
                conditions={k:{'values':copy.deepcopy(metrics[k].get('values')),'retained':bool(metrics[k].get('retained'))} for k in relevant if isinstance(metrics.get(k),dict)}
                group=json.dumps([message['enemy'],message['kind'],conditions],ensure_ascii=False,sort_keys=True)
                groups[group].append(message.get('damage'))
            summaries=[]
            for key,values in list(groups.items())[:12]:
                name,kind,conditions=json.loads(key);numbers=[v for v in values if type(v) is int]
                summaries.append({'ocr_name_candidate':name,'event':kind,'conditions':conditions,'samples':len(values),
                    'mean_damage':sum(numbers)/len(numbers) if numbers else None,'min_damage':min(numbers) if numbers else None,'max_damage':max(numbers) if numbers else None})
            current.append({'object_id':obj['id'],'appearance_class':label,'similar_class_candidate':similar,
                'name_candidates':dict(names),'conditional_battle_experience':summaries,'identity_verified':False})
        return {'status':'empirical_visual_message_associations','current_candidates':current,
            'retained_recent_links':len(data),'note':'OCR名の候補と条件別の経験。画像だけで名前を確定しない。単一候補でも位置と切出しは仮説。外見の類似/未見の向きは同種の証明ではなく、未知の敵HP/命中率を作らない。'}
