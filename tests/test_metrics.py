"""Hand-computed agreement metric tests."""

import pytest

from evalcore.errors import ConfigError
from evalcore.metrics import (
    categorical_metrics,
    compute_metrics,
    numeric_metrics,
)
from evalcore.models import ScoreKind


def test_three_label_confusion_hand_computed() -> None:
    """A worked 3-label example with known accuracy, per-label P/R/F1, and kappa.

    pairs are (predicted, actual). Confusion (actual -> predicted):
        a: a=3, b=1, c=0   (4 actual a)
        b: b=2, c=1        (3 actual b)
        c: c=2, a=1        (3 actual c)
    """
    pairs = [
        ("a", "a"),
        ("a", "a"),
        ("a", "a"),
        ("b", "a"),
        ("b", "b"),
        ("b", "b"),
        ("c", "b"),
        ("c", "c"),
        ("c", "c"),
        ("a", "c"),
    ]
    m = categorical_metrics(pairs, ["a", "b", "c"])

    assert m.n == 10
    assert m.accuracy == pytest.approx(0.7)

    assert m.confusion == {
        "a": {"a": 3, "b": 1, "c": 0},
        "b": {"a": 0, "b": 2, "c": 1},
        "c": {"a": 1, "b": 0, "c": 2},
    }

    assert m.per_label["a"]["precision"] == pytest.approx(0.75)
    assert m.per_label["a"]["recall"] == pytest.approx(0.75)
    assert m.per_label["a"]["f1"] == pytest.approx(0.75)
    assert m.per_label["a"]["support"] == pytest.approx(4.0)

    assert m.per_label["b"]["precision"] == pytest.approx(2 / 3)
    assert m.per_label["b"]["recall"] == pytest.approx(2 / 3)
    assert m.per_label["b"]["f1"] == pytest.approx(2 / 3)
    assert m.per_label["b"]["support"] == pytest.approx(3.0)

    assert m.per_label["c"]["precision"] == pytest.approx(2 / 3)
    assert m.per_label["c"]["recall"] == pytest.approx(2 / 3)
    assert m.per_label["c"]["support"] == pytest.approx(3.0)

    # p_o = 0.7, p_e = 0.4*0.4 + 0.3*0.3 + 0.3*0.3 = 0.34
    # kappa = (0.7 - 0.34) / (1 - 0.34) = 0.36 / 0.66
    assert m.cohens_kappa == pytest.approx(0.36 / 0.66)


def test_perfect_agreement() -> None:
    pairs = [("a", "a"), ("b", "b"), ("c", "c")]
    m = categorical_metrics(pairs, ["a", "b", "c"])
    assert m.accuracy == pytest.approx(1.0)
    assert m.cohens_kappa == pytest.approx(1.0)
    for label in ("a", "b", "c"):
        assert m.per_label[label]["precision"] == pytest.approx(1.0)
        assert m.per_label[label]["recall"] == pytest.approx(1.0)
        assert m.per_label[label]["f1"] == pytest.approx(1.0)


def test_total_disagreement() -> None:
    pairs = [("a", "b"), ("b", "a")]
    m = categorical_metrics(pairs, ["a", "b"])
    assert m.accuracy == pytest.approx(0.0)
    # p_o = 0, p_e = 0.5*0.5 + 0.5*0.5 = 0.5, kappa = (0 - 0.5)/0.5 = -1
    assert m.cohens_kappa == pytest.approx(-1.0)


def test_single_label_degenerate_kappa() -> None:
    """Perfect agreement on one label: p_e == 1 must yield kappa 1.0, not nan."""
    pairs = [("a", "a"), ("a", "a")]
    m = categorical_metrics(pairs, ["a", "b"])
    assert m.accuracy == pytest.approx(1.0)
    assert m.cohens_kappa == pytest.approx(1.0)


def test_empty_categorical() -> None:
    m = categorical_metrics([], ["a", "b"])
    assert m.n == 0
    assert m.accuracy == pytest.approx(0.0)
    assert m.cohens_kappa == pytest.approx(0.0)
    for label in ("a", "b"):
        assert m.per_label[label]["precision"] == pytest.approx(0.0)
        assert m.per_label[label]["recall"] == pytest.approx(0.0)
        assert m.per_label[label]["f1"] == pytest.approx(0.0)
        assert m.per_label[label]["support"] == pytest.approx(0.0)


