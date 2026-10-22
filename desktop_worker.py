"""Headless Electron child. JSONL over inherited pipes; no HTTP control listener."""
from __future__ import annotations
import argparse
import ctypes
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


def child_command(*args):
    return [sys.executable, *args] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve()), *args]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, default=Path(os.environ.get("LOCALAPPDATA", str(Path.cwd()))) / "AIExperience/Network")
    parser.add_argument("--watchdog", type=int)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    from ailink.system_proxy import SystemProxy, watchdog
    if args.watchdog:
        if args.ready_file:
            args.ready_file.write_text("ready", encoding="ascii")
        watchdog(args.watchdog, args.state_dir)
        return
    if args.restore:
        SystemProxy(args.state_dir).restore()
        return
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    write_lock = threading.Lock()
    def send(value):
        with write_lock:
            sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    from ailink.instance import acquire_instance
    mutex = acquire_instance(args.state_dir)
    if not mutex:
        send({"event": "fatal", "error": "另一版本的 AI Link 正在运行，请关闭旧版后重新启动。"})
        return
    service = None
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="desktop-command")
    try:
        from ailink.service import Application
        from ailink.desktop_bridge import DesktopBridge
        system = SystemProxy(args.state_dir)
        system.restore()
        args.state_dir.mkdir(parents=True, exist_ok=True)
        ready = args.state_dir / f"guardian-{os.getpid()}.ready"
        guardian = subprocess.Popen(child_command("--state-dir", str(args.state_dir), "--watchdog", str(os.getpid()), "--ready-file", str(ready)),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 12
        while not ready.exists():
            if guardian.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("独立恢复守护启动失败；未接管系统代理。")
            time.sleep(.05)
        ready.unlink()
        service = Application(Path(__file__).resolve().parent, args.state_dir, system)
        service.start_observers()
        bridge = DesktopBridge(service)
        send({"event": "ready"})
        def execute(request):
            request_id = request.get("id")
            try:
                result = bridge.dispatch(request.get("method"), request.get("params", {}))
                send({"id": request_id, "result": result})
            except Exception as error:
                send({"id": request_id, "error": str(error)[:350]})
        for line in sys.stdin:
            if len(line) > 131072:
                send({"event": "error", "error": "操作参数过大。"})
                continue
            try:
                request = json.loads(line)
                if not isinstance(request, dict) or type(request.get("id")) is not int:
                    raise ValueError()
                if request.get("method") == "shutdown":
                    service.shutdown()
                    send({"id": request["id"], "result": {"closed": True}})
                    break
                pool.submit(execute, request)
            except ValueError:
                send({"event": "error", "error": "操作消息格式无效。"})
    except Exception as error:
        send({"event": "fatal", "error": str(error)[:350]})
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        if service:
            service.shutdown()
        ctypes.windll.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
