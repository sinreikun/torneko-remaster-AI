"""Pixel geometry and OCR debouncing contracts, not a simulated game."""
import io
import unittest
from PIL import Image,ImageDraw
from autoplayer.map_sensor import read_grid,FloorConfirmation,MapSensor

class GridTests(unittest.TestCase):
    def fixture(self,scale=1,color=(48,94,118)):
        w,h=1468,828;t=w/120;left=(w-53*t)/2;top=h/2-160
        im=Image.new('RGB',(w,h),(12,12,12));d=ImageDraw.Draw(im)
        d.rectangle((left+23*t,top+20*t,left+28*t-1,top+25*t-1),fill=color)
        def center(x,y):return left+(x+.5)*t,top+(y+.5)*t
        x,y=center(25,22);d.rectangle((x-5,y-5,x+5,y+5),fill=(180,180,0))
        x,y=center(26,22);d.ellipse((x-5,y-5,x+5,y+5),fill=(190,60,0))
        x,y=center(24,22);d.polygon([(x,y-5),(x+5,y),(x,y+5),(x-5,y)],fill=(0,200,200))
        x,y=center(25,23);d.rectangle((x-5,y-5,x+5,y+5),outline=(0,200,200),width=2)
        im=im.resize((round(w*scale),round(h*scale)));out=io.BytesIO();im.save(out,format='PNG');return out.getvalue()
    def test_grid_5x5_center_and_distinct_objects_at_two_resolutions(self):
        for scale in (1,1280/1468):
            r=read_grid(self.fixture(scale));self.assertEqual(r['status'],'observed')
            self.assertEqual(r['grid_size'],[54,32]);self.assertEqual(r['player_cell'],[25,22])
            self.assertIn((1,0),r['enemies']);self.assertIn((-1,0),r['items']);self.assertIn((0,1),r['stairs'])
            self.assertEqual(len(r['known_cells']),25)
    def test_palette_change_preserves_grid(self):
        for color in [(48,94,118),(90,42,120),(0,105,52)]:
            self.assertEqual(read_grid(self.fixture(color=color))['player_cell'],[25,22])
    def test_no_player_is_unavailable(self):
        im=Image.new('RGB',(1468,828));out=io.BytesIO();im.save(out,format='PNG')
        self.assertEqual(read_grid(out.getvalue())['status'],'unavailable')
    def test_floor_requires_fresh_consistent_observations_and_time(self):
        f=FloorConfirmation();f.update([1],'a',0)
        self.assertEqual(f.update([7],'b',1)['status'],'pending')
        self.assertEqual(f.update([1],'c',2)['confirmed'],1)
        for i in range(4):self.assertFalse(f.update([2],'same-cache',3+i)['changed'])
        self.assertEqual(f.floor,1)
        self.assertFalse(f.update([2],'fresh1',8)['changed'])
        self.assertTrue(f.update([2],'fresh2',9)['changed']);self.assertEqual(f.floor,2)
        self.assertEqual(f.update(None,'missing',10)['confirmed'],2)
    def test_missing_or_conflicting_read_breaks_confirmation(self):
        f=FloorConfirmation();f.update([3],'a',0);f.update([8],'b',1);f.update(None,'c',2)
        self.assertFalse(f.update([8],'d',3)['changed']);self.assertFalse(f.update([8],'e',4)['changed'])
        self.assertFalse(f.update([9],'f',5)['changed']);self.assertEqual(f.floor,3)
    def test_accumulated_cells_survive_temporary_detection_loss(self):
        sensor=MapSensor();r=read_grid(self.fixture());sensor.accumulate(r,1)
        old=set(sensor.cells);sensor.accumulate({'status':'unavailable'},1);self.assertEqual(sensor.cells,old)
        sensor.reset();self.assertFalse(sensor.cells)

    def test_policy_confirms_floor_before_clearing_map_and_retains_gate_on_retry(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from autoplayer.core import Settings
        from autoplayer.memory import MemoryStore
        from autoplayer.policy import ExperiencePolicy
        png=self.fixture()
        def native(floor,stamp):return {'status':'ok','captured_at':stamp,'lines':[{'text':f'{floor}階'},{'text':'HP 19/20'}]}
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings();memory=MemoryStore(Path(tmp)/'memory.sqlite',cfg)
            try:
                p=ExperiencePolicy(memory,cfg);p.observe(png,native(1,1),map_display=True)
                p.map_sensor.cells.add((0,0));old=set(p.map_sensor.cells)
                with patch('autoplayer.map_sensor.time.monotonic',return_value=1):
                    pending=p.observe(png,native(9,2),map_display=True)
                self.assertFalse(pending['eligible']);self.assertEqual(p.map_sensor.cells,old)
                p.observe(png,native(1,3),map_display=True);self.assertEqual(p.map_sensor.cells,old)
                for stamp,now in [(4,2),(5,2.4),(6,3)]:
                    with patch('autoplayer.map_sensor.time.monotonic',return_value=now):
                        result=p.observe(png,native(2,stamp),map_display=True)
                self.assertEqual(result['floor_confirmation']['status'],'changed')
                self.assertNotIn((0,0),p.map_sensor.cells);self.assertEqual(p.map_sensor.floor,2)
                retry=ExperiencePolicy(memory,cfg)
                self.assertIs(retry.map_sensor,p.map_sensor);self.assertEqual(retry.floor_confirmation.floor,2)
            finally:memory.close()

    def test_low_confidence_floor_cannot_trigger_reset(self):
        import tempfile
        from pathlib import Path
        from autoplayer.core import Settings
        from autoplayer.memory import MemoryStore
        from autoplayer.policy import ExperiencePolicy
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings();memory=MemoryStore(Path(tmp)/'memory.sqlite',cfg)
            try:
                p=ExperiencePolicy(memory,cfg);p.floor_confirmation.floor=1;p.map_sensor.cells.add((2,3))
                for stamp in (1,2,3,4):
                    r=p.observe(b'frame',{'status':'ok','captured_at':stamp,'lines':[{'text':'7階','confidence':.4}]})
                    self.assertTrue(r['floor_confirmation']['rejected_low_confidence'])
                self.assertEqual(p.floor_confirmation.floor,1);self.assertIn((2,3),p.map_sensor.cells)
            finally:memory.close()

    def test_pending_floor_refreshes_ocr_instead_of_counting_cache(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from autoplayer.core import Controller,Settings
        agent=Controller.__new__(Controller);agent.cfg=Settings();gate=FloorConfirmation();gate.floor=1;gate.pending=2
        ocr=SimpleNamespace(cache={'old':'response'},read=Mock(return_value={'status':'ok'}))
        agent.llm=SimpleNamespace(ocr=ocr);agent.policy=SimpleNamespace(floor_confirmation=gate)
        agent.read_policy_ocr(b'frame');self.assertFalse(ocr.cache)
        gate.pending=None;ocr.cache['current']='response';agent.read_policy_ocr(b'frame')
        self.assertTrue(ocr.cache)
