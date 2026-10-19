"""Exclude the display overlay from model inputs; keep raw evidence unchanged."""
import io
import re

REFERENCES=re.compile(r'ミニマップ|地図|マップ|minimap|\bmap\b|青い(?:物体|図形|表示|箱|四角|領域|ブロック)|黄色.{0,12}(?:四角|円形|マーカー)|赤い点|blue[_ ](?:object|box|shape|display)|yellow[_ ](?:square|circle)|red[_ ]dot',re.I)
PRIVATE_KEYS={'minimap','minimap_pixels','minimap_observation','player_cell','cell','map_status','_floor_observed'}

def clean_text(text):
    if text=='num7':return ''
    return '。'.join(part for part in re.split(r'[。\n]',text) if part.strip() and not REFERENCES.search(part))

def model_data(value):
    if isinstance(value,str):return clean_text(value)
    if isinstance(value,list):
        return [v for item in value if (v:=model_data(item)) not in ('',None,{},[])]
    if isinstance(value,dict):
        if 'summary' in value and REFERENCES.search(str(value.get('id',''))+' '+str(value['summary'])):return {}
        result={}
        for key,item in value.items():
            if key in PRIVATE_KEYS or key=='num7' or key.startswith('_'):continue
            cleaned=model_data(item)
            if cleaned not in ('',{},[]):result[key]=cleaned
        return result
    return value

class OverlayExclusion:
    def __init__(self):self.rect=None;self.size=None
    def prepare(self,png,observed=None,required=False):
        if png is None or not png.startswith(b'\x89PNG\r\n\x1a\n'):return png,{}
        from PIL import Image,ImageDraw
        from .minimap import read_minimap
        with Image.open(io.BytesIO(png)) as source:
            size=source.size;image=source.convert('RGB')
        if self.size!=size:self.rect=None;self.size=size
        evidence=observed if observed and observed.get('status')=='observed' else read_minimap(png)
        if evidence.get('status')=='observed' and evidence.get('source_size')==list(size):
            w,h=evidence['analysis_size'];box=evidence['bbox'];pad=evidence['cell_size']*2
            rect=[max(0,int((box[0]-pad)*size[0]/w)),max(int(size[1]*.15),int((box[1]-pad)*size[1]/h)),
                  min(size[0],int((box[2]+pad+1)*size[0]/w)),min(size[1],int((box[3]+pad+1)*size[1]/h))]
            if rect[2]>rect[0] and rect[3]>rect[1]:
                self.rect=rect if self.rect is None else [min(self.rect[0],rect[0]),min(self.rect[1],rect[1]),max(self.rect[2],rect[2]),max(self.rect[3],rect[3])]
        if not required and evidence.get('status')!='observed':return png,{'hidden_regions':[]}
        if self.rect is None:
            if required:raise ValueError('除外するUI領域を特定できないため、画像推論を保留します。')
            return png,{'hidden_regions':[]}
        ImageDraw.Draw(image).rectangle(self.rect,fill=(96,96,96))
        out=io.BytesIO();image.save(out,format='PNG')
        return out.getvalue(),{'hidden_regions':[list(self.rect)],'source_size':list(size),'note':'中立色で隠したUI領域は観測不能。物体・通路・穴・壁として解釈せず、移動先にしない。'}


def ocr_without_regions(native,regions):
    def overlaps(line):
        for word in line.get('words',[]):
            box=word.get('box')
            if not isinstance(box,list) or len(box)!=4:continue
            x,y,w,h=box
            if any(x<r[2] and x+w>r[0] and y<r[3] and y+h>r[1] for r in regions):return True
        return False
    return {**native,'lines':[line for line in native.get('lines',[]) if not overlaps(line)]}
