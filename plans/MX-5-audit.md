# MX-5 主链回归与审计

状态：首轮审计已实施，Trace 来源标记缺口未关闭，MX 尚未整体验收。

审计基线：`c066bfb04d4fdbbaa218c59061a45a1c898ff632` 加本批测试与文档修改；日期 2026-11-03。范围依据 [MX 总计划](MX.md#验收与后续)，本批不修改生产行为或服务契约。

## 发现

| 编号 | 级别 | 事实与证据 | 影响 |
|---|---|---|---|
| F1 | 中，MX 验收缺口 | 总计划要求固定对象在 Trace 标源；`LLMNode` 的 `MX_FIXED_BEHAVIOR` 在响应与主循环日志，观察占位来源在 `Observation.Response`。`TraceRecorder` 仅写 `world_state`、`safety_decision` | 固定行为和占位观察的来源不能从 JSONL Trace 中还原；该要求未通过 |
| F2 | 中，审计能力边界 | Gate 先写决策再生成执行 ID；`SkillResult` 和执行 ID 出现在动作响应，但没有执行结果 Trace。序列成功响应只返回摘要 | 测试可核对运行中身份链，落盘数据尚不足以独立重建完整 Episode；不声称完整因果链已完成 |
| F3 | 中，部署验证缺口 | 整轮测试在同一 Python 进程中装配多个真实 ROS 节点，Backend、状态输入、权限和限速由测试替换 | 尚无独立生产进程与真实 PX4 的整轮证据；不能称为 SITL 验收 |
| F4 | 低，构建清单缺口 | `agent_comm` 的测试直接导入 `behavior_executor`、`flight_agent_ros`，但其 `package.xml` 未直接声明这两个测试依赖 | 当前已构建工作区可运行；干净环境的独立包测试依赖未验证，后续单独修正包清单 |

源码依据：[TraceRecorder](../src/flight_agent/components/tracing/recorder.py)、[主入口](../ros2_ws/src/agent_comm/agent_comm/ros_agent_srv.py)、[序列执行器](../ros2_ws/src/behavior_executor/behavior_executor/sequence_executor.py)、[包清单](../ros2_ws/src/agent_comm/package.xml)。

## 验收状态

| 要求 | 结果 | 证据 |
|---|---|---|
| 初始观察、人工输入、LLM、执行、再观察顺序 | 通过替身集成 | `test_flight_round.py` 从真实 `ros_agent_srv.main()` 进入 |
| 固定 hold/rtl 两步与参数传递 | 通过 | 实际飞行动作服务收到合法 JSON，第二项收到第一项原始输出 |
| 首项执行失败或权限未知后停止 | 通过 | 后续 RTL 无服务调用或 Backend 派发；主循环仍再观察 |
| 提案、决策、执行身份对应 | 通过运行中核对 | 批准 Trace 先于 Backend；命令、动作响应与决策 ID 对应，拒绝无执行 ID |
| 等待超过旧 30 秒边界与超时停止 | 通过独立执行器测试 | 真实 ROS 延迟响应 31 秒；超时不派发下一项 |
| 默认无 LLM 不派发、观察缺失明确失败 | 通过 | 原入口与观察服务测试 |
| 占位与未接入项清单 | 已整理 | 见下表 |
| 固定对象来源写入 Trace | 未通过 | F1 |

## 实际调用流

以下为本批测试实际经过的生产模块；测试注入人工文本，并通过生产装配函数创建飞行动作服务和 Gate。

```text
测试输入函数 --人工文本--> ros_agent_srv.main
ros_agent_srv.main --人工文本与观察--> RosAgent
RosAgent --LLM.Request(prompt)--> LLMNode
LLMNode --LLM.Response(固定行为及来源)--> RosAgent
RosAgent --LLM.Response--> ros_agent_srv.main
ros_agent_srv.main --提取后的行为文本--> RosEnv
RosEnv --ExecuteBehavior.Request--> SequenceExecutor
SequenceExecutor --AtomicAction.Request(input, prev_output)--> FlightAtomicActionServer
FlightAtomicActionServer --AtomicActionProposal + MissionConstraints--> SafetyExecutionGate
SafetyExecutionGate --状态读取请求--> Backend 替身
Backend 替身 --WorldState--> SafetyExecutionGate
SafetyExecutionGate --提案、限制、状态与权限--> SafetySupervisor
SafetySupervisor --SafetyDecision--> SafetyExecutionGate
SafetyExecutionGate --提案及决策--> TraceRecorder
SafetyExecutionGate --ApprovedSkillCommand（仅批准）--> Backend 替身
Backend 替身 --SkillResult--> SafetyExecutionGate
SafetyExecutionGate --决策及执行结果--> FlightAtomicActionServer
FlightAtomicActionServer --AtomicAction.Response--> SequenceExecutor
SequenceExecutor --ExecuteBehavior.Response--> RosEnv
RosEnv --Observation.Request--> ObservationManagerNode
ObservationManagerNode --状态读取请求--> Px4WorldStateAdapter
Px4WorldStateAdapter --WorldState（由注入消息聚合）--> ObservationManagerNode
ObservationManagerNode --Observation.Response--> RosEnv
RosEnv --观察、reward、done、info--> ros_agent_srv.main
```

初始观察也经过上述观察服务链。测试记录真实处理函数的输入输出，不替换 Gate 或动作服务的处理结果；未启动飞行动作 `main()` 中的真实 PX4 Backend 装配。

## 占位与未接入项

| 所属模块 | 当前行为 | 后续归属 |
|---|---|---|
| `LLMNode` | 默认明确失败；显式模式固定返回 hold、rtl，未接真实模型 | M5 |
| `ObservationManagerNode` | 读取状态后只输出带 state ID 的占位文本，来源字段标源 | M5 |
| `RosEnv.get_atomic_action_library()` | 使用 Registry 描述，输入输出说明仍为 `MX_UNIMPLEMENTED` | M5 |
| `RosEnv.step()` | 按成功/失败固定返回 reward -0.5/-1.0，done=False、info={}；不表示任务完成 | 保持现有有人循环语义，后续需要时评审 |
| 飞行动作服务 | `verification=INCONCLUSIVE`，无独立完成验证 | M6 |
| 飞行动作生产装配 | Authority 固定 UNKNOWN；真实 Backend 已装配但不能获批执行 | M4 |
| 非飞行动作 | `resolve_poi`、拍照、识别未注册，也不在当前五动作白名单 | 后续功能批次；本轮不调用 |
| 旧审批原型 | 仍有模型、组件及测试，未接入新主链；SPEC 已决定后续删除 | 独立清理批次，不算已删除 |

## 验证

- 环境：Python 3.12.3、ROS 2 Jazzy；PX4/Gazebo 未启动。冻结环境参见 [manifest](../manifests/environment.yaml)，其配置不代表本次已运行仿真。
- 核心：`PYTHONPATH=src .venv/bin/python -m pytest -q tests`，`164 passed`。
- ROS：加载 Jazzy、工作区及项目源码路径，设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`、`ROS_LOG_DIR=/tmp/ros_log_mx5`；沙箱外完整重跑结果为 `125 passed in 33.97s`，运行命令如下。

```bash
/usr/bin/python3 -m pytest -q -o faulthandler_timeout=50 \
  ros2_ws/src/agent_comm/test ros2_ws/src/behavior_executor/test \
  ros2_ws/src/observation_manager/test ros2_ws/src/flight_agent_ros/test
```

- 首次完整回归在沙箱内停滞，主动中止。单独定位发现参数适配器测试停在 `asyncio.Runner.close()` 的线程池关闭等待；沙箱外重跑该文件为 `22 passed`。这支持环境相关判断，不将中止运行计为通过。
- 构建：`colcon build --packages-select rosllm_srvs agent_comm behavior_executor observation_manager flight_agent_ros`，五包通过；日志位于 `/tmp/mx5-colcon-log`。
- 新增断言位于 [test_flight_round.py](../ros2_ws/src/agent_comm/test/test_flight_round.py)，Ruff、`git diff --check` 通过；无生产接口变更，回滚仅需恢复测试和文档。

## 后续任务

1. MX-5 来源记录收口：先明确 F1 的 Trace 事件字段、来源标记和回放兼容方式，再实现与验证；不得用日志代替 JSONL 验收。
2. 包测试依赖收口：修正 F4 并验证干净构建环境中的包测试依赖。
3. 真实环境回归：保留 M3 Backend SITL 回归、新服务真实状态与拒绝链、可信授权下批准飞行三项；其中可信 Authority 和受控 SITL 仍属 M4。

## 待决策项

- F1 的最小 Trace 事件设计尚未冻结；本批未改变 Trace 格式，也未降低原验收要求。
- F2 是否与来源事件一起扩展为执行结果审计，需单独确定范围；现有测试通过不构成该扩展的实现证据。
