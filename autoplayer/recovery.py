"""Recover only a selected application; never replay the interrupted input."""
import subprocess
import time
from pathlib import Path


class TargetUnavailable(RuntimeError):
    pass


class ObservationRetry(RuntimeError):
    pass


class TargetRecovery:
    def __init__(self, target, emit, windows=None, alive=None, launch=None, clock=time.monotonic):
        from .windows import window_targets, process_alive
        self.target = dict(target)
        self.emit = emit
        self.windows = windows or window_targets
        self.alive = alive or process_alive
        self.launch = launch or self._launch
        self.clock = clock
        self.next_launch = 0
        self.attempts = 0
        self.child = None

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

    def recover(self, control):
        self.emit({'event':'target_recovery','status':'waiting','learning':False,
                   'reason':'接続を失いました。保留中の判断を破棄し対象を再検索します。'})
        while control.checkpoint():
            executable = self.target.get('executable', '')
            matches = [w for w in self.windows() if executable and w.get('executable','').casefold()==executable.casefold()
                       and w.get('class_name')==self.target.get('class_name')]
            owned=[w for w in matches if w.get('pid')==self.target.get('pid')]
            if len(owned)==1:
                self.target = owned[0]
                self.emit({'event':'target_recovery','status':'reconnected','target':self.target,'learning':False})
                return dict(self.target)
            pid = self.target.get('pid', 0)
            running = self.alive(pid) if pid else False
            child_running = self.child is not None and self.child.poll() is None
            if (not matches and not running and not child_running and executable
                    and Path(executable).is_file() and Path(executable).suffix.lower()=='.exe'
                    and self.clock()>=self.next_launch):
                # Check the process list again immediately before launching.
                self.attempts += 1
                self.next_launch = self.clock()+min(60,5*2**min(self.attempts-1,4))
                if not control.checkpoint():
                    return None
                if not self.alive(pid):
                    try:
                        self.child = self.launch(executable)
                        self.target['pid'] = self.child.pid
                        self.emit({'event':'target_recovery','status':'launched','pid':self.child.pid,
                                   'attempt':self.attempts,'learning':False})
                    except OSError as error:
                        self.emit({'event':'target_recovery','status':'launch_failed','reason':str(error),
                                   'attempt':self.attempts,'learning':False})
            # Pausing suspends the loop; stopping cancels it even during backoff.
            control.wait(2)
        return None
