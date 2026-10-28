"""Publish a deterministic application bundle; never rebuild/replace the fixed launcher."""
from pathlib import Path
import hashlib, io, json, zipfile
from tools import build_exe

ROOT=Path(__file__).resolve().parents[1]

def bundle():
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,data in [('bootstrap.ps1',b'\xef\xbb\xbf'+(ROOT/'launcher/bootstrap.ps1').read_text(encoding='utf-8-sig').encode()),('application.zip',build_exe.payload())]:
            info=zipfile.ZipInfo(name,(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,data)
    return output.getvalue()

def publish():
    import ast
    module=ast.parse((ROOT/'autoplayer/__init__.py').read_text())
    version=next(ast.literal_eval(node.value) for node in module.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='__version__' for t in node.targets))
    data=bundle();digest=hashlib.sha256(data).hexdigest()
    manifest=dict(protocol=1,version=version,sha256=digest,bytes=len(data),path=f'apps/{digest}.zip')
    release=ROOT/'release';(release/'apps').mkdir(parents=True,exist_ok=True)
    (release/manifest['path']).write_bytes(data)
    (release/'latest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(manifest,indent=2))

if __name__=='__main__':publish()
