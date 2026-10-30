"""Original-style OCR test window: actual capture/file, boxes, engine comparison."""
import io
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from .ocr import ScreenOCR

class OCRTestWindow:
    def __init__(self,parent,backend,data):
        self.backend=backend;self.data=Path(data);self.png=None;self.results=[];self.photo=None
        self.reader=ScreenOCR();self.queue=queue.Queue();self.worker=None;self.closed=False
        self.window=tk.Toplevel(parent);self.window.title('OCRテスト・エンジン比較');self.window.geometry('1300x800')
        bar=ttk.Frame(self.window,padding=8);bar.pack(fill='x')
        ttk.Label(bar,text='エンジン').pack(side='left');self.engine=tk.StringVar(value='auto')
        ttk.Combobox(bar,textvariable=self.engine,values=['auto','windows.media.ocr','ocr-paddle-v5','ocr-paddle-ja'],state='readonly',width=21).pack(side='left',padx=8)
        ttk.Button(bar,text='読み取る',command=self.read).pack(side='left')
        ttk.Button(bar,text='全エンジンで比較',command=lambda:self.read(True)).pack(side='left',padx=4)
        ttk.Button(bar,text='今のゲーム画面を取り直す',command=self.recapture).pack(side='left')
        ttk.Button(bar,text='画像ファイルを開く…',command=self.open).pack(side='left',padx=4)
        ttk.Button(bar,text='結果JSONを保存',command=self.save).pack(side='left')
        self.status=tk.StringVar(value='実ゲーム画面または画像ファイルを指定してください。テスト結果は学習に使いません。')
        ttk.Label(self.window,textvariable=self.status,padding=8,wraplength=1200).pack(fill='x')
        split=ttk.Panedwindow(self.window,orient='horizontal');split.pack(fill='both',expand=True)
        self.canvas=tk.Canvas(split,bg='black',highlightthickness=0);self.text=tk.Text(split,width=48,wrap='word',state='disabled')
        split.add(self.canvas,weight=3);split.add(self.text,weight=2)
        self.canvas.bind('<Configure>',lambda event:self.draw())
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.window.after(100,self.poll)
        if backend:self.recapture()
    def launch(self,fn):
        if self.worker and self.worker.is_alive():self.status.set('読み取り中です。');return
        def work():
            try:fn()
            except Exception as error:self.queue.put(('error',str(error)))
        self.worker=threading.Thread(target=work,daemon=True);self.worker.start()
    def recapture(self):
        if not self.backend:self.status.set('本体の「ゲームに接続」で接続してください。');return
        self.launch(lambda:self.queue.put(('image',self.backend.capture())))
    def open(self):
        if self.worker and self.worker.is_alive():return
        name=filedialog.askopenfilename(parent=self.window,filetypes=[('画像','*.png *.jpg *.jpeg *.bmp'),('すべて','*')])
        if not name:return
        try:
            from PIL import Image
            path=Path(name)
            if path.stat().st_size>32*1024*1024:raise ValueError('32MB以下の画像を選択してください。')
            with Image.open(path) as image:
                if image.width*image.height>16_000_000:raise ValueError('画像は1600万画素以下にしてください。')
                output=io.BytesIO();image.convert('RGB').save(output,format='PNG');self.set_image(output.getvalue())
        except Exception as error:messagebox.showerror('画像を開けません',str(error),parent=self.window)
    def set_image(self,png):
        self.png=png;self.results=[];self.text.configure(state='normal');self.text.delete('1.0','end');self.text.configure(state='disabled');self.draw();self.status.set('画像を取得しました。「読み取る」または「全エンジンで比較」を実行できます。')
    def read(self,compare=False):
        if not self.png:self.status.set('画像がありません。');return
        png=self.png;engine=self.engine.get()
        self.status.set('OCRで読み取り中…（結果は補正せず、エンジン別に表示します）')
        def work():
            engines=['ocr-paddle-v5','ocr-paddle-ja','windows.media.ocr'] if compare else [engine]
            results=[]
            for item in engines:
                results.append(self.reader.read(png) if item=='auto' else self.reader.read_engine(png,item))
            self.queue.put(('results',results))
        self.launch(work)
    def draw(self):
        if not self.png:return
        from PIL import Image,ImageTk
        image=Image.open(io.BytesIO(self.png));width,height=image.size
        scale=min(max(1,self.canvas.winfo_width())/width,max(1,self.canvas.winfo_height())/height)
        image=image.resize((max(1,int(width*scale)),max(1,int(height*scale))))
        self.photo=ImageTk.PhotoImage(image);self.canvas.delete('all');self.canvas.create_image(0,0,anchor='nw',image=self.photo)
        colors=['#50ff70','#ffcc30','#55bfff']
        for i,result in enumerate(self.results):
            for line in result.get('lines',[]):
                for word in line.get('words',[]):
                    box=word.get('box',[])
                    if len(box)!=4:continue
                    x,y,w,h=box;self.canvas.create_rectangle(x*scale,y*scale,(x+w)*scale,(y+h)*scale,outline=colors[i%len(colors)],width=2)
    def report(self):return {'kind':'ocr_test','learning':False,'created_at':time.time(),'results':self.results}
    def save(self,automatic=False):
        if not self.results:return
        path=self.data/'ocr-tests'/f'ocr-{time.time_ns()}.json' if automatic else filedialog.asksaveasfilename(parent=self.window,defaultextension='.json',filetypes=[('JSON','*.json')])
        if not path:return
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(self.report(),ensure_ascii=False,indent=2),encoding='utf-8')
        return path
    def poll(self):
        if self.closed:return
        try:
            while True:
                kind,value=self.queue.get_nowait()
                if kind=='error':self.status.set('読み取り失敗: '+value)
                elif kind=='image':self.set_image(value)
                elif kind=='results':
                    self.results=value;self.text.configure(state='normal');self.text.delete('1.0','end');self.text.insert('1.0',json.dumps(self.report(),ensure_ascii=False,indent=2));self.text.configure(state='disabled');self.draw()
                    try:self.save(True);self.status.set('エンジン別の文字・領域を表示し、結果を自動保存しました。未導入・失敗はそのまま表示しています。')
                    except OSError as error:self.status.set('結果の保存失敗: '+str(error))
        except queue.Empty:pass
        self.window.after(100,self.poll)
    def close(self):
        self.closed=True;self.window.destroy()
