# Autonomous Flight Agent 开发规格 v2.1

> 状态：待评审。实现基线：2026-10-16 的 `8a13b81`；架构对齐依据为 ROS-LLM 论文 v3 与本次核对的开源主分支。本文只修订设计，不代表代码迁移已完成。
> 历史规格：[DEV_SPEC v1.44](docs/archive/DEV_SPEC_v1.44.md)。历史文件保留决策过程，不再作为当前实现或新开发的验收依据。
> 适用范围：V1 飞行机器人任务系统、仓库结构、开发顺序、测评和版本管理。代码事实以实际生产入口及测试证据为准；本文目标不自动等于已实现。

## 1. 项目定义

本项目探索把自然语言任务变成可审查的飞行动作，并在 PX4/Gazebo 中验证执行结果。**目标主编排与 ROS-LLM 一致**：环境观察与人工输入进入 Agent，LLM 输出行为，Behavior Executor 执行原子动作，再获取环境观察并等待下一轮人工输入。ROS Noetic 到 ROS 2 Jazzy、机械臂到 PX4 飞行器的实现替换不得改变这个顺序。

目标使用场景是临时点位任务：操作员描述要去的位置和观察目的，系统选择受限动作、执行并返回可核对的飞行状态。相对手动逐条操控或预先制作固定航线，潜在收益是减少任务准备和重复操作；**节省的人力、时间与成本尚无实测，不能在对外材料中写成已取得的效果**。

系统边界：LLM 只负责任务理解与高层动作选择；确定性 Safety 决定物理动作是否允许；执行后由独立观测验证结果；ROS 2 负责通信与适配；PX4 保持姿态、导航控制和原生 failsafe。Agent 不输出电机、推力、角速率或姿态闭环指令，不替代飞控固件。V1 不包含真实飞机测试、多机协同、视觉搜索或适航声明。

设计依据分三层，不混称为同一技术方案：

