"""Fault-injection tests: recovery, non-replay, ownership, validation and API boundaries."""
from __future__ import annotations

import copy
import http.client
import json
import socket
import socketserver
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ailink.config import ConfigStore, ValidationError, validate_config
from ailink.diagnostics import Diagnostics
from ailink.events import Telemetry
from ailink.kernel import FlClashAdapter
from ailink.server import Application, make_server
from ailink.system_proxy import ENVIRONMENT, INTERNET, SystemProxy
from ailink.transport import Relay, UpstreamFailure, dial, read_header, validate_authority


class MemoryRegistry:
    def __init__(self):
        self.values = {(INTERNET, "ProxyEnable"): {"value": 1, "kind": 4},
                       (INTERNET, "ProxyServer"): {"value": "127.0.0.1:7890", "kind": 1},
                       (INTERNET, "ProxyOverride"): {"value": "*.company.test", "kind": 1}}
        self.fail_once = None

    def read(self, section, name):
        return copy.deepcopy(self.values.get((section, name)))

    def write(self, section, name, value):
        if self.fail_once == name:
            self.fail_once = None
            raise OSError("simulated registry error")
        if value is None:
            self.values.pop((section, name), None)
        else:
            self.values[(section, name)] = copy.deepcopy(value)

    def notify(self):
        pass


