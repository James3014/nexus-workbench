from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from nexus_workbench.benchmark import (
    BenchmarkBundle,
    BenchmarkError,
    build_report,
    load_bundle,
    render_report,
)


ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "benchmarks" / "fixtures" / "smoke_bundle.json"
EXPECTED_REPORT = ROOT / "benchmarks" / "fixtures" / "smoke_report.json"


def load_raw() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def refresh_evaluator_hash(raw: dict, run_index: int) -> None:
    from nexus_workbench.benchmark import BenchmarkRun

    run = BenchmarkRun.from_dict(raw["runs"][run_index])
    for evaluation in raw["evaluations"]:
        if evaluation["run_id"] == run.run_id:
            evaluation["run_hash"] = run.run_hash
            return
    raise AssertionError(f"missing evaluator fixture for {run.run_id}")


class BenchmarkHarnessTests(unittest.TestCase):
    def test_smoke_report_is_byte_stable_and_matches_checked_snapshot(self) -> None:
        bundle = BenchmarkBundle.from_dict(load_raw())
        first = render_report(bundle)
        second = render_report(bundle)
        expected = EXPECTED_REPORT.read_text(encoding="utf-8")
        self.assertEqual(first, second)
        self.assertEqual(first, expected)
        self.assertTrue(first.endswith("\n"))
        self.assertEqual(first.count("\n"), 1)

    def test_smoke_aggregate_matches_hand_checked_values(self) -> None:
        report = build_report(BenchmarkBundle.from_dict(load_raw()))
        baseline = report["aggregate"]["BASELINE"]
        workbench = report["aggregate"]["WORKBENCH"]
        delta = report["aggregate"]["delta_workbench_minus_baseline"]

        self.assertEqual(report["pair_count"], 2)
        self.assertTrue(report["contains_synthetic_cases"])
        self.assertFalse(report["effectiveness_claim_allowed"])
        self.assertEqual(report["claim_ceiling"], "G3_BENCHMARK_HARNESS_VALIDATED_ONLY")

        self.assertEqual(baseline["root_cause_correct_bps"]["mean"], "5000/1")
        self.assertEqual(workbench["root_cause_correct_bps"]["mean"], "10000/1")
        self.assertEqual(baseline["task_complete_bps"]["mean"], "5000/1")
        self.assertEqual(workbench["task_complete_bps"]["mean"], "10000/1")
        self.assertEqual(baseline["evidence_coverage_bps"]["mean"], "7500/1")
        self.assertEqual(workbench["evidence_coverage_bps"]["mean"], "10000/1")
        self.assertEqual(baseline["repeated_actions"]["mean"], "1/2")
        self.assertEqual(workbench["repeated_actions"]["mean"], "0/1")
        self.assertEqual(baseline["total_tokens"]["mean"], "175/1")
        self.assertEqual(workbench["total_tokens"]["mean"], "200/1")
        self.assertEqual(baseline["wall_time_ms"]["mean"], "950/1")
        self.assertEqual(workbench["wall_time_ms"]["mean"], "975/1")

        self.assertEqual(delta["root_cause_correct_bps"]["mean"], "5000/1")
        self.assertEqual(delta["task_complete_bps"]["mean"], "5000/1")
        self.assertEqual(delta["false_conclusions"]["mean"], "-1/2")
        self.assertEqual(delta["evidence_coverage_bps"]["mean"], "2500/1")
        self.assertEqual(delta["tool_calls"]["mean"], "0/1")
        self.assertEqual(delta["repeated_actions"]["mean"], "-1/2")
        self.assertEqual(delta["total_tokens"]["mean"], "25/1")
        self.assertEqual(delta["wall_time_ms"]["mean"], "25/1")

    def test_mismatched_model_identity_is_rejected(self) -> None:
        raw = load_raw()
        raw["runs"][1]["model_identity"] = "different-model"
        refresh_evaluator_hash(raw, 1)
        with self.assertRaisesRegex(BenchmarkError, "model_identity differs"):
            build_report(BenchmarkBundle.from_dict(raw))

    def test_mismatched_provider_or_settings_are_rejected(self) -> None:
        raw = load_raw()
        raw["runs"][1]["provider_identity"] = "different-provider"
        refresh_evaluator_hash(raw, 1)
        with self.assertRaisesRegex(BenchmarkError, "provider_identity differs"):
            build_report(BenchmarkBundle.from_dict(raw))

        raw = load_raw()
        raw["runs"][1]["model_settings_hash"] = "f" * 64
        refresh_evaluator_hash(raw, 1)
        with self.assertRaisesRegex(BenchmarkError, "model_settings_hash differs"):
            build_report(BenchmarkBundle.from_dict(raw))

    def test_source_revision_mismatch_is_rejected(self) -> None:
        raw = load_raw()
        raw["runs"][0]["source_revision"] = "1" * 40
        with self.assertRaisesRegex(BenchmarkError, "source revision mismatch"):
            build_report(BenchmarkBundle.from_dict(raw))

    def test_run_is_bound_to_exact_frozen_case_hash(self) -> None:
        raw = load_raw()
        raw["cases"][0]["task_input"] = "changed frozen task"
        raw["cases"][0]["oracle_requirements"] = ["different oracle"]
        raw["cases"][0]["evidence_universe"].append("NEW-EVIDENCE")
        with self.assertRaisesRegex(BenchmarkError, "case hash mismatch"):
            build_report(BenchmarkBundle.from_dict(raw))

    def test_duplicate_or_missing_arm_is_rejected(self) -> None:
        raw = load_raw()
        duplicate = copy.deepcopy(raw["runs"][0])
        duplicate["run_id"] = "run-root-baseline-duplicate"
        raw["runs"].append(duplicate)
        with self.assertRaisesRegex(BenchmarkError, "duplicate BASELINE arm"):
            build_report(BenchmarkBundle.from_dict(raw))

        raw = load_raw()
        removed_run = raw["runs"].pop(1)
        raw["evaluations"] = [
            item for item in raw["evaluations"] if item["run_id"] != removed_run["run_id"]
        ]
        with self.assertRaisesRegex(BenchmarkError, "requires exactly one BASELINE and one WORKBENCH"):
            build_report(BenchmarkBundle.from_dict(raw))

    def test_run_schema_rejects_agent_self_score_fields(self) -> None:
        raw = load_raw()
        raw["runs"][0]["root_cause_correct"] = True
        with self.assertRaisesRegex(BenchmarkError, "benchmark run keys mismatch"):
            BenchmarkBundle.from_dict(raw)

    def test_missing_or_tampered_evaluator_record_is_rejected(self) -> None:
        raw = load_raw()
        raw["evaluations"].pop(0)
        with self.assertRaisesRegex(BenchmarkError, "missing evaluator records"):
            build_report(BenchmarkBundle.from_dict(raw))

        raw = load_raw()
        raw["evaluations"][0]["run_hash"] = "0" * 64
        with self.assertRaisesRegex(BenchmarkError, "evaluation run_hash mismatch"):
            build_report(BenchmarkBundle.from_dict(raw))

    def test_evidence_outside_case_universe_is_rejected(self) -> None:
        raw = load_raw()
        raw["runs"][0]["evidence_refs"].append("NOT-IN-CASE")
        with self.assertRaisesRegex(BenchmarkError, "outside case universe"):
            build_report(BenchmarkBundle.from_dict(raw))

    def test_negative_counters_and_durations_fail_closed(self) -> None:
        for field in ("input_tokens", "output_tokens", "wall_time_ms"):
            raw = load_raw()
            raw["runs"][0][field] = -1
            with self.assertRaises(BenchmarkError):
                BenchmarkBundle.from_dict(raw)

        raw = load_raw()
        raw["evaluations"][0]["false_conclusion_count"] = -1
        with self.assertRaises(BenchmarkError):
            BenchmarkBundle.from_dict(raw)

    def test_protocol_version_mismatch_is_rejected(self) -> None:
        raw = load_raw()
        raw["runs"][0]["protocol_version"] = "other-protocol"
        with self.assertRaisesRegex(BenchmarkError, "protocol version mismatch"):
            build_report(BenchmarkBundle.from_dict(raw))

    def test_input_permutation_does_not_change_report_identity(self) -> None:
        raw = load_raw()
        raw["cases"].reverse()
        raw["runs"].reverse()
        raw["evaluations"].reverse()
        self.assertEqual(
            render_report(BenchmarkBundle.from_dict(raw)),
            EXPECTED_REPORT.read_text(encoding="utf-8"),
        )

    def test_case_semantics_are_bound_into_runs(self) -> None:
        raw = load_raw()
        raw["cases"][0]["task_input"] += " altered"
        with self.assertRaisesRegex(BenchmarkError, "case hash mismatch"):
            build_report(BenchmarkBundle.from_dict(raw))

        raw = load_raw()
        raw["cases"][0]["evidence_universe"].append("E-new")
        with self.assertRaisesRegex(BenchmarkError, "case hash mismatch"):
            build_report(BenchmarkBundle.from_dict(raw))

    def test_run_and_evaluator_payloads_reject_edge_whitespace_before_hashing(self) -> None:
        run_mutations = [
            ("tool_actions", lambda value: [value[0] + " ", *value[1:]]),
            ("evidence_refs", lambda value: [" " + value[0], *value[1:]]),
            ("result_ref", lambda value: value + " "),
        ]
        for field, mutate in run_mutations:
            raw = load_raw()
            raw["runs"][0][field] = mutate(raw["runs"][0][field])
            with self.assertRaisesRegex(BenchmarkError, "leading or trailing whitespace"):
                BenchmarkBundle.from_dict(raw)

        raw = load_raw()
        raw["evaluations"][0]["notes_ref"] += " "
        with self.assertRaisesRegex(BenchmarkError, "leading or trailing whitespace"):
            BenchmarkBundle.from_dict(raw)

    def test_case_semantics_reject_edge_whitespace_before_hashing(self) -> None:
        raw = load_raw()
        raw["cases"][0]["task_input"] = "    " + raw["cases"][0]["task_input"]
        with self.assertRaisesRegex(BenchmarkError, "leading or trailing whitespace"):
            BenchmarkBundle.from_dict(raw)

        raw = load_raw()
        raw["cases"][0]["oracle_requirements"][0] += " "
        with self.assertRaisesRegex(BenchmarkError, "leading or trailing whitespace"):
            BenchmarkBundle.from_dict(raw)

        raw = load_raw()
        raw["cases"][0]["evidence_universe"][0] = " " + raw["cases"][0]["evidence_universe"][0]
        with self.assertRaisesRegex(BenchmarkError, "leading or trailing whitespace"):
            BenchmarkBundle.from_dict(raw)

    def test_duplicate_json_keys_fail_closed(self) -> None:
        original = FIXTURE.read_text(encoding="utf-8")
        mutated = original.replace(
            '"root_cause_correct": true,',
            '"root_cause_correct": false, "root_cause_correct": true,',
            1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "duplicate.json"
            path.write_text(mutated, encoding="utf-8")
            with self.assertRaisesRegex(BenchmarkError, "duplicate JSON key: root_cause_correct"):
                load_bundle(path)

    def test_malformed_nested_record_fails_with_benchmark_error(self) -> None:
        raw = load_raw()
        raw["runs"][0] = None
        with self.assertRaisesRegex(BenchmarkError, "benchmark run must be an object"):
            BenchmarkBundle.from_dict(raw)

    def test_direct_construction_freezes_sequence_inputs(self) -> None:
        from nexus_workbench.benchmark import (
            BenchmarkCase,
            BenchmarkEvaluation,
            BenchmarkRun,
        )

        requirements = ["oracle-a"]
        evidence = ["E1"]
        case = BenchmarkCase(
            case_id="immutable-case",
            category="fixture",
            provenance_kind="SYNTHETIC_FIXTURE",
            source_repository="example/repo",
            source_revision="1" * 40,
            protocol_version="v1",
            task_input="Inspect the immutable fixture.",
            oracle_requirements=requirements,
            evidence_universe=evidence,
        )
        requirements.append("oracle-b")
        evidence.append("E2")
        self.assertEqual(case.oracle_requirements, ("oracle-a",))
        self.assertEqual(case.evidence_universe, ("E1",))

        actions = ["repo.read:a", "repo.read:a"]
        refs = ["E1"]
        run = BenchmarkRun(
            run_id="immutable-run",
            case_id=case.case_id,
            case_hash=case.case_hash,
            arm="BASELINE",
            source_revision=case.source_revision,
            protocol_version=case.protocol_version,
            model_identity="model",
            provider_identity="provider",
            model_settings_hash="2" * 64,
            tool_actions=actions,
            evidence_refs=refs,
            input_tokens=1,
            output_tokens=1,
            wall_time_ms=1,
            result_ref="result",
        )
        actions.append("repo.read:b")
        refs.append("E2")
        self.assertEqual(run.tool_actions, ("repo.read:a", "repo.read:a"))
        self.assertEqual(run.evidence_refs, ("E1",))

        evaluation = BenchmarkEvaluation(
            evaluation_id="immutable-eval",
            run_id=run.run_id,
            run_hash=run.run_hash,
            evaluator_identity="external-evaluator",
            root_cause_correct=True,
            task_complete=True,
            false_conclusion_count=0,
            notes_ref="notes",
        )
        cases = [case]
        runs = [run]
        evaluations = [evaluation]
        bundle = BenchmarkBundle(cases=cases, runs=runs, evaluations=evaluations)
        cases.clear()
        runs.clear()
        evaluations.clear()
        self.assertEqual(bundle.cases, (case,))
        self.assertEqual(bundle.runs, (run,))
        self.assertEqual(bundle.evaluations, (evaluation,))

    def test_cli_contract_writes_same_canonical_report(self) -> None:
        from nexus_workbench.benchmark import main

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "report.json"
            rc = main(["report", str(FIXTURE), "--output", str(output)])
            self.assertEqual(rc, 0)
            self.assertEqual(
                output.read_bytes(),
                EXPECTED_REPORT.read_bytes(),
            )
            self.assertEqual(main(["validate", str(FIXTURE)]), 0)


if __name__ == "__main__":
    unittest.main()
