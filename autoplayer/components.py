"""Explicit optional components. External binaries are never bundled or auto-executed."""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

CATALOG=[
 {'name':'Python 3.13 / Tk', 'kind':'runtime', 'size':'約30 MB（インストーラー）', 'license':'PSF',
  'description':'本体の実行に必須。インストール時に Tcl/Tk と pip を含めてください。',
  'url':'https://www.python.org/downloads/windows/'},
 {'name':'Python 3.12 / Tk', 'kind':'runtime', 'size':'約25 MB（インストーラー）', 'license':'PSF',
  'description':'既存環境との互換性を優先する選択肢。Python 3.12 以上が必要です。',
  'url':'https://www.python.org/downloads/windows/'},
 {'name':'Pillow', 'kind':'capture', 'size':'約7 MB（版・OSにより変動）', 'license':'MIT-CMU',
  'description':'Windows 画面キャプチャに必須。現在の Python に pip で導入します。',
  'url':'https://pypi.org/project/Pillow/'},
 {'name':'Ollama', 'kind':'server', 'size':'数百 MB〜（版により変動）', 'license':'MIT（モデルは別ライセンス）',
  'description':'推奨。画像モデルを管理し、ローカル API を提供。公式配布ページから取得・インストールします。',
  'url':'https://ollama.com/download/windows'},
 {'name':'llama.cpp', 'kind':'server', 'size':'数十〜数百 MB（CPU/CUDA/Vulkan により変動）', 'license':'MIT（モデルは別ライセンス）',
  'description':'上級者向け。画像対応 GGUF と対応する mmproj が必要。llama-server の OpenAI 互換 API を使用。',
  'url':'https://github.com/ggml-org/llama.cpp/releases'},
 {'name':'LM Studio', 'kind':'server', 'size':'数百 MB〜', 'license':'独自利用規約',
  'description':'GUI でモデルを選ぶ選択肢。画像対応モデルを読み込み、ローカルサーバーを開始してください。',
  'url':'https://lmstudio.ai/download'},
]
MODELS=[
 {'name':'qwen2.5vl:3b','size':'約3.2 GB','license':'Qwen Research License（利用条件を確認）','description':'軽量寄り。小さな文字や複雑な状況の判断精度には限界があります。','url':'https://ollama.com/library/qwen2.5vl:3b'},
 {'name':'qwen2.5vl:7b','size':'約6 GB','license':'Apache-2.0','description':'推奨の初期選択。必要メモリーはモデル容量より大きく、画像・文脈長でも増加します。','url':'https://ollama.com/library/qwen2.5vl:7b'},
 {'name':'qwen2.5vl:32b','size':'約21 GB','license':'Apache-2.0','description':'大容量メモリー向け。判断精度を優先する候補ですが、速度と攻略成功は保証されません。','url':'https://ollama.com/library/qwen2.5vl:32b'},
 {'name':'gemma3:4b','size':'約3.3 GB','license':'Gemma Terms','description':'画像対応の別系列。Ollama で利用規約を確認して選択してください。','url':'https://ollama.com/library/gemma3:4b'},
]

def install_capture():
    subprocess.run([sys.executable,'-m','pip','install','Pillow>=11,<13'],check=True)

def pull_model(endpoint,model,report,stop):
    from .core import Settings
    Settings(endpoint=endpoint).validate()
    if model not in {m['name'] for m in MODELS}: raise ValueError('未登録のモデルです。')
    request=urllib.request.Request(endpoint.rstrip('/')+'/api/pull',json.dumps({'name':model,'stream':True}).encode(),{'Content-Type':'application/json'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    success=False
    with opener.open(request,timeout=60) as response:
        for line in response:
            if stop.is_set(): return False
            value=json.loads(line)
            if 'error' in value: raise RuntimeError(value['error'])
            report(value)
            if value.get('status')=='success': success=True
    if not success: raise RuntimeError('ダウンロード完了を確認できませんでした。')
    return True