1. **系统调用流**：以 [ROS-LLM 开源项目](https://github.com/huawei-noah/HEBO/tree/master/ROSLLM)的 Agent、Observation Manager、Behavior Executor、Atomic Action Library 及人工反馈闭环为基线。不能把本项目自行设计的 Hooks、MissionService 或无人续跑称为上游架构。
2. **产品交互决策点**：参考 [DJI FlightHub 2 Copilot 公开手册](https://fh.dji.com/user-manual/cn/copilot-assistant/copilot-remote-control.html)讨论计划确认、接管和紧急停止；这不代表知道 DJI 的内部实现，也不把尚未实现的交互写成当前能力。
3. **飞行行为与安全条件**：以冻结的 PX4/ROS 2 接口、本仓库代码及 SITL 实测为准。产品参照不得覆盖确定性安全边界或 PX4 原生 failsafe。

### 1.1 V1 目标状态与对齐范围

V1 目标：初始观察 -> 人工任务 -> Agent 根据观察和动作库生成行为序列 -> 执行器逐项执行、失败即停 -> 返回结果并更新观察 -> 等待人工反馈。任务成功须有状态证据，整条链须能在 PX4/Gazebo 测评。

**主调用拓扑与 ROS-LLM 相同**；V1 先验收 JSON 动作序列。行为树、状态机、代码执行和示教扩库未实现前，不声称全功能等价。无人自动续跑从 V1 删除。

飞行 Gate、PX4 Backend、状态完成判定和在途监测是**飞行适配扩展**，不是 ROS-LLM 原生模块；它们不改变主调用拓扑。

### 1.2 源码证据与一一映射

| ROS-LLM 依据 | 上游职责 | 本项目目标对应物 | 当前状态 |
|---|---|---|---|
| [`ros_agent_srv` 主循环](https://github.com/huawei-noah/HEBO/blob/master/ROSLLM/agent_comm/scripts/ros_agent_srv) | 先 `get_obs()`；等待人工输入；用动作库、观察和输入构造 prompt；调用 LLM；`env.step()` 执行行为后再次 `get_obs()` | 唯一的有人任务入口与 Agent/Environment 两个职责 | 未实现；现有 `mission_cli` 只进固定语句 `MissionService` |
| [`agent_comm`](https://github.com/huawei-noah/HEBO/tree/master/ROSLLM/agent_comm) | LLM 服务与 Agent 通信 | LLM Provider 和 prompt/行为提取 | 未实现 |
| [`observation_manager`](https://github.com/huawei-noah/HEBO/tree/master/ROSLLM/observation_manager) | 聚合各观察服务，通过统一 `get_observation` 返回文本观察 | 由 `WorldState` 等来源构造统一观察，不让 Agent 直接消费 PX4 Topic | 尚无统一观察入口；现有状态聚合只是输入基础 |
| [`behavior_executor`](https://github.com/huawei-noah/HEBO/tree/master/ROSLLM/behavior_executor) | `ExecuteBehavior` 服务接收行为；序列/行为树/代码等执行器返回成功标志与消息 | ROS 2 行为执行器；V1 先实现序列模式，逐项调用原子动作、失败即停止 | 未实现；旧 `MissionService` 的 `PlanStep` 循环不能冒充它 |
| [论文 §3.1、§3.5](https://arxiv.org/html/2406.19741v3) | 带名称、类型、描述和输入/输出的原子动作库；JSON 序列中的动作依次执行，前项输出可传给后项 | 飞行原子动作描述库及 ROS 2 action/service 接口，内部复用 Gate/Backend | Skill Registry 与 Backend 可复用，尚无上游式动作描述与执行接口 |
| [论文 §3.2–§3.4](https://arxiv.org/html/2406.19741v3) | 文本观察、人工反馈和动作库共同进入每轮 prompt；一轮行为结束后再等待人 | 飞行状态文本化、人工下一轮反馈 | 未实现 |

上游论文与 README 描述序列执行，但当前 `ros_agent_srv` 的公开代码使用正则提取一个 JSON 对象；**不能仅凭源码入口宣称多动作序列在该脚本中已被证明可运行**。本项目应以论文/Behavior Executor 契约定义行为模式，锁定上游提交并补可执行的序列对照测试；不得复制未经验证的解析细节。以上链接当前指向主分支，正式对齐验收前须记录确切上游 commit。

## 2. 当前实现基线

下表描述仓库当前状态，不是目标架构图。`docs/milestones/M0.md` 至 `M4.md` 保存阶段记录；未建立的 M5 以后里程碑文件不能当作现存交付物。

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
| `src/flight_agent/workflows/mission/service.py`、其 `__init__.py` 导出 | **删除** | 新 Agent 入口和 Behavior Executor 测试通过后，移除固定语句与 `PlanStep` 调度 |
| `mission_flow_model.py` 中的 `MissionPlan`、`PlanStep`、`MissionRunResult` 及公共导出 | **删除这些类型** | 迁移 ROS CLI 和测试引用；保留 `MissionRequest`，复核 `VerificationResult` |
| `mission_cli.py`、ROS 包的 `setup.py` | **改造** | 接入有人入口、Observation Manager 和 Behavior Executor；M3 直连调试入口独立保留 |
| `tests/integration/test_mission_flow.py`、ROS 包的 `test_mission_cli.py` | **删除旧测试，编写新集成测试** | 新测试从生产入口覆盖观察、人工输入、LLM、执行、再观察和失败停止 |
| `components/skills/registry.py`、`skill_model.py` | **保留并扩展** | 动作定义补齐名称、类型、描述、输入/输出，供 prompt 与执行器共用 |
| `components/vehicle/state/`、`Px4WorldStateAdapter` | **保留并接入观察层** | 新建统一观察入口；行为执行后再次读取 |
| `components/safety/`、`safety_model.py`、PX4 Backend/adapters | **保留** | 放在飞行原子动作内部；物理动作仍须经 Gate 和状态判定 |
| `components/tracing/`、`agent_benchmark_sets/`、`eval_harness/` | **保留并扩展** | 提供审计与测评，不参与任务决策 |
| 旧 `human_approval` 组件、模型、协议、Mock、公共导出及专属测试 | **删除** | 逐 Proposal 审批不在 V1；历史由 Git 保存，未来产品审批另行设计 |

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
docs/、manifests/、configs/           阶段记录、环境声明和配置；预留配置不自动生效
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
      ros_agent_srv.py                 [待建；唯一有人 Agent/Environment 主循环]
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
      sequence_executor.py            [待建；ExecuteBehavior 序列执行]
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
    flight_agent_ros/adapters/
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
  workflows/mission/                   [待删除；旧 MissionService]
agent_benchmark_sets/                 [已存在；Agent 测评集]
eval_harness/                         [已存在；测评执行辅助]
tests/                                [已存在；迁移后新增主链集成测试]
```

上游的 `behavior_tree_executor`、`code_executor`、视觉节点、示例包及 `GetUserInput.srv` 不进入 V1 文件树；有人入口先沿用上游 `ros_agent_srv` 的终端输入方式。V1 只实现序列模式。上述路径固定职责和迁移目标，**不表示已复制或实现上游代码**。`rosllm_srvs` 的字段须在实现批次核对 ROS 2 和飞行动作语义。

| 已有模块 | 目标位置/名称 | 处理 |
|---|---|---|
| `WorldStateAggregator`、`Px4WorldStateAdapter` | 保持原名，供 `observation_manager_node` 读取 | 已完成状态聚合；不得直接改称 Observation Manager |
| `SKILL_REGISTRY` 和五种动作映射 | 保持 `skills/registry.py`，供 `atomic_action_library.yaml` 的生成/校验复用 | 已有名称、描述、参数 schema 与命令映射；上游式描述文件和动作输出仍待实现 |
| `SafetyExecutionGate`、`Px4Ros2FlightExecutionBackend` 和 PX4 adapters | 保持飞行/PX4 名称，由 `AtomicAction` 服务实现调用 | 已有飞行部件；不改称 `behavior_executor` |
| `mission_cli.py`、`MissionService` | 新建 `agent_comm/agent_comm/ros_agent_srv.py`；旧文件按 §2.1 删除 | 固定语句入口不是上游 Agent；不能只改文件名 |
| `px4_command_plan_executor.py` | 保持原名；上层新建 `behavior_executor/behavior_executor/sequence_executor.py` | 前者执行动作内 PX4 命令，后者执行行为中的原子动作序列 |

**命名规则**：已完成且语义相同的模块在迁入目标包时同步改名并更新导入、入口和测试。上表中的已实现模块是飞行适配或基础能力，其名称已与目标树一致；不能为凑同名把它们改称尚未实现的上游模块。新模块只有生产调用链接入并通过集成测试后才标为已实现。

## 4. ROS-LLM 对齐的目标调用链

主链只采用 ROS-LLM 的一个环境步：先有 Observation，随后等待人输入；Agent 根据二者及动作库生成行为；Behavior Executor 执行完整行为或遇错停止；环境再获取 Observation；下一轮仍等待人反馈。LLM 不在每个原子动作后自动重规划，也不由执行器或 Runtime 建立另一套任务循环。以上是目标，不是现有实现。

```text
Observation Manager.get_observation() -> 当前观察
人工输入 + 当前观察 + Atomic Action Library -> Agent prompt -> LLM
LLM 行为序列 -> Behavior Executor.execute_behavior()
  -> Atomic Action 1 -> ... -> Atomic Action N（首个失败即停止）
  -> 行为成功/失败标志及消息
Observation Manager.get_observation() -> 新观察 -> 等待下一轮人工输入

飞行原子动作的内部：参数解析 -> Safety Gate -> PX4 Backend
  -> 飞行状态完成判定 -> 原子动作成功/失败及输出。
运行中安全监测和 PX4 failsafe 是飞行平台的独立安全路径，
不选择下一行为，不改变上面的 Agent 环境步调度。
```

- **Agent/Environment** 对应上游 `RosAgent`/`RosEnv`：Agent 形成行为，Environment 执行一个行为并取得下一次观察；不让 `MissionService` 或 Hook 层成为第二决策者。
- **Observation Manager** 是 Agent 读取环境的统一边界。初始观察和行为后的观察都要经此路径；其来源可包括 PX4 状态，但不能把直接读取 `WorldState` 当作已实现该节点。
- **Atomic Action Library / Behavior Executor** 对应上游动作描述与执行服务。V1 序列模式必须逐项执行、传递必要输出、失败即停，并把行为级结果返回 Environment。
- **飞行原子动作** 内部复用确定性 Gate/Backend/状态完成判定。物理动作没有 `APPROVE` 不得派发；ACK 不是动作完成证据。这是公开标记的飞行适配差异，不是 ROS-LLM 原生 Safety 设计。
- **运行中安全路径** 只监测、抢占和处置飞行风险，不生成下一行为或推进人机任务步。PX4 原生 failsafe 仍独立存在。

**删除**通用 `PreToolUse`/`PostToolUse`/`OnToolError`/`Stop` Hooks 与第二套 Tool Invocation 工作流；它们不属于 ROS-LLM 主链。记录、超时、取消留在执行器/飞行原子动作内部。**删除** V1 无人续跑目标。

**删除旧 `MissionService`、固定语句解析、`PlanStep` 调度及验证/恢复占位。**顺序：先迁出 M4 Gate 装配，再验证 M5 新入口，最后删除旧代码与测试。M3 调试路径保持可用。

## 5. 安全不变量

1. 普通物理动作没有对应的 `SafetyDecision=APPROVE` 与 `ApprovedSkillCommand` 不得派发；拒绝、读取失败、Trace 写入失败均不得误放行。
2. State 过期、State ID 不匹配、Authority 不可信、PX4 failsafe/健康证据不满足、越围栏/高度/半径、任务限速与飞控限速不一致时拒绝；具体字段、顺序与原因码以 `contracts/` 和 `components/safety/` 为准。
3. 对 `UNKNOWN` Authority 必须维持拒绝。接入真实控制权来源前，不允许用测试注入的 `AGENT_ALLOWED` 宣称生产 Gate 可飞。
4. Safety 拒绝、执行失败和不确定验证必须分别保留事实，不得映射为任务成功。`INCONCLUSIVE` 不是成功。
5. 在途危险处置必须有独立于模型推理的路径；当前缺口写入 M7-S。任何测试不得用 PX4 原生 failsafe 覆盖这一缺口。
6. 简单的人工任务/反馈输入是 ROS-LLM 主链的一部分；产品级人工 Plan 审批与复杂操作界面另行设计。V1 自主测评不依赖逐 Skill 审批；接管/急停也不能绕过飞行 Safety。

## 6. 契约与数据流

现存核心模型位于 `src/flight_agent/contracts/models/`：`MissionRequest`、`MissionContract`、`WorldState`、`SkillProposal`、`SafetyDecision`、`ApprovedSkillCommand`、`SkillResult`，以及旧主链的 `MissionPlan`、`PlanStep`、`MissionRunResult` 和占位 `VerificationResult`。`FlightExecutionBackendProtocol` 是核心与 ROS 2 实现的调用边界。模型定义存在不代表相应完整业务已接入。

目标 V1 每次动作至少关联任务、提案、决策、执行、状态验证 ID；Trace 要能按这些 ID 重建因果链。字段变动先审核契约和现有数据集，不以重命名作为功能完成证据。世界坐标、Home、NED/ENU 的实际映射以 `WorldState`、PX4 适配器和对应契约测试为准；不在规格中虚构未接入的状态来源。

## 7. 阶段与验收

| 阶段 | 状态（按当前仓库） | 下一验收条件 |
|---|---|---|
| M0 测评设计与骨架 | 完成基础结构 | Dev/Validation/Frozen Test 持续治理；基础结构不代表冻结结果 |
| M1 ROS 2/PX4/Gazebo 环境 | 已有阶段记录 | 重跑时记录冻结版本和真实 SITL 证据 |
| M2 WorldState/Trace 基础 | 已有阶段记录 | 状态时效与回放回归保持可用 |
| M3 确定性飞行动作 | 已有阶段记录 | 保留调试入口与 Backend 回归，不把它计作 Agent 任务成功 |
| M4 Contract/Safety Gate | **进行中** | 可信 Authority 来源；独立于旧 MissionService 的 Gate 装配；拒绝与批准 Trace；真实 PX4/Gazebo 受控链验证及边界回归 |
| M5 ROS-LLM 有人主链 | 未开始 | 初始观察 -> 人工输入/动作库 -> LLM 行为 -> Behavior Executor 序列 -> 行为结果 -> 新观察 -> 等待人；从生产入口和替身/SITL 均可追踪 |
| M6 飞行原子动作完成判定 | 未开始 | 五个飞行动作以状态证据产生成功/失败标志、超时与假成功测试；不再把占位 `INCONCLUSIVE` 当作已完成结果 |
| M7 人类反馈修正与失败报告 | 未开始 | 行为失败后停止序列、返回结果和新观察；仅在下一轮人工输入后让 Agent 生成修正行为。无人续跑移出 ROS-LLM 对齐基线 |
| M7-S 飞行运行中安全专项 | 未开始 | 独立运行中监测、抢占仲裁、处置 Trace 与故障 SITL 测试；不创建任务调度循环 |
| M8–M12 测评、消融与发布 | 未开始 | 完整 Episode Runner、冻结集、基线/消融、报告、Docker/CI；每阶段单独定义 DoD |

**当前下一步先复核 M4 Gate 的可信 Authority 与执行边界，再搭 M5 的上游式最小纵向链**。M4 验收不能依赖尚不存在的 LLM；M5 的第一条测试必须走“初始观察 -> 人工输入 -> LLM/替身 -> Behavior Executor -> 再观察”，不能沿用固定语句 `MissionService` 作为主链。

旧版工期数字位于归档规格，因 M5 对齐、M7 人类反馈边界及 M7-S 范围变化已失效。当前不承诺完成日期；M4 收口后基于纵向切片重新估时。

## 8. 测试、测评与证据

测试分清四类：组件单测、契约测试、从真实装配入口进入的替身集成测试、PX4/Gazebo SITL 端到端测试。替身测试证明软件调用流，不证明飞行；SITL 测试证明指定环境和场景，不证明真实飞机或适航。生产入口尚未接入时，不能以组件测试通过宣称闭环。

`agent_benchmark_sets/dev` 用于开发；`validation` 用于阶段检查；`frozen_test` 只在系统稳定后冻结。`eval_harness` 后续负责 Episode、故障注入、判分和报告，不与测评数据目录混同。任务成功、安全违规、恢复成功、延迟等指标需绑定 Git 提交、环境 manifest、PX4/模型/提示词/Safety 版本与测评集版本；当前没有可对外发布的数值。人工辅助结果与自主结果必须分开报告。

## 9. 开发与版本管理

- 新功能先列生产入口、触发条件、输入输出、创建者/持有者、调用流和验证方式；优先最小纵向切片，再完善组件。不得把单测中的对象装配当作生产接入。
- 修改业务代码、契约、测试或配置前，按 `Agent.md` 汇报本批变更、对象/函数输入输出、数据流节点和验证；涉及结构、Safety 或跨模块边界时先等用户确认。用户仅要求讨论或文档评审时不得擅改代码。
- 每批完成后列实际生产调用链和未接入环节，给出对应文件位置与测试结果。未经用户明确要求，不提交或推送。
- `main` 保持可回归；采用短生命周期变更分支，禁止 force push `main`。提交使用中文说明并携带项目版本号；里程碑、冻结测评与发布须绑定 commit/tag/环境清单。具体版本号由当次变更确定，不把规格版本当软件发布版本。
- 影响架构、契约、安全边界、里程碑、测评协议、仓库结构或发布门槛时，先修订本规格或新增 `docs/adr/` 决策。历史规格保留在 `docs/archive/`；旧章节编号不是当前规范。

## 10. 待确认事项

1. 可信 Authority 的生产来源与失联时行为；这直接决定 M4 是否能受控批准并完成 SITL 验收。
2. M5 对上游 `ExecuteBehavior`、`get_observation` 与原子动作库的 ROS 2 接口映射；序列上限、动作失败后飞行安全处置条件，以及人工下一轮输入的触发方式。
3. 删除 `MissionService`、`MissionPlan`/`PlanStep` 和旧审批原型前，列出跨目录引用，并按 §2.1 的顺序迁移入口、契约和测试。
4. M5/M7/M7-S 的实际工作量与时间表，待 M4 收口和第一条真实 Agent 纵向切片后重估；上游准确 commit 与序列执行对照测试待锁定。

这些是待决策或待实现项，不得在进度表中提前标为完成。
