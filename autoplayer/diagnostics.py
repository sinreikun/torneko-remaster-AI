"""Known-operation diagnostics are deliberately isolated from agent memory."""
import hashlib
import gzip
import json
import os
import sys
import importlib.metadata
from pathlib import Path
import sqlite3
import tempfile
import time
import zipfile


def export_human_examples(db,archive,folder,limit_bytes=2*1024*1024):
    """Small feature records, never images or the unbounded learning database."""
    path=Path(folder)/'human-examples.jsonl.gz'
    total=db.execute('SELECT COUNT(*) FROM human_examples').fetchone()[0]
    included=0;raw_bytes=0
    scores={}
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='human_example_scores'").fetchone():
        for profile,identifier,scene,trials,reward in db.execute('SELECT * FROM human_example_scores'):
            scores.setdefault((profile,identifier),[]).append({'scene':scene,'trials':trials,'reward_sum':reward})
    with path.open('wb') as output,gzip.GzipFile(fileobj=output,mode='wb',mtime=0) as packed:
        for identifier,profile,created,status,data in db.execute('SELECT id,profile,created,status,data FROM human_examples ORDER BY id DESC'):
            row=json.loads(data)
            item={k:row[k] for k in ('source','request_id','requested_at','keys','hold_ms','wait_ms','timing','input_receipt','steps','reason','before_features','after_features','effect','before_sha256','after_sha256','before_capture_interval','after_capture_interval','sampling') if k in row}
            item.update(id=identifier,profile=profile,created=created,status=status,reuse_scores=scores.get((profile,identifier),[]))
            encoded=(json.dumps(item,ensure_ascii=False,separators=(',',':'))+'\n').encode()
            # Bound both decompressed volume and compressed payload. Reserve enough
            # space for the next record and gzip trailer before appending it.
            if len(encoded)>65536 or raw_bytes+len(encoded)>8*limit_bytes or output.tell()+len(encoded)+64>limit_bytes:break
            packed.write(encoded);packed.flush();raw_bytes+=len(encoded);included+=1
    archive.write(path,'demonstrations/human-examples.jsonl.gz',compress_type=zipfile.ZIP_STORED)
    return {'total':total,'included':included,'truncated':included<total,'order':'newest_first',
        'compressed_bytes':path.stat().st_size,'limit_bytes':limit_bytes,'images_included':False,
        'path':'demonstrations/human-examples.jsonl.gz','local_full_data_retained':True}

