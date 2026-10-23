'''Load system flight limits for the ROS flight action server.'''

from pathlib import Path

from pydantic import ValidationError

from flight_agent.contracts import MissionConstraints


def load_safety_constraints(path: Path) -> MissionConstraints:
    '''Read one explicit JSON file and reject missing or invalid limits.'''

    try:
        content = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as error:
        raise ValueError(f'cannot read safety constraints JSON: {path}') from error

    try:
        return MissionConstraints.model_validate_json(content)
    except ValidationError as error:
        raise ValueError(f'invalid safety constraints JSON: {path}') from error
