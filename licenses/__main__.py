"""`python3 -m licenses` -- shorthand for the CLI's `list` (or whatever
subcommand follows)."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
