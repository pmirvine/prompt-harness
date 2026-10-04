from __future__ import annotations

import hashlib
import json
import keyword
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ModelRef(BaseModel):
    provider: str
    model: str

    @classmethod
    def parse(cls, s: str) -> ModelRef:
        provider, sep, model = s.partition(":")
        if not sep:
            raise ValueError(f"invalid model reference (expected 'provider:model'): {s!r}")
        return cls(provider=provider, model=model)

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


class Provider(BaseModel):
    name: str
    base_url: str
    api_key_env: str
    headers: dict[str, str] = Field(default_factory=dict)
    max_tokens_param: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    enabled: bool = True
    timeout: float | None = None
    max_retries: int | None = None


class PromptVersion(BaseModel):
    system: str = ""
    template: str
    temperature: float | None = None
    max_tokens: int | None = None
    extra_params: dict[str, Any] = Field(default_factory=dict)
    documents_name: str = "documents"

    @field_validator("documents_name")
    @classmethod
    def _valid_documents_name(cls, v: str) -> str:
        if not v or not v.isidentifier() or keyword.iskeyword(v):
            raise ValueError(
                f"documents_name must be a valid Python identifier and not a keyword: {v!r}"
            )
        if v in ("input", "output"):
            raise ValueError(f"documents_name must not be 'input' or 'output': {v!r}")
        return v

    @property
    def hash(self) -> str:
        data = self.model_dump(mode="json")
        if data["documents_name"] == "documents":
            del data["documents_name"]  # keep hashes of existing prompts unchanged
        canonical = json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


class Match(BaseModel):
    pattern: str
    regex: bool = False


class Expectation(BaseModel):
    must_include: list[Match] = Field(default_factory=list)
    must_not_include: list[Match] = Field(default_factory=list)
    exact: str | None = None
    normalized: str | None = None
    json_output: bool = False
    json_schema: dict[str, Any] | None = None
    judge_prompt: str | None = None


class Case(BaseModel):
    name: str
    input: str = ""
    documents: list[str] = Field(default_factory=list)
    notes: str = ""
    expectation: Expectation = Field(default_factory=Expectation)


class Harness(BaseModel):
    name: str
    description: str = ""
    prompt: PromptVersion
    cases: list[Case]
    accepted_model: ModelRef | None = None
    accepted_run_id: int | None = None

    @model_validator(mode="after")
    def _unique_case_names(self) -> Harness:
        seen: set[str] = set()
        for c in self.cases:
            if c.name in seen:
                raise ValueError(f"duplicate case name: {c.name}")
            seen.add(c.name)
        return self


class CheckResult(BaseModel):
    name: str
    passed: bool
    reason: str = ""


Status = Literal["pass", "fail", "error", "manual", "judge_error"]


class CaseResult(BaseModel):
    id: int | None = None
    case_name: str
    status: Status
    output: str = ""
    request: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    checks: list[CheckResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    manual_verdict: bool | None = None


class Run(BaseModel):
    id: int | None = None
    harness: str
    prompt_hash: str
    model: ModelRef
    judge_model: ModelRef | None = None
    started_at: str
    finished_at: str | None = None
    results: list[CaseResult] = Field(default_factory=list)
