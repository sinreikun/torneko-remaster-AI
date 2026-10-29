"""Keep pythonw startup errors visible and diagnosable."""
import os
import sys
import traceback
from pathlib import Path

def main():
    try:
        from .ui import main
        main()
    except Exception:
        detail=traceback.format_exc()
        data=Path(os.environ.get('GAMEPILOT_DATA_DIR',Path(__file__).resolve().parent.parent/'data'))
        try:
            data.mkdir(parents=True,exist_ok=True)
            (data/'startup-error.log').write_text(detail,encoding='utf-8')
        except OSError:
            pass
        if sys.platform=='win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,f'起動に失敗しました。\n{data / "startup-error.log"}\n\n{detail[-1500:]}','Local Game Pilot',0x10)
        else:
            sys.stderr.write(detail)
        raise SystemExit(1)

if __name__=='__main__':main()
