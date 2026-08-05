"""Pure shape derivation between evaluators and datasets for seeded generation.

Both seeded-generation directions — dataset-from-evaluator and evaluator-from-dataset —
need to agree on the columns and label space that let a run start without a
``ContractError``. That agreement is pure structure: it depends only on the source
entity, never on I/O, the store, or an LLM. Isolating it here keeps the route handlers
thin and lets either direction reuse the same, tested derivation. Instructions steer
generated *content*; the shape derived here is fixed before any generation call.
"""

from valcore.errors import ContractError
from valcore.models import Dataset, EvaluatorVersion, LabelSchema


def dataset_shape_from_version(
    version: EvaluatorVersion, extra_columns: list[str]
) -> tuple[list[str], LabelSchema]:
    """Columns and label space a dataset needs in order to run under ``version``.

    The version's ``required_columns`` come first in their original order, then
    ``extra_columns`` in the order given. ``_valid_rows`` in ``datagen`` compares each
    generated row's key set to the full column set with exact equality, so the columns
    must be complete and collision-free before generation — a column named twice would
    silently drop every row. Collisions therefore raise rather than being deduplicated.
    """
    required = version.required_columns
    required_set = set(required)

    clashes = [col for col in extra_columns if col in required_set]
    if clashes:
        raise ContractError(
            f"Extra column(s) {clashes} already appear in the evaluator's required "
            f"columns {required}; remove them from the extras."
        )

    seen: set[str] = set()
    duplicates = [col for col in extra_columns if col in seen or seen.add(col)]
    if duplicates:
        raise ContractError(f"Extra column(s) {sorted(set(duplicates))} are listed more than once.")

    columns = list(required) + list(extra_columns)
    schema = LabelSchema(
        kind=version.score_kind,
        labels=version.score_labels,
        minimum=version.score_minimum,
        maximum=version.score_maximum,
    )
    return columns, schema


def evaluator_seed_from_dataset(
    dataset: Dataset,
) -> tuple[list[str], LabelSchema | None]:
    """Columns and optional score space for generating an evaluator.

    An empty ``label_schema`` is a legal "no ground truth" state, so it yields ``None``
    rather than raising; a non-empty schema is parsed and validated by ``LabelSchema``.
    """
    columns = list(dataset.columns)
    if dataset.label_schema:
        return columns, LabelSchema.model_validate(dataset.label_schema)
    return columns, None
