# 开发规则

- 与用户沟通使用中文；工具运行时的所有终端输出、提示信息、API 错误信息、文件名使用英文。
- 本仓库即工具源码目录，使用仓库下的 Python 虚拟环境 `.venv`（基于 Python 3.13）。
- 使用系统 PATH 中配置好的 adb，不在 EXE 中打包 adb。
- 运行：`.venv\Scripts\python -m adbperfetto`（仓库根目录）。
- 测试：`.venv\Scripts\python -m unittest`（仓库根目录）。
- 真机冒烟：`.venv\Scripts\python tools\device_smoke.py [--serial X] [--mobilelog]`。
- 真机端到端：`.venv\Scripts\python tools\device_e2e.py`（录制+MobileLog+断连、杀进程恢复、时长结束、飞行快照、orphan 接管）。
- 演示模式（无需手机，模拟设备）：`set ADBPERFETTO_DEMO=1` 后运行；UI 冒烟 `tools\ui_smoke.py`，截图 `tools\ui_screenshots.py`（Playwright 使用本机 Edge，`channel="msedge"`，不要 `playwright install`）。
- 打包：`build.bat`（PyInstaller 单文件，产物 `dist\adbperfetto-<版本号>.exe`，如 `adbperfetto-2.0.42.exe`；工作区有未提交改动时为 `adbperfetto-<版本号>-dirty.exe`，发布前先提交）。EXE 为无控制台窗口 + 系统托盘图标（菜单 Open / Quit），日志写 `adbperfetto_data\adbperfetto.log`；`--tray/--no-tray`（默认仅打包 EXE 开启）；`tools\make_icon.py` 重新生成 `adbperfetto\assets\adbperfetto.ico`。
- 版本号：`__init__.py` 的 `VERSION_BASE` + git 提交数自动生成（如 2.0.42），BUILD 为短 hash（脏目录加 `-dirty`）；build.bat 先运行 `tools\write_buildinfo.py` 把值写进 `adbperfetto/_buildinfo.py` 再打包（随后删除），EXE 运行时不再调用 git；minor/major 版本手动提升 VERSION_BASE。
- UI 设计约束：颜色即状态码（cyan=用户输入/选中，live 绿=录制/连接，amber=警示/断开，red=错误，orange=每屏唯一主按钮），等宽字体仅用于编辑器和文件路径。

## 已验证的设备事实（KN8, Perfetto v49）

- `perfetto --detach=K` 要求配置里 `write_into_file: true`。
- `perfetto --is_detached=K` rc 0 = 存活，rc 2 = 不存在/已结束；stderr 的 "Warning: No PTY" 无害。
- perfetto v49 没有 `--cancel` 标志。
- 配置文件只能放 `/data/misc/perfetto-configs/`（SELinux 不允许读 /data/local/tmp）；trace 输出到 `/data/misc/perfetto-traces/`。
- flight 模式：`perfetto --txt -c <cfg> -o <base> --background-wait` 在 stdout 打印 pid；`perfetto --clone-by-name <name> -o <file>` 同步写快照；`trigger_perfetto <name>` 异步生成 `<base>.0/.1`；`kill -TERM <pid>` 结束会话。
- `kill -0 <pid>` 对 perfetto 进程返回 EPERM（SELinux），存活判断用 `grep -q adbperfetto_flight /proc/<pid>/cmdline`。
- 测试断连时单纯 `adb kill-server` 维持不住断开（下一条 adb 命令会自动重启 daemon），device_e2e 通过占用 5037 端口模拟 PC 侧失联。
- MobileLog：shell 无权 rm mobilelog 文件，需用 DebugLoggerUI 的 `cmd_name clear_all_logs` 清理；状态看 `getprop vendor.MB.running` / `vendor.MB.realpath`；`cmd_target` 位掩码 1=Mobile、2=Modem、4=Network、16=GPSHost；Modem/Network/GPSHost 没有状态属性，只能解析 DebugLoggerUI 通知的 `android.bigText`。
