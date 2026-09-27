#!/usr/bin/env bash
# Build the release artefacts into dist/.
#
#   packaging/build.sh              build both artefacts
#   packaging/build.sh --dir-only   only dist/ebook-reader-dir/
#   packaging/build.sh --onefile    only dist/ebook-reader
#
# Use the folder build for everyday testing: it starts without unpacking
# anything.  The single file is convenient to copy around, but pays a one-off
# extraction cost on every launch.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
python="${root}/.venv/bin/python"
dist="${root}/dist"
work="${root}/build"

want_dir=1
want_onefile=1
for arg in "$@"; do
    case "$arg" in
        --dir-only) want_onefile=0 ;;
        --onefile)  want_dir=0 ;;
        -h|--help)  sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "未知参数：${arg}（试试 --help）" >&2; exit 2 ;;
    esac
done

if ! "${python}" -c 'import PyInstaller' 2>/dev/null; then
    echo "PyInstaller 未安装，请先执行：" >&2
    echo "  .venv/bin/pip install --retries 10 --timeout 60 pyinstaller" >&2
    exit 1
fi

rm -rf "${work}" "${dist}"
mkdir -p "${work}" "${dist}"

build() {  # $1: 1 for a single file, 0 for a folder
    local mode="$1" label log
    if [[ "$mode" == 1 ]]; then label="单文件"; else label="文件夹"; fi
    log="${work}/pyinstaller-onefile${mode}.log"
    printf '\n=== 构建 %s (%s) ===\n' "${label}" "$([[ $mode == 1 ]] && echo dist/ebook-reader || echo dist/ebook-reader-dir/)"
    EBR_ONEFILE="${mode}" "${python}" -m PyInstaller \
        --noconfirm --clean \
        --distpath "${dist}" \
        --workpath "${work}/pyi-${mode}" \
        --log-level INFO \
        "${here}/ebook-reader.spec" > "${log}" 2>&1 || {
            echo "构建失败，日志尾部：" >&2
            tail -30 "${log}" >&2
            exit 1
        }
    # PyInstaller reports genuinely worrying things (missing binaries, modules
    # it could not analyse) without failing the build; surface them here.
    grep -E '^[0-9]+ WARNING|ERROR' "${log}" | grep -viE 'optional dependency|Hidden import .* not found' \
        | head -10 || true
    echo "完成（完整日志：${log#${root}/}）"
}

[[ "$want_dir" == 1 ]] && build 0
[[ "$want_onefile" == 1 ]] && build 1

printf '\n=== 产物 ===\n'
for item in "${dist}"/*; do
    [[ -e "$item" ]] || continue
    printf '%8s  %s\n' "$(du -sh "$item" | cut -f1)" "${item#${root}/}"
done

# Each artefact is smoke-tested offscreen, in both of the ways it can be
# started.  Passing a book exercises the loader and the render path; starting it
# with no argument exercises the file chooser, which is where Qt pulls in extra
# QML modules (Qt.labs.folderlistmodel, via QtQuick.Dialogs) that nothing else
# touches -- an earlier round shipped a bundle where that dialog silently failed
# to open, and only the no-argument run showed it.
#
# The wall-clock figure matters because it is the one the NFR-001 budget is
# spent from: the folder build pays just for Qt startup, the single file pays
# for extracting ~160 MB first.
SMOKE_SECONDS=8
ERROR_PATTERN='TypeError|ReferenceError|Unable to assign|is not installed|Failed to load|Traceback|Cannot read property|is not a type'
book="$(find "${root}/ebooks" -name '*.epub' 2>/dev/null | sort | head -1)"

smoke_test() {  # $1: executable, $2: label, remaining: arguments for the reader
    local exe="$1" label="$2"
    shift 2
    [[ -x "$exe" ]] || return 0
    local log="${work}/smoke-$(basename "$exe")-${label// /}.log"
    printf '\n=== 冒烟测试 %s ===\n' "$label"
    [[ $# -gt 0 ]] && printf '参数：%s\n' "$*" || printf '参数：无（应弹出文件对话框）\n'

    local start first code
    start="$(date +%s.%N)"
    set +e
    QT_QPA_PLATFORM=offscreen timeout "$SMOKE_SECONDS" "$exe" "$@" > "$log" 2>&1 &
    local pid=$!
    if [[ $# -gt 0 ]]; then
        first=""
        for _ in $(seq 1 $((SMOKE_SECONDS * 20))); do
            if grep -qa 'first page ready' "$log" 2>/dev/null; then
                first="$(date +%s.%N)"
                break
            fi
            sleep 0.05
        done
        if [[ -n "$first" ]]; then
            awk -v a="$start" -v b="$first" 'BEGIN{printf "启动到首屏可见：%.2f 秒\n", b-a}'
        else
            echo "未观察到首屏日志" >&2
        fi
    fi
    wait "$pid"
    code=$?
    set -e

    if [[ "$code" -ne 124 ]]; then
        echo "失败：进程在 ${SMOKE_SECONDS} 秒内退出，退出码 ${code}（期望 124 = 仍在运行）" >&2
        tail -20 "$log" >&2
        exit 1
    fi
    local errors
    errors="$(grep -caiE "$ERROR_PATTERN" "$log" || true)"
    echo "运行 ${SMOKE_SECONDS} 秒无崩溃；QML/运行时错误 ${errors} 条（期望 0）"
    if [[ "$errors" != 0 ]]; then
        grep -aiE "$ERROR_PATTERN" "$log" | head -10
        return 1
    fi
    grep -aoE 'first page ready in [0-9]+ ms' "$log" | tail -1 || true
}

smoke_test "${dist}/ebook-reader-dir/ebook-reader" "文件夹版-打开指定书" "$book"
smoke_test "${dist}/ebook-reader-dir/ebook-reader" "文件夹版-无参数选书"
smoke_test "${dist}/ebook-reader" "单文件版-打开指定书" "$book"

printf '\n=== 依赖完整性 ===\n'
if [[ -d "${dist}/ebook-reader-dir" ]]; then
    "${python}" "${here}/bundle.py" check "${dist}/ebook-reader-dir" || exit 1
fi

# The desktop entry ships with the placeholder `Exec` rewritten to the real
# path, so the artefact can be registered as-is; nothing is installed anywhere.
target="${dist}/ebook-reader"
[[ -x "${dist}/ebook-reader-dir/ebook-reader" ]] && target="${dist}/ebook-reader-dir/ebook-reader"
sed "s|^Exec=.*|Exec=${target} %f|" "${here}/ebook-reader.desktop" > "${dist}/ebook-reader.desktop"

printf '\n=== 怎么用 ===\n'
printf '文件夹版（启动快，推荐日常使用）\n'
printf '  %s [某本书.epub]\n' "${dist#${root}/}/ebook-reader-dir/ebook-reader"
printf '单文件版（便于拷贝分发，启动多花约 0.7 秒解包）\n'
printf '  %s [某本书.epub]\n' "${dist#${root}/}/ebook-reader"
printf '不带参数启动会弹出文件选择框。\n'
printf '\n桌面集成（可选，无需 root；已生成 dist/ebook-reader.desktop）：\n'
printf '  mkdir -p ~/.local/share/applications\n'
printf '  cp dist/ebook-reader.desktop ~/.local/share/applications/\n'
printf '  update-desktop-database ~/.local/share/applications\n'
printf '  xdg-mime default ebook-reader.desktop application/epub+zip\n'

