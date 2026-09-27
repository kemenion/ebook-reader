"""Entry point used by the frozen builds.

``ebook_reader/__main__.py`` uses a relative import, which cannot be the script
PyInstaller analyses (it would be executed as a top-level ``__main__`` with no
package around it).  This thin wrapper imports the package properly instead; the
``src`` directory is on the spec's ``pathex``.
"""

from __future__ import annotations

import sys

from ebook_reader.app.main import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
