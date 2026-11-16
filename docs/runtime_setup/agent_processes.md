# Agent 运行验收

4a 验证安装产物和跨进程人工轮次；4b 使用真实模型与 PX4/Gazebo 验证起降和多动作序列。阶段结果见 [M5 记录](../../plans/M5.md)。

## 4a 运行

在仓库根目录使用 Bash。构建目录和 `--basetemp` 每轮使用新路径，避免 pytest 清除旧证据；以下路径是本次复现示例。需要允许本机 DDS 通信。

```bash
unset AMENT_PREFIX_PATH COLCON_PREFIX_PATH CMAKE_PREFIX_PATH PYTHONPATH
source /opt/ros/jazzy/setup.bash
export PYTHONPATH="$PWD/src:$PWD/.venv/lib/python3.12/site-packages:$PYTHONPATH"
colcon --log-base /tmp/afa_m5_4a_clean/log build --base-paths ros2_ws/src --packages-select px4_msgs rosllm_srvs flight_agent_ros agent_comm observation_manager behavior_executor --build-base /tmp/afa_m5_4a_clean/build --install-base /tmp/afa_m5_4a_clean/install
source /tmp/afa_m5_4a_clean/install/setup.bash
export TEST_TEMP_CLEAN_INSTALL=/tmp/afa_m5_4a_clean/install
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ROS_DOMAIN_ID=43 FLIGHT_M5_LIVE_API=0
export ROS_LOG_DIR=/tmp/ros_log_m5_4a_manual
mkdir -p artifacts/m5-4a
/usr/bin/python3 -m pytest -q -s -x -o faulthandler_timeout=50 --basetemp=artifacts/m5-4a/manual-001 ros2_ws/src/behavior_executor/test/test_process_round.py
```

未设置 `TEST_TEMP_CLEAN_INSTALL` 时，普通回归跳过该用例。用例检查包索引、Python 模块及两个替身包装入口均来自指定安装目录，不通过源码 ROS 包覆盖安装副本。

## 进程与证据

测试启动五个独立进程：`llm_node`、`observation_manager_node`、`sequence_executor`、`flight_atomic_action_server`、`ros_agent_srv`。模型、执行器和人工入口直接运行安装后的 console script；观察和飞行服务由 `process_boundary_fixture.py` 替换外部来源后，通过 `runpy` 运行原安装脚本及其实际 `main()`。

用例先发现观察、模型、行为和五个原子动作服务，再向实际终端入口的 stdin 写入一次输入并关闭 stdin。程序完成固定 Hold/RTL 序列、再观察、重新提示输入后通过 EOF 退出；其余进程由测试逐个回收。每个进程的实际命令、PID、退出码和代码/环境/配置摘要保存于 `test_temp_manifest.json`。

每轮证据位于 `--basetemp` 下的用例目录：`agent.jsonl` 保存两次观察和一次模型回复，`safety.jsonl` 保存两个批准决策和执行结果，`test_temp_*.log` 保存五进程输出。`safety.json` 是本轮实际加载的限制。

## 替身边界

- 模型使用现有 `mx_fixed_behavior:=true`；不验证 HTTP、真实推理或预算账本，本用例主动移除子进程密钥。
- 两个状态来源替换为读取同一测试文件的 Node；接收时间在读取时刷新，不验证 PX4 Topic、聚合器或真实状态时效。观察服务仍按生产实现生成摘要，固定来源标签不作为 PX4 实测证据。
- Backend 使用已有 `MockFlightExecutionBackend`，执行后原子更新共享文件。生产 Backend 构造参数被忽略，PX4 命令、ACK、Offboard 发布及持续时间未执行；最终 `AUTO_RTL` 是替身状态，不代表已返航着陆。
- 限速来源固定为 8m/s，不验证 MAVLink 读取或 `max_age_s`。Authority 使用生产入口的 V1 固定授权假设；Gate、检查器、Trace 及五个 ROS 服务均保留实际实现。

该设施不安装为生产入口，也不替代 4b 中实际 PX4 状态与 Backend 的装配。

## 4b-1：真实模型两轮起降

用例为 `behavior_executor/test/test_agent_sitl.py`。沿用上文安装环境，在仓库根目录运行以下命令；密钥只从本地 `.env` 读取，不写入日志或命令参数。先确认无其他 PX4/XRCE 实例；本用例只清理自己启动的进程。

```bash
set -a
source .env
set +a
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ROS_DOMAIN_ID=0 FLIGHT_M5_LIVE_API=1
export ROS_LOG_DIR=/tmp/ros_log_m5_4b1
/usr/bin/python3 -m pytest -q -s -x -m sitl -k 4b-1 -o faulthandler_timeout=120 ros2_ws/src/behavior_executor/test/test_agent_sitl.py
```

使用 domain 0 与当前 PX4 XRCE 配置通信；4a 的 domain 43 不沿用。测试复用 M4 的仿真启动、原始消息订阅、状态等待、临时限速及心跳设置，并复用 4a 的进程清理。生产节点全部直接运行安装入口，不加载 PX4 替身，模型采用默认真实 API 模式。

脚本逐轮向 stdin 提供“起飞到离地 3 米”和“立即降落”；两次输入之间等待实际终端提示、空中 `AUTO_LOITER` 及高度容差，并观察 1 秒内没有新增模型回复。每轮只发一次输入，失败即结束，不重发模型请求或动作。系统高度上限 5 米、半径 10 米、围栏 ±10 米，限速 8m/s；临时心跳为 10Hz，退出恢复 1Hz，限速恢复并独立回读。预算沿用 `.env` 指定的已有账本，不新建或清零。

每轮创建 `artifacts/m5-4b-1/时间/`：正式 Trace、两次输入时间、真实命令/ACK/位置、配置、进程日志与退出状态均保留。生产 `verification` 仍为 `INCONCLUSIVE`；着陆证据由测试独立核对，不据此宣称 M6 或实机能力。失败轮次保留，不计为通过。

## 4b-2：真实模型多动作序列

沿用 4b-1 的环境和限制，命令中的筛选条件改为 `-k 4b-2`。同一个测试函数按参数选择场景；未筛选时会依次运行两个场景并分别调用真实模型。

起始为地面未解锁。第一轮单独请求起飞至 3 米并等待稳定；第二轮只输入一次，要求模型生成 GoTo（北 3、东 2、高 3 米，接受半径 0.5 米）、Hold 2 秒、Land 三个动作。降落是该序列的最后一项；任一步失败即停止，不追加重试或收尾指令。

飞行服务进程使用 `sys.setprofile`、`threading.setprofile` 观察实际回调返回，不替换服务、Gate、Backend 或输入输出。`test_temp_service_calls.jsonl` 保存收到的 input、prev_output 和原始响应：每轮首项的 prev_output 应为空，后续项应等于紧邻前项的完整 output。飞行动作目前不使用 prev_output 计算参数，本项只验证传递。

证据保存在 `artifacts/m5-4b-2/时间/`，其余格式沿用 4b-1；另外检查 GoTo 附近的原始位置采样、Hold 实际执行耗时、一次清单只产生一轮模型请求，以及整段结束后才读取观察。观察节点与执行节点独立订阅状态，最后一份 Agent 观察可能尚未包含解除解锁状态；应与执行结束状态分别报告，不能把“重新读取观察”当作终态字段已同步。
