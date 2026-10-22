"""Real Codex protocol + local deterministic Responses stream; no paid model calls."""
import json
import os
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import tempfile
import threading
import time
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ailink.codex_rpc import CodexRPC
from ailink.quota import QuotaGuard
from ailink.service import Application
from ailink.desktop_bridge import DesktopBridge


def run(output=None):
    end=threading.Event();requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            requests.append(self.path);number=len(requests)
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            def emit(kind,**params):
                self.wfile.write(('data: '+json.dumps({'type':kind,**params})+'\n\n').encode());self.wfile.flush()
            response={'id':f'resp_{number}','object':'response','created_at':int(time.time()),'model':'gpt-5.2','status':'in_progress','output':[]}
            try:
                emit('response.created',response=response)
                if number>1:
                    while not end.wait(.2):self.wfile.write(b': keepalive\n\n');self.wfile.flush()
                    return
                item={'id':'msg_1','type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':'Local integration ready.','annotations':[]}]}
                emit('response.output_item.added',output_index=0,item={**item,'status':'in_progress','content':[]})
                emit('response.content_part.added',item_id='msg_1',output_index=0,content_index=0,part={'type':'output_text','text':'','annotations':[]})
                emit('response.output_text.delta',item_id='msg_1',output_index=0,content_index=0,delta='Local integration ready.')
                emit('response.output_item.done',output_index=0,item=item)
                emit('response.completed',response={**response,'status':'completed','output':[item],'usage':{'input_tokens':11,'input_tokens_details':{'cached_tokens':0},'output_tokens':9,'output_tokens_details':{'reasoning_tokens':0},'total_tokens':20}})
            except (BrokenPipeError,ConnectionResetError,OSError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    overrides=['-c','model_provider="ailink_integration"','-c','model="gpt-5.2"',
               '-c','model_providers.ailink_integration.name="AI Link local test"',
               '-c',f'model_providers.ailink_integration.base_url="http://127.0.0.1:{server.server_port}/v1"',
               '-c','model_providers.ailink_integration.wire_api="responses"',
               '-c','model_providers.ailink_integration.requires_openai_auth=false',
               '-c','model_providers.ailink_integration.supports_websockets=false']
    from ailink.storage_cleanup import StorageCleanup
    import queue
    original=subprocess.Popen;checks=[];owner=None
    def check(name,condition):
        if not condition:raise AssertionError(name)
        checks.append({'name':name,'passed':True});print('PASS '+name,flush=True)
    temp_root=Path(output).resolve().parent if output else Path.cwd()/'.artifacts/integration';temp_root.mkdir(parents=True,exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='retention-real-',dir=temp_root,ignore_cleanup_errors=True) as directory:
            base=Path(directory);home=base/'codex-home';home.mkdir();workspace=base/'project';workspace.mkdir()
            sentinel=workspace/'important.md';sentinel.write_text('Keep this output',encoding='utf-8')
            class LocalCodex(CodexRPC):
                def connect(self):
                    env={**os.environ,'CODEX_HOME':str(home),'NO_PROXY':'127.0.0.1,localhost'}
                    with patch('ailink.codex_rpc.subprocess.Popen',side_effect=lambda args,**kw:original([*args,*overrides],**{**kw,'env':env})):super().connect()
            owner=LocalCodex();owner.connect();thread=owner.call('thread/start',{'cwd':str(workspace),'sandbox':'read-only','approvalPolicy':'untrusted'})['thread']['id']
            owner.call('turn/start',{'threadId':thread,'input':[{'type':'text','text':'Reply with a short confirmation. Do not run tools.'}]})
            deadline=time.monotonic()+20;completed=False
            while time.monotonic()<deadline:
                try:event=owner.control_events.get(timeout=.2)
                except queue.Empty:continue
                if event.get('turn',{}).get('status')=='completed':completed=True;break
            check('real completed test conversation persisted',completed)
            owner.close();owner=None
            owner=LocalCodex();owner.connect();active=owner.call('thread/start',{'cwd':str(workspace),'sandbox':'read-only','approvalPolicy':'untrusted'})['thread']['id']
            owner.call('turn/start',{'threadId':active,'input':[{'type':'text','text':'Wait for the local test stream; do not use tools.'}]})
            deadline=time.monotonic()+15;running=False
            while time.monotonic()<deadline:
                try:event=owner.control_events.get(timeout=.2)
                except queue.Empty:continue
                if event.get('turn',{}).get('status')=='inProgress':running=True;break
            check('another real client already has a running conversation',running)
            quota=Mock();quota.data={'threads':{}};quota.lock=threading.RLock()
            storage=StorageCleanup(base/'state',quota,LocalCodex,home=home)
            future=time.time()+2*86400
            with patch('ailink.storage_cleanup.time.time',return_value=future):
                storage.scan('local');check('official local scan finds original ID',thread in storage.catalog)
                check('scan retains final response classification',storage.catalog[thread]['classification']['final']==1)
                policy={'source':'local','workspace':str(workspace),'olderDays':0,'from':None,'to':None,'includeLogs':False,'keep':{'files':True,'final':True,'decisions':True,'code':False}}
                blocked=storage.preview([active],policy);result=storage.execute(blocked['token']);blocked_entry=list(storage.batches.values())[-1]['items'][0]
                check('cleanup refuses another active writer without archival',blocked_entry['state']=='failed' and 'active writer' in blocked_entry['error'].lower())
                check('original client conversation remains running',owner.call('thread/read',{'threadId':active})['thread']['status']['type']=='active')
                plan=storage.preview([thread],policy);check('preview does not archive',Path(storage.catalog[thread]['meta']['path']).exists())
                result=storage.execute(plan['token']);batch=list(storage.batches.values())[-1];entry=batch['items'][0]
                if entry['state']!='archived':raise AssertionError('archive failed: '+json.dumps(entry,ensure_ascii=False))
                check('official thread/archive confirms success',entry['state']=='archived')
                backup=storage.root/'batches'/batch['id']/entry['folder']/'original'
                check('verified complete original retained in recovery area',backup.exists())
                check('final response retained independently',(backup.parent/'preserved/final.md').exists())
                storage.restore(batch['id'],thread);check('official thread/unarchive restores original ID',entry['state']=='restored')
                storage.scan('local');plan=storage.preview([thread],policy);storage.execute(plan['token']);batch=list(storage.batches.values())[-1];entry=batch['items'][0]
                check('restored conversation can be archived again',entry['state']=='archived')
                purge=storage.purge_preview(batch['id']);result=storage.purge(purge['token'])
                check('official thread/delete removes archived conversation',result['completed']==1 and entry['state']=='purged')
                backup=storage.root/'batches'/batch['id']/entry['folder']/'original'
                check('purge removes recovery payload but retains final output',not backup.exists() and (backup.parent/'preserved/final.md').exists())
                check('workspace output untouched',sentinel.read_text()=='Keep this output')
                storage.scan('local');check('deleted ID no longer listed',thread not in storage.catalog)
            check('generation used only local test model',len(requests)==2 and all(p.startswith('/v1/') for p in requests))
    finally:
        if owner:owner.close()
        end.set();server.shutdown();server.server_close()
    report={'passed':len(checks),'checks':checks,'real_codex_archive_unarchive_delete':True,'local_model_requests':len(requests),'external_model_requests':0,'user_data_modified':False,'age_simulated':True}
    if output:Path(output).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)

if __name__=='__main__':run(sys.argv[1] if len(sys.argv)>1 else None)
