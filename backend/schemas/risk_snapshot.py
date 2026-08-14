"""Frozen, bounded schema for persisted generation-time risk snapshots."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


RISK_SNAPSHOT_RULE_VERSION_MAX_LENGTH = 128
RISK_SNAPSHOT_CODE_MAX_LENGTH = 128
RISK_SNAPSHOT_REASON_MAX_LENGTH = 1000
RISK_SNAPSHOT_SUGGESTION_MAX_LENGTH = 1000
RISK_SNAPSHOT_MAX_FINDINGS = 50

RiskSnapshotSeverity = Literal["low", "medium", "high"]
RiskSnapshotField = Literal["title", "body", "tags"]


class RiskFindingSnapshot(BaseModel):
    """One public, generation-time risk finding without matched text."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )

    code: str = Field(min_length=1, max_length=RISK_SNAPSHOT_CODE_MAX_LENGTH)
    severity: RiskSnapshotSeverity
    field: RiskSnapshotField
    reason: str = Field(
        min_length=1,
        max_length=RISK_SNAPSHOT_REASON_MAX_LENGTH,
    )
    suggestion: str = Field(
        min_length=1,
        max_length=RISK_SNAPSHOT_SUGGESTION_MAX_LENGTH,
    )

    @field_validator("code", "reason", "suggestion")
    @classmethod
    def reject_untrimmed_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("risk finding text must be normalized")
        return value


class RiskAssessmentSnapshot(BaseModel):
    """Versioned advisory result persisted with one successful generation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )

    rule_version: str = Field(
        min_length=1,
        max_length=RISK_SNAPSHOT_RULE_VERSION_MAX_LENGTH,
    )
    findings: tuple[RiskFindingSnapshot, ...] = Field(
        max_length=RISK_SNAPSHOT_MAX_FINDINGS,
    )

    @field_validator("rule_version")
    @classmethod
    def reject_untrimmed_rule_version(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("risk rule version must be normalized")
        return value

    @model_validator(mode="after")
    def reject_duplicate_rule_fields(self) -> "RiskAssessmentSnapshot":
        identities = {(item.code, item.field) for item in self.findings}
        if len(identities) != len(self.findings):
            raise ValueError("risk findings must be unique by code and field")
        return self


__all__ = [
    "RISK_SNAPSHOT_CODE_MAX_LENGTH",
    "RISK_SNAPSHOT_MAX_FINDINGS",
    "RISK_SNAPSHOT_REASON_MAX_LENGTH",
    "RISK_SNAPSHOT_RULE_VERSION_MAX_LENGTH",
    "RISK_SNAPSHOT_SUGGESTION_MAX_LENGTH",
    "RiskAssessmentSnapshot",
    "RiskFindingSnapshot",
    "RiskSnapshotField",
    "RiskSnapshotSeverity",
]
