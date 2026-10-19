# Autonomous Flight Agent 开发规格 v2.1

> 状态：当前总体设计基线；待确认事项见第 10 节。实现基线：2026-10-16 的 `8a13b81`；架构对齐依据为 ROS-LLM 论文 v3 与本次核对的开源主分支。设计目标不代表代码迁移已完成。
> 历史规格：[DEV_SPEC v1.44](docs/archive/DEV_SPEC_v1.44.md)。历史文件保留决策过程，不再作为当前实现或新开发的验收依据。
> 适用范围：V1 飞行机器人任务系统、仓库结构、开发顺序、测评和版本管理。代码事实以实际生产入口及测试证据为准；本文目标不自动等于已实现。

## 1. 项目定义

本项目探索把自然语言任务变成可审查的飞行动作，并在 PX4/Gazebo 中验证执行结果。主编排以 ROS-LLM 为基线；ROS Noetic 到 ROS 2 Jazzy、机械臂到 PX4 飞行器的实现替换不得改变其调用顺序。具体目标流程见第 4 章。

目标使用场景是临时点位任务：操作员描述要去的位置和观察目的，系统选择受限动作、执行并返回可核对的飞行状态。相对手动逐条操控或预先制作固定航线，潜在收益是减少任务准备和重复操作；**节省的人力、时间与成本尚无实测，不能在对外材料中写成已取得的效果**。

系统边界：LLM 只负责任务理解与高层动作选择；确定性 Safety 决定物理动作是否允许；执行后由独立观测验证结果；ROS 2 负责通信与适配；PX4 保持姿态、导航控制和原生 failsafe。Agent 不输出电机、推力、角速率或姿态闭环指令，不替代飞控固件。V1 不包含真实飞机测试、多机协同、视觉搜索或适航声明。

设计依据分三层，不混称为同一技术方案：

