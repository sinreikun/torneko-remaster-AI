"""Fetch public research pages; retrieved content is untrusted reference data."""
import concurrent.futures, hashlib, html, json, re, urllib.request, urllib.parse
from pathlib import Path
URLS = [
 'https://ja.wikipedia.org/wiki/'+urllib.parse.quote('トルネコの大冒険_不思議のダンジョン'),
 'https://en.wikipedia.org/wiki/Torneko_no_Daib%C5%8Dken:_Fushigi_no_Dungeon',
 'https://www.google.com/search?q='+urllib.parse.quote('トルネコ ちょっとステキなリマスター 公式'),
 'https://www.bing.com/search?q='+urllib.parse.quote('トルネコの大冒険 攻略 不思議のダンジョン 幸せの箱 27階'),
 'https://store.steampowered.com/search/?term=Torneko',
 'https://strategywiki.org/wiki/Torneko_no_Daibouken:_Fushigi_no_Dungeon',
]
def fetch(pair):
 i,url=pair
 record={'url':url}
 try:
  request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (public game reference research)'})
  with urllib.request.urlopen(request,timeout=45) as response:
   data=response.read(2000000);record.update(status=response.status,final_url=response.url,sha256=hashlib.sha256(data).hexdigest())
  source=data.decode('utf-8','replace')
  links=re.findall(r'href=[\"\'](https?[^\"\']+)',source)
  source=re.sub(r'<(script|style)[^>]*>.*?</\1>','',source,flags=re.S)
  text=html.unescape(re.sub('<[^>]+>',' ',source));text=re.sub(r'\s+',' ',text)
  record.update(text=text[:90000],links=links[:200])
 except Exception as error:record['error']=str(error)
 Path('research-evidence').mkdir(exist_ok=True)
 Path(f'research-evidence/{i}.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
 print(i,url,record.get('status',record.get('error')))
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(fetch,enumerate(URLS)))
