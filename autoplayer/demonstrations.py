"""Explicit human examples: target-only sampled keys, asynchronous evidence, retrieval.

No input injection or macro playback. Human demonstrations remain fallible data.
"""
import copy
import hashlib
import io
import json
import queue
import re
import threading
import time
from .interfaces import compact_text,torneko_menu_kind,result_screen
from .controls import is_torneko,ACTIONS


def screen_kind(labels,metrics):
    """Numeric readings and scene identity are separate evidence."""
    text='\n'.join(labels)
    if re.search(r'^(?:[▶►▷>→]?)(?:はい|いいえ|Yes|No)$',text,re.M|re.I):return 'choice'
    if metrics.get('hp') and metrics.get('floor'):return 'gameplay'
    hud=(bool(re.search(r'^\d{1,3}階$',text,re.M)),bool(re.search(r'^Lv',text,re.M|re.I)),
         bool(re.search(r'満腹|腹度',text)),bool(re.search(r'^\d[\d,]*G$',text,re.M|re.I)))
    # Require independent HUD labels, never repair "HP1S" into an HP value.
    return 'gameplay' if sum(hud)>=3 else 'unknown'


def similarity(x,y):
    return max(0,1-sum(abs(i-j) for i,j in zip(x,y))/(255*len(x))) if x and len(x)==len(y) else 0.


def observed_effect(before,after):
    hp0,hp1=before.get('hp'),after.get('hp')
    food0,food1=before.get('fullness'),after.get('fullness')
    movement=None;p0=before.get('map_pose');p1=after.get('map_pose')
    if p0 and p1 and before.get('floor') and before.get('floor')==after.get('floor') and all(p0.get(k)==p1.get(k) for k in ('source_size','analysis_size','cell_size','shape')):
        tile=p0['cell_size'];dx=(p1['player'][0]-p0['player'][0])/tile;dy=(p1['player'][1]-p0['player'][1])/tile
        if abs(dx)<=2 and abs(dy)<=2:movement={'dx_cells':round(dx,2),'dy_cells':round(dy,2),'source':'minimap_marker_displacement_not_world_coordinates'}
    return {'hp_delta':hp1[0]-hp0[0] if hp0 and hp1 and hp0[1]==hp1[1] else None,
        'fullness_delta':food1[0]-food0[0] if food0 and food1 else None,
        'screen_kind_before':before['kind'],'screen_kind_after':after['kind'],
        'scene_transition':before['kind']!=after['kind'],
        'visual_change':round(1-similarity(before.get('center'),after.get('center')),4),
        'contacts_before':before.get('contacts'),'contacts_after':after.get('contacts'),
        'movement':movement,'facing':'unverified','hit':'unverified',
        'attribution':'human_input_followed_by_observation_not_proven_cause'}


def features(png,native):
    from .knowledge import KnowledgeEngine
    from .map_sensor import read_grid
    if not native or native.get('frame_sha256')!=hashlib.sha256(png).hexdigest():native={'status':'frame_mismatch','lines':[]}
    metrics=KnowledgeEngine().reconcile(native)['metrics']
    labels=[compact_text(l.get('text',''))[:160] for l in (native or {}).get('lines',[])]
    tokens=set()
    for label in labels:
        if re.search(r'HP|Lv|満腹|腹度|\d',label,re.I):continue
        tokens.update(label[i:i+2] for i in range(max(0,len(label)-1)))
    kind=result_screen(native) or torneko_menu_kind(native) or screen_kind(labels,metrics)
    visual=[];center=[];hud=[]
    if png.startswith(b'\x89PNG'):
        from PIL import Image
        with Image.open(io.BytesIO(png)) as source:
            image=source.convert('L');visual=list(image.resize((16,12)).tobytes())
            center=list(image.crop((image.width*.25,image.height*.25,image.width*.75,image.height*.8)).resize((16,12)).tobytes())
            hud=list(image.crop((0,0,image.width,image.height*.15)).resize((32,4)).tobytes())
    m=read_grid(png)
    return {'kind':kind,'ocr_status':(native or {}).get('status'),'tokens':sorted(tokens)[:256],
        'quotes':labels[:12],'visual':visual,'center':center,'hud':hud,'kind_basis':'current_ocr_labels',
        'contacts':[list(e) for e in m.get('enemies',[])] if m.get('status')=='observed' else None,
        'hp':metrics.get('hp',{}).get('values'),'floor':metrics.get('floor',{}).get('values'),
        'fullness':metrics.get('fullness',{}).get('values'),
        'map_pose':{k:m[k] for k in ('player','cell_size','shape','source_size','analysis_size')} if m.get('status')=='observed' else None,
        'source_frame_sha256':hashlib.sha256(png).hexdigest()}


