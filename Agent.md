# Agent.md

本文档说明后续开发 Agent 应如何读取 `DEV_SPEC.md`。`DEV_SPEC.md` 是 Autonomous Flight Agent V1 的 Single Source of Truth；实现、测试、评测、文档、仓库结构和里程碑推进应优先以它为准。

## 可读取的信息

Agent 可以从 `DEV_SPEC.md` 读取以下项：

- 项目定义：Autonomous Flight Agent 是基于 ROS2 + PX4 + Gazebo 的自主飞行机器人任务执行系统。
- 当前状态、当前阶段和下一步：读取 `DEV_SPEC.md` 的 `Progress Management`、`Current Progress Board` 和对应 Milestone，不在本文档重复定义。
- V1 目标：可复现 SITL 环境、Flight Skills、Mission Contract、Safety Supervisor、State Verifier、Recovery / Replanning、Trace、Evaluation、Ablation、Docker / CI / Final Report。
- V1 非目标：LLM 不直接输出 actuator、attitude、body-rate、thrust；不维持实时控制环；不自研 PX4；不做 eVTOL / VTOL、Multi-UAV、真实飞机测试、复杂 UI 等扩展。
- 架构边界：LLM 只处于 Mission / Skill Level；PX4 保留底层飞控和原生 failsafe；Safety Supervisor 是执行前确定性仲裁层；State Verifier 根据实际状态判定执行结果。
- Runtime 架构：Observe -> Plan -> Propose -> Safety Check -> Act -> Verify -> Replan / Next。
- 控制时序：LLM loop、Skill execution loop、PX4 control loop 分离。
- 核心数据契约：`MissionRequest`、`MissionContract`、`WorldState`、`MissionPlan`、`SkillProposal`、`SafetyDecision`、`SkillResult`、`VerificationResult`、`TraceEvent`、`MissionEvalCase`。
- ROS2 / PX4 集成约束：PX4 与 ROS2 使用 uXRCE-DDS；Agent / Safety / Evaluation 不直接 import PX4 topic，必须通过 Adapter boundary。
- Flight Skills：Observe、Takeoff、GoTo、Hold、RTL、Land 及 Skill Registry。
- LLM Planner 责任：自然语言任务理解、高层计划生成、Skill 选择、失败后的任务级 replanning；不负责安全放行、实时控制、PX4 protocol correctness。
- Safety Supervisor 规则：按 Schema、Current State、Geofence、Flight Envelope、Command Sequence、Authority 等条件对每条 Proposal 做确定性校验；人工 Plan 审批属于自主测评后的产品交互。
- State Verifier 规则：Takeoff、GoTo、Hold、RTL、Land 的成功判定必须来自状态观测，而不是相信 LLM 或 PX4 ACK。
- Recovery / Replanning 设计：紧急安全行为由 deterministic recovery policy 兜底；LLM replanning 只在安全边界内做任务层调整。
- Trace 要求：完整记录 Mission、Plan、Proposal、Safety Decision、ROS2 Dispatch、PX4 ACK、Skill Result、Verification、Recovery 等事件。
- Mission Set 策略：测评集从 M0 开始持续建设，不等到 M8；`DEV_SPEC.md` 使用 `agent_benchmark_sets/` 保存 Agent 的 Dev / Validation / Frozen Test 测评任务、场景和数据划分。它不是 PX4 Mission 文件，也不是在线任务输入。
- Evaluation 策略：评估方法和评测执行逻辑与测评集分开，包含 Unit / Contract / Safety、Agent Simulation、PX4/Gazebo E2E 三层。
- Frozen Mission Set 原则：最终 Task Success、Hard Safety Violation、Recovery Success、Latency 等指标主要来自 PX4/Gazebo Frozen Mission Set。
- 自主测评边界：V1 主测评不接入人工审批或人工修正；人工辅助结果须与自主结果分开报告。人工 Plan 审批、操作界面与接管体验排在 M12 自主测评报告之后。
- Metrics 定义：Task Success、Hard Safety Violation Rate、Recovery Success、Valid Tool Call Rate、Latency、Safety Intervention Rate、PX4 Reject Rate、LLM Calls / Mission 等。
- Fault Injection 分层：Pure Software、ROS2 Adapter Fault Proxy、PX4/Gazebo Fault Scenario。
- Ablation 设计：B0 Scripted、B1 Agent Baseline、B2 +Safety、B3 +Verifier、B4 +Recovery / Replanning。
- Milestone 顺序和 DoD：M0-M12 的交付物、依赖、完成条件、文档路径和 release gates。
- Repository Skeleton：目标仓库结构、模块交付地图、mock delivery contract、最小 M0 skeleton 均已在 `DEV_SPEC.md` 第 21 章定义。`Agent.md` 不重复定义目录树，执行时应回到 `DEV_SPEC.md` 读取最新结构。
- 项目治理规则：SSOT、Git 策略、文件存储策略、ADR、版本规则、contract-first change、safety change management、dataset governance、dependency management、secrets、code quality、test governance、release / tagging。
- Safety Invariants：未经 `SafetyDecision=APPROVE` 的 Skill 不得执行；stale `WorldState` 不得批准飞行动作；越界 waypoint、超高度目标、PX4 failsafe active 等必须阻断。
- 发布门槛：Infrastructure、Safety Logic、Agent Validation、Frozen Test 四类 release gates。
- 简历/展示边界：Task Success / Safety / Recovery 数字目前是占位，必须由真实 evaluation report 替换后才能对外声称。

