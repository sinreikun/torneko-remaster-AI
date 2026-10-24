"""Shared run controls for play and diagnostics; no GUI calls from workers."""
import threading
import time

class SessionControl:
    def __init__(self, stop=None, seconds=0):
        if seconds not in (0,600,1200,1800,3600): raise ValueError('Invalid run duration')
        self.stop=stop if stop is not None else threading.Event()
        self.paused=threading.Event();self.intervention=threading.Event();self.revision=0
        self.deadline=time.monotonic()+seconds if seconds else None
        self.reason='';self.timer=None
        if seconds:
            self.timer=threading.Timer(seconds,self.expire);self.timer.daemon=True;self.timer.start()
    def expire(self):
        self.reason='実行時間の上限に到達';self.stop.set()
    def toggle_pause(self):
        self.revision+=1
        if self.paused.is_set(): self.paused.clear()
        else: self.paused.set()
        return self.paused.is_set()
    def checkpoint(self):
        while self.paused.is_set() and not self.intervention.is_set() and not self.stop.wait(.05): pass
        if self.deadline is not None and time.monotonic()>=self.deadline: self.expire()
        return not self.stop.is_set()
    def is_set(self):
        return self.stop.is_set() or self.paused.is_set() or self.intervention.is_set()
    def wait(self,seconds):
        end=time.monotonic()+seconds
        while not self.is_set():
            remaining=end-time.monotonic()
            if remaining<=0: return False
            self.stop.wait(min(.02,remaining))
        return True
    def close(self):
        if self.timer: self.timer.cancel()
