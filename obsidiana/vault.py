"""
Python API for Obsidian vaults.
"""

from datetime import date
from functools import cached_property
from pathlib import Path
from urllib.parse import unquote
import os
import posixpath
import re
import subprocess

from attrs import evolve, frozen
from markdown_it import MarkdownIt
from url import URL, URLError
import frontmatter
import networkx as nx

_HEADING = re.compile("^#+ ")
_WIKILINK = re.compile(r"\[\[[^[]+\]\]")
_WIKILINK_TARGET = re.compile(
    r"\[\[([^|#\]\n]+)(?:#[^|\]\n]*)?(?:\|[^\]\n]*)?\]\]",
)
_MD = MarkdownIt()
_NOTE_EXTS = frozenset({"", ".md"})
_URL_ROOT = URL.parse("file:///")


def _is_note_target(target: str) -> bool:
    """
    Whether a link target plausibly refers to a markdown note.

    Only ``.md`` and extension-less targets are accepted; anything else
    (images, audio, PDFs, archives, ...) is an attachment, not a note.
    """
    return posixpath.splitext(target)[1].lower() in _NOTE_EXTS


@frozen
class _Index:
    """
    Lookup tables used to resolve a reference target to a note.
    """

    by_subpath: "dict[str, list[Note]]"
    by_stem: "dict[str, list[Note]]"
    by_alias: "dict[str, list[Note]]"


def _unique(matches) -> "Note | None":
    return matches[0] if len(matches) == 1 else None


@frozen
class Reference:
    """
    A textual reference from one note to another.

    Subclassed by :class:`Wikilink` and :class:`MarkdownLink`, which
    differ in how their ``target`` strings are resolved against the
    vault's lookup tables.
    """

    target: str

    def resolve(self, index: _Index) -> "Note | None":
        """
        Resolve this reference to a note in the vault, or ``None``.
        """
        raise NotImplementedError


@frozen
class Wikilink(Reference):
    """
    A wikilink reference (``[[Target]]``).

    Resolved by trying, in order, an exact subpath match, a unique stem
    match, and finally a unique frontmatter-alias match.
    """

    def resolve(self, index: _Index) -> "Note | None":
        """
        Resolve via subpath, then unique stem, then unique alias.
        """
        target = self.target.lower()
        for table in (index.by_subpath, index.by_stem, index.by_alias):
            if (resolved := _unique(table.get(target, ()))) is not None:
                return resolved
        return None


@frozen
class MarkdownLink(Reference):
    """
    A markdown-style relative link (``[text](note.md)``).

    Resolved only by exact subpath match — the user wrote an explicit
    path, so falling back to stem or alias would be incorrect.
    """

    def resolve(self, index: _Index) -> "Note | None":
        """
        Resolve via exact subpath match only.
        """
        return _unique(index.by_subpath.get(self.target.lower(), ()))


def _resolve_md_href(href: str, base: URL) -> str | None:
    """
    Convert a markdown-link ``href`` into a vault-relative subpath.

    Returns ``None`` for anchors-only, external URLs (any href with a
    URL scheme), malformed URLs, and other hrefs that don't refer to a
    note in the vault.

    The href is resolved against ``base`` (typically
    ``file:///{note_dir}/``), so the WHATWG URL machinery handles
    relative-vs-absolute paths, fragment stripping, and ``..``
    normalization in one step.
    """
    if not href or href.startswith("#"):
        return None
    try:
        joined = base.join(href)
    except URLError:
        return None
    if joined.scheme != "file":
        return None
    target = unquote(joined.path).lstrip("/")
    if not target or not _is_note_target(target):
        return None
    return target.removesuffix(".md")


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

        Reference targets which don't resolve to any note (or which
        resolve ambiguously to multiple) are recorded in the graph-level
        attribute ``broken``, mapping each note to the set of unresolved
        targets it references.

        Resolution rules differ by reference type — see
        :meth:`Wikilink.resolve` and :meth:`MarkdownLink.resolve`. All
        lookups are case-insensitive, matching Obsidian's behavior on
        case-insensitive filesystems.
        """
        notes = list(self.notes())

        by_stem: dict[str, list[Note]] = {}
        by_subpath: dict[str, list[Note]] = {}
        by_alias: dict[str, list[Note]] = {}
        for note in notes:
            by_stem.setdefault(note.path.stem.lower(), []).append(note)
            by_subpath.setdefault(note.subpath().lower(), []).append(note)
            for alias in note.aliases:
                by_alias.setdefault(alias.lower(), []).append(note)
        index = _Index(
            by_subpath=by_subpath,
            by_stem=by_stem,
            by_alias=by_alias,
        )

        graph: nx.DiGraph = nx.DiGraph()
        graph.add_nodes_from(notes)
        broken: dict[Note, frozenset[str]] = {}

        for note in notes:
            unresolved: set[str] = set()
            for ref in note.links:
                resolved = ref.resolve(index)
                if resolved is None:
                    unresolved.add(ref.target)
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
    def aliases(self) -> frozenset[str]:
        """
        Alternative names this note can be referenced by, from frontmatter.

        Obsidian's ``aliases`` field, which makes ``[[Alias]]`` resolve to
        this note. An ``aliases:`` key with no value (parsed as ``None``
        from YAML) is treated the same as a missing key.
        """
        return frozenset(self.frontmatter.get("aliases") or ())

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
        References to other notes from this note's body.

        Both wikilinks (``[[Target]]``) and markdown relative links
        (``[text](note.md)``) are extracted. Section anchors, display
        aliases, and ``.md`` extensions are stripped. Markdown link hrefs
        are URL-decoded and resolved against this note's directory; hrefs
        that point at external URLs, anchors, or non-note resources are
        skipped.

        References inside fenced or inline code are excluded — markdown-it
        is used to tokenize the body so code regions can be skipped
        without hand-rolling a stripper.
        """
        refs: set[Reference] = set()
        rel_parent = self.path.parent.relative_to(self._vault.path)
        relative_dir = rel_parent.as_posix()
        base = (
            URL.parse(f"file:///{relative_dir}/")
            if relative_dir != "."
            else _URL_ROOT
        )
        for token in _MD.parse(self._parsed.content):
            if token.type != "inline" or not token.children:
                continue
            for child in token.children:
                if child.type == "text":
                    for match in _WIKILINK_TARGET.finditer(child.content):
                        target = match.group(1).strip()
                        if not target or not _is_note_target(target):
                            continue
                        refs.add(Wikilink(target=target.removesuffix(".md")))
                elif child.type == "link_open":
                    href = (child.attrs or {}).get("href", "")
                    resolved = _resolve_md_href(str(href), base)
                    if resolved is not None:
                        refs.add(MarkdownLink(target=resolved))
        return frozenset(refs)

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