class SettingsTests(unittest.TestCase):
    def test_unknown_or_remote_configuration_rejected(self):
        cases = [{"invalid": True}, {"upstreams": [{"host": "example.com", "port": 7890}]},
                 {"upstreams": [{"host": "127.0.0.1", "port": 18790}]},
                 {"attempts": True}, {"retry_budget": 900}, {"terminal_environment": "false"}]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_config(value)

    def test_atomic_persistence_and_reject_leaves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory))
            store.save({"attempts": 3})
            before = store.path.read_bytes()
            with self.assertRaises(ValidationError):
                store.save({"attempts": 0})
            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(ConfigStore(Path(directory)).value["attempts"], 3)

    def test_duplicate_upstreams_rejected(self):
        with self.assertRaises(ValidationError):
            validate_config({"upstreams": [{"host": "localhost", "port": 7890}, {"host": "127.0.0.1", "port": 7890}]})

    def test_connect_authority_validation(self):
        self.assertEqual(validate_authority("chatgpt.com:443"), "chatgpt.com:443")
        self.assertEqual(validate_authority("[::1]:443"), "[::1]:443")
        for value in ("a:443\r\nX: y", "a:443/path", "user@a:443", "a", "a:65536"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_authority(value)


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.backend = MemoryRegistry()
        self.original = copy.deepcopy(self.backend.values)
        self.system = SystemProxy(Path(self.temp.name), self.backend)

    def tearDown(self):
        self.temp.cleanup()

    def test_full_roundtrip_and_environment_cleanup(self):
        self.system.acquire(terminal=True)
        self.assertEqual(self.system.current()["server"], "127.0.0.1:18790")
        self.assertIn((ENVIRONMENT, "HTTPS_PROXY"), self.backend.values)
        self.system.restore()
        self.assertEqual(self.original, self.backend.values)
        self.assertFalse(self.system.path.exists())

    def test_partial_write_failure_rolls_back(self):
        self.backend.fail_once = "ProxyOverride"
        with self.assertRaises(OSError):
            self.system.acquire()
        self.assertEqual(self.original, self.backend.values)

    def test_user_changes_are_not_overwritten_on_restore(self):
        self.system.acquire()
        self.backend.write(INTERNET, "ProxyServer", {"value": "127.0.0.1:9999", "kind": 1})
        result = self.system.restore()
        self.assertEqual(self.system.current()["server"], "127.0.0.1:9999")
        self.assertEqual(result["preserved"], 1)

    def test_crash_recovery_from_fresh_instance(self):
        self.system.acquire()
        fresh = SystemProxy(Path(self.temp.name), self.backend)
        fresh.restore()
        self.assertEqual(self.original, self.backend.values)

    def test_double_acquire_refused(self):
        self.system.acquire()
        with self.assertRaises(RuntimeError):
            self.system.acquire()
        self.system.restore()

    def test_stale_watchdog_cannot_restore_new_owner(self):
        self.system.acquire()
        record=json.loads(self.system.path.read_text("utf-8"))
        result=self.system.restore(expected_owner=record["owner_pid"]+100000)
        self.assertEqual(result["restored"],0)
        self.assertTrue(self.system.current()["owned"])
        self.system.restore()


class FakeProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class EchoConnect(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(3)
        header, tail = read_header(self.request)
        self.server.received_headers.append(header)
        self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        if tail:
            self.server.payloads.append(tail)
            self.request.sendall(tail)
        while True:
            try:
                data = self.request.recv(65536)
                if not data:
                    break
                self.server.payloads.append(data)
                self.request.sendall(data)
            except OSError:
                break


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeProxy(("127.0.0.1", 0), EchoConnect)
        self.fake.received_headers, self.fake.payloads = [], []
        threading.Thread(target=self.fake.serve_forever, daemon=True).start()
        self.telemetry = Telemetry()
        self.config = validate_config({"upstreams": [{"host": "127.0.0.1", "port": self.fake.server_address[1]}],
                                       "attempts": 2, "retry_budget": 5, "connect_timeout": 2})
        self.relay = Relay(lambda: self.config, self.telemetry, port=0)
        self.relay.start()

    def tearDown(self):
        self.relay.stop()
        self.fake.shutdown()
        self.fake.server_close()

    def client(self):
        sock = socket.create_connection(("127.0.0.1", self.relay.port), 3)
        sock.sendall(b"CONNECT example.test:443 HTTP/1.1\r\nHost: example.test:443\r\n\r\n")
        header, _ = read_header(sock)
        self.assertIn(b" 200 ", header)
        return sock

    def test_payload_is_opaque_and_forwarded_exactly_once(self):
        with self.client() as client:
            payload = b"\x16\x03\x01opaque-encrypted-application-data"
            client.sendall(payload)
            received = bytearray()
            while len(received) < len(payload):
                received.extend(client.recv(4096))
            self.assertEqual(bytes(received), payload)
        self.assertEqual(b"".join(self.fake.payloads), payload)
        self.assertEqual(len(self.fake.received_headers), 1)

    def test_fallback_before_client_payload(self):
        self.config["upstreams"].insert(0, {"host": "127.0.0.1", "port": self.unused_port()})
        with self.client() as client:
            client.sendall(b"only-once")
            self.assertEqual(client.recv(100), b"only-once")
        self.assertEqual(self.telemetry.snapshot()["counters"]["fallback"], 1)

    @staticmethod
    def unused_port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def test_auth_rejection_not_retried(self):
        calls = []
        def denied(*args):
            calls.append(1)
            raise UpstreamFailure("proxy_rejected", 407)
        with self.assertRaises(UpstreamFailure):
            dial(self.config["upstreams"], "example.test:443", self.config,
                 threading.Event(), self.telemetry, connect_fn=denied)
        self.assertEqual(len(calls), 1)

    def test_retry_then_recover_and_cancel(self):
        calls = []
        sentinel = object()
        def transient(*args):
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionRefusedError()
            return sentinel
        self.assertIs(dial(self.config["upstreams"], "example.test:443", self.config,
                           threading.Event(), self.telemetry, connect_fn=transient), sentinel)
        self.assertEqual(self.telemetry.snapshot()["counters"]["retries"], 1)
        stopped = threading.Event(); stopped.set()
        with self.assertRaises(UpstreamFailure):
            dial(self.config["upstreams"], "example.test:443", self.config, stopped, self.telemetry)

    def test_stop_closes_active_tunnel(self):
        client = self.client()
        client.settimeout(2)
        self.relay.stop()
        try:
            self.assertEqual(client.recv(1), b"")
        finally:
            client.close()

    def test_oversized_header_rejected(self):
        with socket.create_connection(("127.0.0.1", self.relay.port), 3) as client:
            client.sendall(b"X" * 33000)
            self.assertIn(b"502", client.recv(1024))

    def test_no_payloads_in_telemetry(self):
        with self.client() as client:
            client.sendall(b"secret-not-in-logs")
            client.recv(100)
        self.assertNotIn("secret-not-in-logs", json.dumps(self.telemetry.snapshot()))

    def test_established_tunnel_is_never_replayed_after_upstream_drop(self):
        class Drop(socketserver.BaseRequestHandler):
            def handle(inner):
                read_header(inner.request)
                self.fake.received_headers.append(b"connected")
                inner.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                self.fake.payloads.append(inner.request.recv(4096))
        dropped = FakeProxy(("127.0.0.1", 0), Drop)
        threading.Thread(target=dropped.serve_forever, daemon=True).start()
        self.config["upstreams"] = [{"host":"127.0.0.1", "port":dropped.server_address[1]}]
        try:
            with self.client() as client:
                client.sendall(b"non-idempotent-encrypted-request")
                self.assertEqual(client.recv(100), b"")
            self.assertEqual(len(self.fake.received_headers), 1)
            self.assertEqual(self.telemetry.snapshot()["counters"]["retries"], 0)
        finally:
            dropped.shutdown(); dropped.server_close()

    def test_http_post_is_forwarded_once_without_retries(self):
        captured = []
        class Post(socketserver.BaseRequestHandler):
            def handle(inner):
                header, body = read_header(inner.request)
                while len(body) < 7:
                    body += inner.request.recv(100)
                captured.append(header+body)
                inner.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
        posted = FakeProxy(("127.0.0.1", 0), Post)
        threading.Thread(target=posted.serve_forever, daemon=True).start()
        self.config["upstreams"] = [{"host":"127.0.0.1", "port":posted.server_address[1]}]
        try:
            with socket.create_connection(("127.0.0.1",self.relay.port),3) as client:
                client.sendall(b"POST http://example.test/submit HTTP/1.1\r\nHost: example.test\r\nContent-Length: 7\r\n\r\npayload")
                received = client.recv(4096)
                self.assertIn(b"200 OK", received)
            self.assertEqual(len(captured),1)
            self.assertTrue(captured[0].endswith(b"payload"))
        finally:
            posted.shutdown(); posted.server_close()


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.system = SystemProxy(Path(self.temp.name), MemoryRegistry())
        self.app = Application(Path(__file__).resolve().parent/'fixtures/http', Path(self.temp.name), self.system, relay_port=0)
        self.server = make_server(self.app, port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.app.disable()
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, method="GET", body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        content = response.read()
        status, response_headers = response.status, dict(response.getheaders())
        connection.close()
        return status, response_headers, content

    def auth(self):
        return {"Content-Type": "application/json", "X-AI-Link-Token": self.app.token}

    def test_static_and_security_headers(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"AI Link", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_csrf_token_required(self):
        self.assertEqual(self.request("/api/disable", "POST", "{}", {"Content-Type":"application/json"})[0], 403)

    def test_cross_origin_rejected_even_with_token(self):
        headers = self.auth() | {"Origin": "https://evil.test"}
        self.assertEqual(self.request("/api/disable", "POST", "{}", headers)[0], 403)

    def test_dns_rebinding_host_rejected(self):
        self.assertEqual(self.request("/api/bootstrap", headers={"Host":"evil.test"})[0], 403)

    def test_traversal_cannot_read_state(self):
        self.assertEqual(self.request("/../app.py")[0], 404)
        self.assertEqual(self.request("/%2e%2e/app.py")[0], 404)

    def test_invalid_config_rejected(self):
        self.assertEqual(self.request("/api/config", "POST", '{"attempts":0}', self.auth())[0], 400)

    def test_failed_preflight_preserves_system_proxy(self):
        before = copy.deepcopy(self.system.backend.values)
        with patch("ailink.service.probe", return_value={"reachable": False}):
            self.app.enable()
            deadline = time.monotonic()+4
            while self.app.operation["running"] and time.monotonic()<deadline:
                time.sleep(.02)
        self.assertFalse(self.app.operation["running"])
        self.assertIsNotNone(self.app.operation["error"])
        self.assertEqual(before, self.system.backend.values)

    def test_report_has_no_token_or_proxy_backup(self):
        status, _, body = self.request("/api/report")
        self.assertEqual(status, 200)
        self.assertNotIn(self.app.token.encode(), body)
        self.assertNotIn(b"original", body)

    def test_successful_enable_and_disable_roundtrip(self):
        before=copy.deepcopy(self.system.backend.values)
        result={"reachable":True,"provider":"codex","route":"relay","outcome":"restricted"}
        with patch("ailink.service.probe",return_value=result):
            self.app.enable()
            deadline=time.monotonic()+4
            while self.app.operation["running"] and time.monotonic()<deadline:
                time.sleep(.02)
        self.assertIsNone(self.app.operation["error"])
        self.assertTrue(self.system.current()["owned"])
        self.app.disable()
        self.assertEqual(before,self.system.backend.values)

    def test_bad_json_and_oversized_requests(self):
        self.assertEqual(self.request("/api/config","POST","[1]",self.auth())[0],400)
        self.assertEqual(self.request("/api/config","POST","{" ,self.auth())[0],400)
        headers=self.auth()|{"Content-Length":"20000"}
        self.assertEqual(self.request("/api/config","POST","{}",headers)[0],413)

    def test_diagnostics_rejects_nested_non_string_provider(self):
        body=json.dumps({"providers":[{}],"routes":["upstream"],"duration":0})
        self.assertEqual(self.request("/api/diagnostics","POST",body,self.auth())[0],400)

    def test_control_actions_reject_unknown_fields(self):
        self.assertEqual(self.request("/api/disable","POST",'{"unexpected":true}',self.auth())[0],400)

    def test_disable_cancels_enable_before_worker_gets_lock(self):
        before=copy.deepcopy(self.system.backend.values)
        with patch("ailink.service.probe") as mocked:
            with self.app.mutation_lock:
                self.app.enable()
                self.app.disable()
            deadline=time.monotonic()+3
            while self.app.operation["running"] and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertFalse(self.app.operation["running"])
            mocked.assert_not_called()
        self.assertEqual(before,self.system.backend.values)
        self.assertFalse(self.app.relay.server)

    def test_shutdown_during_preflight_prevents_late_proxy_acquisition(self):
        entered,release=threading.Event(),threading.Event()
        before=copy.deepcopy(self.system.backend.values)
        def delayed(provider,*args,**kwargs):
            entered.set()
            if not release.wait(4):
                raise RuntimeError("test release deadline")
            return {"reachable":True,"provider":provider,"route":"relay","outcome":"reachable"}
        with patch("ailink.service.probe",side_effect=delayed):
            self.app.enable()
            self.assertTrue(entered.wait(3))
            close=threading.Thread(target=self.app.shutdown)
            close.start()
            self.assertTrue(self.app.shutdown_event.wait(1))
            release.set()
            close.join(timeout=5)
            self.assertFalse(close.is_alive())
            deadline=time.monotonic()+2
            while self.app.operation["running"] and time.monotonic()<deadline:
                time.sleep(.01)
        self.assertEqual(before,self.system.backend.values)
        self.assertFalse(self.app.relay.server)
        self.assertFalse(self.system.current()["owned"])
        with self.assertRaises(RuntimeError):
            self.app.enable()
        with self.assertRaises(RuntimeError):
            self.app.configure({"attempts":3})

    def test_diagnostics_worker_finishes_and_records_samples(self):
        result={"reachable":True,"provider":"gemini","route":"upstream","outcome":"reachable","latency_ms":10}
        with patch("ailink.diagnostics.probe",return_value=result):
            self.app.diagnostics.start(["gemini"],["upstream"],0)
            deadline=time.monotonic()+3
            while self.app.diagnostics.snapshot()["running"] and time.monotonic()<deadline:
                time.sleep(.02)
        job=self.app.diagnostics.snapshot()
        self.assertFalse(job["running"])
        self.assertEqual(len(job["results"]),1)
        self.assertEqual(len(self.app.telemetry.snapshot()["history"]),1)


class KernelTests(unittest.TestCase):
    def test_running_client_blocks_any_persistent_write(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=FlClashAdapter(Path(directory))
            with patch.object(adapter,"running",return_value=True), self.assertRaises(RuntimeError):
                adapter.apply()

    def test_patch_and_restore_preserve_unrelated_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            app_config={"patchClashConfig":{"keep-alive-interval":30,"mixed-port":7890},
                        "appSettingProps":{"closeConnections":True,"locale":"zh-CN"}}
            prefs={"flutter.config":json.dumps(app_config),"unrelated":"preserve-this"}
            path=root/"shared_preferences.json"
            path.write_text(json.dumps(prefs),encoding="utf-8")
            adapter=FlClashAdapter(root/"state")
            adapter.root=root
            with patch.object(adapter,"running",return_value=False):
                adapter.apply()
                patched=json.loads(path.read_text("utf-8"))
                self.assertEqual(patched["unrelated"],"preserve-this")
                self.assertEqual(json.loads(patched["flutter.config"])["patchClashConfig"]["keep-alive-interval"],15)
                backup=(root/"state"/"flclash-original-fields.json").read_text("utf-8")
                self.assertNotIn("preserve-this",backup)
                adapter.restore()
            self.assertEqual(json.loads(json.loads(path.read_text("utf-8"))["flutter.config"]),app_config)


if __name__ == "__main__":
    unittest.main()
