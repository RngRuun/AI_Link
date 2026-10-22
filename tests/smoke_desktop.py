"""Smoke the actual Electron shell, preload, IPC and bundled backend together.

Uses isolated temporary state/CODEX_HOME. Never connects an account or enables a
proxy. The inspector is bound to loopback, and exists only for this test launch.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import urlopen

import websocket

ROOT = Path(__file__).resolve().parents[1]


def run(executable):
    artifacts = ROOT / '.artifacts'
    artifacts.mkdir(exist_ok=True)
    fixture = Path(tempfile.mkdtemp(prefix='desktop-', dir=artifacts))
    profile, state, codex = (fixture / name for name in ('profile', 'state', 'codex'))
    (codex / 'sessions').mkdir(parents=True)
    env = {**os.environ, 'AI_LINK_PROFILE_DIR': str(profile),
           'AI_LINK_STATE_DIR': str(state), 'CODEX_HOME': str(codex)}
    checks = []
    inspector = None
    process = subprocess.Popen(
        [str(executable), '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0'],
        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    sequence = 0

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append({'name': name, 'passed': True})

    def evaluate(expression):
        nonlocal sequence
        sequence += 1
        inspector.send(json.dumps({'id': sequence, 'method': 'Runtime.evaluate',
                                   'params': {'expression': expression,
                                              'awaitPromise': True, 'returnByValue': True}}))
        while True:
            response = json.loads(inspector.recv())
            if response.get('id') != sequence:
                continue
            if 'error' in response or 'exceptionDetails' in response.get('result', {}):
                raise RuntimeError(str(response)[:500])
            return response['result']['result'].get('value')

    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Desktop exited before startup')
            try:
                port = int((profile / 'DevToolsActivePort').read_text().splitlines()[0])
                with urlopen(f'http://127.0.0.1:{port}/json/list', timeout=1) as response:
                    targets = json.load(response)
                target = next(t for t in targets if t.get('url', '').startswith('ailink://app/'))
                inspector = websocket.create_connection(target['webSocketDebuggerUrl'], timeout=8, suppress_origin=True)
                break
            except (OSError, ValueError, StopIteration):
                time.sleep(.15)
        if inspector is None:
            raise RuntimeError('Desktop inspector never became ready')
        status = None
        while time.monotonic() < deadline:
            result = evaluate("(async()=>{try{return await window.aiLink.request('status')}catch{return null}})()")
            if result and evaluate("!!document.querySelector('.directory-page')"):
                status = result
                break
            time.sleep(.15)
        check('actual shell and bundled service report version', bool(status) and status['version'] == '0.13.0')
        check('fresh desktop starts on directory', evaluate("document.querySelectorAll('.directory-card').length===7"))
        check('fresh proxy and quota protection stay off', not status['relay'] and not status['quota']['enabled'])
        check('personal observer uses isolated sessions', status['quota']['source']['root'] == str(codex / 'sessions'))
        check('team and retention start empty', status['team']['plans']['plus']['all']['total'] == 0 and not status['storage']['items'])
        check('Node access is isolated from renderer', evaluate("typeof require==='undefined' && typeof process==='undefined'"))
        check('unknown IPC command is rejected', evaluate("(async()=>{try{await window.aiLink.request('untrusted.command');return false}catch{return true}})()"))
        for page, selector in [('network', '.network-hero'), ('quota', '.quota-hero'), ('team', '.team-page'), ('storage', '.storage-page')]:
            evaluate(f"document.querySelector('[data-page={page}]').click();true")
            end = time.monotonic() + 5
            while time.monotonic() < end and not evaluate(f"!!document.querySelector('{selector}')"):
                time.sleep(.05)
            check(f'production {page} route renders', evaluate(f"!!document.querySelector('{selector}')&&!document.querySelector('.backend-error')"))
        check('new profile contains no previous appearance', evaluate("localStorage.getItem('ai-link-theme')===null"))
        evaluate('window.close();true')
        inspector.close()
        inspector = None
        process.wait(timeout=25)
        check('desktop shuts down normally', process.returncode == 0)
    finally:
        if inspector:
            inspector.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
    report = {'version': '0.13.0', 'passed': len(checks), 'checks': checks,
              'external_model_requests': 0, 'proxy_mutations': 0,
              'test_data': 'synthetic isolated Electron profile, state and CODEX_HOME'}
    (artifacts / 'desktop-smoke.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'desktop_checks_passed': len(checks)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('executable', nargs='?', type=Path,
                        default=ROOT / 'dist-vue/AI Link/AI Link.exe')
    run(parser.parse_args().executable.resolve())
