"""Measured preview presentation rate; repeated paint timers are not frames."""
def live_status(paint_times,captured_at,now,wall_now):
    samples=[t for t in paint_times if now-2<=t<=now]
    fps=(len(samples)-1)/(samples[-1]-samples[0]) if len(samples)>1 and samples[-1]>samples[0] else 0.
    age=max(0.,wall_now-captured_at)
    if age>1.:return f'取得待ち · {age:.1f}s前 · {fps:.1f} FPS'
    return f'LIVE · {fps:.1f} FPS · {age*1000:.0f}ms前'

class PreviewBudget:
    """Bounded display-only adaptation; never changes observation resolution."""
    def __init__(self):self.period=.05;self.scale=1.;self.last_adjust=0.;self.cost=None
    def sample(self,capture_seconds,draw_seconds,now):
        cost=max(0,capture_seconds)+max(0,draw_seconds)
        self.cost=cost if self.cost is None else self.cost*.8+cost*.2
        if now-self.last_adjust<1.:return
        self.last_adjust=now
        if self.cost>self.period*.8:
            self.period=min(.125,max(.05,self.cost*1.2))
        elif self.cost<self.period*.45:
            self.period=max(.05,self.period*.9)
    def size(self,viewport):
        w,h=viewport
        return max(160,min(1280,round(w))),max(90,min(960,round(h)))

class PreviewFrameCache:
    """One recent window-owned frame; only previews may reuse it."""
    def __init__(self):self.image=None;self.hwnd=None;self.at=0.;self.wall=0.;self.sequence=0;self.from_preview=False
    def frame(self,hwnd,capture,now,wall,preview=False):
        if not preview or not self.from_preview or self.image is None or hwnd!=self.hwnd or not 0<=now()-self.at<=.04:
            image=capture(hwnd);image.thumbnail((1280,960))
            self.image=image;self.hwnd=hwnd;self.at=now();self.wall=wall();self.sequence+=1;self.from_preview=preview
        return self.image,self.wall,self.sequence
