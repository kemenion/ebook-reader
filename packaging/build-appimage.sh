#!/usr/bin/env bash
# Build a portable AppImage from the PyInstaller folder build.
#
# The folder build is the payload on purpose: an AppImage is a SquashFS image
# mounted on demand, so it starts without unpacking the ~160 MB tree that the
# single-file build extracts into /tmp on every launch (ADR-013).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
python="${root}/.venv/bin/python"
dist="${root}/dist"
work="${root}/build"
appdir="${work}/appimage/ebook-reader.AppDir"
tools="${work}/tools"
runtime="${tools}/runtime-x86_64"
output="${dist}/ebook-reader-x86_64.AppImage"

build_first=0
smoke=1

usage() {
    cat <<'EOF'
Build a portable AppImage from the PyInstaller folder build.

用法：
  packaging/build-appimage.sh [选项]

选项：
  --build          先运行 packaging/build.sh --dir-only
  --no-smoke       跳过 AppImage 的离屏冒烟测试
  --output PATH    指定输出路径（默认 dist/ebook-reader-x86_64.AppImage；
                   相对路径按仓库根目录解析）
  -h, --help       显示本帮助

默认使用已经存在的 dist/ebook-reader-dir/，不会删除或重建它。
注意：--build 会调用 build.sh，而该脚本会清空 dist/ 和 build/。
EOF
}

is_elf() {
    local path="$1"
    [[ -s "$path" ]] || return 1
    if command -v file >/dev/null 2>&1; then
        file "$path" | grep -q 'ELF 64-bit'
    else
        [[ "$(head -c 4 "$path" | od -An -tx1 | tr -d ' \n')" == "7f454c46" ]]
    fi
}

download() {
    local url="$1" dest="$2"
    if command -v curl >/dev/null 2>&1; then
        # Do not fall through to wget on failure: both would be trying the
        # same unreachable host, and the caller has mirrors to move on to.
        curl -fL --retry 3 --connect-timeout 15 --max-time 600 \
            --output "$dest" "$url" && return 0
        return 1
    fi
    if command -v wget >/dev/null 2>&1; then
        wget --output-document="$dest" --timeout=60 --tries=2 \
            "$url" && return 0
    fi
    return 1
}

download_tool() {
    local dest="$1" url
    local -a urls=(
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
        # GitHub release assets are unreachable on some networks; these
        # prefixes serve the same object byte for byte.
        "https://ghfast.top/https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
        "https://ghproxy.net/https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    )
    for url in "${urls[@]}"; do
        echo "下载 appimagetool（约 15 MB）：${url}" >&2
        rm -f "$dest"
        if download "$url" "$dest" && is_elf "$dest"; then
            chmod +x "$dest"
            echo "已保存：${dest}" >&2
            return 0
        fi
    done
    rm -f "$dest"
    return 1
}

download_runtime() {
    local dest="$1" url
    local -a urls=(
        "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"
        "https://ghfast.top/https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"
        "https://ghproxy.net/https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"
    )
    for url in "${urls[@]}"; do
        echo "下载 AppImage runtime（约 1 MB）：${url}" >&2
        rm -f "$dest"
        if download "$url" "$dest" && is_elf "$dest"; then
            chmod +x "$dest"
            echo "已保存：${dest}" >&2
            return 0
        fi
    done
    rm -f "$dest"
    return 1
}

# A folder build older than the sources ships the previous version of the app -
# the trap behind "I rebuilt the AppImage and nothing changed": this script
# reuses dist/ebook-reader-dir/ unless --build is given, and PyInstaller is the
# only thing that copies the QML and the Python.
warn_when_stale() {
    local payload="$1" newest
    [[ -x "${payload}/ebook-reader" ]] || return 0
    newest="$(
        find "${root}/src" "${root}/packaging" -type f \
            -newer "${payload}/ebook-reader" -print -quit 2>/dev/null
    )"
    [[ -n "$newest" ]] || return 0
    {
        echo "警告：${payload} 比源码旧（最新的改动是 ${newest}），"
        echo "      所以这个 AppImage 里装的仍是上一次的构建。重新构建请用："
        echo "          packaging/build-appimage.sh --build"
    } >&2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build)    build_first=1 ; shift ;;
        --no-smoke) smoke=0 ; shift ;;
        --output)
            [[ $# -ge 2 ]] || { echo "--output 需要一个路径" >&2; exit 2; }
            output="$2"
            shift 2
            ;;
        -h|--help)  usage; exit 0 ;;
        *)          echo "未知参数：$1（试试 --help）" >&2; exit 2 ;;
    esac
