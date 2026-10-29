"""One measured post-input baseline for quiet, turn-based Torneko movement.
Never predicts coordinates; rejects intervening changes instead of assuming success.
"""
import copy,hashlib,io,time
from PIL import Image
from .observation import TurnObservation,TurnObserver

MOVES=(['w'],['a'],['s'],['d'],['w','a'],['w','d'],['s','a'],['s','d'])
class QuickSteps:
    def __init__(self):self.saved=None;self.active=False;self.hits=0;self.misses=0
    @staticmethod
    def quiet(state):
        m=(state or {}).get('minimap',{});metrics=(state or {}).get('metrics',{})
        hp=metrics.get('hp',{});food=metrics.get('fullness',{})
        return bool(state and state.get('eligible') and not state.get('menu_evidence') and m.get('status')=='observed' and not m.get('retained') and not m.get('enemies')
          and hp.get('values') and not hp.get('retained') and hp['values'][0]/max(1,hp['values'][1])>=.75 and food.get('values') and food['values'][0]>20 and not food.get('retained'))
    @staticmethod
    def signature(png):
        with Image.open(io.BytesIO(png)) as im:
            im=im.convert('RGB');w,h=im.size
            hud=hashlib.sha256(im.crop((0,0,w,round(h*.15))).tobytes()).digest()
            return im.size,hud,TurnObserver.grid(png)
    def remember(self,image,native,state,row,scope,observation,at=None):
        self.saved=None;self.active=False
        before=row.get('policy_state') or {};p=before.get('minimap',{}).get('player_cell');q=state.get('minimap',{}).get('player_cell')
        if not (self.quiet(before) and self.quiet(state) and native and native.get('status')=='ok' and image and row.get('executed') and not row.get('dry_run')
          and (row.get('input_receipt') or {}).get('os_submission')=='accepted' and row.get('observed_keys') in MOVES and observation and observation.kind=='normal'
          and p and q and max(abs(p[i]-q[i]) for i in (0,1))==1 and before['metrics'].get('floor',{}).get('values')==state['metrics'].get('floor',{}).get('values')):return
        size,hud,grid=self.signature(image)
        # Text elsewhere may be a condition, battle message or open UI. No reuse.
        for line in native.get('lines',[]):
            if any(b['box'][1]+b['box'][3]>size[1]*.15 for b in line.get('words',[]) if len(b.get('box',[]))==4):return
            if line.get('text','').strip() and not line.get('words'):return
        self.saved={'image':image,'native':copy.deepcopy(native),'state':copy.deepcopy(state),'scope':scope,'size':size,'hud':hud,'grid':grid,'at':time.monotonic() if at is None else at}
    def take(self,image,scope,now=None):
        saved=self.saved;self.saved=None;self.active=False
        now=time.monotonic() if now is None else now
        if not saved or scope!=saved['scope'] or not 0<=now-saved['at']<=2:self.misses+=1;return None
        size,hud,grid=self.signature(image)
        changed=TurnObserver.bits(saved['grid'],grid)
        if size!=saved['size'] or hud!=saved['hud'] or sum(changed)/len(changed)>.035:self.misses+=1;return None
        from .exterior_screen import white_frames
        if white_frames(image).get('visible') is not False:self.misses+=1;return None
        native=copy.deepcopy(saved['native']);native.update(frame_sha256=hashlib.sha256(image).hexdigest(),region_reuse={'source_frame_sha256':saved['native'].get('frame_sha256'),'basis':'exact_current_hud_pixels; no_text_outside_hud_in_source','original_recognition_times_preserved':True})
        neutral=TurnObservation(image,'boundary_neutral_sample',bool(any(changed)),sum(changed)/len(changed),0,round((now-saved['at'])*1000),timing_mode='measured_post_input_to_next_capture')
        self.active=True;self.hits+=1
        return {'native':native,'map':saved['state']['minimap'],'state':saved['state'],'neutral':neutral,'neutral_image':saved['image'],
          'evidence':{'mode':'previous_completed_input_baseline','age_ms':round((now-saved['at'])*1000),'source_map_frame_sha256':saved['state'].get('frame_sha256'),'coordinates_predicted':False}}
