"""Presentation-only backlog bounds. Execution journals are never routed here."""
from collections import deque
import threading,queue

class UIEventQueue:
    def __init__(self,limit=96):
        self.limit=limit;self.events=deque();self.lock=threading.Lock();self.dropped=0;self.coalesced=0;self.high_water=0
    @staticmethod
    def replaceable(item):
        kind,value=item
        return kind in ('status','progress') or kind=='decision' and isinstance(value,dict)
    @staticmethod
    def key(item):
        kind,value=item
        if kind in ('status','progress'):return kind
        if kind=='decision' and isinstance(value,dict) and value.get('event') in ('llm_activity','perception'):return (kind,value['event'])
        return None
    def put(self,item):
        with self.lock:
            key=self.key(item)
            if key is not None:
                for i,previous in enumerate(self.events):
                    if self.key(previous)==key:
                        del self.events[i];self.coalesced+=1;break
            if len(self.events)>=self.limit:
                for i,previous in enumerate(self.events):
                    if self.replaceable(previous):del self.events[i];self.dropped+=1;break
                else:
                    if self.replaceable(item):self.dropped+=1;return
            self.events.append(item);self.high_water=max(self.high_water,len(self.events))
    def get_nowait(self):
        with self.lock:
            if not self.events:raise queue.Empty
            return self.events.popleft()
    def qsize(self):
        with self.lock:return len(self.events)
    def snapshot(self):
        with self.lock:return {'pending':len(self.events),'high_water':self.high_water,'dropped_presentation_only':self.dropped,'coalesced':self.coalesced,'limit':self.limit}
