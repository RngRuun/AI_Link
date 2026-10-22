"""Isolated packaged service smoke; no account connection or proxy changes."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import socket,secrets,tempfile,argparse
from datetime import datetime, timezone

base=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('executable',nargs='?',type=Path);parser.add_argument('--skip-shared',action='store_true');args=parser.parse_args()
exe=args.executable.resolve() if args.executable else base/'dist-vue/AI Link/resources/app/backend/AI Link Service.exe'
report_path=base/'.artifacts/packaged-smoke.json';report_path.parent.mkdir(parents=True,exist_ok=True)
fixture=Path(tempfile.mkdtemp(prefix='packaged-',dir=report_path.parent))
logs = fixture / 'codex-home/sessions'
state_dir = fixture / 'state'
logs.mkdir(parents=True)
state_dir.mkdir()
(state_dir/'quota-guard.json').write_text(json.dumps({'mode':'plus','thresholds':{'plus':43,'pro':11}}),encoding='utf-8')
env = {**os.environ, 'CODEX_HOME': str(logs.parent)}
checks = []

def test_bundled_shared_transport(client):
    # A real isolated server, no user auth or model requests. Reaching account
    # validation proves the packaged websocket module and authenticated handshake.
    sys.path.insert(0,str(base))
    from ailink.codex_rpc import find_codex
    reserved=socket.socket();reserved.bind(('127.0.0.1',0));port=reserved.getsockname()[1];reserved.close()
    endpoint=f'ws://127.0.0.1:{port}'
    token=fixture/'shared.token';token.write_text(secrets.token_urlsafe(32),encoding='ascii')
    wrong=fixture/'wrong.token';wrong.write_text(secrets.token_urlsafe(32),encoding='ascii')
    server_env={k:v for k,v in env.items() if k not in ('OPENAI_API_KEY','CODEX_API_KEY')}
    server=subprocess.Popen([find_codex(),'app-server','--listen',endpoint,'--ws-auth','capability-token','--ws-token-file',str(token)],env=server_env,
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline=time.monotonic()+12
        while True:
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.3):break
            except OSError:
                if time.monotonic()>=deadline:raise
                time.sleep(.15)
        try:client.rpc('quota.connect',shared=True,endpoint=endpoint,tokenFile=str(wrong))
        except RuntimeError as error:check('packaged WebSocket rejects wrong credentials','连接' in str(error) and 'ChatGPT' not in str(error))
        else:raise AssertionError('wrong shared token accepted')
        try:client.rpc('quota.connect',shared=True,endpoint=endpoint,tokenFile=str(token))
        except RuntimeError as error:check('packaged authenticated WebSocket reaches real account validation','需要 Codex 已登录的 ChatGPT' in str(error))
        else:raise AssertionError('isolated unauthenticated account accepted')
        check('shared disconnect preserves external server',server.poll() is None and not client.rpc('status')['quota']['connected'])
    finally:
        server.terminate();server.wait(timeout=8)

def check(name, condition):
    if not condition:
        raise AssertionError(name)
    checks.append({'name': name, 'passed': True})

def record(identity, count):
    return '\n'.join(json.dumps(r) for r in [
        {'type': 'session_meta', 'payload': {'id': identity}},
        {'timestamp': datetime.fromtimestamp(time.time()-5, timezone.utc).isoformat(), 'type': 'event_msg',
         'payload': {'type': 'token_count', 'info': {'total_token_usage': {'total_tokens': count}}}},
    ]) + '\n'

a = logs / '2026/10/21/a.jsonl'
b = logs / '2026/10/22/b.jsonl'
for path, identity, count in [(a,'smoke-a',100), (b,'smoke-b',200)]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record(identity,count), encoding='utf-8')

class Client:
    def __init__(self):
        self.log = (fixture / ('stderr-' + str(time.time_ns()) + '.log')).open('w', encoding='utf-8')
        self.process = subprocess.Popen([str(exe),'--state-dir',str(state_dir)], env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
        self.messages = queue.Queue()
        threading.Thread(target=self._read,daemon=True).start()
        self.counter=0
        ready=self._next()
        if ready.get('event')!='ready':
            (fixture/'startup-error.json').write_text(json.dumps(ready,ensure_ascii=False),encoding='utf-8')
            self.process.stdin.close()
            self.process.wait(timeout=8)
            self.log.close()
            raise RuntimeError(str(ready))
    def _read(self):
        for line in self.process.stdout:
            try:self.messages.put(json.loads(line))
            except ValueError:continue
    def _next(self):
        return self.messages.get(timeout=25)
    def rpc(self,method,**params):
        self.counter+=1
        self.process.stdin.write(json.dumps({'id':self.counter,'method':method,'params':params})+'\n')
        self.process.stdin.flush()
        while True:
            response=self._next()
            if response.get('event')=='fatal':raise RuntimeError(response['error'])
            if response.get('id')==self.counter:
                if 'error' in response:raise RuntimeError(response['error'])
                return response['result']
    def sampled(self,after=0):
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            status=self.rpc('status')
            source=status['quota']['source']
            if source['updated']>after and not source['running']:
                if source.get('error'):raise RuntimeError(source['error'])
                return status
            time.sleep(.1)
        raise RuntimeError('automatic observer did not finish')
    def close(self):
        if self.process.poll() is None:
            try:self.rpc('shutdown')
            finally:self.process.stdin.close()
            self.process.wait(timeout=12)
        self.log.close()

def start_client():
    # Windows may finish process-handle cleanup just after wait() returns.
    deadline=time.monotonic()+12
    while True:
        try:return Client()
        except RuntimeError as error:
            if '另一版本' not in str(error) or time.monotonic()>=deadline:raise
            time.sleep(.25)

client=start_client()
try:
    status=client.sampled()
    check('packaged version',status['version']=='0.13.0')
    check('legacy reserve value migrates to both windows',status['quota']['config']['thresholds']=={'plus':{'primary':43,'secondary':43},'pro':{'primary':11,'secondary':11}})
    check('network and guard remain off',not status['relay'] and not status['quota']['enabled'])
    check('observer uses isolated CODEX_HOME and 120 seconds',status['quota']['source']['root']==str(logs) and status['quota']['source']['interval']==120)
    check('automatic recursive personal scan',status['quota']['personal']['plans']['plus']['primary']['total']==300)
    check('weekly personal matches logs',status['quota']['personal']['plans']['plus']['secondary']['total']==300)
    check('team initially independent',status['team']['plans']['plus']['all']['total']==0)
    member=client.rpc('team.member.create',name='离线验收成员')['memberId']
    result=client.rpc('team.import.files',paths=[str(logs/'2026')],memberId=member,plan='plus')
    check('year directory import',result['files']==2 and result['tokens']==300)
    result=client.rpc('team.import.files',paths=[str(logs/'2026/10'),str(a.parent)],memberId=member,plan='plus')
    check('month and overlapping day deduplicate',result['files']==2 and result['tokens']==0)
    old=status['quota']['source']['updated']
    with a.open('a',encoding='utf-8') as stream:stream.write(record('smoke-a',160))
    client.rpc('quota.source.refresh')
    status=client.sampled(old)
    check('changed latest JSONL updates personal chart',status['quota']['personal']['plans']['plus']['primary']['total']==360)
    check('team does not import automatically',status['team']['plans']['plus']['all']['total']==300)
    result=client.rpc('team.import.files',paths=[str(a.parent)],memberId=member,plan='plus')
    check('day import credits only delta',result['files']==1 and result['tokens']==60)
    client.rpc('quota.source.configure',enabled=False)
    check('automatic source switch off',not client.rpc('status')['quota']['source']['enabled'])
    client.rpc('quota.source.configure',enabled=True)
    check('automatic source switch on',client.rpc('status')['quota']['source']['enabled'])
    client.rpc('quota.configure',mode='plus',threshold={'primary':25,'secondary':7},autoResume=False,bucket='codex')
    check('packaged Plus stores independent window limits',client.rpc('status')['quota']['config']['thresholds']['plus']=={'primary':25,'secondary':7})
    client.rpc('quota.configure',mode='pro',threshold={'primary':14,'secondary':4},autoResume=False,bucket='codex')
    expected={'plus':{'primary':25,'secondary':7},'pro':{'primary':14,'secondary':4}}
    check('packaged Pro does not change Plus policy',client.rpc('status')['quota']['config']['thresholds']==expected)
    try:client.rpc('quota.configure',mode='plus',threshold={'primary':25,'secondary':0},autoResume=False,bucket='codex')
    except RuntimeError:pass
    else:raise AssertionError('invalid long threshold accepted')
    check('invalid threshold preserves packaged policy',client.rpc('status')['quota']['config']['thresholds']==expected)
    try:client.rpc('quota.arm',enabled=True)
    except RuntimeError:pass
    else:raise AssertionError('guard accepted no connection or target')
    check('no account or target cannot enable packaged guard',not client.rpc('status')['quota']['enabled'])
    check('storage workspace initializes without scanning user history',client.rpc('status')['storage']['items']==[])
    exported=fixture/'conversations.json'
    exported.write_text(json.dumps([{'id':'fixture-chat-1','title':'Packaged cloud fixture','update_time':time.time()-60*86400,'current_node':'final','mapping':{'final':{'parent':None,'children':[],'message':{'author':{'role':'assistant'},'content':{'parts':['结论：保留本机成果。\n\n```python\nprint(42)\n```']}}}}}]),encoding='utf-8')
    original_export=exported.read_bytes()
    result=client.rpc('storage.import',path=str(exported),label='隔离测试账号')
    check('packaged cloud export import stays local',result['imported']==1 and not result['cloud_modified'])
    storage=client.rpc('status')['storage'];identity=storage['items'][0]['id']
    policy={'source':'cloud','workspace':'隔离测试账号','olderDays':30,'from':None,'to':None,'includeLogs':False,'keep':{'files':True,'final':True,'decisions':True,'code':True}}
    plan=client.rpc('storage.preview',ids=[identity],policy=policy)
    check('packaged retention preview does not mutate cloud or create a batch',plan['count']==1 and client.rpc('status')['storage']['batches']==[])
    result=client.rpc('storage.execute',token=plan['token']);storage=client.rpc('status')['storage']
    check('packaged cloud checklist is explicitly pending',storage['batches'][0]['items'][0]['state']=='cloud_pending' and not result['cloud_modified'])
    check('packaged preservation creates final decision and code files',all((state_dir/'retention/batches'/storage['batches'][0]['id']/__import__('hashlib').sha256(identity.encode()).hexdigest()/'preserved'/f'{name}.md').exists() for name in ('final','decisions','code')))
    check('original export remains unchanged',exported.read_bytes()==original_export)
    client.rpc('storage.pin',id=identity,important=True)
    try:client.rpc('storage.preview',ids=[identity],policy=policy)
    except RuntimeError:pass
    else:raise AssertionError('important cloud record accepted by cleaner')
    check('packaged importance protection rejects cleanup',client.rpc('status')['storage']['preview'] is None)
    check('known storage location is allowlisted',client.rpc('storage.location',kind='library')['path']==str(state_dir/'retention'))
    if not args.skip_shared:test_bundled_shared_transport(client)

finally:
    client.close()

client=start_client()
try:
    status=client.sampled()
    check('restart does not duplicate personal observations',status['quota']['personal']['plans']['plus']['primary']['total']==360)
    check('team persistence stays independent',status['team']['plans']['plus']['all']['total']==360)
    check('independent thresholds survive packaged restart',status['quota']['config']['thresholds']==expected)
    check('guard and account untouched',not status['quota']['connected'] and not status['quota']['enabled'])
    check('retention checklist and cloud importance survive restart',len(status['storage']['batches'])==1 and identity in status['storage']['pinned'] and status['storage']['cloud_count']==1)
finally:
    client.close()

result={'version':'0.13.0','passed':len(checks),'checks':checks,'external_model_requests':0,
        'proxy_mutations':0,'test_data':'synthetic isolated CODEX_HOME and state directory'}
output=report_path
output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'packaged_checks_passed':len(checks)},ensure_ascii=False))
