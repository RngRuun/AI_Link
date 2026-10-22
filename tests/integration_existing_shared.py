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
from ailink.quota import QuotaGuard,CONTINUE
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
    checks=[];actions=[];service=None
    def check(name,condition):
        if not condition:raise AssertionError(name)
        checks.append({'name':name,'passed':True});print('PASS '+name,flush=True)
    def wait_for(predicate,message):
        deadline=time.monotonic()+18
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.08)
        raise AssertionError(message)
    class NoSystemChanges:
        def restore(self):return {'restored':0,'preserved':0}
        def current(self):return {'owned':False,'enabled':False,'server':''}
    temp_root=Path(output).resolve().parent if output else Path.cwd()/'.artifacts/integration'
    temp_root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='shared-conversations-',dir=temp_root,ignore_cleanup_errors=True) as directory:
        home=Path(directory).resolve()/'codex-home';home.mkdir();token=Path(directory).resolve()/'shared.token';token.write_text(secrets.token_urlsafe(32))
        port_socket=socket.socket();port_socket.bind(('127.0.0.1',0));port=port_socket.getsockname()[1];port_socket.close();endpoint=f'ws://127.0.0.1:{port}'
        child=subprocess.Popen([find_codex(),'app-server','--listen',endpoint,'--ws-auth','capability-token','--ws-token-file',str(token),*overrides],env={**os.environ,'CODEX_HOME':str(home)},stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        owner=None;quota={'primary':100,'secondary':100}
        def connect_owner():
            deadline=time.monotonic()+12
            while True:
                rpc=CodexRPC(shared=True,endpoint=endpoint,token_file=str(token))
                try:rpc.connect();return rpc
                except Exception:
                    rpc.close()
                    if time.monotonic()>deadline:raise
                    time.sleep(.2)
        class SharedFixture(CodexRPC):
            def call(self,method,params=None,timeout=25):
                if method=='account/read':return {'account':{'type':'chatgpt','email':'local-fixture','planType':'plus'}}
                if method=='account/rateLimits/read':return {'rateLimitsByLimitId':{'codex':{'limitId':'codex','planType':'plus',**{k:{'usedPercent':100-quota[k],'windowDurationMins':300 if k=='primary' else 10080,'resetsAt':int(time.time())+3600} for k in ('primary','secondary')}}}}
                actions.append({'method':method,'params':params or {}})
                return super().call(method,params,timeout)
        try:
            owner=connect_owner()
            def create(text):
                identity=owner.call('thread/start',{'cwd':str(Path(directory).resolve()),'sandbox':'read-only','approvalPolicy':'untrusted'})['thread']['id']
                turn=owner.call('turn/start',{'threadId':identity,'input':[{'type':'text','text':text}]})['turn']['id']
                return identity,turn
            done,done_turn=create('Finish this local test response.')
            def completed():
                while True:
                    try:event=owner.control_events.get_nowait()
                    except queue.Empty:return False
                    if event.get('turn',{}).get('id')==done_turn and event['turn'].get('status')=='completed':return True
            wait_for(completed,'completed reference did not finish')
            a,a_turn=create('Existing task A is waiting on a local stream.');b,b_turn=create('Existing task B is waiting on a local stream.')
            wait_for(lambda:len(requests)==3,'existing model requests did not start')
            check('two tasks already run before AI Link attaches',len(requests)==3)
            service=Application(Path(__file__).resolve().parents[1],Path(directory)/'state',NoSystemChanges(),relay_port=0)
            source=service.quota.source;service.quota=QuotaGuard(Path(directory)/'state',service.telemetry,rpc_factory=SharedFixture);service.quota.source=source
            bridge=DesktopBridge(service);guard=service.quota
            def rpc(method,**params):return bridge.dispatch(method,params)
            def refresh(primary,secondary):
                with guard.lock:quota.update(primary=primary,secondary=secondary);rpc('quota.refresh')
            def config(auto=True):
                rpc('quota.configure',mode='plus',threshold={'primary':25,'secondary':7},autoResume=auto,bucket='codex');rpc('quota.refresh')
            def statuses():
                with guard.lock:guard._drain_usage();return {k:v.get('status') for k,v in guard.runtime.items()}
            def both_stopped():
                states=statuses()
                if states.get(a)!='interrupted' or states.get(b)!='interrupted':return False
                rpc('quota.refresh');return all(guard.data['threads'][k]['state'] in ('paused','paused_idle') for k in (a,b))
            def sent():return [x['params'] for x in actions if x['method']=='turn/start']
            rpc('quota.connect',shared=True,endpoint=endpoint,tokenFile=str(token))
            rpc('quota.threads.browse',search='',cursor=None)
            attached=rpc('quota.threads.attach',ids=[a,b,done])
            check('adopts three existing IDs without starting a conversation',set(attached['attached'])=={a,b,done} and not attached['failed'] and len(requests)==3 and not any(x['method']=='thread/start' for x in actions))
            config();rpc('quota.continuation',threadId=a,enabled=True,prompt='')
            custom='继续完成剩余的前端修改，并运行相关测试，避免重复操作。'
            rpc('quota.continuation',threadId=b,enabled=True,prompt=custom)
            rpc('quota.continuation',threadId=done,enabled=True,prompt='已完成对话不应收到这条消息')
            rpc('quota.arm',enabled=True);refresh(26,8)
            check('both targets remain running above both thresholds',all(guard.data['threads'][k]['state']=='watching' for k in (a,b)))
            refresh(25,100);wait_for(both_stopped,'shared guard did not stop both original tasks')
            interrupted={x['params']['threadId']:x['params']['turnId'] for x in actions if x['method']=='turn/interrupt'}
            check('guard interrupts both original client turns with real confirmations',interrupted.get(a)==a_turn and interrupted.get(b)==b_turn)
            refresh(100,99);check('partial refill sends no automatic message',not sent())
            refresh(100,100);wait_for(lambda:len(requests)>=5 and statuses().get(a)=='inProgress' and statuses().get(b)=='inProgress','multiple continuations did not start')
            check('full quota resumes multiple existing conversation IDs',{p['threadId'] for p in sent()}=={a,b})
            prompts={p['threadId']:p['input'][0]['text'] for p in sent()}
            check('blank uses unfinished-task default and custom text stays exact',prompts[a]==CONTINUE and prompts[b]==custom)
            check('completed conversation receives no automatic message',all(p['threadId']!=done for p in sent()))
            rpc('quota.refresh');rpc('quota.refresh');check('repeated evaluation does not duplicate messages',len(sent())==2)
            rpc('quota.arm',enabled=False);rpc('quota.continuation',threadId=a,enabled=False,prompt='');rpc('quota.arm',enabled=True)
            refresh(100,7);wait_for(both_stopped,'long window did not stop both resumed targets')
            count=len(sent());refresh(100,100);wait_for(lambda:len(requests)>=6,'opted-in target did not continue')
            check('per-conversation opt-out preserves the other target',len(sent())==count+1 and sent()[-1]['threadId']==b and guard.data['threads'][a]['state'] in ('paused','paused_idle'))
            rpc('quota.arm',enabled=False);config(auto=False);rpc('quota.arm',enabled=True)
            refresh(25,100);wait_for(both_stopped,'global-off target did not stop')
            count=len(sent());refresh(100,100);check('master switch off prevents all continuation',len(sent())==count)
            unauthorized=CodexRPC(shared=True,endpoint=endpoint)
            try:
                try:unauthorized.connect()
                except RuntimeError:denied=True
                else:denied=False
            finally:unauthorized.close()
            check('shared service requires its capability credential',denied)
            service.shutdown();service=None
            check('disconnecting AI Link leaves the original shared server available',child.poll() is None and done in owner.call('thread/loaded/list',{'limit':100})['data'])
        finally:
            if service:service.shutdown()
            if owner:owner.close()
            child.terminate();child.wait(timeout=5);end.set();server.shutdown();server.server_close()
    result={'passed':len(checks),'checks':checks,'model_requests_local':len(requests),'external_model_requests':0,'real_shared_server_and_two_clients':True,'quota_and_model_responses_simulated':True,'native_independent_desktop_controlled':False,'actions':actions}
    if output:Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
if __name__=='__main__':
    result=run(sys.argv[1] if len(sys.argv)>1 else None);print(json.dumps({k:v for k,v in result.items() if k not in ('checks','actions')}),flush=True)
