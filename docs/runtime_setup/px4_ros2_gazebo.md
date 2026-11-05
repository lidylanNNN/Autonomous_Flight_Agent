# PX4 ROS2 Gazebo Runtime 配置

## 目标技术栈

项目使用以下 M1 Runtime 技术栈：

```text
Ubuntu 24.04 LTS
ROS 2 Jazzy
Gazebo Harmonic
PX4 v1.16.2 (54f0455ffcd755534539a7cf33a09a20bf71d29d)
uXRCE-DDS
x500 multicopter SITL
```

## 选择原因

本地主机为 Ubuntu 24.04。PX4 ROS 2 Guide 推荐该系统使用 ROS 2 Jazzy，并在
这条仿真路线中使用 Gazebo Harmonic。Gazebo Harmonic 同时为 Ubuntu Noble 提供
Binary Package。

只有当 M1 出现使用 Container 更容易解决的阻塞时，才考虑回退到 Ubuntu 22.04 /
ROS 2 Humble。

## 健康检查

执行：

```bash
python scripts/ros2_px4/check_runtime_health.py
```

该脚本检查：

```text
Ubuntu 版本
Python 版本
uv
ROS 2 命令是否可用
Gazebo 命令是否可用
MicroXRCEAgent 是否可用
PX4 Source Checkout
ROS 2 Jazzy Setup Path
```

## Runtime 冒烟测试

首次构建 Workspace 后，在一个终端启动 Runtime：

```bash
cd ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install --packages-up-to px4_msgs px4_msgs_old translation_node
cd ..
scripts/ros2_px4/start_simulation.sh
```

在另一个终端检查 Topic：

```bash
scripts/ros2_px4/smoke_test_topics.sh
```

在单独终端启动 M2 WorldState Node：

```bash
scripts/ros2_px4/run_px4_world_state_adapter.sh
```

Launcher 会把 uv 管理的 Pydantic 2 环境暴露给 ROS 2 System Python。Ubuntu
Noble 的 `python3-pydantic` 是 Pydantic 1，因此不用于共享 `WorldState` Contract。

从 JSONL Trace 回放最新 WorldState：

```bash
scripts/tracing/replay_trace.sh artifacts/world_state.jsonl
```

可使用 `--record 10`、`--state-id state-123-4` 或
`--at 2026-09-17T08:44:08+00:00` 定位历史快照。记录序号从 1 开始。无效 JSON、
未知记录类型、无效 Contract 或 Timestamp 倒序都会报告源文件行号。

## 安装顺序

1. 安装 ROS 2 Jazzy。
2. Clone PX4-Autopilot v1.16.2 及递归 Submodule。
3. 安装 PX4 开发依赖和 Gazebo Harmonic。
4. 安装 ROS/Gazebo Bridge Package。
5. 构建并运行 `make px4_sitl gz_x500`。
6. 使用 `scripts/ros2_px4/start_simulation.sh` 启动 uXRCE-DDS Agent 和 PX4。
7. 运行 `scripts/ros2_px4/smoke_test_topics.sh`。

## 成功标准

M1 完成时，新终端必须能够：

```text
使用 Gazebo x500 启动 PX4 SITL
启动或确认 uXRCE-DDS Transport
列出 PX4 暴露给 ROS 2 的 Topic
执行可重复的健康检查
记录准确的 PX4 Commit 和 Environment Manifest
```

## 本机验证

当前 M1 主机已通过：

```bash
make px4_sitl
timeout 25s env HEADLESS=1 make px4_sitl gz_x500
```

有界冒烟测试能进入已就绪的 Gazebo World、生成 `x500_0`、成功启动 PX4，并在
UDP 8888 初始化 uXRCE-DDS Client。Timeout 退出是有意设计，用于避免 Simulator
在测试后继续驻留。

## 参考资料

- PX4 ROS 2 User Guide：https://docs.px4.io/main/en/ros2/user_guide
- PX4 uXRCE-DDS Guide：https://docs.px4.io/main/en/middleware/uxrce_dds
- Gazebo Harmonic Ubuntu Install：https://gazebosim.org/docs/harmonic/install_ubuntu/

## M4 实际服务拒绝记录