class DiagnosticStore:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS runs(
            id INTEGER PRIMARY KEY,started REAL,ended REAL,kind TEXT,status TEXT,detail TEXT);
            CREATE TABLE IF NOT EXISTS cycles(id INTEGER PRIMARY KEY,run_id INTEGER,time REAL,data TEXT);''')
    def start(self,kind):
        row=self.db.execute('INSERT INTO runs(started,kind,status) VALUES(?,?,?)',(time.time(),kind,'running'))
        self.db.commit();return row.lastrowid
    def cycle(self,run,value):
        self.db.execute('INSERT INTO cycles(run_id,time,data) VALUES(?,?,?)',(run,time.time(),json.dumps(value,ensure_ascii=False)))
        self.db.commit()
    def finish(self,run,status,detail):
        self.db.execute('UPDATE runs SET ended=?,status=?,detail=? WHERE id=?',(time.time(),status,detail,run));self.db.commit()
    def close(self): self.db.close()

class InputDiagnostics:
    """Probe the OS queue without injecting any game key or assuming a State."""
    def __init__(self,backend,store,emit,control,clock=time.monotonic):
        self.backend=backend;self.store=store;self.emit=emit;self.control=control
        self.clock=clock;self.last_status='not_run'
    def run(self,separation=False):
        kind='separation' if separation else 'compatibility'
        run=self.store.start(kind);status='failed';detail='未完了';cycles=0
        started=self.clock()
        try:
            while cycles<3 or separation:
                if not self.control.checkpoint():status='interrupted';detail='停止要求';break
                if separation and self.clock()-started>=600:break
                receipt=self.backend.probe_input()
                if receipt.get('os_submission')!='accepted':raise RuntimeError('OS/Backendの送信経路を確認できません。')
                baseline=self.backend.capture()
                if self.control.wait(1. if separation else .3):continue
                after=self.backend.capture();cycles+=1
                change=self.backend.difference(baseline,after)
                row={'event':'diagnostic','run_id':run,'cycle':cycles,'kind':kind,
                    'action_kind':'observe','keys':[],'backend':receipt,
                    'natural_change':change,'game_response':'not_tested',
                    'input_isolation':'not_exercised_no_keys_sent',
                    'attribution':'natural_or_external_uncontrolled','learning':False,
                    'frame_sha256':hashlib.sha256(after).hexdigest()}
                if hasattr(self.backend,'record_frame'):self.backend.record_frame(after,'diagnostic_neutral')
                self.store.cycle(run,row);self.emit(row)
            if status!='interrupted':
                status='completed';detail='OS送信経路と無入力観測を確認。キー受理・ゲーム反応・入力分離の実証は未実施。'
            return status=='completed'
        except Exception as error:
            detail=str(error);raise
        finally:
            release_error=None
            try:self.backend.release_all()
            except Exception as error:release_error=error;status='failed';detail+=' / キー解放失敗: '+str(error)
            self.last_status=status;self.store.finish(run,status,detail)
            self.emit({'event':'diagnostic_summary','status':status,'reason':detail,'cycles':cycles,'learning':False})
            if release_error:raise release_error


def export_diagnostics(data,destination,detailed=False):
    """SQLite online backup includes committed WAL. Never copy a live .sqlite file."""
    data=Path(data).resolve();destination=Path(destination).resolve();destination.parent.mkdir(parents=True,exist_ok=True)
    remaining=(160 if detailed else 24)*1024*1024
    report={'bundle_mode':'detailed' if detailed else 'compact','local_full_data_retained':True,'created_at':time.time(),'databases':{},'files':[],'omitted':[],
            'note':'診断は攻略用の経験と別DBです。未実施の検査を成功とは判定しません。'}
    report['runtime']={'python':sys.version,'platform':sys.platform,'packages':{}}
    for name in ('Pillow','rapidocr','onnxruntime'):
        try:report['runtime']['packages'][name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:report['runtime']['packages'][name]='not_installed'
    temporary=destination.with_name(destination.name+'.part')
    try:
        with tempfile.TemporaryDirectory(prefix='gamepilot-export-') as folder,zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as archive:
            for name in ('memory.sqlite','diagnostics.sqlite','world-v2.sqlite'):
                path=data/name
                if not path.exists(): report['databases'][name]={'status':'not_run'};continue
                source=None;target=None
                try:
                    source=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)
                    target=sqlite3.connect(Path(folder)/name)
                    deadline=time.monotonic()+15
                    def progress(*_):
                        if time.monotonic()>deadline: raise TimeoutError('SQLite backup timed out')
                    source.backup(target,pages=256,sleep=.05,progress=progress)
                    check=target.execute('PRAGMA integrity_check').fetchone()[0]
                    entry={'status':'ok' if check=='ok' else 'failed','integrity':check}
                    if name=='world-v2.sqlite':
                        from .nextgen.store import diagnostic_summary
                        entry['generation_two']=diagnostic_summary(target)
                        encoded=json.dumps(entry['generation_two'],ensure_ascii=False).encode()
                        archive.writestr('nextgen/summary.json',encoded);remaining-=len(encoded)
                    if name=='diagnostics.sqlite':
                        entry['runs']=[dict(zip(('id','kind','status','detail'),r)) for r in target.execute('SELECT id,kind,status,detail FROM runs ORDER BY id DESC LIMIT 20')]
                        entry['recent_cycles']=[r[0][:8192] for r in target.execute('SELECT data FROM cycles ORDER BY id DESC LIMIT 100')]
                    if name=='memory.sqlite' and target.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='policy_actions'").fetchone():
                        entry['policy_summary']=[dict(zip(('profile','state_actions','trials','positive_progress_observations','goal_trials','goal_wins'),r)) for r in target.execute('SELECT profile,COUNT(*),SUM(trials),SUM(positive),SUM(goal_trials),SUM(goal_wins) FROM policy_actions GROUP BY profile')]
                        entry['recent_policy_events']=[r[0][:12000] for r in target.execute('SELECT data FROM policy_events ORDER BY id DESC LIMIT 30')]
                    if name=='memory.sqlite' and target.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='improvement_events'").fetchone():
                        entry['recent_improvement_events']=[{'profile':r[0],'data':r[1][:16000]} for r in target.execute('SELECT profile,data FROM improvement_events ORDER BY id DESC LIMIT 30')]
                        entry['improvement_state']=[{'profile':r[0],'data':r[1][:16000]} for r in target.execute('SELECT profile,data FROM improvement_state LIMIT 20')]
                    if name=='memory.sqlite':
                        operational={}
                        for table in ('action_effects_v1','operational_experiments_v1'):
                            if target.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():
                                column='action' if table=='action_effects_v1' else 'test'
                                operational[table]={'total':target.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],
                                    'recent':[{'profile':r[0],'context':r[1],column:json.loads(r[2]) if column=='action' else r[2],'data':json.loads(r[3])} for r in target.execute('SELECT profile,context,'+column+',data FROM '+table+' ORDER BY updated DESC LIMIT 12')]}
                        if target.execute("SELECT 1 FROM sqlite_master WHERE name='experience_rules_v1'").fetchone():
                            operational['experience_rules_v1']={'recent':[{'rule_key':r[0],'data':json.loads(r[1])} for r in target.execute('SELECT rule_key,data FROM experience_rules_v1 ORDER BY updated DESC LIMIT 12')]}
                        if target.execute("SELECT 1 FROM sqlite_master WHERE name='experience_rules_v1'").fetchone():
                            from .experience_cycle import maturity
                            rules=[]
                            for profile,key,raw in target.execute('SELECT profile,rule_key,data FROM experience_rules_v1 ORDER BY updated DESC LIMIT 8'):
                                value=json.loads(raw)
                                rules.append({'profile':profile,'rule_key':key,**{k:value[k] for k in ('revision','samples','scope_kind','observed_conditions') if k in value},
                                    'fields':{k:{'status':maturity(v),'recent_values':v.get('values',[])[-6:],'recent_checks':v.get('checks',[])[-12:],'counterexamples':v.get('counterexamples',0)} for k,v in value.get('fields',{}).items()}})
                            checks=[json.loads(r[0]) for r in target.execute('SELECT data FROM experience_checks_v1 ORDER BY id DESC LIMIT 12')]
                            projection=json.dumps({'rules':rules,'recent_checks':checks,'bounded_projection':True,'full_data_retained_locally':True},ensure_ascii=False).encode()
                            if len(projection)<min(128000,remaining):
                                archive.writestr('supervision/experience-cycle.json',projection);remaining-=len(projection)
                                entry['experience_cycle_export']={'path':'supervision/experience-cycle.json','bytes':len(projection),'depends_on_raw_database':False}
                        if operational:
                            encoded=json.dumps(operational,ensure_ascii=False).encode()
                            if len(encoded)<min(256000,remaining):
                                archive.writestr('supervision/learning.json',encoded);remaining-=len(encoded)
                                entry['operational_learning_export']={'path':'supervision/learning.json','bytes':len(encoded),'depends_on_raw_database':False}
                        if target.execute("SELECT 1 FROM sqlite_master WHERE name='human_examples'").fetchone():
                            entry['human_example_export']=export_human_examples(target,archive,folder)
                            remaining-=entry['human_example_export']['compressed_bytes']
                            entry['human_example_status_counts']=dict(target.execute('SELECT status,COUNT(*) FROM human_examples GROUP BY status'))
                        if target.execute("SELECT 1 FROM sqlite_master WHERE name='exterior_maps_v1'").fetchone():
                            maps=[{'profile':r[0],'map_id':r[1],'graph':json.loads(r[2])} for r in target.execute('SELECT profile,id,data FROM exterior_maps_v1 ORDER BY updated DESC LIMIT 8')]
                            archive.writestr('exterior_maps/maps.json',json.dumps(maps,ensure_ascii=False));remaining-=len(json.dumps(maps).encode())
                            entrances=[]
                            entry_rows=[]
                            for saved_map in maps:
                                entry_rows.extend(target.execute('SELECT profile,map_id,id,pose,image FROM exterior_entries_v1 WHERE profile=? AND map_id=? ORDER BY updated DESC LIMIT 1',(saved_map['profile'],saved_map['map_id'])))
                            for profile,mid,eid,pose,png in entry_rows:
                                entry_image_name='exterior_maps/'+mid+'-'+eid+'.png'
                                if len(png)>remaining:continue
                                archive.writestr(entry_image_name,png);remaining-=len(png);entrances.append({'profile':profile,'map_id':mid,'entry_id':eid,'pose':json.loads(pose),'image':entry_image_name})
                            archive.writestr('exterior_maps/entries.json',json.dumps(entrances,ensure_ascii=False))
                            portals=[json.loads(r[0]) for r in target.execute('SELECT data FROM exterior_portals_v1 ORDER BY updated DESC LIMIT 32')]
                            archive.writestr('exterior_maps/transitions.json',json.dumps(portals,ensure_ascii=False))
                            entry['exterior_maps']={'maps':len(maps),'entrance_images':len(entrances),'portals':len(portals),'source':'bounded_local_atlas'}
                        for table in ('method_stats','background_evidence','game_hypotheses','input_durations','novelty_states','novelty_actions','novelty_outcomes','combat_actions','combat_observations','combat_message_reads','battle_appearance_links','human_examples','human_reuse','human_example_scores','forecast_stats','world_events','experience_checks_v1','item_use_stats'):
                            if target.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                                cursor=target.execute('SELECT * FROM '+table+(' ORDER BY id DESC' if table in ('world_events','combat_message_reads','experience_checks_v1') else ' ORDER BY created DESC' if table=='battle_appearance_links' else '')+' LIMIT 40')
                                labels=[column[0] for column in cursor.description]
                                entry[table]=[dict(zip(labels,row)) for row in cursor]
                    report['databases'][name]=entry
                except (sqlite3.Error,OSError,TimeoutError) as error:
                    report['databases'][name]={'status':'failed','reason':str(error)}
                finally:
                    if target:target.close()
                    if source:source.close()
                if report['databases'][name]['status']=='ok':
                    snapshot=Path(folder)/name;size=snapshot.stat().st_size
                    if detailed or size<=min(remaining,6*1024*1024):
                        archive.write(snapshot,name);remaining-=size
                    else:
                        report['databases'][name]['snapshot_included']=False
                        report['databases'][name]['reason']='軽量ZIPの上限によりDB本体は省略。ローカルの元DBを保持。'
                        report['omitted'].append(name)
            candidates=[data/'settings.json',data/'ui-settings.json',data/'components.json',data/'ui-performance.json']
            candidates+=sorted((data/'profiles').glob('*.json'))
            candidates+=sorted((data/'research').glob('*.json'))
            candidates+=sorted((data/'runs').glob('*.jsonl'),key=lambda p:p.stat().st_mtime,reverse=True)[:(20 if detailed else 3)]
            candidates+=sorted(data.glob('*.log'))
            candidates+=sorted((data/'ocr-tests').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:10]
            # Newest real session only: keep attachment bounded and preserve
            # its exact decision/result frames and recording manifest.
            recordings=sorted((data/'recordings').glob('*'),key=lambda p:p.stat().st_mtime,reverse=True)
            if recordings:
                files=[p for p in recordings[0].iterdir() if p.is_file()]
                metadata=sorted(p for p in files if p.suffix!='.png')
                images=sorted((p for p in files if p.suffix=='.png'),key=lambda p:p.stat().st_mtime,reverse=True)
                selected=[]
                if not detailed:
                    manifest=recordings[0]/'frames.jsonl'
                    if manifest.is_file():
                        latest={};pending=None;map_images={};pending_map=None;unpaired_map={}
                        with manifest.open(encoding='utf-8') as stream:
                            for line in stream:
                                try:frame=json.loads(line)
                                except ValueError:continue
                                name=frame.get('file');phase=frame.get('phase')
                                if phase in ('map_observation','map_result') and isinstance(name,str) and Path(name).name==name and name.endswith('.png'):
                                    map_path=recordings[0]/name
                                    if map_path.is_file():unpaired_map[phase]=map_path
                                    if phase=='map_observation':pending_map=map_path if map_path.is_file() else None
                                    elif map_path.is_file() and pending_map:
                                        map_images={'map_observation':pending_map,'map_result':map_path};pending_map=None
                                if phase in ('observation','result') and isinstance(name,str) and Path(name).name==name and name.endswith('.png'):
                                    path=recordings[0]/name
                                    if phase=='observation':pending=path if path.is_file() else None
                                    elif phase=='result' and path.is_file() and pending:
                                        latest={'observation':pending,'result':path};pending=None
                        if latest:
                            report['image_evidence']={'mode':'latest_complete_observation_result',**{phase:path.relative_to(data).as_posix() for phase,path in latest.items()}}
                        elif pending:
                            latest={'observation':pending};report['image_evidence']={'mode':'observation_only','observation':pending.relative_to(data).as_posix()}
                        selected=list(dict.fromkeys(latest[phase] for phase in ('observation','result') if phase in latest))
                    if manifest.is_file() and not map_images and unpaired_map:
                        map_images=unpaired_map;report['map_image_pair_status']='unpaired'
                    elif manifest.is_file() and map_images:report['map_image_pair_status']='complete'
                    if manifest.is_file() and map_images:
                        selected+=list(map_images.values());report['map_image_evidence']={phase:path.relative_to(data).as_posix() for phase,path in map_images.items()}
                    selected=list(dict.fromkeys(selected))
                    selected+=(p for p in images if p not in selected)
                important=sorted((recordings[0]/'important').rglob('*'))
                candidates=[p for p in important if p.is_file()]+candidates
                report['important_event_evidence']={'files':sum(p.is_file() for p in important),'capacity_events':12,'local_limit_bytes':8*1024*1024}
                candidates+=metadata+(images if detailed else selected[:4] if report.get('map_image_evidence') else selected[:2])
            candidates+=sorted((data/'sessions').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:20]
            for path in candidates:
                if not path.is_file(): continue
                limit=min(max(0,remaining),(10 if detailed else 2)*1024*1024);relative=path.relative_to(data).as_posix()
                if path.stat().st_size>limit:
                    if path.suffix in ('.log','.jsonl') and limit>=1024:
                        with path.open('rb') as source:
                            source.seek(-limit,2);source.readline();tail=source.read(limit)
                        archive.writestr(relative,tail);remaining-=len(tail)
                        report['files'].append(relative)
                        report.setdefault('truncated',[]).append({'file':relative,'original_bytes':path.stat().st_size,'included_tail_bytes':len(tail)})
                    else:report['omitted'].append(relative)
                    continue
                remaining-=path.stat().st_size;archive.write(path,relative);report['files'].append(relative)
            health=['診断レポート（自動検査）']
            for name,value in report['databases'].items():
                health.append(f"{name}: {value['status']}")
                if value.get('reason'):health.append('  '+value['reason'])
                for run in value.get('runs',[]): health.append(f"  #{run['id']} {run['kind']}: {run['status']} {run['detail'] or ''}")
            for file in sorted((data/'sessions').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:1]:
                try:
                    session=json.loads(file.read_text(encoding='utf-8'))
                    report['latest_session']=session
                    health.append('最新実行: '+str(session.get('status'))+' / '+str(session.get('reason','')))
                except (OSError,ValueError) as error: health.append('実行サマリー読取失敗: '+str(error))
            health.append('実ゲーム攻略完了・未実施の互換性試験・OCR精度は、この出力だけでは保証しません。')
            if os.environ.get('GAMEPILOT_DATA_DIR') and data.name=='data':
                for name in ('bootstrap.json','setup-trace.log','install.stdout.log','install.stderr.log','ollama.stdout.log','ollama.stderr.log','setup-error.log'):
                    path=data.parent/name
                    if not path.is_file():continue
                    relative='runtime/'+name;size=path.stat().st_size
                    limit=min(max(0,remaining),(10 if detailed else 2)*1024*1024)
                    if size<=limit:
                        archive.write(path,relative);remaining-=size;report['files'].append(relative)
                    elif path.suffix=='.log' and limit>=1024:
                        with path.open('rb') as source:
                            source.seek(-limit,2);source.readline();tail=source.read(limit)
                        archive.writestr(relative,tail);remaining-=len(tail);report['files'].append(relative)
                        report.setdefault('truncated',[]).append({'file':relative,'original_bytes':size,'included_tail_bytes':len(tail)})
                    else:report['omitted'].append(relative)
            archive.writestr('health.txt','\n'.join(health))
            archive.writestr('manifest.json',json.dumps(report,ensure_ascii=False,indent=2))
        if not detailed and temporary.stat().st_size>28*1024*1024:raise RuntimeError('軽量診断ZIPの上限を超えました。元データは保持しています。')
        temporary.replace(destination)
    finally:
        if temporary.exists(): temporary.unlink()
    return report
