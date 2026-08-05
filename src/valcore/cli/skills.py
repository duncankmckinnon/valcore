"""The ``valcore skills`` group: install the packaged skills into agent directories.

Skills ship inside the wheel as ``valcore/skills/<name>/SKILL.md`` and teach an agent
how to drive valcore. Installing copies -- or symlinks -- each skill directory into the
target agent's skills directory.

Destinations are declared once in :data:`TARGETS`. Adding an agent is one row rather
than another branch in the install routine, which is what keeps ``install``,
``uninstall``, and ``list`` from each growing a per-agent special case.
"""

import shutil
from dataclasses import dataclass
from importlib.resources import files as _package_files
from pathlib import Path
from typing import Any

import click

from valcore.errors import ContractError


@dataclass(frozen=True)
class Target:
    """One installation destination: an agent and where it looks for skills."""

    flag: str
    label: str
    repo: Path
    home: Path

    def directory(self, *, use_home: bool) -> Path:
        """Resolve to the home-level or repo-level skills directory."""
        return self.home if use_home else Path.cwd() / self.repo


TARGETS: tuple[Target, ...] = (
    Target(
        "agents",
        "cross-client (.agents)",
        Path(".agents/skills"),
        Path.home() / ".agents" / "skills",
    ),
    Target(
        "claude",
        "Claude Code",
        Path(".claude/skills"),
        Path.home() / ".claude" / "skills",
    ),
    Target(
        "copilot",
        "GitHub Copilot",
        Path(".github/skills"),
        Path.home() / ".github" / "skills",
    ),
)

_DEFAULT_FLAG = "agents"


def packaged_skills() -> list[tuple[str, Path]]:
    """Return ``(name, directory)`` for every skill shipped inside the package."""
    root = Path(str(_package_files("valcore") / "skills"))
    if not root.is_dir():
        raise ContractError(f"No packaged skills found at {root}; the install may be incomplete.")
    found = [
        (child.name, child)
        for child in root.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    ]
    if not found:
        raise ContractError(f"No skills with a SKILL.md were found under {root}.")
    return sorted(found)


def selected_targets(flags: dict[str, Any], *, all_targets: bool) -> list[Target]:
    """Resolve which targets the flags select, defaulting to the cross-client one."""
    if all_targets:
        return list(TARGETS)
    chosen = [target for target in TARGETS if flags.get(target.flag)]
    if chosen:
        return chosen
    return [target for target in TARGETS if target.flag == _DEFAULT_FLAG]


def _clear(path: Path) -> None:
    """Remove ``path`` whether it is a symlink, a directory, or a file."""
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _same_tree(src: Path, dest: Path) -> bool:
    """True when ``dest`` holds exactly ``src``'s files with identical bytes.

    Compared by content rather than by stat signature: a copied tree keeps its
    mtimes, but an edited skill would otherwise be reported as up to date.
    """
    src_files = {p.relative_to(src): p for p in src.rglob("*") if p.is_file()}
    dest_files = {p.relative_to(dest): p for p in dest.rglob("*") if p.is_file()}
    if src_files.keys() != dest_files.keys():
        return False
    return all(src_files[rel].read_bytes() == dest_files[rel].read_bytes() for rel in src_files)


def install_skill(src: Path, dest: Path, *, symlink: bool, force: bool) -> str:
    """Install one skill directory, returning a past-tense status word for output."""
    if symlink:
        _clear(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(src.resolve(), target_is_directory=True)
        return "linked"

    if dest.exists() and not dest.is_symlink():
        if _same_tree(src, dest):
            return "up to date"
        if not force and not click.confirm(f"    {dest} differs; overwrite?", default=True):
            return "skipped"

    _clear(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    return "copied"


def target_options(fn):
    """Attach one ``--<agent>`` flag per target, plus ``--all``."""
    for target in reversed(TARGETS):
        fn = click.option(f"--{target.flag}", is_flag=True, help=f"Target {target.label}.")(fn)
    return click.option("--all", "all_targets", is_flag=True, help="Target every agent.")(fn)


@click.group()
def skills() -> None:
    """Install the packaged valcore skills into agent directories."""


@skills.command("install")
@target_options
@click.option(
    "--global",
    "use_home",
    is_flag=True,
    help="Install into your home directory instead of this repository.",
)
@click.option("--symlink", is_flag=True, help="Link to the packaged skills instead of copying.")
@click.option("--force", is_flag=True, help="Overwrite differing skills without prompting.")
def skills_install(
    all_targets: bool, use_home: bool, symlink: bool, force: bool, **flags: Any
) -> None:
    """Install the packaged skills.

    With no agent flag the skills go to ``.agents/skills``, which every
    skill-aware client can discover. Each flag adds exactly one destination and
    nothing implicit, so what you pass is what gets written.
    """
    available = packaged_skills()
    for target in selected_targets(flags, all_targets=all_targets):
        directory = target.directory(use_home=use_home)
        click.echo(f"{target.label} -> {directory}", err=True)
        for name, src in available:
            status = install_skill(src, directory / name, symlink=symlink, force=force)
            click.echo(f"    {name}: {status}", err=True)


@skills.command("uninstall")
@target_options
@click.option(
    "--global",
    "use_home",
    is_flag=True,
    help="Uninstall from your home directory instead of this repository.",
)
def skills_uninstall(all_targets: bool, use_home: bool, **flags: Any) -> None:
    """Remove the packaged skills from the selected agent directories."""
    available = packaged_skills()
    for target in selected_targets(flags, all_targets=all_targets):
        directory = target.directory(use_home=use_home)
        click.echo(f"{target.label} -> {directory}", err=True)
        for name, _src in available:
            dest = directory / name
            if dest.is_symlink() or dest.exists():
                _clear(dest)
                click.echo(f"    {name}: removed", err=True)
            else:
                click.echo(f"    {name}: not installed", err=True)


@skills.command("list")
@click.option("--global", "use_home", is_flag=True, help="Check your home directory instead.")
def skills_list(use_home: bool) -> None:
    """Show the packaged skills and where each one is currently installed."""
    for name, src in packaged_skills():
        click.echo(name)
        click.echo(f"    packaged: {src}")
        for target in TARGETS:
            dest = target.directory(use_home=use_home) / name
            if dest.is_symlink():
                state = f"linked -> {dest.readlink()}"
            elif dest.is_dir():
                state = "installed" if _same_tree(src, dest) else "installed (modified)"
            else:
                state = "-"
            click.echo(f"    {target.flag}: {state}")
