"""Agreement metrics comparing evaluator scores against human labels.

Pure functions with no I/O and no numpy/scipy: the statistics are implemented
directly in the standard library.
"""

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from valcore.errors import ConfigError
from valcore.models import ScoreKind


@dataclass
class CategoricalMetrics:
    """Agreement metrics for a categorical score field."""

    n: int
    accuracy: float
    per_label: dict[str, dict[str, float]]
    confusion: dict[str, dict[str, int]]
    cohens_kappa: float


@dataclass
class NumericMetrics:
    """Agreement metrics for a numeric score field."""

    n: int
    mae: float
    rmse: float
    pearson: float | None
    spearman: float | None


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson correlation of two equal-length series, or None on zero variance."""
    n = len(xs)
    if n == 0:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0.0 or var_y == 0.0:
        return None
    return cov / math.sqrt(var_x * var_y)


def _average_ranks(values: Sequence[float]) -> list[float]:
    """Return 1-based ranks of values, assigning average ranks to ties."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def categorical_metrics(
    pairs: Sequence[tuple[str, str]], labels: Sequence[str]
) -> CategoricalMetrics:
    """Compute categorical agreement metrics.

    ``pairs`` is a sequence of ``(predicted, actual)`` tuples in that order.
    The confusion matrix and per-label breakdown are built over the union of
    ``labels`` and any observed values, so off-schema predictions stay visible.
    """
    n = len(pairs)

    observed: set[str] = set()
    for predicted, actual in pairs:
        observed.add(predicted)
        observed.add(actual)
    cats = list(labels) + sorted(observed - set(labels))

    confusion: dict[str, dict[str, int]] = {a: {p: 0 for p in cats} for a in cats}
    for predicted, actual in pairs:
        confusion[actual][predicted] += 1

    correct = sum(confusion[c][c] for c in cats)
    accuracy = correct / n if n else 0.0

    per_label: dict[str, dict[str, float]] = {}
    for c in cats:
        tp = confusion[c][c]
        predicted_total = sum(confusion[a][c] for a in cats)
        actual_total = sum(confusion[c][p] for p in cats)
        precision = tp / predicted_total if predicted_total else 0.0
        recall = tp / actual_total if actual_total else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_label[c] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": float(actual_total),
        }

    kappa = _cohens_kappa(confusion, cats, n)

    return CategoricalMetrics(
        n=n,
        accuracy=accuracy,
        per_label=per_label,
        confusion=confusion,
        cohens_kappa=kappa,
    )


def _cohens_kappa(confusion: dict[str, dict[str, int]], cats: Sequence[str], n: int) -> float:
    """Cohen's kappa from a confusion matrix; 1.0 in the degenerate p_e == 1 case."""
    if n == 0:
        return 0.0
    p_o = sum(confusion[c][c] for c in cats) / n
    p_e = 0.0
    for c in cats:
        pred_frac = sum(confusion[a][c] for a in cats) / n
        actual_frac = sum(confusion[c][p] for p in cats) / n
        p_e += pred_frac * actual_frac
    if p_e == 1.0:
        return 1.0
    return (p_o - p_e) / (1 - p_e)


def numeric_metrics(pairs: Sequence[tuple[float, float]]) -> NumericMetrics:
    """Compute numeric agreement metrics.

    ``pairs`` is a sequence of ``(predicted, actual)`` tuples in that order.
    ``pearson`` and ``spearman`` are ``None`` for empty input or zero variance.
    """
    n = len(pairs)
    if n == 0:
        return NumericMetrics(n=0, mae=0.0, rmse=0.0, pearson=None, spearman=None)

    predicted = [p for p, _ in pairs]
    actual = [a for _, a in pairs]

    mae = sum(abs(p - a) for p, a in pairs) / n
    rmse = math.sqrt(sum((p - a) ** 2 for p, a in pairs) / n)
    pearson = _pearson(predicted, actual)
    spearman = _pearson(_average_ranks(predicted), _average_ranks(actual))

    return NumericMetrics(n=n, mae=mae, rmse=rmse, pearson=pearson, spearman=spearman)


def compute_metrics(
    pairs: Sequence[tuple],
    score_kind: ScoreKind,
    labels: Sequence[str] | None = None,
) -> dict:
    """Dispatch on ``score_kind`` and return the metrics as a plain dict.

    ``pairs`` is a sequence of ``(predicted, actual)`` tuples in that order.
    """
    kind = ScoreKind(score_kind)
    if kind is ScoreKind.CATEGORICAL:
        if labels is None:
            raise ConfigError("Categorical metrics require the label space.")
        return asdict(categorical_metrics(pairs, labels))
    return asdict(numeric_metrics(pairs))
