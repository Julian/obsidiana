"""
Python API for Obsidian vaults.
"""

from datetime import date
from functools import cached_property
from pathlib import Path

from attrs import frozen
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
        Frontmatter from all notes (along with the notes themselves).
        """
        return self._parsed.metadata

    @cached_property
    def tags(self):
        """
        The note's topical tags.
        """
        return frozenset(self.frontmatter.get("tags", ()))

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
