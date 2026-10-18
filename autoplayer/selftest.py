"""Unattended Windows integration: real pixels, real SendInput, real local VLM.
This fixture is an engineering benchmark, NOT a substitute for the target game.
"""
import argparse
import json
import queue
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path
from .core import Settings,LocalLLM,Controller
from .memory import MemoryStore
from .windows import WindowsGame

SCENES=[('TITLE','ENTER','Return'),('MENU','D','d'),('MAP','Z','z'),('BATTLE','SPACE','space'),('VICTORY','',None)]

def main():
    import tkinter as tk
    parser=argparse.ArgumentParser()
    parser.add_argument('--endpoint',required=True);parser.add_argument('--model',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    root=tk.Tk(); title='GamePilot Unattended Fixture '+uuid.uuid4().hex[:8]
    root.title(title);root.geometry('900x620+60+60')
    canvas=tk.Canvas(root,width=900,height=620,bg='#172033',highlightthickness=0);canvas.pack(fill='both',expand=True)
    commands=queue.Queue(); state={'index':0}; results={'fixture':'synthetic visible Windows game','model':args.model,'scenarios':[]}
    def render():
        scene,key,_=SCENES[state['index']]
        canvas.delete('all');canvas.configure(bg='#155a32' if scene=='VICTORY' else '#172033')
        canvas.create_text(450,65,text='AUTOMATED GAME FIXTURE',fill='white',font=('Segoe UI',28))
        canvas.create_text(450,220,text=scene,fill='white',font=('Segoe UI',62,'bold'))
        text='GOAL ACHIEVED' if scene=='VICTORY' else 'Press '+key+' to continue'
        canvas.create_text(450,375,text=text,fill='#ffe08a',font=('Segoe UI',38,'bold'))
        canvas.create_text(450,510,text='Follow the visible instruction. Victory is the only goal.',fill='white',font=('Segoe UI',18))
        root.focus_force()
    def keypress(event):
        expected=SCENES[state['index']][2]
        if expected and event.keysym==expected:
            state['index']+=1;render()
    root.bind('<KeyPress>',keypress)
    def poll():
        try:
            while True:
                fn,event=commands.get_nowait()
                fn();event.set()
        except queue.Empty: pass
        root.after(20,poll)
    def ui(fn):
        event=threading.Event();commands.put((fn,event))
        if not event.wait(10): raise RuntimeError('Fixture UI did not respond')
    def work():
        observations=[]
        try:
            with tempfile.TemporaryDirectory(prefix='gamepilot-real-') as temp:
                base=Path(temp)
                for start in (0,2,4):
                    ui(lambda start=start:(state.update(index=start),render()))
                    time.sleep(.5)
                    cfg=Settings(endpoint=args.endpoint,model=args.model,window_title=title,
                        goal='Reach the green VICTORY screen showing GOAL ACHIEVED. Other screens are not complete.',
                        knowledge='Follow the on-screen key instruction exactly. ENTER means enter, D means d, Z means z, SPACE means space. Never exit.',
                        allowed_keys=['enter','d','z','space'],max_steps=12,max_wait_ms=5000,timeout=300)
                    backend=WindowsGame(title);backend.activate()
                    memory=MemoryStore(base/f'memory-{start}.sqlite',cfg)
                    observations=[]
                    try:
                        agent=Controller(cfg,backend,LocalLLM(cfg),observations.append,base/f'run-{start}.jsonl',memory=memory)
                        agent.run()
                        if not agent.completed: raise AssertionError(f'Completion not verified from {SCENES[start][0]}')
                        if state['index']!=4: raise AssertionError('Agent reported completion before fixture victory')
                    finally: memory.close()
                    results['scenarios'].append({'start':SCENES[start][0],'passed':True,'decisions':len(observations)})
                    print(json.dumps(results['scenarios'][-1]),flush=True)
            results['passed']=True
        except Exception:
            results['passed']=False;results['error']=traceback.format_exc();results['last_observations']=observations[-6:]
            print(results['error'],flush=True)
        finally:
            Path(args.output).parent.mkdir(parents=True,exist_ok=True)
            Path(args.output).write_text(json.dumps(results,indent=2),encoding='utf-8')
            commands.put((root.destroy,threading.Event()))
    render();root.after(20,poll)
    root.after(300,lambda:threading.Thread(target=work,daemon=True).start())
    root.mainloop()
    raise SystemExit(0 if results.get('passed') else 1)

if __name__=='__main__': main()
