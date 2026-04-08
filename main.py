#!/usr/bin/env python3
"""Back-compat launcher when running ``python main.py`` from a git checkout."""
from mailspray.cli import run_cli

if __name__ == "__main__":
    run_cli()
