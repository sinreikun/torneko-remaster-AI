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
        if digest==self.last_hash and self.last.get('status')=='ok' and not getattr(self,'force_fresh',False): return copy.deepcopy(self.last)
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
        self.readers[engine].recognition_cache_enabled=getattr(self,'fast_mode',False)
        self.readers[engine].force_fresh=getattr(self,'force_fresh',False)
        return self.readers[engine].read(png)
    def read_regions(self,png):
        """Current HUD and message pixels, no resizing or invented OCR text."""
        import io
        from PIL import Image
        from .exterior_screen import white_frames
        # Menus/dialogs can place text anywhere; ambiguous detection is full OCR.
        if white_frames(png).get('visible') is not False:return self.read(png)
        with Image.open(io.BytesIO(png)) as source:
            image=source.convert('RGB');w,h=image.size
            top=round(h*.15);bottom=round(h*.55);gap=16
            mosaic=Image.new('RGB',(w,top+gap+h-bottom))
            mosaic.paste(image.crop((0,0,w,top)),(0,0));mosaic.paste(image.crop((0,bottom,w,h)),(0,top+gap))
            stream=io.BytesIO();mosaic.save(stream,format='PNG',compress_level=1)
        result=self.read(stream.getvalue())
        if result.get('status')!='ok':return self.read(png)
        result=copy.deepcopy(result)
        lines=[]
        for line in result.get('lines',[]):
            words=line.get('words',[])
            if not words:continue
            ys=[word['box'][1] for word in words if len(word.get('box',[]))==4]
            if not ys:continue
            offset=0 if max(word['box'][1]+word['box'][3] for word in words if len(word.get('box',[]))==4)<=top else bottom-top-gap if min(ys)>=top+gap else None
            if offset is None:return self.read(png)
            for word in words:
                if len(word.get('box',[]))==4:word['box'][1]+=offset
            lines.append(line)
        from .knowledge import KnowledgeEngine
        observed=KnowledgeEngine().reconcile({'status':'ok','lines':lines})['metrics']
        if not observed.get('floor') or not observed.get('hp'):return self.read(png)
        result.update(lines=lines,frame_sha256=hashlib.sha256(png).hexdigest(),ocr_scope='hud_and_messages',regions=[[0,0,w,top],[0,bottom,w,h]],full_screen_text_read=False)
        return result

    def read(self,png):
        from .cancellation import call_interruptible
        return call_interruptible(lambda:self._read(png),getattr(self,'cancel',None))
    def _read(self,png):
        digest=hashlib.sha256(png).hexdigest()
        engines=tuple(self.engines() if self.selected=='auto' else [self.selected])
        cache_key=(engines,digest)
        if cache_key in self.cache and not getattr(self,'force_fresh',False):
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


def read_spatial_regions(reader,png,regions):
    """Generic measured regions; restore word coordinates to the original frame."""
    import io,math
    from PIL import Image
    sha=hashlib.sha256(png).hexdigest();lines=[];observed=[];errors=[]
    with Image.open(io.BytesIO(png)) as src:
        image=src.convert('RGB');w,h=image.size
        for region in regions[:4]:
            if not isinstance(region,(list,tuple)) or len(region)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in region):continue
            b=[max(0,min(1,v)) for v in region]
            if b[2]<=b[0] or b[3]<=b[1]:continue
            x,y,right,bottom=[round(v*(w if i%2==0 else h)) for i,v in enumerate(b)]
            if right<=x or bottom<=y:continue
            stream=io.BytesIO();image.crop((x,y,right,bottom)).save(stream,format='PNG',compress_level=1)
            value=reader.read(stream.getvalue())
            if value.get('frame_sha256')!=hashlib.sha256(stream.getvalue()).hexdigest() or value.get('status')!='ok':errors.append(value.get('reason','region_ocr_unavailable'));continue
            observed.append([x,y,right,bottom])
            for line in copy.deepcopy(value.get('lines',[])):
                for word in line.get('words',[]):
                    box=word.get('box')
                    if isinstance(box,list) and len(box)==4:box[0]+=x;box[1]+=y
                line['region_source']=[x,y,right,bottom];line['source_frame']=sha;lines.append(line)
    return {'status':'ok' if observed else 'unavailable','frame_sha256':sha,'captured_at':time.time(),'lines':lines[:300],
            'ocr_scope':'spatial_regions','regions':observed,'errors':errors[:4],'full_screen_text_read':False}
