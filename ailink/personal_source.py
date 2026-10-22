"""Two-minute local session observation; no conversation text is retained."""
from __future__ import annotations
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from .config import atomic_json
from .import_paths import collect_jsonl


class PersonalSource:
    def __init__(self, state_dir, context, credit, flush, root=None):
        self.path=Path(state_dir)/'personal-source.json'
        self.root=Path(root) if root else Path(os.environ.get('CODEX_HOME') or str(Path.home()/'.codex'))/'sessions'
        self.context,self.credit,self.flush=context,credit,flush
        self.enabled=True
        try:self.enabled=json.loads(self.path.read_text('utf-8')).get('enabled') is not False
        except (OSError,ValueError,AttributeError):pass
        self.lock=threading.Lock();self.scan_lock=threading.Lock();self.stop=threading.Event();self.wake=threading.Event()
        self.stamps={};self.worker=None
        self.state={'enabled':self.enabled,'root':str(self.root),'interval':120,'running':False,'updated':0,'attempted':0,'files':0,'changed':0,'tokens':0,'error':None,'next':0}

    def snapshot(self):
        with self.lock:return dict(self.state)

    def configure(self, enabled):
        if type(enabled) is not bool:raise ValueError('自动采集开关必须是布尔值。')
        with self.lock:self.enabled=enabled;self.state['enabled']=enabled
        atomic_json(self.path,{'enabled':enabled});self.wake.set()

    def start(self):
        if self.worker:return
        self.worker=threading.Thread(target=self._loop,daemon=True,name='personal-jsonl-observer');self.worker.start()

    def request(self):self.wake.set()

    def scan(self):
        if not self.enabled or not self.scan_lock.acquire(blocking=False):return
        with self.lock:self.state.update(running=True,attempted=time.time(),error=None)
        total=changed=0
        try:
            files=collect_jsonl([str(self.root)],min_mtime=time.time()-8*86400,allow_empty=True)['paths'] if self.root.exists() else []
            active_keys={str(p.resolve()) for p in files}
            self.stamps={key:value for key,value in self.stamps.items() if key in active_keys}
            context=self.context()
            for path in files:
                if self.stop.is_set():break
                info=path.stat();stamp=(info.st_size,info.st_mtime_ns);key=str(path.resolve())
                if self.stamps.get(key)==stamp:continue
                thread,previous=None,0
                with path.open(encoding='utf-8-sig') as stream:
                    for line in stream:
                        if self.stop.is_set():break
                        try:
                            record=json.loads(line);payload=record.get('payload') or {}
                            if record.get('type')=='session_meta':
                                thread=payload.get('id');previous=None if payload.get('forked_from_id') else 0
                            if not isinstance(thread,str) or record.get('type')!='event_msg' or payload.get('type')!='token_count':continue
                            count=payload['info']['total_token_usage']['total_tokens'];stamp_time=datetime.fromisoformat(record['timestamp'].replace('Z','+00:00'))
                            if type(count) is not int or not 0<=count<=10**15 or stamp_time.tzinfo is None:continue
                            if previous is not None and count<previous:continue
                            total+=self.credit(context,thread,count,previous,stamp_time.timestamp())
                            previous=count
                        except (ValueError,TypeError,KeyError,AttributeError,OverflowError):continue
                self.stamps[key]=stamp;changed+=1
            if self.stop.is_set():return
            self.flush()
            with self.lock:self.state.update(updated=time.time(),files=len(files),changed=changed,tokens=total,next=time.time()+120)
        except (OSError,ValueError) as error:
            try:self.flush()
            except OSError:pass
            with self.lock:self.state['error']=str(error)[:250]
        finally:
            with self.lock:self.state['running']=False
            self.scan_lock.release()

    def _loop(self):
        while not self.stop.is_set():
            self.scan();self.wake.wait(120);self.wake.clear()

    def shutdown(self):
        self.stop.set();self.wake.set()
        if self.worker:self.worker.join(timeout=2)
