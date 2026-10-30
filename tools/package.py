"""Deterministic, own-source-only distribution."""
from pathlib import Path
import ast
import hashlib
import zipfile
root=Path(__file__).resolve().parents[1]
out=root/'dist';out.mkdir(exist_ok=True)
files=[]
for directory in ['autoplayer','knowledge','tests','tools','launcher','.github','reference']:
    files.extend(p for p in (root/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
files.extend(root/p for p in ['README.md','DESIGN-PROCEDURE-EXECUTION.md','DESIGN-SPATIAL-CORE.md','DESIGN-ASYNC-RESEARCH.md','DESIGN-KNOWLEDGE.md','DESIGN-SPATIAL-UI.md','DESIGN-AUTONOMY.md','DESIGN-NEXTGEN.md','DESIGN-FAST-SUPERVISED.md','DESIGN-LONG-SESSION.md','DESIGN-EQUIPMENT-EXECUTION.md','DESIGN-DISPLAY-COST.md','DESIGN-LIVE-INVENTORY-RINGS.md','DESIGN-MAP-AND-EQUIPMENT.md','DESIGN-FRONTIER-SELECTION.md','DESIGN-AUTO-UPDATE.md','DESIGN-DECISION-ORDER.md','DESIGN-INVENTORY-REVIEW.md','DESIGN-SUPERVISION-CONTEXT.md','DESIGN-EQUIPMENT-PREPARATION.md','DESIGN-RECOVERY-DECISIONS.md','DESIGN-FLOOR-EVIDENCE.md','DESIGN-0.11.md','DESIGN-MAPLESS-NAVIGATION.md','DESIGN-SUPERVISION.md','DESIGN-OBSERVATION-EXPLORATION.md','requirements.txt','start.bat','setup.bat','VALIDATION.md','REQUIREMENTS.md','ARCHITECTURE.md','INTEGRATION.md','AUDIT.md','LEARNING.md'])
module=ast.parse((root/'autoplayer/__init__.py').read_text())
version=next(ast.literal_eval(node.value) for node in module.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='__version__' for t in node.targets))
path=out/f'LocalGamePilot-{version}-source.zip'
with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
    for p in sorted(files):
        info=zipfile.ZipInfo('LocalGamePilot/'+p.relative_to(root).as_posix(),date_time=(2026,1,1,0,0,0))
        info.compress_type=zipfile.ZIP_DEFLATED;archive.writestr(info,p.read_bytes())
digest=hashlib.sha256(path.read_bytes()).hexdigest()
(out/'SHA256SUMS.txt').write_text(f'{digest}  {path.name}\n',encoding='ascii')
print(f'{path}\n{path.stat().st_size:,} bytes\nSHA256 {digest}')
