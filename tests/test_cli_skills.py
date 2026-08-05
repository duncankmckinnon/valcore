"""Tests for ``valcore skills`` install/uninstall/list and the ``--version`` flag."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from valcore.cli import skills as skills_mod
from valcore.cli.main import cli


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


def test_symlink_links_rather_than_copies(packaged: Path, tmp_path: Path) -> None:
    result = _run(["skills", "install", "--claude", "--symlink"], tmp_path)
    dest = result.sandbox / ".claude" / "skills" / "alpha"
    assert dest.is_symlink()
    assert dest.resolve() == (packaged / "alpha").resolve()


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
