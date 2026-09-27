#!/usr/bin/env bash
# Run the reader straight from the source tree, without installing it.
#
#   ./run.sh                          # open the file chooser
#   ./run.sh "ebooks/某本书.epub"      # open a book directly
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python="${here}/.venv/bin/python"

if [[ ! -x "${python}" ]]; then
    echo "未找到虚拟环境，请先执行：" >&2
    echo "  python3 -m venv .venv && .venv/bin/pip install PySide6-Essentials" >&2
    exit 1
fi

export PYTHONPATH="${here}/src${PYTHONPATH:+:${PYTHONPATH}}"
exec "${python}" -m ebook_reader "$@"
