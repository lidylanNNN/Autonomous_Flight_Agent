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

用例：`ros2_ws/src/flight_agent_ros/test/test_flight_sitl.py`。单一 `test_action_sitl` 使用 `pytest.mark.parametrize` 配置场景；7e 请求起飞 6 米并验证系统 5 米上限导致拒绝、后端零执行及三个命令 Topic 零派发，7f 请求起飞 3 米后降落，7g-1 起飞后等待稳定、悬停 5 秒再降落。7g-2 起飞后移动到北 3 米、东 2 米、高 3 米再降落。四组均启动真实 PX4/Gazebo 和实际 server，不替换后端或适配器。

普通 pytest 默认排除 `sitl`。需要运行时，在仓库根目录执行：

```bash
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
export PYTHONPATH="$PWD/ros2_ws/src/flight_agent_ros:$PWD/src:$PWD/.venv/lib/python3.12/site-packages:$PYTHONPATH"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /usr/bin/python3 -m pytest -q -s -m sitl ros2_ws/src/flight_agent_ros/test/test_flight_sitl.py
```

可追加 `-k 7e`、`-k 7f`、`-k 7g-1` 或 `-k 7g-2` 选择参数项。新增同类场景只需增加一对参数：`test_input_case` 保存输入配置，`expected_output_case` 保存预期结果；逐步对应 `test_input_step`、`expected_output_step`。配置中，`initial_state` 指定初始状态，`px4` 指定临时限速/心跳，输入侧 `steps` 指定服务、输入、等待条件和超时，预期侧 `steps` 指定预期响应/状态和命令，预期侧 `topic_counts` 指定 Topic 计数，`safety` 可覆盖本轮限制。字段条件支持精确值、点分路径及 `{min, max}` 闭区间；起降属于同一参数项的两步序列。

测试会打印本轮 `artifacts/m4-7e/pytest-时间/`、`artifacts/m4-7f/pytest-时间/`、`artifacts/m4-7g-1/pytest-时间/` 或 `artifacts/m4-7g-2/pytest-时间/` 路径，保存环境、代码及测试文件校验值、请求、响应、状态、安全决策、后端调用次数和命令记录。退出时清理本轮启动的进程，失败时也保留证据。运行前应停止其他 PX4 或同名服务，避免共享环境干扰；本用例只负责自己启动的进程。

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


## M4 实际服务悬停记录

2026-11-06，代码 `50839df`（v0.10.71）加本批测试改动，在冻结 PX4 `54f0455`、ROS 2 Jazzy、Gazebo x500 上运行 `test_action_sitl[7g-1-takeoff-hold-land]`。最终证据为 `artifacts/m4-7g-1/pytest-20261106T083108497753Z/`，结果 `1 passed, 2 deselected in 52.54s`。manifest 保存实际生产模块版本、用例、工作树差异与测试源码校验值；模块加载路径由后端观察记录保存。

