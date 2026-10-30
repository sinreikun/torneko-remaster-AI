"""Bounded per-turn learning traces and scroll-stable UI presentation."""
from collections import deque
import math

SERIES={'coverage':('初期観測充足','#59d8ff'),'reward':('観測報酬','#7df08a'),'novelty':('新規性','#ffc66d'),
        'q':('過去Q値','#d79aff'),'forecast':('予測一致率','#ff7f9d'),'confidence':('LLM自己評価','#e8eaf0')}

SERIES.update({'uncertainty':('不確実性','#78aaff'),'information_gain':('情報獲得量','#6fe7dd'),
 'repetition':('反復率','#ff967f'),'llm_rate':('LLM要求率','#d6b8ff'),'decision_ms':('選択ms','#b4dd64'),
 'observation_ms':('観測ms','#ffdf83'),'goal_progress':('局所目標進展','#64d6af'),
 'tracking':('追跡候補継続率','#f4a7df'),'position':('位置信頼度','#a3c8ed')})

class LearningTraces:
    def __init__(self):self.samples=deque(maxlen=240);self.turn=0
    def update(self,row):
        if row.get('event') not in ('result','human_console_result'):return
        if row.get('event')=='human_console_result' and not row.get('keys'):return
        step=row.get('step')
        self.turn=max(self.turn,step) if type(step) is int and step>0 else self.turn+1
        if row.get('generation')==2:
            m=row.get('nextgen',{}).get('metrics',{});fields={'forecast':'prediction_accuracy','novelty':'novelty','uncertainty':'uncertainty','information_gain':'information_gain','repetition':'repetition_rate','llm_rate':'llm_intervention_rate','decision_ms':'decision_ms','observation_ms':'observation_ms','goal_progress':'goal_progress','tracking':'tracking_success','position':'position_confidence'}
            self.samples.append({'turn':self.turn,**{key:m.get(source) for key,source in fields.items()}});return
        actions=(row.get('information_exploration') or {}).get('actions',[])
        update=row.get('input_coverage',{})
        if update.get('situation')==(row.get('information_exploration') or {}).get('situation'):
            actions=[{**a,'observed':update['observed']} if a.get('keys')==update.get('keys') and 'observed' in update else a for a in actions]
        coverage=sum(min(1,a.get('observed',0)/max(1,a.get('required',2))) for a in actions)/len(actions) if actions else None
        prediction=(row.get('forecast_evaluation') or {}).get('status')
        self.samples.append({'turn':self.turn,'coverage':coverage,'reward':(row.get('policy_learning') or {}).get('reward'),
            'novelty':(row.get('world_observation') or {}).get('novelty'),'q':((row.get('policy_choice') or {}).get('selected') or {}).get('q'),
            'forecast':1 if prediction=='matched' else 0 if prediction=='mismatch' else None,'confidence':row.get('confidence') if row.get('performance',{}).get('llm_requested') is not False else None})
    def paint(self,canvas,selected,box,background=None):
        canvas.delete('learning_graph');x,y,w,h=box
        if background is not None:canvas.create_image(x,y,anchor='nw',image=background,tags=('learning_graph','learning_graph_background'))
        canvas.create_rectangle(x,y,x+w,y+h,fill='' if background is not None else '#111a29',outline='#71859f',tags='learning_graph')
        canvas.create_text(x+7,y+6,text=('学習状態 / 手 '+str(self.samples[0]['turn'])+'〜'+str(self.samples[-1]['turn'])+' / 系列別の範囲') if self.samples else '学習状態 / ターンの観測待ち',anchor='nw',fill='#e6edf7',font=('',8),tags='learning_graph')
        if not self.samples:
            canvas.create_text(x+8,y+35,text='操作結果の取得後に描画します',anchor='nw',fill='#b9c7dc',tags='learning_graph');return
        chosen=[key for key in SERIES if selected.get(key)];plot_top=y+24;plot_bottom=y+h-12-11*((len(chosen)+1)//2)
        for key in chosen:
            points=[(i,s.get(key)) for i,s in enumerate(self.samples) if isinstance(s.get(key),(int,float)) and math.isfinite(s[key])]
            label,color=SERIES[key]
            if points:
                values=[v for i,v in points];low,high=min(values),max(values);span=high-low
                # Missing observations break the line; no interpolated values.
                segment=[];previous=None
                for i,value in points:
                    if previous is not None and i!=previous+1:
                        if len(segment)>=4:canvas.create_line(*segment,fill=color,width=2,tags='learning_graph')
                        segment=[]
                    a=x+8+i/max(1,len(self.samples)-1)*(w-16);b=plot_bottom-(.5 if not span else (value-low)/span)*max(1,plot_bottom-plot_top)
                    canvas.create_oval(a-1,b-1,a+1,b+1,fill=color,outline='',tags='learning_graph');segment.extend((a,b));previous=i
                if len(segment)>=4:canvas.create_line(*segment,fill=color,width=2,tags='learning_graph')
                legend=f'{label}: {values[-1]:.3g} [{low:.2g}〜{high:.2g}]'
            else:legend=label+': 未観測'
            canvas.create_text(x+7+(chosen.index(key)%2)*(w//2),y+h-10-11*((len(chosen)+1)//2)+11*(chosen.index(key)//2),text=legend,anchor='nw',fill=color,font=('',8),tags='learning_graph')

def replace_text(widget,text):
    """Keep the same top logical line and horizontal position after replacement."""
    top=widget.index('@0,0');horizontal=widget.xview()[0]
    widget.configure(state='normal');widget.delete('1.0','end');widget.insert('1.0',text)
    widget.yview(top);widget.xview_moveto(horizontal);widget.configure(state='disabled')


def append_text(widget,text):
    text=text[:12000]+('\n…（表示のみ省略。全文は診断ログに保存）\n' if len(text)>12000 else '')
    top=widget.index('@0,0');horizontal=widget.xview()[0]
    widget.configure(state='normal');widget.insert('end',text)
    lines=int(widget.index('end-1c').split('.')[0])
    if lines>1500:
        removed=lines-1200;widget.delete('1.0',f'{removed+1}.0');top=f'{max(1,int(top.split(".")[0])-removed)}.0'
    count=widget.count('1.0','end-1c','chars')
    if isinstance(count,(list,tuple)) and count and type(count[0]) is int and count[0]>200000:
        cut=widget.index(f'1.0+{count[0]-160000}c lineend+1c')
        removed=int(cut.split('.')[0])-1;widget.delete('1.0',cut);top=f'{max(1,int(top.split(".")[0])-removed)}.0'
    widget.yview(top);widget.xview_moveto(horizontal);widget.configure(state='disabled')


def translucent_background(image,box,opacity=.5):
    from PIL import Image
    x,y,w,h=map(round,box)
    original=image.crop((x,y,x+w,y+h)).convert('RGBA')
    shade=Image.new('RGBA',original.size,(17,26,41,round(255*max(0,min(1,opacity)))))
    return Image.alpha_composite(original,shade).convert('RGB')


def flow_positions(width,sizes,padding=(8,2),gap=5):
    """Ordered wrapping with no horizontally clipped action beyond the row."""
    px,py=padding;available=max(1,width-2*px);x=px;y=py;row_height=0;positions=[]
    for requested_w,h in sizes:
        w=min(available,max(1,requested_w))
        if x>px and x+w>width-px:y+=row_height+gap;x=px;row_height=0
        positions.append((x,y,w,h));x+=w+gap;row_height=max(row_height,h)
    return positions,y+row_height+py


def flow_frame_class():
    # Tk stays optional when importing numerical dashboard helpers in tests.
    from tkinter import ttk,TclError
    class FlowFrame(ttk.Frame):
        def __init__(self,parent,padding=0,**kwargs):
            super().__init__(parent,**kwargs)
            self.flow_padding=(padding,padding) if isinstance(padding,int) else tuple(padding[:2])
            self.pending=None;self.tracked=set()
            self.bind('<Configure>',self.schedule);self.bind('<Map>',self.schedule)
        def schedule(self,event=None):
            if self.pending is None:
                try:self.pending=self.after_idle(self.layout)
                except TclError:pass
        def layout(self):
            self.pending=None
            try:
                children=self.winfo_children()
                for child in children:
                    if child not in self.tracked:child.bind('<Configure>',self.schedule,add='+');self.tracked.add(child)
                positions,height=flow_positions(self.winfo_width(),[(c.winfo_reqwidth(),c.winfo_reqheight()) for c in children],self.flow_padding)
                for child,(x,y,w,h) in zip(children,positions):
                    if child.winfo_manager()=='pack':child.pack_forget()
                    child.place(x=x,y=y,width=w,height=h)
                if int(self.cget('height'))!=height:self.configure(height=height)
            except TclError:pass
    return FlowFrame


def split_fraction(value,default=.62):
    import math
    if isinstance(value,bool):return default
    try:ratio=float(value)
    except (TypeError,ValueError):return default
    return max(.1,min(.9,ratio)) if math.isfinite(ratio) else default


def dashboard_sashes(width,ratios=None):
    if not isinstance(ratios,(list,tuple)) or len(ratios)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) for x in ratios):ratios=(.22,.72)
    first=max(.12,min(.35,ratios[0]));second=max(first+.25,min(.88,ratios[1]))
    return round(width*first),round(width*second)
