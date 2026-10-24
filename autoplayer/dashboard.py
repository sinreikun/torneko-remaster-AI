"""Bounded per-turn learning traces and scroll-stable UI presentation."""
from collections import deque
import math

SERIES={'coverage':('初期観測充足','#59d8ff'),'reward':('観測報酬','#7df08a'),'novelty':('新規性','#ffc66d'),
        'q':('過去Q値','#d79aff'),'forecast':('予測一致','#ff7f9d'),'confidence':('LLM自己評価','#e8eaf0')}

class LearningTraces:
    def __init__(self):self.samples=deque(maxlen=240);self.turn=0
    def update(self,row):
        if row.get('event') not in ('result','human_console_result'):return
        if row.get('event')=='human_console_result' and not row.get('keys'):return
        self.turn+=1
        actions=(row.get('information_exploration') or {}).get('actions',[])
        update=row.get('input_coverage',{})
        if update.get('situation')==(row.get('information_exploration') or {}).get('situation'):
            actions=[{**a,'observed':update['observed']} if a.get('keys')==update.get('keys') and 'observed' in update else a for a in actions]
        coverage=sum(min(1,a.get('observed',0)/max(1,a.get('required',2))) for a in actions)/len(actions) if actions else None
        prediction=(row.get('forecast_evaluation') or {}).get('status')
        self.samples.append({'turn':self.turn,'coverage':coverage,'reward':(row.get('policy_learning') or {}).get('reward'),
            'novelty':(row.get('world_observation') or {}).get('novelty'),'q':((row.get('policy_choice') or {}).get('selected') or {}).get('q'),
            'forecast':1 if prediction=='matched' else 0 if prediction=='mismatch' else None,'confidence':row.get('confidence')})
    def paint(self,canvas,selected,box):
        canvas.delete('learning_graph');x,y,w,h=box
        canvas.create_rectangle(x,y,x+w,y+h,fill='#111a29',outline='#71859f',tags='learning_graph')
        canvas.create_text(x+7,y+6,text=('学習状態 / 手 '+str(self.samples[0]['turn'])+'〜'+str(self.samples[-1]['turn'])+' / 系列別の範囲') if self.samples else '学習状態 / ターンの観測待ち',anchor='nw',fill='#e6edf7',font=('',8),tags='learning_graph')
        if not self.samples:
            canvas.create_text(x+8,y+35,text='操作結果の取得後に描画します',anchor='nw',fill='#b9c7dc',tags='learning_graph');return
        chosen=[key for key in SERIES if selected.get(key)];plot_top=y+24;plot_bottom=y+h-12-11*len(chosen)
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
            canvas.create_text(x+7,y+h-10-11*len(chosen)+11*chosen.index(key),text=legend,anchor='nw',fill=color,font=('',8),tags='learning_graph')

def replace_text(widget,text):
    """Keep the same top logical line and horizontal position after replacement."""
    top=widget.index('@0,0');horizontal=widget.xview()[0]
    widget.configure(state='normal');widget.delete('1.0','end');widget.insert('1.0',text)
    widget.yview(top);widget.xview_moveto(horizontal);widget.configure(state='disabled')


def append_text(widget,text):
    top=widget.index('@0,0');horizontal=widget.xview()[0]
    widget.configure(state='normal');widget.insert('end',text)
    lines=int(widget.index('end-1c').split('.')[0])
    if lines>5000 and int(top.split('.')[0])>lines-4500:
        removed=lines-4500;widget.delete('1.0',f'{removed+1}.0');top=f'{max(1,int(top.split(".")[0])-removed)}.0'
    widget.yview(top);widget.xview_moveto(horizontal);widget.configure(state='disabled')
