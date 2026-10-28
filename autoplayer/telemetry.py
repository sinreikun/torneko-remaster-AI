"""Compact UI observations; retained values are explicitly marked as last observed."""
import copy
import time

class RecognitionState:
    def __init__(self):
        self.floor_exploration={};self.map={};self.tentative={};self.metrics={};self.objects=[];self.floor_state={};self.remaining=None
        self.inference=set();self.observed_at=None;self.phase='待機';self.revision=0;self.autonomy={};self.ui_visual={}
    @property
    def thinking(self):return bool(self.inference)
    def update(self,row):
        event=row.get('event')
        if row.get('autonomy'):self.autonomy=copy.deepcopy(row['autonomy'])
        elif event=='autonomy_update':self.autonomy=copy.deepcopy(row.get('state',{}))
        if 'mapless_navigation' in row:self.tentative=copy.deepcopy(row['mapless_navigation'])
        if event=='llm_activity':
            request=row.get('request_id','default')
            if row.get('active'):self.inference.add(request);self.remaining=0
            else:self.inference.discard(request)
            self.phase='LLM分析中' if self.thinking else '観測・実行'
            self.revision+=1;return
        if event=='guidance_pending':
            self.remaining=0;self.phase='作戦指示の反映待ち';self.revision+=1;return
        if event in ('session_end','disconnected'):
            self.inference.clear();self.remaining=None;self.objects=[];self.phase='停止'
            if event=='disconnected':self.floor_exploration={};self.tentative={};self.map={};self.metrics={};self.objects=[];self.observed_at=None
            self.revision+=1;return
        state=row.get('policy_after') if event=='result' else row.get('policy_state')
        world=row.get('world_observation') or {}
        if state is None and event=='human_console_result':
            after=row.get('after_features') or {}
            state={'minimap':{'status':'unavailable'},'metrics':{k:{'values':after[k]} for k in ('hp','floor','fullness') if after.get(k) is not None}}
        if state is not None:
            self.ui_visual=copy.deepcopy(state.get('ui_visual',{}))
            confirmation=state.get('floor_confirmation',{})
            old_floor=self.metrics.get('floor',{}).get('values');new_floor=state.get('metrics',{}).get('floor',{}).get('values')
            if confirmation.get('changed') or world.get('new_adventure') or (old_floor and new_floor and old_floor!=new_floor and confirmation.get('status') in ('initialized','confirmed','changed')):
                self.map={};self.objects=[];self.floor_exploration={}
            if world.get('new_adventure'):self.metrics={}
            if state.get('floor_exploration',{}).get('status')=='observed_floor':self.floor_exploration=copy.deepcopy(state['floor_exploration'])
            observed_map=state.get('minimap',{})
            if observed_map.get('status')=='observed':self.map=copy.deepcopy(observed_map)
            elif self.map.get('status')=='observed':self.map['retained']=True
            fresh=copy.deepcopy(state.get('metrics',{}))
            retained={k:{**v,'retained':True} for k,v in self.metrics.items() if k in ('strength','sword','shield','experience','max_floor','level','gold') and k not in fresh}
            self.metrics={**retained,**fresh}
            self.floor_state=copy.deepcopy(state.get('floor_confirmation',{}))
            self.objects=[] if event=='result' else copy.deepcopy(world.get('objects',[]))
            self.observed_at=time.monotonic();self.phase='保存地図候補・地図照合待ち' if event=='monitor_checkpoint_preview' else '観測・実行'
        elif world and event=='perception':
            self.map={};self.metrics={k:{'values':v} for k,v in world.get('metrics',{}).items()};self.objects=copy.deepcopy(world.get('objects',[]));self.observed_at=time.monotonic()
        evaluation=row.get('forecast_evaluation')
        anticipation=row.get('anticipation')
        if evaluation is not None:
            self.remaining=evaluation.get('remaining',0) if evaluation.get('status')=='matched' else 0
        elif anticipation is not None:
            self.remaining=max(0,int(anticipation.get('remaining',0)))
        # Forecast counts successful future inputs before the next analysis;
        # a perception event is emitted before deciding the current input.
        if self.floor_state.get('status') in ('pending','unreadable'):self.remaining=0
        if row.get('fast_learning'):self.remaining=row['fast_learning'].get('next_review_in',0)
        if row.get('supervision'):self.remaining=0 if row['supervision'].get('review_running') else row['supervision'].get('review_in',0)
        self.revision+=1
    def grid(self):
        if self.tentative.get('active') or self.tentative.get('display_retained'):
            t=self.tentative;px,py=t.get('pose',[0,0]);out={}
            def project(c):return (c[0]-px+27,c[1]-py+16)
            for c in t.get('cells',[]):
                cell=project(c['cell'])
                if valid(cell):out[cell]='tentative'
            for c in t.get('obstacles',[]):
                cell=project(c)
                if valid(cell) and cell not in out:out[cell]='obstacle'
            directions={'w':(0,-1),'d':(1,0),'s':(0,1),'a':(-1,0)}
            for portal in t.get('portals',[]):
                origin=portal.get('cell');delta=directions.get(portal.get('direction'))
                if origin and delta:
                    cell=project([origin[0]+delta[0],origin[1]+delta[1]])
                    if valid(cell):out[cell]='portal'
            out[(27,16)]='uncertain_pose' if t.get('pose_uncertain') else 'estimated_pose'
            return out
        m=self.map
        if m.get('status')!='observed' or m.get('grid_size')!=[54,32]:return {}
        out={};player=m.get('player_cell')
        for cell in m.get('accumulated_cells',[]):
            if valid(cell):out[tuple(cell)]='known'
        if valid(player):
            for offset in m.get('known_cells',[]):
                cell=[player[i]+offset[i] for i in (0,1)]
                if valid(cell):out[tuple(cell)]='known'
            for field,kind in [('exploration_unnecessary_cells','surveyed'),('exhausted_branch_cells','exhausted'),('disputed_cached_cells','disputed'),('route_cells','route'),('frontier_cells','frontier')]:
                for cell in self.floor_exploration.get(field,[]):
                    if valid(cell) and tuple(cell) in out:out[tuple(cell)]=kind
            for field,kind in [('stairs','stairs'),('items','item'),('uncertain_marks','unknown_marker'),('enemies','enemy')]:
                for offset in m.get(field,[]):
                    cell=[player[i]+offset[i] for i in (0,1)]
                    if valid(cell):out[tuple(cell)]=kind
            out[tuple(player)]='player'
        return out
    def item_detail(self,cell):
        if self.tentative.get('active') or self.tentative.get('display_retained'):return '仮経路マップ。ダンジョン内の品とは別の座標です。'
        player=self.map.get('player_cell')
        if player is None:return f'{list(cell)}: 現在のマップ未確認'
        offset=[cell[0]-player[0],cell[1]-player[1]]
        kind=next((label for field,label in [('items','アイテム'),('enemies','敵'),('stairs','階段'),('uncertain_marks','未分類マーク')] if offset in [list(p) for p in self.map.get(field,[])]),None)
        seen='最後のマップ観測を保持・未再確認' if self.map.get('retained') else '最新のマップ観測'
        return f"{list(cell)}: {kind or 'アイコンなし'} / {seen}"
    def summary(self):
        def val(name):
            values=self.metrics.get(name,{}).get('values')
            if not values:return '不明'
            metric=self.metrics[name];mark='保' if metric.get('retained') else '仮' if metric.get('hypothesis') else '推' if metric.get('estimated') else '読' if metric.get('unverified_ocr') else '観'
            return '/'.join(str(v) for v in values)+'['+mark+']'
        age='未観測' if self.observed_at is None else f'{int(time.monotonic()-self.observed_at)}秒前'
        count='分析中' if self.thinking else '未設定' if self.remaining is None else '次の判断で分析' if self.remaining==0 else f'あと{self.remaining}手（状況変化で前倒し）'
        retained=' / '.join({'strength':'ちから','sword':'剣','shield':'盾','experience':'経験値','max_floor':'最深階','level':'Lv','gold':'所持金'}.get(k,k) for k,v in self.metrics.items() if v.get('retained'))
        note='\n保持値（未再確認）: '+retained if retained else ''
        if self.tentative.get('active') or self.tentative.get('display_retained'):note+='\n仮経路地図 / '+self.tentative.get('mode','')+' / '+self.tentative.get('calibration','')+(' / 白枠あり:地形更新停止' if self.tentative.get('white_frame_evidence',{}).get('visible') else '')+'\n'+self.tentative.get('unit','仮の移動単位・実マス未保証')+' / 黄枠:推定位置 橙:障害候補'
        if self.autonomy:
            a=self.autonomy;g=a.get('current_goal') or {};r=a.get('recovery') or {}
            note+='\n進行[推定]: '+str(a.get('phase','未確認'))[:90]+'\n目標[計画]: '+str(g.get('label','未設定'))[:100]
            note+='\n不足能力: '+','.join(a.get('missing_capabilities',[]))+' / 復旧: '+str(r.get('method','なし'))
            note+='\n観=観測 読=OCR未検証 保=保持 推=推定 仮=仮説。未知は空欄。'
        if self.ui_visual:
            selection=self.ui_visual.get('selection',{})
            note+='\nUI選択[関連仮説]: '+str(selection.get('quote') or selection.get('element_id') or '不明')+' / '+str(round(selection.get('confidence',0),2))
            note+=' / 位置付き文字 '+str(len(self.ui_visual.get('elements',[])))+' / 未解決 '+str(len(self.ui_visual.get('needs_observation',[])))
        f=self.floor_exploration
        if f.get('status')=='observed_floor' and not self.tentative.get('active'):
            goal=f.get('goal',{});phase=goal.get('phase','')
            title={'inspect_unclassified_marker':'未分類マークを確認','handle_visible_enemy':'見えている敵に対処','explore_frontier':'未処理先端を探索','reach_stairs':'観測済み階段へ','return_to_stairs':'帰還のため階段へ','collect_visible_items':'観測済みの品を確認','verify_failed_route':'通過実績のある経路を再検証','route_unavailable':'経路を再検討','review_exhausted_floor':'階内の次の目的を再検討','verify_objective':'所持品・目標を再確認','verify_stair_action':'階段の操作結果を再確認'}.get(phase,phase)
            note+=f"\n階内目標: {title} {goal.get('target_cell') or ''}\n既知 {f.get('known_count',0)} / 未処理先端 {f.get('primary_frontier_count',0)} / 完了枝 {len(f.get('exhausted_branch_cells',[]))}"
        task=f.get('exploration_task')
        if task and not (self.tentative.get('active') or self.tentative.get('display_retained')):
            status={'navigating':'移動中','probing':'先端を確認','detour_search':'迂回探索','interrupted':'割込み後に再検討','blocked':'到達不能・再検討'}.get(task.get('status'),task.get('status',''))
            note+=f"\n継続探索目標: {task.get('target_cell')} / {status}"

        atlas=self.tentative.get('atlas',{})
        if atlas.get('map_id'):note+='\n外部地図 '+atlas['map_id']+(' / 保存済み開始地点から再開' if atlas.get('reused') else ' / 新規地図')
        if atlas.get('awaiting_entry'):note+='\n場面切替：開始地点の再観測待ち'
        return f"階 {val('floor')}   Lv {val('level')}   HP {val('hp')}   満腹度 {val('fullness')}   ちから {val('strength')}   剣 {val('sword')}   盾 {val('shield')}   経験値 {val('experience')}   最深階 {val('max_floor')}   所持金 {val('gold')}\n次のLLM：{count} / {self.phase} / 最終観測 {age} / 地図：{'仮経路（実地図と別座標）' if self.tentative.get('active') or self.tentative.get('display_retained') else '確認済み' if self.map.get('status')=='observed' else '取得・階確認待ち'}{'（最終観測を保持）' if self.map.get('retained') and not self.tentative.get('active') else ''}{note}"

