"""Conservative pixel observations of the user's blue/yellow/red minimap legend.
No game input and no model-generated coordinates. Detection can be unavailable.
"""
import io
import math
from collections import deque,Counter
from functools import lru_cache


def components(points,gap=1):
    remaining=set(points);out=[]
    offsets=[(dx,dy) for dx in range(-gap,gap+1) for dy in range(-gap,gap+1) if 0<abs(dx)+abs(dy)<=gap]
    while remaining:
        seed=remaining.pop();found=[seed];queue=deque([seed])
        while queue:
            x,y=queue.popleft()
            for dx,dy in offsets:
                p=x+dx,y+dy
                if p in remaining:remaining.remove(p);found.append(p);queue.append(p)
        out.append(found)
    return out

def quantized_shape(region,left,top,tile):
    cells=Counter((round((x-left)/tile),round((y-top)/tile)) for x,y in region)
    # Require a small supported patch, not a single antialiased edge pixel.
    minimum=max(2,math.ceil(tile*tile*.1))
    return sorted(cell for cell,count in cells.items() if count>=minimum)

@lru_cache(maxsize=6)
def read_minimap(png,isolated=False):
    """Compatibility entry: only the calibrated grid sensor is used now."""
    if not isolated:return {'status':'unavailable','reason':'地図専用表示ではありません。'}
    from .map_sensor import read_grid
    return read_grid(png)
