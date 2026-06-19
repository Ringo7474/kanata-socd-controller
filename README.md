# 方向键急停

一套基于 [Kanata](https://github.com/jtroo/kanata) 的 Windows 方向键冲突处理工具。普通机械键盘也可以获得清晰、可调的 SOCD 后按优先逻辑，无需磁轴键盘或 DKS 行程配置。

> 本项目提供图形控制器和 Kanata 配置，不包含 Kanata、Interception 等第三方二进制文件。

![方向键急停界面](docs/ui.png)

## 界面说明

1. **急停功能**：总开关。关闭后 `W/S`、`A/D` 恢复原始直通，不处理方向冲突。
2. **补偿模式**：“触发一次”只补一个反向按键事件；“固定时长”会按设定毫秒数保持反向补偿。
3. **反向补偿时长**：选择固定时长后，可直接输入或拖动滑杆，在 `1-200 ms` 之间调整。
4. **生效方向**：`W/S` 和 `A/D` 可以分别开启，未勾选的方向保持直通。
5. **当前配置**：实时显示即将写入的 `kanata.kbd`，右上角显示配置校验结果。
6. **运行控制**：“保存配置”只写入文件；“保存并启动”会校验配置并在后台重启 Kanata；“停止”会结束 Kanata。
7. **运行状态**：右上角显示 Kanata 当前是“运行中”还是“未运行”。

## SOCD 是什么

SOCD（Simultaneous Opposite Cardinal Directions）指同时输入两个相反方向，例如 `A + D` 或 `W + S`。

未开启处理时：

```text
按住 A -> 再按住 D -> 游戏同时收到 A + D -> 人物停止
```

开启本工具后：

```text
按住 A -> 再按住 D -> 后按的 D 接管 -> 人物向右移动
松开 D，但 A 仍按住 -> 自动切回 A -> 人物向左移动
```

核心行为是：**后按优先、松键回切、避免相反方向同时输出。**

## 功能

- 支持 `W/S`、`A/D` 两组方向独立启用。
- SOCD 开关关闭后，方向键恢复原始直通。
- 支持“触发一次”和“固定时长”两种反向补偿模式。
- 固定时长可在 `1-200 ms` 之间调整。
- 保存前自动调用 Kanata 检查配置语法。
- 保存并启动、停止操作均在后台完成，不弹出命令行窗口。
- 自动识别 Kanata 是否正在运行。

## 为什么不直接使用 DKS

DKS 依赖磁轴行程，可以在一次按键中绑定多段按下和释放动作，但复杂设置容易出现相反方向同时输出，或某次释放信号没有被正确处理。游戏收到 `A + D` 时，人物可能原地停止；释放信号丢失时，也可能持续向一个方向移动。

本项目直接维护物理按键状态和输出状态：新方向按下时先释放旧方向，松开当前方向时再检查另一枚物理按键是否仍被按住。状态分支更明确，也能用于普通机械键盘。

## 使用方法

1. 从 [Kanata Releases](https://github.com/jtroo/kanata/releases) 下载 Windows `wintercept` 版本。
2. 按 Kanata 官方文档安装 Interception 驱动。
3. 将以下文件放在同一个目录：
   - `SOCD_Controller.exe`
   - `kanata.kbd`
   - `kanata_windows_gui_wintercept_x64.exe`
   - `interception.dll`
4. 打开 `SOCD_Controller.exe`。
5. 选择补偿模式和生效方向，点击“保存并启动”。

程序保存配置时会先生成 `kanata.kbd.bak` 备份。

## 从源码构建

需要 Windows 和 Python 3.12。PowerShell 中运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_gui.ps1
```

脚本会创建本地虚拟环境、安装 PySide6 和 PyInstaller、执行配置自测，然后生成 `SOCD_Controller.exe`。

## 项目文件

- `socd_controller.py`：PySide6 图形控制器和配置生成逻辑。
- `kanata.kbd`：可直接使用的 Kanata 配置。
- `build_gui.ps1`：Windows 一键构建脚本。
- `requirements-gui.txt`：构建依赖。
- `start_wintercept_admin.cmd`：手动管理员启动备用脚本。
- `stop_wintercept_admin.cmd`：手动停止备用脚本。

## 注意事项

- 这是输入重映射工具，不会让普通机械键盘获得磁轴的真实行程感知能力。
- 不同游戏对输入重映射、宏和辅助功能的规则不同，使用前请确认对应游戏规则。
- 强反作弊竞技游戏不建议启用；进入此类游戏前请停止 Kanata。
- Kanata、Interception 及其 DLL 请始终从官方渠道获取。
