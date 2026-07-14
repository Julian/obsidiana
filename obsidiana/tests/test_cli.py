import json

from click.testing import CliRunner
from rich.console import Console
import pytest

from obsidiana._cli import main

SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "status": {
            "enum": ["empty", "draft", "wip", "finished", "in-progress"],
        },
        "tags": {
            "type": "array",
            "items": {"type": "string", "pattern": "^[a-z][a-z0-9/_-]*$"},
        },
        "source": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
                {
                    "type": "object",
                    "properties": {"isbn": {"type": "string"}},
                    "required": ["isbn"],
                },
                {"type": "string"},
            ],
        },
    },
    "required": ["status"],
    "additionalProperties": False,
}


@pytest.fixture(autouse=True)
def plain_console(monkeypatch):
    # A wide, un-colored console so command output is stable to assert on:
    # no ANSI escapes, and no wrapping to split messages across lines.
    monkeypatch.setattr(
        "obsidiana._cli.CONSOLE",
        Console(no_color=True, width=200),
    )


def write_vault(tmp_path, notes, schema=SCHEMA):
    (tmp_path / "schema.json").write_text(json.dumps(schema))
    for name, frontmatter in notes.items():
        path = tmp_path / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\n{frontmatter}\n---\n\nBody of {name}.\n")
        path.chmod(0o644)
    return tmp_path


def run(tmp_path, *args):
    argv = ["validate", "--vault", str(tmp_path), *args]
    return CliRunner().invoke(main, argv)


class TestExitStatus:
    def test_all_valid_exits_zero(self, tmp_path):
        result = run(write_vault(tmp_path, {"A": "status: draft"}))
        assert result.exit_code == 0
        assert "All notes are" in result.output

    def test_invalid_exits_one_with_summary(self, tmp_path):
        result = run(write_vault(tmp_path, {"A": "status: bogus"}))
        assert result.exit_code == 1
        assert "1 problem(s) across 1 note(s)" in result.output

    def test_summary_counts_across_notes(self, tmp_path):
        result = run(
            write_vault(
                tmp_path,
                {"A": "status: bogus", "B": "status: draft\ntag: x"},
            ),
        )
        assert result.exit_code == 1
        assert "2 problem(s) across 2 note(s)" in result.output

    def test_bad_file_mode_reported(self, tmp_path):
        vault = write_vault(tmp_path, {"A": "status: draft"})
        (vault / "A.md").chmod(0o600)
        result = run(vault)
        assert result.exit_code == 1
        assert "file mode" in result.output

    def test_duplicate_id_detected(self, tmp_path):
        result = run(
            write_vault(
                tmp_path,
                {
                    "A": "status: draft\nid: shared",
                    "B": "status: draft\nid: shared",
                },
            ),
        )
        assert result.exit_code == 1
        assert "not unique" in result.output


class TestRendering:
    def test_field_path_and_message(self, tmp_path):
        vault = write_vault(tmp_path, {"A": "status: draft\ntags: [Bad]"})
        result = run(vault)
        assert (
            "tags[0]: 'Bad' does not match '^[a-z][a-z0-9/_-]*$'"
            in result.output
        )

    def test_anyof_drills_into_closest_branch(self, tmp_path):
        # Instead of "is not valid under any of the given schemas", the
        # branch the instance most nearly matched is blamed.
        result = run(
            write_vault(tmp_path, {"A": "status: draft\nsource:\n  url: 1"}),
        )
        assert "source.url: 1 is not of type 'string'" in result.output
        assert "not valid under any" not in result.output

    def test_root_error_renders_without_a_path_prefix(self, tmp_path):
        result = run(write_vault(tmp_path, {"A": "tags: [good]"}))
        assert "'status' is a required property" in result.output

    def test_whole_note_errors_sort_before_field_errors(self, tmp_path):
        # A missing top-level ``status`` (path ``$``) should print above a
        # per-field problem (path ``$.tags[0]``).
        result = run(write_vault(tmp_path, {"A": "tags: [Bad]"}))
        assert result.output.index("required property") < result.output.index(
            "does not match",
        )

    def test_markup_like_value_stays_literal(self, tmp_path):
        # A value that looks like console markup must be shown verbatim,
        # not interpreted (which would strip the brackets).
        result = run(write_vault(tmp_path, {"A": "status: '[bold]nope[/]'"}))
        assert "[bold]nope[/]" in result.output


class TestSuggestions:
    def test_enum_typo_suggests_closest(self, tmp_path):
        result = run(write_vault(tmp_path, {"A": "status: in-progres"}))
        assert "did you mean 'in-progress'" in result.output

    def test_enum_far_value_gets_no_suggestion(self, tmp_path):
        result = run(write_vault(tmp_path, {"A": "status: zzz"}))
        assert result.exit_code == 1
        assert "did you mean" not in result.output

    def test_mistyped_key_suggests_property(self, tmp_path):
        result = run(write_vault(tmp_path, {"A": "status: draft\ntag: x"}))
        assert "did you mean 'tags'" in result.output

    def test_valid_key_is_not_offered_as_a_suggestion(self, tmp_path):
        # Regression: a valid key (``status``) must not be suggested for an
        # unrelated unexpected key (``titel``) by matching itself.
        result = run(write_vault(tmp_path, {"A": "status: draft\ntitel: x"}))
        assert "did you mean" not in result.output


class TestFilter:
    def test_focuses_a_single_note_ignoring_others(self, tmp_path):
        vault = write_vault(
            tmp_path,
            {"A": "status: bogus", "B": "status: draft"},
        )
        result = run(vault, "B")
        assert result.exit_code == 0
        assert "All notes are" in result.output

    def test_reports_the_focused_note_when_invalid(self, tmp_path):
        vault = write_vault(
            tmp_path,
            {"A": "status: draft", "B": "status: bogus"},
        )
        result = run(vault, "B")
        assert result.exit_code == 1
        assert "1 problem(s) across 1 note(s)" in result.output

    def test_matches_by_subpath(self, tmp_path):
        vault = write_vault(tmp_path, {"folder/Deep": "status: bogus"})
        result = run(vault, "folder/Deep")
        assert result.exit_code == 1
        assert "problem(s)" in result.output

    def test_no_match_exits_one(self, tmp_path):
        result = run(write_vault(tmp_path, {"A": "status: draft"}), "nope")
        assert result.exit_code == 1
        assert "No note matching" in result.output
