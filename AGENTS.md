# 开发规则

- 与用户沟通使用中文；工具运行时的所有终端输出、提示信息、API 错误信息、文件名使用英文。
- 本仓库即工具源码目录，使用仓库下的 Python 虚拟环境 `.venv`（基于 Python 3.13）。
- 使用系统 PATH 中配置好的 adb，不在 EXE 中打包 adb。
- 运行：`.venv\Scripts\python -m adbperfetto`（仓库根目录）。
- 测试：`.venv\Scripts\python -m unittest`（仓库根目录）。
- 真机冒烟：`.venv\Scripts\python tools\device_smoke.py [--serial X] [--mobilelog]`。
- 打包：`build.bat`（PyInstaller 单文件，产物 `dist\adbperfetto.exe`）。

## 已验证的设备事实（KN8, Perfetto v49）

- `perfetto --detach=K` 要求配置里 `write_into_file: true`。
- `perfetto --is_detached=K` rc 0 = 存活，rc 2 = 不存在/已结束；stderr 的 "Warning: No PTY" 无害。
- perfetto v49 没有 `--cancel` 标志。
- 配置文件只能放 `/data/misc/perfetto-configs/`（SELinux 不允许读 /data/local/tmp）；trace 输出到 `/data/misc/perfetto-traces/`。
- flight 模式：`perfetto --txt -c <cfg> -o <base> --background-wait` 在 stdout 打印 pid；`perfetto --clone-by-name <name> -o <file>` 同步写快照；`trigger_perfetto <name>` 异步生成 `<base>.0/.1`；`kill -TERM <pid>` 结束会话。
- MobileLog：shell 无权 rm mobilelog 文件，需用 DebugLoggerUI 的 `cmd_name clear_all_logs` 清理；状态看 `getprop vendor.MB.running` / `vendor.MB.realpath`；`cmd_target` 位掩码 1=Mobile、2=Modem、4=Network、16=GPSHost；Modem/Network/GPSHost 没有状态属性，只能解析 DebugLoggerUI 通知的 `android.bigText`。
