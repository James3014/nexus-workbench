from __future__ import annotations

import argparse
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

CASE_SCHEMA = "nexus.workbench.benchmark_case.v1"
RUN_SCHEMA = "nexus.workbench.benchmark_run.v1"
EVALUATION_SCHEMA = "nexus.workbench.benchmark_evaluation.v1"
BUNDLE_SCHEMA = "nexus.workbench.benchmark_bundle.v1"
REPORT_SCHEMA = "nexus.workbench.benchmark_report.v1"
G3_CLAIM_CEILING = "G3_BENCHMARK_HARNESS_VALIDATED_ONLY"

_ARMS = ("BASELINE", "WORKBENCH")
_PROVENANCE_KINDS = frozenset({"SYNTHETIC_FIXTURE", "HISTORICAL_FROZEN"})
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_METRIC_DIRECTIONS = {
    "root_cause_correct_bps": "HIGHER_IS_BETTER",
    "task_complete_bps": "HIGHER_IS_BETTER",
    "false_conclusions": "LOWER_IS_BETTER",
    "evidence_coverage_bps": "HIGHER_IS_BETTER",
    "tool_calls": "LOWER_IS_BETTER",
    "repeated_actions": "LOWER_IS_BETTER",
    "total_tokens": "LOWER_IS_BETTER",
    "wall_time_ms": "LOWER_IS_BETTER",
}


