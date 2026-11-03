# MX-5 结构变更单：主链审计与 Trace 收口

状态：首轮审计、测试依赖迁移已实施并验证；3a、3b 已提交，3c 已实施、待评审，3d 尚未实施。

依据：[当前规格](../SPEC_DEV.md)、[MX 总计划](MX.md)、[首轮审计](MX-5-audit.md)。本文件作为新会话继续 MX-5 的入口；已完成批次不重复实施。

## 已确认范围

- 保存完整的观察文字和 LLM 回复，并记录来源。
- 保存每个已派发动作的执行结果；成功、失败、超时均保留。

## 现状

- 首轮审计已提交为 `d95c5c4`（v0.10.59），当时核心 164 项、ROS 125 项通过。发现及占位清单见[审计报告](MX-5-audit.md)。
- 两个集成测试已从 `agent_comm/test/` 迁到 `behavior_executor/test/`，两包测试依赖已同步；六包干净构建和新安装环境下 50 项回归通过，详见[依赖修正记录](MX-5-test-dependencies.md)。

- Trace 记录与接线进展以文末 3a 至 3c 实施记录为准；最终主链审计仍待 3d。
- Gate 能取得完整 `SkillResult`，动作服务只返回其中的部分字段。

## 目标

- 事后能从文件查看当时提供给 Agent 的观察、LLM 的完整回复，以及每个动作实际返回的执行结果。
- 通过提案、决策、执行 ID 核对批准与执行，不新增任务 ID。

## 已确认记录格式

沿用逐行 JSON，每行保留 `recorded_at` 和 `type`；旧记录字段不改。

| 新记录类型 | 保存内容 | 写入时机 |
|---|---|---|
| `observation` | 服务响应的 `success`、完整 `observation`、`source`、`message` | RosEnv 收到每次观察响应后，包括失败响应 |
| `llm_response` | 实际请求的完整 `prompt`，响应的 `success`、完整 `response`、`info` | RosAgent 收到 LLM 服务响应后，包括默认未接模型时的失败响应 |
| `skill_result` | 完整批准命令 `command` 和 Backend 返回的完整 `result` | Gate 收到执行结果后，返回动作服务前 |
| `skill_execution_error` | 完整批准命令 `command`、异常类名和异常文字 | Backend 执行抛异常时；记录后继续抛出原异常 |

`command` 使用现有 `ApprovedSkillCommand`，`result` 使用现有 `SkillResult`，按其 JSON 模型序列化。批准命令提供提案、决策、执行和状态 ID；结果提供状态、失败原因、开始结束时间等原始字段。

## 约束

- 拒绝的动作没有执行结果，继续以 Safety 决策记录拒绝事实，不伪造 `SkillResult`。
- 执行抛异常只说明执行调用异常，不代表飞机已停止，也不编造成功、失败或超时终态。
- 观察和 LLM 完整内容保存在 Agent 进程的文件，动作执行记录使用 Gate 现有文件；不让多个进程同时写同一个文件。
- 新文件可包含新增记录；新回放器继续读取旧文件，并校验新增记录。旧回放器不能读取新记录，不宣称向旧程序兼容。
- 本次不建立完整 Episode 关联模型；观察、LLM 与动作之间尚不能仅靠新增字段证明完整因果链。

## 对象与调用流

- Agent 入口创建并持有 `TraceRecorder`，交给 RosEnv、RosAgent 使用；Gate 使用现有 Recorder。新增强类型 Trace 记录模型由回放器解析，业务服务字段不变。

```text
RosEnv --完整观察响应及来源--> TraceRecorder
RosAgent --完整 prompt 和 LLM 响应--> TraceRecorder
SafetyExecutionGate --批准命令及 SkillResult--> TraceRecorder
SafetyExecutionGate --批准命令及执行异常--> TraceRecorder
TraceReplay --读取请求--> Trace 文件
Trace 文件 --逐行记录--> TraceReplay
```

## 已确认失败处理

- 观察或 LLM 来源记录写入失败：本轮不继续派发行为，向调用方报告记录错误。
- Safety 决策写入失败：保持现有零派发规则。
- 动作结果写入失败：报告记录错误，不重试动作；动作此时可能已经完成，不能说未执行。
- Backend 异常且异常记录也写入失败：保留原执行异常，额外报告记录错误，避免丢失原始故障。

## 实施任务

1. **主链回归与审计（已完成首轮）**
   - 已核对真实服务调用、对象传递、失败停止与运行中身份关联，列出占位及未接入项；证据见审计报告。
2. **测试依赖收口（已完成）**
   - 两个测试迁入 `behavior_executor/test/`，测试依赖声明与干净构建已验证；内容保持逐字节一致。
