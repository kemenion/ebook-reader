"""Bundle trimming and verification for the frozen builds (architecture.md, P7).

Qt ships a complete desktop stack, and a reader uses a small fraction of it.
Two mechanisms keep the artefact honest:

* :func:`trim` drops the plugin families, QML modules and control styles that
  this application provably never reaches.  Every entry in the drop tables
  carries the reason it is safe to drop, so that removing a line later is a
  deliberate act rather than an accident.
* :func:`prune_orphans` then removes the shared libraries that only the dropped
  items were pulling in.  This is what actually clears the GTK runtime that a
  single unused theme plugin drags along, and it keeps working as the Qt release
  changes instead of a hand-maintained list going stale.

:func:`check` verifies a built bundle from the outside: it re-resolves every
library reference with ``ldd`` and reports anything unresolved, which is the
failure mode over-trimming would produce.

Run ``python packaging/bundle.py check dist/ebook-reader-dir`` after a build.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

# --------------------------------------------------------------------- drops

# Plugin families loaded through Qt's plugin loader, i.e. by dlopen, so nothing
# in the bundle references them and only an explicit table can remove them.
PLUGIN_FAMILIES = frozenset(
    {
        # QML debugger backend; only reachable with -qmljsdebugger.
        "qmltooling",
        # TLS backends for QtNetwork. The reader never opens a network socket.
        "tls",
        # QNetworkInformation backends, same reason.
        "networkinformation",
        # Compositor-side Wayland backends; this is a client.
        "wayland-graphics-integration-server",
    }
)

# Individual plugins, dropped per file rather than per family. `platformthemes`
# holds two: the XDG portal one gives us native file dialogs and stays, while
# the GTK one only supplies widget-theme colours we override ourselves.
PLUGIN_FILES = frozenset({"platformthemes/libqgtk3.so"})

# QML modules, addressed relative to the `qml` directory. Qt Quick Controls
# picks its style at run time, so unused styles cannot be detected by import
# scanning; the application pins the style to Fusion in `app/main.py`, which is
# what makes dropping the others safe.
QML_MODULES = frozenset(
    {
        "QtQuick/Controls/FluentWinUI3",  # 8.3 MB of images, Windows 11 only
        "QtQuick/Controls/Material",
        "QtQuick/Controls/Imagine",
        "QtQuick/Controls/Universal",
        "QtQuick/Controls/designer",  # Qt Design Studio metadata
        "QtQuick/Dialogs/quickimpl/qml/+Material",
        "QtQuick/Dialogs/quickimpl/qml/+Imagine",
        "QtQuick/Dialogs/quickimpl/qml/+Universal",
        "QtQuick/VirtualKeyboard",
        # Qt/labs is trimmed per sub-module rather than wholesale: FileDialog's
        # implementation imports Qt.labs.folderlistmodel, which is exactly the
        # kind of indirect dependency an import scan of our own QML would miss.
        # `check_qml_imports` now guards this automatically.
        "Qt/labs/assetdownloader",  # Qt Design Studio only, 2.2 MB
        "Qt/labs/StyleKit",  # only imported by QtQuick/VirtualKeyboard
        "Qt/labs/animation",
        "Qt/labs/platform",
        "Qt/labs/qmlmodels",
        "Qt/labs/settings",
        "Qt/labs/sharedimage",
        "Qt/labs/synchronizer",
        "Qt/labs/wavefrontmesh",
        "QtTest",
        "QtNetwork",
        "QtWayland",
    }
)

# Destination directories whose shared libraries may be pruned. Deliberately
# narrow: the plugins, QML modules, Python extension modules and the stdlib's
# `lib-dynload` are all loaded by path or by dlopen, so nothing references them
# and a generic reachability sweep would delete them wholesale.  "." is the top
# level of the bundle.
PRUNABLE_DIRS = (".", "PySide6/Qt/lib")

# Top-level libraries the bootloader itself needs, which therefore have no
# referencing entry inside the analysis.
PRUNE_ROOTS_KEEP = ("libpython",)


def _dest(entry: object) -> str:
    """Destination path of a TOC entry, as a forward-slash relative path.

    PyInstaller 6 TOC entries are ``(destination path, source path, type)`` --
    destination first, which is the opposite order to the ``(source,
    destination directory)`` pairs that hooks and this spec's own tables use.
    """
    return str(entry[0]).replace("\\", "/").lstrip("/")


def _src(entry: object) -> str:
    """Source path of a TOC entry."""
    return str(entry[1])


def _prunable(entry: object) -> bool:
    parent = str(PurePosixPath(_dest(entry)).parent)
    return parent in PRUNABLE_DIRS


def _keep(entry: object) -> bool:
    text = _dest(entry)
    if "/plugins/" in text:
        rest = text.split("/plugins/", 1)[1]
        family, _, leaf = rest.partition("/")
        if family in PLUGIN_FAMILIES:
            return False
        if f"{family}/{leaf}" in PLUGIN_FILES:
            return False
    if "PySide6/Qt/qml/" in text:
        module = text.split("PySide6/Qt/qml/", 1)[1]
        if any(module == m or module.startswith(m + "/") for m in QML_MODULES):
            return False
    return True


def _is_elf(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def _needed(name: str, within: dict[str, list[str]]) -> list[str]:
    """Bundled libraries that the bundle member ``name`` links against."""
    from PyInstaller.depend import bindepend

    found = []
    for source in within.get(name, ()):
        try:
            for referenced, _ in bindepend.get_imports(source):
                if referenced in within:
                    found.append(referenced)
        except Exception:  # noqa: BLE001 - anything unparsable is kept anyway
            continue
    return found


def prune_orphans(binaries: list) -> tuple[list, list]:
    """Split ``binaries`` into (kept, dropped) by reachability.

    Anything outside :data:`PRUNABLE_DIRS` is a root, as is ``libpython``; the
    drop candidates are then whatever cannot be reached from those roots by
    following ``DT_NEEDED`` edges.  A library nothing links against can only
    have been pulled in for a plugin that has already been removed.
    """
    within: dict[str, list[str]] = {}
    for entry in binaries:
        within.setdefault(PurePosixPath(_src(entry)).name, []).append(_src(entry))

    def leaf(entry: object) -> str:
        return PurePosixPath(_src(entry)).name

    seen: set[str] = set()
    pending = [
        leaf(entry)
        for entry in binaries
        if _is_elf(_src(entry))
        and (not _prunable(entry) or leaf(entry).startswith(PRUNE_ROOTS_KEEP))
    ]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        pending.extend(_needed(name, within))

    kept, dropped = [], []
    for entry in binaries:
        if _prunable(entry) and _is_elf(_src(entry)) and leaf(entry) not in seen:
            dropped.append(entry)
        else:
            kept.append(entry)
    return kept, dropped


def trim(binaries: list, datas: list) -> tuple[list, list]:
    """Apply the drop tables, then prune whatever they leave unreferenced."""
    kept_datas = [entry for entry in datas if _keep(entry)]
    filtered = [entry for entry in binaries if _keep(entry)]
    kept_binaries, dropped = prune_orphans(filtered)
    print(
        f"[bundle] 按表剔除：数据 {len(datas) - len(kept_datas)} 项、"
        f"二进制 {len(binaries) - len(filtered)} 项；"
        f"孤立的共享库再剪除 {len(dropped)} 个",
        flush=True,
    )
    return kept_binaries, kept_datas


# --------------------------------------------------------------- verification

# QML resolves modules at run time and reports a missing one as a console
# warning, not an error: the file dialog then fails to open while the rest of
# the application carries on working.  It has to be checked explicitly.
_QML_IMPORT = re.compile(r"^\s*import\s+([A-Za-z_][\w.]*)", re.MULTILINE)
_QMLDIR_MODULE = re.compile(r"^\s*module\s+(\S+)", re.MULTILINE)
_QMLDIR_DEPENDS = re.compile(r"^\s*depends\s+(\S+)", re.MULTILINE)

# Registered from Python via `qmlRegisterType`, so no `qmldir` declares it.
APP_MODULES = frozenset({"EbookReader"})


def qml_modules(qml_root: Path) -> set[str]:
    """Module names declared by the ``qmldir`` files under ``qml_root``.

    The declared name is used rather than the directory path because the two do
    not always agree.
    """
    names = set()
    for qmldir in qml_root.rglob("qmldir"):
        match = _QMLDIR_MODULE.search(qmldir.read_text(encoding="utf-8", errors="replace"))
        if match:
            names.add(match.group(1))
    return names


def check_qml_imports(bundle: Path) -> list[tuple[str, str]]:
    """Every QML import that cannot be resolved inside the bundle.

    Quoted imports name a relative directory rather than a module and are left
    to the engine; the regular expression cannot match them because of the
    leading quote.
    """
    qml_root = bundle / "_internal" / "PySide6" / "Qt" / "qml"
    if not qml_root.is_dir():
        print(f"找不到 QML 目录，跳过检查：{qml_root}", file=sys.stderr)
        return []

    available = qml_modules(qml_root)
    # Provided by the engine itself or by plugins, not by a qmldir on disk.
    # `QML` is the language's own pseudo-module, used by the `QtQuick.tooling`
    # type descriptions.
    available.update({"QML", "Qt", "QtQml", "QtQuick"})

    unresolved: dict[str, set[str]] = {}
    for path in [*bundle.rglob("*.qml"), *qml_root.rglob("qmldir")]:
        text = path.read_text(encoding="utf-8", errors="replace")
        names = {m.group(1) for m in _QML_IMPORT.finditer(text)}
        names |= {m.group(1) for m in _QMLDIR_DEPENDS.finditer(text)}
        for name in names:
            if name in APP_MODULES or name in available:
                continue
            unresolved.setdefault(name, set()).add(str(path.relative_to(bundle)))

    return sorted(
        (name, next(iter(paths))) for name, paths in unresolved.items()
    )


def check(bundle: Path) -> int:
    """Report unresolved library references and the bundle's size profile."""
    bundle = bundle.resolve()
    if bundle.is_dir():
        executable = bundle / "ebook-reader"
        if not executable.exists():
            candidates = sorted(p for p in bundle.iterdir() if p.is_file())
            if not candidates:
                print(f"找不到可执行文件：{bundle}", file=sys.stderr)
                return 2
            executable = candidates[0]
    else:
        executable = bundle

    root = bundle if bundle.is_dir() else bundle.parent
    elfs = [p for p in root.rglob("*") if p.is_file() and not p.is_symlink() and _is_elf(str(p))]

    missing: list[tuple[Path, str]] = []
    for path in elfs:
        result = subprocess.run(["ldd", str(path)], capture_output=True, text=True, check=False)
        for line in result.stdout.splitlines():
            if "not found" in line:
                missing.append((path, line.strip().split(" =>")[0]))

    biggest = sorted(
        (
            (p.stat().st_size, p)
            for p in root.rglob("*")
            # Symlinks are excluded on purpose: the top level of the bundle is
            # full of relative links into PySide6/Qt/lib, and following them
            # would report the same 30 MB library half a dozen times.
            if p.is_file() and not p.is_symlink()
        ),
        reverse=True,
    )[:10]

    print(f"检查对象   : {executable}")
    print(f"ELF 文件数 : {len(elfs)}")
    print(f"未解析的库 : {len(missing)}")
    print(f"符号链接   : {sum(1 for p in root.rglob('*') if p.is_symlink())}")
    print(
        f"实体占用   : "
        f"{sum(p.stat().st_size for p in root.rglob('*') if p.is_file() and not p.is_symlink()) / 1024 / 1024:.0f} MB"
    )
    for path, name in missing[:20]:
        print(f"   缺失 {name}  <- {path.relative_to(root)}")

    unresolved_qml = check_qml_imports(bundle if bundle.is_dir() else bundle.parent)
    print(f"未解析的 QML import : {len(unresolved_qml)}")
    for name, where in unresolved_qml[:20]:
        print(f"   缺失 {name}  <- {where}")

    print("体积最大的 10 项：")
    for size, path in biggest:
        print(f"  {size / 1024 / 1024:8.1f} MB  {path.relative_to(root)}")
    return 1 if missing or unresolved_qml else 0


def _main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "check":
        print(__doc__)
        return 2
    return check(Path(argv[1]))


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
