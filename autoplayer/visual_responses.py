"""Session-local image-context response counts; no object or progress labels."""
import io
import uuid

class VisualResponses:
    def __init__(self,capacity=64,cooldown=12):
        self.scope=uuid.uuid4().hex;self.capacity=capacity;self.cooldown=cooldown;self.anchors={};self.actions={};self.sequence=0;self.streak=0
    @staticmethod
    def grid(png):
        if not png or not png.startswith(b'\x89PNG\r\n\x1a\n'):return None
        from PIL import Image
        try:
            with Image.open(io.BytesIO(png)) as image:return [v//32 for v in image.convert('L').resize((16,12)).tobytes()]
        except (OSError,ValueError):return None
    @staticmethod
    def activity(a,b):
        if not a or not b or len(a)!=192 or len(b)!=192:return None
        return sum(abs(x-y)>1 for x,y in zip(a,b))/192
    def context(self,grid):
        if not grid or len(grid)!=192:return None
        nearest=min(self.anchors,key=lambda k:self.activity(self.anchors[k],grid),default=None)
        if nearest is not None and self.activity(self.anchors[nearest],grid)<=.02:return nearest
        self.sequence+=1;identifier=self.sequence;self.anchors[identifier]=list(grid)
        if len(self.anchors)>self.capacity:
            old=next(iter(self.anchors));del self.anchors[old];self.actions.pop(old,None)
        return identifier
    def evidence(self,grid,step):
        context=self.context(grid);records=[];blocked=[]
        for keys,data in self.actions.get(context,{}).items():
            if step-data['last_no_change']>=self.cooldown:data['no_change']=0
            record={'keys':list(keys),**data};records.append(record)
            if data['no_change']>=3:blocked.append(list(keys))
        return {'scope':self.scope,'context_id':context,'records':records[-24:],'blocked_actions':blocked,
            'rule':'粗い画像パターンの仮グループ。別scopeのIDを同じ場所と扱わない。同じパターンで3回変化を確認できない入力は12手だけ優先度を下げる。移動失敗・壁・入力故障の確定ではない。画素変化を攻略成功としない。'}
    def record(self,before,after,neutral,row,step):
        if not row.get('executed') or row.get('dry_run') or row.get('action_kind')!='input' or row.get('turn_kind')=='aborted' or str(row.get('event','')).startswith('diagnostic') or row.get('kind') in ('compatibility','separation') or (row.get('input_receipt') or {}).get('os_submission')!='accepted':return {'status':'excluded'}
        fraction=self.activity(before,after);natural=self.activity(neutral,before)
        if fraction is None or natural is None:return {'status':'missing_image_pair'}
        origin=self.context(before);destination=self.context(after)
        changed=origin!=destination and fraction>=max(.04,natural+.02)
        keys=tuple(row.get('observed_keys',[]))
        if not keys:return {'status':'excluded'}
        stats=self.actions.setdefault(origin,{}) if origin in self.anchors else {}
        if keys not in stats and len(stats)>=32:stats.pop(next(iter(stats)))
        data=stats.setdefault(keys,{'attempts':0,'context_changes':0,'no_change':0,'last_no_change':step})
        if step-data['last_no_change']>=self.cooldown:data['no_change']=0
        data['attempts']+=1;data['context_changes']+=int(changed)
        if changed:data['no_change']=0;self.streak=0
        else:data['no_change']+=1;data['last_no_change']=step;self.streak+=1
        return {'status':'observed','scope':self.scope,'context_before':origin,'context_after':destination,'context_change':changed,'coarse_activity':fraction,'neutral_activity':natural,
            'keys':list(keys),'no_change_attempts':data['no_change'],'stagnant_turns':self.streak,'attribution':'image_context_association_not_verified_progress'}
