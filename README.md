# Autonomous Flight Agent

Autonomous Flight Agent targets the [ROS-LLM](https://github.com/huawei-noah/HEBO/tree/master/ROSLLM) human-input, observation, behavior, execution, and feedback loop on a flight robot using ROS 2 Jazzy, PX4, and Gazebo. The target preserves that orchestration; flight-specific safety checks and PX4 execution live inside atomic actions. This is not a claim that the current code is a ROS 2 port of ROS-LLM or already has the same production call chain.

The project has completed **M3 — Deterministic Flight Skill Executor** and is working on **M4 — Mission Contract + Safety Supervisor**. [SPEC_DEV.md](SPEC_DEV.md) holds the overall design; [AGENT.md](AGENT.md) defines project rules. Detailed interfaces, complex-module designs, phase plans, and architectural decisions live in `contracts/`, `modules/`, `plans/`, and `ADR/` respectively. Evaluation supporting docs remain in `docs/eval_doc/`.

## Project Value

Traditional flight control already solves stabilization, navigation, waypoint execution, geofencing, and failsafe behavior well. This project focuses on the layer above that: turning a semantic mission goal into a safe, verifiable sequence of flight skills.

The target is a closed-loop flight agent that can:

- understand natural-language mission intent;
- compose existing deterministic flight skills such as Takeoff, GoTo, Hold, RTL, and Land;
- stop a failed action sequence, report the result and new observation, then use the operator's next feedback to produce a corrected behavior;
- separate non-deterministic LLM planning from deterministic safety checks;
- verify success from observed vehicle state instead of trusting tool calls or PX4 ACK alone;
- produce traceable evaluation artifacts for task success, safety, recovery, and ablation.

The system is not intended to replace PX4 or low-level flight control. PX4 remains responsible for flight stability, commander behavior, native geofence, and failsafe logic. The agent decides what mission-level action should be proposed next; the safety supervisor decides whether it may execute. ROS-LLM's public implementation uses ROS Noetic; this project retains ROS 2 Jazzy and adapts the architecture and core workflow rather than switching ROS versions.

## Technical Stack

- **Flight runtime:** PX4 SITL, ROS2, uXRCE-DDS
- **Simulator:** Gazebo Harmonic
- **Vehicle target:** x500 multicopter
- **ROS integration:** `rclpy`, `px4_msgs`, ROS2 adapter boundary
- **Agent core:** Python 3.12, Pydantic contracts, typed backend protocols
- **Planning:** LLM provider abstraction with structured skill proposals
- **Safety:** deterministic Mission Contract and Safety Supervisor
- **Verification:** state-based verifier for flight skill outcomes
- **Feedback:** human-guided correction after each completed or failed behavior; flight-specific deterministic safety response inside the execution boundary
- **Mission sets:** schema-driven dev / validation / frozen-test cases
- **Evaluation harness:** unit/contract/safety tests, agent simulation, PX4/Gazebo frozen mission set
- **Tooling:** `uv`, pytest, mypy, ruff, Docker / Docker Compose

## Target Architecture

```text
Initial observation -> human input + atomic-action library -> Agent/LLM behavior
-> Behavior Executor calls atomic actions in sequence, stopping at first failure
-> Flight atomic action: Safety Gate -> ROS 2/PX4 -> state-based result
-> Behavior result -> new observation -> wait for next human input
```

LLM output is limited to mission-level planning and skill selection. The agent must not output actuator commands, body-rate commands, thrust commands, or maintain real-time control loops.

## Current Goal

Complete M4's trusted authority and safety gate validation, then build the ROS-LLM-aligned human/agent/environment loop in M5. M7 covers correction after human feedback, not unattended continuation:

```text
Initial observation -> Human mission/feedback -> Model-selected behavior
-> Behavior Executor -> Safety-gated flight atomic actions -> PX4
-> Behavior result -> New observation -> Next human input
```

## Repository Status

M0-M3 are complete; M4 is in progress. Planned directories and interfaces in the spec should not be treated as implemented until the corresponding milestone DoD is complete.

Current implemented foundations include:

- project metadata and `uv` lockfile;
- mission-set schemas, validation, and evaluation split rules;
- ROS 2 Jazzy / PX4 / Gazebo SITL integration;
- vehicle state aggregation and trace recording;
- deterministic flight skills and execution backend;
- safety gate components and a scripted mission entry, not yet the Agent production loop.

## Evaluation Boundary

Planned metrics such as Task Success, Hard Safety Violation Rate, and Recovery Success are not claimed results yet. They must be replaced by real numbers from versioned evaluation reports, tied to a Git commit, environment manifest, model/prompt version, safety policy version, PX4 commit, and frozen mission set.
