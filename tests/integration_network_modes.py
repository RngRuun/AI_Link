"""Controlled real-socket latency/replay checks; --live adds credential-free HEAD probes."""
import argparse
import json
import os
from pathlib import Path
import socket
import socketserver
import statistics
import sys
import tempfile
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ailink.config import validate_config
from ailink.events import Telemetry
from ailink.service import Application
from ailink.system_proxy import SystemProxy
from ailink.transport import Relay,read_header


def run(report_path, live):
    report_path.parent.mkdir(parents=True,exist_ok=True)
    received=[];lock=threading.Lock()
    class Proxy(socketserver.ThreadingTCPServer):
        daemon_threads=True
    class Echo(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                self.request.settimeout(3);read_header(self.request)
                time.sleep(self.server.delay)
                self.request.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                payload=bytearray()
                while True:
                    data=self.request.recv(65536)
                    if not data:break
                    payload.extend(data);self.request.sendall(data)
                if payload:
                    with lock:received.append(bytes(payload))
            except OSError:pass
    servers=[]
    for delay in (.35,.02):
        server=Proxy(('127.0.0.1',0),Echo);server.delay=delay;servers.append(server)
        threading.Thread(target=server.serve_forever,daemon=True).start()
    config=validate_config({'upstreams':[{'host':'127.0.0.1','port':s.server_address[1]} for s in servers],
                            'route_strategy':'ordered','race_delay_ms':50,'attempts':1})
    telemetry=Telemetry();relay=Relay(lambda:config,telemetry,port=0);relay.start()
    payload=b'opaque-test-payload-'*1024
    times={}
    try:
        for strategy in ('ordered','race'):
            config['route_strategy']=strategy;samples=[]
            for _ in range(3):
                with socket.create_connection(('127.0.0.1',relay.port),3) as client:
                    start=time.monotonic();client.sendall(b'CONNECT example.test:443 HTTP/1.1\r\nHost: example.test:443\r\n\r\n')
                    header,_=read_header(client);assert b' 200 ' in header
                    samples.append((time.monotonic()-start)*1000)
                    client.sendall(payload);client.shutdown(socket.SHUT_WR)
                    echoed=bytearray()
                    while True:
                        data=client.recv(65536)
                        if not data:break
                        echoed.extend(data)
                    assert bytes(echoed)==payload
            times[strategy]=round(statistics.median(samples),2)
        time.sleep(.45)
        assert len(received)==6 and all(p==payload for p in received),'application payload replayed or lost'
        assert times['race'] < times['ordered']*.7,(times,'controlled fast backup did not win')
    finally:
        relay.stop()
        for server in servers:server.shutdown();server.server_close()
    result={'controlled_socket_test':{'injected_connect_delays_ms':[350,20],'median_connect_ms':times,
                                    'payloads_expected':6,'payloads_received':len(received),'no_replay':True,
                                    'scope':'local simulated proxies; not a VPN speed guarantee'}}
    if live:
        with tempfile.TemporaryDirectory(prefix='ai-link-live-',dir=report_path.parent) as directory:
            state=Path(directory)
            actual=Path(os.environ['LOCALAPPDATA'])/'AIExperience/Network/settings.json'
            settings=validate_config(json.loads(actual.read_text(encoding='utf-8')) if actual.exists() else {})
            app=Application(Path(__file__).resolve().parents[1],state,relay_port=0)
            app.store.save(settings)
            before=app.system.current()
            try:
                app.measure_paths();deadline=time.monotonic()+100
                while (snapshot:=app.measure.snapshot())['running'] and time.monotonic()<deadline:time.sleep(.2)
                assert not snapshot['running'],'live measurement deadline exceeded'
                result['live_heads']={k:snapshot[k] for k in ('total','completed','cancelled','summaries','best','error')}
                result['live_heads']['statuses']=[{'provider':r['provider'],'path':r['key'],'status':r.get('status'),'outcome':r.get('outcome')} for r in snapshot['results']]
                assert app.system.current()==before
                result['system_proxy_unchanged']=True
            finally:app.shutdown()
    report_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('report',type=Path);parser.add_argument('--live',action='store_true')
    args=parser.parse_args();run(args.report.resolve(),args.live)