def event_steps(events):
    active=set();steps=[];previous=0
    for event in events:
        at=event['at_ms']
        if at>previous:
            interval={'keys':sorted(active),'duration_ms':at-previous}
            if steps and steps[-1]['keys']==interval['keys']:steps[-1]['duration_ms']+=interval['duration_ms']
            else:steps.append(interval)
        if event['type']=='down':active.add(event['key'])
        else:active.discard(event['key'])
        previous=at
    return steps


class HumanSegments:
    """Pure sampled-edge segmentation. Focus loss/stop never completes an example."""
    def __init__(self):self.active=None;self.suppressed=set();self.current=None;self.released=None;self.previous_frame=None

    @staticmethod
    def usable(frame,now):
        return bool(frame and frame['ended']<=now and now-frame['ended']<=.75 and frame['ended']-frame['started']<=.75)

    def finish(self,frame,now,reason=None):
        row=self.current;self.current=None;self.released=None
        if not row:return None
        row['after']=frame;row['complete']=not reason and row['before'] is not None and self.usable(frame,now)
        row['reason']=reason or ('' if row['complete'] else 'missing_fresh_frame')
        row['steps']=event_steps(row['events']);row['duration_ms']=round((now-row['started'])*1000)
        return row

    def feed(self,focused,keys,frame,now):
        ready=[];keys=set(keys)
        if not focused:
            if self.current:ready.append(self.finish(None,now,'focus_lost_or_paused'))
            self.active=None;self.suppressed=set();self.previous_frame=None;return ready
        if self.active is None:
            self.active=keys;self.suppressed=set(keys);self.previous_frame=frame if self.usable(frame,now) else None;return ready
        self.suppressed&=keys
        down=(keys-self.active)-self.suppressed;up=(self.active-keys)-self.suppressed
        if self.suppressed and not self.current:
            self.suppressed|=keys;self.active=keys;self.previous_frame=None;return ready
        if self.current and self.released is not None and now-self.released>=.35:
            after_ok=self.usable(frame,now) and frame['started']>=self.released+.2
            if after_ok:ready.append(self.finish(frame,now))
            elif down:ready.append(self.finish(None,now,'next_input_before_after_frame'))
            elif now-self.released>2:ready.append(self.finish(None,now,'after_frame_timeout'))
        if down and not self.current:
            self.current={'started':now,'before':self.previous_frame if self.usable(self.previous_frame,now) else None,'events':[]}
        if self.current:
            at=round((now-self.current['started'])*1000)
            for key in sorted(up):
                # A key held before focus acquisition has no recorded key-down.
                if any(e['type']=='down' and e['key']==key for e in self.current['events']):
                    self.current['events'].append({'type':'up','key':key,'at_ms':at})
            for key in sorted(down):self.current['events'].append({'type':'down','key':key,'at_ms':at})
            if down:self.released=None
            if not keys and self.released is None:self.released=now
            if at>=10000 or len(self.current['events'])>=128:
                ready.append(self.finish(None,now,'segment_limit'));self.suppressed=set(keys)
        self.active=keys;self.previous_frame=frame if self.usable(frame,now) else None
        return ready


