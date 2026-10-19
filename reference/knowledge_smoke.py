"""Unattended real OCR + sourced knowledge + local model test on an OWN synthetic HUD.
Never inject input, never claim this is a screenshot or playthrough of the commercial game.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import traceback
from PIL import Image,ImageDraw,ImageFont
from autoplayer.core import LocalLLM,Settings

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--endpoint',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();result={'fixture':'own synthetic dungeon HUD, not the target game','passed':False}
    try:
        image=Image.new('RGB',(1100,700),'#172033');draw=ImageDraw.Draw(image)
        font_path=Path(os.environ['WINDIR'])/'Fonts/arial.ttf'
        title=ImageFont.truetype(str(font_path),48);font=ImageFont.truetype(str(font_path),36)
        for xy,text,f in [((35,35),'SYNTHETIC DUNGEON HUD',title),((35,150),'HP 3/30',font),((35,225),'Fullness 5%',font),((35,300),'Floor 3',font),((35,400),'No victory or ending is displayed.',font),((35,500),'Choose a safe next objective.',font)]:draw.text(xy,text,font=f,fill='white')
        buffer=io.BytesIO();image.save(buffer,format='PNG');png=buffer.getvalue()
        cfg=Settings(endpoint=args.endpoint,model='qwen2.5vl:3b',window_title='トルネコ OCR 知識照合の合成試験',
            goal='表示された未クリアの状況から、安全を優先して攻略を進め、最終的にゲームクリアを目指す。今は次の小目標を自ら設定する。',
            knowledge='この画面は試験用の合成HUD。実ゲームの画面ではない。初代・リマスターの資料は参考情報として現在のOCRと比較し、未確認の仕様を断定しない。',timeout=600)
        llm=LocalLLM(cfg)
        result['frame_sha256']=hashlib.sha256(png).hexdigest()
        for attempt in range(3):
            try:
                decision=llm.decide(png,[],False)
                if decision.status=='complete':raise ValueError('No completed game is displayed; set a continuing subgoal with a visible success condition.')
                state=llm.take_state();record=state['ocr']
                if record.get('status')!='ok':raise AssertionError('Actual Windows OCR did not run: '+str(record))
                if not decision.subgoal.strip() or not decision.goal_success.strip():raise AssertionError('Missing model-chosen objective or criterion')
                if state['autonomous_goal']['frame_sha256']!=result['frame_sha256']:raise AssertionError('Goal is not linked to this frame')
                context=llm.knowledge_engine.reconcile(record)
                if context['metrics'].get('hp',{}).get('values')!=[3,30]:raise AssertionError('HP not extracted from actual OCR')
                if context['metrics'].get('fullness',{}).get('values')!=[5]:raise AssertionError('Fullness not extracted from actual OCR')
                result.update(passed=True,decision=decision.__dict__,state=state,metrics=context['metrics'],source_count=len(context['sources']),fact_count=len(llm.knowledge_engine.catalog['facts']))
                break
            except (ValueError,KeyError) as error:
                result['last_retry']=str(error);llm.set_context({'recovery':str(error),'instruction':'The whole game is not complete. Reassess current OCR and choose your own next subgoal and visible success condition.'})
        if not result['passed']:raise AssertionError('Could not obtain a valid autonomous goal')
    except Exception:result['error']=traceback.format_exc()
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('fixture','passed','error') if k in result},ensure_ascii=False),flush=True)
    raise SystemExit(0 if result['passed'] else 1)
if __name__=='__main__':main()
