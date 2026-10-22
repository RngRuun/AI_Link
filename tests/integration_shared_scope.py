"""Real Codex protocol + local deterministic Responses stream; no paid model calls."""
import json
import os,queue,socket,secrets
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import tempfile
import threading
import time
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ailink.codex_rpc import CodexRPC,find_codex
from ailink.quota import QuotaGuard


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
    temp_root=Path(output).resolve().parent if output else Path.cwd()/'.artifacts/integration'
    temp_root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='shared-scope-',dir=temp_root,ignore_cleanup_errors=True) as directory:
        home=Path(directory).resolve()/'codex-home';home.mkdir();token=Path(directory).resolve()/'shared.token';token.write_text(secrets.token_urlsafe(32))
        port_socket=socket.socket();port_socket.bind(('127.0.0.1',0));port=port_socket.getsockname()[1];port_socket.close();endpoint=f'ws://127.0.0.1:{port}'
        child=subprocess.Popen([find_codex(),'app-server','--listen',endpoint,'--ws-auth','capability-token','--ws-token-file',str(token),*overrides],env={**os.environ,'CODEX_HOME':str(home)},stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        a=b=None
        def connect():
            deadline=time.monotonic()+12
            while True:
                rpc=CodexRPC(shared=True,endpoint=endpoint,token_file=str(token))
                try:rpc.connect();return rpc
                except Exception:
                    rpc.close()
                    if time.monotonic()>deadline:raise
                    time.sleep(.2)
        def observed(rpc,target,seconds):
            status=None;deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                try:event=rpc.control_events.get(timeout=.1)
                except queue.Empty:continue
                if event.get('turn',{}).get('id')==target:status=event['turn']['status']
            return status
        try:
            a=connect();thread=a.call('thread/start',{'cwd':str(Path(directory).resolve()),'sandbox':'read-only','approvalPolicy':'untrusted'})['thread']['id']
            first=a.call('turn/start',{'threadId':thread,'input':[{'type':'text','text':'Local complete.'}]})['turn']['id'];assert observed(a,first,1.5)=='completed'
            active=a.call('turn/start',{'threadId':thread,'input':[{'type':'text','text':'Local wait.'}]})['turn']['id']
            deadline=time.monotonic()+10
            while len(requests)<2 and time.monotonic()<deadline:time.sleep(.1)
            b=connect();result={'loaded_existing':thread in b.call('thread/loaded/list',{'limit':100}).get('data',[])}
            resumed=b.call('thread/resume',{'threadId':thread})['thread'];result['resume_turn_count']=len(resumed.get('turns',[]));result['resume_last_status']=(resumed.get('turns') or [{}])[-1].get('status');result['resume_last_id_matches_active']=(resumed.get('turns') or [{}])[-1].get('id')==active
            b.call('turn/interrupt',{'threadId':thread,'turnId':active})
            result['shared_client_stopped_original_turn']=observed(a,active,1.5)=='interrupted';result['observer_received_confirmation']=observed(b,active,.3)=='interrupted';result['external_model_requests']=0
        finally:
            if a:a.close()
            if b:b.close()
            child.terminate();child.wait(timeout=5);end.set();server.shutdown();server.server_close()
    if output:Path(output).write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result
if __name__=='__main__':print(json.dumps(run(sys.argv[1] if len(sys.argv)>1 else None)),flush=True)