def valid(cell):return isinstance(cell,(tuple,list)) and len(cell)==2 and all(type(v) is int for v in cell) and 0<=cell[0]<54 and 0<=cell[1]<32

COLORS={'unknown_marker':'#b9a4ed','disputed':'#633e40','surveyed':'#294052','exhausted':'#665273','frontier':'#70bd7d','route':'#7da8cc','portal':'#172033','tentative':'#276d70','obstacle':'#be7939','estimated_pose':'#172a30','uncertain_pose':'#833f6c','known':'#365e83','player':'#ffe04b','enemy':'#ff685e','item':'#3de5dc','stairs':'#73bcff'}

def grid_layout(width,height):
    width=max(1,float(width));height=max(1,float(height));pad=min(8,width/4,height/4)
    unit=min((width-2*pad)/54,(height-2*pad)/32)
    return ((width-54*unit)/2,(height-32*unit)/2,unit)

def grid_cell(px,py,layout):
    x,y,unit=layout;col=int((px-x)//unit);row=int((py-y)//unit)
    return (col,row) if x<=px<x+54*unit and y<=py<y+32*unit and valid((col,row)) else None

def paint_grid(canvas,cells,x=8,y=8,unit=6):
    # All 1,728 grid cells are drawn, including unknown ones.
    for row in range(32):
        for col in range(54):
            kind=cells.get((col,row));a=x+col*unit;b=y+row*unit;inset=min(1,unit*.2);mark=min(2,unit*.3)
            canvas.create_rectangle(a,b,a+unit,b+unit,fill=COLORS['known'] if kind in ('enemy','item','stairs','unknown_marker') else COLORS.get(kind,'#121c2b'),outline='#263346',width=1,tags='recognition')
            if kind in ('estimated_pose','uncertain_pose'):canvas.create_rectangle(a+inset,b+inset,a+unit-inset,b+unit-inset,outline='#ffe04b',tags='recognition')
            elif kind=='portal':canvas.create_rectangle(a+inset,b+inset,a+unit-inset,b+unit-inset,outline='#cc8bff',tags='recognition')
            elif kind=='obstacle':canvas.create_line(a,b+unit,a+unit,b,fill='#ffbe63',tags='recognition')
            elif kind=='enemy':canvas.create_oval(a+inset,b+inset,a+unit-inset,b+unit-inset,fill=COLORS[kind],outline='',tags='recognition')
            elif kind=='item':canvas.create_polygon(a+unit/2,b,a+unit,b+unit/2,a+unit/2,b+unit,a,b+unit/2,fill=COLORS[kind],tags='recognition')
            elif kind=='unknown_marker':
                canvas.create_polygon(a+unit/2,b,a+unit,b+unit/2,a+unit/2,b+unit,a,b+unit/2,fill='',outline=COLORS[kind],tags='recognition')
                canvas.create_line(a+unit/2,b+mark,a+unit/2,b+unit-mark,fill=COLORS[kind],tags='recognition')
            elif kind=='stairs':canvas.create_rectangle(a+inset,b+inset,a+unit-inset,b+unit-inset,fill='#121c2b',outline=COLORS[kind],tags='recognition')

class RecognitionPanel:
    def __init__(self,parent,state):
        import tkinter as tk
        from tkinter import ttk
        self.state=state;self.frame=ttk.LabelFrame(parent,text='認識モニター（観測値・推定）',padding=5)
        top=ttk.Frame(self.frame);top.pack(fill='x')
        self.lamp=tk.Canvas(top,width=20,height=20,highlightthickness=0);self.lamp.pack(side='left')
        self.lamp.create_oval(3,3,17,17,fill='#394654',outline='',tags='lamp')
        self.label=ttk.Label(top,text='LLM待機');self.label.pack(side='left')
        self.canvas=tk.Canvas(self.frame,width=1,height=210,bg='#121c2b',highlightthickness=0);self.canvas.pack(fill='x')
        self.text=ttk.Label(self.frame,justify='left',wraplength=340);self.text.pack(fill='x')
        self.legend=ttk.Label(self.frame,wraplength=340,text='黄:自分 赤:敵 菱形:品 枠:階段 紫枠:場面切替\n緑:未確認方向の先端 青:観測床 薄青:経路 紫:調査済み枝 赤茶:再確認待ち\n品: 橙=満杯 黄枠=取得未確認 灰=保留 紫枠=未分類');self.legend.pack(fill='x')
        self.selected_cell=None
        self.detail=ttk.Label(self.frame,text='マスをクリックすると品/マークの状態と理由を表示',wraplength=340,justify='left');self.detail.pack(fill='x')
        self.canvas.bind('<Button-1>',self.select_cell)
        self.last_revision=-1;self.last_layout=None;self.last_width=None;self.redraw_pending=False
        self.canvas.bind('<Configure>',self.resize_canvas)
        self.frame.bind('<Configure>',self.resize_text)
    def resize_text(self,event):
        width=max(1,event.width-16)
        for widget in (self.text,self.legend,self.detail):widget.configure(wraplength=width)
    def resize_canvas(self,event):
        if event.width!=self.last_width:
            self.last_width=event.width
            self.canvas.configure(height=max(1,round(max(1,event.width-16)*32/54+16)))
        if not self.redraw_pending:
            self.redraw_pending=True;self.canvas.after_idle(self.redraw)
    def redraw(self):
        self.redraw_pending=False
        if self.canvas.winfo_exists():self.refresh()
    def select_cell(self,event):
        cell=grid_cell(event.x,event.y,grid_layout(self.canvas.winfo_width(),self.canvas.winfo_height()))
        if cell is not None:self.selected_cell=cell;self.detail.configure(text=self.state.item_detail(cell))
    def refresh(self):
        layout=grid_layout(self.canvas.winfo_width(),self.canvas.winfo_height())
        if self.last_revision!=self.state.revision or self.last_layout!=layout:
            if self.selected_cell is not None:self.detail.configure(text=self.state.item_detail(self.selected_cell))
            self.canvas.delete('recognition');paint_grid(self.canvas,self.state.grid(),*layout);self.last_layout=layout
            self.last_revision=self.state.revision
        self.lamp.itemconfigure('lamp',fill='#6dff80' if self.state.thinking else '#394654')
        self.label.configure(text='LLM使用中' if self.state.thinking else 'LLM待機')
        self.text.configure(text=self.state.summary())
