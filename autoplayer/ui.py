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
import os
from .core import Settings, LocalLLM, Controller
from .memory import MemoryStore
from .components import CATALOG, MODELS, pull_model, install_capture

DATA=Path(os.environ.get('GAMEPILOT_DATA_DIR',Path(__file__).resolve().parent.parent/'data'))

class Application:
    def __init__(self,root):
        self.root=root;self.configure_theme();root.title('ローカルLLM ゲーム攻略ツール — 統合コンソール');root.geometry('1500x920');root.minsize(1000,640)
        self.queue=queue.Queue();self.stop=threading.Event();self.worker=None
        self.backend=None;self.connection_title=None;self.control=None;self.preview_done=threading.Event()
        self.frame_slot=None;self.shown_frame=None;self.latest_zip=None;self.probe=None;self.probe_keys=0
        self.run_started=None;self.run_counts={'decisions':0,'sent':0,'changed':0,'retries':0};self.last_row={}
        self.cfg=Settings();self.ui_state={}
        if (DATA/'settings.json').exists():
            try: self.cfg=Settings(**json.loads((DATA/'settings.json').read_text(encoding='utf-8'))).validate()
            except Exception as error: messagebox.showwarning('設定',f'設定を読み込めません。既定値で開始します。\n{error}')
        if (DATA/'ui-settings.json').exists():
            try: self.ui_state=json.loads((DATA/'ui-settings.json').read_text(encoding='utf-8'))
            except (OSError,ValueError): pass
        if not self.cfg.knowledge:
            default=Path(__file__).resolve().parent.parent/'knowledge/torneko.md'
            if default.exists(): self.cfg.knowledge=default.read_text(encoding='utf-8')
        from .placement import WindowPlacement
        self.placement=WindowPlacement(root,self.ui_state)
        header=ttk.Frame(root,padding=8);header.pack(fill='x')
        ttk.Label(header,text='ゲーム攻略くん + ローカルLLM',font=('',16,'bold')).pack(side='left')
        self.badges=tk.StringVar(value='未接続 / WindowMessage・背面対応 / 診断と学習を分離')
        ttk.Label(header,textvariable=self.badges).pack(side='left',padx=16)
        toolbar=ttk.Frame(root,padding=(8,0));toolbar.pack(fill='x')
        ttk.Button(toolbar,text='ゲームに接続',command=self.connect).pack(side='left')
        self.start_button=ttk.Button(toolbar,text='▶ 攻略開始',command=self.start);self.start_button.pack(side='left',padx=4)
        self.pause_button=ttk.Button(toolbar,text='⏸ 一時停止',command=self.pause);self.pause_button.pack(side='left')
        ttk.Button(toolbar,text='■ 停止',command=self.stop.set).pack(side='left',padx=4)
        ttk.Button(toolbar,text='緊急停止 (F8 / Ctrl+Shift+F9)',command=self.emergency).pack(side='left')
        tools_bar=ttk.Frame(root,padding=(8,4));tools_bar.pack(fill='x')
        ttk.Button(tools_bar,text='OCRテスト',command=self.open_ocr).pack(side='left',padx=4)
        ttk.Button(tools_bar,text='セットアップ',command=lambda:self.tabs.select(self.setup_tab)).pack(side='left')
        ttk.Button(tools_bar,text='軽量診断ZIP',command=self.export).pack(side='left',padx=4)
        ttk.Button(tools_bar,text='詳細ZIP',command=lambda:self.export(True)).pack(side='left',padx=4)
        ttk.Button(tools_bar,text='最新ZIPを開く',command=self.open_report).pack(side='left')
        ttk.Button(tools_bar,text='提出用ZIPをコピー',command=self.copy_report).pack(side='left',padx=4)
        options=ttk.Frame(root,padding=8);options.pack(fill='x')
        ttk.Label(options,text='実行時間').pack(side='left')
        self.duration=tk.StringVar(value=self.ui_state.get('duration','なし'))
        self.duration_box=ttk.Combobox(options,textvariable=self.duration,values=['なし','10分','20分','30分','60分'],state='readonly',width=6)
        self.duration_box.pack(side='left',padx=6)
        self.separation=tk.BooleanVar(value=False);self.compat=tk.BooleanVar(value=self.ui_state.get('compat',True))
        ttk.Checkbutton(options,text='分離監視（無入力・10分）',variable=self.separation).pack(side='left')
        ttk.Checkbutton(options,text='開始時にOS/Backend経路を確認',variable=self.compat).pack(side='left',padx=6)
        self.dry=tk.BooleanVar(value=False);ttk.Checkbutton(options,text='観察のみ（キー送信なし）',variable=self.dry).pack(side='left')
        ttk.Label(options,text='背面のまま動作 / 最小化不可 / 診断は学習に使用しません').pack(side='right')
        self.status=tk.StringVar(value='待機中 — ゲームに接続するとライブ画面を表示します。')
        ttk.Label(root,textvariable=self.status,padding=(10,4),wraplength=1400).pack(fill='x')
        self.tabs=ttk.Notebook(root);self.tabs.pack(fill='both',expand=True,padx=8,pady=5)
        dashboard=ttk.Frame(self.tabs);config_tab=ttk.Frame(self.tabs);config=self.scroll_frame(config_tab);self.setup_tab=ttk.Frame(self.tabs)
        self.tabs.add(dashboard,text='攻略・観測・検証');self.tabs.add(config_tab,text='目標・攻略知識・接続設定');self.tabs.add(self.setup_tab,text='セットアップ')
        split=ttk.Panedwindow(dashboard,orient='horizontal');split.pack(fill='both',expand=True)
        left=ttk.Frame(split);right=ttk.Frame(split);split.add(left,weight=3);split.add(right,weight=2)
        self.live=tk.Canvas(left,bg='black',highlightthickness=0,height=390);self.live.pack(fill='both',expand=True)
        self.live.create_text(280,180,text='ゲームに接続していません',fill='white',tags='image')
        self.live.bind('<Configure>',lambda event:self.draw_frame(force=True))
        self.live_info=tk.StringVar(value='画面の記録は実機実行時のみ。架空ゲームは使用しません。')
        ttk.Label(left,textvariable=self.live_info).pack(fill='x')
        ttk.Label(left,text='観測 → 行動 → 結果',font=('',11,'bold')).pack(anchor='w',pady=(6,2))
        logbox=ttk.Frame(left);logbox.pack(fill='both',expand=True)
        self.log=tk.Text(logbox,height=12,wrap='word',state='disabled');self.log.pack(side='left',fill='both',expand=True)
        logscroll=ttk.Scrollbar(logbox,command=self.log.yview);logscroll.pack(side='right',fill='y');self.log.configure(yscrollcommand=logscroll.set)
        panel_host=self.scroll_frame(right);self.panels={};self.panel_bodies={}
        for title in ['バックエンド状態','現在状態','目的','候補と評価','選択行動と理由','結果','仮説','新発見','修正履歴','今回統計','累積統計','系列のパターン','OCR・攻略知識の照合','行動学習','自己改善','キー配置・ミニマップ']:
            group=ttk.Frame(panel_host);group.pack(fill='x',pady=3)
            button=ttk.Button(group,text=title,command=lambda name=title:self.toggle_panel(name));button.pack(fill='x')
            body=tk.Text(group,height=4,wrap='word',state='disabled',relief='flat');body.pack(fill='x')
            self.panels[title]=body;self.panel_bodies[title]=body
            if self.ui_state.get('collapsed',{}).get(title): body.pack_forget()
        self.vars={}
        for row,(label,key) in enumerate([('ゲームのタイトル（一部・固有の文字列）','window_title'),('API形式（ollama / openai）','provider'),('ローカルAPI URL','endpoint'),('画像対応モデル名','model'),('許可キー（カンマ区切り）','allowed_keys'),('手数上限（0 = 上限なし）','max_steps'),('OCR（auto = 導入済みV5 → V4 → Windows）','ocr_engine')]):
            ttk.Label(config,text=label).grid(row=row,column=0,sticky='w')
            value=getattr(self.cfg,key);value=','.join(value) if isinstance(value,list) else str(value)
            self.vars[key]=tk.StringVar(value=value)
            if key=='provider':widget=ttk.Combobox(config,textvariable=self.vars[key],values=['ollama','openai'],state='readonly')
            elif key=='ocr_engine':widget=ttk.Combobox(config,textvariable=self.vars[key],values=['auto','windows.media.ocr','ocr-paddle-v5','ocr-paddle-ja'],state='readonly')
            elif key=='window_title':
                self.window_choice=ttk.Combobox(config,textvariable=self.vars[key],postcommand=self.refresh_windows);widget=self.window_choice
            else:widget=ttk.Entry(config,textvariable=self.vars[key])
            widget.grid(row=row,column=1,sticky='ew',pady=3)
        config.columnconfigure(1,weight=1)
        ttk.Label(config,text='最終目標（小目標・成功条件はLLMが観測から設定）').grid(row=7,column=0,columnspan=2,sticky='w',pady=(8,0))
        self.goal=tk.Text(config,height=4,wrap='word');self.goal.insert('1.0',self.cfg.goal);self.goal.grid(row=8,column=0,columnspan=2,sticky='ew')
        ttk.Label(config,text='攻略知識・キーの意味・優先事項（編集は任意）').grid(row=9,column=0,columnspan=2,sticky='w',pady=(8,0))
        self.knowledge=tk.Text(config,height=10,wrap='word');self.knowledge.insert('1.0',self.cfg.knowledge);self.knowledge.grid(row=10,column=0,columnspan=2,sticky='nsew');config.rowconfigure(10,weight=1)
        ttk.Button(config,text='知識ファイルを読み込む…',command=self.load_knowledge).grid(row=11,column=0,sticky='w',pady=8)
        ttk.Button(config,text='保存した記憶・進捗を表示',command=self.show_memory).grid(row=11,column=1,sticky='w')
        setup=self.scroll_frame(self.setup_tab);self.setup(setup);self.setup_ocr(setup)
        self.set_panel('バックエンド状態','ゲーム未接続\n取得: PrintWindow / 入力: WindowMessage（背面分離）\nLLM: '+self.cfg.model+'\nDB: '+str(DATA))
        self.set_panel('目的',self.cfg.goal+'\n小目標: 未設定')
        from .controls import REFERENCE
        self.set_panel('キー配置・ミニマップ',REFERENCE)
        self.set_panel('自己改善','停滞時にLLMが仮説を提案し、最大4入力・8判断で検証します。効果不明・悪化なら撤回。採用も24判断限定です。')
        self.set_panel('行動学習','実際に送信した単独キーと前後の地図・HUD観測から、状態別の行動価値を保存・更新します。LLMの重みの再訓練ではありません。')
        self.set_panel('候補と評価','LLMの信頼度・危険度・許可キーを表示します。\n未生成の候補点数は表示しません。')
        self.set_panel('系列のパターン','保存済み経験から状態・操作の反復を集計します。診断は含めません。')
        reports=sorted((DATA/'exports').glob('*.zip'),key=lambda p:p.stat().st_mtime,reverse=True)
        if reports:self.latest_zip=reports[0]
        self.preview=threading.Thread(target=self.preview_loop,daemon=True);self.preview.start()
        root.protocol('WM_DELETE_WINDOW',self.close);root.after(100,self.poll)

    def configure_theme(self):
        from tkinter import font
        families=set(font.families(self.root))
        family=next((name for name in ('Yu Gothic UI','Meiryo UI','Noto Sans CJK JP','Segoe UI') if name in families),'TkDefaultFont')
        if family!='TkDefaultFont':
            for name in ('TkDefaultFont','TkTextFont','TkMenuFont','TkHeadingFont'):
                font.nametofont(name).configure(family=family,size=10)
        background='#171a21';well='#232832';foreground='#e8e8ec'
        self.root.configure(background=background)
        self.root.option_add('*Text.background',well);self.root.option_add('*Text.foreground',foreground)
        self.root.option_add('*Text.insertBackground',foreground);self.root.option_add('*Text.selectBackground','#285887')
        self.root.option_add('*Text.font','TkTextFont')
        style=ttk.Style(self.root);style.theme_use('clam')
        style.configure('.',background=background,foreground=foreground)
        style.configure('TButton',padding=(8,5),background='#303848',foreground=foreground)
        style.map('TButton',background=[('active','#3d5270'),('disabled','#252a33')],foreground=[('disabled','#858b97')])
        style.configure('TEntry',fieldbackground=well,foreground=foreground,insertcolor=foreground)
        style.configure('TCombobox',fieldbackground=well,foreground=foreground,arrowcolor=foreground)
        style.map('TCombobox',fieldbackground=[('readonly',well)],foreground=[('readonly',foreground)])
        style.configure('TNotebook.Tab',padding=(12,6),background='#252a33',foreground=foreground)
        style.map('TNotebook.Tab',background=[('selected','#3d5270')])
        style.configure('TLabelframe',bordercolor='#3b4251');style.configure('TLabelframe.Label',foreground=foreground)

    def scroll_frame(self,parent):
        canvas=tk.Canvas(parent,highlightthickness=0,bg='#171a21');scroll=ttk.Scrollbar(parent,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        frame=ttk.Frame(canvas,padding=6);window=canvas.create_window((0,0),window=frame,anchor='nw')
        frame.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda event:canvas.itemconfigure(window,width=event.width))
        return frame

    def set_panel(self,title,value):
        body=self.panels[title];body.configure(state='normal');body.delete('1.0','end')
        body.insert('1.0',value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2));body.configure(state='disabled')

    def save_ui(self):
        self.placement.track()
        self.ui_state.update(duration=self.duration.get(),compat=self.compat.get())
        DATA.mkdir(parents=True,exist_ok=True);temporary=DATA/'ui-settings.json.tmp'
        temporary.write_text(json.dumps(self.ui_state,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(DATA/'ui-settings.json')

    def toggle_panel(self,title):
        body=self.panel_bodies[title];collapsed=bool(body.winfo_manager())
        if collapsed:body.pack_forget()
        else:body.pack(fill='x')
        self.ui_state.setdefault('collapsed',{})[title]=collapsed
        try:self.save_ui()
        except OSError as error:self.status.set('表示設定の保存に失敗: '+str(error))

    def preview_loop(self):
        while not self.preview_done.wait(.4):
            backend=self.backend
            if backend is None:continue
            try:self.frame_slot=(backend.capture(),time.time())
            except Exception as error:self.frame_slot=(None,str(error))

    def draw_frame(self,force=False):
        frame=self.frame_slot
        if not frame or (frame is self.shown_frame and not force):return
        self.shown_frame=frame;self.live.delete('image')
        if frame[0] is None:
            self.live.create_text(20,20,anchor='nw',text=frame[1],fill='white',tags='image');return
        from PIL import Image,ImageTk
        import io
        image=Image.open(io.BytesIO(frame[0]));width,height=image.size
        image.thumbnail((max(1,self.live.winfo_width()),max(1,self.live.winfo_height())))
        self.live_photo=ImageTk.PhotoImage(image)
        self.live.create_image(self.live.winfo_width()/2,self.live.winfo_height()/2,image=self.live_photo,tags='image')
        self.live_info.set(f'{width}×{height} / 最終取得 {time.strftime("%H:%M:%S",time.localtime(frame[1]))} / 背面キャプチャ')

    def connect(self):
        if self.busy():return
        title=self.vars['window_title'].get().strip()
        def work():
            from .windows import WindowsGame
            backend=WindowsGame(title);backend.capture();self.queue.put(('connected',(backend,title)))
        self.launch(work)

    def pause(self):
        if self.control and self.busy():
            paused=self.control.toggle_pause();self.pause_button.configure(text='▶ 再開' if paused else '⏸ 一時停止')
            self.status.set('一時停止中（送信予定の推論は破棄し、再開時に再観測）' if paused else '再開しました')

    def emergency(self):
        self.stop.set()
        self.status.set('緊急停止を要求しました。')

    def open_ocr(self):
        from .ocr_ui import OCRTestWindow
        OCRTestWindow(self.root,self.backend,DATA)

    def setup_ocr(self,parent):
        from .ocr_components import MODELS
        box=ttk.LabelFrame(parent,text='元ソース互換のOCR追加部品（任意）',padding=8);box.pack(fill='x',pady=10)
        ttk.Label(box,text='Windows内蔵OCRは追加DL不要。V5/V4を選ぶと、UIがRapidOCR 3.4.0・ONNX Runtime CPUとモデルを取得します。\nランタイム約100〜200MB（環境依存）、モデルは下記容量。モデルは元ソースの実測SHA-256と照合します。',wraplength=1000).pack(anchor='w')
        self.ocr_choice=tk.StringVar(value='ocr-paddle-v5')
        for model in MODELS:
            amount=sum(f['size_bytes'] for f in model['files'])/1024/1024
            ttk.Radiobutton(box,text=f"{model['display_name']} / {amount:.1f} MiB / Apache-2.0",variable=self.ocr_choice,value=model['id']).pack(anchor='w')
        ttk.Label(box,text='RapidOCR: Apache-2.0 / ONNX Runtime: MIT / 推論補助ライブラリは各パッケージの利用条件',wraplength=1000).pack(anchor='w')
        self.ocr_license=tk.BooleanVar(value=False)
        ttk.Checkbutton(box,text='選択部品の利用条件を確認し、導入する',variable=self.ocr_license).pack(anchor='w')
        buttons=ttk.Frame(box);buttons.pack(fill='x')
        ttk.Button(buttons,text='ライセンス・取得元',command=lambda:webbrowser.open('https://github.com/RapidAI/RapidOCR')).pack(side='left')
        ttk.Button(buttons,text='選択OCRを自動導入',command=self.install_ocr).pack(side='left',padx=8)
        ttk.Button(buttons,text='導入済みモデルの完全検査',command=self.verify_ocr).pack(side='left')

    def install_ocr(self):
        if not self.ocr_license.get():self.status.set('OCR部品の利用条件を確認してチェックしてください。');return
        model=self.ocr_choice.get()
        def work():
            from .ocr_components import install
            install(model,self.stop,lambda value:self.queue.put(('progress',value)),DATA)
        self.launch(work)

    def verify_ocr(self):
        def work():
            from .ocr_components import MODELS,installed
            result={model['id']:('検査済み・利用可' if installed(model,DATA) else '未導入または検査不合格') for model in MODELS}
            self.queue.put(('progress',json.dumps(result,ensure_ascii=False)))
        self.launch(work)

    def export(self,detailed=False):
        if self.busy():self.status.set('実行終了時に自動生成します。停止後に最新ZIPを開けます。');return
        def work():
            from .diagnostics import export_diagnostics
            path=DATA/'exports'/f"{'detailed' if detailed else 'diagnostics'}-{time.time_ns()}.zip";export_diagnostics(DATA,path,detailed);self.queue.put(('exported',path))
        self.launch(work)

    def open_report(self):
        if not self.latest_zip or not self.latest_zip.exists():self.status.set('実行終了後にZIPを自動生成します。手動出力も可能です。');return
        if os.name=='nt':
            import subprocess
            subprocess.Popen(['explorer.exe','/select,',str(self.latest_zip)])
        else:self.status.set(str(self.latest_zip))

    def copy_report(self):
        if not self.latest_zip or not self.latest_zip.exists():self.status.set('提出用ZIPがまだありません。');return
        try:
            if os.name=='nt':
                from .clipboard import copy_file
                copy_file(self.latest_zip,self.root.winfo_id())
            else:self.root.clipboard_clear();self.root.clipboard_append(str(self.latest_zip))
            self.status.set('ZIPをコピーしました。提出先の添付欄・ファイル欄に貼り付けてください。自動送信はしません。')
        except Exception as error:messagebox.showerror('コピー失敗',str(error))

    def show_memory(self):
        try:
            memory=MemoryStore(DATA/'memory.sqlite',self.settings())
            try: value=memory.context()
            finally: memory.close()
            window=tk.Toplevel(self.root);window.title('記憶・進捗（モデルの報告は未確定の仮説）')
            text=tk.Text(window,width=100,height=30,wrap='word');text.pack(fill='both',expand=True)
            text.insert('1.0',json.dumps(value,ensure_ascii=False,indent=2));text.configure(state='disabled')
        except Exception as error: messagebox.showerror('記憶を開けません',str(error))
    def refresh_windows(self):
        if os.name=='nt':
            from .windows import visible_windows
            self.window_choice.configure(values=visible_windows())
    def setup(self,parent):
        ttk.Label(parent,text='本体には外部ランタイムやモデルを同梱しません。起動用exeが必要な部品の取得と導入を自動で進めます。',wraplength=900).pack(anchor='w')
        timing=ttk.Frame(parent);timing.pack(fill='x',pady=6)
        ttk.Label(timing,text='1回の推論の待機上限（秒・1〜600。知識付きCPU推論は数分かかります）').pack(side='left')
        self.vars['timeout']=tk.StringVar(value=str(self.cfg.timeout))
        ttk.Entry(timing,textvariable=self.vars['timeout'],width=7).pack(side='left',padx=8)
        managed = bool(os.environ.get('GAMEPILOT_DATA_DIR'))
        if managed:
            ttk.Label(parent,text='Python・画面取得部品・Ollama は起動用exeが自動で導入・検証します。\nランタイムを変更する場合は本体を閉じ、同じexeを再実行してください。',wraplength=850).pack(anchor='w',pady=10)
        for item in ([] if managed else CATALOG):
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
            v[key]=[x.strip().lower() for x in val.split(',') if x.strip()] if key=='allowed_keys' else int(val) if key=='max_steps' else float(val) if key=='timeout' else val
        v.update(goal=self.goal.get('1.0','end').strip(),knowledge=self.knowledge.get('1.0','end').strip())
        if len(v['knowledge'])>50000: raise ValueError('知識は 50,000 文字以内にしてください。')
        return Settings(**v).validate()
    def busy(self): return self.worker is not None and self.worker.is_alive()
    def launch(self,fn):
        if self.busy(): messagebox.showinfo('処理中','現在の処理が終了するまでお待ちください。'); return
        self.stop.clear(); self.start_button.configure(state='disabled')
        def task():
            try: fn()
            except Exception as e:
                self.queue.put(('error',str(e)))
                try:
                    DATA.mkdir(parents=True,exist_ok=True)
                    with (DATA/'application.log').open('a',encoding='utf-8') as log:
                        import traceback
                        log.write(time.strftime('%Y-%m-%d %H:%M:%S')+'\n'+traceback.format_exc()+'\n')
                    from .diagnostics import export_diagnostics
                    archive=DATA/'exports'/f'error-{time.time_ns()}.zip'
                    export_diagnostics(DATA,archive);self.queue.put(('exported',archive))
                except Exception as export_error:self.queue.put(('export_error',str(export_error)))
            finally: self.queue.put(('done',None))
        self.worker=threading.Thread(target=task,daemon=True); self.worker.start()
    def start(self):
        if self.busy(): return
        try:
            cfg=self.settings();DATA.mkdir(parents=True,exist_ok=True)
            temporary=DATA/'settings.json.tmp';temporary.write_text(json.dumps(asdict(cfg),ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(DATA/'settings.json')
            self.cfg=cfg;self.save_ui()
            seconds={'なし':0,'10分':600,'20分':1200,'30分':1800,'60分':3600}[self.duration.get()]
        except Exception as error:messagebox.showerror('設定エラー',str(error));return
        dry=self.dry.get();separation=self.separation.get();compat=self.compat.get() and not dry
        # Separation now uses only neutral OS probes and observation.
        # State-independent diagnostics never steal focus or send gameplay keys.
        from .session import SessionControl
        self.stop.clear();self.control=SessionControl(self.stop,0 if separation else seconds)
        control=self.control;self.run_started=time.time();self.run_counts={'decisions':0,'sent':0,'changed':0,'retries':0}
        def work():
            from .windows import WindowsGame
            from .recording import FrameRecorder,RecordedBackend
            from .diagnostics import DiagnosticStore,InputDiagnostics,export_diagnostics
            from . import __version__
            session=str(time.time_ns());summary={'app_version':__version__,'id':session,'started':time.time(),'status':'running','kind':'separation' if separation else 'play','dry_run':dry,'reason':'','model':cfg.model,'goal':cfg.goal,'ocr_engine':cfg.ocr_engine}
            recorder=None;backend=None;monitor_done=threading.Event();memory=None;diagnostic_store=None;exploration_run=None
            try:
                backend=self.backend if self.backend and self.connection_title==cfg.window_title else WindowsGame(cfg.window_title)
                backend.capture();self.queue.put(('connected',(backend,cfg.window_title)))
                summary['connection']={'hwnd':int(backend.hwnd),'process_id':backend.process_id.value,
                    'window_title':backend._text(backend.hwnd),'capture':'PrintWindow','input':'window_message'}
                def monitor():
                    while not monitor_done.wait(.03):
                        if backend.emergency():self.stop.set()
                threading.Thread(target=monitor,daemon=True).start()
                recorder=FrameRecorder(DATA/'recordings'/session);recorded=RecordedBackend(backend,recorder)
                recorded.record_frame(backend.capture(),'session_start')
                if separation or compat:
                    store=DiagnosticStore(DATA/'diagnostics.sqlite')
                    try:
                        runner=InputDiagnostics(recorded,store,lambda row:self.queue.put(('decision',row)),control)
                        passed=runner.run(separation)
                    finally:store.close();self.queue.put(('close_probe',None))
                    if not passed:summary.update(status='stopped',reason='診断を中断しました。');return
                    if separation:summary.update(status='diagnostic_completed',reason='10分間のOS経路・無入力観測を完了。ゲーム反応・キー分離は未検証。');return
                if not control.checkpoint():summary.update(status='stopped',reason=control.reason or '停止要求');return
                memory=MemoryStore(DATA/'memory.sqlite',cfg)
                diagnostic_store=DiagnosticStore(DATA/'diagnostics.sqlite');exploration_run=diagnostic_store.start('exploration_statistics')
                agent=Controller(cfg,recorded,LocalLLM(cfg),lambda row:self.queue.put(('decision',row)),DATA/'runs'/(session+'.jsonl'),self.stop,memory,control,lambda row:diagnostic_store.cycle(exploration_run,row))
                self.queue.put(('status','ローカルLLMが実ゲーム画面を判断しています。初回推論は数分かかる場合があります。'))
                agent.run(dry)
                summary.update(status='verified_complete' if agent.completed else 'stopped',reason='別観測で目標達成を確認' if agent.completed else control.reason or ('停止要求' if self.stop.is_set() else 'ループ終了・手数上限または安全条件'))
                self.queue.put(('memory',memory.context()))
                cumulative=memory.db.execute('SELECT COUNT(*) FROM experiences WHERE profile=?',(memory.profile,)).fetchone()[0]
                self.queue.put(('cumulative',cumulative))
            except Exception as error:
                summary.update(status='error',reason=str(error));self.queue.put(('error',str(error)))
                DATA.mkdir(parents=True,exist_ok=True)
                with (DATA/'application.log').open('a',encoding='utf-8') as log:
                    import traceback
                    log.write(time.strftime('%Y-%m-%d %H:%M:%S')+'\n'+traceback.format_exc()+'\n')
            finally:
                monitor_done.set();control.close()
                if diagnostic_store:
                    diagnostic_store.finish(exploration_run,summary['status'],summary.get('reason',''));diagnostic_store.close()
                if memory:memory.close()
                try:
                    if backend:
                        backend.release_all()
                        if recorder:
                            try:recorder.record(backend.capture(),'session_end')
                            except Exception as error:summary['final_capture_error']=str(error)
                except Exception as error:summary['release_error']=str(error)
                if recorder:summary['recording']=recorder.close()
                summary['ended']=time.time()
                folder=DATA/'sessions';folder.mkdir(parents=True,exist_ok=True)
                (folder/(session+'.json')).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
                self.queue.put(('session',summary));self.queue.put(('close_probe',None))
                try:
                    archive=DATA/'exports'/('diagnostics-'+session+'.zip');export_diagnostics(DATA,archive);self.queue.put(('exported',archive))
                except Exception as error:self.queue.put(('export_error',str(error)))
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
    def append_log(self,value):
        self.log.configure(state='normal');self.log.insert('end',json.dumps(value,ensure_ascii=False)+'\n')
        if int(self.log.index('end-1c').split('.')[0])>2500:self.log.delete('1.0','500.0')
        self.log.see('end');self.log.configure(state='disabled')

    def update_decision(self,value):
        event=value.get('event');self.append_log(value)
        if event in ('diagnostic','diagnostic_summary','diagnostic_skipped'):self.set_panel('結果',value);return
        if event=='self_improvement':self.set_panel('自己改善',value);return
        if event=='result':
            if value.get('policy_choice'):
                self.set_panel('自己改善',{'state':value.get('self_improvement'),'evaluation':value.get('improvement_evaluation')})
                self.set_panel('行動学習',{'selection':value.get('policy_choice'),'update':value.get('policy_learning')})
                self.set_panel('候補と評価',value['policy_choice'].get('context',{}))
            self.run_counts['sent']+=int(value.get('executed',False));self.run_counts['changed']+=int(bool(value.get('changed')))
            self.set_panel('結果',{key:value.get(key) for key in ('step','action_kind','observed_keys','input_receipt','neutral_before','executed','changed','transition_attribution','turn_kind','duration_ms','raw_change','masked_activity')})
            return
        if value.get('status')=='retry':self.run_counts['retries']+=1
        if 'scene' not in value:
            self.set_panel('現在状態',value);return
        self.run_counts['decisions']+=1;self.last_row=value
        self.status.set(f"{value.get('scene','')} / {value.get('subgoal','')}")
        self.set_panel('現在状態',{'独立した画面観察（モデル報告・未検証）':value.get('scene_observation',{}),'モデルの計画時の場面判断':(value.get('llm_proposal') or {}).get('scene',value.get('scene','')),'採用した場面仮説':value.get('scene',''),'採用根拠':value.get('scene_basis','model_report'),'採用した観察':value.get('observation','')})
        self.set_panel('目的',{'overall_goal':self.cfg.goal,'autonomous_goal':value.get('autonomous_goal',{'objective':value.get('subgoal'),'success_condition':value.get('goal_success')}),'progress':value.get('progress',[])})
        self.set_panel('候補と評価',{'confidence':value.get('confidence'),'risk':value.get('risk'),'allowed_keys':self.cfg.allowed_keys,'learned_policy':value.get('policy_choice'),'note':'モデルの信頼度と、経験から計算した優先度・試行数は別の指標です。'})
        self.set_panel('選択行動と理由',{'keys':value.get('keys'),'hold_ms':value.get('hold_ms'),'reason':value.get('reason')})
        self.set_panel('仮説',value.get('memory_updates',[]));self.set_panel('新発見',value.get('progress',[]))
        self.set_panel('修正履歴',value.get('knowledge_revisions',{}));self.set_panel('OCR・攻略知識の照合',{'ocr':value.get('ocr',{}),'knowledge_assessments':value.get('knowledge_assessments',[]),'note':'OCRと出典の照合は仮説。ゲーム内効果の実証とは区別します。'})

    def poll(self):
        try:
            for _ in range(100):
                kind,value=self.queue.get_nowait()
                if kind=='error':self.status.set('エラーで停止: '+value);self.set_panel('現在状態',{'status':'error','reason':value});self.append_log({'event':'error','reason':value})
                elif kind=='done':
                    self.control=None
                    self.start_button.configure(state='normal');self.pause_button.configure(text='⏸ 一時停止')
                elif kind=='status':self.status.set(value)
                elif kind=='progress':self.progress.set(value)
                elif kind=='model':self.vars['model'].set(value);self.vars['provider'].set('ollama');self.progress.set('モデルの取得が完了しました。')
                elif kind=='decision':self.update_decision(value)
                elif kind=='connected':
                    self.backend,self.connection_title=value;self.badges.set('接続済み / 背面分離 / OCR: '+self.cfg.ocr_engine)
                    self.set_panel('バックエンド状態',{'window':self.connection_title,'capture':'PrintWindow','input':'WindowMessage / ウィンドウ宛て（ゲーム受理とは別）','model':self.cfg.model,'endpoint':self.cfg.endpoint,'ocr':self.cfg.ocr_engine,'data':str(DATA)})
                    self.status.set('ゲームに接続しました。ライブ画面を取得しています。')
                elif kind=='close_probe':
                    if self.probe:self.probe.destroy();self.probe=None
                elif kind=='exported':self.latest_zip=Path(value);self.status.set(self.status.get().split('\n診断ZIP')[0]+'\n診断ZIPを生成しました。「提出用ZIPをコピー」で添付できます。');self.append_log({'event':'diagnostic_export','path':str(value)})
                elif kind=='export_error':self.status.set('診断ZIPの作成に失敗: '+value);self.append_log({'event':'export_error','reason':value})
                elif kind=='session':self.set_panel('現在状態',value);self.status.set(value['status']+' / '+value['reason'])
                elif kind=='memory':
                    self.set_panel('仮説',value.get('provisional_claims',[]));self.set_panel('系列のパターン',value.get('known_transitions',[]))
                elif kind=='cumulative':self.set_panel('累積統計',{'saved_experiences':value,'note':'この目標・知識・ゲーム設定の経験。診断を除く。'})
        except queue.Empty:pass
        try:self.draw_frame()
        except Exception as error:self.live_info.set('ライブ表示エラー: '+str(error))
        elapsed=int(time.time()-self.run_started) if self.run_started else 0
        self.set_panel('今回統計',{**self.run_counts,'elapsed_seconds':elapsed,'paused':bool(self.control and self.control.paused.is_set())})
        self.root.after(100,self.poll)

    def close(self):
        self.stop.set()
        if self.busy():
            self.status.set('停止中。記録と診断ZIPを保存してから閉じます。');self.root.after(200,self.close)
        else:
            self.preview_done.set()
            if self.control:self.control.close()
            try:self.save_ui()
            except OSError:pass
            self.root.destroy()

def main():
    if os.name=='nt':
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    root=tk.Tk(); Application(root); root.mainloop()
