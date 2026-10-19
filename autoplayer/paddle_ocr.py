"""Optional ONNX OCR adapter; only reference-model hashes are accepted."""
import hashlib
import io
import threading
import time
from .ocr_components import MODELS,installed,directory

class PaddleOCR:
    def __init__(self,model_id):
        self.model=next(x for x in MODELS if x['id']==model_id)
        self.engine=None;self.lock=threading.Lock()
    def _load(self):
        if not installed(self.model): raise RuntimeError('セットアップで選択OCRモデルを導入してください。')
        from rapidocr import RapidOCR
        folder=directory(self.model);v5=self.model['id']=='ocr-paddle-v5'
        self.engine=RapidOCR(params={
            'Global.use_cls':False,'Global.max_side_len':1280,'Global.log_level':'error',
            'Det.model_path':str(folder/'det.onnx'),'Det.limit_side_len':1280,'Det.limit_type':'max',
            'Det.mean':[.485,.456,.406],'Det.std':[.229,.224,.225],
            'Rec.model_path':str(folder/('rec.onnx' if v5 else 'rec_japan.onnx')),
            'Rec.rec_keys_path':str(folder/('dict.txt' if v5 else 'japan_dict.txt')),
            'EngineConfig.onnxruntime.intra_op_num_threads':4,
            'EngineConfig.onnxruntime.inter_op_num_threads':1})
        if v5:
            # Restrict the CTC alphabet before argmax, as in the source's V5
            # engine. This is a language alphabet, never game-word correction.
            decoder=self.engine.text_rec.postprocess_op
            allowed=set((folder/'allowed_chars.txt').read_text(encoding='utf-8').splitlines())|{' ','blank'}
            forbidden=[i for i,char in enumerate(decoder.character) if char not in allowed]
            class AlphabetDecoder:
                def __call__(self,predictions,*args,**kwargs):
                    predictions=predictions.copy();predictions[...,forbidden]=0
                    return decoder(predictions,*args,**kwargs)
            self.engine.text_rec.postprocess_op=AlphabetDecoder()
    def read(self,png):
        record={'engine':self.model['id'],'status':'unavailable','frame_sha256':hashlib.sha256(png).hexdigest(),
                'captured_at':time.time(),'lines':[],'confidence':None}
        try:
            with self.lock:
                if self.engine is None: self._load()
                output=self.engine(png,use_cls=False,text_score=.5 if self.model['id']=='ocr-paddle-v5' else 0)
            lines=[]
            if output.txts is not None:
                for text,score,polygon in zip(output.txts,output.scores,output.boxes):
                    left=float(min(p[0] for p in polygon));top=float(min(p[1] for p in polygon))
                    width=float(max(p[0] for p in polygon))-left;height=float(max(p[1] for p in polygon))-top
                    lines.append({'text':str(text),'confidence':float(score),'words':[{'text':str(text),'box':[left,top,width,height]}]})
            record.update(status='ok',language='ja',lines=lines)
        except Exception as error: record['reason']=str(error)[:700]
        return record
