"""Known-operation diagnostics are deliberately isolated from agent memory."""
import hashlib
import io
import json
import os
import sys
import importlib.metadata
from pathlib import Path
import sqlite3
import tempfile
import time
import zipfile

PROFILE=Path(__file__).resolve().parent.parent/'knowledge/profile.json'

def diagnostic_keys():
    profile=json.loads(PROFILE.read_text(encoding='utf-8'))
    return [profile['input']['keys'][key].lower() for key in profile['diagnostics']['toggle']]

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

def menu_diagnostic_evidence(ocr):
    """The profile toggle is not a valid round-trip probe on the title menu."""
    from .knowledge import normalize
    if ocr.get('status')!='ok':return []
    options=('ぼうけんをする','ぼうけんのしょをつくる','ぼうけんのしょをけす','むらのなまえをかえる','ぼうけんをやめる')
    matched=[line['text'] for line in ocr.get('lines',[]) if any(normalize(option) in normalize(line.get('text','')) for option in options)]
    distinct={option for option in options if any(normalize(option) in normalize(line) for line in matched)}
    return matched if len(distinct)>=2 else []

class InputDiagnostics:
    def __init__(self,backend,store,emit,control,leaked_keys=lambda:0,scene_probe=None):
        self.backend=backend;self.store=store;self.emit=emit;self.control=control;self.leaked_keys=leaked_keys;self.scene_probe=scene_probe;self.last_status='not_run'
    def run(self,separation=False):
        kind='separation' if separation else 'compatibility'
        run=self.store.start(kind);status='failed';detail='未完了';cycles=0
        try:
            if not self.backend.matches_profile():
                raise RuntimeError('既知操作の診断は同梱プロファイルの対象プロセス・クラスにだけ実行できます。')
            if not separation and self.scene_probe:
                image=self.backend.capture();ocr=self.scene_probe(image)
                evidence=menu_diagnostic_evidence(ocr)
                if evidence:
                    status='not_applicable';detail='開始メニューでは既知操作の往復を検証できないため、診断を未実施として攻略判断へ進みます。'
                    if hasattr(self.backend,'record_frame'):self.backend.record_frame(image,'diagnostic_not_applicable')
                    row={'event':'diagnostic_skipped','status':status,'reason':detail,'ocr_evidence':evidence,
                        'frame_sha256':hashlib.sha256(image).hexdigest(),'learning':False}
                    self.store.cycle(run,row);self.emit(row)
                    return False
            keys=diagnostic_keys(); started=time.monotonic()
            while separation or cycles<3:
                if not self.control.checkpoint(): status='interrupted';detail='停止要求';break
                if separation and time.monotonic()-started>=600:
                    status='passed';detail='10分間の往復・入力分離を確認';break
                foreground=self.backend.foreground()
                if foreground==self.backend.hwnd:
                    raise RuntimeError('分離確認用ウィンドウが前面でないため、背面入力を検証できません。')
                leaked=self.leaked_keys();baseline=self.backend.capture()
                # Estimate idle animation noise before attributing changes to a key.
                if self.control.wait(.3): continue
                idle=self.backend.capture();noise=self.backend.difference(baseline,idle)
                frames=[];observations=[];interrupted=False
                for key in keys:
                    if not self.control.checkpoint(): interrupted=True;break
                    # A pause invalidates a half-finished known-operation pair.
                    revision=self.control.revision
                    before=self.backend.capture()
                    self.backend.act([key],80,self.control)
                    turn=self.backend.observe_turn(before,'diagnostic',300,self.control)
                    frames.append(turn.image);observations.append({'kind':turn.kind,'duration_ms':turn.duration_ms,'raw_change':turn.raw_change})
                    if revision!=self.control.revision or self.control.is_set(): interrupted=True;break
                if interrupted:
                    status='interrupted';detail='往復の途中で一時停止・停止したため未検証';break
                if hasattr(self.backend,'record_frame'):
                    self.backend.record_frame(idle,'diagnostic_baseline')
                    self.backend.record_frame(frames[0],'diagnostic_changed')
                    self.backend.record_frame(frames[1],'diagnostic_returned')
                change=self.backend.difference(idle,frames[0]);returned=self.backend.difference(idle,frames[1])
                isolated=self.backend.foreground()==foreground and self.leaked_keys()==leaked
                passed=change>max(1.,noise*3) and returned<=max(3.,noise*2) and isolated
                cycles+=1
                row={'event':'diagnostic','run_id':run,'cycle':cycles,'kind':kind,
                     'changed':change,'return_difference':returned,'idle_noise':noise,
                     'isolated':isolated,'passed':passed,'turns':observations,
                     'learning':False,'frame_sha256':hashlib.sha256(frames[-1]).hexdigest()}
                self.store.cycle(run,row);self.emit(row)
                if not passed: raise RuntimeError('既知操作の変化・復帰・前面アプリへの非干渉を確認できません。診断結果を確認してください。')
            else:
                status='passed';detail='3往復の画面変化・復帰・入力分離を確認（攻略動作の保証ではありません）'
            return status=='passed'
        except Exception as error:
            detail=str(error);raise
        finally:
            release_error=None
            try: self.backend.release_all()
            except Exception as error:
                release_error=error;status='failed';detail+=' / キー解放失敗: '+str(error)
            finally:
                self.last_status=status
                self.store.finish(run,status,detail)
                self.emit({'event':'diagnostic_summary','status':status,'reason':detail,'cycles':cycles,'learning':False})
            if release_error:raise release_error


