"""``python -m walk_planner <command>`` — the developer CLI (see ``walk_planner.cli``)."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
