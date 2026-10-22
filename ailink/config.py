"""Validated, atomic configuration; no account credentials are stored."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

DEFAULTS = {
    "upstreams": [{"host": "127.0.0.1", "port": 7890}],
    "attempts": 4,
    "connect_timeout": 5,
    "retry_budget": 25,
    "keepalive_idle": 60,
    "keepalive_interval": 15,
    "terminal_environment": False,
    "route_strategy": "adaptive",
    "race_delay_ms": 150,
}


class ValidationError(ValueError):
    pass


def validate_config(raw: object) -> dict:
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise ValidationError("配置包含未知字段，或格式不是对象。")
    result = dict(DEFAULTS) | raw
    upstreams = result["upstreams"]
    if not isinstance(upstreams, list) or not 1 <= len(upstreams) <= 2:
        raise ValidationError("请配置 1–2 个本地 HTTP / mixed 代理。")
    normalized = []
    for entry in upstreams:
        if not isinstance(entry, dict) or set(entry) != {"host", "port"}:
            raise ValidationError("代理必须包含 host 和 port。")
        host, port = entry["host"], entry["port"]
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValidationError("此版本仅连接本机代理，避免意外外发流量。")
        if type(port) is not int or not 1 <= port <= 65535 or port in (18790, 18791):
            raise ValidationError("上游端口必须为 1–65535，且不能指向面板或中继本身。")
        item = {"host": "127.0.0.1" if host == "localhost" else host, "port": port}
        if item in normalized:
            raise ValidationError("上游代理不能重复。")
        normalized.append(item)
    result["upstreams"] = normalized
    bounds = {"attempts": (1, 8), "connect_timeout": (2, 15), "retry_budget": (5, 90),
              "keepalive_idle": (30, 300), "keepalive_interval": (10, 60), "race_delay_ms": (50, 500)}
    for key, (low, high) in bounds.items():
        value = result[key]
        if type(value) is not int or not low <= value <= high:
            raise ValidationError(f"{key} 必须是 {low}–{high} 范围内的整数。")
    if type(result["terminal_environment"]) is not bool:
        raise ValidationError("终端环境变量开关必须是布尔值。")
    if result["route_strategy"] not in ("ordered", "adaptive", "race"):
        raise ValidationError("请选择按顺序、自适应或错峰竞速策略。")
    return result


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class ConfigStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "settings.json"
        self.value = validate_config(json.loads(self.path.read_text("utf-8"))) if self.path.exists() else validate_config({})

    def save(self, value: dict) -> dict:
        new = validate_config(value)
        atomic_json(self.path, new)
        self.value = new
        return new
