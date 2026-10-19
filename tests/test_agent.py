import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from autoplayer.core import Settings,LocalLLM,Controller,parse_decision
from autoplayer.components import pull_model

VALID={'scene':'title','observation':'Start game visible','subgoal':'Start','goal_success':'Gameplay visible after starting','reason':'Start selected','confidence':.95,'status':'continue','action':{'keys':['enter'],'hold_ms':80,'wait_ms':0}}
class Backend:
    def __init__(self): self.actions=[];self.releases=0
    def capture(self): return b'fake-test-frame'
    def act(self,keys,ms,stop): self.actions.append((keys,ms))
    def release_all(self): self.releases+=1
class Script:
    def __init__(self,values): self.values=iter(values);self.calls=[]
    def decide(self,png,history,stalled):
        self.calls.append((png,history[:],stalled))
        v=next(self.values)
        if isinstance(v,Exception): raise v
        return parse_decision(json.dumps(v),Settings())
    def verify_completion(self,image): return {'achieved':True,'confidence':.99,'evidence':['verified test fixture']}

def change(**kwargs):
    v=copy.deepcopy(VALID);v.update(kwargs)
    if kwargs.get('status')=='complete' and 'action' not in kwargs: v['action']={'keys':[],'hold_ms':1,'wait_ms':0}
    return v

class ProtocolTests(unittest.TestCase):
    def test_decision(self): self.assertEqual(parse_decision(json.dumps(VALID),Settings()).keys,['enter'])
    def test_bad_keys(self):
        for keys in (['alt','tab'],['powershell'],['enter','enter'],['enter']*3,'enter',[1]):
            with self.subTest(keys=keys),self.assertRaises(ValueError):
                parse_decision(json.dumps(change(action={'keys':keys,'hold_ms':80,'wait_ms':0})),Settings())
    def test_duration_limits(self):
        for field,value in [('hold_ms',0),('hold_ms',251),('hold_ms',True),('wait_ms',-1),('wait_ms',5001)]:
            v=copy.deepcopy(VALID);v['action'][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError): parse_decision(json.dumps(v),Settings())
    def test_invalid_shape(self):
        for value in ['[]','null','```json\n{}\n```','{}',json.dumps(change(confidence=float('nan'))),json.dumps(change(confidence=True)),json.dumps(change(status='shell')),json.dumps(change(scene=9))]:
            with self.subTest(value=value),self.assertRaises((ValueError,TypeError)): parse_decision(value,Settings())
    def test_endpoints(self):
        for endpoint in ['http://example.com','file:///etc/passwd','http://127.0.0.1@evil.com','http://user:pass@localhost','http://localhost/?key=1']:
            with self.subTest(endpoint=endpoint),self.assertRaises(ValueError): Settings(endpoint=endpoint).validate()
        for endpoint in ['http://localhost:11434','http://127.0.0.1:8080/v1','http://[::1]:1234']:
            Settings(endpoint=endpoint).validate()
    def test_invalid_settings(self):
        for args in [dict(max_steps=-1),dict(allowed_keys=[]),dict(allowed_keys=['f4']),dict(window_title=''),dict(goal=''),dict(provider='unknown')]:
            with self.subTest(args=args),self.assertRaises(ValueError): Settings(**args).validate()

