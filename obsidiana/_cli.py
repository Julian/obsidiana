from collections import Counter
from pathlib import Path
import json

from jsonschema.exceptions import relevance
from jsonschema.validators import validator_for
from rich.console import Console
from rich.table import Table
from rich.tree import Tree
import rich_click as click

from obsidiana.vault import Vault

STDOUT = Console()


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


VAULT = click.option(
    "--vault",
    default=lambda: Vault(path=Path.cwd()),
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
def validate_frontmatter(vault):
    """
    Validate the frontmatter of all notes in the vault against a JSON Schema.
    """
    schema = json.loads(vault.child("schema.json").read_text())
    Validator = validator_for(schema)
    Validator.check_schema(schema)
    validator = Validator(schema, format_checker=Validator.FORMAT_CHECKER)

    tree = Tree("[red]Invalid Notes[/red]")

    for note in vault.notes():
        if note.awaiting_triage():
            continue

        errors = sorted(validator.iter_errors(note.frontmatter), key=relevance)
        if not errors:
            continue

        subtree = tree.add(note.subpath())
        for error in errors:
            subtree.add(str(error))

    if tree.children:
        STDOUT.print(tree)
    else:
        STDOUT.print("All notes are [green]valid[/green].")


@main.command()
@VAULT
def todo(vault):
    """
    Show all notes with TODO tags.
    """
    for note in vault.notes():
        # TODO: Include notes with TODO subsections.
        if "todo" in note.tags or "todo/now" in note.tags:
            STDOUT.print(note.subpath())


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

    STDOUT.print(table)


@main.command()
@VAULT
def anki(vault):
    """
    Show all notes labelled for Anki deck inclusion.
    """
    for note in vault.notes():
        if "learn/anki" in note.tags:
            STDOUT.print(note.subpath())
