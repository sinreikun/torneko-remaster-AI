"""One checkpoint per profile. Restore static map history only after visual agreement."""
import copy,io,json,time

class MonitorCheckpoint:
    def __init__(self,db,profile):
        self.db=db;self.profile=profile;self.candidate=None;self.latest=None;self.status='none'
        db.execute('CREATE TABLE IF NOT EXISTS monitor_checkpoint(profile TEXT PRIMARY KEY,data TEXT NOT NULL)')
        row=db.execute('SELECT data FROM monitor_checkpoint WHERE profile=?',(profile,)).fetchone()
        if row:
            try:
                value=json.loads(row[0])
                valid=isinstance(value,dict) and value.get('version') in (1,2) and isinstance(value.get('cells'),list) and len(value['cells'])<=1728 and isinstance(value.get('view'),list) and len(value['view'])==144 and all(type(v) is int and 0<=v<=255 for v in value['view']) and isinstance(value.get('metrics'),dict) and isinstance(value.get('connections',[]),list) and len(value.get('connections',[]))<=6912 and isinstance(value.get('floor'),list) and isinstance(value.get('player'),list)
                if valid:self.candidate=value;self.status='awaiting_current_scene'
                else:self.status='invalid_checkpoint'
            except (ValueError,TypeError):self.status='invalid_checkpoint'
    @staticmethod
    def view(png):
        from PIL import Image,ImageStat
        try:
            with Image.open(io.BytesIO(png)) as image:
                im=image.convert('L').resize((160,90))
                # Tile means suppress character animation, retain the surrounding room.
                return [round(ImageStat.Stat(im.crop((x*10,y*10,(x+1)*10,(y+1)*10))).mean[0]) for y in range(9) for x in range(16)]
        except (ValueError,OSError):return None
    @staticmethod
    def identity(state):
        m=state.get('minimap',{});f=state.get('metrics',{}).get('floor',{})
        if not state.get('eligible') or m.get('status')!='observed' or m.get('retained') or f.get('retained') or not f.get('values') or m.get('grid_size')!=[54,32]:return None
        p=m.get('player_cell')
        if not p or len(p)!=2:return None
        cells=sorted([x+p[0],y+p[1]] for x,y in m.get('known_cells',[]))
        if len(cells)<25:return None
        return {'floor':f['values'],'player':p,'visible_cells':cells}
    def restore(self,state,png,sensor,navigation,world):
        saved=self.candidate;identity=self.identity(state)
        if not saved or not identity:return False
        self.candidate=None;view=self.view(png)
        metric=state.get('metrics',{}).get('level',{})
        same=all(saved.get(k)==v for k,v in identity.items()) and view is not None and sum(abs(x-y) for x,y in zip(view,saved['view']))/144<=6 and sum(abs(x-y)>20 for x,y in zip(view,saved['view']))<=8
        if metric.get('values') and saved.get('level') and metric['values']!=saved['level']:same=False
        if not same:self.status='rejected_different_scene';return False
        def cells(values):return {tuple(p) for p in values if isinstance(p,list) and len(p)==2 and all(type(v) is int for v in p) and 0<=p[0]<54 and 0<=p[1]<32}
        sensor.cells.update(cells(saved['cells']));sensor.floor=identity['floor'][0]
        m=state['minimap'];m['accumulated_cells']=[list(p) for p in sorted(sensor.cells)]
        navigation.observe(state)
        for record in saved.get('connections',[]):
            if not isinstance(record,list) or len(record)!=3 or type(record[2]) is not int or not 1<=record[2]<=1000000:continue
            ends=cells(record[:2])
            if len(ends)!=2 or not ends<=sensor.cells:continue
            a,b=sorted(ends)
            if max(abs(a[i]-b[i]) for i in (0,1))!=1:continue
            edge=navigation.edge_pair(a,b);navigation.crossings[edge]=max(navigation.crossings[edge],record[2])
        # Retained numbers are references, never current HP or live enemies.
        for k,v in saved.get('metrics',{}).items():
            if k not in ('strength','sword','shield','experience','max_floor','level','gold'):continue
            retained={**copy.deepcopy(v),'retained':True,'status':'checkpoint_past_observation_not_current_confirmation'}
            world.last_known_metrics.setdefault(k,retained)
            state.setdefault('metrics',{}).setdefault(k,copy.deepcopy(retained))
        self.status='restored_static_map';m['checkpoint_restore']={'status':self.status,'saved_at':saved.get('saved_at'),'rule':'床と入力に対応した通過接続のみ再利用。敵・アイテム位置・目的地・現在値は新しい観測から取得。'}
        return True
    def preview(self,metrics,png):
        """Read-only connection preview; never authorize navigation from this."""
        saved=self.candidate;view=self.view(png)
        floor=metrics.get('floor',{})
        if not saved or floor.get('retained') or floor.get('values')!=saved['floor'] or view is None:return None
        if sum(abs(x-y) for x,y in zip(view,saved['view']))/144>6 or sum(abs(x-y)>20 for x,y in zip(view,saved['view']))>8:return None
        retained={k:{**copy.deepcopy(v),'retained':True} for k,v in saved.get('metrics',{}).items()}
        return {'event':'monitor_checkpoint_preview','policy_state':{'eligible':False,'metrics':{**retained,**copy.deepcopy(metrics)},
          'minimap':{'status':'observed','retained':True,'checkpoint_preview':True,'grid_size':[54,32],'player_cell':saved['player'],'accumulated_cells':saved['cells'],'known_cells':[]},
          'floor_exploration':{'status':'observed_floor','known_count':len(saved['cells'])}}}
    def remember(self,state,png,sensor,navigation,world):
        identity=self.identity(state)
        if not identity:
            # A menu can hide an unchanged map; a changed floor cannot inherit it.
            floor=state.get('metrics',{}).get('floor',{}).get('values')
            if self.latest and floor and floor!=self.latest['floor']:self.latest=None
            return
        view=self.view(png)
        if view is None:return
        self.latest={'version':2,**copy.deepcopy(identity),'cells':[list(p) for p in sorted(sensor.cells)],'connections':[[list(a),list(b),n] for (a,b),n in navigation.crossings.items() if n>0][:6912],
          'metrics':copy.deepcopy(world.last_known_metrics),'level':state.get('metrics',{}).get('level',{}).get('values'),'view':view,'saved_at':time.time()}
    def save(self):
        if self.latest:
            self.db.execute('INSERT OR REPLACE INTO monitor_checkpoint(profile,data) VALUES(?,?)',(self.profile,json.dumps(self.latest,ensure_ascii=False)))
        elif self.status!='awaiting_current_scene':self.db.execute('DELETE FROM monitor_checkpoint WHERE profile=?',(self.profile,))
        self.db.commit()
