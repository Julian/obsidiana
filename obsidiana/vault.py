"""
Python API for Obsidian vaults.
"""

from datetime import date
from functools import cached_property
from pathlib import Path
import os
import re
import subprocess

from attrs import evolve, frozen
from markdown_it import MarkdownIt
import frontmatter
import networkx as nx

_HEADING = re.compile("^#+ ")
_WIKILINK = re.compile(r"\[\[[^[]+\]\]")
_WIKILINK_TARGET = re.compile(
    r"\[\[([^|#\]\n]+)(?:#[^|\]\n]*)?(?:\|[^\]\n]*)?\]\]",
)
_MD = MarkdownIt()


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

    def graph(self) -> nx.DiGraph:
        """
        The reference graph of all notes in the vault.

        Notes are nodes; a directed edge ``a -> b`` means ``a`` contains a
        wikilink that resolves to ``b``. Self-references are not represented
        as edges, since for graph queries like "leaves" the user-facing
        intent is references to *other* notes.

        Wikilink targets which don't resolve to any note (or which resolve
        ambiguously to multiple) are recorded in the graph-level attribute
        ``broken``, mapping each note to the set of unresolved targets it
        references.

        Resolution attempts an exact subpath match first, then falls back
        to matching by file stem when that stem is unique within the vault.
        Resolution is case-insensitive, matching Obsidian's behavior on
        case-insensitive filesystems.
        """
        notes = list(self.notes())

        by_stem: dict[str, list[Note]] = {}
        by_subpath: dict[str, list[Note]] = {}
        for note in notes:
            by_stem.setdefault(note.path.stem.lower(), []).append(note)
            by_subpath.setdefault(note.subpath().lower(), []).append(note)

        def resolve(target: str) -> Note | None:
            target = target.lower()
            matches = by_subpath.get(target, ())
            if len(matches) == 1:
                return matches[0]
            matches = by_stem.get(target, ())
            return matches[0] if len(matches) == 1 else None

        graph: nx.DiGraph = nx.DiGraph()
        graph.add_nodes_from(notes)
        broken: dict[Note, frozenset[str]] = {}

        for note in notes:
            unresolved: set[str] = set()
            for target in note.links:
                resolved = resolve(target)
                if resolved is None:
                    unresolved.add(target)
                elif resolved is not note:
                    graph.add_edge(note, resolved)
            if unresolved:
                broken[note] = frozenset(unresolved)

        graph.graph["broken"] = broken
        return graph


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
        for line in self.lines():
            if not line or _HEADING.match(line):
                continue
            parts = line.split()
            if all(_WIKILINK.match(part) for part in parts):
                continue
            return False
        return True

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

    @cached_property
    def links(self):
        """
        The wikilink targets referenced from this note's body.

        Section anchors (``#section``) and display aliases (``|alias``) are
        stripped; only the link target itself is returned. Wikilinks
        appearing inside fenced or inline code are excluded — markdown-it
        is used to tokenize the body so code regions can be skipped without
        hand-rolling a stripper.
        """
        targets: set[str] = set()
        for token in _MD.parse(self._parsed.content):
            if token.type != "inline" or not token.children:
                continue
            for child in token.children:
                if child.type != "text":
                    continue
                for match in _WIKILINK_TARGET.finditer(child.content):
                    targets.add(match.group(1).strip())
        return frozenset(targets)

    def subpath(self) -> str:
        """
        The subpath of this note inside of the vault, without extension.

        Always uses forward-slash separators, matching Obsidian's display
        convention (and wikilink syntax) regardless of the host platform.
        """
        path = self.path.relative_to(self._vault.path).with_suffix("")
        return path.as_posix()

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
