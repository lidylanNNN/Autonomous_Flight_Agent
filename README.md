# Autonomous Flight Agent

Autonomous Flight Agent adapts the [ROS-LLM](https://github.com/huawei-noah/HEBO/tree/master/ROSLLM) task-to-behavior-to-observation loop to a flight robot on ROS 2 Jazzy, PX4, and Gazebo. The first target is human-in-the-loop: a person enters a mission, the agent selects a bounded sequence of flight actions, and the executor returns observations after the sequence ends or fails. Unattended continuation comes later. This is an adaptation target, not a claim that the current code is a ROS 2 port of ROS-LLM.

The project has completed **M3 — Deterministic Flight Skill Executor** and is working on **M4 — Mission Contract + Safety Supervisor**. `DEV_SPEC.md` is the single source of truth for scope, architecture, repository structure, milestones, evaluation, versioning, and release gates.

## Project Value

Traditional flight control already solves stabilization, navigation, waypoint execution, geofencing, and failsafe behavior well. This project focuses on the layer above that: turning a semantic mission goal into a safe, verifiable sequence of flight skills.

The target is a closed-loop flight agent that can:

- understand natural-language mission intent;
- compose existing deterministic flight skills such as Takeoff, GoTo, Hold, RTL, and Land;
- first stop and report a failed action sequence to the operator, then later adapt autonomously when the unattended phase is implemented;
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
- **Recovery:** deterministic fallback policy plus mission-level replanning
- **Mission sets:** schema-driven dev / validation / frozen-test cases
- **Evaluation harness:** unit/contract/safety tests, agent simulation, PX4/Gazebo frozen mission set
- **Tooling:** `uv`, pytest, mypy, ruff, Docker / Docker Compose

## Target Architecture

```text
Human mission -> Agent selects a bounded atomic-action sequence
-> Executor calls runtime hooks and flight safety gate for each action
-> ROS 2/PX4 execution, stopping at the first failure
-> Deterministic Hold/RTL response when appropriate
-> Observation after the sequence ends -> next human input

Later, the agent may start the next sequence itself from that observation.
```

LLM output is limited to mission-level planning and skill selection. The agent must not output actuator commands, body-rate commands, thrust commands, or maintain real-time control loops.

## Current Goal

Complete M4's trusted authority and safety gate validation, then build the assisted ROS-LLM-derived Agent/ROS 2 production loop in M5. M7 adds unattended continuation:

```text
Human mission -> Model-selected flight action sequence -> Safety gate per action
-> ROS 2/PX4 execution -> Vehicle observation -> Next human input
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
