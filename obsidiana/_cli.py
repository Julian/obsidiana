from pathlib import Path
import json

from jsonschema.exceptions import relevance
from jsonschema.validators import validator_for
from rich.tree import Tree
import rich
import rich_click as click

from obsidiana.vault import Vault


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

        frontmatter = note.frontmatter()
        errors = sorted(validator.iter_errors(frontmatter), key=relevance)
        if not errors:
            continue

        subtree = tree.add(note.subpath())
        for error in errors:
            subtree.add(str(error))

        if tree.children:
            rich.print(tree)
        else:
            rich.print("All notes are [green]valid[/green].")
