"""Build a native Windows x64 bootstrapper; embeds ONLY our source, not runtimes."""
from pathlib import Path
import argparse, hashlib, io, json, os, subprocess, zipfile
root=Path(__file__).resolve().parents[1]

def payload():
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        paths=sorted(p for folder in ('autoplayer','knowledge') for p in (root/folder).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
        for p in paths:
            info=zipfile.ZipInfo(p.relative_to(root).as_posix(),(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,p.read_bytes())
    return output.getvalue()

def generate():
    build=root/'build';build.mkdir(exist_ok=True)
    app=payload(); ps=b'\xef\xbb\xbf'+(root/'launcher/bootstrap.ps1').read_text(encoding='utf-8-sig').encode('utf-8')
    (build/'application.zip').write_bytes(app)
    (build/'bootstrap.ps1').write_bytes(ps)
    def c_array(name,data):
        return 'static const unsigned char '+name+'[]={\n'+',\n'.join(','.join(str(b) for b in data[i:i+24]) for i in range(0,len(data),24))+'\n};\n'
    (build/'payload.h').write_text(c_array('bootstrap_ps1',ps)+c_array('application_zip',app),encoding='ascii')
    return build

def main():
    p=argparse.ArgumentParser();p.add_argument('--zig',default='zig');p.add_argument('--payload-only',action='store_true');args=p.parse_args()
    build=generate()
    if args.payload_only: return
    out=root/'dist';out.mkdir(exist_ok=True)
    exe=out/'LocalGamePilot.exe'
    environment={**os.environ,'ZIG_GLOBAL_CACHE_DIR':str(build/'zig-global'),'ZIG_LOCAL_CACHE_DIR':str(build/'zig-local')}
    subprocess.run([args.zig,'cc','-target','x86_64-windows-gnu','-Os','-s','-municode','-Wl,--subsystem,windows',
                    '-Wall','-Wextra','-Werror','-I',str(build),str(root/'launcher/main.c'),'-o',str(exe),'-luser32','-lshell32'],check=True,env=environment)
    digest=hashlib.sha256(exe.read_bytes()).hexdigest()
    (out/'LocalGamePilot.exe.sha256').write_text(f'{digest}  {exe.name}\n',encoding='ascii')
    print(json.dumps({'path':str(exe),'bytes':exe.stat().st_size,'sha256':digest},indent=2))
if __name__=='__main__': main()