class DemonstrationStore:
    def __init__(self,memory,cfg):
        self.db=memory.db;self.profile=memory.profile;self.cfg=cfg
        self.db.executescript('''CREATE TABLE IF NOT EXISTS human_examples(
            id INTEGER PRIMARY KEY,profile TEXT,created REAL,status TEXT,data TEXT);
            CREATE INDEX IF NOT EXISTS human_examples_profile ON human_examples(profile,id);
            CREATE TABLE IF NOT EXISTS human_reuse(id INTEGER PRIMARY KEY,profile TEXT,created REAL,data TEXT);
            CREATE TABLE IF NOT EXISTS human_example_scores(profile TEXT,example_id INTEGER,scene TEXT,
                trials INTEGER,reward REAL,PRIMARY KEY(profile,example_id,scene));''')
        self.last_search={}

    def add(self,row,status):
        with self.db:
            cursor=self.db.execute('INSERT INTO human_examples(profile,created,status,data) VALUES(?,?,?,?)',
                (self.profile,time.time(),status,json.dumps(row,ensure_ascii=False)))
        return cursor.lastrowid

    def complete(self,identifier,row):
        with self.db:self.db.execute('UPDATE human_examples SET status=?,data=? WHERE id=? AND profile=?',
            ('complete' if row.get('complete') else 'censored',json.dumps(row,ensure_ascii=False),identifier,self.profile))

    def retrieve(self,png,native):
        self.last_search={'evaluated':0,'rejected':{},'selected_ids':[]}
        if not self.db.execute('SELECT 1 FROM human_examples WHERE profile=? AND status=? LIMIT 1',(self.profile,'complete')).fetchone():return []
        current=features(png,native);ranked=[]
        if current['ocr_status']!='ok':return []
        def upgraded(f):
            f=copy.deepcopy(f)
            menu=torneko_menu_kind({'status':'ok','lines':[{'text':q} for q in f.get('quotes',[])]})
            if menu:f['kind']=menu
            elif f.get('kind')=='unknown':f['kind']=screen_kind(f.get('quotes',[]),{})
            return f
        current=upgraded(current)
        # Historical examples are retained; old unknown labels can be reclassified
        # using their original quotes without inventing numeric readings.
        anchors=[]
        for raw, in self.db.execute("SELECT data FROM human_examples WHERE profile=? AND status='complete' ORDER BY id DESC LIMIT 256",(self.profile,)):
            anchor=upgraded(json.loads(raw).get('before_features',{}))
            if anchor.get('kind')=='gameplay' and anchor.get('hud'):anchors.append(anchor)
            if len(anchors)>=8:break
        if current['kind']=='unknown' and any(similarity(current.get('hud'),a.get('hud'))>=.97 and similarity(current.get('center'),a.get('center'))>=.94 for a in anchors):
            current.update(kind='gameplay',kind_basis='same_profile_visual_reference')
        scores={r[0]:(r[1],r[2]) for r in self.db.execute('SELECT example_id,trials,reward FROM human_example_scores WHERE profile=? AND scene=?',(self.profile,current['kind']))}
        def reject(reason):self.last_search['rejected'][reason]=self.last_search['rejected'].get(reason,0)+1
        for identifier,raw in self.db.execute('SELECT id,data FROM human_examples WHERE profile=? AND status=? ORDER BY id DESC',(self.profile,'complete')):
            self.last_search['evaluated']+=1
            row=json.loads(raw);before=upgraded(row.get('before_features',{}))
            if before.get('ocr_status')!='ok' or before.get('kind')!=current['kind']:reject('scene_or_ocr');continue
            steps=copy.deepcopy(row.get('steps',[]))
            if not steps or any(k not in self.cfg.allowed_keys or (is_torneko(self.cfg.window_title) and k=='num7') for step in steps for k in step['keys']):reject('keys');continue
            a=set(before.get('tokens',[]));b=set(current['tokens']);text=len(a&b)/max(1,len(a|b))
            visual=similarity(before.get('visual'),current.get('visual'));center=similarity(before.get('center'),current.get('center'));adaptation=None
            old=before.get('contacts');new=current.get('contacts')
            # Transfer only a pure orient-then-attack example across rotations.
            pure=all(not s['keys'] or s['keys']==['num1'] or ('num5' in s['keys'] and len(s['keys'])==2 and any(k in 'wasd' for k in s['keys'])) for s in steps)
            if current['kind']=='gameplay' and pure and any('num5' in s['keys'] for s in steps) and old and new and len(old)==len(new)==1 and sum(abs(v) for v in old[0])==sum(abs(v) for v in new[0])==1:
                directions=[(0,-1),(1,0),(0,1),(-1,0)];keys=['w','d','s','a']
                shift=(directions.index(tuple(new[0]))-directions.index(tuple(old[0])))%4
                if shift:
                    remap={k:keys[(i+shift)%4] for i,k in enumerate(keys)}
                    for step in steps:step['keys']=[remap.get(k,k) for k in step['keys']]
                    adaptation='単一の隣接敵の相対方向に合わせ、向き変更を試す方向だけを回転。成功の保証ではない。'
                contact_match=True
            else:
                contact_match=bool(current['kind']=='gameplay' and old and new and old==new)
                if current['kind']=='gameplay' and old and new and old!=new:reject('contacts');continue
            if current['kind']=='gameplay' and not contact_match and (visual<.92 or center<.90):reject('visual');continue
            if current['kind']=='unknown' and (text<.65 or center<.92):reject('unknown_evidence');continue
            if current['kind'] not in ('gameplay','unknown') and text<.5:reject('text');continue
            score=.45*text+.25*visual+.3*center
            if contact_match:score=max(score,.85)
            trials,total=scores.get(identifier,(0,0.))
            adjustment=max(-.25,min(.05,total/max(1,trials)))*min(1,trials/3)
            bounded=[]
            for step in steps[:8]:
                if len(step['keys'])>4:break
                if is_torneko(self.cfg.window_title):step['keys'].sort(key=lambda k:(k not in ('num5','num3'),k))
                bounded.append({'keys':step['keys'],'hold_ms':min(self.cfg.max_hold_ms,max(1,step['duration_ms'])) if step['keys'] else 1,
                    'wait_ms':min(self.cfg.max_wait_ms,step['duration_ms']) if not step['keys'] else 0,
                    'recorded_duration_ms':step['duration_ms'],
                    'meanings':[self.cfg.key_bindings.get(k,ACTIONS.get(k,k)) for k in step['keys']]})
            if not bounded:continue
            ranked.append({'source':'human_example','id':identifier,'similarity':round(score,3),'priority':round(score+adjustment,3),
                'match_evidence':{'text':round(text,3),'visual':round(visual,3),'center':round(center,3),'contacts':contact_match,'kind_basis':current.get('kind_basis')},
                'reuse_experience':{'trials':trials,'mean_reward':total/max(1,trials),'priority_adjustment':adjustment},'screen_kind':current['kind'],
                'steps':bounded,'adaptation':adaptation,'observed_effect':row.get('effect',{}),
                'recorded_step_count':len(steps),'sequence_truncated':len(bounded)<len(steps),
                'recorded_hp':before.get('hp'),'current_hp':current.get('hp'),
                'historical_after_ocr':[q[:120] for q in row.get('after_features',{}).get('quotes',[]) if not re.search(r'HP|Lv|満腹|腹度|^\d+階$|^\d+G$',q,re.I)][:4],
                'note':'人間の実例であり正解・因果の証明ではない。結果は例全体の過去の観測であり現画面の事実ではない。現在の対象・方向・危険を再確認し、1入力ずつ候補として利用。一括再生しない。'})
        ranked.sort(key=lambda r:r['priority'],reverse=True)
        self.last_search.update(selected_ids=[r['id'] for r in ranked[:2]],matched=len(ranked),current_kind=current['kind'],hp_known=bool(current.get('hp')))
        return ranked[:2]

    def record_reuse(self,row):
        examples=row.get('demonstration_candidates') or []
        if not examples:return
        data={'example_ids':[e['id'] for e in examples],'executed':row.get('executed'),'observed_keys':row.get('observed_keys',[]),
            'not_executed_reason':row.get('not_executed_reason'),
            'match_evidence':[{'id':e['id'],'evidence':e.get('match_evidence'),'priority':e.get('priority')} for e in examples],
            'matches_first_step':[e['id'] for e in examples if e.get('steps') and e['steps'][0]['keys']==row.get('observed_keys')],
            'policy_source':(row.get('policy_choice') or {}).get('source'),'turn_kind':row.get('turn_kind'),
            'policy_result':row.get('policy_learning'),'combat_result':row.get('combat_evaluation'),
            'note':'候補提示と実入力の一致を記録。人間の例が原因で選択したことや成功は断定しない。'}
        eligible=bool(row.get('executed') and row.get('action_kind')=='input' and not row.get('dry_run') and row.get('turn_kind')!='aborted' and (row.get('input_receipt') or {}).get('os_submission')=='accepted')
        policy=row.get('policy_learning') or {};reward=policy.get('reward') if eligible and policy.get('status')=='trained' else None
        data.update(score_reward=reward,scored_ids=[],score_basis='existing_observation_policy_not_demonstration_success')
        with self.db:
            for example in examples:
                first=example.get('steps',[{}])[0] if example.get('steps') else {}
                # Only the matching first input is scored, never the unplayed sequence.
                duration_ok=abs(row.get('hold_ms',0)-first.get('hold_ms',0))<=max(30,first.get('hold_ms',0)*.4)
                if reward is None or not first.get('keys') or set(first['keys'])!=set(row.get('observed_keys',[])) or not duration_ok:continue
                self.db.execute('INSERT INTO human_example_scores VALUES(?,?,?,1,?) ON CONFLICT(profile,example_id,scene) DO UPDATE SET trials=trials+1,reward=reward+excluded.reward',
                    (self.profile,example['id'],example['screen_kind'],max(-1,min(1,reward))))
                data['scored_ids'].append(example['id'])
            self.db.execute('INSERT INTO human_reuse(profile,created,data) VALUES(?,?,?)',(self.profile,time.time(),json.dumps(data,ensure_ascii=False)))