2026-11-04，代码 `0fd8642`（v0.10.67），PX4 与本文冻结版本一致。本轮启动实际 `flight_atomic_action_server.main()`，保留真实后端、状态订阅、命令适配器、Gate 和 Trace；没有使用模拟对象。

- 场景：地面未解锁，真实位置和 Home 有效，failsafe 未激活。系统高度上限 5 米，请求 Takeoff 高度 6 米。
- 限制文件：从仓库示例收紧高度、半径及围栏，仅用于本轮拒绝验证，不作为批准飞行配置。
- 结果：25.2 毫秒返回拒绝，唯一原因 `ALTITUDE_ABOVE_ENVELOPE`，无执行 ID；提案和决策 ID 与落盘 Trace 一致。
- 后端观察：使用 Python 的函数调用观察功能，实际 `get_world_state()` 进入 2 次，`execute()` 进入 0 次；不替换对象或函数。观察代码在 server 启动前启用，覆盖工作线程。
- 命令观察：在请求前完成 DDS 发现，确认实际适配器发布者存在；请求前后监听 `/fmu/in/vehicle_command`、`/fmu/in/offboard_control_mode`、`/fmu/in/trajectory_setpoint`，三个 Topic 均无消息。
- 独立读取 PX4 `MPC_XY_VEL_MAX=12.0`，当前系统上限为 8.0。本轮高度检查已拒绝，未进入 Gate 限速读取；后续批准飞行前必须处理限速不匹配，不能为放行而调高系统上限。
- 本轮结束后已停止启动的 server、PX4、Gazebo 和 XRCE Agent，未起飞。

本地原始证据目录为 `artifacts/m4-7e/20261104T102055Z/`：`manifest.json` 保存环境和版本，`safety.json` 保存限制，`request.json` 和 `result.json` 保存请求结果，`backend_calls.json` 保存函数进入次数，`publishers.json` 和 `command_messages.json` 保存命令观察，`safety.jsonl`、`world.jsonl` 及进程日志保存原始记录。`run.py` 保存本轮运行程序，`sha256.json` 保存这些文件的校验值。artifacts 按仓库规则不进入 Git。

复现需加载 Jazzy、工作区生成接口、四个 ROS 包的源码及项目 Python 依赖，在仓库根目录执行上述归档的 `run.py`；程序调用现有 `start_simulation.sh`，独立启动 server、发送一次请求并清理进程。首次启动触发了 PX4 增量编译；本轮使用无输入终端运行时 PX4 提示符大量刷新，原始日志已保留，后续启动应使用保持打开的终端输入。

这条记录证明真实状态读取和本场景的拒绝零派发，不证明真实后端的批准执行或物理动作正确；后者按 M4 的 7f 继续验证。

## 用 pytest 重复运行真实拒绝场景

用例：`ros2_ws/src/flight_agent_ros/test/test_flight_sitl.py`。它直接启动真实 PX4/Gazebo 和 server，通过 ROS 请求起飞 6 米，并检查系统 5 米上限导致拒绝、真实后端未执行、三个命令 Topic 没有消息。没有替换后端或适配器。

普通 pytest 默认排除 `sitl`。需要运行时，在仓库根目录执行：

```bash
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
export PYTHONPATH="$PWD/ros2_ws/src/flight_agent_ros:$PWD/src:$PWD/.venv/lib/python3.12/site-packages:$PYTHONPATH"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /usr/bin/python3 -m pytest -q -s -m sitl ros2_ws/src/flight_agent_ros/test/test_flight_sitl.py
```

测试会打印本轮 `artifacts/m4-7e/pytest-时间/` 路径，保存环境、代码及测试文件校验值、请求、响应、状态、安全决策、后端调用次数和命令记录。退出时清理本轮启动的进程，失败时也保留证据。运行前应停止其他 PX4 或同名服务，避免共享环境干扰；本用例只负责自己启动的进程。

当前 pytest 用例的测试辅助变量和自写记录文件使用 `test_temp_` 前缀，例如 `test_temp_messages`、`test_temp_backend_calls.json`、`test_temp_response.json` 和 `test_temp_server.log`；本轮输入配置为 `test_temp_safety.json`。真实 server、后端和 ROS 对象保持原名，正式 Trace 仍为 `world.jsonl` 与 `safety.jsonl`。前面列出的历史证据按其运行时版本保留原文件名。
