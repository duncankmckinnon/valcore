"""Tests for ``valcore skills`` install/uninstall/list and the ``--version`` flag."""

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from valcore.cli import skills as skills_mod
from valcore.cli.main import cli
from valcore.errors import ContractError
from valcore.models import Dataset, EvaluatorVersion, ScoreKind, check_dataset_compatibility


def _skill_body() -> str:
    """Return the text of the packaged ``use-valcore`` SKILL.md."""
    directory = dict(skills_mod.packaged_skills())["use-valcore"]
    return (directory / "SKILL.md").read_text()


def _reference_body() -> str:
    """Return the text of the packaged ``use-valcore`` reference.md (CLI reference)."""
    directory = dict(skills_mod.packaged_skills())["use-valcore"]
    return (directory / "reference.md").read_text()


@pytest.fixture
def packaged(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``packaged_skills`` at a throwaway skill tree and return its root."""
    root = Path(str(tmp_path)) / "packaged"
    for name in ("alpha", "beta"):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(f"# {name}\n")
    monkeypatch.setattr(
        skills_mod,
        "packaged_skills",
        lambda: sorted((p.name, p) for p in root.iterdir() if p.is_dir()),
    )
    return root


def _run(args: list[str], cwd: Path) -> object:
    """Invoke the CLI with ``cwd`` as the working directory."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=cwd) as sandbox:
        result = runner.invoke(cli, args)
        result.sandbox = Path(sandbox)  # type: ignore[attr-defined]
    return result


# -- packaging ----------------------------------------------------------------


def test_real_skills_are_packaged() -> None:
    """The shipped skill must be discoverable, or the wheel is broken."""
    names = [name for name, _ in skills_mod.packaged_skills()]
    assert names == ["use-valcore"]


def test_packaged_skills_all_have_a_skill_md() -> None:
    for _name, directory in skills_mod.packaged_skills():
        assert (directory / "SKILL.md").is_file()


def test_cli_reference_ships_alongside_the_skill() -> None:
    """The reference is a sibling file, so installing the skill must carry it."""
    directory = dict(skills_mod.packaged_skills())["use-valcore"]
    assert (directory / "reference.md").is_file()


def test_skill_documents_the_gateway_setup() -> None:
    """Nothing runs without a gateway key, so the skill has to say so."""
    directory = dict(skills_mod.packaged_skills())["use-valcore"]
    body = (directory / "SKILL.md").read_text()
    assert "PYDANTIC_AI_GATEWAY_API_KEY" in body
    assert "valcore config set-key" in body
    assert "gateway/anthropic:claude-sonnet-5" in body


# -- seeded generation content ------------------------------------------------
#
# These guard against the drift the skill update fixes: the packaged prose must
# describe seeded generation as merged, not as it was before it existed.


def test_skill_documents_generating_a_dataset_from_an_evaluator_version() -> None:
    """One direction of seeded generation: shape the dataset from a version."""
    body = _skill_body().lower()
    assert "dataset from an evaluator version" in body


def test_skill_documents_generating_an_evaluator_from_a_dataset() -> None:
    """The other direction: seed an evaluator's columns from a dataset."""
    body = _skill_body().lower()
    assert "evaluator from a dataset" in body


def test_skill_says_labels_are_optional_and_only_needed_for_validation() -> None:
    """A dataset needs no labels to be scored; labels gate validation runs only."""
    body = _skill_body().lower()
    # The claim must tie "optional" labels to the validation-only requirement, not
    # merely mention validation somewhere in the file.
    only_for_validation = (
        "required only for" in body
        or "only required for" in body
        or "only for a validation" in body
        or "only for validation" in body
    )
    assert only_for_validation, "skill should say labels are required only for validation runs"


def test_skill_still_documents_the_gateway_and_compatibility_rules() -> None:
    """The update must not drop the material the earlier tests already guard."""
    body = _skill_body()
    assert "PYDANTIC_AI_GATEWAY_API_KEY" in body
    assert "Compatibility rules" in body


def test_unlabeled_dataset_claim_is_backed_by_check_dataset_compatibility() -> None:
    """Tie the skill's unlabeled-dataset prose to real behavior so they cannot diverge.

    An empty ``label_schema`` is the legal "no ground truth" state: as long as the
    required columns are present, the dataset stays runnable against the version.
    """
    version = EvaluatorVersion(
        evaluator_id="ev",
        version_name="v1",
        model="gateway/anthropic:claude-sonnet-5",
        instructions="Score the answer.",
        prompt_template="{answer}",
        required_columns=["answer"],
        score_field="score",
        score_kind=ScoreKind.CATEGORICAL,
        score_labels=["pass", "fail"],
    )
    dataset = Dataset(name="unlabeled", columns=["answer"], label_schema={})

    # Must not raise: no label space means nothing to reconcile with the score space.
    check_dataset_compatibility(version, dataset)


def test_empty_label_schema_does_not_waive_the_required_columns_check() -> None:
    """Back the corrected prose: an empty schema skips checks 2/3 but not check 1.

    The skill now states an unlabeled dataset "still must satisfy check 1: the
    evaluator's required_columns must be present." Guard that so the wording cannot
    over-relax into "runs against any evaluator" again.
    """
    version = EvaluatorVersion(
        evaluator_id="ev",
        version_name="v1",
        model="gateway/anthropic:claude-sonnet-5",
        instructions="Score the answer.",
        prompt_template="{answer}",
        required_columns=["answer"],
        score_field="score",
        score_kind=ScoreKind.CATEGORICAL,
        score_labels=["pass", "fail"],
    )
    # Empty label schema, but the required column is absent.
    dataset = Dataset(name="wrong-shape", columns=["question"], label_schema={})

    with pytest.raises(ContractError):
        check_dataset_compatibility(version, dataset)


# -- reference.md (CLI) content -----------------------------------------------
#
# Seeded generation is API/web only. The CLI reference must say so plainly and must
# not invent a command or flag for it -- the worst outcome named in the task.


def test_reference_notes_seeded_generation_is_not_in_the_cli() -> None:
    """The CLI reference points agents away from hunting for a flag that does not exist."""
    body = _reference_body()
    assert "Not in the CLI" in body
    # The table of contents at the top must link the new section, or it drifts.
    assert "#not-in-the-cli" in body


# -- target resolution --------------------------------------------------------


def test_no_flags_defaults_to_agents() -> None:
    chosen = skills_mod.selected_targets({}, all_targets=False)
    assert [t.flag for t in chosen] == ["agents"]


def test_flags_are_additive_and_nothing_is_implicit() -> None:
    chosen = skills_mod.selected_targets({"claude": True, "copilot": True}, all_targets=False)
    assert [t.flag for t in chosen] == ["claude", "copilot"]


def test_all_selects_every_target() -> None:
    chosen = skills_mod.selected_targets({}, all_targets=True)
    assert [t.flag for t in chosen] == [t.flag for t in skills_mod.TARGETS]


def test_global_resolves_under_home() -> None:
    target = next(t for t in skills_mod.TARGETS if t.flag == "claude")
    assert target.directory(use_home=True) == Path.home() / ".claude" / "skills"
    assert target.directory(use_home=False) == Path.cwd() / ".claude" / "skills"


# -- install ------------------------------------------------------------------


def test_install_copies_into_the_default_directory(packaged: Path, tmp_path: Path) -> None:
    result = _run(["skills", "install"], tmp_path)
    assert result.exit_code == 0
    installed = result.sandbox / ".agents" / "skills" / "alpha" / "SKILL.md"
    assert installed.read_text() == "# alpha\n"


def test_install_claude_does_not_touch_agents(packaged: Path, tmp_path: Path) -> None:
    result = _run(["skills", "install", "--claude"], tmp_path)
    assert (result.sandbox / ".claude" / "skills" / "alpha").is_dir()
    assert not (result.sandbox / ".agents").exists()


def test_install_multiple_agents(packaged: Path, tmp_path: Path) -> None:
    result = _run(["skills", "install", "--claude", "--copilot"], tmp_path)
    assert (result.sandbox / ".claude" / "skills" / "beta").is_dir()
    assert (result.sandbox / ".github" / "skills" / "beta").is_dir()
    assert not (result.sandbox / ".agents").exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows symlinks require optional privileges")
def test_symlink_links_rather_than_copies(packaged: Path, tmp_path: Path) -> None:
    result = _run(["skills", "install", "--claude", "--symlink"], tmp_path)
    dest = result.sandbox / ".claude" / "skills" / "alpha"
    assert dest.is_symlink()
    assert dest.resolve() == (packaged / "alpha").resolve()


def test_symlink_failure_explains_windows_and_copy_fallback(
    packaged: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = packaged / "alpha"
    dest = tmp_path / "installed" / "alpha"

    def _deny_symlink(*_args: object, **_kwargs: object) -> None:
        raise OSError("privilege not held")

    monkeypatch.setattr(Path, "symlink_to", _deny_symlink)

    with pytest.raises(ContractError, match="Developer Mode.*omit --symlink"):
        skills_mod.install_skill(src, dest, symlink=True, force=False)


def test_reinstall_of_identical_content_is_reported_up_to_date(
    packaged: Path, tmp_path: Path
) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        assert runner.invoke(cli, ["skills", "install"]).exit_code == 0
        second = runner.invoke(cli, ["skills", "install"])
        assert second.exit_code == 0
        assert "up to date" in second.output


def test_divergent_content_prompts_and_declining_leaves_it_alone(
    packaged: Path, tmp_path: Path
) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        runner.invoke(cli, ["skills", "install"])
        edited = Path(".agents/skills/alpha/SKILL.md")
        edited.write_text("# edited by hand\n")

        declined = runner.invoke(cli, ["skills", "install"], input="n\n")
        assert declined.exit_code == 0
        assert edited.read_text() == "# edited by hand\n"

        forced = runner.invoke(cli, ["skills", "install", "--force"])
        assert forced.exit_code == 0
        assert edited.read_text() == "# alpha\n"


@pytest.mark.skipif(os.name == "nt", reason="Windows symlinks require optional privileges")
def test_symlink_replaces_an_existing_copy(packaged: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        runner.invoke(cli, ["skills", "install", "--claude"])
        dest = Path(".claude/skills/alpha")
        assert dest.is_dir() and not dest.is_symlink()

        runner.invoke(cli, ["skills", "install", "--claude", "--symlink"])
        assert dest.is_symlink()


# -- uninstall ----------------------------------------------------------------


def test_uninstall_removes_only_the_targeted_directory(packaged: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        runner.invoke(cli, ["skills", "install", "--claude", "--copilot"])
        runner.invoke(cli, ["skills", "uninstall", "--claude"])

        assert not Path(".claude/skills/alpha").exists()
        assert Path(".github/skills/alpha").is_dir()


def test_uninstall_reports_when_nothing_is_installed(packaged: Path, tmp_path: Path) -> None:
    result = _run(["skills", "uninstall", "--claude"], tmp_path)
    assert result.exit_code == 0
    assert "not installed" in result.output


@pytest.mark.skipif(os.name == "nt", reason="Windows symlinks require optional privileges")
def test_uninstall_removes_a_symlink(packaged: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        runner.invoke(cli, ["skills", "install", "--claude", "--symlink"])
        runner.invoke(cli, ["skills", "uninstall", "--claude"])
        assert not Path(".claude/skills/alpha").is_symlink()
        assert (packaged / "alpha" / "SKILL.md").is_file()  # source survived


# -- list ---------------------------------------------------------------------


def test_list_reports_installed_and_modified_state(packaged: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        runner.invoke(cli, ["skills", "install", "--claude"])
        listing = runner.invoke(cli, ["skills", "list"])
        assert "installed" in listing.output

        Path(".claude/skills/alpha/SKILL.md").write_text("# changed\n")
        modified = runner.invoke(cli, ["skills", "list"])
        assert "installed (modified)" in modified.output


# -- version ------------------------------------------------------------------


def test_version_flag_matches_the_version_subcommand() -> None:
    runner = CliRunner()
    flag = runner.invoke(cli, ["--version"])
    subcommand = runner.invoke(cli, ["version"])
    assert flag.exit_code == 0
    assert subcommand.exit_code == 0
    assert flag.output.strip() == subcommand.output.strip()
    assert flag.output.strip()


def test_resolve_version_falls_back_when_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A source checkout with no installed distribution must not crash."""
    from importlib import import_module
    from importlib.metadata import PackageNotFoundError

    # `valcore.cli` re-exports a `main` function, which shadows the `main`
    # submodule on attribute access -- import it by name instead.
    main_mod = import_module("valcore.cli.main")

    def _raise(_name: str) -> str:
        raise PackageNotFoundError("valcore")

    monkeypatch.setattr(main_mod, "package_version", _raise)
    assert main_mod._resolve_version()  # non-empty, no exception


# -- config reference content -------------------------------------------------
#
# `config set`/`unset` accept keys by name, so an agent works from the reference
# table rather than a --help listing. These pin that table to the code: a new
# FileConfig field, or a change to which keys `set` refuses, has to reach the docs
# in the same commit. They derive their expectations from the code, so ordinary
# prose edits do not require touching them.


def _config_table_row(body: str, key: str) -> str:
    """Return the reference.md table row documenting `key`, or raise if there is none."""
    prefix = f"| `{key}` |"
    rows = [line for line in body.splitlines() if line.startswith(prefix)]
    assert rows, f"no config table row for {key!r} in reference.md"
    return rows[0]


def test_reference_table_lists_every_config_key() -> None:
    """Every key `config set`/`unset` accepts has its own row in the reference table.

    Matching the row rather than a bare mention: these key names also appear in prose,
    so a substring check passes even when the table itself has lost the key.
    """
    from valcore.config import FileConfig

    body = _reference_body()
    for key in FileConfig.model_fields:
        _config_table_row(body, key)


def test_reference_marks_the_keys_set_refuses() -> None:
    """A key `set` will not take must be documented as unset-only, not silently listed."""
    from valcore.cli.main import _CONFIG_FIELDS

    body = _reference_body()
    refused = [key for key, field in _CONFIG_FIELDS.items() if not field.settable]
    assert refused, "expected at least one non-settable key to document"
    for key in refused:
        row = _config_table_row(body, key)
        assert "unset only" in row, f"{key} is not marked unset-only in reference.md: {row}"


def test_reference_documents_the_config_set_commands() -> None:
    """The generic setters are the only path to five of the keys, so they must appear."""
    body = _reference_body()
    assert "`set KEY VALUE`" in body
    assert "`unset KEY`" in body
