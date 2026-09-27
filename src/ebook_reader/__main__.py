"""``python -m ebook_reader [book.epub]`` entry point."""

from __future__ import annotations

import sys

from .app.main import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
