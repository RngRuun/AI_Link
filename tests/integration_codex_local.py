"""Real Codex protocol + local deterministic Responses stream; no paid model calls."""
import json
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


def run():
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
    original=subprocess.Popen;rpc=CodexRPC();report={};thread_id=None
    try:
        with patch('ailink.codex_rpc.subprocess.Popen',side_effect=lambda args,**kw:original([*args,*overrides],**kw)):rpc.connect()
        temp_root = Path(sys.argv[1]).resolve().parent if len(sys.argv)>1 else Path.cwd()/'.artifacts/integration'
        temp_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='ai-link-local-integration-',dir=temp_root,ignore_cleanup_errors=True) as directory:
            guard=QuotaGuard(directory,Mock());guard.rpc=rpc;guard.plan='plus';guard.shared=False
            guard.data['account_fingerprint']='local-test-provider';guard.enabled=True;guard.updated=time.time()
            guard.windows=[{'key':'primary','remaining':100},{'key':'secondary','remaining':100}]
            thread_id=guard.start_chat('Reply with a short confirmation only. Do not run any tools.',directory)
            for _ in range(200):
                guard._drain_usage()
                if guard.runtime.get(thread_id,{}).get('status')=='completed':break
                time.sleep(.1)
            assert guard.personal.snapshot('me')['plans']['plus']['primary']['total']==20,'token event not observed'
            report['real_usage_event']=True
            second=rpc.call('turn/start',{'threadId':thread_id,'input':[{'type':'text','text':'Wait for the local test stream.'}]})['turn']
            guard.runtime[thread_id]=second;guard.data['caps']['plus']={'enabled':True,'primary':1,'secondary':1000}
            for _ in range(200):
                if len(requests)>=2:break
                time.sleep(.1)
            guard._evaluate()
            for _ in range(100):
                guard._drain_usage()
                if guard.runtime.get(thread_id,{}).get('status')=='interrupted':break
                time.sleep(.1)
            assert guard.runtime.get(thread_id,{}).get('status')=='interrupted','real interruption not confirmed'
            assert guard.data['threads'][thread_id]['stop_reason']=='personal_cap'
            guard._evaluate()
            assert guard.data['threads'][thread_id]['state']=='paused'
            for entry in guard.personal.entries:entry['at']-=18001  # Simulated passage of the personal five-hour window.
            guard.data['auto_resume']=True
            guard._evaluate()
            assert guard.data['threads'][thread_id]['state']=='watching'
            assert guard.runtime[thread_id]['id']!=second['id']
            resumed_id=guard.runtime[thread_id]['id']
            for _ in range(100):
                guard._drain_usage()
                if guard.runtime.get(thread_id,{}).get('id')==resumed_id and guard.runtime[thread_id].get('status')=='inProgress' and len(requests)>=3:break
                time.sleep(.1)
            rpc.call('turn/interrupt',{'threadId':thread_id,'turnId':resumed_id})
            report.update(real_personal_cap_interrupt=True,interrupted_confirmation=True,real_resume_rpc=True,personal_window_expiry_simulated=True,model_generation_requests_external=0,model_requests_local=len(requests))
            rpc.call('thread/archive',{'threadId':thread_id});thread_id=None
            rpc.close()
    finally:
        if thread_id:
            try:rpc.call('thread/archive',{'threadId':thread_id})
            except Exception:pass
        end.set();rpc.close();server.shutdown();server.server_close()
    return report

if __name__=='__main__':
    result=run();print(json.dumps(result))
    if len(sys.argv)>1:Path(sys.argv[1]).write_text(json.dumps(result,indent=2),encoding='utf-8')
