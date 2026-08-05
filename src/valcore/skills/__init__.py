"""Skill documents shipped inside the wheel and installed by ``valcore skills install``.

This package exists so ``importlib.resources.files("valcore.skills")`` resolves in an
installed wheel as well as in a source checkout. The skills themselves are plain
directories of markdown, not importable modules.
"""
