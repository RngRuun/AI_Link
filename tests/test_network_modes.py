import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from ailink.config import validate_config
from ailink.events import Telemetry
from ailink.network_measure import NetworkMeasure
from ailink.routing import RouteHealth
from ailink.transport import dial,RelayHandler,UpstreamFailure


class Connection:
    def __init__(self):self.closed=threading.Event()
    def close(self):self.closed.set()


class NetworkModeTests(unittest.TestCase):
    def setUp(self):
        self.upstreams=[{'host':'127.0.0.1','port':10001},{'host':'127.0.0.1','port':10002}]
        self.config=validate_config({'upstreams':self.upstreams,'route_strategy':'race','race_delay_ms':50,'attempts':1})
    def test_race_returns_fast_backup_without_waiting_for_slow_loser(self):
        slow,fast=Connection(),Connection()
        def connect(u,*_):time.sleep(.4 if u['port']==10001 else .01);return slow if u['port']==10001 else fast
        telemetry=Telemetry();start=time.monotonic()
        self.assertIs(dial(self.upstreams,'example.test:443',self.config,threading.Event(),telemetry,connect_fn=connect),fast)
        self.assertLess(time.monotonic()-start,.3)
        self.assertTrue(slow.closed.wait(1));self.assertFalse(fast.closed.is_set())
        self.assertEqual(telemetry.snapshot()['counters']['fallback'],1)
    def test_fast_first_path_prevents_unnecessary_second_connect(self):
        calls=[];first=Connection()
        def connect(u,*_):calls.append(u);return first
        self.assertIs(dial(self.upstreams,'example.test:443',self.config,threading.Event(),Telemetry(),connect_fn=connect),first)
        time.sleep(.1);self.assertEqual(len(calls),1)
    def test_stop_closes_inflight_race_results(self):
        stop=threading.Event();connections=[]
        def connect(*_):c=Connection();connections.append(c);time.sleep(.2);return c
        timer=threading.Timer(.08,stop.set);timer.start()
        with self.assertRaises(UpstreamFailure):dial(self.upstreams,'example.test:443',self.config,stop,Telemetry(),connect_fn=connect)
        time.sleep(.25);self.assertTrue(connections);self.assertTrue(all(c.closed.is_set() for c in connections))
    def test_auth_denial_stops_race_retries(self):
        calls=[]
        def connect(*_):calls.append(1);raise UpstreamFailure('proxy_rejected',407)
        with self.assertRaises(UpstreamFailure):dial(self.upstreams,'example.test:443',{**self.config,'attempts':4},threading.Event(),Telemetry(),connect_fn=connect)
        time.sleep(.1);self.assertEqual(len(calls),1)
    def test_circuit_penalty_expires_and_success_recovers(self):
        health=RouteHealth();a,b=self.upstreams
        health.record(a,True,20);health.record(b,True,80)
        self.assertEqual(health.ordered(self.upstreams)[0],a)
        health.record(a,False,100);health.record(a,False,100)
        self.assertEqual(health.ordered(self.upstreams)[0],b)
        health.record(a,True,10)
        self.assertEqual(health.ordered(self.upstreams)[0],a)
        self.assertEqual(health.snapshot(self.upstreams)[0]['cooldown_seconds'],0)
    def test_probe_observations_expire(self):
        health=RouteHealth();a,b=self.upstreams
        health.benchmark(a,{'p50_ms':100,'succeeded':6});health.benchmark(b,{'p50_ms':20,'succeeded':6})
        self.assertEqual(health.ordered(self.upstreams)[0],b)
        with patch('ailink.routing.time.time',return_value=time.time()+301):self.assertEqual(health.ordered(self.upstreams)[0],a)
    def test_invalid_strategy_and_stagger_are_rejected(self):
        for fields in [{'route_strategy':'magic'},{'race_delay_ms':True},{'race_delay_ms':0}]:
            with self.assertRaises(ValueError):validate_config(fields)
    def test_series_uses_actual_counter_deltas_and_elapsed_time(self):
        telemetry=Telemetry();telemetry.add(uploaded=2048,downloaded=4096)
        telemetry.last_tick=time.monotonic()-2
        data=telemetry.snapshot();self.assertAlmostEqual(data['series'][0]['up_bps'],1024,delta=2)
        self.assertAlmostEqual(data['series'][0]['down_bps'],2048,delta=4)
        telemetry.connection(10);telemetry.connection(90)
        self.assertEqual(telemetry.snapshot()['connections'],{'samples':2,'p50_ms':50,'p95_ms':90})
    def test_slow_upload_receiver_does_not_block_reverse_traffic_or_lose_half_close(self):
        client,left=socket.socketpair();right,server=socket.socketpair()
        for sock in (client,left,right,server):sock.settimeout(4)
        right.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,4096)
        owner=Mock();owner.stop_event=threading.Event();owner.telemetry=Telemetry()
        errors=[]
        def pump():
            try:RelayHandler.pump(left,right,owner)
            except Exception as e:errors.append(e)
        worker=threading.Thread(target=pump,daemon=True);worker.start()
        payload=b'opaque'*350000
        def upload():client.sendall(payload);client.shutdown(socket.SHUT_WR)
        sender=threading.Thread(target=upload,daemon=True);sender.start()
        try:
            time.sleep(.1);server.sendall(b'reverse-control')
            self.assertEqual(client.recv(100),b'reverse-control')
            received=bytearray()
            while len(received)<len(payload):
                part=server.recv(65536)
                if not part:break
                received.extend(part)
            self.assertEqual(bytes(received),payload)
            sender.join(2);self.assertFalse(sender.is_alive())
            self.assertEqual(server.recv(1),b'')
            server.shutdown(socket.SHUT_WR);self.assertEqual(client.recv(1),b'')
            worker.join(2);self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            self.assertEqual(owner.telemetry.snapshot()['counters']['uploaded'],len(payload))
        finally:
            owner.stop_event.set()
            for sock in (client,left,right,server):sock.close()
    def test_benchmark_uses_complete_comparable_samples_and_cancellation(self):
        relay=Mock();relay.port=18790;relay.health=RouteHealth()
        measure=NetworkMeasure(lambda:self.config,relay)
        def probe(provider,route,config,*_):
            return dict(provider=provider,route=route,reachable=True,status=405,outcome='method_unsupported',latency_ms=80 if route=='relay' else (100 if config['upstreams'][0]['port']==10001 else 30))
        with patch('ailink.network_measure.probe',side_effect=probe):
            measure.start()
            for _ in range(100):
                if not measure.snapshot()['running']:break
                time.sleep(.01)
            result=measure.snapshot();self.assertEqual(result['completed'],18);self.assertEqual(result['best'],'upstream-1')
            self.assertEqual(len(result['summaries']),3)
            measure.invalidate();self.assertIsNone(measure.snapshot()['best']);self.assertTrue(measure.snapshot()['stale'])
        def slow(*args):time.sleep(.08);return probe(*args)
        with patch('ailink.network_measure.probe',side_effect=slow):
            measure.start();measure.cancel()
            for _ in range(100):
                if not measure.snapshot()['running']:break
                time.sleep(.01)
            self.assertTrue(measure.snapshot()['cancelled']);self.assertIsNone(measure.snapshot()['best'])

if __name__=='__main__':unittest.main()
