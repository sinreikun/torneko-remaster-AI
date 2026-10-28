"""Recover only a selected application; never replay the interrupted input."""
import subprocess
import ntpath
import time
from pathlib import Path


class TargetUnavailable(RuntimeError):
    pass


class ObservationRetry(RuntimeError):
    pass


class TargetRecovery:
    def __init__(self, target, emit, windows=None, alive=None, launch=None, clock=time.monotonic, processes=None, executable_for_pid=None):
        from .windows import window_targets, process_alive,process_parents,process_executable
        self.target = dict(target)
        self.emit = emit
        self.windows = windows or window_targets
        self.alive = alive or process_alive
        self.launch = launch or self._launch
        self.clock = clock
        self.next_launch = 0
        self.attempts = 0
        self.child = None
        self.processes=processes or process_parents;self.executable_for_pid=executable_for_pid or process_executable
        self.roots=set();self.lineage=set();self.preexisting=set();self.launched=False;self.stable=None;self.stable_scans=0;self.last_status=None
        self.entry=self.target.get('launch_executable') or self._entry_from_parent() or self.target.get('executable','')
        self.target['launch_executable']=self.entry

    @staticmethod
    def _launch(executable):
        # No shell, model-generated arguments, guessed launcher, or elevation.
        import os
        options={}
        if os.name=='nt':
            startup=subprocess.STARTUPINFO()
            startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow=4 # SW_SHOWNOACTIVATE; no foreground request
            options['startupinfo']=startup
        return subprocess.Popen([executable], cwd=str(Path(executable).parent), shell=False,**options)

    @staticmethod
    def _path(path):return ntpath.normcase(ntpath.normpath(path)) if path else ''

    def _entry_from_parent(self):
        parents={p['pid']:p.get('parent_pid',0) for p in self.processes()}
        pid=self.target.get('pid',0);body=self._path(self.target.get('executable',''));seen=set();entry=''
        shared={'steam.exe','explorer.exe','cmd.exe','powershell.exe','python.exe','pythonw.exe','java.exe','dotnet.exe','epicgameslauncher.exe','goggalaxy.exe'}
        for _ in range(6):
            pid=parents.get(pid,0)
            if not pid or pid in seen:break
            seen.add(pid);candidate=self.executable_for_pid(pid);path=self._path(candidate)
            if not path or ntpath.basename(path) in shared:break
            directory=ntpath.dirname(path)
            if path!=body and body.startswith(directory+'\\') and directory!=ntpath.splitdrive(directory)[0]+'\\' and Path(candidate).is_file():entry=candidate
            else:break
        return entry

    def _descendants(self):
        rows=self.processes();owned=set(self.roots)|set(self.lineage)
        for _ in range(16):
            added={p['pid'] for p in rows if p.get('parent_pid') in owned}-owned
            if not added:break
            owned.update(added)
        self.lineage=set(sorted(owned)[-256:]);return owned

    def _status(self,status,**extra):
        signature=(status,extra.get('reason'))
        if signature!=self.last_status:
            self.emit({'event':'target_recovery','status':status,'learning':False,**extra});self.last_status=signature

    def connected(self,target):
        self.target={**target,'launch_executable':self.entry};self.attempts=0;self.next_launch=0
        self.roots.clear();self.lineage.clear();self.launched=False;self.child=None;self.stable=None;self.stable_scans=0
        self._status('reconnected',target=dict(self.target))
        return dict(self.target)

    def recover(self, control):
        self._status('waiting',reason='接続を失いました。保留中の判断を破棄し対象を再検索します。')
        while control.checkpoint():
            request=getattr(control,'reconnect_requested',None)
            if request is not None:request.clear()
            windows=self.windows();executable=self.target.get('executable','')
            matches=[w for w in windows if executable and self._path(w.get('executable',''))==self._path(executable) and w.get('class_name')==self.target.get('class_name')]
            owned=[w for w in matches if w.get('pid')==self.target.get('pid')]
            if len(owned)==1 and control.checkpoint():return self.connected(owned[0])
            descendants=self._descendants() if self.launched else set()
            root=ntpath.dirname(self._path(self.entry))
            related=[w for w in windows if w.get('pid') in descendants and
                     (self._path(w.get('executable',''))==self._path(executable) or
                      w.get('class_name')==self.target.get('class_name') and root and self._path(w.get('executable','')).startswith(root+'\\'))]
            # A launcher can exit, or delegate through a platform process. Only
            # newly appeared matching game windows from this launch are eligible.
            fresh=[w for w in matches if self.launched and (w.get('pid'),w.get('hwnd')) not in self.preexisting]
            candidates=related or fresh
            if len(candidates)>1:
                same_title=[w for w in candidates if w.get('title','').strip().casefold()==self.target.get('title','').strip().casefold()]
                if len(same_title)==1:candidates=same_title
            if len(candidates)==1:
                candidate=candidates[0];identity=(candidate['pid'],candidate['hwnd'])
                self.stable_scans=self.stable_scans+1 if identity==self.stable else 1;self.stable=identity
                if (self.stable_scans>=2 or self.child is not None and candidate['pid']==self.child.pid) and control.checkpoint():return self.connected(candidate)
                self._status('window_stabilizing',target=candidate)
            elif candidates:self._status('ambiguous',reason='起動した対象の候補が複数あるため、入力せずウィンドウの確定を待っています。')
            else:self.stable=None;self.stable_scans=0
            pid=self.target.get('pid',0);running=self.alive(pid) if pid else False
            child_running=self.child is not None and self.child.poll() is None
            lineage_running=any(self.alive(p) for p in descendants if p!=pid)
            entry=self.entry
            if (not matches and not running and not child_running and not lineage_running and entry
                    and Path(entry).is_file() and Path(entry).suffix.lower()=='.exe' and self.clock()>=self.next_launch):
                self.attempts+=1;self.next_launch=self.clock()+min(60,5*2**min(self.attempts-1,4))
                if not control.checkpoint():return None
                if not self.alive(pid):
                    try:
                        self.preexisting={(w.get('pid'),w.get('hwnd')) for w in windows}
                        self.child=self.launch(entry);self.roots.add(self.child.pid);self.launched=True
                        self._status('launched',pid=self.child.pid,attempt=self.attempts,launch_executable=entry,game_executable=executable)
                    except OSError as error:self._status('launch_failed',reason=str(error),attempt=self.attempts)
            elif self.launched and not candidates:self._status('waiting_for_game_window',reason='起動プロセスとゲーム本体を追跡中です。ゲームウィンドウの出現を待っています。')
            control.wait(2)
        return None
