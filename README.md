# Autonomous Flight Agent

Autonomous Flight Agent is a mission-level robotics agent for autonomous UAV tasks. It is designed around ROS2, PX4, and Gazebo, with an LLM planner operating above deterministic flight control rather than inside the real-time control loop.

The project is currently in **M0 — Freeze Scope + Eval Spec**. `DEV_SPEC.md` is the single source of truth for scope, architecture, repository structure, milestones, evaluation, versioning, and release gates.

## Project Value

Traditional flight control already solves stabilization, navigation, waypoint execution, geofencing, and failsafe behavior well. This project focuses on the layer above that: turning a semantic mission goal into a safe, verifiable sequence of flight skills.

The core value is a closed-loop mission agent that can:

- understand natural-language mission intent;
- compose existing deterministic flight skills such as Takeoff, GoTo, Hold, RTL, and Land;
- adapt at runtime when a target is unsafe, blocked, stale, or failed;
- separate non-deterministic LLM planning from deterministic safety checks;
- verify success from observed vehicle state instead of trusting tool calls or PX4 ACK alone;
- produce traceable evaluation artifacts for task success, safety, recovery, and ablation.

The system is not intended to replace PX4 or low-level flight control. PX4 remains responsible for flight stability, commander behavior, native geofence, and failsafe logic. The agent decides what mission-level action should be proposed next; the safety supervisor decides whether it may execute.

## Technical Stack

- **Flight runtime:** PX4 SITL, ROS2, uXRCE-DDS
- **Simulator:** Gazebo Harmonic
- **Vehicle target:** x500 multicopter
- **ROS integration:** `rclpy`, `px4_msgs`, ROS2 adapter boundary
- **Agent core:** Python 3.11, Pydantic contracts, typed runtime protocols
- **Planning:** LLM provider abstraction with structured skill proposals
- **Safety:** deterministic Mission Contract and Safety Supervisor
- **Verification:** state-based verifier for flight skill outcomes
- **Recovery:** deterministic fallback policy plus mission-level replanning
- **Mission sets:** schema-driven dev / validation / frozen-test cases
- **Evaluation harness:** unit/contract/safety tests, agent simulation, PX4/Gazebo frozen mission set
- **Tooling:** `uv`, pytest, mypy, ruff, Docker / Docker Compose

## Target Architecture

```text
Observe
-> Plan
-> Propose
-> Safety Check
-> Act
-> Verify
-> Replan / Next
```

LLM output is limited to mission-level planning and skill selection. The agent must not output actuator commands, body-rate commands, thrust commands, or maintain real-time control loops.

## Current Goal

M0 must finish the evaluation and contract foundation before PX4 / ROS2 / Gazebo runtime implementation begins:

```text
MissionEvalCase Schema
-> 20-30 Dev Mission Cases
-> EnvironmentManifest
-> Dataset split / freeze rules
-> Schema validation script
-> docs/milestones/M0.md
```

## Repository Status

This repository currently contains the M0 skeleton defined by `DEV_SPEC.md`. Planned directories and interfaces in the spec should not be treated as implemented until the corresponding milestone DoD is complete.

Current initialized pieces:

- project metadata and `uv` lockfile;
- M0 safety and environment manifest placeholders;
- core `flight_agent` package skeleton;
- initial contract/runtime placeholder modules;
- mission-set schema and dataset directories;
- unit and contract test directories;
- M0 milestone document.

## Evaluation Boundary

Planned metrics such as Task Success, Hard Safety Violation Rate, and Recovery Success are not claimed results yet. They must be replaced by real numbers from versioned evaluation reports, tied to a Git commit, environment manifest, model/prompt version, safety policy version, PX4 commit, and frozen mission set.
