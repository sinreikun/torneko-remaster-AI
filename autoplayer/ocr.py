"""Independent OS OCR. No model-generated text is labelled as OCR evidence."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import copy
from collections import OrderedDict

class WindowsOCR:
    def __init__(self): self.last_hash=None; self.last=None
    def read(self,png):
        digest=hashlib.sha256(png).hexdigest()
        if digest==self.last_hash and self.last.get('status')=='ok': return copy.deepcopy(self.last)
        record={'frame_sha256':digest,'captured_at':time.time(),'status':'unavailable','engine':'windows.media.ocr','lines':[],'confidence':None}
        if not png.startswith(b'\x89PNG\r\n\x1a\n'): record['reason']='OCR requires a PNG capture'
        elif os.name!='nt': record['reason']='Windows OCR is available on Windows only'
        else:
            try:
                with tempfile.TemporaryDirectory(prefix='gamepilot-ocr-') as folder:
                    path=Path(folder)/'frame.png';path.write_bytes(png)
                    # A separate top strip prevents scene textures from changing
                    # the reading orientation/segmentation of the HUD. Same pixels,
                    # no inferred labels or numbers; retain the full-frame OCR too.
                    import io
                    from PIL import Image
                    hud=Path(folder)/'top.png'
                    with Image.open(io.BytesIO(png)) as image:
                        image.crop((0,0,image.width,max(1,round(image.height*.15)))).save(hud)
                    script=Path(__file__).with_name('ocr.ps1')
                    # Paths are arguments, never interpolated PowerShell code.
                    from .cancellation import run_process
                    result=run_process(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script),'-ImagePath',str(path),'-HudImagePath',str(hud)],timeout=25,cancel=getattr(self,'cancel',None),creationflags=subprocess.CREATE_NO_WINDOW)
                    if result.returncode: raise RuntimeError(result.stderr.decode('utf-8','replace')[:700])
                    value=json.loads(result.stdout.decode('utf-8-sig'))
                    lines=value.get('lines',[])
                    if not isinstance(lines,list) or len(lines)>300: raise ValueError('Invalid OCR line count')
                    if any(not isinstance(line,dict) or not isinstance(line.get('text'),str) or len(line['text'])>2000 for line in lines): raise ValueError('Invalid OCR lines')
                    record.update(value,status='ok')
            except (OSError,subprocess.SubprocessError,ValueError,RuntimeError) as error: record['reason']=str(error)[:700]
        self.last_hash=digest;self.last=record
        return dict(record)


class ScreenOCR:
    """Auto chooses installed V5, V4, then Windows; failures stay visible."""
    def __init__(self,engine='auto'):
        if engine not in ('auto','windows.media.ocr','ocr-paddle-v5','ocr-paddle-ja'): raise ValueError('Unknown OCR engine')
        self.selected=engine;self.readers={};self.last_hash=None;self.last=None;self.cache=OrderedDict()
    def engines(self):
        from .ocr_components import MODELS,installed
        return [model['id'] for model in MODELS if installed(model)]+['windows.media.ocr']
    def read_engine(self,png,engine):
        if engine not in self.readers:
            if engine=='windows.media.ocr': self.readers[engine]=WindowsOCR()
            else:
                from .paddle_ocr import PaddleOCR
                self.readers[engine]=PaddleOCR(engine)
        self.readers[engine].cancel=getattr(self,'cancel',None)
        return self.readers[engine].read(png)
    def read(self,png):
        from .cancellation import call_interruptible
        return call_interruptible(lambda:self._read(png),getattr(self,'cancel',None))
    def _read(self,png):
        digest=hashlib.sha256(png).hexdigest()
        engines=tuple(self.engines() if self.selected=='auto' else [self.selected])
        cache_key=(engines,digest)
        if cache_key in self.cache:
            self.cache.move_to_end(cache_key)
            return {**copy.deepcopy(self.cache[cache_key]),'cache_hit':True}
        failures=[]
        for engine in engines:
            value=self.read_engine(png,engine)
            if value['status']=='ok': break
            failures.append({'engine':engine,'reason':value.get('reason','unavailable')})
        if failures: value={**value,'fallback_failures':failures}
        self.last_hash=digest;self.last=value
        if value['status']=='ok':
            self.cache[cache_key]=copy.deepcopy(value)
            while len(self.cache)>16:self.cache.popitem(last=False)
        return copy.deepcopy(value)
