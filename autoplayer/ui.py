from __future__ import annotations
import json
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from dataclasses import asdict
import webbrowser
from .core import Settings, LocalLLM, Controller
from .components import CATALOG, MODELS, pull_model, install_capture

DATA=Path(__file__).resolve().parent.parent/'data'

class Application:
    def __init__(self,root):
        self.root=root; root.title('ローカルLLM ゲーム攻略ツール'); root.geometry('980x820')
        self.queue=queue.Queue(); self.stop=threading.Event(); self.worker=None
        self.cfg=Settings()
        if (DATA/'settings.json').exists():
            try: self.cfg=Settings(**json.loads((DATA/'settings.json').read_text(encoding='utf-8'))).validate()
            except Exception as e: messagebox.showwarning('設定',f'設定を読み込めません。既定値で開始します。\n{e}')
        tabs=ttk.Notebook(root); tabs.pack(fill='both',expand=True)
        run=ttk.Frame(tabs,padding=10); setup_tab=ttk.Frame(tabs)
        tabs.add(run,text='攻略・目標・知識'); tabs.add(setup_tab,text='環境・モデルの選択と取得')
        canvas=tk.Canvas(setup_tab,highlightthickness=0)
        scroll=ttk.Scrollbar(setup_tab,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y'); canvas.pack(side='left',fill='both',expand=True)
        setup=ttk.Frame(canvas,padding=10)
        window=canvas.create_window((0,0),window=setup,anchor='nw')
        setup.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(window,width=e.width))
        self.vars={}
        for row,(label,key) in enumerate([('ゲームのタイトル（一部・固有の文字列）','window_title'),('API 形式（ollama / openai）','provider'),('ローカル API URL','endpoint'),('画像対応モデル名','model'),('許可キー（カンマ区切り）','allowed_keys'),('最大手数','max_steps')]):
            ttk.Label(run,text=label).grid(row=row,column=0,sticky='w')
            val=getattr(self.cfg,key); val=','.join(val) if isinstance(val,list) else str(val)
            self.vars[key]=tk.StringVar(value=val)
            if key=='provider': w=ttk.Combobox(run,textvariable=self.vars[key],values=['ollama','openai'],state='readonly')
            else: w=ttk.Entry(run,textvariable=self.vars[key])
            w.grid(row=row,column=1,sticky='ew',pady=3)
        run.columnconfigure(1,weight=1)
        ttk.Label(run,text='攻略目標（具体的な達成条件を含める）').grid(row=6,column=0,columnspan=2,sticky='w')
        self.goal=tk.Text(run,height=3,wrap='word'); self.goal.insert('1.0',self.cfg.goal); self.goal.grid(row=7,column=0,columnspan=2,sticky='ew')
        ttk.Label(run,text='攻略知識・キーの意味・優先事項（テキストで注入。セーブ削除・購入等は実行禁止）').grid(row=8,column=0,columnspan=2,sticky='w')
        self.knowledge=tk.Text(run,height=7,wrap='word'); self.knowledge.insert('1.0',self.cfg.knowledge)
        self.knowledge.grid(row=9,column=0,columnspan=2,sticky='ew')
        ttk.Button(run,text='知識ファイルを読み込む…',command=self.load_knowledge).grid(row=10,column=0,sticky='w')
        controls=ttk.Frame(run); controls.grid(row=11,column=0,columnspan=2,sticky='ew',pady=8)
        self.dry=tk.BooleanVar(value=True)
        ttk.Checkbutton(controls,text='観察のみ（キー送信なし）',variable=self.dry).pack(side='left')
        self.start_button=ttk.Button(controls,text='保存して開始（5秒後）',command=self.start); self.start_button.pack(side='left',padx=8)
        ttk.Button(controls,text='停止 / F8',command=self.stop.set).pack(side='left')
        ttk.Label(run,text='開始後5秒以内にゲームを前面へ。前面が変わると停止します。推論中も F8 で停止予約できます。').grid(row=12,column=0,columnspan=2,sticky='w')
        self.status=tk.StringVar(value='待機中'); ttk.Label(run,textvariable=self.status).grid(row=13,column=0,columnspan=2,sticky='w')
        self.log=tk.Text(run,height=14,wrap='word',state='disabled'); self.log.grid(row=14,column=0,columnspan=2,sticky='nsew'); run.rowconfigure(14,weight=1)
        self.setup(setup)
        root.protocol('WM_DELETE_WINDOW',self.close); root.after(100,self.poll)
    def setup(self,parent):
        ttk.Label(parent,text='本体には外部ランタイムやモデルを同梱しません。選択して公式ページから取得してください。',wraplength=900).pack(anchor='w')
        for item in CATALOG:
            box=ttk.LabelFrame(parent,text=f"{item['name']} / {item['size']} / {item['license']}",padding=5); box.pack(fill='x',pady=3)
            ttk.Label(box,text=item['description'],wraplength=820).pack(anchor='w')
            ttk.Button(box,text='公式ダウンロード・説明',command=lambda url=item['url']:webbrowser.open(url)).pack(anchor='e')
            if item['kind']=='capture': ttk.Button(box,text='この Python に Pillow を導入',command=self.install).pack(anchor='e')
        ttk.Label(parent,text='Ollama モデル（いずれか1つ。Ollama 起動後にダウンロード）').pack(anchor='w',pady=5)
        self.model_choice=tk.StringVar(value=MODELS[1]['name'])
        for item in MODELS:
            line=ttk.Frame(parent); line.pack(fill='x')
            ttk.Radiobutton(line,text=f"{item['name']} — {item['size']} / {item['license']}",variable=self.model_choice,value=item['name']).pack(side='left')
            ttk.Button(line,text='モデル情報・利用条件',command=lambda url=item['url']:webbrowser.open(url)).pack(side='right')
            ttk.Label(parent,text=item['description'],wraplength=850).pack(anchor='w')
        ttk.Button(parent,text='選択モデルをダウンロードして設定',command=self.download).pack(pady=8)
        self.progress=tk.StringVar(); ttk.Label(parent,textvariable=self.progress,wraplength=900).pack(anchor='w')
    def load_knowledge(self):
        p=filedialog.askopenfilename(filetypes=[('Knowledge','*.md *.txt'),('All','*')])
        if p:
            try:
                text=Path(p).read_text(encoding='utf-8')
                if len(text)>50000: raise ValueError('知識は 50,000 文字以内にしてください。')
                self.knowledge.delete('1.0','end'); self.knowledge.insert('1.0',text)
            except Exception as e: messagebox.showerror('読み込みエラー',str(e))
    def settings(self):
        v=asdict(self.cfg)
        for key,var in self.vars.items():
            val=var.get().strip()
            v[key]=[x.strip().lower() for x in val.split(',') if x.strip()] if key=='allowed_keys' else int(val) if key=='max_steps' else val
        v.update(goal=self.goal.get('1.0','end').strip(),knowledge=self.knowledge.get('1.0','end').strip())
        if len(v['knowledge'])>50000: raise ValueError('知識は 50,000 文字以内にしてください。')
        return Settings(**v).validate()
    def busy(self): return self.worker is not None and self.worker.is_alive()
    def launch(self,fn):
        if self.busy(): messagebox.showinfo('処理中','現在の処理が終了するまでお待ちください。'); return
        self.stop.clear(); self.start_button.configure(state='disabled')
        def task():
            try: fn()
            except Exception as e: self.queue.put(('error',str(e)))
            finally: self.queue.put(('done',None))
        self.worker=threading.Thread(target=task,daemon=True); self.worker.start()
    def start(self):
        if self.busy(): return
        try:
            cfg=self.settings(); DATA.mkdir(exist_ok=True)
            (DATA/'settings.json').write_text(json.dumps(asdict(cfg),ensure_ascii=False,indent=2),encoding='utf-8')
            self.cfg=cfg
        except Exception as e: messagebox.showerror('設定エラー',str(e)); return
        dry=self.dry.get()
        def work():
            from .windows import WindowsGame
            backend=WindowsGame(cfg.window_title)
            monitor_done=threading.Event()
            def monitor():
                while not monitor_done.wait(.03):
                    if backend.emergency(): self.stop.set()
            watch=threading.Thread(target=monitor,daemon=True); watch.start()
            try:
                for left in range(5,0,-1):
                    self.queue.put(('status',f'{left} 秒後に開始。ゲームを前面にしてください。'))
                    if self.stop.wait(1): return
                path=DATA/'runs'/f'{time.time_ns()}.jsonl'
                Controller(cfg,backend,LocalLLM(cfg),lambda row:self.queue.put(('decision',row)),path,self.stop).run(dry)
            finally: monitor_done.set(); backend.release_all()
        self.launch(work)
    def install(self):
        if messagebox.askyesno('Pillow の導入','PyPI から Pillow を取得し、現在の Python に導入します。続行しますか？'):
            self.launch(lambda:(install_capture(),self.queue.put(('progress','Pillow の導入が完了しました。'))))
    def download(self):
        name=self.model_choice.get(); endpoint=self.vars['endpoint'].get()
        if not messagebox.askyesno('モデルの取得',f'{name} を Ollama で取得します。数GB以上の空き容量が必要です。モデル情報の利用条件を確認しましたか？'): return
        def work():
            ok=pull_model(endpoint,name,lambda x:self.queue.put(('progress',json.dumps(x,ensure_ascii=False))),self.stop)
            if ok: self.queue.put(('model',name))
        self.launch(work)
    def poll(self):
        try:
            while True:
                kind,value=self.queue.get_nowait()
                if kind=='error': self.status.set('エラーで停止'); messagebox.showerror('処理停止',value)
                elif kind=='done': self.start_button.configure(state='normal'); self.status.set('処理終了（再開時は現在の画面から再判断）')
                elif kind=='status': self.status.set(value)
                elif kind=='progress': self.progress.set(value)
                elif kind=='model': self.vars['model'].set(value); self.vars['provider'].set('ollama'); self.progress.set('モデルの取得が完了しました。')
                elif kind=='decision':
                    self.log.configure(state='normal'); self.log.insert('end',json.dumps(value,ensure_ascii=False)+'\n'); self.log.see('end'); self.log.configure(state='disabled')
        except queue.Empty: pass
        self.root.after(100,self.poll)
    def close(self):
        self.stop.set()
        if self.busy():
            self.status.set('停止中。進行中の API 要求が終了してから閉じます。')
            self.root.after(200,self.close)
        else: self.root.destroy()

def main():
    root=tk.Tk(); Application(root); root.mainloop()
