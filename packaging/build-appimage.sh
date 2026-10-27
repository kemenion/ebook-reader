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
logs="${work}/logs"
appdir="${work}/appimage/ebook-reader.AppDir"
output="${dist}/ebook-reader-x86_64.AppImage"

#: The build tools live *outside* build/ on purpose.  build.sh starts with
#: `rm -rf build dist`, so a cache under build/ has to be downloaded again after
#: every rebuild of the payload - 16 MB over a link where GitHub releases time
#: out.  This one is per user, survives any number of rebuilds, and is shared by
#: every checkout on the machine.  EBR_CACHE_DIR overrides it wholesale.
cache="${EBR_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/ebook-reader-build}"
tools="${cache}/tools"
#: Downloads land here first and are moved into tools/ only after they pass
#: verification, so an interrupted run can never leave a file that looks cached.
partial="${cache}/partial"
#: Where the previous version of this script kept the tools.  A copy that still
#: verifies is moved into the cache instead of being downloaded again.
legacy_tools="${work}/tools"
runtime="${tools}/runtime-x86_64"

#: Sizes that separate a whole download from an interrupted one: appimagetool is
#: itself an AppImage (~14 MB), the type2 runtime about 0.9 MB.  A truncated
#: file still starts with a valid ELF header - one real incident left a 2 MB
#: stub here that `file` called a legitimate ELF, and the next build failed
#: inside a half-downloaded tool with a message about squashfs.
APPIMAGETOOL_MIN_BYTES=$((8 * 1024 * 1024))
RUNTIME_MIN_BYTES=$((512 * 1024))
#: Anything after the runtime stub is the payload; the smallest real one here is
#: appimagetool's own ~13 MB SquashFS.
MIN_PAYLOAD_BYTES=$((1024 * 1024))
#: Abandon a URL that crawls instead of waiting for --max-time: the direct GitHub
#: download connects but moves at ~10 KB/s on this network, which once burned ten
#: minutes of a build before the first mirror was even tried.
MIN_SPEED=$((20 * 1024))
SPEED_WINDOW=30

#: GitHub release assets are unreachable on some networks; the mirrors serve the
#: same object byte for byte.  GitHub itself goes first (it is fast where it
#: works), and only the first URL is tried without retries - a black hole costs
#: three connect timeouts otherwise, per URL.
APPIMAGETOOL_URLS=(
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    "https://ghfast.top/https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    "https://ghproxy.net/https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
)
RUNTIME_URLS=(
    "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"
    "https://ghfast.top/https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"
    "https://ghproxy.net/https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"
)

build_first=0
smoke=1
offline=0

