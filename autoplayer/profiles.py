"""Persistent game identities and editable settings, independent of window titles."""
import hashlib
import json
from dataclasses import asdict
from pathlib import Path


class ProfileStore:
    def __init__(self, folder):
        self.folder = Path(folder)

    @staticmethod
    def identity(target):
        # Titles, HWNDs and PIDs may change on every launch.
        source = [target.get('executable', '').casefold(), target.get('class_name', '')]
        host=source[0].replace('\\','/').rsplit('/',1)[-1]
        if not source[0] or host in ('chrome.exe','msedge.exe','firefox.exe','opera.exe','python.exe','pythonw.exe','java.exe','javaw.exe','dotnet.exe'):
            source.append(target['title'])
        return hashlib.sha256(json.dumps(source, ensure_ascii=False).encode()).hexdigest()[:24]

    def all(self):
        result = []
        for path in sorted(self.folder.glob('*.json')):
            try:
                item = json.loads(path.read_text(encoding='utf-8'))
                if item.get('id') == path.stem:
                    result.append(item)
            except (OSError, ValueError):
                continue
        return result

    def load(self, identity):
        return next((p for p in self.all() if p['id'] == identity), None)

    def save(self, target, cfg):
        identity = cfg.profile_id or self.identity(target)
        if len(identity) != 24 or any(c not in '0123456789abcdef' for c in identity):
            raise ValueError('プロファイルIDが不正です。')
        cfg.profile_id = identity
        value = {'id': identity, 'name': target['title'],
                 'target': {k: target.get(k, '') for k in ('title', 'executable', 'class_name')},
                 'settings': asdict(cfg)}
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / (identity + '.json')
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
        return value

    def select(self, target, previous):
        from .core import Settings
        from .controls import is_torneko
        identity = self.identity(target)
        existing = self.load(identity)
        if existing:
            cfg = Settings(**existing['settings'])
            cfg.window_title = target['title']
            return cfg.validate()
        shared = {k: getattr(previous, k) for k in ('endpoint', 'provider', 'model', 'ocr_engine', 'timeout', 'compute_mode')}
        if is_torneko(target['title']):
            cfg = Settings(**shared)
            from .controls import ACTIONS
            cfg.key_bindings = dict(ACTIONS)
            knowledge = Path(__file__).resolve().parent.parent / 'knowledge' / 'torneko.md'
            if knowledge.is_file():
                cfg.knowledge = knowledge.read_text(encoding='utf-8')
        else:
            cfg = Settings(**shared, allowed_keys=['up','down','left','right','enter','escape','space'], knowledge='')
        cfg.window_title = target['title']
        cfg.profile_id = identity
        cfg.max_steps = 0
        cfg.resilient = True
        cfg.adaptive_timing = True
        return cfg.validate()


def parse_bindings(text):
    """Pasteable key=meaning lines; no executable expressions or OS shortcuts."""
    result = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        key, separator, meaning = line.partition('=')
        if not separator or not meaning.strip():
            raise ValueError('キー配置は1行に key=操作内容 の形式で指定してください。')
        result[key.strip().lower()] = meaning.strip()
    return result
