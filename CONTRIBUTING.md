# 开发与提交

主入口是 `desktop-app/electron/main.cjs` 与 `desktop_worker.py`，前端是 Vue 3／TypeScript。修改业务规则时同时考虑 IPC 校验、状态持久化、异常与结果未知情况。

1. 按 README 安装依赖；运行 `scripts/verify.ps1`。
2. UI 修改同时检查深浅主题、560／760／1024／1440 宽度与 150% 缩放。保留减少动画、键盘焦点、独立滚动和稳定点击区域。
3. 有本机 Codex CLI 时可运行 `scripts/verify.ps1 -TestCodex`；这些联调使用隔离目录和本机模型替身，不使用真实模型额度。
4. 需要检查打包产物时先构建，再运行 `python tests/smoke_packaged.py`。未安装 Codex 的机器加 `--skip-shared`。
5. 不提交 `.venv`、`node_modules`、`.artifacts`、`dist`、`dist-vue`、日志、缓存、登录信息、导出聊天或用户恢复备份。

GitHub Actions 定义 Windows 单元、IPC、UI、构建与打包检查。真实账号、VPN 与自然额度重置仍需要单独验收，不把受控模拟视为这些验收。