## 不应误读的信息

- `DEV_SPEC.md` 描述的是 V1 目标态和开发计划，不代表当前代码已经实现。
- `DEV_SPEC.md` 中的 Repository Skeleton 是目标结构和落地顺序，不代表当前仓库已经具备所有目录和文件。
- 任何性能指标、成功率、安全率、恢复率，在没有真实 Evaluation Report 前都不能写成已完成成果。
- Public benchmark 只能作为 UAV reasoning / scenario mapping 参考，不能替代 PX4/Gazebo closed-loop benchmark。
- LLM 不能绕过 Mission Contract、Safety Supervisor、State Verifier 或 PX4 failsafe；测评后若启用人工 Plan 审批，也不得绕过该审批。
- 为了赶进度，不允许删除 Safety、Verifier、Evaluation 或 Trace 这些核心边界。

## 仓库结构

仓库结构不在本文档中重新定义。Agent 需要仓库布局时，应读取 `DEV_SPEC.md`：

- 第 21 章 `Repository Skeleton`：核心代码、ROS2/PX4 adapter、mission sets、evaluation harness、tests 的隔离区域、依赖方向、硬规则。
- 第 21.1 节 `Final Repository Skeleton`：最终目标目录树。
- 第 21.2 节 `Module Delivery Map`：模块、代码路径、文档路径、最低测试和首次里程碑。
- 第 21.6 节 `M0 Minimal Skeleton`：当前 M0 应优先落地的最小结构。

若实际仓库结构与 `DEV_SPEC.md` 不一致，默认以 `DEV_SPEC.md` 为设计目标；只有在完成对应 Milestone 和 DoD 后，才能把目标结构视为已落地。

## 版本管理

版本管理方式已在 `DEV_SPEC.md` 第 21.8 章定义。Agent 执行时应遵守：

- 项目范围、架构、开发顺序以 `DEV_SPEC.md` 为准。
- 当前开发进度以 Progress Board 和 `docs/milestones/Mx.md` 为准，不以聊天记录为准。
- 架构决策写入 `docs/adr/ADR-xxx.md`。
- `main` 应保持可构建、可运行；功能开发使用短生命周期 `feature/*`，Bug 使用 `fix/*`，高风险实验使用 `experiment/*`。
- 禁止对 `main` force push。
- Milestone 完成后必须绑定 Git Commit，并按 `DEV_SPEC.md` 要求打 Tag。
- Frozen Test、Release、Ablation 结果必须绑定 Git Commit、Tag、环境 Manifest、测评集版本、模型/Prompt/Safety/PX4 等版本信息。
- 以下变化必须升级 `DEV_SPEC.md` 版本：Architecture、Contract、Milestone、Safety Boundary、Evaluation Protocol、Repository Structure、Release Gate。
- `DEV_SPEC.md` 始终表示当前版本，历史版本由 Git/Tag 保存。

