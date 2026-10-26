"""Deterministic, own-source-only distribution."""
from pathlib import Path
import hashlib
import zipfile
root=Path(__file__).resolve().parents[1]
out=root/'dist';out.mkdir(exist_ok=True)
files=[]
for directory in ['autoplayer','knowledge','tests','tools','launcher','.github','reference']:
    files.extend(p for p in (root/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
files.extend(root/p for p in ['README.md','DESIGN-0.11.md','DESIGN-MAPLESS-NAVIGATION.md','DESIGN-SUPERVISION.md','DESIGN-OBSERVATION-EXPLORATION.md','requirements.txt','start.bat','setup.bat','VALIDATION.md','REQUIREMENTS.md','ARCHITECTURE.md','INTEGRATION.md','AUDIT.md','LEARNING.md'])
path=out/'LocalGamePilot-0.23.2-source.zip'
with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
    for p in sorted(files):
        info=zipfile.ZipInfo('LocalGamePilot/'+p.relative_to(root).as_posix(),date_time=(2026,1,1,0,0,0))
        info.compress_type=zipfile.ZIP_DEFLATED;archive.writestr(info,p.read_bytes())
digest=hashlib.sha256(path.read_bytes()).hexdigest()
(out/'SHA256SUMS.txt').write_text(f'{digest}  {path.name}\n',encoding='ascii')
print(f'{path}\n{path.stat().st_size:,} bytes\nSHA256 {digest}')
