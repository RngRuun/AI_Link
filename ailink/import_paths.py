"""Bounded recursive discovery of user-selected local JSONL roots."""
from __future__ import annotations

import os
import stat
from pathlib import Path

MAX_ROOTS = 20
MAX_FILES = 5000
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_VISITED = 50000
MAX_DEPTH = 32


def collect_jsonl(paths, *, min_mtime=None, allow_empty=False):
    if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_ROOTS or any(not isinstance(p, str) or not p.strip() for p in paths):
        raise ValueError("每次请选择 1–20 个 JSONL 文件或文件夹。")
    files, seen_files, seen_dirs = [], set(), set()
    total, visited, skipped_links = 0, 0, 0

    def inspect(path, explicit=False):
        nonlocal skipped_links
        try:
            info = path.lstat()
        except OSError as error:
            raise ValueError(f"无法读取「{path.name}」，请检查目录或文件是否仍然可用；本批未写入。") from error
        # Do not follow Windows junctions/reparse points or Unix symbolic links.
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            if explicit:
                raise ValueError("请选择实际文件或目录，不支持链接、联接点或重解析点。")
            skipped_links += 1
            return None
        return info

    def add_file(path, info):
        nonlocal total
        if min_mtime is not None and info.st_mtime < min_mtime:
            return
        identity = os.path.normcase(str(path.resolve()))
        if identity in seen_files:
            return
        if info.st_size > MAX_FILE_BYTES:
            raise ValueError(f"「{path.name}」超过单文件 64 MB 限制；本批未写入。")
        total += info.st_size
        if len(files) >= MAX_FILES or total > MAX_TOTAL_BYTES:
            raise ValueError("本次目录超过 5000 份记录或合计 1 GB；请改选较小的月份或日期目录。本批未写入。")
        seen_files.add(identity)
        files.append(path)

    for value in paths:
        root = Path(value)
        info = inspect(root, explicit=True)
        if stat.S_ISREG(info.st_mode):
            if root.suffix.lower() != '.jsonl':
                raise ValueError("请选择 JSONL 文件，或包含 JSONL 的年／月／日文件夹。")
            add_file(root, info)
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("只能导入普通 JSONL 文件或文件夹。")
        pending = [(root, 0)]
        while pending:
            directory, depth = pending.pop()
            identity = os.path.normcase(str(directory.resolve()))
            if identity in seen_dirs:
                continue
            seen_dirs.add(identity)
            try:
                with os.scandir(directory) as entries:
                    children = []
                    for entry in entries:
                        visited += 1
                        if visited > MAX_VISITED:
                            raise ValueError("所选目录内容过多，请直接选择 sessions 下的年／月／日目录；本批未写入。")
                        children.append(Path(entry.path))
                children.sort(key=lambda p: p.name.casefold())
            except OSError as error:
                raise ValueError(f"无法读取目录「{directory.name}」；本批未写入。") from error
            subdirectories = []
            for child in children:
                child_info = inspect(child)
                if child_info is None:
                    continue
                if stat.S_ISDIR(child_info.st_mode):
                    if depth >= MAX_DEPTH:
                        raise ValueError("目录层级超过 32 层，请选择更接近会话文件的目录；本批未写入。")
                    subdirectories.append((child, depth + 1))
                elif stat.S_ISREG(child_info.st_mode) and child.suffix.lower() == '.jsonl':
                    add_file(child, child_info)
            pending.extend(reversed(subdirectories))
    if not files and not allow_empty:
        raise ValueError("所选目录及子目录中没有找到 JSONL 记录。")
    return {'paths': files, 'files': len(files), 'bytes': total, 'skipped_links': skipped_links}
