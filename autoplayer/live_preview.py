"""Measured preview presentation rate; repeated paint timers are not frames."""
def live_status(paint_times,captured_at,now,wall_now):
    samples=[t for t in paint_times if now-2<=t<=now]
    fps=(len(samples)-1)/(samples[-1]-samples[0]) if len(samples)>1 and samples[-1]>samples[0] else 0.
    age=max(0.,wall_now-captured_at)
    if age>1.:return f'取得待ち · {age:.1f}s前 · {fps:.1f} FPS'
    return f'LIVE · {fps:.1f} FPS · {age*1000:.0f}ms前'
