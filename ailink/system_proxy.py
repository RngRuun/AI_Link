"""Transactional Windows user-proxy ownership with compare-before-restore."""
from __future__ import annotations

import ctypes
import json
import os
import threading
from pathlib import Path

from .config import atomic_json

INTERNET = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
ENVIRONMENT = r"Environment"
PROXY_NAMES = ("ProxyEnable", "ProxyServer", "ProxyOverride")
ENV_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


class WindowsRegistry:
    def __init__(self):
        if os.name != "nt":
            raise OSError("系统代理接管仅支持 Windows。")
        import winreg
        self.reg = winreg

    def read(self, section, name):
        try:
            with self.reg.OpenKey(self.reg.HKEY_CURRENT_USER, section) as key:
                value, kind = self.reg.QueryValueEx(key, name)
            return {"value": value, "kind": kind}
        except FileNotFoundError:
            return None

    def write(self, section, name, record):
        with self.reg.CreateKey(self.reg.HKEY_CURRENT_USER, section) as key:
            if record is None:
                try:
                    self.reg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
            else:
                self.reg.SetValueEx(key, name, 0, record["kind"], record["value"])

    def notify(self):
        wininet = ctypes.WinDLL("wininet", use_last_error=True)
        wininet.InternetSetOptionW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
        wininet.InternetSetOptionW.restype = ctypes.c_int
        for option in (39, 37):  # SETTINGS_CHANGED, REFRESH
            if not wininet.InternetSetOptionW(None, option, None, 0):
                raise ctypes.WinError(ctypes.get_last_error())
        result = ctypes.c_size_t()
        send = ctypes.windll.user32.SendMessageTimeoutW
        send.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t, ctypes.c_void_p,
                         ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_size_t)]
        send.restype = ctypes.c_ssize_t
        send(0xFFFF, 0x001A, 0, ctypes.cast(ctypes.c_wchar_p("Environment"), ctypes.c_void_p), 2, 1000, ctypes.byref(result))


class SystemProxy:
    def __init__(self, state_dir: Path, backend=None, port=18790):
        self.path = state_dir / "proxy-ownership.json"
        self.backend = backend or WindowsRegistry()
        self.port = port
        self.lock = threading.RLock()

    def current(self):
        b = self.backend
        return {
            "enabled": bool((b.read(INTERNET, "ProxyEnable") or {}).get("value", 0)),
            "server": (b.read(INTERNET, "ProxyServer") or {}).get("value", ""),
            "pac_configured": bool((b.read(INTERNET, "AutoConfigURL") or {}).get("value")),
            "owned": self.path.exists(),
        }

    def acquire(self, terminal=False):
        with self.lock:
            if self.path.exists():
                raise RuntimeError("已有代理接管记录，请先关闭优化或恢复原设置。")
            override = (self.backend.read(INTERNET, "ProxyOverride") or {}).get("value", "")
            bypasses = [x for x in str(override).split(";") if x]
            for host in ("<local>", "localhost", "127.*", "[::1]"):
                if host not in bypasses:
                    bypasses.append(host)
            proposed = [(INTERNET, "ProxyServer", {"value": f"127.0.0.1:{self.port}", "kind": 1}),
                        (INTERNET, "ProxyOverride", {"value": ";".join(bypasses), "kind": 1}),
                        (INTERNET, "ProxyEnable", {"value": 1, "kind": 4})]
            if terminal:
                for name in ENV_NAMES:
                    value = "localhost,127.0.0.1,::1" if name == "NO_PROXY" else f"http://127.0.0.1:{self.port}"
                    if name == "NO_PROXY":
                        original = (self.backend.read(ENVIRONMENT, name) or {}).get("value", "")
                        value = str(original) + ("," if original else "") + value
                    proposed.append((ENVIRONMENT, name, {"value": value, "kind": 1}))
            records = [dict(section=section, name=name, original=self.backend.read(section, name), expected=value)
                       for section, name, value in proposed]
            # Journal before the first write so the watchdog can undo a partial transaction.
            atomic_json(self.path, {"schema": 1, "owner_pid": os.getpid(), "port": self.port, "records": records})
            try:
                for section, name, value in proposed:
                    self.backend.write(section, name, value)
                self.backend.notify()
            except BaseException:
                self.restore()
                raise

    def restore(self, expected_owner=None):
        with self.lock:
            if not self.path.exists():
                return {"restored": 0, "preserved": 0}
            snapshot = json.loads(self.path.read_text("utf-8"))
            if expected_owner is not None and snapshot.get("owner_pid") != expected_owner:
                return {"restored": 0, "preserved": 0}
            if snapshot.get("schema") != 1 or not isinstance(snapshot.get("records"), list):
                raise RuntimeError("恢复记录损坏，请通过 Windows 代理设置手动恢复。")
            restored = preserved = 0
            for record in reversed(snapshot["records"]):
                section, name = record["section"], record["name"]
                if section not in (INTERNET, ENVIRONMENT) or name not in (*PROXY_NAMES, *ENV_NAMES):
                    raise RuntimeError("恢复记录含有未知设置，已停止自动恢复。")
                current = self.backend.read(section, name)
                if current == record["expected"]:
                    self.backend.write(section, name, record["original"])
                    restored += 1
                elif current != record["original"]:
                    preserved += 1
            self.backend.notify()
            self.path.unlink()
            return {"restored": restored, "preserved": preserved}


def watchdog(owner_pid: int, state_dir: Path):
    """An independent process restores settings even after a hard backend crash."""
    if os.name != "nt":
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, owner_pid)
    if handle:
        kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
        kernel.CloseHandle(handle)
    SystemProxy(state_dir).restore(expected_owner=owner_pid)
