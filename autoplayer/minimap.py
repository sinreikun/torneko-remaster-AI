"""Conservative pixel observations of the user's blue/yellow/red minimap legend.
No game input and no model-generated coordinates. Detection can be unavailable.
"""
import hashlib
import io
from collections import deque
from functools import lru_cache


def components(points):
    remaining=set(points);out=[]
    while remaining:
        seed=remaining.pop();found=[seed];queue=deque([seed])
        while queue:
            x,y=queue.popleft()
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
                p=x+dx,y+dy
                if p in remaining:remaining.remove(p);found.append(p);queue.append(p)
        out.append(found)
    return out

@lru_cache(maxsize=6)
def read_minimap(png):
    if not png.startswith(b'\x89PNG\r\n\x1a\n'):return {'status':'unavailable','reason':'PNG画像を取得できていません。'}
    from PIL import Image
    try:
        with Image.open(io.BytesIO(png)) as source:
            source_size=source.size;image=source.convert('RGB');image.thumbnail((640,360))
    except (OSError,ValueError):return {'status':'unavailable','reason':'画像を読み取れません。'}
    width,height=image.size;blue=set();yellow=set();red=set()
    for y in range(height):
        for x in range(width):
            r,g,b=image.getpixel((x,y))
            if 60<=r<=205 and g>105 and g-r>=18 and b-g>=8:blue.add((x,y))
            if r>90 and g>95 and b<min(r,g)*.55 and abs(r-g)<60:yellow.add((x,y))
            if r>145 and r>g*1.6 and r>b*1.5:red.add((x,y))
    candidates=[]
    for region in components(blue):
        if not 30<=len(region)<=width*height*.12:continue
        xs,ys=zip(*region);left,right=min(xs),max(xs);top,bottom=min(ys),max(ys)
        if right-left<5 or bottom-top<5:continue
        markers=[part for part in components({p for p in yellow if left-3<=p[0]<=right+3 and top-3<=p[1]<=bottom+3})
            if 2<=len(part)<=100 and max(p[0] for p in part)-min(p[0] for p in part)<=12 and max(p[1] for p in part)-min(p[1] for p in part)<=12]
        if len(markers)!=1:continue
        marker=markers[0];mx=sum(p[0] for p in marker)/len(marker);my=sum(p[1] for p in marker)/len(marker)
        # A small, roughly square yellow marker must touch the blue overlay.
        if not any((x+dx,y+dy) in blue for x,y in marker for dx,dy in ((2,0),(-2,0),(0,2),(0,-2))):continue
        tile=max(3,max(max(p[i] for p in marker)-min(p[i] for p in marker)+2 for i in (0,1)))
        if tile>12:continue
        enemies=components({p for p in red if left-tile<=p[0]<=right+tile and top-tile<=p[1]<=bottom+tile})
        enemy_cells=[(round((sum(x for x,y in part)/len(part)-mx)/tile),round((sum(y for x,y in part)/len(part)-my)/tile)) for part in enemies if len(part)>=2]
        nearby={}
        for key,(dx,dy) in {'w':(0,-1),'a':(-1,0),'s':(0,1),'d':(1,0)}.items():
            cx,cy=round(mx+dx*tile),round(my+dy*tile)
            known=sum((cx+xx,cy+yy) in blue for xx in (-1,0,1) for yy in (-1,0,1))>=3
            nearby[key]={'known':known,'enemy':(dx,dy) in enemy_cells,'unobserved_cell':'wall_or_unexplored_unknown'}
        # Shape is translation-independent; pose remains an approximate screen observation.
        shape=sorted({(round((x-left)/tile),round((y-top)/tile)) for x,y in region})
        candidates.append({'status':'observed','confidence':.8,'bbox':[left,top,right,bottom],
            'player':[round(mx,2),round(my,2)],'cell_size':tile,'player_cell':[round(mx/tile),round(my/tile)],
            'known_pixels':len(region),'shape':hashlib.sha256(repr(shape).encode()).hexdigest()[:16],
            'neighbors':nearby,'enemies':enemy_cells,'analysis_size':[width,height],'source_size':list(source_size),
            'note':'色・形状の推定。座標は画面内であり、固定のワールド座標とは未確認。'})
    if len(candidates)!=1:return {'status':'unavailable','reason':'ミニマップと自位置を一意に検出できません。','candidates':len(candidates)}
    return candidates[0]