1. **系统调用流**：以 [ROS-LLM 开源项目](https://github.com/huawei-noah/HEBO/tree/master/ROSLLM)的 Agent、Observation Manager、Behavior Executor、Atomic Action Library 及人工反馈闭环为基线。不能把本项目自行设计的 Hooks、MissionService 或无人续跑称为上游架构。
2. **产品交互决策点**：参考 [DJI FlightHub 2 Copilot 公开手册](https://fh.dji.com/user-manual/cn/copilot-assistant/copilot-remote-control.html)讨论计划确认、接管和紧急停止；这不代表知道 DJI 的内部实现，也不把尚未实现的交互写成当前能力。
3. **飞行行为与安全条件**：以冻结的 PX4/ROS 2 接口、本仓库代码及 SITL 实测为准。产品参照不得覆盖确定性安全边界或 PX4 原生 failsafe。

### 1.1 V1 目标状态与对齐范围

V1 目标：初始观察 -> 人工任务 -> `RosAgent` 根据观察和动作库生成行为序列 -> `SequenceExecutor` 逐项执行、失败即停 -> 返回结果并更新观察 -> 等待人工反馈。任务成功须有状态证据，整条链须能在 PX4/Gazebo 测评。

**主调用拓扑与 ROS-LLM 相同**；V1 先验收 JSON 动作序列。行为树、状态机、代码执行和示教扩库未实现前，不声称全功能等价。V1 不包含无人自动续跑。

飞行 Gate、PX4 Backend、状态完成判定和在途监测是**飞行适配扩展**，不是 ROS-LLM 原生模块；它们不改变主调用拓扑。

### 1.2 源码证据与一一映射

| ROS-LLM 依据 | 上游职责 | 本项目目标对应物 |
|---|---|---|
| [`ros_agent_srv` 主循环](https://github.com/huawei-noah/HEBO/blob/master/ROSLLM/agent_comm/scripts/ros_agent_srv) | `main()` 创建 `RosEnv` 与 `RosAgent`，先调用 `env.get_obs()`，等待人工输入，再调用 `agent.get_action(human, obs)` 和 `env.step(action)`；`step()` 内执行行为并再次获取观察 | 唯一的有人任务入口；主循环调度 `RosAgent` 决策和 `RosEnv` 环境交互，二者互不调度 |
| [`agent_comm`](https://github.com/huawei-noah/HEBO/tree/master/ROSLLM/agent_comm) | LLM 服务与 Agent 通信 | LLM Provider 和 prompt/行为提取 |
| [`observation_manager`](https://github.com/huawei-noah/HEBO/tree/master/ROSLLM/observation_manager) | 聚合各观察服务，通过统一 `get_observation` 返回文本观察 | 由 `WorldState` 等来源构造统一观察，不让 Agent 直接消费 PX4 Topic |
| [`behavior_executor`](https://github.com/huawei-noah/HEBO/tree/master/ROSLLM/behavior_executor) | `ExecuteBehavior` 服务接收行为；序列/行为树/代码等执行器返回成功标志与消息 | ROS 2 行为执行器；V1 先实现序列模式，逐项调用原子动作、失败即停止 |
| [论文 §3.1、§3.5](https://arxiv.org/html/2406.19741v3) | 带名称、类型、描述和输入/输出的原子动作库；JSON 序列中的动作依次执行，前项输出可传给后项 | 飞行原子动作描述库及 ROS 2 action/service 接口，内部复用 Gate/Backend |
| [论文 §3.2–§3.4](https://arxiv.org/html/2406.19741v3) | 文本观察、人工反馈和动作库共同进入每轮 prompt；一轮行为结束后再等待人 | 飞行状态文本化、人工下一轮反馈 |

上游 prompt 允许多动作序列，`sequence_executor` 也按序列调用原子动作；但当前 `ros_agent_srv` 的提取正则只捕获代码块中的首个 JSON 对象，不能声称其入口已可靠传递多动作行为。本项目在 MX 支持固定两步行为的完整提取与对照测试，M5 再完善真实模型输出的解析和错误处理；这是补齐上游实现缺口，不改变主循环拓扑。以上链接当前指向主分支，正式对齐验收前须记录确切上游 commit。

## 2. 当前实现基线

下表描述仓库当前状态，不是目标架构图。`plans/M0.md` 至 `M4.md` 保存阶段记录；未建立的 M5 以后里程碑文件不能当作现存交付物。

| 能力 | 当前代码/入口 | 可声称的边界 |
|---|---|---|
| 环境与状态 | `ros2_ws/`、`scripts/ros2_px4/`、`Px4WorldStateAdapter`、`WorldStateAggregator` | M1/M2 的 PX4/Gazebo、ROS 2 消息和状态聚合基础已建立；运行环境以 `manifests/environment.yaml` 为准 |
| 确定性飞行动作 | `flight_execution_backend.py`、PX4 Command/Offboard adapters、Skill Registry | Takeoff、GoTo、Hold、RTL、Land 有代码映射；M3 调试脚本有 SITL 验证记录，不等于 Agent 任务闭环 |
| 任务主链 | `mission_cli.py -> MissionService.run()` | 只识别固定语句 `起飞到10米`；其他语句返回 `UNSUPPORTED`，无 LLM 或通用规划器 |
| 执行前 Safety | `SafetyExecutionGate -> SafetySupervisor -> FlightExecutionBackend` | Gate 和检查器、决策 Trace 已实现，并有替身集成测试；正式任务入口的 Authority 当前传入 `UNKNOWN`，因此不能声称已在真实任务入口获批飞行 |
| 验证与恢复 | `MissionService._verify_placeholder()`、`_recover_placeholder()` | 验证只返回 `INCONCLUSIVE`，恢复只返回 `None`；没有任务成功判定、重新规划或安全收尾实现 |
| Trace | `TraceRecorder`、`TraceReplay` | 可记录/回放 WorldState 与 SafetyDecision；完整 Episode 因果链尚未完成 |
| 测评数据 | `agent_benchmark_sets/`、schema 与 validation scripts | Dev 用例存在；Validation/Frozen Test 目前只是预留 manifest，不得报告冻结测评指标 |
| 旧审批原型 | `human_approval` 模型、组件、Mock 和测试 | 尚存仓库，但不是当前自主执行链，不等于任务级人工确认产品功能 |

### 2.1 文件处置决定（代码尚未修改）

| 现有文件/组 | 决定 | 实施条件 |
|---|---|---|
| `src/flight_agent/workflows/mission/service.py`、其 `__init__.py` 导出 | **抽取逐步派发/失败停止逻辑后删除旧入口** | 先迁出 M4 Gate 装配；新 Agent 入口和 Behavior Executor 多步测试通过后，删除固定语句规划与旧恢复循环 |
| `mission_flow_model.py` 中的 `MissionPlan`、`PlanStep`、`MissionRunResult` 及公共导出 | **保留并复核 `PlanStep`；删除旧任务级类型** | `PlanStep` 可作序列中一个飞行 Skill 的记录，不是 Skill 内部的 PX4 命令；它也不能直接代表所有 ROS-LLM 原子动作。迁移引用后删除 `MissionPlan`、`MissionRunResult`，保留 `MissionRequest`，复核 `VerificationResult` |
| `mission_cli.py`、ROS 包的 `setup.py` | **改造** | 接入有人入口、Observation Manager 和 Behavior Executor；M3 直连调试入口独立保留 |
| `tests/integration/test_mission_flow.py`、ROS 包的 `test_mission_cli.py` | **删除旧测试，编写新集成测试** | 新测试从生产入口覆盖观察、人工输入、LLM、执行、再观察和失败停止 |
| `components/skills/registry.py`、`skill_model.py` | **保留并扩展** | 动作定义补齐名称、类型、描述、输入/输出，供 prompt 与执行器共用 |
| `components/vehicle/state/`、`Px4WorldStateAdapter` | **保留并接入观察层** | 新建统一观察入口；行为执行后再次读取 |
| `components/safety/`、`safety_model.py`、PX4 Backend/adapters | **保留并复用** | 飞行原子动作继续调用现有 `SafetyExecutionGate -> SafetySupervisor -> Backend`；物理动作仍须经 Gate 和状态判定 |
| `components/tracing/`、`agent_benchmark_sets/`、`eval_harness/` | **保留并扩展** | 提供审计与测评，不参与任务决策 |
| 旧 `human_approval` 组件、模型、协议、Mock、公共导出及专属测试 | **删除** | 逐 Proposal 审批不在 V1；历史由 Git 保存，未来产品审批另行设计 |
| 旧设计中的通用 `PreToolUse`/`PostToolUse`/`OnToolError`/`Stop` Hooks 与第二套 Tool Invocation 工作流 | **从目标架构删除** | 当前无对应生产模块；记录、超时、取消属于 Behavior Executor 或飞行原子动作内部，不新增第二套任务调度 |

`configs/safety.yaml` 按原决策保留为未生效的预留配置。表中删除是确定目标；代码迁移按实施条件分批进行。

**两条实际入口必须分开报告：**

```text
受控任务入口：mission_cli --instruction/--contract-json
  -> MissionService 固定语句占位 -> Proposal -> Gate -> Supervisor
  -> 仅 APPROVE 才可 Backend.execute -> SkillResult -> INCONCLUSIVE
  当前 Authority=UNKNOWN；不能把替身测试的批准说成 SITL 任务批准。

M3 调试入口：mission_cli --legacy-m3-direct
  -> 固定飞行脚本 -> PX4 ROS 2 Backend -> PX4
  显式绕过任务 Gate，只作 M3 调试与回归，不进入自主安全测评。
```

## 3. 固定环境与仓库边界

`manifests/environment.yaml` 固定 Ubuntu 24.04、Python 3.12/uv、ROS 2 Jazzy、PX4 v1.16.2 对应提交、与其匹配的内嵌 `px4_msgs`、Gazebo Harmonic、x500 和 uXRCE-DDS。`pyproject.toml` 与 `uv.lock` 管理核心 Python 环境；ROS 包通过 `ros2_ws`/colcon 构建。Docker/Compose 仍是计划，不是当前部署证据。环境 manifest 中较早的 `spec_version` 是历史字段，更新前不得把它当成本规格版本。

当前仓库的职责分界：

```text
src/flight_agent/contracts/          Pydantic 数据契约、Protocol、错误类型
src/flight_agent/components/         Safety、Skill Registry、Mock Backend、State、Trace
src/flight_agent/workflows/mission/  当前固定语句 MissionService；待迁移旧链
ros2_ws/src/flight_agent_ros/        ROS 2 入口、PX4 状态/命令/参数/Offboard 适配
ros2_ws/src/px4_msgs/                与冻结 PX4 版本匹配的消息定义
agent_benchmark_sets/               Agent 测评数据与 schema，不是在线任务或 PX4 Mission
eval_harness/                        测评辅助代码；完整 Episode Runner 尚未实现
tests/                               单元、契约和替身集成测试
AGENT.md、SPEC_DEV.md                 长期规则与当前总体设计
contracts/、modules/                 详细接口契约与复杂模块内部设计（区别于代码包）
plans/、ADR/                          阶段计划与重要设计决策
docs/                                历史归档、环境操作手册、测评辅助文档
manifests/、configs/                 环境声明与配置；预留配置不自动生效
```

核心代码不得直接依赖 `rclpy`、`px4_msgs` 或 ROS Topic 名；PX4 适配留在 ROS 包。`flight_agent` 不依赖 `eval_harness`，测评集不反向依赖产品代码。目录存在不等于能力已接入；新增目录应由实际生产调用链驱动，不先批量铺设空壳。

### 3.1 目标目录与文件

以下是**迁移完成后的目标路径，不是当前文件清单**。主链采用 [ROS-LLM 源码的包名](https://github.com/huawei-noah/HEBO/tree/master/ROSLLM)：`agent_comm`、`observation_manager`、`behavior_executor`、`rosllm_srvs`、`rosllm_msgs`。节点沿用上游的 `ros_agent_srv`、`llm_node`、`observation_manager_node`、`sequence_executor`；`.py`、`setup.py`、`package.xml` 和接口构建方式按 ROS 2 调整。`flight_agent_ros`、`px4_msgs`、`flight_agent` 是 PX4/飞行适配或现有 Python 工程边界，不冒充上游包。没有自定义消息需求前不创建空的 `rosllm_msgs` 包。

```text
ros2_ws/src/
  agent_comm/                          [待建；上游同名 ROS 包]
    package.xml
    setup.py
    config/
      atomic_action_library.yaml      [待建；上游式动作名与描述索引]
    agent_comm/
      ros_agent_srv.py                 [待建；持有 RosAgent 与 RosEnv 的有人主循环]
      llm_node.py                      [待建；LLM 服务]
  observation_manager/                [待建；上游同名 ROS 包]
    package.xml
    setup.py
    observation_manager/
      observation_manager_node.py      [待建；统一 get_observation]
      vehicle_state_summarizer.py      [待建；PX4 观察源，飞行差异]
  behavior_executor/                  [待建；上游同名 ROS 包]
    package.xml
    setup.py
    behavior_executor/
      sequence_executor.py            [待建；ExecuteBehavior 序列执行；复用 MissionService.run() 的逐步派发、失败停止逻辑]
  rosllm_srvs/                         [待建；上游同名接口包，ROS 2 构建]
    package.xml
    CMakeLists.txt
    srv/
      AtomicAction.srv                 [待建；飞行动作契约]
      ExecuteBehavior.srv             [待建；行为执行契约]
      Observation.srv                 [待建；统一观察契约]
      LLM.srv                         [待建；模型调用契约]
  rosllm_msgs/                         [V1 无自定义消息时不创建]
  flight_agent_ros/                    [已存在；PX4/ROS 2 飞行适配]
    flight_agent_ros/
      flight_atomic_action_server.py  [待建；飞行 AtomicAction 服务，装配并调用 Safety Gate]
      adapters/
        px4_world_state_adapter.py      [已实现；观察源，不等于 Observation Manager]
        px4_parameter_adapter.py        [已实现；飞行参数读取]
        flight_execution_backend.py     [已实现；动作内调用，不等于 Behavior Executor]
        px4_command_plan_mapper.py      [已实现；PX4 命令映射]
        px4_command_plan_executor.py    [已实现；PX4 命令执行，不等于 sequence_executor]
        px4_vehicle_command_adapter.py  [已实现；PX4 命令与 ACK]
        px4_offboard_setpoint_adapter.py [已实现；PX4 Offboard]
  px4_msgs/                            [已存在；冻结 PX4 消息定义]
src/flight_agent/
  contracts/                           [已存在；Pydantic 契约与 Protocol]
  components/
    vehicle/state/world_state_aggregator.py [已实现；状态聚合]
    safety/                            [已实现；飞行原子动作内部 Gate/检查]
    skills/registry.py                 [已实现；动作注册基础，待补动作描述]
    tracing/                           [已实现；追踪基础]
  workflows/mission/                   [现有旧 MissionService；处置见第 2.1 节]
agent_benchmark_sets/                 [已存在；Agent 测评集]
eval_harness/                         [已存在；测评执行辅助]
tests/                                [已存在；迁移后新增主链集成测试]
```

上游的 `behavior_tree_executor`、`code_executor`、视觉节点、示例包及 `GetUserInput.srv` 不进入 V1 文件树；有人入口先沿用上游 `ros_agent_srv` 的终端输入方式。V1 只实现序列模式。上述路径固定职责和迁移目标，**不表示已复制或实现上游代码**。`rosllm_srvs` 的字段须在实现批次核对 ROS 2 和飞行动作语义。

现有模块的保留、复用和迁移决定见第 2.1 节。命名须反映实际职责：PX4 命令执行器不是行为序列执行器，状态聚合器不是 Observation Manager；不能为凑同名把已有模块改称尚未实现的上游模块。

## 4. ROS-LLM 对齐的目标调用链

主链只采用 ROS-LLM 的一个环境步：`ros_agent_srv.main()` 先调用 `RosEnv.get_obs()`，等待人工输入，调用 `RosAgent.get_action(human, obs)` 得到行为，再调用 `RosEnv.step(action)`；`step()` 调用 Behavior Executor 执行行为并再次获取观察。下一轮仍由 `main()` 等待人输入。`RosEnv` 不调用 `RosAgent`，LLM 不在每个原子动作后自动重规划，执行器或 Runtime 也不建立另一套任务循环。以上是目标，不是现有实现。

```text
ros_agent_srv.main(): 创建 RosEnv，读取 AtomicActionLibrary 并创建 RosAgent
main() -> RosEnv.get_obs() -> Observation Manager -> 当前观察
main(): 等待人工输入
main() -> RosAgent.get_action(人工输入, 当前观察) -> LLM -> 行为
main() -> RosEnv.step(行为) -> SequenceExecutor.execute_behavior()
  -> Atomic Action 1 -> ... -> Atomic Action N（首个失败即停止）
  -> 行为成功/失败标志及消息
RosEnv.step() -> RosEnv.get_obs() -> Observation Manager -> 新观察
RosEnv.step() -> main(): (obs, reward, done, info) -> 等待下一轮人工输入

飞行原子动作的内部：参数解析 -> Safety Gate -> PX4 Backend
  -> 飞行状态完成判定 -> 原子动作成功/失败及输出。
运行中安全监测和 PX4 failsafe 是飞行平台的独立安全路径，
不选择下一行为，不改变上面的 Agent 环境步调度。
```

一次完整转换示例：用户说“去三号塔看看有没有异物，拍张照，然后回来”。此例**假设飞机已在空中**，三号塔位置库、相机和识别服务已经接入；这些视觉/位置服务当前尚未实现，不得把示例当作 V1 已有能力。方括号内是模块、配置源或外部参与者，箭头上是输出的数据对象；`RosAgent`、`SequenceExecutor`、`SafetyExecutionGate` 等是对应模块持有的类对象，不是链路中的数据对象。以下为目标链路，标为“待建”的模块尚未接入生产。

```text
[agent_comm.config.atomic_action_library.yaml] --AtomicActionLibrary(动作描述)--> [agent_comm.RosEnv]
[agent_comm.RosEnv] --AtomicActionLibrary--> [agent_comm.RosAgent(由 ros_agent_srv.main 创建)]
[agent_comm.ros_agent_srv.main] --get_obs()--> [agent_comm.RosEnv]
[agent_comm.RosEnv] --ObservationRequest--> [observation_manager.observation_manager_node]
[observation_manager.observation_manager_node] --ObservationResponse(当前观察)--> [agent_comm.RosEnv]
[agent_comm.RosEnv] --当前观察--> [agent_comm.ros_agent_srv.main]
[人工输入] --HumanInput("去三号塔看看有没有异物，拍张照，然后回来")--> [agent_comm.ros_agent_srv.main]
[agent_comm.ros_agent_srv.main] --HumanInput + 当前观察--> [agent_comm.RosAgent.get_action]
[agent_comm.RosAgent.get_action] --LLMRequest(prompt)--> [agent_comm.llm_node]
[agent_comm.llm_node] --LLMResponse(逐行 JSON 行为文本)--> [agent_comm.RosAgent.get_action]
[agent_comm.RosAgent.get_action] --行为文本--> [agent_comm.ros_agent_srv.main]
[agent_comm.ros_agent_srv.main] --行为文本--> [agent_comm.RosEnv.step]
[agent_comm.RosEnv.step] --ExecuteBehaviorRequest(behavior=行为文本)--> [behavior_executor.sequence_executor]

[behavior_executor.sequence_executor] --AtomicActionRequest(input="三号塔", prev_output="")--> [外部 resolve_poi 服务；未实现]
[外部 resolve_poi 服务；未实现] --AtomicActionResponse(output=目标位置)--> [behavior_executor.sequence_executor]
[behavior_executor.sequence_executor] --AtomicActionRequest(input=飞行参数, prev_output=目标位置)--> [flight_agent_ros.flight_atomic_action_server；待建]  (goto)
[flight_agent_ros.flight_atomic_action_server] --AtomicActionResponse(success, output=到达证据)--> [behavior_executor.sequence_executor]
[behavior_executor.sequence_executor] --AtomicActionRequest(input=拍照参数, prev_output=到达证据)--> [外部 capture_photo 服务；未实现]
[外部 capture_photo 服务；未实现] --AtomicActionResponse(output=photo_id)--> [behavior_executor.sequence_executor]
[behavior_executor.sequence_executor] --AtomicActionRequest(input=识别参数, prev_output=photo_id)--> [外部 inspect_image 服务；未实现]
[外部 inspect_image 服务；未实现] --AtomicActionResponse(output=识别结论)--> [behavior_executor.sequence_executor]
[behavior_executor.sequence_executor] --AtomicActionRequest(input=返航参数, prev_output=识别结论)--> [flight_agent_ros.flight_atomic_action_server；待建]  (rtl)
[flight_agent_ros.flight_atomic_action_server] --AtomicActionResponse(success, output=返航证据)--> [behavior_executor.sequence_executor]

goto/rtl 共用的飞行原子动作内部链路（每次调用均执行）：
[flight_agent_ros.flight_atomic_action_server] --AtomicActionProposal + MissionConstraints + expected_mission_id + execution_id--> [flight_agent.components.safety.execution_gate]
[flight_agent.components.safety.execution_gate] --状态读取请求--> [flight_agent_ros.adapters.flight_execution_backend]
[flight_agent_ros.adapters.flight_execution_backend] --WorldState--> [flight_agent.components.safety.execution_gate]
[flight_agent.components.safety.execution_gate] --AtomicActionProposal + MissionConstraints + expected_mission_id + WorldState + ControlAuthority--> [flight_agent.components.safety.supervisor]
[flight_agent.components.safety.supervisor] --SafetyDecision + flight_agent.contracts.AtomicAction(仅批准时)--> [flight_agent.components.safety.execution_gate]
[flight_agent.components.safety.execution_gate] --flight_agent.contracts.AtomicAction(仅批准时)--> [flight_agent_ros.adapters.flight_execution_backend]
[flight_agent_ros.adapters.flight_execution_backend] --SkillResult--> [flight_agent.components.safety.execution_gate]
[flight_agent.components.safety.execution_gate] --SafetyDecision + SkillResult(拒绝时为 None)--> [flight_agent_ros.flight_atomic_action_server]

[behavior_executor.sequence_executor] --ExecuteBehaviorResponse(success, message, info)--> [agent_comm.RosEnv.step]
[agent_comm.RosEnv.step] --get_obs()--> [agent_comm.RosEnv]
[agent_comm.RosEnv] --ObservationRequest--> [observation_manager.observation_manager_node]
[observation_manager.observation_manager_node] --ObservationResponse(新观察)--> [agent_comm.RosEnv]
[agent_comm.RosEnv.step] --(obs, reward, done, info)；执行诊断仅作为飞行扩展保留--> [agent_comm.ros_agent_srv.main]
[agent_comm.ros_agent_srv.main] --等待下一轮 HumanInput--> [人工输入]
```

`ExecuteBehaviorRequest` 是整个序列的输入，`AtomicActionRequest` 是逐项调用的输入；`service_name` 选择 ROS 服务，不是 `AtomicActionRequest` 的字段。目标 `flight_agent.contracts.AtomicAction` 是 Safety 批准后的内部执行凭证，不是 `rosllm_srvs/srv/AtomicAction` 的未审查请求。飞行 Gate 只收冻结的 `MissionConstraints`，不要求完整 `MissionContract`；`expected_mission_id` 必须来自可信会话上下文，不能从 LLM 输出的 `AtomicActionProposal.mission_id` 回填。现有旧入口由 `MissionContract.mission_id` 提供该值；新 ROS 服务尚无可信会话来源，接入前必须设计并验证，不能仅改图后宣称可飞。原子动作仅向下一项传递紧邻前一项的 `output`；巡检照片和识别结论要独立保存，才能在返航后从新观察中取回。若任一步失败，序列停止；运行中安全处置不依赖序列尾部的 `rtl`。

## 5. 安全不变量

1. 普通物理动作没有对应的 `SafetyDecision=APPROVE` 与 `ApprovedSkillCommand` 不得派发；拒绝、读取失败、Trace 写入失败均不得误放行。
2. State 过期、State ID 不匹配、Authority 不可信、PX4 failsafe/健康证据不满足、越围栏/高度/半径、任务限速与飞控限速不一致时拒绝；具体字段、顺序与原因码以 `src/flight_agent/contracts/` 和 `src/flight_agent/components/safety/` 的实现及 `contracts/` 的设计文档核对。
3. 对 `UNKNOWN` Authority 必须维持拒绝。接入真实控制权来源前，不允许用测试注入的 `AGENT_ALLOWED` 宣称生产 Gate 可飞。
4. Safety 拒绝、执行失败和不确定验证必须分别保留事实，不得映射为任务成功。`INCONCLUSIVE` 不是成功。
5. 在途危险处置必须有独立于模型推理的路径；当前缺口写入 M7-S。任何测试不得用 PX4 原生 failsafe 覆盖这一缺口。
6. 简单的人工任务/反馈输入是 ROS-LLM 主链的一部分；产品级人工 Plan 审批与复杂操作界面另行设计。V1 自主测评不依赖逐 Skill 审批；接管/急停也不能绕过飞行 Safety。

## 6. 契约与数据流

现存核心模型位于 `src/flight_agent/contracts/models/`：`MissionRequest`、`MissionContract`、`MissionConstraints`、`WorldState`、`AtomicActionProposal`、`SafetyDecision`、`ApprovedSkillCommand`、`SkillResult`，以及旧主链的 `MissionPlan`、`PlanStep`、`MissionRunResult` 和占位 `VerificationResult`。`AtomicActionProposal` 是原 `SkillProposal` 的现名，仍表示 Safety 审查前的内部飞行动作提案。Gate 已改为接收 `MissionConstraints` 和独立的可信任务 ID；`FlightExecutionBackendProtocol` 是核心与 ROS 2 实现的调用边界。模型定义存在不代表相应完整业务已接入。

**目标命名决定**：Agent 对象统一叫 `RosAgent`；对外动作统一叫 `AtomicAction`。当前内部模型 `ApprovedSkillCommand` 目标改名为 `flight_agent.contracts.AtomicAction`，表示 Safety 批准后的单次动作执行凭证；ROS 服务 `rosllm_srvs/srv/AtomicAction` 接收尚未审查的动作请求。两者同名但属于不同模块和信任边界，不得互换或因改名取消审批。现有代码、导出、测试和测评数据仍使用旧名；迁移须经契约评审并同步更新全部引用。

目标 V1 每次动作至少关联任务、提案、决策、执行、状态验证 ID；Trace 要能按这些 ID 重建因果链。字段变动先审核契约和现有数据集，不以重命名作为功能完成证据。世界坐标、Home、NED/ENU 的实际映射以 `WorldState`、PX4 适配器和对应契约测试为准；不在规格中虚构未接入的状态来源。

## 7. 阶段与验收

| 阶段 | 状态（按当前仓库） | 下一验收条件 |
|---|---|---|
| M0 测评设计与骨架 | 完成基础结构 | Dev/Validation/Frozen Test 持续治理；基础结构不代表冻结结果 |
| M1 ROS 2/PX4/Gazebo 环境 | 已有阶段记录 | 重跑时记录冻结版本和真实 SITL 证据 |
| M2 WorldState/Trace 基础 | 已有阶段记录 | 状态时效与回放回归保持可用 |
| M3 确定性飞行动作 | 已有阶段记录 | 保留调试入口与 Backend 回归，不把它计作 Agent 任务成功 |
| [MX 主流程骨架贯通](plans/MX.md) | **进行中**（主链代码未接入） | 先贯通真实模块调用；分项工期与占位边界见阶段计划 |
| M4 Contract/Safety Gate | **进行中** | MX 骨架后收口可信 Authority、独立 Gate 装配及 PX4/Gazebo 受控链验证；MX 不替代 Safety 验收 |
| M5 ROS-LLM 有人主链 | 未开始 | 在 MX 已贯通的模块链上替换固定返回：真实 LLM 决策、动作描述与稳健行为解析、完整人类反馈；从生产入口和替身/SITL 均可追踪 |
| M6 飞行原子动作完成判定 | 未开始 | 五个飞行动作以状态证据产生成功/失败标志、超时与假成功测试；不再把占位 `INCONCLUSIVE` 当作已完成结果 |
| M7 人类反馈修正与失败报告 | 未开始 | 行为失败后停止序列、返回结果和新观察；仅在下一轮人工输入后让 Agent 生成修正行为。无人续跑移出 ROS-LLM 对齐基线 |
| M7-S 飞行运行中安全专项 | 未开始 | 独立运行中监测、抢占仲裁、处置 Trace 与故障 SITL 测试；不创建任务调度循环 |
| M8–M12 测评、消融与发布 | 未开始 | 完整 Episode Runner、冻结集、基线/消融、报告、Docker/CI；每阶段单独定义 DoD |

旧版项目总工期位于归档规格，因架构与阶段范围变化已失效。当前不承诺项目完成日期；MX 的分项工作量见 [阶段计划](plans/MX.md)，其他阶段待纵向切片后重估。

## 8. 测试、测评与证据

测试分清四类：组件单测、契约测试、从真实装配入口进入的替身集成测试、PX4/Gazebo SITL 端到端测试。替身测试证明软件调用流，不证明飞行；SITL 测试证明指定环境和场景，不证明真实飞机或适航。生产入口尚未接入时，不能以组件测试通过宣称闭环。

`agent_benchmark_sets/dev` 用于开发；`validation` 用于阶段检查；`frozen_test` 只在系统稳定后冻结。`eval_harness` 后续负责 Episode、故障注入、判分和报告，不与测评数据目录混同。任务成功、安全违规、恢复成功、延迟等指标需绑定 Git 提交、环境 manifest、PX4/模型/提示词/Safety 版本与测评集版本；当前没有可对外发布的数值。人工辅助结果与自主结果必须分开报告。

## 9. 开发与版本管理

开发流程、文档归属、审核阈值和版本管理规则以 [AGENT.md](AGENT.md) 为准；本章不另行定义同一规则。

## 10. 待确认事项

1. 可信 Authority 的生产来源与失联时行为；这直接决定 M4 是否能受控批准并完成 SITL 验收。
2. MX 先冻结上游 `ExecuteBehavior`、`get_observation` 与原子动作库的 ROS 2 接口映射及内部固定对象；M5 再替换真实 LLM/行为解析。序列上限、动作失败后飞行安全处置条件，以及人工下一轮输入的触发方式仍待确认。
3. `PlanStep` 是否仍适合作为新行为序列中的飞行动作记录，需在第 2.1 节所列旧模块迁移前确认。
4. M5/M7/M7-S 的实际工作量与时间表，待 M4 收口和第一条真实 Agent 纵向切片后重估；上游准确 commit 与序列执行对照测试待锁定。

这些是待决策或待实现项，不得在进度表中提前标为完成。
