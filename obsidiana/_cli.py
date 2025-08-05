from collections import Counter, defaultdict
from pathlib import Path
import json
import os
import re
import subprocess
import sys

from jsonschema.exceptions import ValidationError, relevance
from jsonschema.validators import validator_for
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
import rich_click as click

from obsidiana.vault import Vault

CONSOLE = Console()


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
@click.version_option(prog_name="ob")
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


@main.command()
@VAULT
def validate(vault):
    """
    Validate all note frontmatter in the vault against a JSON Schema.

    Also apply some simple validation rules for the content itself.
    """
    schema = json.loads(vault.child("schema.json").read_text())
    Validator = validator_for(schema)
    Validator.check_schema(schema)
    validator = Validator(schema, format_checker=Validator.FORMAT_CHECKER)

    tree = Tree("[red]Invalid Notes[/red]")

    ids = defaultdict(list)
    need_triage = 0
    for note in vault.notes():
        if note.awaiting_triage():
            need_triage += 1
            continue

        seen = ids[note.id]
        seen.append(note)

        errors = sorted(validator.iter_errors(note.frontmatter), key=relevance)
        if len(seen) > 1:
            rest = ", ".join(note.subpath() for note in seen)
            error = ValidationError(f"ID is not unique (duplicated by {rest})")
            errors.append(error)

        if not note.is_empty:
            if note.status == "empty":
                error = ValidationError(
                    "Note is not empty but has empty status.",
                )
                errors.append(error)
            else:
                # FIXME: Get rid of/reimplement python-frontmatter since it
                #        makes this validation not possible (by eating \n's)
                contents = note.path.read_text().removeprefix("---\n")
                end, _, rest = contents.partition("---")
                newline_count = 0
                for each in rest:
                    if each == "\n":
                        newline_count += 1
                    else:
                        break

                if newline_count != 2:  # noqa: PLR2004
                    error = ValidationError(
                        "Note content must have exactly one empty line "
                        "after the frontmatter.",
                    )
                    errors.append(error)

        if not errors:
            continue

        subtree = tree.add(note.subpath())
        for error in errors:
            subtree.add(str(error))

    if tree.children:
        CONSOLE.print(tree)
    else:
        end = f" ({need_triage} need triage)" if need_triage else ""
        CONSOLE.print(f"All notes are [green]valid[/green]{end}.")


@main.command()
@VAULT
def triage(vault):
    """
    Triage any notes waiting for review.
    """
    for note in vault.needs_triage():
        edited = note.edit()
        if edited.is_empty:
            subprocess.run(  # noqa: S603
                ["git", "rm", note.path],  # noqa: S607
                cwd=vault.path,
                check=True,
            )


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
