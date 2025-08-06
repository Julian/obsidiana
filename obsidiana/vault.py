"""
Python API for Obsidian vaults.
"""

from datetime import date
from functools import cached_property
from pathlib import Path
import os
import subprocess

from attrs import evolve, frozen
import frontmatter


@frozen
class Vault:
    """
    An Obsidian vault.
    """

    path: Path

    def child(self, *segments: str) -> Path:
        """
        Return a path within this vault.
        """
        return self.path.joinpath(*segments)

    def notes(self):
        """
        All notes within the vault.
        """
        return (
            Note(path=path, vault=self) for path in self.path.rglob("*.md")
        )

    def needs_triage(self):
        """
        All notes in the vault which are awaiting triage.
        """
        return (note for note in self.notes() if note.awaiting_triage())


@frozen
class Note:
    """
    An Obsidian note.
    """

    path: Path
    _vault: Vault

    @cached_property
    def _parsed(self):
        """
        The note's parsed contents.
        """
        return frontmatter.loads(self.path.read_text())

    @property
    def frontmatter(self):
        """
        (YAML) frontmatter from the note.
        """
        return self._parsed.metadata

    @cached_property
    def id(self):
        """
        The note's Obsidian ID.
        """
        return self.frontmatter.get("id", self.path.stem)

    @cached_property
    def status(self):
        """
        The note's status, as defined in the frontmatter.

        Defaults to "empty" if no status is set.
        """
        return self.frontmatter.get("status", "empty")

    @cached_property
    def tags(self):
        """
        The note's topical tags.
        """
        return frozenset(self.frontmatter.get("tags", ()))

    @cached_property
    def is_empty(self):
        """
        Does this note have no content?

        Notes with only empty lines, or whose only line is the note
        heading are also empty.
        """
        return not any(
            line.strip() for line in self.lines() if not line.startswith("# ")
        )

    def edit(self):
        """
        Edit this note in the configured text editor.

        Returns a new note, as details will likely have changed.
        """
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR", "vi")
        subprocess.run([editor, self.path], check=True, cwd=self._vault.path)  # noqa: S603
        return evolve(self)

    def lines(self):
        """
        The note's body.
        """
        return self._parsed.content.splitlines()

    def subpath(self) -> str:
        """
        The subpath of this note inside of the fault, without extension.
        """
        path = self.path.relative_to(self._vault.path)
        return str(path).removesuffix(".md")

    def awaiting_triage(self):
        """
        A note in the vault which is awaiting being refiled into another spot.

        For me these are daily notes in the root of the vault.
        """
        try:
            date.fromisoformat(self.path.stem)
        except ValueError:
            return False
        return self.path.parent == self._vault.path