usage() {
    cat <<'EOF'
Build a portable AppImage from the PyInstaller folder build.

用法：
  packaging/build-appimage.sh [选项]

选项：
  --build          先运行 packaging/build.sh --dir-only
  --no-smoke       跳过 AppImage 的离屏冒烟测试
  --offline        只用缓存里的 appimagetool / runtime，绝不联网
  --output PATH    指定输出路径（默认 dist/ebook-reader-x86_64.AppImage；
                   相对路径按仓库根目录解析）
  -h, --help       显示本帮助

默认使用已经存在的 dist/ebook-reader-dir/，不会删除或重建它。
注意：--build 会调用 build.sh，而该脚本会清空 dist/ 和 build/。

构建工具（appimagetool 与 AppImage runtime）缓存在 ~/.cache/ebook-reader-build/tools/
（XDG_CACHE_HOME 与 EBR_CACHE_DIR 可改位置），不在 build/ 里，
因此重建上一步不会让它们重下；首次会下载约 16 MB。
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

#: One URL into *dest*.  Retries are left to the mirrors: the first URL is
#: GitHub itself, which here either connects and crawls or does not connect at
#: all, and retrying a black hole costs three connect timeouts before the next
#: mirror is even tried.
fetch() {  # $1 url, $2 dest, $3 retries
    local url="$1" dest="$2" retries="$3"
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry "$retries" --retry-delay 1 --connect-timeout 10 \
            --speed-limit "$MIN_SPEED" --speed-time "$SPEED_WINDOW" \
            --max-time 900 --output "$dest" "$url"
        return
    fi
    if command -v wget >/dev/null 2>&1; then
        wget --output-document="$dest" --timeout=60 --tries="$((retries + 1))" "$url"
        return
    fi
    echo "需要 curl 或 wget 才能下载构建工具。" >&2
    return 1
}

#: The runtime stub answers this before it mounts anything, so it costs one exec
#: (~1 ms) and needs neither FUSE nor the payload - which is exactly why it is
#: the check that catches a truncated download: the stub at the front of an
#: interrupted appimagetool is intact and replies, its SquashFS is what is gone.
has_runtime_version() {
    local path="$1" out
    out="$("$path" --appimage-version 2>&1)" && grep -qi 'runtime version' <<<"$out" \
        && return 0
    out="$(APPIMAGE_EXTRACT_AND_RUN=1 "$path" --appimage-version 2>&1)" \
        && grep -qi 'runtime version' <<<"$out"
}

#: $3 asks for a payload after the stub: appimagetool carries its own ~13 MB
#: SquashFS, the bare runtime carries none.  That difference is also what catches
#: pointing APPIMAGETOOL at runtime-x86_64 by mistake.
is_usable() {  # $1 path, $2 minimum bytes, $3 1 = payload required
    local path="$1" min="$2" need_payload="$3" size offset
    [[ -f "$path" && -x "$path" ]] || return 1
    size="$(stat -c %s "$path" 2>/dev/null || echo 0)"
    [[ "$size" -ge "$min" ]] || return 1
    is_elf "$path" || return 1
    has_runtime_version "$path" || return 1
    if [[ "$need_payload" == 1 ]]; then
        offset="$("$path" --appimage-offset 2>/dev/null || echo 0)"
        [[ "$offset" =~ ^[0-9]+$ ]] || return 1
        (( offset > 0 && size - offset >= MIN_PAYLOAD_BYTES )) || return 1
    fi
    return 0
}

#: The cache, or a copy left behind by the previous version of this script.
#: Anything that fails verification is deleted instead of reused: keeping a
#: broken tool turns the next build into a failure that has nothing to do with
#: the sources.
find_cached() {  # $1 file name, $2 minimum bytes, $3 payload required -> path
    local name="$1" min="$2" need_payload="$3" src
    for src in "${tools}/${name}" "${legacy_tools}/${name}"; do
        [[ -f "$src" ]] || continue
        chmod +x "$src" 2>/dev/null || true
        if is_usable "$src" "$min" "$need_payload"; then
            mkdir -p "$tools"
            if [[ "$src" != "${tools}/${name}" ]]; then
                mv -f "$src" "${tools}/${name}" && src="${tools}/${name}"
                echo "沿用旧的工具缓存：${src}" >&2
            fi
            printf '%s\n' "$src"
            return 0
        fi
        echo "删除无效的工具缓存（下载不完整或无法运行）：${src}" >&2
        rm -f "$src"
    done
    return 1
}

#: Download to partial/, verify, and only then give the file its real name.  A
#: half-finished download therefore never looks like a cache hit, whatever
#: interrupted it.
fetch_tool() {  # $1 file name, $2 minimum bytes, $3 payload?, rest: URLs
    local name="$1" min="$2" need_payload="$3"
    shift 3
    local dest="${tools}/${name}" part="${partial}/${name}" url first=1 retries
    mkdir -p "$tools" "$partial"
    for url in "$@"; do
        retries=3
        if [[ "$first" == 1 ]]; then
            # GitHub itself gets one chance: where it works it is the fastest
            # route, and where it does not, every attempt costs the whole speed
            # window (30 s here) for nothing - the mirrors answer at once.
            retries=0
            first=0
        fi
        echo "下载 ${name}：${url}" >&2
        rm -f "$part"
        if fetch "$url" "$part" "$retries"; then
            chmod +x "$part"
            if is_usable "$part" "$min" "$need_payload"; then
                mv -f "$part" "$dest"
                echo "已保存：${dest}" >&2
                return 0
            fi
            echo "下载到的文件不完整（体积、自检或载荷不符），换下一个地址。" >&2
        fi
        rm -f "$part"
    done
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
        --offline)  offline=1 ; shift ;;
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
    echo "build.sh 会清空 dist/ 和 build/；工具缓存在 ${cache}，不受影响。" >&2
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

printf '\n=== 构建工具 ===\n'
printf '缓存目录：%s\n' "$tools"
mkdir -p "$tools" "$partial"

offline_error() {  # $1 tool name, $2 env var, $3 file name
    {
        echo "--offline 指定了，但缓存里没有可用的 $1：${tools}/$3"
        echo "去掉 --offline 让它下载，或设置 $2 指向手动下载的文件。"
    } >&2
    exit 1
}

if [[ -n "${APPIMAGETOOL:-}" ]]; then
    tool="$APPIMAGETOOL"
    chmod +x "$tool" 2>/dev/null || true
    if ! is_usable "$tool" "$APPIMAGETOOL_MIN_BYTES" 1; then
        {
            echo "APPIMAGETOOL 指向的文件不是可用的 appimagetool：${tool}"
            echo "（需要是 appimagetool 的 AppImage：体积够、自带 runtime 与载荷）"
        } >&2
        exit 1
    fi
else
    tool="$(find_cached appimagetool-x86_64.AppImage "$APPIMAGETOOL_MIN_BYTES" 1 || true)"
    if [[ -z "$tool" ]]; then
        [[ "$offline" == 1 ]] && offline_error appimagetool APPIMAGETOOL \
            appimagetool-x86_64.AppImage
        if ! fetch_tool appimagetool-x86_64.AppImage "$APPIMAGETOOL_MIN_BYTES" 1 \
            "${APPIMAGETOOL_URLS[@]}"; then
            {
                echo "appimagetool 下载失败（三个地址都不可用）。"
                echo "可手动下载 appimagetool-x86_64.AppImage，设置 APPIMAGETOOL 指向它，"
                echo "或直接放进 ${tools}/ 后重跑。"
            } >&2
            exit 1
        fi
        tool="${tools}/appimagetool-x86_64.AppImage"
    fi
fi
echo "appimagetool：${tool}"

# appimagetool downloads the type2 runtime from GitHub on its own, which fails
# on the same networks that block the tool itself.  Fetch it here and hand it
# over explicitly with --runtime-file.
if [[ -n "${APPIMAGE_RUNTIME:-}" ]]; then
    runtime="$APPIMAGE_RUNTIME"
    chmod +x "$runtime" 2>/dev/null || true
    if ! is_usable "$runtime" "$RUNTIME_MIN_BYTES" 0; then
        echo "APPIMAGE_RUNTIME 指向的文件不是可用的 type2 runtime：${runtime}" >&2
        exit 1
    fi
else
    found="$(find_cached runtime-x86_64 "$RUNTIME_MIN_BYTES" 0 || true)"
    if [[ -z "$found" ]]; then
        [[ "$offline" == 1 ]] && offline_error "AppImage runtime" APPIMAGE_RUNTIME \
            runtime-x86_64
        if ! fetch_tool runtime-x86_64 "$RUNTIME_MIN_BYTES" 0 "${RUNTIME_URLS[@]}"; then
            {
                echo "AppImage runtime 下载失败（三个地址都不可用）。"
                echo "可手动下载 runtime-x86_64，设置 APPIMAGE_RUNTIME 指向它，"
                echo "或直接放进 ${tools}/ 后重跑。"
            } >&2
            exit 1
        fi
    fi
    runtime="${found:-${tools}/runtime-x86_64}"
fi
echo "runtime：${runtime}"

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
        local log="${logs}/appimage-${label// /}.log"
        last_log="$log"
        mkdir -p "$logs"
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

