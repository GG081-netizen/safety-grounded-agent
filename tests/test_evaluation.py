"""Tests for Phase 12 evaluation readiness."""

from conversation_agent.evaluation import evaluate_policy_boundary, evaluate_rag_adapter


def test_rag_adapter_eval_runs_all_fixed_cases():
    report = evaluate_rag_adapter()
    names = [case.case_name for case in report.cases]
    assert names == [
        "external_success_with_citation",
        "external_success_without_citation",
        "external_failure_with_local_fallback",
        "external_failure_without_fallback",
        "blocked_request_no_rag_call",
        "local_provider_direct",
    ]
    assert report.summary.case_count == 6


def test_rag_adapter_eval_metrics_and_gates_default_public_output():
    report = evaluate_rag_adapter()
    summary = report.summary
    assert summary.blocked_no_rag_call_rate == 1.0
    assert summary.raw_response_exposure_rate == 0.0
    assert summary.fallback_rate > 0
    assert summary.citation_coverage >= 0.5
    assert summary.average_confidence >= 0.3
    assert summary.status == "pass"


def test_rag_adapter_eval_detects_raw_response_exposure_when_enabled():
    report = evaluate_rag_adapter(include_raw_response=True)
    assert report.summary.raw_response_exposure_rate > 0
    assert report.summary.status == "fail"


def test_rag_adapter_eval_blocked_case_never_calls_rag():
    report = evaluate_rag_adapter()
    blocked = next(case for case in report.cases if case.case_name == "blocked_request_no_rag_call")
    assert blocked.blocked is True
    assert blocked.rag_call_count == 0
    assert blocked.trace_steps == ["policy_engine"]


def test_rag_adapter_eval_fallback_case_records_degraded_path():
    report = evaluate_rag_adapter()
    fallback = next(case for case in report.cases if case.case_name == "external_failure_with_local_fallback")
    assert fallback.provider == "fallback"
    assert fallback.confidence <= 0.55
    assert "external_rag_query" in fallback.trace_steps
    assert "local_rag_fallback" in fallback.trace_steps


def test_policy_boundary_eval_defaults_to_pass():
    report = evaluate_policy_boundary()
    summary = report.summary
    assert summary.status == "pass"
    assert summary.blocked_detection_rate >= 0.9
    assert summary.uncertain_detection_rate >= 0.8
    assert summary.safe_pass_rate >= 0.8
    assert summary.blocked_no_rag_call_rate == 1.0
    assert summary.business_boundary_coverage >= 4
    assert "privacy_overreach" in summary.covered_categories
    assert "business_uncertain" in summary.covered_categories


def test_policy_boundary_eval_has_blocked_policy_only_trace():
    report = evaluate_policy_boundary()
    blocked = [case for case in report.cases if case.expected_status == "BLOCKED"]
    assert blocked
    assert all(case.rag_call_count == 0 for case in blocked)
    assert all(case.trace_steps == ["policy_engine"] for case in blocked)
