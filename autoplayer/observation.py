"""Bounded settle detection adapted from the supplied TurnDetector/MotionMask design."""
import io
import time
from dataclasses import dataclass, field

@dataclass
class TurnObservation:
    image: bytes
    kind: str
    changed: bool
    raw_change: float
    masked_activity: float
    duration_ms: int
    last_activity_ms: int | None = None
    requested_minimum_ms: int = 0
    applied_minimum_ms: int = 0
    timing_mode: str = "standard"
    scene_boundary: dict = field(default_factory=dict)

class TurnObserver:
    def __init__(self): self.motion={};self.masks={};self.fast_mode=False;self.timing_samples={};self.failures={};self.neutral_calls=0;self.input_signature=();self.track_scene_boundaries=False
    @staticmethod
    def grid(png):
        from PIL import Image
        with Image.open(io.BytesIO(png)) as image:
            return list(image.convert('L').resize((32,24)).tobytes())
    @staticmethod
    def bits(a,b): return [abs(x-y)>8 for x,y in zip(a,b)]
    def observe(self,capture,baseline,scene,stop,minimum_ms=0,clock=time.monotonic):
        requested_minimum=minimum_ms;fast=self.fast_mode and scene!='human_console'
        initial=self.grid(baseline)
        bands=tuple(int(sum(v for i,v in enumerate(initial) if (i//32>=12)*2+(i%32>=16)==b)/192/32) for b in range(4)) if fast else ()
        key=(self.input_signature,bands);samples=self.timing_samples.get(key,[])
        calibrated=fast and scene!='neutral_observation' and len(samples)>=4
        if calibrated:
            duration=sorted(samples)[min(len(samples)-1,int(len(samples)*.9))]
            minimum_ms=min(minimum_ms,max(350,int(duration*1.3+180)+150*self.failures.get(key,0)))
        if scene=='neutral_observation':self.neutral_calls+=1
        quick_neutral=fast and scene=='neutral_observation' and self.neutral_calls%8!=0
        mode='short_neutral' if quick_neutral else 'adaptive_margin' if calibrated else 'standard'
        def is_dark(values):return bool(values) and sum(v<24 for v in values)/len(values)>.92 and sum(values)/len(values)<18
        def is_black(values):return bool(values) and sum(v<8 for v in values)/len(values)>.985 and sum(values)/len(values)<6
        boundary={'blackout_seen':self.track_scene_boundaries and is_black(initial),'sampled':self.track_scene_boundaries,'dark_seen':self.track_scene_boundaries and is_dark(initial),'dark_samples':0,'sample_count':0}
        previous=initial; anchor=initial
        mask=self.masks.get(scene,set());late=set();stable_since=None
        start=clock();maximum=max(1.2,min(8.,minimum_ms/1000+2.))
        image=baseline;changed=False;kind='timeout';current=initial
        while not stop.wait(.06 if fast else .1):
            image=capture();current=self.grid(image);now=clock();elapsed=now-start
            if self.track_scene_boundaries:
                boundary['sample_count']+=1
                if is_dark(current):boundary['dark_seen']=True;boundary['dark_samples']+=1
                if is_black(current):boundary['blackout_seen']=True
            raw=self.bits(initial,current)
            changed=changed or any(bit and i not in mask for i,bit in enumerate(raw))
            moving={i for i,bit in enumerate(self.bits(previous,current)) if bit}
            if elapsed>=maximum*2/3: late|=moving
            if any(bit and i not in mask for i,bit in enumerate(self.bits(anchor,current))):
                anchor=current;stable_since=now
            elif stable_since is None: stable_since=now
            if quick_neutral and elapsed>=.3 and not (self.track_scene_boundaries and is_black(current)):kind='neutral_sample';break
            if elapsed>=minimum_ms/1000 and changed and now-stable_since>=.25 and not (self.track_scene_boundaries and is_black(current)):
                kind='normal';break
            if not changed and elapsed>=max(1.2,minimum_ms/1000): kind='no_change';break
            if elapsed>=maximum: break
            previous=current
        if stop.is_set(): kind='aborted'
        if kind=='timeout':
            counts=self.motion.setdefault(scene,{})
            for i in late: counts[i]=counts.get(i,0)+1
            learned={i for i,n in counts.items() if n>=2}
            if len(learned)/len(initial)<=.12: self.masks[scene]=learned
        if fast and scene!='neutral_observation':
            if kind=='normal' and changed and stable_since is not None:
                self.timing_samples[key]=(samples+[max(0,(stable_since-start)*1000)])[-12:]
                self.failures[key]=max(0,self.failures.get(key,0)-1)
            elif kind in ('timeout','no_change'):self.failures[key]=min(10,self.failures.get(key,0)+1)
        boundary['final_dark']=self.track_scene_boundaries and is_dark(current)
        boundary['dark_recovered']=boundary['dark_seen'] and not boundary['final_dark']
        raw=self.bits(initial,current)
        return TurnObservation(image,kind,any(bit and i not in mask for i,bit in enumerate(raw)),sum(raw)/len(raw),sum(raw[i] for i in mask)/max(1,len(mask)),int((clock()-start)*1000),
            max(0,int((stable_since-start)*1000)) if stable_since is not None else None,requested_minimum,minimum_ms,mode,boundary)


def image_color_facts(png):
    """Small measured pixel facts, independent of model captions and goals."""
    if not png.startswith(b'\x89PNG\r\n\x1a\n'): return None
    from PIL import Image
    import io,colorsys
    with Image.open(io.BytesIO(png)) as original:
        image=original.convert('RGB').resize((64,64)).quantize(colors=8)
    count,index=max(image.getcolors(),key=lambda value:value[0])
    rgb=image.getpalette()[index*3:index*3+3]
    hue,saturation,value=colorsys.rgb_to_hsv(*(channel/255 for channel in rgb))
    if value<.12: color='black'
    elif saturation<.15: color='white' if value>.85 else 'gray'
    elif hue<.06 or hue>=.94: color='red'
    elif hue<.15: color='orange'
    elif hue<.20: color='yellow'
    elif hue<.46: color='green'
    elif hue<.55: color='cyan'
    elif hue<.75: color='blue'
    elif hue<.90: color='purple'
    else: color='pink'
    return {'dominant_rgb':rgb,'dominant_color':color,'fraction':round(count/4096,3),
        'description':f'Dominant pixel color: {color}; RGB {rgb}.',
        'note':'A pixel measurement is not proof of scene type or goal completion.'}
