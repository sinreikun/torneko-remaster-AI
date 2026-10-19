"""Optional OCR model selection using the supplied, measured source manifest."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

MANIFEST=Path(__file__).resolve().parent.parent/'knowledge/ocr-components.json'
MODELS=json.loads(MANIFEST.read_text(encoding='utf-8'))['components']

def data_root(): return Path(os.environ.get('GAMEPILOT_DATA_DIR',Path(__file__).resolve().parent.parent/'data'))
def directory(model,root=None): return (Path(root) if root is not None else data_root())/model['install_dir']
def valid_file(path,spec):
    return path.is_file() and path.stat().st_size==spec['size_bytes'] and hashlib.sha256(path.read_bytes()).hexdigest()==spec['sha256']
def installed(model,root=None):
    return importlib.util.find_spec('rapidocr') is not None and importlib.util.find_spec('onnxruntime') is not None and all(valid_file(directory(model,root)/f['path'],f) for f in model['files'])

class HTTPSRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,msg,headers,newurl):
        if not newurl.startswith('https://'):raise ValueError('Insecure redirect')
        return super().redirect_request(request,fp,code,msg,headers,newurl)

def download_file(spec,path,stop,progress):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if valid_file(path,spec): progress('検査済み: '+path.name);return
    part=path.with_suffix(path.suffix+'.part');offset=part.stat().st_size if part.exists() else 0
    if offset>=spec['size_bytes']: part.unlink();offset=0
    url=spec['urls'][0]
    if not url.startswith('https://'): raise ValueError('HTTPS is required')
    headers={'User-Agent':'LocalGamePilot-OCR-Setup'}
    if offset: headers['Range']=f'bytes={offset}-'
    request=urllib.request.Request(url,headers=headers)
    with urllib.request.build_opener(HTTPSRedirects()).open(request,timeout=45) as response:
        if not response.url.startswith('https://'): raise ValueError('Insecure redirect')
        if offset and (response.status!=206 or not response.headers.get('Content-Range','').startswith(f'bytes {offset}-')): offset=0
        with part.open('ab' if offset else 'wb') as output:
            total=offset
            while True:
                if stop.is_set(): raise InterruptedError('OCR部品の取得を中断しました。')
                block=response.read(128*1024)
                if not block: break
                total+=len(block)
                if total>spec['size_bytes']: raise ValueError('OCR component exceeds manifest size')
                output.write(block);progress(f"{path.name}: {total:,} / {spec['size_bytes']:,} bytes")
    if not valid_file(part,spec):
        part.unlink(missing_ok=True);raise ValueError('OCR部品のSHA-256またはサイズが一致しません。')
    part.replace(path)

def install(model_id,stop,progress,root=None):
    root=Path(root) if root is not None else data_root();root.mkdir(parents=True,exist_ok=True)
    model=next(x for x in MODELS if x['id']==model_id)
    progress('RapidOCR / ONNX Runtime CPU を PyPI から導入中…')
    log=root/'ocr-install.log'
    with log.open('a',encoding='utf-8') as output:
        process=subprocess.Popen([sys.executable,'-m','pip','install','--disable-pip-version-check','rapidocr==3.4.0','onnxruntime>=1.23,<2'],stdout=output,stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            while process.poll() is None:
                if stop.wait(.2):
                    process.terminate();process.wait(timeout=15);raise InterruptedError('OCR部品の導入を中断しました。')
            if process.returncode: raise RuntimeError('OCRランタイムの導入に失敗しました。診断ZIPのocr-install.logを確認してください。')
        finally:
            if process.poll() is None: process.kill();process.wait()
    importlib.invalidate_caches()
    for spec in model['files']: download_file(spec,directory(model,root)/spec['path'],stop,progress)
    record=root/'components.json'
    current=json.loads(record.read_text(encoding='utf-8')) if record.exists() else {}
    current[model_id]={'version':model['version'],'license':model['license'],'files':model['files'],'installed_at':time.time()}
    temporary=record.with_suffix('.json.tmp');temporary.write_text(json.dumps(current,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(record)
    progress('OCR部品のサイズ・SHA-256検査が完了しました。')
