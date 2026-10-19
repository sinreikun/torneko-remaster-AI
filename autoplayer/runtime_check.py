"""Validate the installed real application and local runtime; never start a demo game."""
import argparse
import json
import os
from pathlib import Path
import tkinter as tk
import urllib.request


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--endpoint',required=True);parser.add_argument('--model',required=True);parser.add_argument('--output',required=True);parser.add_argument('--verify-ocr-components',action='store_true')
    args=parser.parse_args();output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    os.environ.setdefault('GAMEPILOT_DATA_DIR',str(output.parent/'data'))
    report={'kind':'application_runtime','synthetic_game_used':False,'real_game_test':'required','checks':{},'success':False}
    root=None;app=None
    try:
        from .core import Settings,LocalLLM
        from .ui import Application,DATA
        from .diagnostics import export_diagnostics
        from .ocr_components import MODELS,installed
        cfg=Settings(endpoint=args.endpoint,model=args.model).validate()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request=urllib.request.Request(cfg.endpoint+'/api/show',json.dumps({'model':cfg.model}).encode(),{'Content-Type':'application/json'})
        with opener.open(request,timeout=45) as response:info=json.load(response)
        assert 'vision' in info.get('capabilities',[]),info
        report['checks']['vision_model_installed']=True
        root=tk.Tk();app=Application(root);root.update()
        assert len(app.panels)==13
        assert app.settings().validate()
        assert not app.busy() and app.backend is None
        report['checks']['real_application_ui']=True
        report['checks']['no_game_input_sent']=True
        # Capture only our real application UI, not a simulated game or commercial screen.
        if os.name=='nt':
            from PIL import ImageGrab
            ImageGrab.grab(bbox=(root.winfo_rootx(),root.winfo_rooty(),root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height())).save(output.parent/'application-ui.png')
        archive=output.parent/'runtime-diagnostics.zip'
        export_diagnostics(DATA,archive)
        report['checks']['diagnostic_zip_created']=archive.is_file()
        if os.name=='nt':
            from .clipboard import copy_file
            import ctypes
            copy_file(archive,root.winfo_id())
            report['checks']['clipboard_file_attachment']=bool(ctypes.windll.user32.IsClipboardFormatAvailable(15))
            assert report['checks']['clipboard_file_attachment']
        if args.verify_ocr_components:
            import threading,io
            from PIL import Image,ImageDraw,ImageFont
            from .ocr_components import install
            from .ocr import ScreenOCR
            image=Image.new('RGB',(1000,220),'white')
            font=ImageFont.truetype(str(Path(os.environ['WINDIR'])/'Fonts/arial.ttf'),56)
            ImageDraw.Draw(image).text((30,60),'COMPONENT CHECK 12345',font=font,fill='black')
            stream=io.BytesIO();image.save(stream,format='PNG')
            results={}
            for model in MODELS:
                install(model['id'],threading.Event(),lambda text:print(text,flush=True))
                value=ScreenOCR(model['id']).read(stream.getvalue())
                if value['status']!='ok':raise RuntimeError(json.dumps(value,ensure_ascii=False))
                text=' '.join(line['text'] for line in value['lines']).upper()
                if 'COMPONENT' not in text or '12345' not in text:raise RuntimeError('OCR component text check failed: '+text)
                results[model['id']]=value
            report['checks']['ocr_text_components']=results
        report['checks']['optional_ocr']={model['id']:installed(model) for model in MODELS}
        report['success']=True
    except Exception as error:
        report['error']=str(error);raise
    finally:
        if app:app.close()
        elif root:root.destroy()
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
