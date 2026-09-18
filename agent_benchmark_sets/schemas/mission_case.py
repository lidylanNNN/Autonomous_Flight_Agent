"""MissionEvalCase schema for M0 development mission sets."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat


class MissionOutcome(BaseModel):
    '''描述 required 或 forbidden outcome。'''

    model_config = ConfigDict(extra='allow')

    type: str = Field(min_length=1)


class MissionFault(BaseModel):
    '''描述注入到测评 case 中的故障或扰动。'''

    model_config = ConfigDict(extra='allow')

    type: str = Field(min_length=1)


class MissionEvalCase(BaseModel):
    '''定义单条 M0 MissionEvalCase。'''

    case_id: str = Field(pattern=r'^DEV-T\d{2}-[NCF]$')
    instruction: str = Field(min_length=1)
    initial_world: str = Field(min_length=1)
    initial_vehicle_state: dict[str, Any]
    contract_overrides: dict[str, Any]
    injected_faults: list[MissionFault]
    required_outcomes: list[MissionOutcome] = Field(min_length=1)
    forbidden_outcomes: list[MissionOutcome] = Field(min_length=1)
    max_mission_time_s: PositiveFloat
    tags: list[str] = Field(min_length=1)


class MissionSetManifest(BaseModel):
    '''定义 mission set case manifest 的兼容读取模型。'''

    model_config = ConfigDict(populate_by_name=True, extra='allow')

    manifest_id: str = Field(min_length=1)
    spec_version: str = Field(min_length=1)
    taxonomy_doc: str = Field(min_length=1)
    schema_path: str = Field(alias='schema', min_length=1)
    split: Literal['dev', 'validation', 'frozen_test']
    case_count: int = Field(ge=0)
    case_files: list[str]
    notes: list[str] = Field(default_factory=list)
