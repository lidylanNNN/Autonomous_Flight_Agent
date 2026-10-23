'''System safety JSON loading tests.'''

import json
from pathlib import Path

import pytest
from flight_agent_ros.safety_config import load_safety_constraints

from flight_agent.contracts import MissionConstraints, SkillName


def test_load_safety_constraints_accepts_explicit_example_path() -> None:
    example = (
        Path(__file__).resolve().parents[4] / 'configs' / 'flight_safety.example.json'
    )

    constraints = load_safety_constraints(example)

    assert isinstance(constraints, MissionConstraints)
    assert constraints.max_altitude_m == 50.0
    assert constraints.allowed_skills == tuple(SkillName)


@pytest.mark.parametrize('relative_path', ['missing.json', 'directory'])
def test_load_safety_constraints_rejects_unreadable_path(
    tmp_path: Path, relative_path: str
) -> None:
    (tmp_path / 'directory').mkdir()

    with pytest.raises(ValueError, match='cannot read safety constraints JSON'):
        load_safety_constraints(tmp_path / relative_path)


def test_load_safety_constraints_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / 'invalid.json'
    path.write_text('{broken', encoding='utf-8')

    with pytest.raises(ValueError, match='invalid safety constraints JSON'):
        load_safety_constraints(path)


@pytest.mark.parametrize(
    ('field_name', 'value'),
    [('unexpected', True), ('max_altitude_m', -1.0)],
)
def test_load_safety_constraints_rejects_invalid_fields(
    tmp_path: Path, field_name: str, value: bool | float
) -> None:
    example = (
        Path(__file__).resolve().parents[4] / 'configs' / 'flight_safety.example.json'
    )
    payload = json.loads(example.read_text(encoding='utf-8'))
    payload[field_name] = value
    path = tmp_path / 'invalid.json'
    path.write_text(json.dumps(payload), encoding='utf-8')

    with pytest.raises(ValueError, match='invalid safety constraints JSON'):
        load_safety_constraints(path)
