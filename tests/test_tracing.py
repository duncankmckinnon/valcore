"""Tests for the Logfire tracing module: configuration, span shape, and warnings."""

import importlib.util
import warnings
from pathlib import Path

import pytest

from valcore import tracing
from valcore.config import FileConfig
from valcore.models import Dataset, DatasetRow, EvaluatorVersion, Run, RunKind, RunStatus, ScoreKind

_LOGFIRE_PRESENT = importlib.util.find_spec("logfire") is not None


@pytest.fixture(autouse=True)
def _reset_tracing_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the idempotency guard and any exported Logfire env vars for each test.

    The guard is process-global (module-level), so without a reset the second test in
    the file would silently observe the first test's "already configured" state.
    """
    monkeypatch.setattr(tracing, "_configured", False, raising=False)
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)


def make_version(**overrides: object) -> EvaluatorVersion:
    """Build a valid categorical EvaluatorVersion, applying any field overrides."""
    base: dict[str, object] = {
        "evaluator_id": "ev1",
        "version_name": "my eval",
        "model": "gateway/anthropic:claude-sonnet-5",
        "instructions": "You are an evaluator.",
        "prompt_template": "Rate the answer to {question}.",
        "required_columns": ["question"],
        "output_fields": [
            {
                "name": "verdict",
                "type": "enum",
                "description": "The verdict.",
                "enum_values": ["pass", "fail"],
            }
        ],
        "score_field": "verdict",
        "score_kind": ScoreKind.CATEGORICAL,
        "score_labels": ["pass", "fail"],
    }
    base.update(overrides)
    return EvaluatorVersion.model_validate(base)


def make_dataset(**overrides: object) -> Dataset:
    """Build a minimal Dataset, applying any field overrides."""
    base: dict[str, object] = {"name": "my dataset", "columns": ["question"]}
    base.update(overrides)
    return Dataset.model_validate(base)


def make_row(dataset_id: str, idx: int = 0, **overrides: object) -> DatasetRow:
    """Build a minimal DatasetRow, applying any field overrides."""
    base: dict[str, object] = {"dataset_id": dataset_id, "idx": idx, "data": {"question": "hi"}}
    base.update(overrides)
    return DatasetRow.model_validate(base)


def make_run(version_id: str, dataset_id: str, **overrides: object) -> Run:
    """Build a minimal Run, applying any field overrides."""
    base: dict[str, object] = {
        "kind": RunKind.EVAL,
        "version_id": version_id,
        "dataset_id": dataset_id,
        "status": RunStatus.RUNNING,
        "concurrency": 4,
    }
    base.update(overrides)
    return Run.model_validate(base)


class TestConfigureTracingSilentWithoutToken:
    """No token configured must be a fully silent, working no-op path."""

    def test_emits_no_warning(self) -> None:
        cfg = FileConfig()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            tracing.configure_tracing(cfg)
        assert len(caught) == 0

    def test_run_span_yields_without_raising(self) -> None:
        cfg = FileConfig()
        tracing.configure_tracing(cfg)
        version = make_version()
        dataset = make_dataset()
        run = make_run(version.id, dataset.id)
        with tracing.run_span(run, version, dataset, row_count=1) as span:
            assert span is not None

    def test_row_span_yields_without_raising(self) -> None:
        cfg = FileConfig()
        tracing.configure_tracing(cfg)
        dataset = make_dataset()
        row = make_row(dataset.id)
        with tracing.row_span(row) as span:
            assert span is not None

    def test_run_span_yields_even_without_configure_call(self) -> None:
        """Callers need no conditionals: an unconfigured process still yields a span."""
        version = make_version()
        dataset = make_dataset()
        run = make_run(version.id, dataset.id)
        with tracing.run_span(run, version, dataset, row_count=1):
            pass

    def test_row_span_yields_even_without_configure_call(self) -> None:
        dataset = make_dataset()
        row = make_row(dataset.id)
        with tracing.row_span(row):
            pass


class TestConfigureTracingTokenWithoutLogfire:
    """A configured token with the ``logfire`` extra absent must warn exactly once."""

    def _simulate_logfire_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original_find_spec = importlib.util.find_spec

        def fake_find_spec(name: str, *args: object, **kwargs: object) -> object:
            if name == "logfire":
                return None
            return original_find_spec(name, *args, **kwargs)

        monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    def test_warns_once_naming_the_extra(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._simulate_logfire_absent(monkeypatch)
        cfg = FileConfig(logfire_token="lf-write-token")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            tracing.configure_tracing(cfg)
        user_warnings = [w for w in caught if issubclass(w.category, UserWarning)]
        assert len(user_warnings) == 1
        assert "valcore[logfire]" in str(user_warnings[0].message)

    def test_does_not_warn_again_on_second_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._simulate_logfire_absent(monkeypatch)
        cfg = FileConfig(logfire_token="lf-write-token")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            tracing.configure_tracing(cfg)
            tracing.configure_tracing(cfg)
        user_warnings = [w for w in caught if issubclass(w.category, UserWarning)]
        assert len(user_warnings) == 1


@pytest.mark.skipif(not _LOGFIRE_PRESENT, reason="logfire extra not installed")
class TestConfigureTracingTokenWithLogfirePresent:
    """With the real extra installed, a configured token must never warn."""

    def test_no_warning_when_logfire_present(self) -> None:
        cfg = FileConfig(logfire_token="lf-write-token")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            tracing.configure_tracing(cfg)
        user_warnings = [w for w in caught if issubclass(w.category, UserWarning)]
        assert len(user_warnings) == 0


class TestConfigureTracingIdempotent:
    """configure_tracing must be safe to call twice without reconfiguring Logfire."""

    def test_second_call_does_not_reconfigure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict[str, object]] = []
        monkeypatch.setattr(tracing.logfire, "configure", lambda **kwargs: calls.append(kwargs))
        cfg = FileConfig()
        tracing.configure_tracing(cfg)
        tracing.configure_tracing(cfg)
        assert len(calls) == 1

    def test_configure_called_with_expected_arguments(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, object]] = []
        monkeypatch.setattr(tracing.logfire, "configure", lambda **kwargs: calls.append(kwargs))
        tracing.configure_tracing(FileConfig())
        assert len(calls) == 1
        assert calls[0]["send_to_logfire"] == "if-token-present"
        assert calls[0]["service_name"] == "valcore"
        assert calls[0]["console"] is False

    def test_applies_token_to_environment_before_configuring(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(tracing.logfire, "configure", lambda **kwargs: None)
        cfg = FileConfig(logfire_token="lf-from-config")
        tracing.configure_tracing(cfg)
        import os

        assert os.environ["LOGFIRE_TOKEN"] == "lf-from-config"


@pytest.mark.skipif(not _LOGFIRE_PRESENT, reason="logfire extra not installed")
class TestSpanTreeShape:
    """The run span must be the parent of each row span, carrying the documented attributes."""

    def test_run_span_is_parent_of_row_span(self, capfire) -> None:
        version = make_version()
        dataset = make_dataset()
        run = make_run(version.id, dataset.id)
        row = make_row(dataset.id, idx=0)

        with tracing.run_span(run, version, dataset, row_count=1) as span:
            with tracing.row_span(row):
                pass
            span.set_attribute("status", "completed")
            span.set_attribute("accuracy", 0.92)

        spans = {s["name"]: s for s in capfire.exporter.exported_spans_as_dict()}
        assert "valcore.run" in spans
        assert "valcore.score_row" in spans

        run_data = spans["valcore.run"]
        row_data = spans["valcore.score_row"]
        assert row_data["parent"] == run_data["context"]

    def test_run_span_carries_documented_attributes(self, capfire) -> None:
        version = make_version(version_name="accuracy-check")
        dataset = make_dataset(name="golden-set")
        run = make_run(version.id, dataset.id, concurrency=7)

        with tracing.run_span(run, version, dataset, row_count=5) as span:
            span.set_attribute("status", "completed")

        spans = {s["name"]: s for s in capfire.exporter.exported_spans_as_dict()}
        attrs = spans["valcore.run"]["attributes"]
        assert attrs["run_id"] == run.id
        assert attrs["kind"] == RunKind.EVAL.value
        assert attrs["version_id"] == version.id
        assert attrs["version_name"] == "accuracy-check"
        assert attrs["dataset_id"] == dataset.id
        assert attrs["dataset_name"] == "golden-set"
        assert attrs["row_count"] == 5
        assert attrs["concurrency"] == 7
        assert attrs["status"] == "completed"

    def test_run_span_carries_metrics_keys_set_before_close(self, capfire) -> None:
        version = make_version()
        dataset = make_dataset()
        run = make_run(version.id, dataset.id)
        metrics = {"accuracy": 0.92, "agreement_rate": 0.87}

        with tracing.run_span(run, version, dataset, row_count=1) as span:
            span.set_attribute("status", "completed")
            for key, value in metrics.items():
                span.set_attribute(key, value)

        spans = {s["name"]: s for s in capfire.exporter.exported_spans_as_dict()}
        attrs = spans["valcore.run"]["attributes"]
        assert attrs["accuracy"] == 0.92
        assert attrs["agreement_rate"] == 0.87

    def test_row_span_carries_row_id_and_idx(self, capfire) -> None:
        dataset = make_dataset()
        row = make_row(dataset.id, idx=3)

        with tracing.row_span(row):
            pass

        spans = {s["name"]: s for s in capfire.exporter.exported_spans_as_dict()}
        attrs = spans["valcore.score_row"]["attributes"]
        assert attrs["row_id"] == row.id
        assert attrs["idx"] == 3

    def test_multiple_row_spans_share_the_same_run_parent(self, capfire) -> None:
        version = make_version()
        dataset = make_dataset()
        run = make_run(version.id, dataset.id)
        rows = [make_row(dataset.id, idx=i) for i in range(3)]

        with tracing.run_span(run, version, dataset, row_count=len(rows)) as span:
            for row in rows:
                with tracing.row_span(row):
                    pass
            span.set_attribute("status", "completed")

        exported = capfire.exporter.exported_spans_as_dict()
        run_data = next(s for s in exported if s["name"] == "valcore.run")
        row_spans = [s for s in exported if s["name"] == "valcore.score_row"]
        assert len(row_spans) == 3
        for row_data in row_spans:
            assert row_data["parent"] == run_data["context"]


class TestNoLocalAgentInstrumentation:
    """valcore must never instrument an agent locally -- the Gateway already reports."""

    def test_no_source_file_references_instrument_pydantic_ai_or_instrument_all(self) -> None:
        src_root = Path(__file__).resolve().parent.parent / "src" / "valcore"
        offenders = []
        for path in src_root.rglob("*.py"):
            text = path.read_text()
            if "instrument_pydantic_ai" in text or "instrument_all" in text:
                offenders.append(str(path))
        assert offenders == [], f"Found forbidden client-side instrumentation in: {offenders}"
