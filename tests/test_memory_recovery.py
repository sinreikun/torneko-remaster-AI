import copy
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from PIL import Image,ImageDraw
from autoplayer.core import Controller,Settings,LocalLLM,parse_decision
from autoplayer.memory import MemoryStore
from autoplayer.observation import TurnObserver
from test_agent import VALID,Backend,Script,change

class DurableMemoryTests(unittest.TestCase):
    def test_reopen_and_correct_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'memory.sqlite';cfg=Settings()
            decision=parse_decision(json.dumps(change(plan=['Open route','Reach goal'],progress=['Started'],memory_updates=[{'key':'button1','value':'opens a dialog','confidence':.8}])),cfg)
            memory=MemoryStore(path,cfg);memory.remember({'scene':'title','executed':True,'changed':False,'keys':['enter']},decision);memory.close()
            memory=MemoryStore(path,cfg)
            self.assertEqual(memory.context()['plan'],['Open route','Reach goal'])
            self.assertEqual(memory.context()['provisional_claims'][0]['value'],'opens a dialog')
            self.assertEqual(len(memory.context()['unconfirmed_input_responses']),1)
            decision.memory_updates[0]['value']='moves selection'
            memory.remember({'scene':'menu'},decision);memory.close()
            memory=MemoryStore(path,cfg)
            self.assertEqual(len(memory.context()['provisional_claims']),1)
            self.assertEqual(memory.context()['provisional_claims'][0]['value'],'moves selection');memory.close()
    def test_transition_graph_does_not_join_separate_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db';cfg=Settings()
            memory=MemoryStore(path,cfg)
            memory.remember({'scene':'title','keys':['enter'],'executed':True})
            memory.remember({'scene':'menu','keys':[],'executed':False})
            memory.remember({'scene':'loading','keys':[],'executed':False})
            graph=memory.context()['known_transitions']
            self.assertTrue(any(r['origin']=='title' and r['destination']=='menu' and r['keys']==['enter'] for r in graph))
            self.assertTrue(any(r['origin']=='menu' and r['destination']=='loading' and r['keys']==[] for r in graph))
            memory.close();memory=MemoryStore(path,cfg)
            memory.remember({'scene':'victory'})
            self.assertFalse(any(r['destination']=='victory' for r in memory.context()['known_transitions']))
            memory.close()
    def test_corrupt_memory_is_preserved_and_rebuilt(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'memory.sqlite';original=b'broken non-SQLite content'
            path.write_bytes(original)
            memory=MemoryStore(path,Settings());self.assertEqual(memory.history(),[]);memory.close()
            backups=list(Path(directory).glob('memory.sqlite.broken-*'))
            self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),original)
    def test_goal_and_knowledge_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'memory'
            first=MemoryStore(path,Settings(goal='goal A'));first.remember({'scene':'victory'});first.close()
            for cfg in (Settings(goal='goal B'),Settings(goal='goal A',knowledge='different controls')):
                memory=MemoryStore(path,cfg);self.assertEqual(memory.history(),[]);memory.close()
    def test_dry_run_does_not_teach_action_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryStore(Path(directory)/'memory',Settings())
            d=parse_decision(json.dumps(change(memory_updates=[{'key':'test','value':'fiction','confidence':1}])),Settings())
            memory.remember({'dry_run':True},d)
            self.assertEqual(memory.context()['provisional_claims'],[]);memory.close()
    def test_control_loop_resumes_history(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=Settings(max_steps=1);path=Path(directory)
            memory=MemoryStore(path/'db',cfg);memory.remember({'scene':'previous','executed':True,'keys':['enter'],'changed':True})
            llm=Script([VALID]);Controller(cfg,Backend(),llm,lambda r:None,path/'run',memory=memory).run()
            self.assertEqual(llm.calls[0][1][0]['scene'],'previous');memory.close()

class RecoveryTests(unittest.TestCase):
    def run_agent(self,llm,cfg=None,backend=None):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        backend=backend or Backend();output=[]
        agent=Controller(cfg or Settings(max_steps=6,recovery_attempts=1),backend,llm,output.append,Path(directory.name)/'run')
        agent.run();return agent,backend,output
    def test_invalid_json_recovers_without_input(self):
        agent,b,out=self.run_agent(Script([ValueError('invalid JSON'),VALID,change(status='complete')]))
        self.assertTrue(agent.completed);self.assertEqual(len(b.actions),1)
        self.assertEqual(out[0]['status'],'retry')
    def test_repeated_invalid_json_stops(self):
        b=Backend()
        with self.assertRaises(ValueError): self.run_agent(Script([ValueError('bad')]*3),backend=b)
        self.assertEqual(b.actions,[]);self.assertEqual(b.releases,1)
    def test_unverified_completion_does_not_finish(self):
        class Verify(Script):
            def __init__(self): super().__init__([change(status='complete'),VALID,change(status='complete')]);self.verifications=0
            def verify_completion(self,image):
                self.verifications+=1
                return {'achieved':self.verifications>1,'evidence':['new visible goal'],'confidence':.95}
        llm=Verify();agent,b,_=self.run_agent(llm)
        self.assertTrue(agent.completed);self.assertEqual(llm.verifications,2);self.assertEqual(len(b.actions),1)
    def test_no_verifier_cannot_finish(self):
        class Unverified:
            def decide(self,*args): return parse_decision(json.dumps(change(status='complete')),Settings())
        agent,b,_=self.run_agent(Unverified(),Settings(max_steps=2))
        self.assertFalse(agent.completed);self.assertEqual(b.actions,[])
    def test_destructive_risk_never_sends(self):
        agent,b,_=self.run_agent(Script([change(risk='destructive')]))
        self.assertFalse(agent.completed);self.assertEqual(b.actions,[])
    def test_loading_wait_is_not_stagnation(self):
        waiting=change(scene='loading',action={'keys':[],'hold_ms':1,'wait_ms':0})
        agent,b,_=self.run_agent(Script([waiting]*9+[change(status='complete')]),Settings(max_steps=12))
        self.assertTrue(agent.completed);self.assertEqual(b.actions,[])
    def test_key_name_aliases_still_respect_allowlist(self):
        for name,expected in [('ENTER','enter'),('Return','enter'),('SPACEBAR','space')]:
            value=change(action={'keys':[name],'hold_ms':80,'wait_ms':0})
            self.assertEqual(parse_decision(json.dumps(value),Settings(window_title='Other game',allowed_keys=['enter','space'])).keys,[expected])
        with self.assertRaises(ValueError):
            parse_decision(json.dumps(change(action={'keys':['ESC'],'hold_ms':80,'wait_ms':0})),Settings(allowed_keys=['enter']))
    def test_memory_protocol_bounds(self):
        for values in ({'plan':['x']*9},{'progress':[4]},{'memory_updates':[{'key':'x','value':'y','confidence':float('nan')}]},{'risk':'run shell'}):
            with self.subTest(values=values),self.assertRaises(ValueError): parse_decision(json.dumps(change(**values)),Settings())

class TurnTests(unittest.TestCase):
    @staticmethod
    def frame(color,box=None):
        image=Image.new('RGB',(320,240),color)
        if box: ImageDraw.Draw(image).rectangle(box,fill='white')
        output=io.BytesIO();image.save(output,format='PNG');return output.getvalue()
    def observe(self,observer,frames,stop=None):
        class Clock:
            value=0
            def __call__(self): return self.value
        clock=Clock()
        class Stop:
            def wait(self,seconds): clock.value+=seconds;return False
            def is_set(self): return False
        baseline=frames[0];index=0
        def capture():
            nonlocal index
            index=min(index+1,len(frames)-1);return frames[index]
        return observer.observe(capture,baseline,'fixture',stop or Stop(),clock=clock)
    def test_black_loading_is_valid_observation(self):
        result=self.observe(TurnObserver(),[self.frame('black')])
        self.assertEqual(result.kind,'no_change');self.assertFalse(result.changed)
    def test_settles_after_visible_response(self):
        result=self.observe(TurnObserver(),[self.frame('black'),self.frame('white')])
        self.assertEqual(result.kind,'normal');self.assertTrue(result.changed)
        self.assertGreaterEqual(result.duration_ms,250)
    def test_animation_mask_is_local_and_bounded(self):
        observer=TurnObserver()
        # Continuous local motion times out twice before becoming a settle-only mask.
        frames=[self.frame('black',(10,10,30,30) if i%2 else (40,10,60,30)) for i in range(50)]
        for _ in range(2): result=self.observe(observer,frames)
        self.assertEqual(result.kind,'timeout');self.assertTrue(observer.masks['fixture'])
        self.assertLessEqual(len(observer.masks['fixture'])/768,.12)
        observer=TurnObserver();frames=[self.frame('white' if i%2 else 'black') for i in range(50)]
        for _ in range(2): self.observe(observer,frames)
        self.assertFalse(observer.masks.get('fixture'))

if __name__=='__main__':unittest.main()