3. **来源与执行结果 Trace（3a 至 3c 已实施，3d 未实施）**
   - 3a. 记录器和回放器（已提交）：新增四种记录的写入方法、强类型模型与遍历方法；兼容读取旧记录，检查命令与结果的身份一致性。
   - 3b. 观察和 LLM 接线（已提交）：Agent 入口提供独立 Trace 路径，接入 RosEnv、RosAgent；记录实际服务响应。
   - 3c. 动作结果接线（已实施、待评审）：Gate 在 Backend 返回或抛异常后记录事实，保留先写批准决策再派发的顺序。
   - 3d. 主链验证：从人工入口验证完整内容、来源、动作身份及失败分支，更新 MX-5 验收状态。

步骤 3 保留大步骤划分，3a 至 3d 分别作为最小可评审批次；每批包含所需测试与文档，完成后交由用户评审。

## 验证

- 3a：旧文件、新旧混合文件可读取；未知类型、字段错误、命令与结果 ID 不一致须报错。
- 3b：固定 LLM、默认失败、观察占位及观察失败均如实记录；来源记录失败时不派发动作。
- 3c：成功、执行失败、超时及异常均留有对应记录；拒绝零执行；结果写入失败不重复执行。
- 3d：实际服务、Gate 与 Backend 替身链路通过；明确未启动 PX4/Gazebo。

## 风险与回滚

- 完整文本会增加文件大小，异常记录可能包含运行诊断；来源文字必须保留原值，不能伪装真实模型或真实观测总结。
- 结果写入错误不能撤销已执行动作，服务诊断须明确区分记录失败和未执行。
- 回滚按批次恢复调用方、记录器和回放器；包含新类型的 Trace 文件须由新版回放器读取。

## 新会话起点

- 先读 `AGENT.md`、当前 SPEC、本文和审计报告；检查工作区与最新提交。
- 本文的记录格式与失败处理已获用户确认；3c 代码待评审，后续从 3d 主链审计开始。不要重复实施已完成批次、迁移测试或恢复旧任务模型。
- 测试新路径为 `ros2_ws/src/behavior_executor/test/`；干净构建产物在 `/tmp/mx5-deps-clean/`，可用作参考，不能假定新环境一定存在。
- 沙箱内参数适配器测试曾停在 asyncio 线程池关闭阶段；沙箱外同一文件及完整 ROS 回归通过，环境差异见审计报告。
- MX 仍未整体验收；真实 PX4/SITL、可信授权和完整 Episode 关联仍有缺口，不因结构变更单提交而标为完成。

## 3a 实施记录（2026-11-03，`925794b` / v0.10.61）

- 变更：`components/tracing/` 新增四类写入、模型及遍历方法；旧记录格式与读取接口保留。
- 对象：四种记录模型继承内部 `ContentTraceRecord` 基类；记录器写入前创建，回放器读取时解析，无新增生产节点。
- 输入输出：写入接收完整文本字段、命令和结果或异常，返回 `None`；遍历返回对应模型。字段或身份错误抛校验异常，文件写入错误向外传播。
- 身份校验：结果的 `execution_id`、`proposal_id`、`skill_name` 必须与命令一致；不要求起始状态等于批准状态。
- 测试数据流：测试 --事件内容--> TraceRecorder --JSONL--> 临时文件；临时文件 --记录行--> TraceReplay --强类型记录--> 测试。
- 生产接线：本批新增方法尚无生产调用方；Agent 与 Gate 保持原调用链，来源与结果接线仍分别待 3b、3c，F1 尚未关闭。
- 验证：`PYTHONPATH=src .venv/bin/python -m pytest -q tests`，190 项通过（其中 Trace 35 项）；覆盖完整文本、失败响应、全部动作终态、异常、混合记录、身份不一致与非法字段。
- 静态检查：`.venv/bin/ruff check src/flight_agent/components/tracing tests/unit/test_trace.py` 与 `.venv/bin/mypy src/flight_agent/components/tracing --follow-imports=silent` 通过；后者范围为 tracing 三个文件。
- 本批仅为核心直接调用测试，未运行 ROS、PX4/Gazebo；无偏离已确认范围。后经用户授权提交并推送。

## 3b 实施记录（2026-11-03，`393d5e2` / v0.10.62）

