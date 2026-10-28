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
from . import __version__
from .core import Settings, LocalLLM, Controller
from .memory import MemoryStore
from .components import CATALOG, MODELS, pull_model, install_capture

DATA=Path(os.environ.get('GAMEPILOT_DATA_DIR',Path(__file__).resolve().parent.parent/'data'))

class Application:
    def __init__(self,root):
        self.root=root;self.configure_theme();root.title(f'ローカルLLM ゲーム攻略ツール v{__version__} — 統合コンソール');root.geometry('1500x920');root.minsize(1000,640)
        from .ui_budget import UIEventQueue
        self.queue=UIEventQueue();self.stop=threading.Event();self.worker=None;self.closing_at=None;self.watchdog_file=None;self.watchdog_at=0
        root.report_callback_exception=self.callback_error
        self.backend=None;self.connection_title=None;self.control=None;self.preview_done=threading.Event()
        self.frame_slot=None;self.shown_frame=None;self.latest_zip=None;self.probe=None;self.probe_keys=0
        self.run_started=None;self.run_finished=None;self.run_counts={'decisions':0,'sent':0,'changed':0,'retries':0};self.last_row={}
        self.cfg=Settings();self.ui_state={}
        from .profiles import ProfileStore
        self.profiles=ProfileStore(DATA/'profiles');self.selected_target=None
        if (DATA/'settings.json').exists():
            try: self.cfg=Settings(**json.loads((DATA/'settings.json').read_text(encoding='utf-8'))).validate()
            except Exception as error: messagebox.showwarning('設定',f'設定を読み込めません。既定値で開始します。\n{error}')
        if (DATA/'ui-settings.json').exists():
            try: self.ui_state=json.loads((DATA/'ui-settings.json').read_text(encoding='utf-8'))
            except (OSError,ValueError): pass
        from .telemetry import RecognitionState
        from .guidance import OperatorGuidance
        from .dashboard import LearningTraces
        self.traces=LearningTraces()
        self.recognition=RecognitionState();self.recognition_window=None;self.recognition_floating=None;self.overlay=None
        self.operator_guidance=OperatorGuidance(self.ui_state.get('operator_guidance',''))
        from .controls import is_torneko
        saved=self.profiles.load(self.cfg.profile_id) if self.cfg.profile_id else None
        if saved:self.selected_target=dict(saved['target'])
        if not self.cfg.knowledge and is_torneko(self.cfg.window_title):
            default=Path(__file__).resolve().parent.parent/'knowledge/torneko.md'
            if default.exists(): self.cfg.knowledge=default.read_text(encoding='utf-8')
        from .placement import WindowPlacement
        self.placement=WindowPlacement(root,self.ui_state)
        header=ttk.Frame(root,padding=8);header.pack(fill='x')
        ttk.Label(header,text=f'ゲーム攻略くん + ローカルLLM v{__version__}',font=('',16,'bold')).pack(side='left')
        self.badges=tk.StringVar(value='未接続 / WindowMessage・背面対応 / 診断と学習を分離')
        ttk.Label(header,textvariable=self.badges).pack(side='left',padx=16)
        from .dashboard import flow_frame_class
        FlowFrame=flow_frame_class()
        # One shared flow, rather than separate fixed rows for each category.
        # Its parent spans the root, so later actions fill the remaining width.
        toolbar=FlowFrame(root,padding=(10,2));toolbar.pack(fill='x')
        ttk.Button(toolbar,text='起動中のゲームを選択',command=self.choose_target).pack(side='left',padx=4)
        ttk.Button(toolbar,text='ゲームに接続',command=self.connect).pack(side='left')
        self.start_button=ttk.Button(toolbar,text='▶ 攻略開始',command=self.start);self.start_button.pack(side='left',padx=4)
        self.pause_button=ttk.Button(toolbar,text='⏸ 一時停止',command=self.pause);self.pause_button.pack(side='left')
        ttk.Button(toolbar,text='■ 停止',command=self.request_stop).pack(side='left',padx=4)
        emergency_button=ttk.Button(toolbar,text='緊急停止 (F8 / Ctrl+Shift+F9)',command=self.emergency);emergency_button.pack(side='left')
        tools_bar=toolbar
        self.demo_button=ttk.Button(tools_bar,text='お手本記録',command=lambda:self.start(demonstration=True));self.demo_button.pack(side='left',padx=4)
        ttk.Button(tools_bar,text='コンソール操作',command=self.open_console).pack(side='left',padx=4)
        ttk.Button(tools_bar,text='OCRテスト',command=self.open_ocr).pack(side='left',padx=4)
        ttk.Button(tools_bar,text='セットアップ',command=lambda:self.tabs.select(self.setup_tab)).pack(side='left')
        ttk.Button(tools_bar,text='軽量診断ZIP',command=self.export).pack(side='left',padx=4)
        ttk.Button(tools_bar,text='詳細ZIP',command=lambda:self.export(True)).pack(side='left',padx=4)
        ttk.Button(tools_bar,text='最新ZIPを開く',command=self.open_report).pack(side='left')
        ttk.Button(tools_bar,text='提出用ZIPをコピー',command=self.copy_report).pack(side='left',padx=4)
        visual_bar=toolbar
        ttk.Button(visual_bar,text='認識モニターを別窓で開く',command=self.open_recognition).pack(side='left')
        ttk.Button(visual_bar,text='作戦指示を入力',command=self.open_guidance).pack(side='left',padx=4)
        self.overlay_enabled=tk.BooleanVar(value=self.ui_state.get('preview_graph_enabled',False))
        ttk.Checkbutton(visual_bar,text='キャプチャに学習グラフ',variable=self.overlay_enabled,command=self.configure_overlay).pack(side='left',padx=8)
        self.overlay_layers={}
        graph_controls=toolbar
        from .dashboard import SERIES
        for key,(label,color) in SERIES.items():
            var=tk.BooleanVar(value=self.ui_state.get('graph_series',{}).get(key,key in ('forecast','novelty','uncertainty')));self.overlay_layers[key]=var
            ttk.Checkbutton(graph_controls,text=label,variable=var,command=self.configure_overlay).pack(side='left')
        ttk.Button(graph_controls,text='全種',command=lambda:self.select_graphs(True)).pack(side='left')
        ttk.Button(graph_controls,text='全解除',command=lambda:self.select_graphs(False)).pack(side='left')
        options=toolbar
        duration_group=ttk.Frame(options);duration_group.pack(side='left')
        ttk.Label(duration_group,text='実行時間').pack(side='left')
        self.duration=tk.StringVar(value=self.ui_state.get('duration','なし'))
        self.duration_box=ttk.Combobox(duration_group,textvariable=self.duration,values=['なし','10分','20分','30分','60分'],state='readonly',width=6)
        self.duration_box.pack(side='left',padx=6)
        self.separation=tk.BooleanVar(value=False);self.compat=tk.BooleanVar(value=self.ui_state.get('compat',True))
        ttk.Checkbutton(options,text='分離監視（無入力・10分）',variable=self.separation).pack(side='left')
        ttk.Checkbutton(options,text='起動時の入力経路確認',variable=self.compat).pack(side='left',padx=6)
        self.dry=tk.BooleanVar(value=False);ttk.Checkbutton(options,text='観察のみ（キー送信なし）',variable=self.dry).pack(side='left')
        fast_bar=toolbar
        self.supervised_learning=tk.BooleanVar(value=self.cfg.supervised_learning)
        ttk.Label(fast_bar,text='経験予測コア v2 / 非同期LLM監督').pack(side='left')
        self.fast_learning=tk.BooleanVar(value=True);self.review_interval=tk.IntVar(value=self.cfg.llm_review_interval)
        ttk.Label(fast_bar,text='観測・操作は常時高速 / 状況に応じて精査').pack(side='left')
        review_group=ttk.Frame(fast_bar);review_group.pack(side='left')
        ttk.Label(review_group,text='監督LLMの定期確認（手）').pack(side='left',padx=(0,4))
        ttk.Spinbox(review_group,from_=16,to=4096,increment=16,textvariable=self.review_interval,width=6).pack(side='left')
        ttk.Label(review_group,text='（次回開始から適用）').pack(side='left',padx=5)
        for widget in toolbar.winfo_children():
            if isinstance(widget,ttk.Button):widget.configure(style='Toolbar.TButton')
        self.start_button.configure(style='Start.Toolbar.TButton')
        emergency_button.configure(style='Emergency.Toolbar.TButton')
        toolbar.schedule()
        self.status=tk.StringVar(value='待機中 — ゲームに接続するとライブ画面を表示します。')
        status_host=ttk.Frame(root,padding=(10,4));status_host.pack(fill='x')
        self.status_text=tk.Text(status_host,width=1,height=2,wrap='word',state='disabled',relief='flat',takefocus=False)
        self.status_text.pack(side='left',fill='x',expand=True)
        status_scroll=ttk.Scrollbar(status_host,command=self.status_text.yview);status_scroll.pack(side='right',fill='y');self.status_text.configure(yscrollcommand=status_scroll.set)
        self.status.trace_add('write',self.refresh_status_text);self.refresh_status_text()
        self.tabs=ttk.Notebook(root);self.tabs.pack(fill='both',expand=True,padx=8,pady=5)
        dashboard=ttk.Frame(self.tabs);config_tab=ttk.Frame(self.tabs);config=self.scroll_frame(config_tab);self.setup_tab=ttk.Frame(self.tabs)
        self.tabs.add(dashboard,text='攻略・観測・検証');self.tabs.add(config_tab,text='目標・攻略知識・接続設定');self.tabs.add(self.setup_tab,text='セットアップ')
        split=ttk.Panedwindow(dashboard,orient='horizontal');split.pack(fill='both',expand=True)
        self.dashboard_split=split;self.dashboard_last_width=None;self.dashboard_dragging=False
        split.bind('<Configure>',self.resize_dashboard)
        split.bind('<ButtonPress-1>',lambda event:setattr(self,'dashboard_dragging',True))
        split.bind('<ButtonRelease-1>',self.save_dashboard)
        observer=ttk.Frame(split,width=280);left=ttk.Frame(split,width=800);right=ttk.Frame(split,width=400)
        # Pane dimensions belong to the user/splitter, never to log labels.
        for pane in (observer,left,right):pane.pack_propagate(False)
        split.add(observer,weight=2);split.add(left,weight=5);split.add(right,weight=3)
        from .observer_view import ObserverPanels,ObserverDigest,SECTIONS
        self.observer_panels=ObserverPanels();self.observer_digest=ObserverDigest();self.observer_positions={}
        section=self.ui_state.get('observer_section','予測と結果')
        if section not in SECTIONS:section='予測と結果'
        self.observer_section=tk.StringVar(value=section);self.observer_shown_section=section
        ttk.Label(observer,text='攻略の観察',padding=5,font=('',11,'bold')).pack(fill='x')
        selector=ttk.Combobox(observer,textvariable=self.observer_section,values=list(SECTIONS),state='readonly',width=22)
        selector.pack(fill='x',padx=4,pady=(0,5));selector.bind('<<ComboboxSelected>>',self.change_observer_section)
        ttk.Label(observer,text='表示内容を選択 / 境界をドラッグして幅変更',wraplength=240,padding=4).pack(fill='x')
        detail_host=ttk.Frame(observer);detail_host.pack(fill='both',expand=True,padx=4,pady=4)
        self.detail_body=tk.Text(detail_host,width=27,height=12,wrap='word',state='disabled',relief='flat',padx=7,pady=7)
        self.detail_body.pack(side='left',fill='both',expand=True)
        detail_scroll=ttk.Scrollbar(detail_host,command=self.detail_body.yview);detail_scroll.pack(side='right',fill='y');self.detail_body.configure(yscrollcommand=detail_scroll.set)
        self.capture_log_split=ttk.Panedwindow(left,orient='vertical');self.capture_log_split.pack(fill='both',expand=True)
        capture_host=ttk.Frame(self.capture_log_split,height=400);log_host=ttk.Frame(self.capture_log_split,height=250)
        for pane in (capture_host,log_host):pane.pack_propagate(False)
        self.capture_log_split.add(capture_host,weight=3);self.capture_log_split.add(log_host,weight=2)
        from .dashboard import split_fraction
        self.capture_log_ratio=split_fraction(self.ui_state.get('capture_log_ratio',.62))
        self.capture_log_dragging=False;self.capture_log_pending=False
        self.capture_log_split.bind('<Configure>',self.resize_capture_log)
        self.capture_log_split.bind('<ButtonPress-1>',lambda event:setattr(self,'capture_log_dragging',True))
        self.capture_log_split.bind('<ButtonRelease-1>',self.save_capture_log)
        self.live=tk.Canvas(capture_host,width=1,bg='black',highlightthickness=0,height=390);self.live.pack(fill='both',expand=True)
        self.live.create_text(280,180,text='ゲームに接続していません',fill='white',tags='image')
        self.live.bind('<Configure>',self.resize_live)
        self.live_info=tk.StringVar(value='画面の記録は実機実行時のみ。架空ゲームは使用しません。')
        self.live_caption=ttk.Label(capture_host,textvariable=self.live_info,wraplength=300);self.live_caption.pack(fill='x')
        ttk.Label(log_host,text='ターンの要約・介入・観測結果',font=('',11,'bold')).pack(anchor='w',pady=(6,2))
        logtools=ttk.Frame(log_host);logtools.pack(anchor='e')
        ttk.Button(logtools,text='ログの最新へ',command=lambda:self.log.see('end')).pack(side='left')
        self.log_follow_latest=tk.BooleanVar(value=self.ui_state.get('log_follow_latest',False))
        ttk.Checkbutton(logtools,text='最新で固定',variable=self.log_follow_latest,command=self.follow_log_latest).pack(side='left',padx=(8,0))
        logbox=ttk.Frame(log_host);logbox.pack(fill='both',expand=True)
        self.log=tk.Text(logbox,width=1,height=12,wrap='word',state='disabled');self.log.pack(side='left',fill='both',expand=True)
        logscroll=ttk.Scrollbar(logbox,command=self.log.yview);logscroll.pack(side='right',fill='y');self.log.configure(yscrollcommand=logscroll.set)
        from .telemetry import RecognitionPanel
        self.runtime_summary=tk.StringVar(value='経過 00:00:00 / 完了ターン 0')
        runtime_label=ttk.Label(right,textvariable=self.runtime_summary,padding=7,font=('',11,'bold'),wraplength=280);runtime_label.pack(fill='x')
        right.bind('<Configure>',lambda event:runtime_label.configure(wraplength=max(1,event.width-14)))
        right_split=ttk.Panedwindow(right,orient='vertical');right_split.pack(fill='both',expand=True)
        recognition_host=ttk.Frame(right_split);summary_host=ttk.LabelFrame(right_split,text='判断の要点',padding=4)
        right_split.add(recognition_host,weight=3);right_split.add(summary_host,weight=2)
        self.recognition_panel=RecognitionPanel(self.scroll_frame(recognition_host),self.recognition);self.recognition_panel.frame.pack(fill='x',padx=4,pady=4)
        self.judgment_body=tk.Text(summary_host,width=28,height=9,wrap='word',state='disabled',relief='flat',padx=5,pady=5)
        self.judgment_body.pack(side='left',fill='both',expand=True)
        summary_scroll=ttk.Scrollbar(summary_host,command=self.judgment_body.yview);summary_scroll.pack(side='right',fill='y');self.judgment_body.configure(yscrollcommand=summary_scroll.set)
        self.refresh_observer_section()
        self.set_panel('判断の要点',self.observer_digest.summary())
        self.set_panel('観察ダイジェスト',self.observer_digest.journal())
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
        profile_line=ttk.Frame(config);profile_line.grid(row=12,column=0,columnspan=2,sticky='ew',pady=5)
        ttk.Label(profile_line,text='ゲーム別プロファイル').pack(side='left')
        self.profile_choice=ttk.Combobox(profile_line,state='readonly',width=42,postcommand=self.refresh_profiles)
        self.profile_choice.pack(side='left',padx=6);self.profile_choice.bind('<<ComboboxSelected>>',self.load_profile)
        ttk.Button(profile_line,text='このプロファイルを保存',command=self.save_profile).pack(side='left')
        ttk.Label(config,text='キー配置（1行に w=前進 / num1=決定 など。入力すると許可キーもこの一覧に合わせます）').grid(row=13,column=0,columnspan=2,sticky='w')
        self.bindings=tk.Text(config,height=5,wrap='word');self.bindings.grid(row=14,column=0,columnspan=2,sticky='ew')
        self.bindings.insert('1.0','\n'.join(k+'='+v for k,v in self.cfg.key_bindings.items()))
        flags=ttk.Frame(config);flags.grid(row=15,column=0,columnspan=2,sticky='ew')
        self.resilient=tk.BooleanVar(value=self.cfg.resilient);self.adaptive=tk.BooleanVar(value=self.cfg.adaptive_timing)
        ttk.Checkbutton(flags,text='対象終了時の再起動・再観測を継続',variable=self.resilient).pack(side='left')
        ttk.Checkbutton(flags,text='入力時間を観測に基づき調整',variable=self.adaptive).pack(side='left',padx=8)
        compute=ttk.Frame(config);compute.grid(row=16,column=0,columnspan=2,sticky='ew',pady=4)
        ttk.Label(compute,text='推論資源（shared=GPU設定を維持 / cpu_only=GPUを使わない）').pack(side='left')
        self.vars['compute_mode']=tk.StringVar(value=self.cfg.compute_mode)
        ttk.Combobox(compute,textvariable=self.vars['compute_mode'],values=['shared','cpu_only'],state='readonly',width=12).pack(side='left',padx=8)
        self.refresh_profiles()
        setup=self.scroll_frame(self.setup_tab);self.setup(setup);self.setup_ocr(setup)
        self.set_panel('バックエンド状態','ゲーム未接続\n取得: PrintWindow / 入力: WindowMessage（背面分離）\nLLM: '+self.cfg.model+'\nDB: '+str(DATA))
        self.set_panel('目的',self.cfg.goal+'\n小目標: 未設定')
        from .controls import REFERENCE
        self.set_panel('キー配置・ミニマップ',REFERENCE if is_torneko(self.cfg.window_title) else self.cfg.key_bindings or 'キー配置一覧を入力してください。')
        self.set_panel('自己改善','停滞時にLLMが仮説を提案し、最大4入力・8判断で検証します。効果不明・悪化なら撤回。採用も24判断限定です。')
        self.set_panel('行動学習','ゲーム別に観測・小目標・操作結果を保存。入力時間は確認済み移動キーの観測関連から調整します。トルネコは地図/HUD方策も併用。LLM本体の再訓練ではありません。')
        self.set_panel('候補と評価','LLMの信頼度・危険度・許可キーを表示します。\n未生成の候補点数は表示しません。')
        self.set_panel('系列のパターン','保存済み経験から状態・操作の反復を集計します。診断は含めません。')
        reports=sorted((DATA/'exports').glob('*.zip'),key=lambda p:p.stat().st_mtime,reverse=True)
        if reports:self.latest_zip=reports[0]
        self.preview=threading.Thread(target=self.preview_loop,daemon=True);self.preview.start()
        root.protocol('WM_DELETE_WINDOW',self.close);root.after(100,self.poll);root.after(33,self.refresh_live)

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
        for widget in ('Button','Checkbutton','Radiobutton','Spinbox','Entry','Listbox'):
            self.root.option_add('*'+widget+'.background',well);self.root.option_add('*'+widget+'.foreground',foreground)
            self.root.option_add('*'+widget+'.selectColor','#34485f')
        style=ttk.Style(self.root);style.theme_use('clam')
        style.configure('.',background=background,foreground=foreground)
        style.configure('TButton',padding=(7,4),background='#303848',foreground=foreground)
        style.map('TButton',background=[('active','#3d5270'),('disabled','#252a33')],foreground=[('disabled','#858b97')])
        style.configure('Toolbar.TButton',width=0,padding=(8,4),borderwidth=1,relief='flat',background='#303c50')
        style.configure('Start.Toolbar.TButton',background='#245a80')
        style.map('Start.Toolbar.TButton',background=[('disabled','#252a33'),('active','#337ead')])
        style.configure('Emergency.Toolbar.TButton',background='#713c43')
        style.map('Emergency.Toolbar.TButton',background=[('disabled','#252a33'),('active','#974e57')])
        style.configure('TCheckbutton',background=background,foreground=foreground,indicatorbackground='#34485f',indicatorforeground=foreground)
        style.map('TCheckbutton',background=[('active','#283449')],foreground=[('disabled','#8898ac')])
        style.configure('TSpinbox',fieldbackground=well,foreground=foreground,arrowcolor=foreground)
        style.configure('Sash',sashthickness=7,gripcount=0)
        style.configure('TScrollbar',background='#34485f',troughcolor=well,arrowcolor=foreground)
        style.configure('TEntry',fieldbackground=well,foreground=foreground,insertcolor=foreground)
        style.configure('TCombobox',fieldbackground=well,foreground=foreground,arrowcolor=foreground)
        style.map('TCombobox',fieldbackground=[('readonly',well)],foreground=[('readonly',foreground)])
        style.configure('TNotebook.Tab',padding=(12,6),background='#252a33',foreground=foreground)
        style.map('TNotebook.Tab',background=[('selected','#3d5270')])
        style.configure('TLabelframe',bordercolor='#3b4251');style.configure('TLabelframe.Label',foreground=foreground)
        style.configure('GameList.Treeview',background='#f4f7fb',fieldbackground='#f4f7fb',
                        foreground='#17212f',bordercolor='#8b9aad')
        style.map('GameList.Treeview',background=[('selected','#1d4ed8')],foreground=[('selected','#ffffff')])
        style.configure('GameList.Treeview.Heading',background='#d9e2ef',foreground='#17212f')
        style.map('GameList.Treeview.Heading',background=[('active','#c7d5e8')],foreground=[('active','#17212f')])

    def scroll_frame(self,parent):
        canvas=tk.Canvas(parent,highlightthickness=0,bg='#171a21');scroll=ttk.Scrollbar(parent,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        frame=ttk.Frame(canvas,padding=6);window=canvas.create_window((0,0),window=frame,anchor='nw')
        frame.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda event:canvas.itemconfigure(window,width=event.width))
        return frame

    def refresh_status_text(self,*args):
        from .dashboard import replace_text
        text=self.status.get()
        if getattr(self.status_text,'_last_text',None)==text:return
        replace_text(self.status_text,text);self.status_text._last_text=text

    def set_panel(self,title,value):
        from .dashboard import replace_text
        if title=='今回統計':self.runtime_summary.set(value);return
        if title=='判断の要点':
            body=self.judgment_body;text=value
            if getattr(body,'_last_text',None)!=text:replace_text(body,text);body._last_text=text
            return
        destination=self.observer_panels.put(title,value)
        from .observer_view import SECTIONS
        if destination in SECTIONS.get(self.observer_section.get(),()):self.refresh_observer_section()

    def refresh_observer_section(self):
        from .dashboard import replace_text
        text=self.observer_panels.render(self.observer_section.get())
        if getattr(self.detail_body,'_last_text',None)!=text:
            replace_text(self.detail_body,text);self.detail_body._last_text=text

    def change_observer_section(self,event=None):
        body=self.detail_body
        self.observer_positions[self.observer_shown_section]=(body.index('@0,0'),body.xview()[0])
        self.observer_shown_section=self.observer_section.get();self.refresh_observer_section()
        top,x=self.observer_positions.get(self.observer_shown_section,('1.0',0.))
        body.yview(top);body.xview_moveto(x)
        self.ui_state['observer_section']=self.observer_shown_section
        try:self.save_ui()
        except OSError as error:self.status.set('表示設定の保存に失敗: '+str(error))

    def open_recognition(self):
        from .telemetry import RecognitionPanel
        if self.recognition_window and self.recognition_window.winfo_exists():
            self.recognition_window.deiconify();return
        self.recognition_window=tk.Toplevel(self.root);self.recognition_window.title(f'認識モニター v{__version__}')
        self.recognition_window.geometry('380x355');self.recognition_window.minsize(355,330)
        self.recognition_floating=RecognitionPanel(self.recognition_window,self.recognition)
        self.recognition_floating.frame.pack(fill='both',expand=True)
        self.recognition_window.protocol('WM_DELETE_WINDOW',self.recognition_window.withdraw)

    def select_graphs(self,enabled):
        for value in self.overlay_layers.values():value.set(enabled)
        self.configure_overlay()
    def configure_overlay(self):
        if self.overlay:self.overlay.close();self.overlay=None
        self.draw_learning_graph()
    def draw_learning_graph(self):
        enabled=self.overlay_enabled.get();bounds=getattr(self,'preview_bounds',None)
        signature=(self.traces.turn,enabled,tuple((k,v.get()) for k,v in self.overlay_layers.items()),bounds)
        changed=getattr(self,'_graph_signature',None)!=signature
        if not enabled or not bounds:
            if changed:self.live.delete('learning_graph');self._graph_signature=signature
            return
        x,y,w,h=bounds
        if w<150 or h<140:
            if changed:self.live.delete('learning_graph');self._graph_signature=signature
            return
        now=time.monotonic()
        if not changed and (getattr(self,'_graph_frame',None) is self.shown_frame or now-getattr(self,'_graph_background_at',0)<.25):return
        width=min(285,max(120,w*.42));height=min(170,max(100,h*.4))
        from .dashboard import translucent_background
        from PIL import ImageTk
        box=(round(x+w-width-8),round(y+h-height-8),round(width),round(height))
        crop=(box[0]-x,box[1]-y,box[2],box[3])
        background=translucent_background(self.preview_image,crop)
        if changed:
            self.graph_background=ImageTk.PhotoImage(background)
            self.traces.paint(self.live,{k:v.get() for k,v in self.overlay_layers.items()},box,background=self.graph_background)
        else:self.graph_background.paste(background)
        self._graph_signature=signature;self._graph_background_at=now;self._graph_frame=self.shown_frame

    def open_guidance(self):
        existing=getattr(self,'guidance_window',None)
        if existing and existing.winfo_exists():existing.deiconify();return
        window=tk.Toplevel(self.root);self.guidance_window=window;window.title('自由文の作戦指示');window.geometry('560x245')
        ttk.Label(window,text='例：慎重に進めて / アイテム回収を優先 / 戦闘を避けて階段を探して',padding=8).pack(anchor='w')
        editor=tk.Text(window,height=6,wrap='word');editor.pack(fill='both',expand=True,padx=8);editor.insert('1.0',self.operator_guidance.snapshot()['text'])
        pending=[None]
        def persist():
            pending[0]=None
            try:self.save_ui()
            except OSError as error:self.status.set('作戦指示は反映済みですが保存できません: '+str(error))
        def apply(clear=False):
            try:
                text='' if clear else editor.get('1.0','end-1c')
                before=self.operator_guidance.snapshot()['revision'];revision=self.operator_guidance.set(text)
                self.ui_state['operator_guidance']=self.operator_guidance.snapshot()['text']
                if revision!=before:
                    self.recognition.update({'event':'guidance_pending'})
                    self.status.set(f'作戦指示を自動反映しました（更新{revision}）。各LLM呼出で現在の文章を適用します。')
                if clear:editor.delete('1.0','end')
                if pending[0] is not None:window.after_cancel(pending[0])
                pending[0]=window.after(400,persist)
            except ValueError as error:self.status.set('作戦指示は未反映: '+str(error))
        def modified(event=None):
            if editor.edit_modified():
                editor.edit_modified(False);apply()
        editor.edit_modified(False);editor.bind('<<Modified>>',modified)
        buttons=ttk.Frame(window,padding=8);buttons.pack(fill='x')
        ttk.Button(buttons,text='現在の文章を反映（入力時も自動）',command=apply).pack(side='left')
        ttk.Button(buttons,text='指示を解除',command=lambda:apply(True)).pack(side='left',padx=6)
        ttk.Label(window,text='入力内容を毎回のLLM呼出に適用。2000文字以内。観測と操作制限は維持します。',padding=8).pack(anchor='w')
        def hide():
            apply();persist();window.withdraw()
        window.protocol('WM_DELETE_WINDOW',hide)

    def resize_capture_log(self,event=None):
        if not self.capture_log_pending:
            self.capture_log_pending=True;self.root.after_idle(self.apply_capture_log_ratio)
    def apply_capture_log_ratio(self):
        self.capture_log_pending=False
        if self.capture_log_dragging or not self.capture_log_split.winfo_exists():return
        height=self.capture_log_split.winfo_height()
        if height>1:
            minimum=min(70,height*.2)
            self.capture_log_split.sashpos(0,round(max(minimum,min(height-minimum,height*self.capture_log_ratio))))
    def save_capture_log(self,event=None):
        self.capture_log_dragging=False
        height=self.capture_log_split.winfo_height()
        if height>1:
            from .dashboard import split_fraction
            self.capture_log_ratio=split_fraction(self.capture_log_split.sashpos(0)/height)
            self.apply_capture_log_ratio()
            try:self.save_ui()
            except OSError as error:self.status.set('表示設定の保存に失敗: '+str(error))

    def save_ui(self):
        self.placement.track()
        self.ui_state.update(capture_log_ratio=self.capture_log_ratio,log_follow_latest=self.log_follow_latest.get(),duration=self.duration.get(),compat=self.compat.get(),preview_graph_enabled=self.overlay_enabled.get(),graph_series={k:v.get() for k,v in self.overlay_layers.items()})
        DATA.mkdir(parents=True,exist_ok=True);temporary=DATA/'ui-settings.json.tmp'
        temporary.write_text(json.dumps(self.ui_state,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(DATA/'ui-settings.json')

    def preview_loop(self):
        from PIL import Image
        from .live_preview import PreviewBudget
        budget=PreviewBudget();last_sequence=None
        while not self.preview_done.is_set():
            started=time.monotonic();backend=self.backend
            if backend is None or not getattr(self,'preview_visible',True):
                self.preview_done.wait(.1);continue
            try:
                value=None
                if hasattr(backend,'capture_preview'):
                    value=backend.capture_preview(self.preview_done,size=budget.size(getattr(self,'preview_viewport',(640,400))))
                    if value:
                        at=value.get('captured_at',time.time());sequence=value.get('sequence',at)
                        if sequence!=last_sequence and (not self.frame_slot or self.frame_slot[0] is None or at>self.frame_slot[1]):
                            self.frame_slot=(Image.frombytes('RGB',tuple(value['size']),value['pixels']),at)
                        last_sequence=sequence
                        budget.sample(time.monotonic()-started,getattr(self,'preview_draw_seconds',0),time.monotonic())
            except Exception as error:
                from .cancellation import OperationCancelled
                if not isinstance(error,OperationCancelled):self.frame_slot=(None,str(error))
            # Deadline pacing includes capture time; no fixed extra 400ms delay.
            self.preview_done.wait(max(.01,budget.period-(time.monotonic()-started)))

    def resize_live(self,event):
        if hasattr(self,'live_caption'):self.live_caption.configure(wraplength=max(1,event.width-8))
        self.preview_viewport=(max(160,event.width),max(90,event.height))
        self.draw_frame(force=True)

    def refresh_live(self):
        if self.preview_done.is_set():return
        try:
            self.preview_visible=bool(self.root.winfo_viewable() and self.live.winfo_viewable())
            if self.preview_visible:self.draw_frame();self.draw_live_status()
        except Exception as error:self.live_info.set('LIVE表示エラー: '+str(error))
        finally:
            try:self.root.after(33 if getattr(self,'preview_visible',True) else 250,self.refresh_live)
            except tk.TclError:pass

    def draw_live_status(self):
        if not getattr(self,'preview_bounds',None) or not self.frame_slot or self.frame_slot[0] is None:return
        now=time.monotonic()
        if now-getattr(self,'last_live_status_at',0)<.25:return
        self.last_live_status_at=now
        from .live_preview import live_status
        text=live_status(getattr(self,'live_paint_times',()),self.frame_slot[1],time.monotonic(),time.time())
        self.live.delete('live_status');x,y,w,h=self.preview_bounds
        tag=self.live.create_text(x+7,y+h-7,text=text,anchor='sw',fill='#d7e6ed',font=('Segoe UI',9),tags='live_status')
        box=self.live.bbox(tag)
        if box:
            background=self.live.create_rectangle(box[0]-4,box[1]-2,box[2]+4,box[3]+2,fill='#14202b',outline='',tags='live_status')
            self.live.tag_lower(background,tag)

    def draw_frame(self,force=False):
        frame=self.frame_slot
        if not frame or (frame is self.shown_frame and not force):return
        started=time.monotonic()
        self.shown_frame=frame
        if frame[0] is None:
            self.live.delete('image');self.live_image_id=None
            self.preview_bounds=None;self._graph_signature=None;self._graph_frame=None;self.live.delete('learning_graph');self.live.delete('live_status');self.last_live_status_at=0
            self.live.create_text(20,20,anchor='nw',text=frame[1],fill='white',tags='image');return
        from PIL import Image,ImageTk
        import io
        source=frame[0] if isinstance(frame[0],Image.Image) else Image.open(io.BytesIO(frame[0]));width,height=source.size
        from collections import deque
        if not hasattr(self,'live_paint_times'):self.live_paint_times=deque(maxlen=60)
        if not force:self.live_paint_times.append(time.monotonic())
        ratio=min(max(1,self.live.winfo_width())/width,max(1,self.live.winfo_height())/height)
        size=(max(1,round(width*ratio)),max(1,round(height*ratio)))
        image=source.resize(size,Image.Resampling.LANCZOS if size[0]<width or size[1]<height else Image.Resampling.NEAREST) if size!=source.size else source.copy()
        if not isinstance(frame[0],Image.Image):source.close()
        self.live_photo=ImageTk.PhotoImage(image)
        if getattr(self,'live_image_id',None):
            self.live.itemconfigure(self.live_image_id,image=self.live_photo)
            self.live.coords(self.live_image_id,self.live.winfo_width()/2,self.live.winfo_height()/2)
        else:
            self.live.delete('image')
            self.live_image_id=self.live.create_image(self.live.winfo_width()/2,self.live.winfo_height()/2,image=self.live_photo,tags='image')
        self.preview_image=image
        self.preview_bounds=((self.live.winfo_width()-image.width)/2,(self.live.winfo_height()-image.height)/2,image.width,image.height)
        self.draw_learning_graph();self.draw_live_status()
        self.preview_draw_seconds=time.monotonic()-started
        self.live_info.set(f'{width}×{height} / 最終取得 {time.strftime("%H:%M:%S",time.localtime(frame[1]))} / 背面キャプチャ')

    def connect(self):
        if self.busy():return
        title=self.vars['window_title'].get().strip()
        cfg=self.settings()
        target=dict(self.selected_target) if self.selected_target else None
        def work():
            from .windows import WindowsGame
            backend=self.open_target(title,target);backend.window_capture.cancel=self.stop;image=backend.capture();self.queue.put(('connected',(backend,title)))
            from .controls import is_torneko
            if is_torneko(cfg.window_title):
                memory=None
                try:
                    from .monitor_checkpoint import MonitorCheckpoint
                    memory=MemoryStore(DATA/'memory.sqlite',cfg);checkpoint=MonitorCheckpoint(memory.db,memory.profile)
                    if checkpoint.candidate:
                        from .ocr import ScreenOCR
                        from .knowledge import KnowledgeEngine
                        ocr=ScreenOCR(cfg.ocr_engine);ocr.cancel=self.stop
                        preview=checkpoint.preview(KnowledgeEngine().reconcile(ocr.read(image))['metrics'],image)
                        if preview:self.queue.put(('decision',preview))
                except Exception as error:self.queue.put(('status','接続済み。保存地図の事前照合は未完了: '+str(error)))
                finally:
                    if memory:memory.close()
        self.launch(work)

    @staticmethod
    def open_target(title,target=None):
        from .windows import WindowsGame,window_targets
        if target:
            candidates=window_targets()
            exact=[w for w in candidates if w['hwnd']==target.get('hwnd') and w['pid']==target.get('pid')
                   and w.get('executable')==target.get('executable')]
            if not exact:
                exact=[w for w in candidates if target.get('executable') and w['executable'].casefold()==target['executable'].casefold()
                       and w['class_name']==target.get('class_name')]
            if len(exact)!=1:
                from .recovery import TargetUnavailable
                raise TargetUnavailable('対象が閉じているか複数あります。対象の再接続を待ちます。')
            return WindowsGame(exact[0]['title'],hwnd=exact[0]['hwnd'])
        return WindowsGame(title)

    def apply_profile(self,cfg,target):
        self.cfg=cfg;self.selected_target=dict(target);self.backend=None;self.connection_title=None
        for key,var in self.vars.items():
            value=getattr(cfg,key);var.set(','.join(value) if isinstance(value,list) else str(value))
        for widget,value in ((self.goal,cfg.goal),(self.knowledge,cfg.knowledge),(self.bindings,'\n'.join(k+'='+v for k,v in cfg.key_bindings.items()))):
            widget.delete('1.0','end');widget.insert('1.0',value)
        self.resilient.set(cfg.resilient);self.adaptive.set(cfg.adaptive_timing);self.fast_learning.set(True);self.supervised_learning.set(cfg.supervised_learning);self.review_interval.set(cfg.llm_review_interval)
        from .controls import REFERENCE,is_torneko
        self.set_panel('キー配置・ミニマップ',REFERENCE if is_torneko(cfg.window_title) else cfg.key_bindings or 'キー配置一覧を入力してください。')
        self.set_panel('目的',cfg.goal);self.status.set('選択: '+target['title']+' / キー配置は接続設定で編集できます。')

    def refresh_profiles(self):
        self.profile_values=self.profiles.all()
        self.profile_choice.configure(values=[p['name']+' ['+p['id'][:8]+']' for p in self.profile_values])
        if self.cfg.profile_id:
            for i,p in enumerate(self.profile_values):
                if p['id']==self.cfg.profile_id:self.profile_choice.current(i);break

    def load_profile(self,event=None):
        if self.busy():return
        index=self.profile_choice.current()
        if index<0:return
        try:
            value=self.profile_values[index];self.apply_profile(Settings(**value['settings']).validate(),value['target'])
        except Exception as error:messagebox.showerror('プロファイル',str(error))

    def save_profile(self):
        if self.busy():return
        try:
            cfg=self.settings();target=self.selected_target or {'title':cfg.window_title}
            self.profiles.save(target,cfg);self.cfg=cfg;self.refresh_profiles()
            self.status.set('プロファイルを保存しました: '+target['title'])
        except Exception as error:messagebox.showerror('プロファイル保存',str(error))

    def choose_target(self):
        if self.busy():return
        from .windows import window_targets
        dialog=tk.Toplevel(self.root);dialog.title('起動中のゲームを選択');dialog.geometry('1000x420')
        tree=ttk.Treeview(dialog,columns=('title','pid','executable'),show='headings',selectmode='browse',style='GameList.Treeview')
        tree.tag_configure('even',background='#f4f7fb',foreground='#17212f')
        tree.tag_configure('odd',background='#e7edf5',foreground='#17212f')
        for key,label,width in (('title','ウィンドウ',380),('pid','PID',80),('executable','プログラム',480)):
            tree.heading(key,text=label);tree.column(key,width=width)
        tree.pack(fill='both',expand=True,padx=8,pady=8);targets=[]
        def refresh():
            targets[:]=window_targets();tree.delete(*tree.get_children())
            for i,t in enumerate(targets):tree.insert('', 'end',iid=str(i),tags=('odd' if i%2 else 'even',),values=(t['title'],t['pid'],t['executable'] or '取得不可（自動再起動不可）'))
        def select(event=None):
            selected=tree.selection()
            if not selected:return
            try:
                target=targets[int(selected[0])]
                if self.selected_target:self.profiles.save(self.selected_target,self.settings())
                cfg=self.profiles.select(target,self.cfg);self.profiles.save(target,cfg)
                self.apply_profile(cfg,target);self.refresh_profiles();dialog.destroy();self.connect()
            except Exception as error:messagebox.showerror('ゲーム選択',str(error),parent=dialog)
        buttons=ttk.Frame(dialog);buttons.pack(fill='x',padx=8,pady=8)
        ttk.Button(buttons,text='一覧更新',command=refresh).pack(side='left')
        ttk.Button(buttons,text='選択して接続',command=select).pack(side='right')
        tree.bind('<Double-1>',select);refresh()

    def pause(self):
        if self.control and self.busy():
            paused=self.control.toggle_pause();self.pause_button.configure(text='▶ 再開' if paused else '⏸ 一時停止')
            if getattr(self,'console_active',False):
                self.console_status(bool(getattr(self,'console_commands',None) and self.console_commands.ready));self.status.set('コンソール操作を一時停止。未送信要求は破棄します。' if paused else 'コンソール操作を再開しました。')
            elif getattr(self,'recording_human',False):self.status.set('お手本記録を一時停止。途中の操作例は保存対象から外します。' if paused else 'お手本記録を再開。キーを一度離してから操作してください。')
            else:self.status.set('一時停止中（送信予定の推論は破棄し、再開時に再観測）' if paused else '再開しました')

    def request_stop(self):
        self.stop.set();self.status.set('停止要求を受け付けました。待機中の処理を中断しています。')

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
        if self.selected_target and v['window_title']!=self.selected_target['title']:
            raise ValueError('ゲームの変更は「起動中のゲームを選択」またはプロファイル一覧から行ってください。')
        from .profiles import parse_bindings
        bindings=parse_bindings(self.bindings.get('1.0','end'))
        if bindings:v['allowed_keys']=list(bindings)
        v.update(key_bindings=bindings,resilient=self.resilient.get(),adaptive_timing=self.adaptive.get(),fast_learning=self.fast_learning.get(),supervised_learning=self.supervised_learning.get(),llm_review_interval=self.review_interval.get())
        v.update(goal=self.goal.get('1.0','end').strip(),knowledge=self.knowledge.get('1.0','end').strip())
        if len(v['knowledge'])>50000: raise ValueError('知識は 50,000 文字以内にしてください。')
        return Settings(**v).validate()
    def busy(self): return self.worker is not None and self.worker.is_alive()
    def launch(self,fn):
        if self.busy(): messagebox.showinfo('処理中','現在の処理が終了するまでお待ちください。'); return
        self.stop.clear(); self.start_button.configure(state='disabled')
        if hasattr(self,'demo_button'):self.demo_button.configure(state='disabled')
        def task():
            from .cancellation import OperationCancelled
            try: fn()
            except OperationCancelled:
                self.queue.put(('status','処理を中断しました。'))
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
    def open_console(self):
        if getattr(self,'console_window',None) is not None and self.console_window.winfo_exists():
            self.console_window.lift();return
        window=tk.Toplevel(self.root);self.console_window=window;window.title(f'コンソール操作・お手本記録 v{__version__}')
        ttk.Label(window,text='ゲームを前面にせず、このパネルから操作します。\n攻略中はボタンで割り込み、未送信の自動計画を破棄して1回操作します。\n入力・観測・保存が終わると自動攻略へ戻ります。',padding=8).pack()
        self.console_start=ttk.Button(window,text='単独操作を開始（攻略停止中）',command=lambda:self.start(console=True));self.console_start.pack(pady=4)
        timing=ttk.Frame(window);timing.pack(padx=8,pady=4)
        self.console_hold=tk.IntVar(value=min(100,self.cfg.max_hold_ms));self.console_wait=tk.IntVar(value=800)
        ttk.Label(timing,text='保持(ms)').pack(side='left');ttk.Spinbox(timing,from_=1,to=self.cfg.max_hold_ms,textvariable=self.console_hold,width=6).pack(side='left')
        ttk.Label(timing,text='再入力までの最低待機(ms)').pack(side='left');ttk.Spinbox(timing,from_=100,to=10000,increment=100,textvariable=self.console_wait,width=7).pack(side='left')
        keys=ttk.Frame(window);keys.pack(padx=8,pady=4);self.console_buttons=[];self.console_selected={}
        from .controls import ACTIONS
        pad=ttk.LabelFrame(keys,text='移動・決定',padding=5);pad.pack(fill='x')
        extra=ttk.LabelFrame(keys,text='その他の操作',padding=5);extra.pack(fill='x',pady=6)
        positions={}
        for choices,pos in [(('w','up'),(0,1)),(('a','left'),(1,0)),(('s','down'),(2,1)),(('d','right'),(1,2))]:
            key=next((k for k in choices if k in self.cfg.allowed_keys),None)
            if key:positions[key]=pos
        confirm=next((k for k in ('num1','enter','z') if k in self.cfg.allowed_keys and k not in positions),None)
        if confirm:positions[confirm]=(1,1)
        auxiliary=0
        for key in self.cfg.allowed_keys:
            if key in positions:parent=pad;row,col=positions[key]
            else:parent=extra;row,col=auxiliary//3,auxiliary%3;auxiliary+=1
            block=ttk.Frame(parent);block.grid(row=row,column=col,padx=3,pady=3,sticky='ew')
            value=tk.BooleanVar(value=False);self.console_selected[key]=value
            ttk.Checkbutton(block,variable=value).pack(side='left')
            button=ttk.Button(block,text=key+' '+self.cfg.key_bindings.get(key,ACTIONS.get(key,'')),width=18,command=lambda k=key:self.console_send([k]))
            button.pack(side='left');self.console_buttons.append(button)
        ttk.Label(window,text='チェックしたキーは「同時押し」で送信。チェックだけでは入力しません。').pack(padx=8)
        actions=ttk.Frame(window);actions.pack(pady=6)
        for label,command in [('同時押しを送信',lambda:self.console_send([k for k,v in self.console_selected.items() if v.get()])),('無入力で観測',lambda:self.console_send([]))]:
            button=ttk.Button(actions,text=label,command=command);button.pack(side='left',padx=4);self.console_buttons.append(button)
        ttk.Button(actions,text='停止・保存',command=self.request_stop).pack(side='left')
        self.console_label=tk.StringVar(value='コンソール操作を開始してください。');ttk.Label(window,textvariable=self.console_label,padding=8).pack()
        window.protocol('WM_DELETE_WINDOW',self.close_console)
        self.console_status(bool(getattr(self,'console_commands',None) and self.console_commands.ready))
    def close_console(self):
        if getattr(self,'console_active',False) and getattr(self,'console_commands',None) and not self.console_commands.closed:self.request_stop()
        self.console_window.destroy();self.console_window=None
    def console_status(self,ready):
        intervention=bool(self.busy() and not getattr(self,'recording_human',False) and getattr(self,'interventions',None) and self.interventions.ready)
        allowed=intervention or bool(ready and not self.stop.is_set() and not (self.control and self.control.is_set()))
        if hasattr(self,'console_start') and self.console_start.winfo_exists():self.console_start.configure(state='disabled' if self.busy() else 'normal')
        for button in getattr(self,'console_buttons',[]):
            try:button.configure(state='normal' if allowed else 'disabled')
            except tk.TclError:pass
        if getattr(self,'console_window',None) is not None and self.console_window.winfo_exists():
            self.console_label.set('攻略に割り込んで入力できます。' if intervention else '入力できます。' if allowed else '準備・観測・待機中、または停止中です。')
    def console_send(self,keys):
        intervention=bool(self.busy() and not getattr(self,'recording_human',False))
        commands=getattr(self,'interventions',None) if intervention else getattr(self,'console_commands',None)
        if intervention and commands and not self.stop.is_set():
            try:
                if commands.submit(keys,self.console_hold.get(),self.console_wait.get()):
                    self.control.intervention.set()
                    self.console_status(False);self.console_label.set('割込受付。安全に切り替えて送信します。')
            except (ValueError,tk.TclError) as error:self.console_label.set(str(error))
            return
        if not commands or self.stop.is_set() or (self.control and self.control.is_set()):return
        try:
            if commands.submit(keys,self.console_hold.get(),self.console_wait.get()):self.console_status(False)
        except (ValueError,tk.TclError) as error:self.console_label.set(str(error))

    def start(self,demonstration=False,console=False):
        if self.busy(): return
        self.recording_human=demonstration or console;self.console_active=console
        try:
            cfg=self.settings();DATA.mkdir(parents=True,exist_ok=True)
            temporary=DATA/'settings.json.tmp';temporary.write_text(json.dumps(asdict(cfg),ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(DATA/'settings.json')
            if self.selected_target:self.profiles.save(self.selected_target,cfg)
            self.cfg=cfg;self.save_ui()
            from .dashboard import LearningTraces
            self.traces=LearningTraces()
            seconds={'なし':0,'10分':600,'20分':1200,'30分':1800,'60分':3600}[self.duration.get()]
        except Exception as error:messagebox.showerror('設定エラー',str(error));return
        if console:
            from .console_play import ConsoleCommands
            self.console_commands=ConsoleCommands(cfg)
        selected_target=dict(self.selected_target) if self.selected_target else None
        dry=self.dry.get() and not (demonstration or console);separation=self.separation.get() and not (demonstration or console);compat=self.compat.get() and not dry and not (demonstration or console)
        # Separation now uses only neutral OS probes and observation.
        # State-independent diagnostics never steal focus or send gameplay keys.
        from .session import SessionControl
        from .console_play import ConsoleCommands
        self.interventions=ConsoleCommands(cfg);self.intervention_capable=not (demonstration or console or dry or separation)
        self.stop.clear();self.control=SessionControl(self.stop,0 if separation else seconds)
        control=self.control;self.status.set('お手本記録：ゲームを手動で前面にして操作してください。ツールは入力しません。停止で保存・ZIP生成。' if demonstration else '攻略を開始しています。停止ボタンで中断できます。');self.run_started=time.time();self.run_finished=None;self.run_counts={'decisions':0,'sent':0,'changed':0,'retries':0}
        def work():
            from .windows import WindowsGame
            from .recording import FrameRecorder,RecordedBackend
            from .diagnostics import DiagnosticStore,InputDiagnostics,export_diagnostics
            from . import __version__
            session=str(time.time_ns());summary={'app_version':__version__,'id':session,'started':time.time(),'status':'running','kind':'human_console' if console else 'demonstration' if demonstration else 'separation' if separation else 'play','dry_run':dry,'reason':'','model':cfg.model,'goal':cfg.goal,'ocr_engine':cfg.ocr_engine}
            recorder=None;backend=None;monitor_done=threading.Event();memory=None;diagnostic_store=None;exploration_run=None
            try:
                from .recovery import TargetRecovery,TargetUnavailable,ObservationRetry
                from .background import BackgroundUnavailable
                from .capture_service import CaptureTimeout
                from .cancellation import OperationCancelled
                from .windows import emergency_stop_requested
                def monitor():
                    while not monitor_done.wait(.03):
                        if emergency_stop_requested():self.stop.set()
                threading.Thread(target=monitor,daemon=True).start()
                def recovery_event(row):
                    self.queue.put(('decision',row));self.queue.put(('status',row.get('reason') or ('復旧: '+row.get('status',''))))
                    folder=DATA/'runs';folder.mkdir(parents=True,exist_ok=True)
                    with (folder/(session+'.jsonl')).open('a',encoding='utf-8') as stream:
                        stream.write(json.dumps(row,ensure_ascii=False)+'\n')
                recovery=TargetRecovery(selected_target,recovery_event) if selected_target and cfg.resilient and not dry and not separation and not demonstration and not console else None
                def reconnect(target):
                    while control.checkpoint():
                        try:
                            value=self.open_target(cfg.window_title,target) if target else self.backend if self.backend and self.connection_title==cfg.window_title else WindowsGame(cfg.window_title)
                            value.window_capture.cancel=control.stop
                            value.capture()
                            if recovery:recovery.target.update(pid=value.process_id.value,hwnd=int(value.hwnd))
                            return value
                        except TargetUnavailable:
                            if not recovery:raise
                            target=recovery.recover(control)
                            if not target:return None
                        except CaptureTimeout as error:
                            if not cfg.resilient:raise
                            recovery_event({'event':'capture_timeout','reason':str(error),'learning':False,'status':'retrying'})
                            control.wait(2)
                    return None
                backend=reconnect(selected_target)
                if backend is None:summary.update(status='stopped',reason='復旧待機を停止');return
                self.queue.put(('connected',(backend,cfg.window_title)))
                summary['connection']={'hwnd':int(backend.hwnd),'process_id':backend.process_id.value,
                    'window_title':backend._text(backend.hwnd),'capture':'PrintWindow','input':'window_message'}
                recorder=FrameRecorder(DATA/'recordings'/session);recorded=RecordedBackend(backend,recorder)
                try:recorded.record_frame(backend.capture(),'session_start')
                except TargetUnavailable:
                    backend=reconnect(selected_target)
                    if backend is None:summary.update(status='stopped',reason='復旧待機を停止');return
                    recorded=RecordedBackend(backend,recorder)
                if separation or compat:
                    passed=False
                    while control.checkpoint():
                        store=DiagnosticStore(DATA/'diagnostics.sqlite')
                        try:
                            runner=InputDiagnostics(recorded,store,lambda row:self.queue.put(('decision',row)),control)
                            passed=runner.run(separation);break
                        except TargetUnavailable:
                            if not recovery:raise
                            target=recovery.recover(control)
                            if not target:break
                            backend=reconnect(target)
                            if backend is None:break
                            recorded=RecordedBackend(backend,recorder);self.queue.put(('connected',(backend,cfg.window_title)))
                        finally:store.close();self.queue.put(('close_probe',None))
                    if not passed:summary.update(status='stopped',reason='診断を中断しました。');return
                    if separation:summary.update(status='diagnostic_completed',reason='10分間のOS経路・無入力観測を完了。ゲーム反応・キー分離は未検証。');return
                if not control.checkpoint():summary.update(status='stopped',reason=control.reason or '停止要求');return
                memory=MemoryStore(DATA/'memory.sqlite',cfg) if console or demonstration else None
                if console:
                    from .console_play import ConsolePlayer
                    from .demonstrations import DemonstrationStore
                    from .ocr import ScreenOCR
                    summary['connection']['input']='console_window_message_only'
                    runner=ConsolePlayer(recorded,DemonstrationStore(memory,cfg),ScreenOCR(cfg.ocr_engine),control,self.console_commands,
                        lambda row:self.queue.put(('decision',row)),DATA/'runs'/(session+'.jsonl'),recorder)
                    summary['console']=runner.run()
                    summary.update(status='stopped',reason=control.reason or 'コンソール操作を停止・保存しました。')
                    return
                if demonstration:
                    from .demonstrations import DemonstrationStore,DemonstrationRecorder
                    from .ocr import ScreenOCR
                    summary['connection']['input']='human_observation_only'
                    self.queue.put(('status','お手本記録中：ゲームを手動で前面にして操作。操作の合間に少し手を離すと前後の例を保存します。停止で終了・ZIP生成。'))
                    runner=DemonstrationRecorder(recorded,DemonstrationStore(memory,cfg),ScreenOCR(cfg.ocr_engine),control,
                        lambda row:self.queue.put(('decision',row)),DATA/'runs'/(session+'.jsonl'),recorder)
                    summary['demonstration']=runner.run()
                    summary.update(status='stopped',reason=control.reason or summary['demonstration'].get('stop_reason') or 'お手本記録を停止・保存しました。')
                    return
                diagnostic_store=DiagnosticStore(DATA/'diagnostics.sqlite');exploration_run=diagnostic_store.start('exploration_statistics')
                from dataclasses import replace
                from .research import public_references,reference_context
                self.queue.put(('status','公開資料の候補を検索します。画像や操作履歴は送信しません。'))
                references=public_references(cfg.window_title,cfg.profile_id,DATA/'research',control)
                recovery_event({'event':'public_reference_lookup','status':references['status'],
                    'sources':[{k:s[k] for k in ('title','url','applicability')} for s in references.get('sources',[])],
                    'learning':False})
                from .cancellation import OperationCancelled
                self.interventions.set_ready(self.intervention_capable)
                total_steps=0;retry=0;agent=None
                def agent_event(row):
                    nonlocal total_steps,retry
                    if row.get('event')=='result':
                        retry=0;total_steps+=1
                        row={**row,'controller_step':row.get('step'),'step':total_steps}
                    elif row.get('event')=='decision':row={**row,'controller_step':row.get('step'),'step':total_steps+1}
                    self.queue.put(('decision',row))
                self.queue.put(('status','現在の画面から目標と操作を判断します。未知の状況は未確認として記録します。'))
                while control.checkpoint() and (not cfg.max_steps or total_steps<cfg.max_steps):
                    runtime_cfg=replace(cfg,max_steps=max(1,cfg.max_steps-total_steps) if cfg.max_steps else 0)
                    # New controller/LLM after an interruption: never reuse pending input,
                    # neutral baseline, motion masks, or an old goal-completion verdict.
                    if memory:memory.last_row=None
                    if hasattr(backend,'observer'):del backend.observer
                    try:
                        llm=LocalLLM(runtime_cfg);llm.operator_guidance=self.operator_guidance;llm.public_references=reference_context(references)
                        from .nextgen.controller import NextGenController
                        agent=NextGenController(runtime_cfg,recorded,llm,agent_event,DATA/'runs'/(session+'.jsonl'),self.stop,memory,control,lambda row:diagnostic_store.cycle(exploration_run,row))
                        agent.run(dry);break
                    except OperationCancelled:
                        if not control.intervention.is_set():raise
                        # The automatic controller has unwound and released its
                        # keys. Only this worker can now send the manual input.
                        control.intervention.clear()
                        command=self.interventions.queue.get_nowait()
                        command.update(intervention=True,source='manual_intervention')
                        from .console_play import ConsolePlayer
                        from .demonstrations import DemonstrationStore
                        from .ocr import ScreenOCR
                        if memory is None:memory=MemoryStore(DATA/'memory.sqlite',cfg)
                        manual_control=SessionControl(self.stop)
                        runner=ConsolePlayer(recorded,DemonstrationStore(memory,cfg),ScreenOCR(cfg.ocr_engine),manual_control,self.interventions,
                            lambda row:self.queue.put(('decision',row)),DATA/'runs'/(session+'.jsonl'),recorder)
                        previous_cancel=backend.window_capture.cancel
                        backend.window_capture.cancel=manual_control
                        try:
                            runner.log({'event':'human_intervention_begin',**command,'pending_autonomous_input':'discarded'})
                            runner.execute(command)
                            runner.log({'event':'human_intervention_end',**command})
                        finally:
                            backend.window_capture.cancel=previous_cancel
                            manual_control.close();memory.last_row=None;memory.runtime_world=None;memory.runtime_navigation=None
                            self.interventions.set_ready(not self.stop.is_set())
                        continue
                    except TargetUnavailable:
                        if memory:memory.runtime_navigation=None;memory.runtime_world=None
                        if not recovery:raise
                        target=recovery.recover(control)
                        if not target:break
                        backend=reconnect(target)
                        if backend is None:break
                        recorded=RecordedBackend(backend,recorder)
                        self.queue.put(('connected',(backend,cfg.window_title)))
                        summary['reconnections']=summary.get('reconnections',0)+1
                    except BackgroundUnavailable:raise
                    except OperationCancelled:
                        if not control.checkpoint():break
                        continue
                    except Exception as error:
                        if self.stop.is_set():break
                        if not cfg.resilient or dry:raise
                        retry+=1
                        recovery_event({'event':'observation_recovery' if isinstance(error,ObservationRetry) else 'system_recovery',
                            'reason':str(error),'attempt':retry,'learning':False,
                            'note':'キーを送らず待機。ゲーム終了の根拠がなければ再起動しません。'})
                        # Finite work per attempt, interruptible exponential backoff.
                        control.wait(min(30,5*2**min(retry-1,3)))
                completed=bool(agent and agent.completed)
                summary.update(status='verified_complete' if completed else 'stopped',reason='別観測で目標達成を確認' if completed else control.reason or ('停止要求' if self.stop.is_set() else 'ループ終了・手数上限または安全条件'))
                self.queue.put(('memory',{'provisional_claims':getattr(agent,'summary',{}),'known_transitions':[]}))
                cumulative=getattr(agent,'summary',{}).get('steps',0)
                self.queue.put(('cumulative',cumulative))
            except Exception as error:
                from .background import BackgroundUnavailable
                from .cancellation import OperationCancelled
                summary.update(status='stopped' if isinstance(error,OperationCancelled) else 'unsupported_background' if isinstance(error,BackgroundUnavailable) else 'error',reason=(control.reason or str(error)) if isinstance(error,OperationCancelled) else str(error))
                if not isinstance(error,OperationCancelled):self.queue.put(('error',str(error)))
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
                        if not demonstration:backend.release_all()
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
    def follow_log_latest(self):
        if self.log_follow_latest.get():self.log.see('end')

    def append_log(self,value):
        from .dashboard import append_text
        event=value.get('event','')
        if event in ('llm_activity','perception','decision','sent'):return
        if event=='result':
            learning=value.get('input_coverage',{})
            text=(f"手 {value.get('step','?')} | {value.get('subgoal','')}\n"
                f"入力: {value.get('observed_keys',[])} / {value.get('hold_ms','?')}ms | {value.get('reason','')}\n"
                f"結果: 変化={value.get('changed')} / {value.get('turn_kind','')} | 観測={learning.get('observed','?')}回（因果は未確定）\n"
                f"処理: {value.get('performance',{}).get('total_ms','?')}ms / 学習選択: {value.get('active_input',{}).get('source','方策・モデル')}\n")
            if value.get('generation')==2:
                ng=value['nextgen'];text+=f"予測一致: {ng['result'].get('match')} / 不確実性: {ng['metrics']['uncertainty']:.3f} / 新規状態: {ng['result'].get('new_state')}\n選択: 情報利得 {ng['selected']['information_gain_proxy']:.3f} / 目標価値 {ng['selected']['expected_goal_value']:.3f} / 観測上の損失リスク {ng['selected']['observed_loss_risk']:.3f}\n"
            if value.get('item_description_observation'):
                item=value['item_description_observation'];text+=f"品の説明: {item.get('item_name','未確定')} / {item.get('status')} / {item.get('quotes',[])}\n"
            stages=value.get('performance',{}).get('stages_ms',{})
            if stages:text+=f"内訳ms: OCR {stages.get('ocr_ms','?')} / 地図取得 {stages.get('map_capture_ms',0)} / 自然観測 {stages.get('neutral_observation_ms',0)} / 入力後観測 {stages.get('input_response_observation_ms',0)} / 学習・保存 {stages.get('learning_save_ms',0)}\n"
            if value.get('supervision'):
                sup=value['supervision'];op=value.get('operation',{});evaluation=value.get('operation_evaluation',{})
                text+=f"監督: {op.get('skill')} / {op.get('selected_source')} / 段階 {op.get('execution_stage','未記録')} / 計画残り{sup.get('remaining')}手 / 推論{'中' if sup.get('review_running') else '待機'} / 方針採用{sup.get('adoptions')}回\n"
                text+=f"作用照合: {value.get('action_effect_learning',{}).get('role')} / 進行={evaluation.get('progress')} / 準備={evaluation.get('preparation')} / 検証={value.get('operational_experiment')}\n"
            if value.get('learning_connection'):
                from .learning_connection import summary as learning_summary
                text+=learning_summary(value['learning_connection'])+'\n'
            prediction=(value.get('learned_predictions') or {}).get('selected')
            if prediction:
                result=(value.get('learned_prediction_evaluation') or {}).get('status','未検証')
                text+=f"経験予測: {prediction['trials']}件 / 観測済み{prediction['supported_horizon']}手先 / 期待評価 {prediction['expected_return']:.3f} / 照合 {result}\n"
            combat=value.get('combat_evaluation') or {}
            if combat.get('status')=='updated':text+=f"戦闘学習: 評価 {combat['reward']:.3f} / 予測誤差 {combat.get('prediction_error')}\n"
            visual=value.get('visual_response',{})
            if visual.get('status')=='observed':text+=f"画像反応: 大きな変化={visual.get('context_change')} / 変化未確認 {visual.get('stagnant_turns',0)}手 / 一時保留 {value.get('visual_action_evidence',{}).get('blocked_actions',[])}（攻略成果は未確認）\n"
        elif event in ('loop_detected','loop_escape'):
            text=f"反復検出 / 脱出 | {event} #{value.get('id')} | 手 {value.get('steps',[])}\n種類: {value.get('kind','')} / 周期 {value.get('period','')} / {value.get('reason','別の局所目標へ切り替えます')}\n"
        elif str(event).startswith('human_'):
            text=f"人間操作 {event} | 入力 {value.get('keys',[])} | {value.get('reason','')}\n"
        elif event in ('error','diagnostic_export','observation_recovery','self_improvement','supervisor_error','supervisor_discarded','supervisor_protocol_warning','supervisor_local_goal'):
            text=f"{event}: {value.get('reason') or value.get('status') or value.get('path','')}\n"
        else:return
        append_text(self.log,text+'─'*35+'\n')
        self.follow_log_latest()

    def update_decision(self,value):
        event=value.get('event');self.recognition.update(value);self.traces.update(value)
        digest=getattr(self,'observer_digest',None)
        if digest and digest.update(value):
            self.set_panel('判断の要点',digest.summary());self.set_panel('観察ダイジェスト',digest.journal())
        if event in ('llm_activity','perception'):return
        self.append_log(value)
        if value.get('method_selection'):
            self.set_panel('ゲームの推定',value['method_selection'].get('game_hypothesis',{}))
            self.set_panel('手法の選択・学習',value.get('method_context') or value['method_selection'])
        if value.get('background_compatibility'):self.set_panel('背景適合性',value['background_compatibility'])
        if isinstance(event,str) and event.startswith('human_'):
            self.set_panel('行動学習',value)
            if event=='human_console_result' and value.get('keys'):self.run_counts['sent']+=1
            if event=='human_console_ready':
                self.console_status(value['ready']);return
            if not self.stop.is_set():
                if event=='human_console_result':self.status.set('操作結果を保存しました。待機が終わると次の操作ができます。')
                elif event=='human_focus':self.status.set(value.get('reason') or ('お手本記録中：対象ゲームの操作だけを記録します。' if value['target_focused'] else 'お手本記録待機：ゲームを手動で前面にしてください。他アプリのキーは記録しません。'))
                elif event=='human_example_processed':self.status.set('お手本記録中：例 #'+str(value['example_id'])+' / '+value['status']+'。停止で保存・ZIP生成。')
            return
        if event in ('diagnostic','diagnostic_summary','diagnostic_skipped'):self.set_panel('結果',value);return
        if event in ('loop_detected','loop_escape'):
            self.set_panel('自己改善',value);self.status.set('反復を検出し、実行器が局所目標を切り替えます。' if event=='loop_detected' else '反復脱出の結果: '+value.get('reason','未確認'));return
        if event=='self_improvement':self.set_panel('自己改善',value);return
        if event=='supervisor_plan':
            self.set_panel('運用計画',value['plan']);return
        if event=='decision' and value.get('generation')==2:
            self.run_counts['decisions']+=1;self.last_row=value;self.status.set(value['subgoal'])
            self.set_panel('目的',{'全体目標':self.cfg.goal,'局所目標':value['subgoal']})
            self.set_panel('選択行動と理由',{'入力':value['keys'],'保持ms':value['hold_ms'],'理由':value['reason']})
            self.set_panel('候補と評価',value['nextgen']['candidates']);return
        if event=='result' and value.get('generation')==2:
            ng=value['nextgen'];m=ng['metrics'];r=ng['result'];selected=ng['selected']
            def number(key):
                v=m.get(key);return '未評価' if v is None else f'{v:.3g}'
            self.set_panel('行動学習',f"事前予測一致率: {number('prediction_accuracy')} / 不確実性: {number('uncertainty')}\n新規情報: {number('information_gain')} / 反復率: {number('repetition_rate')}\nLLM要求/手: {number('llm_intervention_rate')} / 選択時間: {number('decision_ms')}ms\n規則: {r.get('rule_status','未学習')} / 条件細分化: {r.get('condition_split_node')}\n観測上の局所目標: {number('goal_progress')}（ゲームクリアとは別）")
            self.set_panel('仮説',{'作用の仮説':ng['hypotheses'],'外見の未確認解釈':ng.get('semantic_interpretations',[])});self.set_panel('候補と評価',ng['candidates'])
            self.set_panel('目的',ng['objective']);self.set_panel('運用計画',value.get('supervision'))
            self.set_panel('現在状態',{'現在観測':value['policy_after']['metrics'],'保持した過去値':value['policy_after'].get('retained'),'画面に出ている許可外キー候補':value['policy_after'].get('unavailable_input_hints',[]),'状態識別':value['policy_after']['state_identification'],'匿名の追跡候補':value['policy_after']['objects']})
            self.set_panel('自己改善',ng['parameters']);self.set_panel('結果',{'予測':ng['prediction_before_input'],'実測':r.get('effects'),'一致':r.get('match'),'照合した項目':r.get('evaluated_features'),'欠測または未予測で対象外':r.get('unscored_features'),'因果':r.get('attribution'),'遅延':r.get('delayed'),'処理時間':value['performance']})
            self.run_counts['sent']+=int(value.get('executed',False));self.run_counts['changed']+=int(bool(value.get('changed')))
            return
        if event=='result':
            if value.get('supervision'):
                self.set_panel('運用計画',value['supervision'])
                self.set_panel('行動学習',{'入力前の予測':value.get('action_prediction'),'予測と実測の検証':value.get('prediction_validation'),'作用の学習':value.get('action_effect_learning'),'計画の照合':value.get('operation_evaluation'),'仮説検証':value.get('operational_experiment')})
                self.set_panel('候補と評価',value.get('operation',{}))
            if value.get('policy_choice') and not value.get('supervision'):

                self.set_panel('自己改善',{'予測制御':value.get('anticipation'),'予測照合':value.get('forecast_evaluation'),'state':value.get('self_improvement'),'evaluation':value.get('improvement_evaluation')})
                coverage=value.get('information_exploration',{});actions=coverage.get('actions',[]);ready=sum(a.get('observed',0)>=a.get('required',2) for a in actions)
                self.set_panel('行動学習',f"この状況の初期観測: {ready}/{len(actions)}候補が目安を充足\n収集中: {not coverage.get('initial_coverage_ready',False)} / 採用元: {value.get('active_input',{}).get('source','方策・モデル')}\n今回操作の観測回数: {value.get('input_coverage',{}).get('observed','不明')} / 報酬: {value.get('policy_learning',{}).get('reward','未採点')}\n観測回数や行動名だけで因果・成功を確定しません。")
                self.set_panel('候補と評価',value['policy_choice'].get('context',{}))
            self.run_counts['sent']+=int(value.get('executed',False));self.run_counts['changed']+=int(bool(value.get('changed')))
            self.set_panel('結果',{key:value.get(key) for key in ('step','action_kind','observed_keys','input_receipt','neutral_before','executed','changed','transition_attribution','turn_kind','duration_ms','raw_change','masked_activity','performance')})
            return
        if value.get('status')=='retry':self.run_counts['retries']+=1
        if 'scene' not in value:
            self.set_panel('現在状態',value);return
        self.run_counts['decisions']+=1;self.last_row=value
        self.status.set(f"{value.get('scene','')} / {value.get('subgoal','')}")
        self.set_panel('現在状態',{'独立した画面観察（モデル報告・未検証）':value.get('scene_observation',{}),'モデルの計画時の場面判断':(value.get('llm_proposal') or {}).get('scene',value.get('scene','')),'採用した場面仮説':value.get('scene',''),'採用根拠':value.get('scene_basis','model_report'),'採用した観察':value.get('observation','')})
        self.set_panel('目的','全体目標: '+self.cfg.goal+'\n今回の小目標: '+str(value.get('subgoal','未設定'))+'\n達成条件: '+str(value.get('goal_success','未設定')))
        self.set_panel('候補と評価',{'confidence':value.get('confidence'),'risk':value.get('risk'),'allowed_keys':self.cfg.allowed_keys,'learned_policy':value.get('policy_choice'),'note':'モデルの信頼度と、経験から計算した優先度・試行数は別の指標です。'})
        self.set_panel('選択行動と理由','入力予定: '+str(value.get('keys',[]))+' / '+str(value.get('hold_ms','?'))+'ms\n'+str(value.get('reason','')))
        self.set_panel('仮説',{'対象の仮説':value.get('object_hypotheses',[]),'モデルの報告':value.get('memory_updates',[])})
        self.set_panel('新発見',{'毎ターンの観測':value.get('world_observation',{}),'モデルの進捗報告':value.get('progress',[])})
        self.set_panel('修正履歴',value.get('knowledge_revisions',{}));self.set_panel('OCR・攻略知識の照合',{'ocr':value.get('ocr',{}),'knowledge_assessments':value.get('knowledge_assessments',[]),'note':'OCRと出典の照合は仮説。ゲーム内効果の実証とは区別します。'})

    def arm_watchdog(self):
        now=time.monotonic()
        if now-self.watchdog_at<5:return
        try:
            import faulthandler
            if self.watchdog_file is None:
                DATA.mkdir(parents=True,exist_ok=True)
                self.watchdog_file=(DATA/'hang-traces.log').open('a',encoding='utf-8')
            faulthandler.dump_traceback_later(20,file=self.watchdog_file)
            self.watchdog_at=now
        except (OSError,RuntimeError):pass

    def callback_error(self,kind,error,traceback_object):
        import traceback
        detail=''.join(traceback.format_exception(kind,error,traceback_object))
        control=getattr(self,'control',None)
        if control is not None:control.reason='表示処理のエラー: '+type(error).__name__+': '+str(error)[:300]
        self.stop.set()
        try:
            DATA.mkdir(parents=True,exist_ok=True)
            with (DATA/'ui-errors.log').open('a',encoding='utf-8') as stream:
                stream.write(time.strftime('%Y-%m-%d %H:%M:%S')+'\n'+detail+'\n')
        except OSError:pass
        try:self.status.set('表示処理のエラーを記録し攻略を停止しました。UIの更新は継続します。')
        except Exception:pass

    def write_ui_performance(self,seconds):
        now=time.monotonic()
        if now-getattr(self,'_ui_health_at',0)<10:return
        self._ui_health_at=now
        try:
            DATA.mkdir(parents=True,exist_ok=True)
            value={'at':time.time(),'version':__version__,'decision_update_ms':round(getattr(self,'ui_decision_ms',0),2),'poll_ms':round(seconds*1000,2),'preview_draw_ms':round(getattr(self,'preview_draw_seconds',0)*1000,2),'queue':self.queue.snapshot(),'trace_samples':len(self.traces.samples),'turn':self.traces.turn}
            path=DATA/'ui-performance.json';temporary=path.with_suffix('.tmp')
            temporary.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');temporary.replace(path)
        except (OSError,AttributeError):pass

    def resize_dashboard(self,event):
        if self.dashboard_dragging or event.width<=1 or event.width==self.dashboard_last_width:return
        self.dashboard_last_width=event.width
        self.root.after_idle(self.apply_dashboard_layout)

    def apply_dashboard_layout(self):
        if self.dashboard_dragging:return
        from .dashboard import dashboard_sashes
        width=self.dashboard_split.winfo_width()
        if width<=1:return
        for index,position in enumerate(dashboard_sashes(width,self.ui_state.get('dashboard_ratios'))):self.dashboard_split.sashpos(index,position)

    def save_dashboard(self,event=None):
        self.dashboard_dragging=False;width=self.dashboard_split.winfo_width()
        if width<=1:return
        self.ui_state['dashboard_ratios']=[self.dashboard_split.sashpos(i)/width for i in range(2)]
        try:self.save_ui()
        except OSError as error:self.status.set('表示設定の保存に失敗: '+str(error))

    def poll(self):
        started=time.monotonic()
        try:self.poll_once()
        except Exception:
            import sys
            self.callback_error(*sys.exc_info())
        finally:
            self.arm_watchdog()
            self.write_ui_performance(time.monotonic()-started)
            try:self.root.after(100,self.poll)
            except tk.TclError:pass

    def poll_once(self):
        try:
            deadline=time.monotonic()+.03
            for _ in range(20):
                if time.monotonic()>deadline:break
                kind,value=self.queue.get_nowait()
                if kind=='error':self.status.set('エラーで停止: '+value);self.set_panel('現在状態',{'status':'error','reason':value});self.append_log({'event':'error','reason':value})
                elif kind=='done':
                    self.control=None
                    self.recording_human=False;self.console_active=False
                    if getattr(self,'console_commands',None):self.console_commands.close()
                    if getattr(self,'interventions',None):self.interventions.close()
                    if hasattr(self,'console_buttons'):self.console_status(False)
                    if self.backend is not None and self.closing_at is None:self.backend.window_capture.cancel=None
                    self.start_button.configure(state='normal');self.pause_button.configure(text='⏸ 一時停止')
                    if hasattr(self,'demo_button'):self.demo_button.configure(state='normal')
                elif kind=='status':self.status.set(value)
                elif kind=='progress':self.progress.set(value)
                elif kind=='model':self.vars['model'].set(value);self.vars['provider'].set('ollama');self.progress.set('モデルの取得が完了しました。')
                elif kind=='decision':
                    update_started=time.monotonic();self.update_decision(value);self.ui_decision_ms=(time.monotonic()-update_started)*1000
                elif kind=='connected':
                    previous=self.backend
                    self.backend,self.connection_title=value
                    self.recognition.update({'event':'disconnected'})
                    if self.overlay:self.overlay.connect(self.backend)
                    if previous is not None and previous is not self.backend:self.close_capture(previous)
                    self.badges.set('接続済み / 背面分離 / OCR: '+self.cfg.ocr_engine)
                    self.set_panel('バックエンド状態',{'window':self.connection_title,'capture':'PrintWindow','input':'WindowMessage / ウィンドウ宛て（ゲーム受理とは別）','model':self.cfg.model,'endpoint':self.cfg.endpoint,'ocr':self.cfg.ocr_engine,'data':str(DATA)})
                    self.status.set('ゲームに接続しました。ライブ画面を取得しています。')
                elif kind=='close_probe':
                    if self.probe:self.probe.destroy();self.probe=None
                elif kind=='exported':self.latest_zip=Path(value);self.status.set(self.status.get().split('\n診断ZIP')[0]+'\n診断ZIPを生成しました。「提出用ZIPをコピー」で添付できます。');self.append_log({'event':'diagnostic_export','path':str(value)})
                elif kind=='export_error':self.status.set('診断ZIPの作成に失敗: '+value);self.append_log({'event':'export_error','reason':value})
                elif kind=='session':self.run_finished=value.get('ended',time.time());self.recognition.update({'event':'session_end'});self.set_panel('現在状態',value);self.status.set(value['status']+' / '+value['reason'])
                elif kind=='memory':
                    self.set_panel('仮説',value.get('provisional_claims',[]));self.set_panel('系列のパターン',value.get('known_transitions',[]))
                elif kind=='cumulative':self.set_panel('累積統計',{'saved_experiences':value,'note':'この目標・知識・ゲーム設定の経験。診断を除く。'})
        except queue.Empty:pass
        try:
            if getattr(self,'preview_visible',True):self.draw_frame();self.draw_learning_graph()
        except Exception as error:self.live_info.set('ライブ表示エラー: '+str(error))
        if getattr(self,'console_window',None) is not None and self.console_window.winfo_exists():self.console_status(bool(getattr(self,'console_commands',None) and self.console_commands.ready))
        self.recognition_panel.refresh()
        if self.recognition_floating and self.recognition_window.winfo_exists():self.recognition_floating.refresh()
        if self.overlay:
            try:self.overlay.refresh()
            except Exception as error:
                self.overlay.close();self.overlay=None;self.overlay_enabled.set(False);self.status.set('オーバーレイを停止しました: '+str(error))
        elapsed=int((self.run_finished or time.time())-self.run_started) if self.run_started else 0
        self.set_panel('今回統計',f"経過 {elapsed//3600:02d}:{elapsed//60%60:02d}:{elapsed%60:02d}   完了ターン {self.traces.turn}\n送信 {self.run_counts['sent']} / 画面変化 {self.run_counts['changed']} / 再試行 {self.run_counts['retries']}\n{'一時停止' if self.control and self.control.paused.is_set() else '実行中' if self.busy() else '停止'}")

    @staticmethod
    def close_capture(backend):
        capture=getattr(backend,'window_capture',None)
        if capture and hasattr(capture,'close'):threading.Thread(target=capture.close,daemon=True).start()

    def close(self):
        self.stop.set();self.preview_done.set()
        if self.closing_at is None:self.closing_at=time.monotonic()
        if self.busy() and time.monotonic()-self.closing_at<5:
            self.status.set('停止中。記録を保存しています（終了待ちは最大5秒）。');self.root.after(200,self.close)
            return
        if self.busy():
            try:
                DATA.mkdir(parents=True,exist_ok=True)
                with (DATA/'ui-errors.log').open('a',encoding='utf-8') as stream:
                    stream.write('終了待ち5秒超過。ゲームは終了せずUIを閉じます。診断ZIP未完の場合も元ログを保持。\n')
            except OSError:pass
        if self.control:self.control.close()
        if self.watchdog_file:
            import faulthandler
            faulthandler.cancel_dump_traceback_later();self.watchdog_file.close();self.watchdog_file=None
        if self.overlay:self.overlay.close();self.overlay=None
        if self.backend:self.close_capture(self.backend)
        try:self.save_ui()
        except (OSError,tk.TclError):pass
        self.root.destroy()

def main():
    if os.name=='nt':
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    root=tk.Tk(); Application(root); root.mainloop()
