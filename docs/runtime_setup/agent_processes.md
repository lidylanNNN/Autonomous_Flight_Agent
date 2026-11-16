# M5-4a 独立进程验收

本说明只验证安装产物、服务发现及跨进程人工轮次。阶段结果见 [M5 记录](../../plans/M5.md#4a-实施记录)；真实模型和 PX4/Gazebo 留待 4b 验收。

## 运行

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