场景从位置和 Home 有效、地面未解锁开始，经 `/takeoff` 请求 3 米。Hold 前等待 `AUTO_LOITER`、已解锁离地、高度 2.8～3.2 米、三个方向速度各在 ±0.1 m/s 内且综合状态年龄小于 200 ms，然后经 `/hold` 请求 `duration_s=5.0`，最后经 `/land` 落地上锁。系统限制及 PX4 限速/心跳处理复用[起降记录](#m4-实际服务起降记录)，本轮 `MPC_XY_VEL_MAX` 设置前、运行中及恢复后均独立回读为 8 m/s；退出时恢复 1 Hz 心跳。

三个动作均为 `APPROVE`、`CHECKS_PASSED`、`SUCCEEDED`，提案、决策、执行 ID 与 Trace 一致；后端进入执行前对应决策已落盘。共观察到命令 `22`、`400`、`176`、`21` 及接受 ACK；Hold 的 `176` 参数为 `param1=1, param2=6`。实际状态读取 6 次，去重执行 ID 为 3 个；profile 的 execute call 事件包含协程恢复，不作为动作次数。

Hold 目标 NED 为 `(0.073888, -0.035925, -2.881249)` 米，实际执行耗时 6.682 秒（包括 Offboard 准备），ROS 响应耗时保存于 `test_temp_hold_response.json`。本轮证据检查使用执行结束前的完整 5 秒窗口，水平及垂直误差各限 0.5 米，原始位置接收与源采样时间均严格递增、间隔各不超过 0.5 秒；独立收到 501 个位置采样，最大接收间隔 12.592 ms，最大水平误差 0.035674 米、垂直误差 0.028211 米。正式 WorldState Trace 中该窗口的记录均为 `OFFBOARD`、离地已解锁、无 failsafe 且位置新鲜；命令观察同时确认位置控制心跳与固定位置设定值。

`test_temp_hold_evidence.json` 保存窗口、目标和误差汇总，`test_temp_command_messages.json` 保存客户端收到的原始位置、命令、心跳、设定值与 ACK；请求/响应及正式 Trace 沿用前述证据命名。所有文件校验值已复核，测试源码与运行 manifest 一致。最终已着陆上锁；本轮创建的 server、PX4/Gazebo 和 XRCE Agent 已退出。

本批失败轮次保留在同一 `artifacts/m4-7g-1/` 下，不计通过：

- `pytest-20261106T082516270052Z`：起飞因 `STALE_STATE` 拒绝，未派发飞行命令。
- `pytest-20261106T082605929107Z`：Hold 执行成功，但证据检查错误使用综合状态年龄；一条年龄 1102 ms 的记录中位置仍新鲜，低频着陆状态过期。后续改为检查位置自身新鲜标志；生产 Gate 未变。
- `pytest-20261106T082719758670Z`、`pytest-20261106T082910391763Z`：Hold 执行成功，但 server 的 WorldState Trace 出现 0.885 秒、0.731 秒采样间隔，未满足 0.5 秒证据要求。最终补充独立客户端对真实 `VehicleLocalPosition` 的订阅，保持原间隔上限检查位置，不以 server 的 Trace 定时器充当原始位置采样。以上三轮在检查失败后停止仿真，未执行后续 Land；不计完整场景通过。

这些记录暴露了状态聚合与 Trace 调度的时序限制；原始位置证据不代表在途安全监测已经实现。通过范围仅为受控 SITL 下的实际动作服务链与采样位置验证；`verification` 仍为 `INCONCLUSIVE`，未运行 LLM/Agent 主循环，也未覆盖 GoTo、RTL、实机或采样间隔内的连续轨迹。复现使用上文 pytest 命令加 `-k 7g-1`。


## M4 实际服务移动记录

2026-11-06，代码 `803b501`（v0.10.72）加本批测试改动，在冻结 PX4 `54f0455`、ROS 2 Jazzy、Gazebo x500 上运行 `test_action_sitl[7g-2-takeoff-goto-land]`，首轮 `1 passed, 3 deselected in 53.44s`。证据目录为 `artifacts/m4-7g-2/pytest-20261106T091718181148Z/`。manifest 保存场景、预期、源码校验值及工作树差异摘要；实际加载的模块路径见后端观察记录。

本场景复用[悬停场景](#m4-实际服务悬停记录)的起飞和稳定状态等待条件，经 `/goto` 请求 `north_m=3.0, east_m=2.0, altitude_m=3.0, acceptance_radius_m=0.5`，随后经 `/land` 落地上锁。安全配置、临时限速和心跳处理复用[起降记录](#m4-实际服务起降记录)；本轮 PX4 限速设置前、运行中及恢复后均独立回读为 8 m/s，退出恢复 1 Hz 心跳。V1 入口沿用操作员已授权的受控 SITL 假设。

三个动作均由实际 server main 装配，通过 Gate、Supervisor、真实 Backend、PX4 适配器执行。Trace 中提案、决策和执行 ID 一致，决策在进入后端及发送命令前已落盘。PX4 命令按步骤为 Takeoff `22, 400`、GoTo `176`、Land `21`，均收到接受 ACK；最终离地状态为 false、解锁状态为 false。本轮 server 和仿真进程均已退出。

独立客户端采样确认 GoTo 执行期间所有收到的位置设定值均为 NED `(3, 2, -3)` 米，执行耗时 3.082 秒。309 个原始位置采样从 `(0.024188, -0.053816, -2.847260)` 移动到 `(2.772475, 1.829789, -2.975431)` 米，水平位移 3.331824 米，终点水平误差 0.284147 米、垂直误差 0.024569 米，均满足各 0.5 米门槛。位置有效，接收时间及源采样时间递增且间隔均不超过 0.5 秒；最大接收间隔 12.519 ms，首尾采样距执行边界不超过 0.5 秒。

`test_temp_goto_evidence.json` 保存目标、起终点、位移、误差和采样汇总；原始消息、请求响应与 Trace 沿用既有记录方式。所有文件校验值及测试源码与 manifest 的一致性已核对。临时副本的错误目标、未到达与采样缺失均触发检查失败，原始证据通过；未修改正式证据。

GoTo 返回时的 WorldState Trace 综合年龄为 1205 ms，低频着陆状态不新鲜，但位置自身新鲜。终点检查使用独立客户端在执行结束前收到的原始位置，未把响应中较早的聚合位置当作终点；后续 Land 等待新的状态年龄小于 200 ms 后再请求。该现象不改变 Gate 条件，也不证明在途安全监测已实现。

通过范围仅为本场景实际动作服务链、目标映射及采样位置验收；服务 `verification` 仍为 `INCONCLUSIVE`，未运行 LLM/Agent 主循环，不覆盖 RTL、M6、M7-S、实机或采样间隔内连续轨迹。复现使用上文 pytest 命令加 `-k 7g-2`。
