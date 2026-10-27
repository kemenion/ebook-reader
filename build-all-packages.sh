#!/usr/bin/env bash
# 一条命令重建全部发布产物（文件夹版 + 单文件版 + AppImage）。
#
#   ./build-all-packages.sh                  三个产物全做（首次会下载约 16 MB 构建工具）
#   ./build-all-packages.sh --skip-onefile   跳过单文件版（最慢的一步，约 1.5 分钟）
#   ./build-all-packages.sh --skip-appimage  只做 PyInstaller 的两个产物
#   ./build-all-packages.sh --offline        只用缓存的构建工具，绝不联网
#   ./build-all-packages.sh --no-smoke       跳过离屏冒烟测试（快 20 秒左右）
#
# 为什么要这个包装脚本：三个产物由两个脚本产出，而且必须按这个顺序跑。
# build.sh 一开头就清空 dist/ 与 build/，build-appimage.sh 只是压缩「已经存在的」
# dist/ebook-reader-dir/ —— 所以 `build-appimage.sh --build` 虽然一条命令出 AppImage，
# 却会顺手删掉刚做好的单文件版。只有先 build.sh、再 build-appimage.sh，三个产物才
# 能同时留在 dist/ 里。
#
# 目录结构（前三项每次重建都会清空，缓存不会）：
#   build/logs/                 PyInstaller 与冒烟测试的日志
#   build/pyi-{dir,onefile}/    PyInstaller 的工作目录
#   build/appimage/             组装中的 AppDir 与镜像校验目录
#   dist/                       产物
#   ${XDG_CACHE_HOME:-~/.cache}/ebook-reader-build/tools/
#                               appimagetool 与 AppImage runtime：只下载一次，
#                               之后每次重建都复用（EBR_CACHE_DIR 可改位置）
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python="${here}/.venv/bin/python"
cache="${EBR_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/ebook-reader-build}"

build_onefile=1
build_appimage=1
offline=0
smoke=1

usage() {
    sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-onefile)  build_onefile=0 ; shift ;;
        --skip-appimage) build_appimage=0 ; shift ;;
        --offline)       offline=1 ; shift ;;
        --no-smoke)      smoke=0 ; shift ;;
        -h|--help)       usage; exit 0 ;;
        *) echo "未知参数：$1（试试 --help）" >&2; exit 2 ;;
    esac
done

# Fail before build.sh wipes dist/: a missing .venv here would otherwise leave
# the tree with neither old artefacts nor new ones.
if [[ ! -x "$python" ]]; then
    echo "找不到虚拟环境 Python：${python}" >&2
    echo "请先按 README 建好 .venv 并安装 PySide6-Essentials。" >&2
    exit 1
fi
if ! "$python" -c 'import PyInstaller' 2>/dev/null; then
    echo "PyInstaller 未安装，请先执行：" >&2
    echo "  .venv/bin/pip install --retries 10 --timeout 60 pyinstaller==6.22.3" >&2
    exit 1
fi

stage=0
total=0
step() {  # $1 title, rest: command and arguments
    local title="$1"
    shift
    stage=$((stage + 1))
    printf '\n================================================================\n'
    printf '== 第 %d 步：%s\n' "$stage" "$title"
    printf '================================================================\n'
    local started=$SECONDS spent
    "$@"
    spent=$((SECONDS - started))
    total=$((total + spent))
    printf '== 完成：%s（%d 分 %02d 秒）\n' "$title" $((spent / 60)) $((spent % 60))
}

pyi_args=()
if [[ "$build_onefile" == 0 ]]; then
    pyi_args+=(--dir-only)
fi
if [[ "$smoke" == 0 ]]; then
    pyi_args+=(--no-smoke)
fi
if [[ "$build_onefile" == 1 ]]; then
    step "PyInstaller 打包（文件夹版 + 单文件版）" \
        "${here}/packaging/build.sh" "${pyi_args[@]}"
else
    step "PyInstaller 打包（文件夹版）" "${here}/packaging/build.sh" "${pyi_args[@]}"
fi

if [[ "$build_appimage" == 1 ]]; then
    if [[ ! -x "${here}/dist/ebook-reader-dir/ebook-reader" ]]; then
        echo "找不到 dist/ebook-reader-dir/，无法生成 AppImage。" >&2
        exit 1
    fi
    app_args=()
    if [[ "$offline" == 1 ]]; then
        app_args+=(--offline)
    fi
    if [[ "$smoke" == 0 ]]; then
        app_args+=(--no-smoke)
    fi
    # No --build on purpose: step 1 already produced the payload, and --build
    # would wipe dist/ again (taking the single file with it).
    step "生成 AppImage（压缩第 1 步的文件夹版）" \
        "${here}/packaging/build-appimage.sh" "${app_args[@]}"
else
    echo "（--skip-appimage：不生成 AppImage）"
fi

printf '\n=== 产物（%s） ===\n' "${here}/dist"
for item in "${here}/dist"/*; do
    [[ -e "$item" ]] || continue
    printf '%10s  %s\n' "$(du -sh "$item" | cut -f1)" "${item#${here}/}"
done

printf '\n=== 构建工具缓存（重建不会清空，也不会重新下载） ===\n'
if [[ -d "${cache}/tools" ]]; then
    for item in "${cache}/tools"/*; do
        [[ -e "$item" ]] || continue
        printf '%10s  %s\n' "$(du -sh "$item" | cut -f1)" "$item"
    done
    printf '%10s  %s\n' "$(du -sh "$cache" | cut -f1)" "$cache"
else
    echo "（尚未下载：${cache}/tools）"
fi

printf '\n总耗时：%d 分 %02d 秒\n' $((total / 60)) $((total % 60))
