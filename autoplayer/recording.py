"""Bounded, asynchronous real-screen recording for reproducible field reports."""
import copy
import hashlib
from collections import deque
import json
from pathlib import Path
import queue
import threading
import time

class FrameRecorder:
    def __init__(self,folder,limit_bytes=128*1024*1024):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True)
        self.limit=limit_bytes;self.used=0;self.written=0;self.dropped=0;self.failed=0;self.capped=0;self.error=None;self.dropped_details=deque(maxlen=4096)
        from .important_events import ImportantEvents
        self.important=ImportantEvents(self.folder/'important')
        self.queue=queue.Queue(maxsize=16);self.done=threading.Event();self.saved=set()
        self.thread=threading.Thread(target=self._work,daemon=True);self.thread.start()
    def record(self,png,phase):
        entry={'at':time.time(),'phase':phase,'sha256':hashlib.sha256(png).hexdigest()}
        try: self.queue.put_nowait((entry,png))
        except queue.Full:
            self.dropped+=1;self.dropped_details.append({**entry,'file':None,'reason':'queue_full'})
    def record_important(self,row,before,after):
        entry={'at':time.time(),'phase':'important_event','important':True}
        try:self.queue.put_nowait((entry,(copy.deepcopy(row),before,after)))
        except queue.Full:self.dropped+=1;self.dropped_details.append({**entry,'file':None,'reason':'queue_full'})
    def _work(self):
        try:self._drain()
        except OSError as error:self.failed+=1;self.error=str(error)
    def _drain(self):
        with (self.folder/'frames.jsonl').open('a',encoding='utf-8') as manifest:
            while not self.done.is_set() or not self.queue.empty():
                try: entry,png=self.queue.get(timeout=.1)
                except queue.Empty: continue
                try:
                    if entry.get('important'):
                        self.important.observe(*png);continue
                    name=entry['sha256']+'.png'
                    if name in self.saved: entry['file']=name
                    elif self.used+len(png)<=self.limit:
                        (self.folder/name).write_bytes(png);self.saved.add(name);self.used+=len(png);self.written+=1;entry['file']=name
                    else: self.capped+=1;entry['file']=None;entry['reason']='storage_limit'
                    manifest.write(json.dumps(entry,ensure_ascii=False)+'\n');manifest.flush()
                except (OSError,ValueError) as error:self.failed+=1;self.error=str(error)
                finally: self.queue.task_done()
    def close(self):
        self.done.set();self.thread.join()
        value={key:getattr(self,key) for key in ('written','dropped','failed','capped','used','limit','error')}
        value['dropped_details_lost']=max(0,self.dropped-len(self.dropped_details))
        if self.dropped_details:
            with (self.folder/'frames.jsonl').open('a',encoding='utf-8') as manifest:
                for row in self.dropped_details:manifest.write(json.dumps(row,ensure_ascii=False)+'\n')
        (self.folder/'recording.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
        return value

class RecordedBackend:
    def __init__(self,backend,recorder): self.backend=backend;self.recorder=recorder
    def __getattr__(self,name): return getattr(self.backend,name)
    def record_frame(self,png,phase): self.recorder.record(png,phase)
    def record_important(self,row,before,after):self.recorder.record_important(row,before,after)
