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
    original=subprocess.Popen;checks=[];actions=[];service=None
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
    try:
        with tempfile.TemporaryDirectory(prefix='codex-lifecycle-',dir=temp_root,ignore_cleanup_errors=True) as directory:
            workspace=Path(directory)/'workspace';workspace.mkdir()
            test_home=Path(directory)/'codex-home';test_home.mkdir()
            quota={'primary':100,'secondary':100,'plan':'plus'}
            class LocalCodex(CodexRPC):
                def connect(self):
                    env={**os.environ,'CODEX_HOME':str(test_home),'NO_PROXY':'127.0.0.1,localhost'}
                    with patch('ailink.codex_rpc.subprocess.Popen',side_effect=lambda args,**kw:original([*args,*overrides],**{**kw,'env':env})):
                        super().connect()
                def call(self,method,params=None,timeout=25):
                    if method=='account/read':return {'account':{'type':'chatgpt','email':'local-fixture','planType':quota['plan']}}
                    if method=='account/rateLimits/read':
                        return {'rateLimitsByLimitId':{'codex':{'limitId':'codex','planType':quota['plan'],**{key:{'usedPercent':100-quota[key],'windowDurationMins':300 if key=='primary' else 10080,'resetsAt':int(time.time())+3600} for key in ('primary','secondary')}}}}
                    actions.append({'method':method,'threadId':(params or {}).get('threadId'),'turnId':(params or {}).get('turnId')})
                    return super().call(method,params,timeout)
            service=Application(Path(__file__).resolve().parents[1],Path(directory)/'state',NoSystemChanges(),relay_port=0)
            source=service.quota.source
            service.quota=QuotaGuard(Path(directory)/'state',service.telemetry,rpc_factory=LocalCodex)
            service.quota.source=source
            bridge=DesktopBridge(service);guard=service.quota
            def rpc(method,**params):return bridge.dispatch(method,params)
            def current(thread):
                with guard.lock:
                    guard._drain_usage();return dict(guard.runtime.get(thread,{}))
            def refresh(primary,secondary):
                with guard.lock:
                    quota.update(primary=primary,secondary=secondary);rpc('quota.refresh')
            def config(target,auto=True):
                rpc('quota.configure',mode='plus',threshold={'primary':25,'secondary':7},autoResume=auto,bucket='codex',resumeTarget=target,caps={'enabled':False,'primary':100000,'secondary':1000000})
                rpc('quota.refresh')
            def stopped(thread):
                if current(thread).get('status')!='interrupted':return False
                rpc('quota.refresh');return guard.data['threads'][thread]['state']=='paused'
            starts=lambda:len([a for a in actions if a['method']=='turn/start'])
            rpc('quota.connect',shared=False)
            first=rpc('quota.prepare',cwd=str(workspace))['threadId']
            check('prepare real selected thread without generation',len(requests)==0 and guard.data['threads'][first]['prepared'])
            config(first);rpc('quota.arm',enabled=True)
            rpc('quota.chat',prompt='Reply with a short confirmation. Do not run tools.',cwd=str(workspace),threadId=first)
            wait_for(lambda:current(first).get('status')=='completed','initial real turn did not complete')
            check('initial task completes in exact prepared thread',not guard.data['threads'][first]['prepared'] and any(a['method']=='turn/start' and a['threadId']==first for a in actions))
            check('real token event enters personal ledger',guard.personal.snapshot('me')['plans']['plus']['primary']['total']==20)
            rpc('quota.arm',enabled=False)
            target=rpc('quota.prepare',cwd=str(workspace))['threadId'];config(target)
            rpc('quota.arm',enabled=True)
            rpc('quota.chat',prompt='Wait for the local test stream. Do not run tools.',cwd=str(workspace),threadId=target)
            wait_for(lambda:current(target).get('status')=='inProgress' and len(requests)>=2,'protected turn did not start')
            refresh(26,8)
            check('one percent above each limit keeps turn running',guard.data['threads'][target]['state']=='watching')
            refresh(100,7);wait_for(lambda:stopped(target),'long-term limit did not interrupt real turn')
            check('long-term boundary receives real interrupted confirmation',guard.data['threads'][target]['trigger_windows']==['secondary'])
            count=starts();refresh(100,99)
            check('one non-full window prevents continuation',starts()==count)
            refresh(100,100)
            wait_for(lambda:current(target).get('status')=='inProgress' and len(requests)>=3,'designated continuation did not start')
            check('full quota continues only designated conversation',starts()==count+1 and [a for a in actions if a['method']=='turn/start'][-1]['threadId']==target)
            rpc('quota.refresh');rpc('quota.refresh')
            check('repeated refresh does not duplicate continuation',starts()==count+1)
            refresh(25,100);wait_for(lambda:stopped(target),'short-term limit did not interrupt real turn')
            check('short-term boundary independently interrupts resumed turn',guard.data['threads'][target]['trigger_windows']==['primary'])
            refresh(100,100)
            wait_for(lambda:current(target).get('status')=='inProgress' and len(requests)>=4,'second continuation did not start')
            rpc('quota.arm',enabled=False);config(target,auto=False);rpc('quota.arm',enabled=True)
            refresh(100,7);wait_for(lambda:stopped(target),'auto-off real turn did not stop')
            count=starts();refresh(100,100);rpc('quota.refresh')
            check('auto-resume off prevents continuation when full',starts()==count)
            rpc('quota.arm',enabled=False);new=rpc('quota.prepare',cwd=str(workspace))['threadId'];config(new,auto=False)
            rpc('quota.arm',enabled=True)
            for primary,secondary,label in [(25,100,'short'),(100,7,'long')]:
                refresh(primary,secondary);count=starts()
                try:rpc('quota.chat',prompt='Must not be sent.',cwd=str(workspace),threadId=new)
                except RuntimeError:pass
                else:raise AssertionError(label+' limit accepted a new task')
                check(label+' limit blocks generation before RPC',starts()==count and guard.data['threads'][new]['prepared'])
            rpc('quota.arm',enabled=False)
            check('guard switch off remains off',not guard.enabled)
            new=rpc('quota.prepare',cwd=str(workspace))['threadId'];config(new,auto=False)
            count=len(requests);rpc('quota.disconnect');quota.update(primary=100,secondary=100);rpc('quota.connect',shared=False)
            restored=guard.data['resume_target']
            check('reconnect restores unused prepared target without generation',restored!=new and restored in {c['id'] for c in guard.choices} and new not in guard.data['threads'] and guard.data['threads'][restored]['prepared'] and len(requests)==count)
            config(restored,auto=False);rpc('quota.arm',enabled=True)
            rpc('quota.chat',prompt='Wait for the local stream after reconnect.',cwd=str(workspace),threadId=restored)
            wait_for(lambda:current(restored).get('status')=='inProgress' and len(requests)>=count+1,'restored prepared target did not start')
            refresh(25,100);wait_for(lambda:stopped(restored),'restored target was not interrupted')
            check('restored target starts and receives real interruption',guard.data['threads'][restored]['trigger_windows']==['primary'])
            rpc('quota.arm',enabled=False)

            check('all generation uses local endpoint',all(p=='/v1/responses' for p in requests))
            service.shutdown();service=None
    finally:
        if service:service.shutdown()
        end.set();server.shutdown();server.server_close()
    report={'passed':len(checks),'checks':checks,'model_requests_local':len(requests),'model_generation_requests_external':0,'real_process_and_turn_events':True,'quota_and_account_are_simulated':True,'natural_quota_refill_tested':False,'shared_desktop_control_tested':False,'actions':actions}
    if output:Path(output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

if __name__=='__main__':
    result=run(sys.argv[1] if len(sys.argv)>1 else None)
    print(json.dumps({k:v for k,v in result.items() if k not in ('checks','actions')}),flush=True)
