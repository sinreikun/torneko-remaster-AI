"""HUD geometry, map colour evidence, retained navigation, event export contracts."""
import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from PIL import Image,ImageDraw
from autoplayer.core import Settings,parse_decision
from autoplayer.knowledge import KnowledgeEngine
from autoplayer.interfaces import torneko_menu_kind
from autoplayer.minimap import read_minimap
from autoplayer.policy import ExperiencePolicy
from autoplayer.memory import MemoryStore
from autoplayer.recording import FrameRecorder,RecordedBackend
from autoplayer.diagnostics import export_diagnostics
from autoplayer.important_events import ImportantEvents
from test_policy import state,receipt_row
from test_agent import change


def line(text,box):return {'text':text,'words':[{'text':text,'box':box}]}
def palette_png(rgb):
    image=Image.new('RGB',(160,120),'black');draw=ImageDraw.Draw(image)
    draw.rectangle((40,40,84,70),fill=rgb);draw.rectangle((60,52,63,55),fill=(200,200,0))
    stream=io.BytesIO();image.save(stream,format='PNG');return stream.getvalue()

class ObservationRevisionTests(unittest.TestCase):
    def test_split_hud_requires_unique_same_row_geometry_and_flags_hunger(self):
        engine=KnowledgeEngine();native={'status':'ok','lines':[line('満腹度',[619,38,99,39]),line('6%',[732,39,80,36])]}
        self.assertEqual(engine.reconcile(native)['metrics']['fullness']['values'],[6])
        self.assertIn('食料',engine.reconcile(native)['goal_review']['resource_priority'])
        bad=copy.deepcopy(native);bad['lines'][1]['words'][0]['box'][1]=300
        self.assertNotIn('fullness',engine.reconcile(bad)['metrics'])
        bad=copy.deepcopy(native);bad['lines'].append(line('99%',[815,39,50,36]))
        self.assertNotIn('fullness',engine.reconcile(bad)['metrics'])
        self.assertEqual(torneko_menu_kind({'status':'ok','lines':[{'text':t} for t in ['HP28/48','食べる','投げる','置く']]}),'item_actions')

    def test_marker_local_palette_accepts_new_hues_only_in_isolated_capture(self):
        for rgb in ((0,105,52),(90,42,120),(40,120,180)):
            png=palette_png(rgb);r=read_minimap(png,True)
            self.assertEqual(r['status'],'observed');self.assertEqual(r['palette_source'],'marker_local_palette')
            self.assertTrue(r['known_cells'])
        self.assertEqual(read_minimap(palette_png((90,42,120)))['status'],'unavailable')

    def test_retries_retain_routes_floor_change_resets_pose_and_pixels_are_not_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings();memory=MemoryStore(Path(tmp)/'memory.sqlite',cfg)
            try:
                first=ExperiencePolicy(memory,cfg);a=state();b=state('B',x=261.7)
                first.routes.record(a,b,'d',{})
                second=ExperiencePolicy(memory,cfg)
                self.assertIs(second.routes,first.routes);self.assertTrue(second.routes.edges)
                second.routes.node({**a,'metrics':{'floor':{'values':[2]}}})
                self.assertFalse(second.routes.edges)
                a=state();b=copy.deepcopy(a);b['minimap']['known_pixels']*=3
                self.assertEqual(first.signals(a,b)['coverage'],0)
                a['minimap']['known_cells']=[(0,0)];b['minimap']['known_cells']=[(x,0) for x in range(5)]
                self.assertGreater(first.signals(a,b)['coverage'],0)
            finally:memory.close()

    def test_low_food_requests_one_menu_observation_and_failed_moves_do_not_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings();memory=MemoryStore(Path(tmp)/'memory.sqlite',cfg)
            try:
                policy=ExperiencePolicy(memory,cfg);s=state();s['metrics']['fullness']={'values':[6]}
                d=parse_decision(json.dumps(change(action={'keys':['w'],'hold_ms':80,'wait_ms':0})),cfg)
                chosen,info=policy.choose(d,s,stable=True)
                self.assertEqual(chosen.keys,['num0']);self.assertEqual(info['source'],'resource_inspection')
                _,again=policy.choose(d,s,stable=True);self.assertNotEqual(again['source'],'resource_inspection')
                s['metrics']['fullness']['values']=[80]
                for k in 'wasd':
                    policy.routes.record(s,s,k,{});policy.routes.record(s,s,k,{})
                policy.routes.inspection_node=None
                chosen,info=policy.choose(d,s,stable=True);self.assertEqual(info['source'],'navigation_inspection')
                chosen,info=policy.choose(d,s,stable=True);self.assertEqual(chosen.keys,[])
                self.assertEqual(info['source'],'navigation_reassessment_needed')
            finally:memory.close()

    def test_distant_failed_attack_reconsiders_without_claiming_enemy_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings();memory=MemoryStore(Path(tmp)/'memory.sqlite',cfg)
            try:
                policy=ExperiencePolicy(memory,cfg);s=state();s['minimap']['enemies']=[[0,2]]
                for _ in range(2):policy.learn(s,s,receipt_row('num1'),s)
                d=parse_decision(json.dumps(change(scene='unknown',reason='右の敵を倒すために攻撃する。',action={'keys':['num1'],'hold_ms':80,'wait_ms':0})),cfg)
                chosen,info=policy.choose(d,s,stable=True)
                self.assertIn(chosen.keys[0],'wasd');self.assertIn('射程を仮定せず',info['reconsidered_nonmovement']['reason'])
            finally:memory.close()

    def test_correlated_ocr_enemy_name_alias_preserves_original(self):
        from autoplayer.combat import CombatExperiments
        from autoplayer.battle_messages import read_messages
        with tempfile.TemporaryDirectory() as tmp:
            memory=MemoryStore(Path(tmp)/'memory.sqlite',Settings())
            try:
                combat=CombatExperiments(memory,Settings())
                native={'status':'ok','frame_sha256':'frame','lines':[{'text':'トルネコはーまどうしに10ポイントのダメージを与えた。まどうしをやっつけた。'}]}
                row=receipt_row(observed_keys=['num1'],ocr={'frame_sha256':'before'})
                events=combat.observe_messages(row,{},[(native,'frame')])
                hit=next(e for e in events if e['kind']=='damage_dealt')
                self.assertEqual(hit['enemy'],'まどうし');self.assertEqual(hit['raw_enemy'],'ーまどうし')
            finally:memory.close()

    def test_floor_evidence_survives_ordinary_cap_and_later_dialogues_and_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);recorder=FrameRecorder(root/'recordings'/'session',limit_bytes=1)
            png=palette_png((90,42,120));recorder.record(png,'observation')
            evidence=recorder.important
            def row(floor,scene='gameplay'):
                return {'step':floor,'scene':scene,'observed_keys':['num1'],'ocr':{'status':'ok','lines':[{'text':'下りる'},{'text':'そのまま'}]},
                    'policy_state':{'metrics':{'floor':{'values':[floor]}}},'policy_after':{'metrics':{'floor':{'values':[floor]}}}}
            evidence.observe(row(6),png,png);evidence.observe(row(7),png,png)
            for _ in range(20):evidence.observe(row(7,'dialog'),png,png)
            recorder.close();events=[json.loads(f.read_text()) for f in (recorder.folder/'important').rglob('event.json')]
            transition=next(e for e in events if 'floor_changed' in e['reasons'])
            self.assertEqual(transition['previous_actions'][0]['observed_keys'],['num1']);self.assertTrue(transition['frames'])
            self.assertIn('following_action',transition)
            report=export_diagnostics(root,root/'report.zip')
            with zipfile.ZipFile(root/'report.zip') as z:
                self.assertTrue(any('/important/' in name and name.endswith('event.json') for name in z.namelist()))
            self.assertGreater(report['important_event_evidence']['files'],0)
