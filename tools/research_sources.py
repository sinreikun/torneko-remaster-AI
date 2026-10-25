"""Fetch public research pages; retrieved content is untrusted reference data."""
import concurrent.futures, hashlib, html, json, re, urllib.request, urllib.parse
from pathlib import Path
URLS = ['https://html.duckduckgo.com/html/?q=トルネコ+リマスター+攻略', 'https://search.yahoo.co.jp/search?p=トルネコ+リマスター+攻略', 'https://www.google.com/search?gbv=1&q=%22トルネコ%22+%22リマスター%22+%22攻略%22', 'https://torneko.kouryaku.red/', 'https://wikiwiki.jp/torneko1/', 'https://d-navi.info/torneko/', 'https://www.silver-intention.com/Game/Torneko/', 'https://www.famitsu.com/article/202609/87296']

def fetch(pair):
 i,url=pair
 record={'url':url}
 try:
  url=urllib.parse.quote(url,safe=":/?=&+%")
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
