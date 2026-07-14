from collections import Counter, defaultdict
from difflib import get_close_matches
from pathlib import Path
import json
import os
import re
import shlex
import subprocess
import sys

from jsonschema.exceptions import ValidationError, best_match
from jsonschema.validators import validator_for
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree
import networkx as nx
import rich_click as click

from obsidiana.vault import Vault

CONSOLE = Console()
MODE = 0o644


class _Vault(click.ParamType):
    """
    Select an Obsidian vault.
    """

    name = "vault"

    def convert(
        self,
        value: str | Vault,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> Vault:
        if not isinstance(value, str):
            return value
        return Vault(path=Path(value))


def default_vault() -> Path:
    """
    A default vault location.
    """
    path = Path(os.environ.get("OBSIDIAN_VAULT", os.curdir))
    return Vault(path=path)


VAULT = click.option(
    "--vault",
    default=default_vault,
    type=_Vault(),
    help="the path to an Obsidian vault",
)


@click.group(context_settings=dict(help_option_names=["--help", "-h"]))
@click.version_option(prog_name="ob", package_name="obsidiana")
def main():
    """
    Tools for working with Obsidian vaults.
    """


@main.command()
@VAULT
def up(vault):
    """
    Update the vault to the latest branch.
    """
    try:
        subprocess.run(
            ["git", "fetch"],  # noqa: S607
            cwd=vault.path,
            check=True,
        )
    except subprocess.CalledProcessError:
        CONSOLE.print("[red]Unable to fetch new changes.[/red]")
        sys.exit(1)

    result = subprocess.run(
        [  # noqa: S607
            "git",
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(refname:short)",
        ],
        cwd=vault.path,
        capture_output=True,
        text=True,
        check=True,
    )
    latest_ref = result.stdout.splitlines()[0]

    try:
        subprocess.run(  # noqa: S603
            ["git", "merge", "--ff-only", latest_ref],  # noqa: S607
            cwd=vault.path,
            check=True,
        )
    except subprocess.CalledProcessError:
        CONSOLE.print(
            "[red]There are local changes preventing updating.[/red]",
        )
        sys.exit(1)


def _closest(value: object, candidates) -> str | None:
    """
    The single closest string candidate to ``value``, or ``None``.

    Used to turn a validation failure into a "did you mean ...?" hint.
    Only strings are compared -- a non-string value can't be a typo of
    anything in a way worth suggesting.
    """
    if not isinstance(value, str):
        return None
    options = [each for each in candidates if isinstance(each, str)]
    matches = get_close_matches(value, options, n=1)
    return matches[0] if matches else None


def _suggest(error: ValidationError) -> str:
    """
    The error's message, plus a "did you mean ...?" hint when one fits.

    The common failure in a notes vault is a typo -- a misspelled
    ``status`` value or a mistyped frontmatter key -- so for ``enum`` and
    ``additionalProperties`` errors we look for a near-match among the
    allowed values or property names.
    """
    if error.validator == "enum":
        hint = _closest(error.instance, error.validator_value)
    elif error.validator == "additionalProperties":
        schema = error.schema if isinstance(error.schema, dict) else {}
        allowed = schema.get("properties", {})
        hint = next(
            (
                match
                for key in error.instance
                if key not in allowed
                and (match := _closest(key, allowed)) is not None
            ),
            None,
        )
    else:
        hint = None

    if hint is None:
        return error.message
    return f"{error.message} (did you mean {hint!r}?)"


def _location(error: ValidationError):
    """
    A sort key placing whole-note errors first, then fields by path.
    """
    resolved = best_match([error])
    return resolved.json_path, resolved.message


def _format_error(error: ValidationError) -> Text:
    """
    Render a validation error compactly as ``field.path: message``.

    ``best_match`` descends ``anyOf``/``oneOf``/``not`` errors into the
    branch the instance most nearly matched -- turning the unhelpful "is
    not valid under any of the given schemas" into the specific leaf
    failure -- and returns the error untouched when it has no context, so
    it is always safe to call.

    A ``Text`` (not a markup string) is returned so a note value
    containing ``[...]`` can't be misparsed as console markup. Errors with
    no instance path (our hand-built content rules, and root-level
    failures such as ``required``) render as just their message.
    """
    error = best_match([error])
    message = _suggest(error)
    if not error.absolute_path:
        return Text(message)
    where = error.json_path.removeprefix("$.")
    return Text.assemble((where, "cyan"), ": ", message)


@main.command()
@click.argument("only", required=False)
@VAULT
def validate(vault, only):
    """
    Validate all note frontmatter in the vault against a JSON Schema.

    Also apply some simple validation rules for the content itself. Pass
    ONLY (a note subpath or name) to validate just that one note.
    """
    schema = json.loads(vault.child("schema.json").read_text(encoding="utf-8"))
    Validator = validator_for(schema)
    Validator.check_schema(schema)
    validator = Validator(schema, format_checker=Validator.FORMAT_CHECKER)

    notes = vault.notes()
    if only is not None:
        want = only.removesuffix(".md").lower()
        notes = [
            note
            for note in notes
            if want in {note.subpath().lower(), note.path.stem.lower()}
        ]
        if not notes:
            CONSOLE.print(
                Text.assemble("No note matching ", (only, "red"), "."),
            )
            sys.exit(1)

    tree = Tree("[red]Invalid Notes[/red]")

    ids = defaultdict(list)
    need_triage = 0
    problems = 0
    for note in notes:
        errors = []

        mode = note.path.stat().st_mode & 0o777
        if mode != MODE:
            errors.append(
                ValidationError(
                    f"Note has file mode {mode:o} (instead of {MODE:o}).",
                ),
            )

        if note.awaiting_triage():
            need_triage += 1
        else:
            seen = ids[note.id]
            seen.append(note)

            errors.extend(validator.iter_errors(note.frontmatter))
            if len(seen) > 1:
                rest = ", ".join(note.subpath() for note in seen)
                errors.append(
                    ValidationError(
                        f"ID is not unique (duplicated by {rest})",
                    ),
                )

            if note.is_empty:
                if note.status == "finished":
                    errors.append(
                        ValidationError(
                            "Note is empty but has a finished status.",
                        ),
                    )
            elif note.status == "empty":
                errors.append(
                    ValidationError(
                        "Note is not empty but has empty status.",
                    ),
                )
            else:
                # FIXME: Get rid of/reimplement python-frontmatter since it
                #        makes this validation impossible (by eating \n's)
                contents = note.path.read_text(
                    encoding="utf-8",
                ).removeprefix("---\n")
                end, _, rest = contents.partition("---")
                newline_count = 0
                for each in rest:
                    if each == "\n":
                        newline_count += 1
                    else:
                        break

                if newline_count != 2:  # noqa: PLR2004
                    errors.append(
                        ValidationError(
                            "Note content must have exactly one empty "
                            "line after the frontmatter, "
                            f"not {newline_count - 1}.",
                        ),
                    )

        if not errors:
            continue

        problems += len(errors)
        subtree = tree.add(Text(note.subpath()))
        for error in sorted(errors, key=_location):
            subtree.add(_format_error(error))

    if tree.children:
        CONSOLE.print(tree)
        triage = f" ({need_triage} needing triage)" if need_triage else ""
        CONSOLE.print(
            f"\n[red]{problems}[/red] problem(s) across "
            f"[red]{len(tree.children)}[/red] note(s){triage}.",
        )
        sys.exit(1)
    else:
        end = f" ({need_triage} needing triage)" if need_triage else ""
        CONSOLE.print(f"All notes are [green]valid[/green]{end}.")


@main.command()
@VAULT
def triage(vault):
    """
    Triage any notes waiting for review.
    """
    i = 0
    for i, note in enumerate(vault.needs_triage()):  # noqa: B007
        try:
            edited = note.edit()
        except subprocess.CalledProcessError as error:
            cmd = shlex.join(str(arg) for arg in error.cmd)
            CONSOLE.print(f"[red]{cmd}[/red] exited with non-zero status.")
            break

        if edited.is_empty:
            subprocess.run(  # noqa: S603
                ["git", "rm", note.path],  # noqa: S607
                cwd=vault.path,
                check=True,
            )
    if i == 0:
        CONSOLE.print("No notes need triaging.")


@main.command()
@VAULT
def todo(vault):
    """
    Show notes and tasks with todos.
    """
    whole_note_todo = set()
    tasks_table = Table("Note", "Task", title="Tasks")

    for note in vault.notes():
        if "todo" in note.tags or "todo/now" in note.tags:
            whole_note_todo.add(note)

        tasks = [line for line in note.lines() if "#todo" in line]
        if tasks:
            panel = Panel("\n".join(tasks), box=box.SIMPLE)
            tasks_table.add_row(note.subpath(), panel)

    todo_panel = Panel(
        "\n".join(sorted(note.subpath() for note in whole_note_todo)),
        title="Notes with #todo tags",
        border_style="cyan",
    )
    CONSOLE.print(todo_panel)

    if tasks_table.row_count > 0:
        CONSOLE.print(tasks_table)


@main.command()
@VAULT
def tags(vault):
    """
    Show all tags used in the vault, ordered by frequency.
    """
    tags = Counter()
    for note in vault.notes():
        tags.update(note.tags)

    table = Table(show_header=True)
    table.add_column("Tag", style="bold cyan")
    table.add_column("Note Count", style="yellow", justify="right")

    for tag, count in tags.most_common():
        table.add_row(tag, str(count))

    CONSOLE.print(table)


@main.command()
@VAULT
def links(vault):
    """
    Output all external links across all notes in the vault.
    """
    link_re = re.compile(r"\[[^]]*\]\((https?://[^)]+)\)")

    for note in vault.notes():
        for line in note.lines():
            for match in link_re.findall(line):
                sys.stdout.write(match)
                sys.stdout.write("\n")


@main.command()
@VAULT
def anki(vault):
    """
    Show all notes labelled for Anki deck inclusion.
    """
    for note in vault.notes():
        if "learn/anki" in note.tags:
            sys.stdout.write(note.subpath())
            sys.stdout.write("\n")


@main.group(name="list")
def list_():
    """
    List notes by graph relationship.
    """


def _print_notes(notes):
    for subpath in sorted(note.subpath() for note in notes):
        sys.stdout.write(f"{subpath}\n")


@list_.command()
@VAULT
def isolated(vault):
    """
    Notes with no incoming or outgoing references.
    """
    _print_notes(nx.isolates(vault.graph()))


@list_.command(aliases=["orphans"])
@VAULT
def sources(vault):
    """
    Notes with no incoming references.
    """
    graph = vault.graph()
    _print_notes(note for note, degree in graph.in_degree() if degree == 0)


@list_.command(aliases=["leaves"])
@VAULT
def sinks(vault):
    """
    Notes with no outgoing references.
    """
    graph = vault.graph()
    _print_notes(note for note, degree in graph.out_degree() if degree == 0)


@list_.command()
@VAULT
def broken(vault):
    """
    References which don't resolve to any note in the vault.

    Output is one ``note<TAB>target`` pair per line.
    """
    broken_links = vault.graph().graph["broken"]
    rows = sorted(
        (note.subpath(), target)
        for note, targets in broken_links.items()
        for target in targets
    )
    for subpath, target in rows:
        sys.stdout.write(f"{subpath}\t{target}\n")