## Agent 执行规则

1. 开始任何开发前，先读取 `DEV_SPEC.md` 对应章节。
2. 如果实现会改变 contract、safety、runtime boundary、evaluation schema 或 milestone scope，先更新 `DEV_SPEC.md` 或新增 ADR。
3. 当前 Milestone、开发重点和下一步必须从 `DEV_SPEC.md` 读取，不在本文档硬编码。
4. 每个 Milestone 只有满足交付物完成、DoD 满足、必要测试通过、trace/eval artifact 生成时，才能标记为 `DONE`。

## 开发原则：优先建立真实调用链

- 新功能默认按自顶向下的纵向切片开发：先确认生产入口、触发条件、关键调用链和预期输出，再实现最小可运行链路，随后逐步完善内部组件。不得把先写完所有独立组件当作默认开发顺序。
- 新增组件前说明由谁创建、由谁持有、在什么条件下调用，以及生产环境是否已接入；没有明确使用场景的组件不得提前批量开发。
- 明确区分生产入口调用和测试代码直接调用。组件单测通过不等于生产链路已接入；优先以调用实际生产装配路径的集成测试验证链路，单测用于补充边界与故障覆盖。
- 外部系统尚不可用时，可以用替身验证生产软件调用链，但必须标明替身边界；不得把替身测试称为 PX4/Gazebo 端到端验证。
- 每批完成后列出实际生产调用链，标注尚未接入的组件；若尚无生产入口，明确说明，不能宣称功能已闭环。

## 代码修改前汇报

每批修改业务代码、测试、配置或数据契约前，Agent 必须先向用户汇报本批范围；发现需要扩大范围时，先补充汇报再继续修改。汇报至少包含：

- **变更点**：当前行为、目标行为、涉及的文件或模块，以及本批不处理的内容。
- **调用链**：生产入口、触发条件、现有和目标调用流、预期输出；明确哪些环节尚未接入。
- **对象职责**：逐一说明新增或删除的类、模型、节点、适配器等对象的作用、创建者、持有者、调用条件和生产接入状态；没有则明确写“无”。
- **函数输入输出**：说明新增或改变的函数接收什么、返回什么，以及关键副作用或异常；没有则明确写“无”。
- **数据流变化**：用“来源 -> 处理节点 -> 去向”列出受影响的链路，标明新增或删除的节点及其输入输出；没有节点增删则明确写“无节点增删”。
- **验证方式**：列出本批准备运行的测试或检查。

汇报不等于请求批准：普通批次汇报后可实施；达到下述等待确认条件的批次，必须先获得用户确认。

## 小步变更与审核

每一批 Agent 改动必须只有一个可独立评审的目的，并且包含该目的所需的测试与文档同步。
行数是提醒 Agent 停下来拆分的软阈值，不是替代架构判断的硬规则。

- 默认批次不超过 300 行语义改动，且只涉及一个模块边界；完成测试后交由用户审阅。
- 预计 300–600 行语义改动，或同时影响两个模块边界时，在修改前汇报中补充风险，等待用户确认后再实施。
- 超过 600 行语义改动，或涉及 Contract、Safety、ROS2/PX4、顶层目录、外部依赖或数据集路径时，必须拆为多个可独立验证的批次；先提交结构变更单，等待用户确认。
- 纯机械改名、文件移动、格式化和生成文件不按其重复行数计入语义改动，但必须与功能、行为或 Contract 改动分批实施；如需提交，也应分别提交。
- 任何 Contract、Safety Boundary、Interface、依赖方向或 Repository Structure 变更，无论行数多少，都属于结构变更；在修改前汇报中补充原因、影响范围和回滚方式，等待用户确认。
- 每一批完成后，Agent 必须按修改前汇报逐项总结变更点、对象职责、函数输入输出和数据流变化，标出各项对应的代码文件与行号；列出实际生产调用链及未接入组件，区分生产路径验证与测试直接调用。未实现或偏离原汇报的内容须说明原因。另须报告 `git diff --stat`、改动文件清单、验证命令与结果；未经用户明确要求，不提交或推送。
