import click


@click.command(context_settings=dict(help_option_names=["--help", "-h"]))
@click.version_option(prog_name="ob")
def main():
    pass
