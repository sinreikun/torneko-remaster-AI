"""Submitted OCR geometry regression and ambiguity contracts, not a demo game."""
import copy,hashlib,json,unittest
from pathlib import Path
from autoplayer.knowledge import KnowledgeEngine
from autoplayer.current_evidence import current,historical
from autoplayer.telemetry import RecognitionState
from autoplayer.core import LocalLLM,Settings
from test_agent import OBSERVED,change
from unittest.mock import Mock

FIXTURE=json.loads((Path(__file__).parent/'fixtures/status-1792803186761758100.json').read_text())

def native(*rows):
    return {'status':'ok','frame_sha256':'current','lines':[{'text':text,'words':[{'text':text,'box':[x,y,w,h]}]} for text,x,y,w,h in rows]}

class HudStatusTests(unittest.TestCase):
    def setUp(self):self.engine=KnowledgeEngine()
    def test_submitted_status_panel_values_and_zero_values(self):
        metrics=self.engine.reconcile(FIXTURE)['metrics']
        expected={'floor':[1],'max_floor':[25],'level':[1],'hp':[15,15],'fullness':[97],'strength':[8,8],'sword':[0],'shield':[0],'experience':[0]}
        self.assertEqual({k:v['values'] for k,v in metrics.items()},expected)
        for key in ('strength','sword','shield','experience','max_floor'):
            self.assertEqual(metrics[key]['source'],'same_row_boxed_label_value')
            self.assertEqual(metrics[key]['frame_sha256'],FIXTURE['frame_sha256'])
        self.assertIn('現在の階ではない',metrics['max_floor']['meaning'])
        self.assertNotIn('gold',metrics) # OCR's OG is not silently changed to 0G.
    def test_shift_and_scale_keep_relative_label_value_pairing(self):
        for scale in (.5,1.5):
            o=copy.deepcopy(FIXTURE)
            for l in o['lines']:
                for w in l['words']:
                    x,y,width,height=w['box'];w['box']=[x*scale-200,y*scale+70,width*scale,height*scale]
            self.assertEqual(self.engine.reconcile(o)['metrics']['strength']['values'],[8,8])
            self.assertEqual(self.engine.reconcile(o)['metrics']['shield']['values'],[0])
    def test_other_row_column_or_unknown_label_is_not_used_as_value(self):
        examples=[native(('剣の強さ',10,10,60,20),('9',120,45,15,20)),
            native(('剣の強さ',10,10,60,20),('ちから',140,10,60,20),('8/8',220,10,40,20)),
            native(('盾の強さ',10,10,60,20),('未知項目',120,10,60,20),('9',220,10,15,20)),
            native(('剣の強さ',10,10,60,20),('0',120,10,15,20),('2',150,10,15,20))]
        for o in examples:
            m=self.engine.reconcile(o)['metrics'];self.assertNotIn('sword',m);self.assertNotIn('shield',m)
    def test_conflict_or_missing_current_frame_stays_unknown(self):
        o=copy.deepcopy(FIXTURE);o['lines'].append({'text':'ちから 7/8'})
        self.assertNotIn('strength',self.engine.reconcile(o)['metrics'])
        self.assertNotIn('strength',self.engine.reconcile({'status':'unavailable','lines':o['lines']})['metrics'])
        self.assertEqual(current(b'new',o,self.engine)['metrics'],{})
    def test_deepest_record_never_becomes_current_floor(self):
        o={'status':'ok','lines':[{'text':'最深階25階'},{'text':'1階'},{'text':'HP 15/15'}]}
        m=self.engine.reconcile(o)['metrics'];self.assertEqual(m['floor']['values'],[1]);self.assertEqual(m['max_floor']['values'],[25])
        o['lines']=o['lines'][:1];self.assertNotIn('floor',self.engine.reconcile(o)['metrics'])
    def test_status_reaches_current_planning_evidence_and_monitor(self):
        o=copy.deepcopy(FIXTURE);o['frame_sha256']=hashlib.sha256(b'current').hexdigest()
        llm=LocalLLM(Settings());llm.ocr.read=Mock(return_value=o);llm.observe_scene=Mock(return_value=OBSERVED);llm._request=Mock(return_value=json.dumps(change()))
        llm.decide(b'current',[],False);m=llm._request.call_args.args[2]['current_evidence']['metrics']
        self.assertEqual(m['strength']['values'],[8,8]);self.assertEqual(m['shield']['values'],[0])
        state=RecognitionState();state.update({'event':'perception','policy_state':{'metrics':m,'minimap':{}}})
        for label in ('ちから 8/8','剣 0','盾 0','経験値 0','最深階 25','Lv 1'):self.assertIn(label,state.summary())
        llm.ocr.read.return_value={'status':'unavailable','frame_sha256':o['frame_sha256'],'lines':[]}
        llm.decide(b'current',[],False);self.assertEqual(llm._request.call_args.args[2]['current_evidence']['metrics'],{})
    def test_new_metrics_do_not_leak_from_history_into_current_evidence(self):
        old={'level':2,'max_floor':25,'experience':30,'gold':50,'metrics':{'strength':[8,8]},'keys':['w']}
        self.assertEqual(historical(old),{'keys':['w']})
