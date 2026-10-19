"""Bounded settle detection adapted from the supplied TurnDetector/MotionMask design."""
import io
import time
from dataclasses import dataclass

@dataclass
class TurnObservation:
    image: bytes
    kind: str
    changed: bool
    raw_change: float
    masked_activity: float
    duration_ms: int
    last_activity_ms: int | None = None

class TurnObserver:
    def __init__(self): self.motion={};self.masks={}
    @staticmethod
    def grid(png):
        from PIL import Image
        with Image.open(io.BytesIO(png)) as image:
            return list(image.convert('L').resize((32,24)).tobytes())
    @staticmethod
    def bits(a,b): return [abs(x-y)>8 for x,y in zip(a,b)]
    def observe(self,capture,baseline,scene,stop,minimum_ms=0,clock=time.monotonic):
        initial=self.grid(baseline); previous=initial; anchor=initial
        mask=self.masks.get(scene,set());late=set();stable_since=None
        start=clock();maximum=max(1.2,min(8.,minimum_ms/1000+2.))
        image=baseline;changed=False;kind='timeout';current=initial
        while not stop.wait(.1):
            image=capture();current=self.grid(image);now=clock();elapsed=now-start
            raw=self.bits(initial,current)
            changed=changed or any(bit and i not in mask for i,bit in enumerate(raw))
            moving={i for i,bit in enumerate(self.bits(previous,current)) if bit}
            if elapsed>=maximum*2/3: late|=moving
            if any(bit and i not in mask for i,bit in enumerate(self.bits(anchor,current))):
                anchor=current;stable_since=now
            elif stable_since is None: stable_since=now
            if elapsed>=minimum_ms/1000 and changed and now-stable_since>=.25:
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
        raw=self.bits(initial,current)
        return TurnObservation(image,kind,any(bit and i not in mask for i,bit in enumerate(raw)),sum(raw)/len(raw),sum(raw[i] for i in mask)/max(1,len(mask)),int((clock()-start)*1000),
            max(0,int((stable_since-start)*1000)) if stable_since is not None else None)


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