done

[[ "$output" == /* ]] || output="${root}/${output}"

if [[ "$build_first" == 1 ]]; then
    echo "=== 先运行 packaging/build.sh --dir-only ==="
    echo "注意：build.sh 会清空 dist/ 和 build/，因此 appimagetool 缓存也会被清掉。" >&2
    "${here}/build.sh" --dir-only
fi

payload="${dist}/ebook-reader-dir"
if [[ ! -x "${payload}/ebook-reader" || ! -d "${payload}/_internal" ]]; then
    echo "找不到文件夹版产物：${payload}" >&2
    echo "请先执行：" >&2
    echo "  packaging/build.sh --dir-only" >&2
    echo "或者使用：packaging/build-appimage.sh --build" >&2
    exit 1
fi

if [[ "$build_first" == 0 ]]; then
    warn_when_stale "$payload"
fi

if [[ ! -x "$python" ]]; then
    echo "找不到虚拟环境 Python：${python}" >&2
    echo "请先按 README 安装依赖。" >&2
    exit 1
fi

case "$(uname -m)" in
    x86_64|amd64) ;;
    *)
        echo "当前只支持 x86_64：PyInstaller 产物和 appimagetool 都是 x86_64。" >&2
        exit 1
        ;;
esac

mkdir -p "$tools"

if [[ -n "${APPIMAGETOOL:-}" ]]; then
    tool="$APPIMAGETOOL"
    if [[ ! -x "$tool" ]]; then
        echo "APPIMAGETOOL 指向的文件不可执行：${tool}" >&2
        exit 1
    fi
else
    tool="${tools}/appimagetool-x86_64.AppImage"
    if [[ ! -x "$tool" ]] || ! is_elf "$tool"; then
        rm -f "$tool"
        if ! download_tool "$tool"; then
            echo "appimagetool 下载失败。" >&2
            echo "可手动下载 appimagetool-x86_64.AppImage，然后设置 APPIMAGETOOL 指向它。" >&2
            exit 1
        fi
    fi
fi

# appimagetool downloads the type2 runtime from GitHub on its own, which fails
# on the same networks that block the tool itself.  Fetch it here and hand it
# over explicitly with --runtime-file.
if [[ -n "${APPIMAGE_RUNTIME:-}" ]]; then
    runtime="$APPIMAGE_RUNTIME"
    if [[ ! -x "$runtime" ]]; then
        echo "APPIMAGE_RUNTIME 指向的文件不可执行：${runtime}" >&2
        exit 1
    fi
elif [[ ! -x "$runtime" ]] || ! is_elf "$runtime"; then
    rm -f "$runtime"
    if ! download_runtime "$runtime"; then
        echo "AppImage runtime 下载失败。" >&2
        echo "可手动下载 runtime-x86_64，然后设置 APPIMAGE_RUNTIME 指向它。" >&2
        exit 1
    fi
fi

printf '\n=== 组装 AppDir ===\n'
rm -rf "$appdir"
mkdir -p \
    "${appdir}/usr/bin" \
    "${appdir}/usr/share/applications" \
    "${appdir}/usr/share/icons/hicolor/scalable/apps" \
    "${appdir}/usr/share/icons/hicolor/256x256/apps"

cp -a "${payload}/." "${appdir}/usr/bin/"

install -m 755 "${here}/appimage/AppRun" "${appdir}/AppRun"
install -m 644 "${here}/appimage/ebook-reader.desktop" "${appdir}/ebook-reader.desktop"
install -m 644 "${here}/appimage/ebook-reader.desktop" \
    "${appdir}/usr/share/applications/ebook-reader.desktop"
install -m 644 "${here}/appimage/ebook-reader.svg" "${appdir}/ebook-reader.svg"
install -m 644 "${here}/appimage/ebook-reader.svg" \
    "${appdir}/usr/share/icons/hicolor/scalable/apps/ebook-reader.svg"
ln -sf ebook-reader.svg "${appdir}/.DirIcon"

# Most desktops accept SVG, but the hicolor spec wants a PNG at a concrete
# size, and some icon caches only index that.  QtSvg is already in the venv.
if "$python" "${here}/appimage/render_icon.py" \
    "${here}/appimage/ebook-reader.svg" "${appdir}/ebook-reader.png"; then
    install -m 644 "${appdir}/ebook-reader.png" \
        "${appdir}/usr/share/icons/hicolor/256x256/apps/ebook-reader.png"
else
    echo "警告：PNG 图标生成失败，AppImage 将只带 SVG 图标。" >&2
fi

printf 'AppDir：%s（%s）\n' "${appdir#${root}/}" "$(du -sh "$appdir" | cut -f1)"

printf '\n=== 生成 AppImage ===\n'
mkdir -p "$(dirname "$output")"
rm -f "$output"

extra=()
help_text="$(APPIMAGE_EXTRACT_AND_RUN=1 "$tool" --help 2>&1 || true)"
if grep -q -- '--no-appstream' <<<"$help_text"; then
    # There is no AppStream metadata in this repository; without the flag
    # appimagetool only warns, but the warning looks like a build failure.
    extra+=(--no-appstream)
fi
if grep -q -- '--comp' <<<"$help_text"; then
    # zstd is the default in current appimagetool builds; state it so an older
    # tool cannot silently fall back to a much larger gzip image.
    extra+=(--comp zstd)
fi

if ! ARCH=x86_64 "$tool" "${extra[@]}" --runtime-file "$runtime" \
    "$appdir" "$output"; then
    echo "appimagetool 直接运行失败，改用 APPIMAGE_EXTRACT_AND_RUN=1 重试。" >&2
    rm -f "$output"
    APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 \
        "$tool" "${extra[@]}" --runtime-file "$runtime" "$appdir" "$output"
fi

if [[ ! -s "$output" ]]; then
    echo "appimagetool 没有生成文件：${output}" >&2
    exit 1
fi
chmod +x "$output"
printf '产物：%s（%s）\n' "${output#${root}/}" "$(du -h "$output" | cut -f1)"

printf '\n=== AppImage runtime ===\n'
if ! "$output" --appimage-version; then
    echo "直接读取 runtime 版本失败，尝试 APPIMAGE_EXTRACT_AND_RUN=1。" >&2
    APPIMAGE_EXTRACT_AND_RUN=1 "$output" --appimage-version
fi

# Read one file back out of the image.  This exercises the runtime's SquashFS
# path without mounting it through FUSE, which is the fallback path on hosts
# without libfuse2.
verify_dir="${work}/appimage/verify"
rm -rf "$verify_dir"
mkdir -p "$verify_dir"
(
    cd "$verify_dir"
    "$output" --appimage-extract ebook-reader.desktop >/dev/null
)
if [[ ! -f "${verify_dir}/squashfs-root/ebook-reader.desktop" ]]; then
    echo "无法从 AppImage 中解出 ebook-reader.desktop" >&2
    exit 1
fi
echo "镜像内容可读取：ebook-reader.desktop"

if [[ "$smoke" == 1 ]]; then
    smoke_seconds=8
    error_pattern='TypeError|ReferenceError|Unable to assign|is not installed|Failed to load|Traceback|Cannot read property|is not a type'
    book="$(find "${root}/ebooks" -name '*.epub' 2>/dev/null | sort | head -1 || true)"
    extract_run=0
    last_log=""

    smoke_test() {  # $1: label, remaining: arguments for the reader
        local label="$1"
        shift
        local log="${work}/appimage/smoke-${label// /}.log"
        last_log="$log"
        mkdir -p "$(dirname "$log")"
        printf '\n=== 冒烟测试 %s ===\n' "$label"
        if [[ $# -gt 0 ]]; then
            printf '参数：%s\n' "$*"
        else
            printf '参数：无（应弹出文件对话框）\n'
        fi

        local start first code errors
        start="$(date +%s.%N)"
        local -a envs=(QT_QPA_PLATFORM=offscreen)
        [[ "$extract_run" == 1 ]] && envs+=(APPIMAGE_EXTRACT_AND_RUN=1)
        set +e
        env "${envs[@]}" timeout "$smoke_seconds" "$output" "$@" > "$log" 2>&1 &
        local pid=$!
        if [[ $# -gt 0 ]]; then
            first=""
            for _ in $(seq 1 $((smoke_seconds * 20))); do
                if grep -qa 'first window ready' "$log" 2>/dev/null; then
                    first="$(date +%s.%N)"
                    break
                fi
                sleep 0.05
            done
            if [[ -n "$first" ]]; then
                awk -v a="$start" -v b="$first" \
                    'BEGIN{printf "启动到首屏可见：%.2f 秒\n", b-a}'
            else
                echo "未观察到首屏日志" >&2
            fi
        fi
        wait "$pid"
        code=$?
        set -e

        if [[ "$code" -ne 124 ]]; then
            echo "失败：进程在 ${smoke_seconds} 秒内退出，退出码 ${code}（期望 124 = 仍在运行）" >&2
            tail -20 "$log" >&2
            return 1
        fi
        errors="$(grep -caiE "$error_pattern" "$log" || true)"
        echo "运行 ${smoke_seconds} 秒无崩溃；QML/运行时错误 ${errors} 条（期望 0）"
        if [[ "$errors" != 0 ]]; then
            grep -aiE "$error_pattern" "$log" | head -10
            return 1
        fi
        grep -aoE 'first window ready in [0-9]+ ms' "$log" | tail -1 || true
        return 0
    }

    run_smoke() {  # retry once with extract-and-run when FUSE is unavailable
        local label="$1"
        shift
        if smoke_test "$label" "$@"; then
            return 0
        fi
        if [[ "$extract_run" == 0 ]] \
            && grep -qiE 'fuse|libfuse|failed to mount|dlopen' "$last_log" 2>/dev/null; then
            echo "检测到 FUSE 挂载失败，改用 APPIMAGE_EXTRACT_AND_RUN=1 重试。" >&2
            extract_run=1
            smoke_test "$label" "$@"
        else
            return 1
        fi
    }

    if [[ -n "$book" ]]; then
        run_smoke "打开指定书" "$book" || exit 1
    else
        echo "ebooks/ 下没有 epub，跳过带书冒烟测试。" >&2
    fi
    run_smoke "无参数选书" || exit 1

    # FUSE is not guaranteed on every host (notably containers and older
    # distributions without libfuse2).  Exercise the documented fallback once
    # even when the normal path worked, so a regression there cannot hide.
    if [[ "$extract_run" == 0 && -n "$book" ]]; then
        extract_run=1
        run_smoke "解包运行-打开指定书" "$book" || exit 1
    fi
fi

printf '\n=== 依赖完整性 ===\n'
"$python" "${here}/bundle.py" check "${appdir}/usr/bin" || exit 1

printf '\n=== 产物 ===\n'
printf '%8s  %s\n' "$(du -h "$output" | cut -f1)" "${output#${root}/}"

printf '\n=== 怎么用 ===\n'
printf '  %s [某本书.epub]\n' "${output#${root}/}"
printf '无 FUSE 的机器：\n'
printf '  APPIMAGE_EXTRACT_AND_RUN=1 %s [某本书.epub]\n' "${output#${root}/}"
printf '双击运行前确认可执行位：chmod +x %s\n' "${output#${root}/}"

