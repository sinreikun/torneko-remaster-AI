"""Versioned operator strategy notes shared by Tk and the worker thread."""
import threading

class OperatorGuidance:
    def __init__(self,text=''):
        self.lock=threading.Lock();self.text=text[:2000] if isinstance(text,str) else '';self.revision=0
    def set(self,text):
        if not isinstance(text,str) or len(text)>2000:raise ValueError('作戦指示は2000文字以内で入力してください。')
        with self.lock:
            text=text.strip()
            if text!=self.text:self.text=text;self.revision+=1
            return self.revision
    def snapshot(self):
        with self.lock:return {'revision':self.revision,'text':self.text}
