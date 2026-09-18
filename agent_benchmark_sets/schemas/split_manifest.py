'''Mission set split manifest schema。'''

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MissionSplitManifest(BaseModel):
    '''定义 dev、validation、frozen_test 的 split manifest。'''

    model_config = ConfigDict(populate_by_name=True, extra='forbid')

    manifest_id: str = Field(min_length=1)
    spec_version: str = Field(min_length=1)
    taxonomy_doc: str = Field(min_length=1)
    schema_path: str = Field(alias='schema', min_length=1)
    split: Literal['dev', 'validation', 'frozen_test']
    status: Literal['draft', 'frozen']
    split_method: Literal['manual_family_level', 'family_level_random_split']
    source_commit: str | None
    random_seed: int | None
    environment_manifest: str = Field(min_length=1)
    case_count: int = Field(ge=0)
    family_count: int = Field(ge=0)
    family_ids: list[str]
    case_files: list[str]
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def counts_must_match_lists(self) -> MissionSplitManifest:
        '''校验 manifest 的数量字段与列表长度一致。'''

        if self.case_count != len(self.case_files):
            raise ValueError('case_count must equal len(case_files)')
        if self.family_count != len(self.family_ids):
            raise ValueError('family_count must equal len(family_ids)')
        if self.status == 'frozen' and self.source_commit is None:
            raise ValueError('frozen split must record source_commit')
        if self.split_method == 'family_level_random_split' and self.random_seed is None:
            raise ValueError('random split must record random_seed')
        return self
