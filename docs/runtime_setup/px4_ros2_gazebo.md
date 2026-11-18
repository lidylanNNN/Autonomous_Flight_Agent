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

默认启动 `x500`；选择冻结版本已有的前视单目机型时，在上述启动位置运行：

```bash
scripts/ros2_px4/start_simulation.sh --model x500_mono_cam
```

`--model` 只接受 `x500` 和 `x500_mono_cam`，非法参数在创建进程前以退出码 2 失败；`--help` 显示用法。两种机型均沿用 `HEADLESS=1` 和既有仿真参数配置。相机机型启动不代表已取得有效图像，图像接入按 [M6 T02](../../plans/M6.md#51-环境出图第-1-日6-小时)验证。

恢复默认机型时，先在原终端按 Ctrl-C 停止本轮，再无参数运行脚本。每次只启动一套仿真，避免复用上一轮世界或机型。启动验证仅观察地面就绪状态，不发送解锁或飞行命令。

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

## M6 单人物场景

场景配置、预定观察点、资源版本/许可和哈希以 [configs/m6](../../configs/m6/README.md) 为准。开始前停止上一轮仿真，避免沿用旧世界。终端一在仓库根目录启动：

```bash
GZ_SIM_RESOURCE_PATH="$PWD/configs/m6/models${GZ_SIM_RESOURCE_PATH:+:$GZ_SIM_RESOURCE_PATH}" \
  scripts/ros2_px4/start_simulation.sh --model x500_mono_cam
```

等待实际 Gazebo 世界及 PX4 就绪，保持飞机在地面；终端二在仓库根目录选择场景：

```bash
scripts/ros2_px4/configure_m6_scene.sh target
# 切换到无目标配置；需要恢复时再次运行 target
scripts/ros2_px4/configure_m6_scene.sh empty
```

配置脚本先检查本地资源哈希，再查询世界、移除同名旧目标，按参数决定是否创建人物。脚本只报告服务接受请求，实际实体以世界查询为准：

```bash
gz service -s /world/default/scene/info \
  --reqtype gz.msgs.Empty --reptype gz.msgs.Scene --timeout 5000 --req ''
```

`target` 应只有一个 `m6_person`，姿态与场景 SDF 一致；`empty` 不应有该实体或带后缀的副本。完整重置时停止终端一的仿真，再执行上述启动和配置步骤；不在运行中的飞机上重置世界/时钟。资源校验失败时先恢复原始文件，不绕过校验。实体查询只验证场景装载；图片与检测见下文 T01c，ROS 桥接见 T02a。

## M6 T01c 检测工具试跑

工具版本、许可、资源测量与通过范围以 [T01c 记录](../../plans/M6.md#t01c-原生图片与检测工具试跑2026-11-17) 为准。使用系统 `/usr/bin/python3`，需要已有 `python3-opencv`、NumPy、Gazebo transport13/msgs10 Python 绑定；不要在项目 uv 环境直接运行。完整重新采集时先停止上一轮仿真，在仓库根目录执行有界验证脚本（约一分钟上限，退出清理本轮进程）：

```bash
/usr/bin/python3 artifacts/m6_t01c/test_temp_run.py
```

脚本通过实际启动入口运行 `x500_mono_cam`，配置 `target`，从列出的唯一相机 `/image` topic 取得当前帧，保存到新时间戳目录并调用 HOG。需要宿主机仿真/图形设备及 PX4 运行目录写权限。脚本仅作 T01c 验证，不是生产相机服务或 ROS 桥。

只复跑检测时可消费已保存的真实原图；这不会重新采集，不算新场景运行。输出路径由操作员指定，避免覆盖归档结果：

```bash
/usr/bin/time -v /usr/bin/python3 artifacts/m6_t01c/test_temp_detect.py \
  artifacts/m6_t01c/20261117T085203Z/camera.png \
  /tmp/test_temp_m6_detection.json
```

该命令固定 CPU 单 OpenCV 线程，不接收场景坐标或真值；JSON 包含输入图片哈希、检测框、SVM score、耗时和检测进程峰值 RSS。空列表表示成功但未检出，图片不可读则非零退出；有输出文件不自动代表检出人物。归档原图、原始消息及结果位于上述证据目录，版本和校验值见 `manifest.json`。

## M6 T02a ROS 图像桥接

在前述 M6 相机仿真与 `target` 场景就绪后，另开终端，在仓库根目录运行：

```bash
scripts/ros2_px4/start_camera_bridge.sh
```

入口只支持 `default` 世界和 `x500_mono_cam_0` 实例，不接受配置参数；`--help` 查看用法。运行依赖为系统 ROS Jazzy 的 `ros_gz_bridge`、`sensor_msgs`、`rosgraph_msgs`，脚本加载 `/opt/ros/jazzy/setup.bash` 并逐项检查。验证版本、影响与回滚见 [T02a 记录](../../plans/M6.md#t02a-恢复实施图像桥接2026-11-17)。

映射与 QoS 以 [camera_bridge.yaml](../../scripts/ros2_px4/camera_bridge.yaml) 为唯一配置来源：图像及参数使用 `SENSOR_DATA`，时钟使用 `CLOCK`；三个映射均为 `GZ_TO_ROS`。不启动第二个桥接或其他 `/clock` 发布者；操作员退出桥接终端即停止转发，仿真需在原终端另行停止。启动日志只表示桥接创建，必须检查实际消息：

```bash
source /opt/ros/jazzy/setup.bash
ros2 topic echo /camera/camera_info --once --qos-reliability best_effort
ros2 topic echo /clock --once --qos-reliability best_effort
ros2 topic echo /camera/image_raw --field header --once --qos-reliability best_effort
```

这些命令用于观察消息，不能代替本批原始像素、参数和时间对应验证。实际采集方须使用兼容的 best-effort 订阅 QoS；需使用仿真时间的 ROS 节点另行设置 `use_sim_time=true`，启动桥接不会自动修改其他节点的时间源。源时间戳保留、frame_id 按桥接库标准转换，具体边界见上述 T02a 记录；不提供 NED/ENU 图像定位。

完整地面验证可运行 `/usr/bin/python3 artifacts/m6_t02a/test_temp_run.py`；它从实际脚本启动新仿真、场景与桥接，调用 pytest 读取原生/ROS 消息，结束后清理本轮进程。先停止其他仿真/桥接；除上述运行依赖外，验证器需要系统 pytest、Pillow 和 Gazebo Python 绑定。它不安装依赖、不飞行、不调用 Agent。采集有效性约束见 [T02b 契约](../../contracts/rosllm_services.md#m6-t02b-采集有效性判据)，判据边界及真实暂停验证见 [T02b 记录](../../plans/M6.md#t02b-采集有效性判据2026-11-18)；正式采集服务与缓存保护仍待 T03b 接入。

## M6 T03c Agent 拍照验证

从当前源码重新构建 `agent_comm`、`behavior_executor`、`observation_manager`、`flight_agent_ros` 后，在仓库根目录执行下列命令。它会启动并清理本轮仿真和生产节点，经真实模型运行拍照及暂停后的失败两轮，沿用现有累计预算；运行前停止其他仿真及同名节点。证据范围与结果见 [T03c 记录](../../plans/M6.md#t03c-agent-拍照接入2026-11-18)。

```bash
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
source .env
export PYTHONPATH="$PWD/.venv/lib/python3.12/site-packages:$PWD/ros2_ws/src/flight_agent_ros:$PWD/src:/usr/lib/python3/dist-packages:$PYTHONPATH"
export ROS_LOG_DIR="$PWD/artifacts/m6_t03c/live_ros_logs"
FLIGHT_M6_LIVE_API=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest ros2_ws/src/behavior_executor/test/test_agent_capture_sitl.py -m sitl -q -s -o addopts= -p no:cacheprovider
```

## M6 T03b 拍照服务

在前述相机仿真、场景与桥接运行后，于仓库根目录加载 ROS 与工作区环境，启动拍照服务。首次使用新增入口前，在 `ros2_ws` 中执行 `colcon build --packages-select flight_agent_ros --symlink-install`，再回到仓库根目录。

```bash
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
ros2 run flight_agent_ros capture_photo_server --output-dir artifacts/photos
```

另开已加载上述环境的终端调用：

```bash
ros2 service call /capture_photo rosllm_srvs/srv/AtomicAction "{input: '{}', prev_output: ''}"
```

只有 `success=true` 才使用返回的 `image_path` 和 `metadata_path`；图片、配套 JSON 与哈希含义见[拍照服务契约](../../contracts/rosllm_services.md#m6-t03a-拍照服务)。服务不需要重新安装已有依赖；必须持续收到相机、时钟和有效 PX4 局部位置，启动服务本身不代表已能拍照。仿真重启时按 T02b 要求停止旧桥接和拍照服务，再在新仿真就绪后重启，不沿用旧缓存。

源码测试为 `ros2_ws/src/flight_agent_ros/test/test_capture_photo_server.py`，直接检查正式业务代码。真实地面验证为同目录 `test_capture_photo_sitl.py`，显式通过 pytest 的 `-m sitl` 执行；它会自行启动与清理仿真、桥接和服务，运行前停止其他同类进程。结果与原始证据目录见 [M6 T03b 记录](../../plans/M6.md#t03b-正式拍照服务2026-11-18)。本阶段仍未接入 Agent 或检测服务。

## M6 T04b 图片检测服务

首次使用时，在 `ros2_ws` 中加载 Jazzy 环境并执行 `colcon build --packages-select flight_agent_ros --symlink-install`。使用系统 Python/OpenCV 环境，不将 uv 的 NumPy 置于系统包之前。回到仓库根目录，加载环境后启动：

```bash
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
ros2 run flight_agent_ros inspect_image_server --photo-root artifacts/photos
```

照片根目录必须对应拍照服务的 `--output-dir` 且已存在。请求 `input` 为 `{"target_class":"person"}`，`prev_output` 必须原样传入一次成功拍照返回的完整 `output` JSON；字段、错误和预算见[检测服务契约](../../contracts/rosllm_services.md#m6-t04a-图片检测服务)。未检出仍成功，工具故障返回失败。Agent 串联验证见下方 T04c。

在已加载上述环境的终端执行回归：

```bash
export ROS_DOMAIN_ID=74
export ROS_LOG_DIR="$PWD/artifacts/m6_t04b/ros_logs"
export PYTHONPATH="$PWD/ros2_ws/src/flight_agent_ros:$PYTHONPATH"
/usr/bin/python3 -m pytest -q ros2_ws/src/flight_agent_ros/test/test_inspect_image_server.py ros2_ws/src/flight_agent_ros/test/test_inspect_image_entrypoint.py ros2_ws/src/flight_agent_ros/test/test_capture_photo_server.py
```

正式入口用例需要本地 T03b 证据照片，缺失时明确跳过；不能把跳过算作实际服务通过。用例自行启停检测进程，不启动仿真或飞行；复用真实已保存照片的证据范围与构造故障范围见 [T04b 记录](../../plans/M6.md#t04b-独立检测服务2026-11-18)。

## M6 T04d 逐项结果交付

重建 `agent_comm` 和 `behavior_executor` 后，按原方式启动有人入口。结果目录默认是启动目录下的 `artifacts/behavior_results`；可指定：

```bash
ros2 run agent_comm ros_agent_srv --ros-args -p behavior_result_dir:=/绝对路径/behavior_results
```

每轮执行结束后，终端显示逐项原始输出和“行为结果文件”的绝对路径。打开该 JSON，按步骤序号读取原始 `output`，用 `photo_id` 关联图片和检测结果；图片及采集时间、位置仍从拍照输出引用的原图和元数据读取。后续动作不覆盖已有步骤，失败轮也保留已返回的结果。`BEHAVIOR_RESULT_SAVE_FAILED` 表示落盘未成功，此时不能把残留文件当作正式交付。字段与边界只在 [T04d 契约](../../contracts/rosllm_services.md#m6-t04d-逐项结果交付)定义。

真实地面验证沿用下一节的环境和命令，证据写入 `artifacts/m6_t04d/<时间>/`，正式结果独立保存在其中的 `behavior_results/`。不需要启动飞行动作服务，飞机保持未解锁；单点飞行返航后交付见下方 T05a。

## M6 T05a 单点飞行视觉验证

加载 T03c 所列 ROS、工作区、`.env` 和 `PYTHONPATH` 环境后执行：

```bash
export ROS_LOG_DIR="$PWD/artifacts/m6_t05a/live_ros_logs"
FLIGHT_M6_LIVE_API=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest ros2_ws/src/behavior_executor/test/test_agent_visual_sitl.py -m sitl -q -s -o addopts= -p no:cacheprovider
```

运行前停止其他同名仿真和节点。用例自行启停实际生产进程，沿用累计模型预算，临时设置并恢复限速、心跳及低空 RTL 参数。输入和判据在调用前保存为 `test_temp_case.json`；证据位于 `artifacts/m6_t05a/<时间>/`，正式交付在其 `behavior_results/` 下。结果和证据边界以 [T05a 记录](../../plans/M6.md#t05a-单点飞行视觉闭环2026-11-18)为准。

## M6 T04c Agent 拍照后检测验证

先按 T03c 重建四个生产包，并加载该节的 ROS、工作区、`.env`、`PYTHONPATH` 环境。运行前停止其他同名仿真和节点，再执行：

```bash
export ROS_LOG_DIR="$PWD/artifacts/m6_t04c/live_ros_logs"
FLIGHT_M6_LIVE_API=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest ros2_ws/src/behavior_executor/test/test_agent_capture_sitl.py -m sitl -k inspect -q -s -o addopts= -p no:cacheprovider
```

用例自行启停仿真、相机桥及生产节点，保持飞机在地面，沿用既有模型预算。检测进程单独使用系统 Python，并从继承的 `PYTHONPATH` 移除本项目 uv 环境，以满足上节系统 OpenCV 的要求；其他节点保持原环境。证据、失败记录和适用范围见 [T04c 记录](../../plans/M6.md#t04c-agent-拍照后检测2026-11-18)。T04d 扩展了该用例，另核对正式终端交付的逐项结果及失败轮不覆盖前轮文件；单点飞行返航验证见上方 T05a。

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

用例：`ros2_ws/src/flight_agent_ros/test/test_flight_sitl.py`。单一 `test_action_sitl` 使用 `pytest.mark.parametrize` 配置场景；7e 请求起飞 6 米并验证系统 5 米上限导致拒绝、后端零执行及三个命令 Topic 零派发，7f 请求起飞 3 米后降落，7g-1 起飞后等待稳定、悬停 5 秒再降落。7g-2 起飞后移动到北 3 米、东 2 米、高 3 米再降落。7g-3 起飞、GoTo 到异地后经 RTL 自动返回并着陆。五组均启动真实 PX4/Gazebo 和实际 server，不替换后端或适配器。

普通 pytest 默认排除 `sitl`。需要运行时，在仓库根目录执行：

```bash
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
export PYTHONPATH="$PWD/ros2_ws/src/flight_agent_ros:$PWD/src:$PWD/.venv/lib/python3.12/site-packages:$PYTHONPATH"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /usr/bin/python3 -m pytest -q -s -m sitl ros2_ws/src/flight_agent_ros/test/test_flight_sitl.py
```

可追加 `-k 7e`、`-k 7f`、`-k 7g-1`、`-k 7g-2` 或 `-k 7g-3` 选择参数项。新增同类场景只需增加一对参数：`test_input_case` 保存输入配置，`expected_output_case` 保存预期结果；逐步对应 `test_input_step`、`expected_output_step`。配置中，`initial_state` 指定初始状态，`px4` 指定临时限速/心跳，输入侧 `steps` 指定服务、输入、等待条件和超时，预期侧 `steps` 指定预期响应/状态和命令，预期侧 `topic_counts` 指定 Topic 计数，`safety` 可覆盖本轮限制。字段条件支持精确值、点分路径及 `{min, max}` 闭区间；起降属于同一参数项的两步序列。

测试会打印本轮 `artifacts/m4-7e/pytest-时间/`、`artifacts/m4-7f/pytest-时间/`、`artifacts/m4-7g-1/pytest-时间/`、`artifacts/m4-7g-2/pytest-时间/` 或 `artifacts/m4-7g-3/pytest-时间/` 路径，保存环境、代码及测试文件校验值、请求、响应、状态、安全决策、后端调用次数和命令记录。退出时清理本轮启动的进程，失败时也保留证据。运行前应停止其他 PX4 或同名服务，避免共享环境干扰；本用例只负责自己启动的进程。

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


## M4 实际服务返航记录

2026-11-09，基于 `422c28d`（v0.10.73）及本批测试改动，在冻结 PX4 `54f0455`、ROS 2 Jazzy、Gazebo x500 上运行 `test_action_sitl[7g-3-takeoff-goto-rtl]`，首轮 `1 passed, 4 deselected in 51.30s`。证据目录为 `artifacts/m4-7g-3/pytest-20261109T025650481218Z/`。源码、工作树差异和环境版本由 manifest 绑定；所有证据校验值及测试源码匹配已复核。

准备阶段复用前述起飞与 GoTo：经真实 `/takeoff` 到 3 米，经 `/goto` 到北 3 米、东 2 米、高 3 米。等待 OFFBOARD、Home 有效、已解锁离地、位置处于目标附近且状态年龄小于 200 ms，再经 `/rtl` 发送空参数。RTL 请求时冻结 Home NED `(-0.031941, 0.003313, -0.010177)` 米，避免把着陆后可能更新的 Home 当成原目标。RTL 场景没有 `/land` 请求。

PX4 源码默认 RTL_RETURN_ALT 为 60 米，但本轮实际运行初值回读为 30 米；测试不依赖源码默认值作为运行值。为保持本场景高度 5 米、半径 10 米及围栏 ±10 米的既有限制，测试临时设置 RTL_RETURN_ALT=3、RTL_DESCEND_ALT=2、RTL_LAND_DELAY=0、RTL_TYPE=0、RTL_CONE_ANG=0，逐项设置后回读。退出回读恢复值分别为 30、10、0、0、45，详见 `test_temp_rtl_parameters.json`；PX4 水平限速设置前、运行中与恢复后均为 8 m/s，心跳退出恢复 1 Hz。该配置只覆盖受控低空返航，不代表默认高度参数组合已验收。

入口仍为实际 `flight_atomic_action_server.main()` 装配的 ROS 服务、Gate、Supervisor、真实 Backend 与 PX4 适配器。V1 假定操作员已授权，其他检查生效；本轮无替身或直接 Backend 调用。Trace 关联三个动作的提案、决策与执行 ID，并验证决策先落盘后进入后端、发送命令。命令依次为起飞 `22, 400`、GoTo `176`、RTL `20`，全部收到接受 ACK；RTL 接受后停止 GoTo 的 Offboard 流，由 PX4 执行返航着陆。

RTL 位置检查保留执行开始前 0.5 秒内最后一个采样，起点 NED 为 `(3.021241, 2.080707, -3.002689)` 米，水平距原 Home 3.692896 米；结束前最后采样为 `(0.003132, -0.012308, 0.029027)` 米，水平误差 0.038393 米，小于 1 米门槛。执行耗时 9.959 秒，997 个有效位置采样的接收时间及源采样时间均递增、间隔不超过 0.5 秒，最大接收间隔 12.822 ms，末采样距执行结束不超过 0.5 秒。正式 WorldState Trace 在执行窗口中记录 OFFBOARD 转入 AUTO_RTL，包含空中已解锁的 RTL 状态，窗口内无 failsafe；最终响应证实着陆且已上锁。

`test_temp_rtl_evidence.json` 保存原 Home、起终点、距离、耗时与采样汇总；原始位置和命令/ACK 存于既有消息文件，请求响应与正式 Trace 沿用既有命名。临时副本中的未返回 Home、删除执行开始前采样、移除 RTL 模式均使新增检查失败，原始证据通过。未改生产逻辑或正式证据。退出记录确认本轮 server、PX4/Gazebo 和 XRCE Agent 所属启动进程已结束，参数恢复读数已保存。

服务仍返回 `verification=INCONCLUSIVE`。本场景证明受控低空 RTL 的实际服务链与返回原 Home 后着陆，不代表 M6 独立验证、M7-S 在途监测、完整 Agent/LLM 闭环、其他 RTL 参数组合或真机能力。复现使用本页 pytest 命令加 `-k 7g-3`；完整 ROS 回归的源码优先路径见 [7g-2 回归环境](../../plans/M4.md#7g-2-回归环境)。
