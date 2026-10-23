"""Explicit console commands: one in-flight input, journal before send, then observe."""
import hashlib
import json
import os
import queue
import threading
import time
from .demonstrations import features,observed_effect

class ConsoleCommands:
    def __init__(self,cfg):
        self.cfg=cfg;self.queue=queue.Queue(maxsize=1);self.lock=threading.Lock();self.ready=False;self.closed=False;self.sequence=0
    def submit(self,keys,hold_ms,wait_ms):
        keys=list(dict.fromkeys(keys))
        from .controls import is_torneko
        if is_torneko(self.cfg.window_title):keys.sort(key=lambda k:(k not in ('num5','num3','num2'),k))
        if any(k not in self.cfg.allowed_keys for k in keys) or len(keys)>4:raise ValueError('許可されていないキーです。')
        if not 1<=hold_ms<=self.cfg.max_hold_ms or not 100<=wait_ms<=10000:raise ValueError('保持時間・待機時間が範囲外です。')
        with self.lock:
            if not self.ready or self.closed:return False
            self.sequence+=1;self.ready=False
            self.queue.put_nowait({'request_id':self.sequence,'keys':keys,'hold_ms':hold_ms,'wait_ms':wait_ms,'requested_at':time.time()})
        return True
    def set_ready(self,value):
        with self.lock:self.ready=bool(value and not self.closed)
    def close(self):
        with self.lock:self.closed=True;self.ready=False

class ConsolePlayer:
    def __init__(self,backend,store,ocr,control,commands,emit,journal,recorder):
        self.backend=backend;self.store=store;self.ocr=ocr;self.control=control;self.commands=commands;self.emit=emit;self.journal=journal;self.recorder=recorder
        self.ocr.cancel=control;self.stats={'submitted':0,'complete':0,'incomplete':0,'observations':0}
        from .world_model import WorldModel
        import sqlite3
        self.world=WorldModel(store if isinstance(getattr(store,'db',None),sqlite3.Connection) else None,journal.stem)
        self.next_ready_at=0.
    def log(self,row):
        self.journal.parent.mkdir(parents=True,exist_ok=True)
        with self.journal.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush();os.fsync(stream.fileno())
        self.emit(row)
    def read(self,png):
        try:return self.ocr.read(png)
        except Exception as error:return {'status':'unavailable','reason':str(error),'lines':[]}
    def execute(self,command):
        revision=self.control.revision
        self.log({'event':'human_console_accepted',**command,'executed':False})
        before=self.backend.capture();before_time=time.monotonic();native0=self.read(before)
        self.recorder.record(before,'observation')
        self.log({'event':'human_console_requested',**command,'before_sha256':hashlib.sha256(before).hexdigest(),'ocr_before':native0})
        if self.control.is_set() or self.control.revision!=revision:
            self.log({'event':'human_console_cancelled',**command,'reason':'送信前に停止または一時停止','executed':False});return
        receipt={'os_submission':'not_sent'};after=None;error=None;kind='observe';release_at=None
        submission_started=time.monotonic()
        try:
            if command['keys']:
                receipt=self.backend.act(command['keys'],command['hold_ms'],self.control);self.stats['submitted']+=1
            else:self.stats['observations']+=1
            release_at=time.monotonic()
            self.next_ready_at=release_at+command['wait_ms']/1000
            self.log({'event':'human_console_sent','request_id':command['request_id'],'receipt':receipt,'keys':command['keys']})
            observation=self.backend.observe_turn(before,'human_console',command['wait_ms'],self.control)
            after=observation.image;kind=observation.kind
            self.control.wait(max(0,command['wait_ms']/1000-(time.monotonic()-release_at)))
        except Exception as failure:
            error=str(failure)
            if command['keys'] and release_at is None:receipt={'os_submission':'unknown','reason':'送信処理が完了しておらず部分送信の可能性を排除できません。'}
        finally:
            if command['keys']:
                try:self.backend.release_all()
                except Exception as failure:error=(error or '')+' / キー解放: '+str(failure)
        native1=self.read(after) if after and not self.control.is_set() else {'status':'unavailable','lines':[]}
        if after:self.recorder.record(after,'result')
        before_features=features(before,native0);after_features=features(after,native1) if after else None
        accepted=bool(command['keys'] and receipt.get('os_submission')=='accepted')
        complete=bool(accepted and not error and kind!='aborted' and not self.control.is_set() and self.control.revision==revision and time.monotonic()>=self.next_ready_at and before_features['ocr_status']=='ok' and after_features and after_features['ocr_status']=='ok')
        row={**command,'source':'human_console','complete':complete,'action_kind':'input' if command['keys'] else 'observe',
            'reason':error or ('neutral_observation' if not command['keys'] else '' if complete else 'observation_or_submission_incomplete'),
            'steps':[{'keys':command['keys'],'duration_ms':command['hold_ms']}],
            'input_receipt':receipt,'before_features':before_features,'after_features':after_features,
            'effect':observed_effect(before_features,after_features) if after_features else {},
            'before_sha256':hashlib.sha256(before).hexdigest(),'after_sha256':hashlib.sha256(after).hexdigest() if after else None,
            'timing':{'hold_ms_requested':command['hold_ms'],'reinput_minimum_ms':command['wait_ms'],
                'backend_submission_ms':round((release_at-submission_started)*1000) if release_at is not None else None,
                'turn_kind':kind,'total_ms':round((time.monotonic()-before_time)*1000)},
            'note':'コンソール指定入力。OS受付とゲーム反応は別。保持時間は指定値で、ゲームが受理した長さの証明ではない。'}
        from .knowledge import KnowledgeEngine
        def sensed(native):return {'metrics':KnowledgeEngine().reconcile(native)['metrics'],'minimap':{'status':'unavailable'},'eligible':False}
        initial=sensed(native0);final=sensed(native1)
        row['world_observation']=self.world.observe(before,native0,initial)
        row['world_learning']=self.world.feedback(initial,final,{'executed':complete,'observed_keys':command['keys'],'input_receipt':receipt,'turn_kind':kind},native0,native1,{},before,after or before)
        self.world.inventory.observe(native1,after)
        identifier=self.store.add(row,'complete' if complete else 'censored') if command['keys'] else None
        self.stats['complete' if complete else 'incomplete']+=int(bool(command['keys']))
        self.log({'event':'human_console_result','example_id':identifier,**row})
        if error:raise RuntimeError(error)
    def run(self):
        try:
            while self.control.checkpoint():
                if time.monotonic()<self.next_ready_at:
                    self.control.stop.wait(min(.05,self.next_ready_at-time.monotonic()));continue
                self.commands.set_ready(True);self.emit({'event':'human_console_ready','ready':True})
                while not self.control.is_set():
                    try:command=self.commands.queue.get(timeout=.05);break
                    except queue.Empty:continue
                else:
                    self.commands.set_ready(False)
                    while True:
                        try:pending=self.commands.queue.get_nowait()
                        except queue.Empty:break
                        self.log({'event':'human_console_cancelled',**pending,'executed':False,'reason':'一時停止または停止で未送信要求を破棄'})
                    if not self.control.checkpoint():break
                    continue
                self.emit({'event':'human_console_ready','ready':False})
                self.execute(command)
        finally:
            self.commands.close()
            while True:
                try:pending=self.commands.queue.get_nowait()
                except queue.Empty:break
                self.log({'event':'human_console_cancelled',**pending,'executed':False,'reason':'終了時の未送信要求を破棄'})
            self.emit({'event':'human_console_ready','ready':False})
        return self.stats
