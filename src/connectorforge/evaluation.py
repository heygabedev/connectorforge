"""Independent expectations evaluated against isolated connector executions."""

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from connectorforge import __version__
from connectorforge.execution import PreviewInput, execute_preview
from connectorforge.problems import Problem

EVALUATOR_VERSION = "1"


class Success(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["success"]
    status: int = Field(ge=200, le=399)
    data: Any


class Failure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["error"]
    code: str = Field(min_length=1, max_length=64)


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    input: PreviewInput
    expected: Success | Failure = Field(discriminator="kind")


class EvaluationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")
    seed: int = Field(default=0, ge=0, le=2**32 - 1)
    cases: list[GoldenCase] = Field(min_length=1, max_length=1000)


async def evaluate(bundle: dict[str, Any], definition: EvaluationInput) -> dict[str, Any]:
    from connectorforge.catalog import fingerprint

    if len({case.id for case in definition.cases}) != len(definition.cases):
        raise Problem(422, "duplicate_case", "Golden case identifiers must be unique.")
    results = []
    for case in definition.cases:
        try:
            execution = await execute_preview(bundle, case.input)
            actual: dict[str, Any] = {
                "kind": "success",
                "status": execution["meta"]["status"],
                "data": execution["data"],
            }
        except Problem as error:
            actual = {"kind": "error", "code": error.code}
        results.append(
            {
                "id": case.id,
                "passed": actual == case.expected.model_dump(),
                "actual_hash": fingerprint(actual),
                "expected_hash": fingerprint(case.expected.model_dump()),
                "outcome": actual["kind"],
                "error": actual.get("code"),
            }
        )
        await asyncio.sleep(0)
    context = {
        "bundle": fingerprint(bundle),
        "dataset": fingerprint(definition.model_dump(exclude_unset=True)),
        "dataset_version": definition.dataset_version,
        "evaluator": EVALUATOR_VERSION,
        "compiler": bundle["compiled"]["compiler"],
        "application": __version__,
        "fixtures": fingerprint(bundle["fixtures"]),
        "policy": fingerprint(bundle["policy"]),
        "seed": definition.seed,
    }
    return {
        "passed": all(item["passed"] for item in results),
        "cases": results,
        "context": context,
        "fingerprint": fingerprint(context),
    }
