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

用例：`ros2_ws/src/flight_agent_ros/test/test_flight_sitl.py`。单一 `test_action_sitl` 使用 `pytest.mark.parametrize` 配置场景；7e 请求起飞 6 米并验证系统 5 米上限导致拒绝、后端零执行及三个命令 Topic 零派发，7f 请求起飞 3 米后降落。两组均启动真实 PX4/Gazebo 和实际 server，不替换后端或适配器。

普通 pytest 默认排除 `sitl`。需要运行时，在仓库根目录执行：

```bash
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
export PYTHONPATH="$PWD/ros2_ws/src/flight_agent_ros:$PWD/src:$PWD/.venv/lib/python3.12/site-packages:$PYTHONPATH"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /usr/bin/python3 -m pytest -q -s -m sitl ros2_ws/src/flight_agent_ros/test/test_flight_sitl.py
```

可追加 `-k 7e` 或 `-k 7f` 选择参数项。新增同类场景只需增加一对参数：`test_input_case` 保存输入配置，`expected_output_case` 保存预期结果；逐步对应 `test_input_step`、`expected_output_step`。配置中，`initial_state` 指定初始状态，`px4` 指定临时限速/心跳，输入侧 `steps` 指定服务、输入、等待条件和超时，预期侧 `steps` 指定预期响应/状态和命令，预期侧 `topic_counts` 指定 Topic 计数，`safety` 可覆盖本轮限制。字段条件支持精确值、点分路径及 `{min, max}` 闭区间；起降属于同一参数项的两步序列。

测试会打印本轮 `artifacts/m4-7e/pytest-时间/` 或 `artifacts/m4-7f/pytest-时间/` 路径，保存环境、代码及测试文件校验值、请求、响应、状态、安全决策、后端调用次数和命令记录。退出时清理本轮启动的进程，失败时也保留证据。运行前应停止其他 PX4 或同名服务，避免共享环境干扰；本用例只负责自己启动的进程。

当前命名规则见 [AGENT.md](../../AGENT.md#测试覆盖与证据核对)。业务返回值保留 `success`，实际 server 加载的配置使用 `safety.json`，正式 Trace 为 `world.jsonl` 与 `safety.jsonl`；业务代码中不存在的对象及其记录文件，如 `test_temp_counts`、`test_temp_messages`、`test_temp_backend_calls.json`，使用 `test_temp_` 前缀。历史证据保持运行时文件名，不追溯改名。


## M4 实际服务起降记录

2026-11-05，基于 `22c1cd3`（v0.10.69）及本轮未提交测试改动，运行单一参数化 `test_action_sitl[7f-takeoff-land]`。环境仍为冻结 PX4 `54f0455`、ROS 2 Jazzy、Gazebo x500；各轮 manifest 保存实际版本、完整用例配置、工作树差异和测试文件校验值。

本轮系统上限为高度 5 米、水平速度 8 m/s、半径 10 米，矩形围栏为 ±10 米且保留 2 米边距。首次完整起降将 PX4 `MPC_XY_VEL_MAX` 从 12 降为 8 m/s，结束回读确认恢复 12；最终参数化轮次开始时读到 8，运行及恢复回读均为 8。每轮均通过真实 MAVLink 独立读数，不把先前记录的 12 当作后续轮次现值。MAVLink Onboard 本地端口为 14580、远端为 14540，心跳临时由 1 Hz 提高至 10 Hz，退出时恢复 1 Hz，避免限速读取等待心跳消耗状态有效期；系统状态有效期仍为 1000 ms。

生产调用范围为 `flight_atomic_action_server.main()` 装配的实际 ROS 服务、Gate、Supervisor、真实 Backend、PX4 命令适配器及 PX4/Gazebo。测试仅订阅命令/ACK、读取正式 Trace，并以 profile 观察真实后端；没有替身或直接后端调用。两次读取之间不写观察计数文件，第二次读取返回后才保存计数；进入执行函数时额外记录当时已落盘的决策 ID，不改变函数结果。

证据索引：

- 首次完整起降：`artifacts/m4-7f/pytest-20261105T122739279755Z/`。
- 单一参数化函数最终验证：7e 为 `artifacts/m4-7e/pytest-20261105T124035868417Z/`，7f 为 `artifacts/m4-7f/pytest-20261105T124058175893Z/`。
- 失败证据保留：`122545512673Z` 的起飞因 `STALE_STATE` 拒绝；`122700161402Z` 使用错误的心跳本地端口，尚未请求动作。参数化改造初跑的 7e `123851911790Z`、7f `123912176807Z` 均因 `STATE_ID_MISMATCH` 未满足预期且零执行；这反映两次状态读取可跨更新的时序风险，不计为通过。

每轮 `test_temp_*request.json`、`test_temp_*response.json` 保存请求、输出、状态及耗时；`safety.jsonl` 关联提案、批准、执行 ID 和结果；`test_temp_execution_entries.json` 证明进入后端时决策已记录；`test_temp_command_messages.json` 保存完整命令与 ACK。最终轮 Takeoff 响应耗时 8.99 秒、NED z=-2.520 米且已解锁离地；Land 响应耗时 8.32 秒、NED z=0.060 米且已着陆上锁。`world.jsonl` 保留状态变化，退出状态、参数恢复和校验值分别由对应测试记录保存。后端 `execute_calls` 是 profile call 事件数，包含协程恢复；实际执行数量由去重的 execution ID 核对，本轮为 2 次。服务继续返回 `verification=INCONCLUSIVE`；这些证据只证明指定受控 SITL 的动作链，不代表真实 LLM、M6 独立验证、M7-S 或实机能力。


### 2026-11-06 命名修正后复跑

当前 `success`、`safety.json` 命名版本已完成真实复跑：7e 证据为 `artifacts/m4-7e/pytest-20261106T014309644519Z/`，7f 为 `artifacts/m4-7f/pytest-20261106T014332930290Z/`，pytest 为 `2 passed in 68.26s`。7e 命令数为 0，7f 为 3，最终均已着陆上锁；实际入口加载 `safety.json`。测试源码校验值与提交前版本一致，各轮文件校验值已复核，参数已恢复至本轮原值且无仿真进程残留。
