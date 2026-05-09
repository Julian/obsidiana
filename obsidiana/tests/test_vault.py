from pathlib import Path

import networkx as nx
import pytest

from obsidiana.vault import MarkdownLink, Note, Vault, Wikilink


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return Vault(path=tmp_path)


def write_note(
    vault: Vault,
    name: str,
    body: str = "",
    frontmatter: str = "",
) -> None:
    path = vault.path / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    block = f"---\n{frontmatter}\n---\n\n" if frontmatter else ""
    path.write_text(f"{block}{body}")


def find(vault: Vault, stem: str) -> Note:
    return next(note for note in vault.notes() if note.path.stem == stem)


def wiki(*targets):
    return frozenset(Wikilink(target=t) for t in targets)


def md(*targets):
    return frozenset(MarkdownLink(target=t) for t in targets)


class TestLinks:
    def test_basic_wikilink(self, vault):
        write_note(vault, "A", "Links to [[B]].")
        assert find(vault, "A").links == wiki("B")

    def test_alias_stripped(self, vault):
        write_note(vault, "A", "See [[B|the bee]].")
        assert find(vault, "A").links == wiki("B")

    def test_anchor_stripped(self, vault):
        write_note(vault, "A", "Section [[B#intro]].")
        assert find(vault, "A").links == wiki("B")

    def test_anchor_and_alias_stripped(self, vault):
        write_note(vault, "A", "[[B#intro|the intro]].")
        assert find(vault, "A").links == wiki("B")

    def test_multiple_wikilinks_per_line(self, vault):
        write_note(vault, "A", "Both [[B]] and [[C]] here.")
        assert find(vault, "A").links == wiki("B", "C")

    def test_embed_captured(self, vault):
        write_note(vault, "A", "Transclusion: ![[B]].")
        assert find(vault, "A").links == wiki("B")

    def test_no_links(self, vault):
        write_note(vault, "A", "Nothing references anything here.")
        assert find(vault, "A").links == frozenset()

    def test_fenced_code_block_excluded(self, vault):
        write_note(
            vault,
            "A",
            "Real [[B]].\n\n```\n[[Fake]] and [fake](fake.md)\n```\n",
        )
        assert find(vault, "A").links == wiki("B")

    def test_inline_code_excluded(self, vault):
        write_note(vault, "A", "Real [[B]] but `[[Fake]]` is in code.")
        assert find(vault, "A").links == wiki("B")

    def test_frontmatter_not_scanned(self, vault):
        write_note(
            vault,
            "A",
            body="Body [[B]].",
            frontmatter="parent: '[[FromFrontmatter]]'",
        )
        assert find(vault, "A").links == wiki("B")

    def test_links_inside_heading(self, vault):
        write_note(vault, "A", "# Refers to [[B]]\n")
        assert find(vault, "A").links == wiki("B")

    def test_basic_markdown_link(self, vault):
        write_note(vault, "A", "See [it](B.md) for more.")
        assert find(vault, "A").links == md("B")

    def test_markdown_link_without_extension(self, vault):
        write_note(vault, "A", "[no ext](B)")
        assert find(vault, "A").links == md("B")

    def test_markdown_link_relative_to_note_dir(self, vault):
        write_note(vault, "folder/A", "[sib](sibling.md)")
        assert find(vault, "A").links == md("folder/sibling")

    def test_markdown_link_parent_dir(self, vault):
        write_note(vault, "folder/A", "[up](../top.md)")
        assert find(vault, "A").links == md("top")

    def test_markdown_link_vault_rooted(self, vault):
        write_note(vault, "folder/A", "[abs](/other/top.md)")
        assert find(vault, "A").links == md("other/top")

    def test_markdown_link_url_decoded(self, vault):
        write_note(vault, "A", "[enc](my%20note.md)")
        assert find(vault, "A").links == md("my note")

    def test_markdown_link_anchor_stripped(self, vault):
        write_note(vault, "A", "[sec](B.md#intro)")
        assert find(vault, "A").links == md("B")

    def test_markdown_link_external_skipped(self, vault):
        write_note(
            vault,
            "A",
            "[a](https://x.com) [b](mailto:x@y) [c](tel:+1) [real](B.md)",
        )
        assert find(vault, "A").links == md("B")

    def test_markdown_link_anchor_only_skipped(self, vault):
        write_note(vault, "A", "[here](#section) and [real](B.md)")
        assert find(vault, "A").links == md("B")

    def test_image_not_treated_as_note_reference(self, vault):
        write_note(vault, "A", "![alt](pic.png) and [real](B.md)")
        assert find(vault, "A").links == md("B")

    def test_wikilink_and_markdown_link_to_same_target_distinct(self, vault):
        write_note(vault, "A", "[[B]] and [also](B.md)")
        assert find(vault, "A").links == wiki("B") | md("B")

    def test_image_wikilink_excluded(self, vault):
        write_note(vault, "A", "Embed: ![[diagram.png]]\n\n[[B]]")
        assert find(vault, "A").links == wiki("B")

    def test_audio_wikilink_excluded(self, vault):
        write_note(vault, "A", "Listen: ![[recording.mp3]]\n\n[[B]]")
        assert find(vault, "A").links == wiki("B")

    def test_pdf_markdown_link_excluded(self, vault):
        write_note(vault, "A", "[paper](paper.pdf) and [real](B.md)")
        assert find(vault, "A").links == md("B")

    def test_dotted_stem_needs_explicit_md_extension(self, vault):
        # The allowlist treats ``v1.0`` as extension ``.0`` (not a note),
        # so reference notes with dotted stems via the explicit extension.
        write_note(vault, "A", "[[v1.0]] but [[v1.0.md]] works.")
        assert find(vault, "A").links == wiki("v1.0")

    def test_reference_style_markdown_link(self, vault):
        write_note(
            vault,
            "A",
            "See [the note][ref] for context.\n\n[ref]: B.md\n",
        )
        assert find(vault, "A").links == md("B")

    def test_wikilink_with_explicit_md_extension(self, vault):
        write_note(vault, "A", "[[B.md]]")
        assert find(vault, "A").links == wiki("B")

    def test_wikilink_with_md_extension_and_alias(self, vault):
        write_note(vault, "A", "[[B.md|the bee]]")
        assert find(vault, "A").links == wiki("B")

    def test_archive_excluded(self, vault):
        write_note(vault, "A", "[bundle](backup.zip) and [real](B.md)")
        assert find(vault, "A").links == md("B")

    def test_docx_excluded(self, vault):
        write_note(vault, "A", "![[handout.docx]] and [[B]]")
        assert find(vault, "A").links == wiki("B")

    def test_whitespace_only_wikilink_excluded(self, vault):
        write_note(vault, "A", "[[   ]] and [[B]]")
        assert find(vault, "A").links == wiki("B")


