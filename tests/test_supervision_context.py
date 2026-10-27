"""Recorded OCR/request-size contracts. No LLM server or artificial game."""
import copy,io,json,threading,unittest,urllib.error
from pathlib import Path
from unittest.mock import Mock,patch
from autoplayer.core import LocalLLM,Settings
from autoplayer.performance import bounded_request_prompt,context_retry_prompt
from autoplayer.decision_protocol import SUPERVISION_INSTRUCTION
from autoplayer.supervision import StateLedger,PLAN_SCHEMA,Supervisor
from autoplayer.inventory_audit import InventoryAudit
from test_supervision import proposal,ledger,reply_ready

class SupervisionContextTests(unittest.TestCase):
    def fixture(self):return json.loads((Path(__file__).parent/'fixtures/supervision-overflow-1793073804365358500.json').read_text())
    def prompt(self):
        d=self.fixture();l=StateLedger();l.observe(d['ocr'],d['state'],{'location':'dungeon'},{},{'revision':3,'text':'盾を装備して慎重に進む'},0,4)
        a=d['audit'];a['active']=True;a['stage']='analyse'
        source=l.snapshot();source.update(inventory_audit=a,active_execution={'inventory_audit':a},inventory_observed_now=True,
            last_observed_inventory={'rows':a['pages'][0]},world_knowledge={'inventory':{'rows':a['pages'][0]},'retained_state':d['state'].get('retained_state',{})})
        return {'task':'operational_supervision','goal':'箱を持ち帰る','operator_guidance':{'revision':3,'text':'盾を装備して慎重に進む'},'current_key_bindings':{'num1':'決定/攻撃','num7':'説明表示'},'instruction':SUPERVISION_INSTRUCTION,'state_ledger':source}
    def test_recorded_inventory_and_current_evidence_survive_bounded_summary(self):
        p=self.prompt();original=copy.deepcopy(p);b=bounded_request_prompt(p)
        self.assertLessEqual(len(json.dumps(b,ensure_ascii=False,separators=(',',':'))),6500)
        for k in ('goal','operator_guidance','current_key_bindings','instruction'):self.assertEqual(b[k],p[k])
        self.assertEqual(b['state_ledger']['current']['text'],p['state_ledger']['current']['text'])
        self.assertEqual(b['state_ledger']['current']['selection_text'],p['state_ledger']['current']['selection_text'])
        self.assertEqual(len(b['state_ledger']['inventory']['rows']),7)
        shield=next(r for r in b['state_ledger']['inventory']['rows'] if r['display_name']=='青銅の盾')
        self.assertIn('防御力',shield.get('description_ocr',''));self.assertIsNone(shield['equipped'])
        self.assertEqual(b['state_ledger']['inventory_audit']['observed_metrics']['shield']['values'],[0])
        self.assertEqual(p,original)
    def test_large_nested_history_is_not_retried_unchanged(self):
        p=self.prompt();p['state_ledger']['recent']=[{'old':'以前の記録'*5000}]*12
        p['state_ledger']['experience_review']={'nested':[p['state_ledger']['recent']]*10}
        b=bounded_request_prompt(p);t=context_retry_prompt(b)
        self.assertLessEqual(len(json.dumps(t,ensure_ascii=False,separators=(',',':'))),5000)
        self.assertEqual(t['state_ledger']['current']['metrics']['hp']['values'],[12,19])
        self.assertLess(len(json.dumps(t)),len(json.dumps(p)))
    def test_current_mandatory_data_is_not_silently_cut(self):
        p=self.prompt();p['operator_guidance']['text']='必要な作戦指示'*3000
        with self.assertRaisesRegex(ValueError,'必須情報'):bounded_request_prompt(p)
    def test_http_context_retry_has_actual_smaller_payload_and_preserves_server_detail(self):
        p=self.prompt();p['state_ledger']['action_effects']=[{'keys':['w'],'role':'move','samples':1,'mean_observed_value':.1}]*12
        request_sizes=[]
        def send(request,**kw):
            body=json.loads(request.data);request_sizes.append(len(body['messages'][1]['content']))
            raise urllib.error.HTTPError(request.full_url,400,'Bad Request',{},io.BytesIO(json.dumps({'error':self.fixture()['server_error']}).encode()))
        client=LocalLLM(Settings(provider='ollama',window_title='トルネコ'))
        with patch('urllib.request.build_opener',return_value=Mock(open=Mock(side_effect=send))):
            with self.assertRaisesRegex(ValueError,'46587'):client._request(None,'監督役',p,PLAN_SCHEMA)
        self.assertEqual(len(request_sizes),2);self.assertLess(request_sizes[1],request_sizes[0])
    def test_previous_request_error_does_not_abort_current_audit(self):
        a=InventoryAudit();l=ledger(step=2);a.analyse_interface(l.current)
        sup=Supervisor(lambda *args:proposal(),threading.Event());self.addCleanup(sup.close)
        sup.errors=1;sup.last_request=2;sup.last_completed={'audit_id':None,'error':True}
        self.assertTrue(a.awaiting_analysis(sup,2))
        l.current['inventory_audit']=a.context();sup.tick(l,{'inventory_audit':a.context()});reply_ready(sup);sup.tick(l)
        self.assertFalse(a.awaiting_analysis(sup,2));self.assertEqual(a.last['status'],'analysed')
    def test_cached_item_name_is_not_reused_as_another_items_description(self):
        from autoplayer.world_model import Inventory
        rows=self.fixture()['audit']['pages'][0];inv=Inventory();inv.rows=rows
        inv.description_reads=[r['known_description'] for r in rows if r.get('known_description')]
        mistaken=next(r for r in rows if r['display_name']=='目くらまし草')
        self.assertEqual(mistaken['known_description']['quotes'],['大きいパン'])
        self.assertIsNone(inv.known_description(mistaken))
        self.assertIsNotNone(inv.known_description(next(r for r in rows if r['display_name']=='青銅の盾')))
