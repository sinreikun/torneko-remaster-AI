"""Bounded compressed planning timelines accompany ordinary diagnostic ZIPs."""
import gzip

def export_traces(data,archive,remaining,detailed=False):
    report=[]
    paths=sorted((data/'planning').glob('*.jsonl'),key=lambda p:p.stat().st_mtime,reverse=True)[:2]
    for path in paths:
        limit=(12 if detailed else 4)*1024*1024;size=path.stat().st_size
        with path.open('rb') as src:
            if size>limit:src.seek(-limit,2);src.readline()
            raw=src.read(limit)
        budget=min(remaining,2*1024*1024 if detailed else 512*1024)
        packed=gzip.compress(raw,compresslevel=6)
        while len(packed)>budget and len(raw)>4096:
            raw=raw[len(raw)//2:];raw=raw.partition(b'\n')[2];packed=gzip.compress(raw,compresslevel=6)
        if not raw or len(packed)>budget:continue
        name='planning/'+path.name+'.gz';archive.writestr(name,packed);remaining-=len(packed)
        report.append({'file':name,'original_bytes':size,'included_uncompressed_bytes':len(raw),'records':raw.count(b'\n'),'tail_truncated':size>len(raw),'source':'actual_plan_input_prediction_outcome_timeline'})
    return remaining,report
