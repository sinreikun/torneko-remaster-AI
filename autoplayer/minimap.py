"""Conservative pixel observations of the user's blue/yellow/red minimap legend.
No game input and no model-generated coordinates. Detection can be unavailable.
"""
import hashlib
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
            if (60<=r<=205 and g>105 and g-r>=18 and b-g>=8) or (isolated and b>70 and b>r*1.4 and b>g*1.08):blue.add((x,y))
            if r>90 and g>95 and b<min(r,g)*.55 and abs(r-g)<60:yellow.add((x,y))
            if r>145 and r>g*1.6 and r>b*1.5:red.add((x,y))
    candidates=[];yellow_parts=components(yellow)
    for region in components(blue,gap=2):
        if not 30<=len(region)<=width*height*.12:continue
        xs,ys=zip(*region);left,right=min(xs),max(xs);top,bottom=min(ys),max(ys)
        if max(right-left,bottom-top)<5:continue
        markers=[part for part in yellow_parts
            if any(left-3<=x<=right+3 and top-3<=y<=bottom+3 for x,y in part) and 2<=len(part)<=100 and max(p[0] for p in part)-min(p[0] for p in part)<=12 and max(p[1] for p in part)-min(p[1] for p in part)<=12]
        if len(markers)!=1:continue
        marker=markers[0];mx=sum(p[0] for p in marker)/len(marker);my=sum(p[1] for p in marker)/len(marker)
        # A small, roughly square yellow marker must touch the blue overlay.
        if not any((x+dx,y+dy) in blue for x,y in marker for dx,dy in ((2,0),(-2,0),(0,2),(0,-2))):continue
        tile=max(3,max(max(p[i] for p in marker)-min(p[i] for p in marker)+2 for i in (0,1)))
        # The antialiased edge can remove one marker pixel at this scale.
        if tile in (5,6):tile=6
        if tile>12:continue
        enemies=components({p for p in red if left-tile<=p[0]<=right+tile and top-tile<=p[1]<=bottom+tile})
        enemy_cells=[(round((sum(x for x,y in part)/len(part)-mx)/tile),round((sum(y for x,y in part)/len(part)-my)/tile)) for part in enemies if len(part)>=2]
        nearby={}
        for key,(dx,dy) in {'w':(0,-1),'a':(-1,0),'s':(0,1),'d':(1,0)}.items():
            cx,cy=round(mx+dx*tile),round(my+dy*tile)
            known=sum((cx+xx,cy+yy) in blue for xx in (-1,0,1) for yy in (-1,0,1))>=3
            nearby[key]={'known':known,'enemy':(dx,dy) in enemy_cells,'unobserved_cell':'wall_or_unexplored_unknown'}
        # Shape is translation-independent; pose remains an approximate screen observation.
        shape=quantized_shape(region,left,top,tile)
        candidates.append({'status':'observed','confidence':.8,'bbox':[left,top,right,bottom],
            'player':[round(mx,2),round(my,2)],'cell_size':tile,'player_cell':[round(mx/tile),round(my/tile)],
            '_region':region,'known_pixels':len(region),'shape':hashlib.sha256(repr(shape).encode()).hexdigest()[:16],
            'neighbors':nearby,'enemies':enemy_cells,'analysis_size':[width,height],'source_size':list(source_size),
            'note':'色・形状の推定。座標は画面内であり、固定のワールド座標とは未確認。'})
    # The yellow marker may split the blue overlay into disconnected pieces.
    # Multiple blue components attached to the *same* marker are one map, not
    # competing player detections. Distinct markers remain ambiguous.
    grouped={}
    for candidate in candidates:
        grouped.setdefault(tuple(candidate['player']),[]).append(candidate)
    merged=[]
    for parts in grouped.values():
        item=dict(parts[0]);region=set().union(*(set(p['_region']) for p in parts))
        xs,ys=zip(*region);left,top,right,bottom=min(xs),min(ys),max(xs),max(ys)
        tile=item['cell_size']
        shape=quantized_shape(region,left,top,tile)
        item.update(bbox=[left,top,right,bottom],known_pixels=len(region),
            shape=hashlib.sha256(repr(shape).encode()).hexdigest()[:16],blue_components=len(parts),
            enemies=sorted({tuple(e) for p in parts for e in p['enemies']}))
        for key in item['neighbors']:
            item['neighbors'][key]=dict(item['neighbors'][key])
            item['neighbors'][key]['known']=any(p['neighbors'][key]['known'] for p in parts)
            item['neighbors'][key]['enemy']=any(p['neighbors'][key]['enemy'] for p in parts)
        item.pop('_region',None);merged.append(item)
    if len(merged)!=1:return {'status':'unavailable','reason':'ミニマップと自位置を一意に検出できません。','candidates':len(merged)}
    return merged[0]