def export_diagnostics(data,destination):
    """SQLite online backup includes committed WAL. Never copy a live .sqlite file."""
    data=Path(data).resolve();destination=Path(destination).resolve();destination.parent.mkdir(parents=True,exist_ok=True)
    report={'created_at':time.time(),'databases':{},'files':[],'omitted':[],
            'note':'診断は攻略用の経験と別DBです。未実施の検査を成功とは判定しません。'}
    report['runtime']={'python':sys.version,'platform':sys.platform,'packages':{}}
    for name in ('Pillow','rapidocr','onnxruntime'):
        try:report['runtime']['packages'][name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:report['runtime']['packages'][name]='not_installed'
    temporary=destination.with_name(destination.name+'.part')
    try:
        with tempfile.TemporaryDirectory(prefix='gamepilot-export-') as folder,zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as archive:
            for name in ('memory.sqlite','diagnostics.sqlite'):
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
                    if name=='diagnostics.sqlite':
                        entry['runs']=[dict(zip(('id','kind','status','detail'),r)) for r in target.execute('SELECT id,kind,status,detail FROM runs ORDER BY id DESC LIMIT 20')]
                    report['databases'][name]=entry
                except (sqlite3.Error,OSError,TimeoutError) as error:
                    report['databases'][name]={'status':'failed','reason':str(error)}
                finally:
                    if target:target.close()
                    if source:source.close()
                if report['databases'][name]['status']=='ok':archive.write(Path(folder)/name,name)
            candidates=[data/'settings.json',data/'ui-settings.json',data/'components.json']
            candidates+=sorted((data/'runs').glob('*.jsonl'),key=lambda p:p.stat().st_mtime,reverse=True)[:20]
            candidates+=sorted(data.glob('*.log'))
            candidates+=sorted((data/'ocr-tests').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:10]
            # Newest real session only: keep attachment bounded and preserve
            # its exact decision/result frames and recording manifest.
            recordings=sorted((data/'recordings').glob('*'),key=lambda p:p.stat().st_mtime,reverse=True)
            if recordings: candidates+=sorted(p for p in recordings[0].iterdir() if p.is_file())
            candidates+=sorted((data/'sessions').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:20]
            remaining=160*1024*1024
            for path in candidates:
                if not path.is_file(): continue
                limit=min(remaining,10*1024*1024);relative=path.relative_to(data).as_posix()
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
                    if path.is_file() and path.stat().st_size<=10*1024*1024:
                        archive.write(path,'runtime/'+name);report['files'].append('runtime/'+name)
            archive.writestr('health.txt','\n'.join(health))
            archive.writestr('manifest.json',json.dumps(report,ensure_ascii=False,indent=2))
        temporary.replace(destination)
    finally:
        if temporary.exists(): temporary.unlink()
    return report
