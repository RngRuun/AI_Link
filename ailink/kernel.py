"""Conservative FlClash adapter: edit persistent settings only while it is stopped."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .config import atomic_json


class FlClashAdapter:
    def __init__(self, state_dir):
        self.state_dir = state_dir
        self.root = Path(os.environ.get("APPDATA", "")) / "com.follow" / "clash"

    def running(self):
        if os.name != "nt":
            return False
        output = subprocess.run(["tasklist.exe", "/FI", "IMAGENAME eq FlClash.exe", "/FO", "CSV", "/NH"],
                                capture_output=True, timeout=5, creationflags=0x08000000)
        return b'"FlClash.exe"' in output.stdout

    def status(self):
        path = self.root / "shared_preferences.json"
        if not path.exists():
            return {"available": False, "running": False}
        try:
            prefs = json.loads(path.read_text("utf-8"))
            config = json.loads(prefs["flutter.config"])
            return dict(available=True, running=self.running(),
                        keepalive=config["patchClashConfig"].get("keep-alive-interval"),
                        close_connections=config["appSettingProps"].get("closeConnections"),
                        optimized=config["patchClashConfig"].get("keep-alive-interval") == 15 and config["appSettingProps"].get("closeConnections") is False)
        except (OSError, ValueError, KeyError):
            return {"available": False, "running": False, "error": "无法读取已知格式的 FlClash 设置。"}

    def apply(self):
        if self.running():
            raise RuntimeError("请先从托盘彻底退出 FlClash，再保存内核设置。这样可避免被运行中的程序覆盖。")
        path = self.root / "shared_preferences.json"
        prefs = json.loads(path.read_text("utf-8"))
        config = json.loads(prefs["flutter.config"])
        original = {"schema": 1, "keepalive_interval": config["patchClashConfig"]["keep-alive-interval"],
                    "close_connections": config["appSettingProps"]["closeConnections"]}
        config["patchClashConfig"]["keep-alive-interval"] = 15
        config["appSettingProps"]["closeConnections"] = False
        backup = self.state_dir / "flclash-original-fields.json"
        if not backup.exists():
            atomic_json(backup, original)
        prefs["flutter.config"] = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
        atomic_json(path, prefs)
        # Preserve all other preferences and all subscription/profile contents.
        return {"saved": True, "note": "保活间隔已设为 15 秒；切换策略时保留已有连接。重新打开 FlClash 后生效。"}

    def restore(self):
        if self.running():
            raise RuntimeError("请先彻底退出 FlClash，再恢复内核设置。")
        backup = self.state_dir / "flclash-original-fields.json"
        if not backup.exists():
            raise RuntimeError("没有由本工具创建的 FlClash 备份。")
        old = json.loads(backup.read_text("utf-8"))
        if old.get("schema") != 1:
            raise RuntimeError("内核字段备份格式不受支持。")
        path = self.root / "shared_preferences.json"
        prefs = json.loads(path.read_text("utf-8"))
        config = json.loads(prefs["flutter.config"])
        config["patchClashConfig"]["keep-alive-interval"] = old["keepalive_interval"]
        config["appSettingProps"]["closeConnections"] = old["close_connections"]
        prefs["flutter.config"] = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
        atomic_json(path, prefs)
        backup.unlink()
        return {"restored": True}
