from pathlib import Path

import networkx as nx
import pytest

from obsidiana.vault import Note, Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return Vault(path=tmp_path)


def write_note(vault: Vault, name: str, body: str = "") -> None:
    path = vault.path / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def find(vault: Vault, stem: str) -> Note:
    return next(note for note in vault.notes() if note.path.stem == stem)


class TestLinks:
    def test_basic(self, vault):
        write_note(vault, "A", "Links to [[B]].")
        assert find(vault, "A").links == frozenset(["B"])

    def test_alias_stripped(self, vault):
        write_note(vault, "A", "See [[B|the bee]].")
        assert find(vault, "A").links == frozenset(["B"])

    def test_anchor_stripped(self, vault):
        write_note(vault, "A", "Section [[B#intro]].")
        assert find(vault, "A").links == frozenset(["B"])

    def test_anchor_and_alias_stripped(self, vault):
        write_note(vault, "A", "[[B#intro|the intro]].")
        assert find(vault, "A").links == frozenset(["B"])

    def test_multiple_per_line(self, vault):
        write_note(vault, "A", "Both [[B]] and [[C]] here.")
        assert find(vault, "A").links == frozenset(["B", "C"])

    def test_embed_captured(self, vault):
        write_note(vault, "A", "Transclusion: ![[B]].")
        assert find(vault, "A").links == frozenset(["B"])

    def test_no_links(self, vault):
        write_note(vault, "A", "Nothing references anything here.")
        assert find(vault, "A").links == frozenset()

    def test_fenced_code_block_excluded(self, vault):
        write_note(
            vault,
            "A",
            "Real [[B]].\n\n```\n[[Fake]] in fenced block\n```\n",
        )
        assert find(vault, "A").links == frozenset(["B"])

    def test_inline_code_excluded(self, vault):
        write_note(vault, "A", "Real [[B]] but `[[Fake]]` is in code.")
        assert find(vault, "A").links == frozenset(["B"])

    def test_frontmatter_not_scanned(self, vault):
        (vault.path / "A.md").write_text(
            "---\nparent: '[[FromFrontmatter]]'\n---\n\nBody [[B]].",
        )
        assert find(vault, "A").links == frozenset(["B"])

    def test_links_inside_heading(self, vault):
        write_note(vault, "A", "# Refers to [[B]]\n")
        assert find(vault, "A").links == frozenset(["B"])


class TestGraph:
    def test_basic_edge(self, vault):
        write_note(vault, "A", "[[B]]")
        write_note(vault, "B", "")
        graph = vault.graph()
        assert graph.has_edge(find(vault, "A"), find(vault, "B"))

    def test_self_loop_skipped(self, vault):
        write_note(vault, "Selfie", "I link to [[Selfie]].")
        graph = vault.graph()
        assert graph.number_of_edges() == 0
        assert list(nx.isolates(graph)) == [find(vault, "Selfie")]

    def test_subpath_resolution(self, vault):
        write_note(vault, "A", "[[folder/B]]")
        write_note(vault, "folder/B", "")
        graph = vault.graph()
        assert graph.has_edge(find(vault, "A"), find(vault, "B"))

    def test_stem_resolution_when_unique(self, vault):
        write_note(vault, "A", "[[B]]")
        write_note(vault, "folder/B", "")
        graph = vault.graph()
        assert graph.has_edge(find(vault, "A"), find(vault, "B"))

    def test_ambiguous_stem_is_broken(self, vault):
        write_note(vault, "A", "[[B]]")
        write_note(vault, "folder1/B", "")
        write_note(vault, "folder2/B", "")
        graph = vault.graph()
        assert graph.out_degree(find(vault, "A")) == 0
        assert graph.graph["broken"][find(vault, "A")] == frozenset(["B"])

    def test_missing_target_is_broken(self, vault):
        write_note(vault, "A", "[[Ghost]]")
        graph = vault.graph()
        assert graph.graph["broken"][find(vault, "A")] == frozenset(["Ghost"])

    def test_case_insensitive_resolution(self, vault):
        write_note(vault, "A", "[[foo]]")
        write_note(vault, "Foo", "")
        graph = vault.graph()
        assert graph.has_edge(find(vault, "A"), find(vault, "Foo"))

    def test_case_insensitive_subpath_resolution(self, vault):
        write_note(vault, "A", "[[FOLDER/foo]]")
        write_note(vault, "folder/Foo", "")
        graph = vault.graph()
        assert graph.has_edge(find(vault, "A"), find(vault, "Foo"))

    def test_isolated_includes_no_links_either_way(self, vault):
        write_note(vault, "A", "[[B]]")
        write_note(vault, "B", "")
        write_note(vault, "Lonely", "no links")
        graph = vault.graph()
        assert list(nx.isolates(graph)) == [find(vault, "Lonely")]

    def test_orphan_has_outgoing_but_no_incoming(self, vault):
        write_note(vault, "A", "[[B]]")
        write_note(vault, "B", "")
        graph = vault.graph()
        sources = {n for n, d in graph.in_degree() if d == 0}
        assert sources == {find(vault, "A")}

    def test_leaf_has_incoming_but_no_outgoing(self, vault):
        write_note(vault, "A", "[[B]]")
        write_note(vault, "B", "")
        graph = vault.graph()
        sinks = {n for n, d in graph.out_degree() if d == 0}
        assert sinks == {find(vault, "B")}

    def test_broken_omitted_when_all_resolve(self, vault):
        write_note(vault, "A", "[[B]]")
        write_note(vault, "B", "")
        assert vault.graph().graph["broken"] == {}