- 变更：`agent_comm/ros_agent_srv.py` 在观察和实际 LLM 服务响应返回后、交给调用方前保存完整内容；失败观察不触发 LLM，也不伪造模型响应记录。
- 对象职责：`main()` 创建并持有一个 `TraceRecorder`，交给 `RosEnv`、`RosAgent` 共用；无新增类或生产节点。
- 输入输出：两构造函数新增记录器参数；`main()` 增加仅限关键字的可选 `trace_path: Path` 参数供测试指定路径。原返回类型与 ROS 服务字段保持不变。
- 路径：生产入口使用 ROS 参数 `agent_trace_path`，默认 `artifacts/agent_trace_<随机 UUID>.jsonl`；可用 `ros2 run agent_comm ros_agent_srv --ros-args -p agent_trace_path:=/tmp/agent_run.jsonl` 指定，每个进程须独占文件。
- 实际生产数据流：观察服务 --Observation.Response--> RosEnv --完整响应及来源--> TraceRecorder；LLM 服务 --LLM.Response--> RosAgent --实际 prompt、完整回复及 info--> TraceRecorder；TraceRecorder --JSONL--> Agent 独立文件。
- 失败处理：记录写入的 `OSError` 增加 `OBSERVATION_TRACE_WRITE_FAILED` 或 `LLM_TRACE_WRITE_FAILED` 上下文后传播，入口退出并销毁节点；不继续派发。执行后的再观察写入失败不能撤销已执行动作。
- 生产接线：新增来源写入已接入 `ros_agent_srv.main()`；Gate、动作结果与执行异常记录仍未接线，完整 Episode 关联与 3d 审计尚未完成。
- 验证范围：真实 ROS 服务及入口测试；PX4 Topic 输入与原子动作由替身提供，未运行 PX4/Gazebo，无范围偏离。
- 构建：加载 Jazzy 和 `/tmp/mx5-deps-clean/install/setup.bash` 后，执行 `colcon --log-base /tmp/mx5-3b-colcon-log build --base-paths ros2_ws/src --build-base /tmp/mx5-deps-clean/build --install-base /tmp/mx5-deps-clean/install --packages-select agent_comm`，1 包成功；这是增量构建，不是六包重新干净构建。
- ROS 验证：仅加载上述安装目录，`PYTHONPATH` 补核心 `src` 和 `.venv/lib/python3.12/site-packages`，设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`、`ROS_LOG_DIR=/tmp/ros_log_mx5_3b_installed`；`/usr/bin/python3 -m pytest -q -o faulthandler_timeout=50 ros2_ws/src/behavior_executor/test ros2_ws/src/observation_manager/test ros2_ws/src/flight_agent_ros/test/test_flight_atomic_action_server.py`，53 项通过（33.69 秒）。已确认入口模块来自安装目录，无 ROS 包源码路径覆盖。
- 核心：`PYTHONPATH=src .venv/bin/python -m pytest -q tests`，190 项通过；两处修改的 Python 文件 Ruff 与 `git diff --check` 通过。

## 3c 实施记录（2026-11-03，未提交）

- 变更：`SafetyExecutionGate.submit()` 在 Backend 返回后调用结果记录方法，执行抛异常时调用异常记录方法；两个写入方法复用 3a 已确认的格式。
- 对象职责：无新增或删除类、模型、节点；Gate 持有的 Recorder 仍由 `build_flight_atomic_action_server()` 创建并传入。
- 输入输出：`submit(proposal, constraints)` 的参数和 `(SafetyDecision, SkillResult | None)` 返回类型不变；拒绝仍返回 `(decision, None)`，不分配执行 ID、不派发、不写执行结果。
- 结果记录失败：抛出包含执行 ID、Backend 返回状态和不得重试说明的 `RuntimeError`，原记录异常保留为 cause；动作服务沿用 `SERVICE_ERROR`，详细诊断写日志。不能把空执行 ID 响应解释为 Backend 未执行。
- 双重异常：Backend 原异常保持对象身份并继续抛出；记录异常通过 `add_note()` 附加到原异常，并由 Gate 错误日志额外报告，不编造动作终态。
- 实际生产调用流：动作服务 --提案与限制--> Gate；Gate --提案与决策--> TraceRecorder；Gate --批准命令（仅批准且决策落盘后）--> Backend；Backend --SkillResult 或执行异常--> Gate；Gate --批准命令及结果或异常--> TraceRecorder；TraceRecorder --JSONL--> 现有动作 Trace 文件。
- 接线边界：生产装配已使用本 Gate，默认 Authority 仍为 `UNKNOWN`；批准与故障验证显式注入 Backend、权限与限速替身，未运行 PX4/Gazebo。3d 最终主链审计尚未实施。
- 核心验证：`PYTHONPATH=src .venv/bin/python -m pytest -q tests`，195 项通过；新增 Gate 单测覆盖失败、超时、结果记录失败、Backend 异常和双重异常，并验证单次执行与拒绝无结果记录。
- 静态验证：修改的三个 Python 文件 Ruff 通过；`.venv/bin/mypy src/flight_agent/components/safety/execution_gate.py --follow-imports=silent` 通过（仅 Gate 文件）。
- ROS 验证：加载 Jazzy 与 `/tmp/mx5-deps-clean/install/setup.bash`，`PYTHONPATH` 补核心 `src` 与 `.venv/lib/python3.12/site-packages`；设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`、`ROS_LOG_DIR=/tmp/ros_log_mx5_3c`，执行 `/usr/bin/python3 -m pytest -q -o faulthandler_timeout=50 ros2_ws/src/behavior_executor/test ros2_ws/src/observation_manager/test ros2_ws/src/flight_agent_ros/test/test_flight_atomic_action_server.py`，68 项通过（33.94 秒）。使用现有 ROS 安装产物与本批核心源码，无 ROS 生产代码变更、未重新构建。
- 服务验证覆盖五个动作的成功、失败、超时、执行异常、结果记录失败和双重异常；已确认决策先于派发、拒绝零执行、写入失败不重试。无偏离原汇报，未提交或推送。