def test_label_with_zero_support() -> None:
    """A label never appearing as actual/predicted has 0.0 P/R/F1, not nan."""
    pairs = [("a", "a"), ("b", "b")]
    m = categorical_metrics(pairs, ["a", "b", "c"])
    assert m.per_label["c"]["precision"] == pytest.approx(0.0)
    assert m.per_label["c"]["recall"] == pytest.approx(0.0)
    assert m.per_label["c"]["f1"] == pytest.approx(0.0)
    assert m.per_label["c"]["support"] == pytest.approx(0.0)


def test_off_schema_prediction_visible() -> None:
    """A predicted value outside `labels` appears in the confusion matrix."""
    pairs = [("z", "a"), ("a", "a")]
    m = categorical_metrics(pairs, ["a", "b"])
    assert "z" in m.confusion
    assert "z" in m.confusion["a"]
    assert m.confusion["a"]["z"] == 1
    assert m.confusion["a"]["a"] == 1
    assert "z" in m.per_label
    # z was predicted once but is never the true label: precision 0, support 0.
    assert m.per_label["z"]["precision"] == pytest.approx(0.0)
    assert m.per_label["z"]["support"] == pytest.approx(0.0)


def test_numeric_mae_rmse_known_series() -> None:
    """predicted=[1,2,3,4], actual=[1,2,3,5]: errors 0,0,0,1."""
    pairs = [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 5.0)]
    m = numeric_metrics(pairs)
    assert m.n == 4
    assert m.mae == pytest.approx(0.25)
    assert m.rmse == pytest.approx(0.5)


def test_pearson_perfectly_correlated() -> None:
    pairs = [(1.0, 2.0), (2.0, 4.0), (3.0, 6.0)]
    m = numeric_metrics(pairs)
    assert m.pearson == pytest.approx(1.0)


def test_pearson_perfectly_anti_correlated() -> None:
    pairs = [(1.0, 3.0), (2.0, 2.0), (3.0, 1.0)]
    m = numeric_metrics(pairs)
    assert m.pearson == pytest.approx(-1.0)


def test_spearman_with_ties() -> None:
    """Ties get average ranks; predicted [1,2,2,3] vs actual [1,2,3,4]."""
    pairs = [(1.0, 1.0), (2.0, 2.0), (2.0, 3.0), (3.0, 4.0)]
    m = numeric_metrics(pairs)
    # ranks predicted [1, 2.5, 2.5, 4], actual [1, 2, 3, 4]
    # cov=4.5, var_x=4.5, var_y=5.0 -> 4.5/sqrt(22.5)
    assert m.spearman == pytest.approx(4.5 / (22.5**0.5))


def test_zero_variance_returns_none() -> None:
    pairs = [(5.0, 1.0), (5.0, 2.0), (5.0, 3.0)]
    m = numeric_metrics(pairs)
    assert m.pearson is None
    assert m.spearman is None


def test_empty_numeric() -> None:
    m = numeric_metrics([])
    assert m.n == 0
    assert m.mae == pytest.approx(0.0)
    assert m.rmse == pytest.approx(0.0)
    assert m.pearson is None
    assert m.spearman is None


def test_compute_metrics_categorical_dispatch() -> None:
    pairs = [("a", "a"), ("b", "b")]
    out = compute_metrics(pairs, ScoreKind.CATEGORICAL, labels=["a", "b"])
    assert isinstance(out, dict)
    assert set(out) == {"n", "accuracy", "per_label", "confusion", "cohens_kappa"}
    assert out["accuracy"] == pytest.approx(1.0)


def test_compute_metrics_numeric_dispatch() -> None:
    pairs = [(1.0, 1.0), (2.0, 3.0)]
    out = compute_metrics(pairs, ScoreKind.NUMERIC)
    assert isinstance(out, dict)
    assert set(out) == {"n", "mae", "rmse", "pearson", "spearman"}
    assert out["mae"] == pytest.approx(0.5)


def test_compute_metrics_categorical_requires_labels() -> None:
    with pytest.raises(ConfigError):
        compute_metrics([("a", "a")], ScoreKind.CATEGORICAL)


def test_compute_metrics_accepts_string_score_kind() -> None:
    out = compute_metrics([(1.0, 1.0)], "numeric")
    assert out["n"] == 1
