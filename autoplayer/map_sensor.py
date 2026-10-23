"""Human-visible Torneko map: 54 columns x 32 rows; never game memory.
Geometry is calibrated from the user's full-map and 5x5-room screenshots.
"""
import hashlib
import io
import time
from functools import lru_cache
from collections import Counter

class FloorConfirmation:
    def __init__(self):
        self.floor=None;self.pending=None;self.samples=set();self.started=0
    def update(self,values,token,now=None):
        now=time.monotonic() if now is None else now
        valid=isinstance(values,(list,tuple)) and len(values)==1 and isinstance(values[0],int) and 1<=values[0]<=999
        if not valid:
            self.pending=None;self.samples.clear()
            return {'status':'unreadable','confirmed':self.floor,'changed':False}
        value=values[0]
        if self.floor is None:
            self.floor=value
            return {'status':'initialized','confirmed':value,'changed':False}
        if value==self.floor:
            self.pending=None;self.samples.clear()
            return {'status':'confirmed','confirmed':value,'changed':False}
        if value!=self.pending:
            self.pending=value;self.samples=set();self.started=now
        self.samples.add(token)
        changed=len(self.samples)>=3 and now-self.started>=.6
        previous=self.floor;count=len(self.samples)
        if changed:
            self.floor=value;self.pending=None;self.samples.clear()
        return {'status':'changed' if changed else 'pending','confirmed':self.floor,
                'observed':value,'previous':previous,'changed':changed,'samples':count}

class MapSensor:
    def __init__(self):self.reset()
    def reset(self):self.cells=set();self.floor=None
    def accumulate(self,result,floor):
        if result.get('status')!='observed':return result
        # Called only after OCR floor confirmation; pending frames never enter this map.
        self.floor=floor
        px,py=result['player_cell']
        self.cells.update((x+px,y+py) for x,y in result['known_cells'])
        result=dict(result);result['accumulated_cells']=[list(p) for p in sorted(self.cells)]
        result['confirmed_floor']=floor
        return result

@lru_cache(maxsize=6)
def read_grid(png):
    try:return _read_grid(png)
    except (OSError,ValueError,ZeroDivisionError):
        return {'status':'unavailable','reason':'地図画像を読み取れません。'}

def _read_grid(png):
    from PIL import Image
    from .minimap import components,quantized_shape
    with Image.open(io.BytesIO(png)) as source:
        size=source.size
        # Keep markers about 10 pixels wide, independent of the capture resolution.
        image=source.convert('RGB').resize((1468,round(size[1]*1468/size[0])))
    w,h=image.size;pixels=image.load();tile=1468/120 # 12.2333, 54*32 calibrated cells
    left=(w-53*tile)/2;top=h/2-160
    if sum(max(rgb)<30 for rgb in image.getdata())<w*h*.7:
        return {'status':'unavailable','reason':'地図専用表示ではありません。'}
    yellow=set();red=set();cyan=set()
    for y in range(max(round(top),round(h*.2)),min(h,round(top+32*tile))):
        for x in range(max(0,round(left)),min(w,round(left+54*tile))):
            r,g,b=pixels[x,y]
            if r>90 and g>95 and b<min(r,g)*.55 and abs(r-g)<60:yellow.add((x,y))
            if r>110 and r>g*1.6 and r>b*1.5:red.add((x,y))
            if g>135 and b>125 and r<g*.7 and abs(g-b)<45:cyan.add((x,y))
    markers=[p for p in components(yellow) if 20<=len(p)<=180 and all(4<=max(v[i] for v in p)-min(v[i] for v in p)<=14 for i in (0,1))]
    if len(markers)!=1:return {'status':'unavailable','reason':'54×32格子内の黄色い自位置が一意ではありません。','sensor':'fixed_grid_54x32'}
    marker=markers[0];mx=sum(x for x,y in marker)/len(marker);my=sum(y for x,y in marker)/len(marker)
    player=[round((mx-left)/tile-.5),round((my-top)/tile-.5)]
    # Small alignment correction only; do not silently invent a different grid.
    ex=mx-(left+(player[0]+.5)*tile);ey=my-(top+(player[1]+.5)*tile)
    if max(abs(ex),abs(ey))>tile*.25:return {'status':'unavailable','reason':'マーク中心と固定格子の配置が一致しません。','sensor':'fixed_grid_54x32'}
    left+=ex;top+=ey
    colors=Counter();marks=yellow|red|cyan
    for y in range(max(0,round(my-tile)),min(h,round(my+tile))):
        for x in range(max(0,round(mx-tile)),min(w,round(mx+tile))):
            rgb=pixels[x,y]
            if (x,y) in marks or max(rgb)<40 or max(rgb)-min(rgb)<25:continue
            colors[tuple(v//24 for v in rgb)]+=1
    if not colors:return {'status':'unavailable','reason':'地図の床色を校正できません。','sensor':'fixed_grid_54x32'}
    palette=colors.most_common(1)[0][0];region=set()
    for y in range(max(0,round(top)),min(h,round(top+32*tile))):
        for x in range(max(0,round(left)),min(w,round(left+54*tile))):
            if tuple(v//24 for v in pixels[x,y])==palette:region.add((x,y))
    if len(region)<50:return {'status':'unavailable','reason':'地図の床領域が不足しています。'}
    known={c for c in quantized_shape(region,mx,my,tile) if 0<=c[0]+player[0]<54 and 0<=c[1]+player[1]<32};known.add((0,0))
    objects={'enemies':[],'items':[],'stairs':[],'uncertain_marks':[]}
    for name,points in [('enemies',red),('cyan',cyan)]:
        for part in components(points):
            if not 8<=len(part)<=180:continue
            cx=sum(x for x,y in part)/len(part);cy=sum(y for x,y in part)/len(part)
            cell=(round((cx-mx)/tile),round((cy-my)/tile))
            if max(abs((cx-mx)/tile-cell[0]),abs((cy-my)/tile-cell[1]))>.28:continue
            if not (0<=cell[0]+player[0]<54 and 0<=cell[1]+player[1]<32):continue
            kind=name
            if name=='cyan':
                x0=min(x for x,y in part);x1=max(x for x,y in part);y0=min(y for x,y in part);y1=max(y for x,y in part)
                rx=max(1,(x1-x0)/2);ry=max(1,(y1-y0)/2);cx0=(x0+x1)/2;cy0=(y0+y1)/2
                corners=sum(abs(x-cx0)/rx>.65 and abs(y-cy0)/ry>.65 for x,y in part)
                # Square outlines have corner pixels, diamonds do not.
                kind='stairs' if corners>=4 else 'items'
            objects[kind].append(cell);known.add(cell)
    neighbors={k:{'known':v in known,'enemy':v in objects['enemies'],'unobserved_cell':'wall_or_unexplored_unknown'} for k,v in {'w':(0,-1),'a':(-1,0),'s':(0,1),'d':(1,0)}.items()}
    return {'status':'observed','sensor':'fixed_grid_54x32','confidence':.85,'grid_size':[54,32],
        'grid_origin':[left,top],'cell_size':tile,'player':[mx,my],'player_cell':player,
        'bbox':[left,top,left+54*tile,top+32*tile],'known_pixels':len(region),'known_cells':sorted(known),
        'shape':hashlib.sha256(repr(sorted(known)).encode()).hexdigest()[:16],
        'neighbors':neighbors,**objects,'analysis_size':[w,h],'source_size':list(size),
        'note':'ユーザー提示の54×32格子。未観測セルは壁と断定しない。マークは画面観測であり通行可能性の保証ではない。'}