class BenchmarkError(ValueError):
    """Fail-closed validation error for G3 benchmark material."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _exact_keys(data: Mapping[str, Any], expected: set[str], label: str) -> None:
    if not isinstance(data, Mapping):
        raise BenchmarkError(f"{label} must be an object")
    actual = set(data)
    if actual != expected:
        raise BenchmarkError(
            f"{label} keys mismatch; extra={sorted(actual - expected)}, missing={sorted(expected - actual)}"
        )


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkError(f"{label} must be a non-empty string")
    return value.strip()


def _sha40(value: Any, label: str) -> str:
    text = _text(value, label).lower()
    if not _SHA40_RE.fullmatch(text):
        raise BenchmarkError(f"{label} must be a 40-character lowercase hex Git SHA")
    return text


def _sha256(value: Any, label: str) -> str:
    text = _text(value, label).lower()
    if not _SHA256_RE.fullmatch(text):
        raise BenchmarkError(f"{label} must be a 64-character lowercase hex digest")
    return text


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BenchmarkError(f"{label} must be an integer >= 0")
    return value


def _boolean(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise BenchmarkError(f"{label} must be boolean")
    return value


def _strings(
    value: Any,
    label: str,
    *,
    allow_empty: bool = True,
    unique: bool = True,
) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, list):
        raise BenchmarkError(f"{label} must be a list of strings")
    result = tuple(_text(item, label) for item in value)
    if not allow_empty and not result:
        raise BenchmarkError(f"{label} must not be empty")
    if unique and len(result) != len(set(result)):
        raise BenchmarkError(f"{label} must not contain duplicates")
    return result


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    category: str
    provenance_kind: str
    source_repository: str
    source_revision: str
    protocol_version: str
    task_input: str
    oracle_requirements: tuple[str, ...]
    evidence_universe: tuple[str, ...]
    schema: str = CASE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != CASE_SCHEMA:
            raise BenchmarkError("unsupported benchmark case schema")
        object.__setattr__(self, "case_id", _text(self.case_id, "case_id"))
        object.__setattr__(self, "category", _text(self.category, "category"))
        provenance = _text(self.provenance_kind, "provenance_kind").upper()
        if provenance not in _PROVENANCE_KINDS:
            raise BenchmarkError(f"unsupported provenance_kind: {provenance}")
        object.__setattr__(self, "provenance_kind", provenance)
        object.__setattr__(self, "source_repository", _text(self.source_repository, "source_repository"))
        object.__setattr__(self, "source_revision", _sha40(self.source_revision, "source_revision"))
        object.__setattr__(self, "protocol_version", _text(self.protocol_version, "protocol_version"))
        object.__setattr__(self, "task_input", _text(self.task_input, "task_input"))
        oracle_requirements = tuple(self.oracle_requirements)
        evidence_universe = tuple(self.evidence_universe)
        if not oracle_requirements:
            raise BenchmarkError("oracle_requirements must not be empty")
        if not evidence_universe:
            raise BenchmarkError("evidence_universe must not be empty")
        if len(oracle_requirements) != len(set(oracle_requirements)):
            raise BenchmarkError("oracle_requirements must not contain duplicates")
        if len(evidence_universe) != len(set(evidence_universe)):
            raise BenchmarkError("evidence_universe must not contain duplicates")
        for value in oracle_requirements:
            _text(value, "oracle_requirements")
        for value in evidence_universe:
            _text(value, "evidence_universe")
        object.__setattr__(self, "oracle_requirements", oracle_requirements)
        object.__setattr__(self, "evidence_universe", evidence_universe)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "case_id": self.case_id,
            "category": self.category,
            "provenance_kind": self.provenance_kind,
            "source_repository": self.source_repository,
            "source_revision": self.source_revision,
            "protocol_version": self.protocol_version,
            "task_input": self.task_input,
            "oracle_requirements": list(self.oracle_requirements),
            "evidence_universe": list(self.evidence_universe),
        }

    @property
    def case_hash(self) -> str:
        return content_hash(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BenchmarkCase":
        expected = {
            "schema", "case_id", "category", "provenance_kind", "source_repository",
            "source_revision", "protocol_version", "task_input", "oracle_requirements", "evidence_universe",
        }
        _exact_keys(data, expected, "benchmark case")
        return cls(
            schema=data["schema"],
            case_id=data["case_id"],
            category=data["category"],
            provenance_kind=data["provenance_kind"],
            source_repository=data["source_repository"],
            source_revision=data["source_revision"],
            protocol_version=data["protocol_version"],
            task_input=data["task_input"],
            oracle_requirements=_strings(data["oracle_requirements"], "oracle_requirements", allow_empty=False),
            evidence_universe=_strings(data["evidence_universe"], "evidence_universe", allow_empty=False),
        )


@dataclass(frozen=True)
class BenchmarkRun:
    run_id: str
    case_id: str
    case_hash: str
    arm: str
    source_revision: str
    protocol_version: str
    model_identity: str
    provider_identity: str
    model_settings_hash: str
    tool_actions: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    input_tokens: int
    output_tokens: int
    wall_time_ms: int
    result_ref: str
    schema: str = RUN_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != RUN_SCHEMA:
            raise BenchmarkError("unsupported benchmark run schema")
        object.__setattr__(self, "run_id", _text(self.run_id, "run_id"))
        object.__setattr__(self, "case_id", _text(self.case_id, "case_id"))
        object.__setattr__(self, "case_hash", _sha256(self.case_hash, "case_hash"))
        arm = _text(self.arm, "arm").upper()
        if arm not in _ARMS:
            raise BenchmarkError(f"unsupported arm: {arm}")
        object.__setattr__(self, "arm", arm)
        object.__setattr__(self, "source_revision", _sha40(self.source_revision, "source_revision"))
        object.__setattr__(self, "protocol_version", _text(self.protocol_version, "protocol_version"))
        object.__setattr__(self, "model_identity", _text(self.model_identity, "model_identity"))
        object.__setattr__(self, "provider_identity", _text(self.provider_identity, "provider_identity"))
        object.__setattr__(
            self,
            "model_settings_hash",
            _sha256(self.model_settings_hash, "model_settings_hash"),
        )
        tool_actions = tuple(self.tool_actions)
        evidence_refs = tuple(self.evidence_refs)
        for value in tool_actions:
            _text(value, "tool_actions")
        if len(evidence_refs) != len(set(evidence_refs)):
            raise BenchmarkError("evidence_refs must not contain duplicates")
        for value in evidence_refs:
            _text(value, "evidence_refs")
        object.__setattr__(self, "tool_actions", tool_actions)
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(self, "input_tokens", _nonnegative_int(self.input_tokens, "input_tokens"))
        object.__setattr__(self, "output_tokens", _nonnegative_int(self.output_tokens, "output_tokens"))
        object.__setattr__(self, "wall_time_ms", _nonnegative_int(self.wall_time_ms, "wall_time_ms"))
        object.__setattr__(self, "result_ref", _text(self.result_ref, "result_ref"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "case_hash": self.case_hash,
            "arm": self.arm,
            "source_revision": self.source_revision,
            "protocol_version": self.protocol_version,
            "model_identity": self.model_identity,
            "provider_identity": self.provider_identity,
            "model_settings_hash": self.model_settings_hash,
            "tool_actions": list(self.tool_actions),
            "evidence_refs": list(self.evidence_refs),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "wall_time_ms": self.wall_time_ms,
            "result_ref": self.result_ref,
        }

    @property
    def run_hash(self) -> str:
        return content_hash(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BenchmarkRun":
        expected = {
            "schema", "run_id", "case_id", "case_hash", "arm", "source_revision", "protocol_version",
            "model_identity", "provider_identity", "model_settings_hash", "tool_actions",
            "evidence_refs", "input_tokens", "output_tokens", "wall_time_ms", "result_ref",
        }
        _exact_keys(data, expected, "benchmark run")
        return cls(
            schema=data["schema"],
            run_id=data["run_id"],
            case_id=data["case_id"],
            case_hash=data["case_hash"],
            arm=data["arm"],
            source_revision=data["source_revision"],
            protocol_version=data["protocol_version"],
            model_identity=data["model_identity"],
            provider_identity=data["provider_identity"],
            model_settings_hash=data["model_settings_hash"],
            tool_actions=_strings(data["tool_actions"], "tool_actions", unique=False),
            evidence_refs=_strings(data["evidence_refs"], "evidence_refs"),
            input_tokens=data["input_tokens"],
            output_tokens=data["output_tokens"],
            wall_time_ms=data["wall_time_ms"],
            result_ref=data["result_ref"],
        )


@dataclass(frozen=True)
class BenchmarkEvaluation:
    evaluation_id: str
    run_id: str
    run_hash: str
    evaluator_identity: str
    root_cause_correct: bool
    task_complete: bool
    false_conclusion_count: int
    notes_ref: str
    schema: str = EVALUATION_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != EVALUATION_SCHEMA:
            raise BenchmarkError("unsupported benchmark evaluation schema")
        object.__setattr__(self, "evaluation_id", _text(self.evaluation_id, "evaluation_id"))
        object.__setattr__(self, "run_id", _text(self.run_id, "run_id"))
        object.__setattr__(self, "run_hash", _sha256(self.run_hash, "run_hash"))
        object.__setattr__(self, "evaluator_identity", _text(self.evaluator_identity, "evaluator_identity"))
        object.__setattr__(self, "root_cause_correct", _boolean(self.root_cause_correct, "root_cause_correct"))
        object.__setattr__(self, "task_complete", _boolean(self.task_complete, "task_complete"))
        object.__setattr__(
            self,
            "false_conclusion_count",
            _nonnegative_int(self.false_conclusion_count, "false_conclusion_count"),
        )
        object.__setattr__(self, "notes_ref", _text(self.notes_ref, "notes_ref"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "evaluation_id": self.evaluation_id,
            "run_id": self.run_id,
            "run_hash": self.run_hash,
            "evaluator_identity": self.evaluator_identity,
            "root_cause_correct": self.root_cause_correct,
            "task_complete": self.task_complete,
            "false_conclusion_count": self.false_conclusion_count,
            "notes_ref": self.notes_ref,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BenchmarkEvaluation":
        expected = {
            "schema", "evaluation_id", "run_id", "run_hash", "evaluator_identity",
            "root_cause_correct", "task_complete", "false_conclusion_count", "notes_ref",
        }
        _exact_keys(data, expected, "benchmark evaluation")
        return cls(
            schema=data["schema"],
            evaluation_id=data["evaluation_id"],
            run_id=data["run_id"],
            run_hash=data["run_hash"],
            evaluator_identity=data["evaluator_identity"],
            root_cause_correct=data["root_cause_correct"],
            task_complete=data["task_complete"],
            false_conclusion_count=data["false_conclusion_count"],
            notes_ref=data["notes_ref"],
        )


@dataclass(frozen=True)
class BenchmarkBundle:
    cases: tuple[BenchmarkCase, ...]
    runs: tuple[BenchmarkRun, ...]
    evaluations: tuple[BenchmarkEvaluation, ...]
    schema: str = BUNDLE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != BUNDLE_SCHEMA:
            raise BenchmarkError("unsupported benchmark bundle schema")
        cases = tuple(self.cases)
        runs = tuple(self.runs)
        evaluations = tuple(self.evaluations)
        if any(type(item) is not BenchmarkCase for item in cases):
            raise BenchmarkError("cases must contain BenchmarkCase records")
        if any(type(item) is not BenchmarkRun for item in runs):
            raise BenchmarkError("runs must contain BenchmarkRun records")
        if any(type(item) is not BenchmarkEvaluation for item in evaluations):
            raise BenchmarkError("evaluations must contain BenchmarkEvaluation records")
        object.__setattr__(self, "cases", cases)
        object.__setattr__(self, "runs", runs)
        object.__setattr__(self, "evaluations", evaluations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "cases": [
                item.to_dict() for item in sorted(self.cases, key=lambda item: item.case_id)
            ],
            "runs": [
                item.to_dict() for item in sorted(self.runs, key=lambda item: item.run_id)
            ],
            "evaluations": [
                item.to_dict()
                for item in sorted(self.evaluations, key=lambda item: item.evaluation_id)
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BenchmarkBundle":
        _exact_keys(data, {"schema", "cases", "runs", "evaluations"}, "benchmark bundle")
        for key in ("cases", "runs", "evaluations"):
            if not isinstance(data[key], list):
                raise BenchmarkError(f"{key} must be a list")
        return cls(
            schema=data["schema"],
            cases=tuple(BenchmarkCase.from_dict(item) for item in data["cases"]),
            runs=tuple(BenchmarkRun.from_dict(item) for item in data["runs"]),
            evaluations=tuple(BenchmarkEvaluation.from_dict(item) for item in data["evaluations"]),
        )


def _fraction(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def _metrics(case: BenchmarkCase, run: BenchmarkRun, evaluation: BenchmarkEvaluation) -> dict[str, int]:
    unique_actions = set(run.tool_actions)
    evidence_count = len(set(run.evidence_refs))
    return {
        "root_cause_correct_bps": 10000 if evaluation.root_cause_correct else 0,
        "task_complete_bps": 10000 if evaluation.task_complete else 0,
        "false_conclusions": evaluation.false_conclusion_count,
        "evidence_coverage_bps": (10000 * evidence_count) // len(case.evidence_universe),
        "tool_calls": len(run.tool_actions),
        "repeated_actions": len(run.tool_actions) - len(unique_actions),
        "total_tokens": run.input_tokens + run.output_tokens,
        "wall_time_ms": run.wall_time_ms,
    }


def _arm_summary(metric_rows: Sequence[Mapping[str, int]]) -> dict[str, Any]:
    count = len(metric_rows)
    if count == 0:
        raise BenchmarkError("cannot aggregate zero benchmark rows")
    result: dict[str, Any] = {}
    for metric in _METRIC_DIRECTIONS:
        total = sum(row[metric] for row in metric_rows)
        result[metric] = {
            "sum": total,
            "count": count,
            "mean": _fraction(Fraction(total, count)),
        }
    return result


def build_report(bundle: BenchmarkBundle) -> dict[str, Any]:
    cases: dict[str, BenchmarkCase] = {}
    for case in bundle.cases:
        if case.case_id in cases:
            raise BenchmarkError(f"duplicate case_id: {case.case_id}")
        cases[case.case_id] = case
    if not cases:
        raise BenchmarkError("benchmark bundle must contain at least one case")

    runs_by_id: dict[str, BenchmarkRun] = {}
    runs_by_case: dict[str, dict[str, BenchmarkRun]] = {case_id: {} for case_id in cases}
    for run in bundle.runs:
        if run.run_id in runs_by_id:
            raise BenchmarkError(f"duplicate run_id: {run.run_id}")
        case = cases.get(run.case_id)
        if case is None:
            raise BenchmarkError(f"run references unknown case_id: {run.case_id}")
        if run.case_hash != case.case_hash:
            raise BenchmarkError(f"case hash mismatch for run {run.run_id}")
        if run.source_revision != case.source_revision:
            raise BenchmarkError(f"source revision mismatch for run {run.run_id}")
        if run.protocol_version != case.protocol_version:
            raise BenchmarkError(f"protocol version mismatch for run {run.run_id}")
        unknown_evidence = sorted(set(run.evidence_refs) - set(case.evidence_universe))
        if unknown_evidence:
            raise BenchmarkError(
                f"run {run.run_id} references evidence outside case universe: {unknown_evidence}"
            )
        if run.arm in runs_by_case[run.case_id]:
            raise BenchmarkError(f"duplicate {run.arm} arm for case {run.case_id}")
        runs_by_id[run.run_id] = run
        runs_by_case[run.case_id][run.arm] = run

    evaluations_by_run: dict[str, BenchmarkEvaluation] = {}
    evaluation_ids: set[str] = set()
    for evaluation in bundle.evaluations:
        if evaluation.evaluation_id in evaluation_ids:
            raise BenchmarkError(f"duplicate evaluation_id: {evaluation.evaluation_id}")
        evaluation_ids.add(evaluation.evaluation_id)
        run = runs_by_id.get(evaluation.run_id)
        if run is None:
            raise BenchmarkError(f"evaluation references unknown run_id: {evaluation.run_id}")
        if evaluation.run_id in evaluations_by_run:
            raise BenchmarkError(f"duplicate evaluation for run {evaluation.run_id}")
        if evaluation.run_hash != run.run_hash:
            raise BenchmarkError(f"evaluation run_hash mismatch for run {evaluation.run_id}")
        evaluations_by_run[evaluation.run_id] = evaluation

    if set(evaluations_by_run) != set(runs_by_id):
        missing = sorted(set(runs_by_id) - set(evaluations_by_run))
        raise BenchmarkError(f"missing evaluator records for runs: {missing}")

    pairs: list[dict[str, Any]] = []
    arm_rows: dict[str, list[dict[str, int]]] = {arm: [] for arm in _ARMS}
    delta_rows: list[dict[str, int]] = []

    for case_id in sorted(cases):
        case = cases[case_id]
        arm_map = runs_by_case[case_id]
        if set(arm_map) != set(_ARMS):
            raise BenchmarkError(f"case {case_id} requires exactly one BASELINE and one WORKBENCH run")
        baseline = arm_map["BASELINE"]
        workbench = arm_map["WORKBENCH"]
        fairness_fields = ("model_identity", "provider_identity", "model_settings_hash")
        for field in fairness_fields:
            if getattr(baseline, field) != getattr(workbench, field):
                raise BenchmarkError(f"unfair pair for {case_id}: {field} differs across arms")

        baseline_eval = evaluations_by_run[baseline.run_id]
        workbench_eval = evaluations_by_run[workbench.run_id]
        baseline_metrics = _metrics(case, baseline, baseline_eval)
        workbench_metrics = _metrics(case, workbench, workbench_eval)
        delta = {
            metric: workbench_metrics[metric] - baseline_metrics[metric]
            for metric in _METRIC_DIRECTIONS
        }
        arm_rows["BASELINE"].append(baseline_metrics)
        arm_rows["WORKBENCH"].append(workbench_metrics)
        delta_rows.append(delta)

        pairs.append(
            {
                "case_id": case.case_id,
                "case_hash": case.case_hash,
                "category": case.category,
                "provenance_kind": case.provenance_kind,
                "source_repository": case.source_repository,
                "source_revision": case.source_revision,
                "protocol_version": case.protocol_version,
                "model_identity": baseline.model_identity,
                "provider_identity": baseline.provider_identity,
                "model_settings_hash": baseline.model_settings_hash,
                "baseline": {
                    "run_id": baseline.run_id,
                    "run_hash": baseline.run_hash,
                    "evaluation_id": baseline_eval.evaluation_id,
                    "evaluator_identity": baseline_eval.evaluator_identity,
                    "metrics": baseline_metrics,
                },
                "workbench": {
                    "run_id": workbench.run_id,
                    "run_hash": workbench.run_hash,
                    "evaluation_id": workbench_eval.evaluation_id,
                    "evaluator_identity": workbench_eval.evaluator_identity,
                    "metrics": workbench_metrics,
                },
                "delta_workbench_minus_baseline": delta,
            }
        )

    delta_summary: dict[str, Any] = {}
    for metric in _METRIC_DIRECTIONS:
        total = sum(row[metric] for row in delta_rows)
        delta_summary[metric] = {
            "sum": total,
            "count": len(delta_rows),
            "mean": _fraction(Fraction(total, len(delta_rows))),
        }

    return {
        "schema": REPORT_SCHEMA,
        "claim_ceiling": G3_CLAIM_CEILING,
        "effectiveness_claim_allowed": False,
        "input_bundle_hash": content_hash(bundle.to_dict()),
        "contains_synthetic_cases": any(
            case.provenance_kind == "SYNTHETIC_FIXTURE" for case in cases.values()
        ),
        "metric_directions": dict(_METRIC_DIRECTIONS),
        "pair_count": len(pairs),
        "pairs": pairs,
        "aggregate": {
            "BASELINE": _arm_summary(arm_rows["BASELINE"]),
            "WORKBENCH": _arm_summary(arm_rows["WORKBENCH"]),
            "delta_workbench_minus_baseline": delta_summary,
        },
    }


def load_bundle(path: str | Path) -> BenchmarkBundle:
    try:
        raw = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object_pairs,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"unable to read benchmark bundle: {path}") from exc
    if not isinstance(raw, Mapping):
        raise BenchmarkError("benchmark bundle root must be an object")
    return BenchmarkBundle.from_dict(raw)


def render_report(bundle: BenchmarkBundle) -> str:
    return canonical_json(build_report(bundle)) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Nexus Workbench deterministic G3 benchmark harness")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate a benchmark bundle")
    validate.add_argument("bundle")

    report = sub.add_parser("report", help="emit canonical deterministic benchmark report")
    report.add_argument("bundle")
    report.add_argument("--output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        bundle = load_bundle(args.bundle)
        if args.command == "validate":
            build_report(bundle)
            return 0
        rendered = render_report(bundle)
        if args.output:
            Path(args.output).write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0
    except BenchmarkError as exc:
        raise SystemExit(f"benchmark validation failed: {exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