class TestAliases:
    def test_missing(self, vault):
        write_note(vault, "A", "")
        assert find(vault, "A").aliases == frozenset()

    def test_empty_value(self, vault):
        # ``aliases:`` parses as None in YAML; should behave like missing.
        write_note(vault, "A", body="body", frontmatter="aliases:")
        assert find(vault, "A").aliases == frozenset()

    def test_list(self, vault):
        write_note(vault, "A", body="body", frontmatter="aliases: [One, Two]")
        assert find(vault, "A").aliases == frozenset(["One", "Two"])


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

    def test_markdown_link_creates_edge(self, vault):
        write_note(vault, "A", "[link](B.md)")
        write_note(vault, "B", "")
        graph = vault.graph()
        assert graph.has_edge(find(vault, "A"), find(vault, "B"))

    def test_markdown_link_resolves_via_subpath(self, vault):
        write_note(vault, "A", "[link](folder/B.md)")
        write_note(vault, "folder/B", "")
        graph = vault.graph()
        assert graph.has_edge(find(vault, "A"), find(vault, "B"))

    def test_markdown_link_does_not_use_stem_fallback(self, vault):
        # Wikilink would resolve [[B]] to folder/B via stem fallback.
        # A markdown link with an explicit (wrong) path should NOT.
        write_note(vault, "A", "[wrong](B.md)")
        write_note(vault, "folder/B", "")
        graph = vault.graph()
        assert graph.number_of_edges() == 0
        assert graph.graph["broken"][find(vault, "A")] == frozenset(["B"])

    def test_wikilink_still_uses_stem_fallback(self, vault):
        write_note(vault, "A", "[[B]]")
        write_note(vault, "folder/B", "")
        graph = vault.graph()
        assert graph.has_edge(find(vault, "A"), find(vault, "B"))

    def test_markdown_link_self_loop_skipped(self, vault):
        write_note(vault, "Selfie", "[me](Selfie.md)")
        graph = vault.graph()
        assert graph.number_of_edges() == 0

    def test_markdown_link_external_url_not_broken(self, vault):
        write_note(vault, "A", "[ext](https://example.com)")
        graph = vault.graph()
        assert graph.graph["broken"] == {}

    def test_image_embed_not_broken(self, vault):
        write_note(vault, "A", "![[chart.png]] and ![[clip.mp4]]")
        graph = vault.graph()
        assert graph.graph["broken"] == {}

    def test_malformed_url_skipped(self, vault):
        write_note(vault, "A", "[bad](http://[invalid) [real](B.md)")
        write_note(vault, "B", "")
        graph = vault.graph()
        assert graph.graph["broken"] == {}
        assert graph.has_edge(find(vault, "A"), find(vault, "B"))

    def test_alias_resolution(self, vault):
        write_note(vault, "real", frontmatter="aliases: [Alt Name]")
        write_note(vault, "A", "[[Alt Name]]")
        graph = vault.graph()
        assert graph.has_edge(find(vault, "A"), find(vault, "real"))

    def test_alias_resolution_case_insensitive(self, vault):
        write_note(vault, "real", frontmatter="aliases: [Cute Name]")
        write_note(vault, "A", "[[cute name]]")
        graph = vault.graph()
        assert graph.has_edge(find(vault, "A"), find(vault, "real"))

    def test_multiple_aliases(self, vault):
        write_note(vault, "real", frontmatter="aliases:\n  - One\n  - Two")
        write_note(vault, "A", "[[One]]")
        write_note(vault, "B", "[[Two]]")
        graph = vault.graph()
        assert graph.has_edge(find(vault, "A"), find(vault, "real"))
        assert graph.has_edge(find(vault, "B"), find(vault, "real"))

    def test_ambiguous_alias_is_broken(self, vault):
        write_note(vault, "first", frontmatter="aliases: [Shared]")
        write_note(vault, "second", frontmatter="aliases: [Shared]")
        write_note(vault, "A", "[[Shared]]")
        graph = vault.graph()
        assert graph.out_degree(find(vault, "A")) == 0
        assert graph.graph["broken"][find(vault, "A")] == frozenset(["Shared"])

    def test_markdown_link_does_not_use_alias(self, vault):
        write_note(vault, "real", frontmatter="aliases: [Alt]")
        write_note(vault, "A", "[wrong](Alt.md)")
        graph = vault.graph()
        assert graph.number_of_edges() == 0
        assert graph.graph["broken"][find(vault, "A")] == frozenset(["Alt"])

    def test_stem_beats_alias_when_both_match(self, vault):
        # ``Foo.md`` exists; another note has alias ``Foo``. Stem wins.
        write_note(vault, "Foo", "")
        write_note(vault, "other", frontmatter="aliases: [Foo]")
        write_note(vault, "A", "[[Foo]]")
        graph = vault.graph()
        assert graph.has_edge(find(vault, "A"), find(vault, "Foo"))
        assert not graph.has_edge(find(vault, "A"), find(vault, "other"))
