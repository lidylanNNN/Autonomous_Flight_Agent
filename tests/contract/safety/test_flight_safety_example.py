'''Validate the example system safety constraints against the shared model.'''

from pathlib import Path

from flight_agent.contracts import MissionConstraints, SkillName


def test_flight_safety_example_matches_constraints_contract() -> None:
    example = (
        Path(__file__).resolve().parents[3] / 'configs' / 'flight_safety.example.json'
    )

    constraints = MissionConstraints.model_validate_json(example.read_text(encoding='utf-8'))

    assert constraints.allowed_skills == tuple(SkillName)
    assert constraints.requires_home_position is True
