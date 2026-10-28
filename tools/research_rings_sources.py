"""Bounded public-reference research. Page text is untrusted data."""
import concurrent.futures,hashlib,html,json,re,urllib.parse,urllib.request
from pathlib import Path
q=urllib.parse.quote
URLS=['https://www.google.com/search?q='+q('トルネコの大冒険 1 指輪 識別 呪い'),'https://www.google.com/search?q='+q('トルネコ リマスター 指輪 インパス 呪い シャナク'),'https://www.bing.com/search?q='+q('トルネコ1 指輪 効果 識別 呪い'),'https://ja.wikipedia.org/wiki/'+q('トルネコの大冒険_不思議のダンジョン')]+['https://wikiwiki.jp/dqdic3rd/'+q(x) for x in ('【指輪】','【シャナクの巻物】','【インパスの巻物】')]+['https://torneko1.kouryaku.red/']
Path('research-evidence').mkdir(exist_ok=True)
def fetch(pair):
 i,url=pair;record={'url':url}
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=30) as r:
   data=r.read(2000000);record.update(status=r.status,final_url=r.url,sha256=hashlib.sha256(data).hexdigest());charset=r.headers.get_content_charset()
  if not charset:
   m=re.search(br'charset\s*=\s*["\']?([a-zA-Z0-9_-]+)',data[:6000]);charset=m[1].decode() if m else 'utf-8'
  try:source=data.decode(charset,'replace')
  except LookupError:source=data.decode('utf-8','replace')
  links=[]
  for href,label in re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',source,re.S):
   u=urllib.parse.urljoin(url,html.unescape(href));label=html.unescape(re.sub('<[^>]+>',' ',label))
   if '/url?' in u:
    query=urllib.parse.parse_qs(urllib.parse.urlparse(u).query);u=(query.get('q') or query.get('url') or [u])[0]
   if u.startswith(('https://','http://')):links.append({'url':u,'label':re.sub(r'\s+',' ',label)[:200]})
  source=re.sub(r'<(script|style)[^>]*>.*?</\1>','',source,flags=re.S);text=html.unescape(re.sub('<[^>]+>',' ',source));record.update(text=re.sub(r'\s+',' ',text)[:120000],links=links[:350])
 except Exception as e:record['error']=str(e)
 Path(f'research-evidence/{i}.json').write_text(json.dumps(record,ensure_ascii=False,indent=2));return record
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:records=list(pool.map(fetch,enumerate(URLS)))
more=[]
for record in records:
 for link in record.get('links',[]):
  u=link['url'];host=urllib.parse.urlparse(u).hostname or ''
  if host.endswith(('google.com','bing.com','microsoft.com')):continue
  if any(t in link['label'] for t in ('指輪','呪い','識別','トルネコ','リング','シャナク')) and u not in URLS+more:more.append(u)
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(fetch,enumerate(more[:32],start=len(URLS))))