class LoopTests(unittest.TestCase):
    def run_loop(self,values,dry=False,backend=None,stop=None,steps=10):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        path=Path(tmp.name)/'run.jsonl';backend=backend or Backend();llm=Script(values);out=[]
        controller=Controller(Settings(max_steps=steps,recovery_attempts=0),backend,llm,out.append,path,stop)
        controller.run(dry)
        rows=[json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []
        return backend,llm,out,rows
    def test_scene_sequence(self):
        values=[change(scene=s,observation=s+' visible') for s in ['title','loading','dialog','gameplay','inventory','defeat']]+[change(scene='victory',status='complete')]
        b,l,o,rows=self.run_loop(values)
        self.assertEqual(len(b.actions),6); self.assertEqual(len(l.calls),7)
        self.assertEqual(l.calls[1][1][0]['scene'],'title')
        self.assertEqual(rows[-1]['status'],'complete');self.assertEqual(b.releases,1)
    def test_dry_run(self):
        b,_,_,rows=self.run_loop([VALID,change(status='complete')],True)
        self.assertEqual(b.actions,[]); self.assertTrue(rows[1]['dry_run'])
    def test_blocked_never_inputs(self):
        b,_,_,rows=self.run_loop([change(status='blocked')]);self.assertEqual(b.actions,[])
    def test_stopped_before_request(self):
        s=threading.Event();s.set();b,l,_,_=self.run_loop([],stop=s)
        self.assertEqual(l.calls,[]);self.assertEqual(b.actions,[]);self.assertEqual(b.releases,1)
    def test_cancel_during_inference(self):
        b=Backend();s=threading.Event()
        class Cancel:
            def decide(self,*args): s.set();return parse_decision(json.dumps(VALID),Settings())
        with tempfile.TemporaryDirectory() as d:
            Controller(Settings(),b,Cancel(),lambda r:None,Path(d)/'run',s).run()
        self.assertEqual(b.actions,[]);self.assertEqual(b.releases,1)
    def test_invalid_response_releases(self):
        b=Backend()
        with self.assertRaises(ValueError): self.run_loop([ValueError('bad json')],backend=b)
        self.assertEqual(b.actions,[]);self.assertEqual(b.releases,1)
    def test_stale_screen(self):
        b=Backend();b.is_current=lambda image:False
        with self.assertRaisesRegex(RuntimeError,'画面が変わり'): self.run_loop([VALID]*5,backend=b)
        self.assertEqual(b.actions,[]);self.assertEqual(b.releases,1)
    def test_low_confidence(self):
        b=Backend()
        with self.assertRaisesRegex(RuntimeError,'信頼度'): self.run_loop([change(confidence=.1)]*3,backend=b)
        self.assertEqual(b.actions,[])
    def test_step_limit(self):
        b,_,out,_=self.run_loop([VALID],steps=1)
        self.assertEqual(len(b.actions),1);self.assertEqual(out[-1]['status'],'limit')
    def test_journal_failure_prevents_input(self):
        b=Backend()
        with tempfile.TemporaryDirectory() as d:
            controller=Controller(Settings(),b,Script([VALID]),lambda r:None,Path(d)/'run')
            controller._write=lambda row: (_ for _ in ()).throw(OSError('full'))
            with self.assertRaises(OSError): controller.run()
        self.assertEqual(b.actions,[]);self.assertEqual(b.releases,1)

class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                cls.requests.append((self.path,body))
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
                if self.path=='/api/pull': self.wfile.write(b'{"status":"success"}\n');return
                value={'message':{'content':json.dumps(VALID)}} if self.path=='/api/chat' else {'choices':[{'message':{'content':json.dumps(VALID)}}]}
                self.wfile.write(json.dumps(value).encode())
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls): cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def test_ollama_contract(self):
        cfg=Settings(endpoint=self.url,goal='勝利する',knowledge='W moves up')
        d=LocalLLM(cfg).decide(b'png',[],False)
        path,body=self.requests[-1];self.assertEqual(path,'/api/chat');self.assertEqual(d.scene,'title')
        self.assertEqual(body['messages'][1]['images'],['cG5n'])
        branches=body['format']['oneOf']
        for branch in branches:
            self.assertEqual(branch['properties']['action']['properties']['keys']['items']['enum'],cfg.allowed_keys)
        complete=next(branch for branch in branches if branch['properties']['status']['enum']==['complete'])
        self.assertEqual(complete['properties']['action']['properties']['keys']['maxItems'],0)
        prompt=json.loads(body['messages'][1]['content']);self.assertEqual(prompt['goal'],'勝利する');self.assertEqual(prompt['knowledge'],'W moves up')
    def test_openai_contract(self):
        for url in (self.url,self.url+'/v1'):
            LocalLLM(Settings(endpoint=url,provider='openai')).decide(b'png',[],True)
            path,body=self.requests[-1];self.assertEqual(path,'/v1/chat/completions')
            self.assertTrue(body['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,'))
    def test_text_only_verifier_does_not_send_a_fake_image(self):
        for provider in ('ollama','openai'):
            LocalLLM(Settings(endpoint=self.url,provider=provider))._request(None,'Verify facts',{'observations':['TITLE']})
            _,body=self.requests[-1]
            if provider=='ollama': self.assertNotIn('images',body['messages'][1])
            else: self.assertEqual(len(body['messages'][1]['content']),1)
    def test_download_contract(self):
        out=[];self.assertTrue(pull_model(self.url,'qwen2.5vl:7b',out.append,threading.Event()))
        self.assertEqual(out[-1]['status'],'success')
    def test_unregistered_download(self):
        with self.assertRaises(ValueError): pull_model(self.url,'unknown',lambda x:None,threading.Event())

if __name__=='__main__': unittest.main()