class DemonstrationRecorder:
    def __init__(self,backend,store,ocr,control,emit,journal,recorder):
        self.backend=backend;self.store=store;self.ocr=ocr;self.control=control;self.emit=emit;self.journal=journal;self.recorder=recorder
        self.latest=None;self.sample_done=threading.Event();self.sample_events=queue.Queue(maxsize=128);self.done=threading.Event();self.finish_cancel=threading.Event();self.jobs=queue.Queue(maxsize=4);self.results=queue.Queue(maxsize=8)
        self.stats={'segments':0,'complete':0,'censored':0,'queue_dropped':0,'sample_interval_ms':20,'stop_reason':'','sample_count':0,'sample_gap_max_ms':0,'sample_gap_sum_ms':0,'sampling_gaps':0,'segment_queue_dropped':0}

    def write(self,row):
        self.journal.parent.mkdir(parents=True,exist_ok=True)
        with self.journal.open('a',encoding='utf-8') as stream:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
        self.emit(row)

    def capture_loop(self):
        while not self.done.is_set():
            if self.control.is_set():self.done.wait(.05);continue
            try:
                started=time.monotonic();png=self.backend.capture()
                self.latest={'png':png,'started':started,'ended':time.monotonic()}
            except Exception:self.latest=None
            self.done.wait(.1)

    def processing_loop(self):
        self.ocr.cancel=self.finish_cancel
        while not self.done.is_set() or not self.jobs.empty():
            try:identifier,row,frames=self.jobs.get(timeout=.1)
            except queue.Empty:continue
            try:
                before=self.ocr.read(frames[0]['png']);after=self.ocr.read(frames[1]['png'])
                row['before_features']=features(frames[0]['png'],before);row['after_features']=features(frames[1]['png'],after)
                if before.get('status')!='ok' or after.get('status')!='ok':row.update(complete=False,reason='ocr_unavailable')
                row['effect']=observed_effect(row['before_features'],row['after_features'])
            except Exception as error:row.update(complete=False,reason='processing_incomplete',processing_error=str(error)[:300])
            self.results.put((identifier,row));self.jobs.task_done()

    def collect(self):
        while True:
            try:identifier,row=self.results.get_nowait()
            except queue.Empty:return
            self.store.complete(identifier,row);key='complete' if row['complete'] else 'censored';self.stats[key]+=1
            self.write({'event':'human_example_processed','example_id':identifier,'status':key,'reason':row.get('reason',''),'effect':row.get('effect')})

    def save(self,segment):
        before=segment.pop('before');after=segment.pop('after');self.stats['segments']+=1
        row={**segment,'source':'human_example','session_clock':'monotonic','sampling':'20ms polling; very short taps may be missed',
            'controls':self.store.cfg.key_bindings,'profile':self.store.profile,
            'before_sha256':hashlib.sha256(before['png']).hexdigest() if before else None,
            'after_sha256':hashlib.sha256(after['png']).hexdigest() if after else None,
            'before_capture_interval':[before['started'],before['ended']] if before else None,
            'after_capture_interval':[after['started'],after['ended']] if after else None,
            'image_storage':'Bounded existing frame recorder; hashes do not guarantee files survived its storage cap'}
        for frame,phase in ((before,'observation'),(after,'result')):
            if frame:self.recorder.record(frame['png'],phase)
        identifier=self.store.add(row,'pending' if row['complete'] else 'censored')
        self.write({'event':'human_example_recorded','example_id':identifier,'complete_observation':row['complete'],'steps':row['steps'],'reason':row['reason'],'before_sha256':row['before_sha256'],'after_sha256':row['after_sha256']})
        if row['complete']:
            try:self.jobs.put_nowait((identifier,row,(before,after)))
            except queue.Full:
                row.update(complete=False,reason='processing_queue_full');self.store.complete(identifier,row);self.stats['queue_dropped']+=1;self.stats['censored']+=1
        else:self.stats['censored']+=1

    def sample_loop(self):
        """No SQLite, hashing, file IO, OCR, or UI emit on this thread."""
        segments=HumanSegments();focus_state=None;last_sample=None;revision=self.control.revision
        def enqueue(kind,value):
            try:self.sample_events.put_nowait((kind,value));return True
            except queue.Full:
                self.stats['segment_queue_dropped']+=int(kind=='segment');return False
        try:
            while not self.control.stop.is_set() and not self.done.is_set():
                now=time.monotonic()
                if self.control.paused.is_set() or revision!=self.control.revision:
                    for segment in segments.feed(False,[],None,now):enqueue('segment',segment)
                    revision=self.control.revision
                    if self.control.paused.is_set():self.control.stop.wait(.02);last_sample=None;continue
                snapshot=self.backend.human_input_snapshot(self.store.cfg.allowed_keys)
                now=time.monotonic()
                if last_sample is not None:
                    gap=(now-last_sample)*1000
                    self.stats['sample_count']+=1;self.stats['sample_gap_sum_ms']+=gap
                    self.stats['sample_gap_max_ms']=max(self.stats['sample_gap_max_ms'],gap)
                    if gap>100:
                        self.stats['sampling_gaps']+=1
                        for segment in segments.feed(False,[],None,now):
                            segment['reason']='input_sampling_gap';enqueue('segment',segment)
                last_sample=now
                focused=snapshot['target_focused'] and not snapshot.get('recording_blocked')
                state=(focused,snapshot.get('recording_blocked'))
                if state!=focus_state:
                    focus_state=state;enqueue('event',{'event':'human_focus','target_focused':focused,'reason':snapshot.get('recording_blocked'),
                        'note':'対象ゲームの前面中だけ許可キーを記録。他アプリのキーは取得・保存しません。'})
                for segment in segments.feed(focused,snapshot['keys'],self.latest,now):enqueue('segment',segment)
                self.control.stop.wait(.02)
        except Exception as error:self.stats['stop_reason']=str(error)
        finally:
            if segments.current:enqueue('segment',segments.finish(None,time.monotonic(),'recording_stopped'))
            self.sample_done.set()

    def drain_samples(self):
        while True:
            try:kind,value=self.sample_events.get_nowait()
            except queue.Empty:return
            if kind=='segment':self.save(value)
            else:self.write(value)

    def run(self):
        capture=threading.Thread(target=self.capture_loop,daemon=True)
        processor=threading.Thread(target=self.processing_loop,daemon=True)
        sampler=threading.Thread(target=self.sample_loop,daemon=True)
        capture.start();processor.start();sampler.start()
        try:
            while not self.sample_done.is_set():
                self.collect();self.drain_samples()
                self.sample_done.wait(.01)
        finally:
            self.drain_samples();self.done.set();deadline=time.monotonic()+2
            while processor.is_alive() and time.monotonic()<deadline:
                self.collect();processor.join(.05)
            self.finish_cancel.set();self.collect()
            self.stats['pending_incomplete']=self.store.db.execute("SELECT COUNT(*) FROM human_examples WHERE profile=? AND status='pending'",(self.store.profile,)).fetchone()[0]
            with self.store.db:self.store.db.execute("UPDATE human_examples SET status='incomplete' WHERE profile=? AND status='pending'",(self.store.profile,))
            self.stats['sample_gap_mean_ms']=round(self.stats.pop('sample_gap_sum_ms')/max(1,self.stats['sample_count']),2)
            self.stats['sample_gap_max_ms']=round(self.stats['sample_gap_max_ms'],2)
        return dict(self.stats)
