"""Compact UI observations; retained values are explicitly marked as last observed."""
import copy
import time

class RecognitionState:
    def __init__(self):
        self.map={};self.metrics={};self.objects=[];self.floor_state={};self.remaining=None
        self.inference=set();self.observed_at=None;self.phase='待機';self.revision=0
    @property
    def thinking(self):return bool(self.inference)
    def update(self,row):
        event=row.get('event')
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
            if event=='disconnected':self.map={};self.metrics={};self.objects=[];self.observed_at=None
            self.revision+=1;return
        state=row.get('policy_after') if event=='result' else row.get('policy_state')
        world=row.get('world_observation') or {}
        if state is None and event=='human_console_result':
            after=row.get('after_features') or {}
            state={'minimap':{'status':'unavailable'},'metrics':{k:{'values':after[k]} for k in ('hp','floor','fullness') if after.get(k) is not None}}
        if state is not None:
            confirmation=state.get('floor_confirmation',{})
            old_floor=self.metrics.get('floor',{}).get('values');new_floor=state.get('metrics',{}).get('floor',{}).get('values')
            if confirmation.get('changed') or world.get('new_adventure') or (old_floor and new_floor and old_floor!=new_floor and confirmation.get('status') in ('initialized','confirmed','changed')):
                self.map={};self.objects=[]
            if world.get('new_adventure'):self.metrics={}
            observed_map=state.get('minimap',{})
            if observed_map.get('status')=='observed':self.map=copy.deepcopy(observed_map)
            elif self.map.get('status')=='observed':self.map['retained']=True
            fresh=copy.deepcopy(state.get('metrics',{}))
            retained={k:{**v,'retained':True} for k,v in self.metrics.items() if k in ('strength','sword','shield','experience','max_floor','level','gold') and k not in fresh}
            self.metrics={**retained,**fresh}
            self.floor_state=copy.deepcopy(state.get('floor_confirmation',{}))
            self.objects=[] if event=='result' else copy.deepcopy(world.get('objects',[]))
            self.observed_at=time.monotonic();self.phase='観測・実行'
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
        self.revision+=1
    def grid(self):
        m=self.map
        if m.get('status')!='observed' or m.get('grid_size')!=[54,32]:return {}
        out={};player=m.get('player_cell')
        for cell in m.get('accumulated_cells',[]):
            if valid(cell):out[tuple(cell)]='known'
        if valid(player):
            for offset in m.get('known_cells',[]):
                cell=[player[i]+offset[i] for i in (0,1)]
                if valid(cell):out[tuple(cell)]='known'
            for field,kind in [('stairs','stairs'),('items','item'),('enemies','enemy')]:
                for offset in m.get(field,[]):
                    cell=[player[i]+offset[i] for i in (0,1)]
                    if valid(cell):out[tuple(cell)]=kind
            out[tuple(player)]='player'
        return out
    def summary(self):
        def val(name):
            values=self.metrics.get(name,{}).get('values')
            return '/'.join(str(v) for v in values) if values else '不明'
        age='未観測' if self.observed_at is None else f'{int(time.monotonic()-self.observed_at)}秒前'
        count='分析中' if self.thinking else '未設定' if self.remaining is None else '次の判断で分析' if self.remaining==0 else f'あと{self.remaining}手（状況変化で前倒し）'
        retained=' / '.join({'strength':'ちから','sword':'剣','shield':'盾','experience':'経験値','max_floor':'最深階','level':'Lv','gold':'所持金'}.get(k,k) for k,v in self.metrics.items() if v.get('retained'))
        note='\n保持値（未再確認）: '+retained if retained else ''
        return f"階 {val('floor')}   Lv {val('level')}   HP {val('hp')}\n満腹度 {val('fullness')}   ちから {val('strength')}\n剣 {val('sword')}   盾 {val('shield')}   経験値 {val('experience')}\n最深階 {val('max_floor')}   所持金 {val('gold')}\n次のLLM：{count}\n{self.phase} / 最終観測 {age}\n地図：{'確認済み' if self.map.get('status')=='observed' else '取得・階確認待ち'}{'（最終観測を保持）' if self.map.get('retained') else ''}{note}"

def valid(cell):return isinstance(cell,(tuple,list)) and len(cell)==2 and all(type(v) is int for v in cell) and 0<=cell[0]<54 and 0<=cell[1]<32

COLORS={'known':'#365e83','player':'#ffe04b','enemy':'#ff685e','item':'#3de5dc','stairs':'#73bcff'}

def paint_grid(canvas,cells,x=8,y=8,unit=6):
    # All 1,728 grid cells are drawn, including unknown ones.
    for row in range(32):
        for col in range(54):
            kind=cells.get((col,row));a=x+col*unit;b=y+row*unit
            canvas.create_rectangle(a,b,a+unit,b+unit,fill=COLORS['known'] if kind in ('enemy','item','stairs') else COLORS.get(kind,'#121c2b'),outline='#263346',width=1,tags='recognition')
            if kind=='enemy':canvas.create_oval(a+1,b+1,a+unit-1,b+unit-1,fill=COLORS[kind],outline='',tags='recognition')
            elif kind=='item':canvas.create_polygon(a+unit/2,b,a+unit,b+unit/2,a+unit/2,b+unit,a,b+unit/2,fill=COLORS[kind],tags='recognition')
            elif kind=='stairs':canvas.create_rectangle(a+1,b+1,a+unit-1,b+unit-1,fill='#121c2b',outline=COLORS[kind],tags='recognition')

class RecognitionPanel:
    def __init__(self,parent,state):
        import tkinter as tk
        from tkinter import ttk
        self.state=state;self.frame=ttk.LabelFrame(parent,text='認識モニター（観測値・推定）',padding=5)
        top=ttk.Frame(self.frame);top.pack(fill='x')
        self.lamp=tk.Canvas(top,width=20,height=20,highlightthickness=0);self.lamp.pack(side='left')
        self.lamp.create_oval(3,3,17,17,fill='#394654',outline='',tags='lamp')
        self.label=ttk.Label(top,text='LLM待機');self.label.pack(side='left')
        self.canvas=tk.Canvas(self.frame,width=340,height=210,bg='#121c2b',highlightthickness=0);self.canvas.pack(fill='x')
        self.text=ttk.Label(self.frame,justify='left',wraplength=340);self.text.pack(anchor='w')
        ttk.Label(self.frame,text='黄:自分  赤:敵  菱形:品  枠:階段  暗色:不明').pack(anchor='w')
        self.last_revision=-1
    def refresh(self):
        if self.last_revision!=self.state.revision:
            self.canvas.delete('recognition');paint_grid(self.canvas,self.state.grid())
            self.last_revision=self.state.revision
        self.lamp.itemconfigure('lamp',fill='#6dff80' if self.state.thinking else '#394654')
        self.label.configure(text='LLM使用中' if self.state.thinking else 'LLM待機')
        self.text.configure(text=self.state.summary())
