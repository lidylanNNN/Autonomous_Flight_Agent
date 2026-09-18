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
python scripts/runtime/check_runtime_health.py
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
scripts/runtime/start_simulation.sh
```

在另一个终端检查 Topic：

```bash
scripts/runtime/smoke_test_topics.sh
```

在单独终端启动 M2 WorldState Node：

```bash
scripts/runtime/run_world_state_node.sh
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
6. 使用 `scripts/runtime/start_simulation.sh` 启动 uXRCE-DDS Agent 和 PX4。
7. 运行 `scripts/runtime/smoke_test_topics.sh`。

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
