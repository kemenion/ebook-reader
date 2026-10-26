# 项目基线（先读这一页）

自包含、高性能、中文排版考究的桌面 EPUB 阅读器。PySide6 + QML + `QTextDocument`，
排版由 Qt 富文本引擎完成，**运行时只有一个依赖**：`PySide6-Essentials`。

项目文档（改动前按需查阅，它们是本规范的依据）：

| 文档 | 何时读 |
|---|---|
| `README.md` | 用户可见行为、安装 / 运行 / 打包 |
| `arch/architecture.md` | 分层、模块职责、ADR（001–024）、性能预算、缺陷史 |
| `requirement/requirements.md` | FR / NFR / 约束（引用编号时用它核对） |

## 硬约束（不可协商，任何改动都不得破坏）

1. **依赖清单只允许 `PySide6-Essentials`**。禁止 `PySide6-Addons`（QtWebEngine）、
   Chromium / WebKit / Electron / Tauri / GTK，禁止新增第三方解析库（ADR-009）。
   EPUB 解析只用 stdlib：`zipfile`、`xml.etree`、`html`、`json`（ADR-010）。
2. **分层单向**：`QML → app（ReaderController）→ typeset → domain`，无环。
   - `domain/` **不得 import 任何 Qt**（ADR-012）。图片用 `ImageRef`（路径 + 内置尺寸），
     解码只发生在 `typeset/`。
   - `typeset/` 与 `app/` 可用 `QtCore/QtGui`；`app` 另可用 `QtQml/QtQuick`（启动引导与
     `PageItem`）；`typeset` 不碰 QML，`app` 不含排版算法。
   - QML 不做算法、不持有业务状态，只绑定与转发输入。
3. **不使用 `QTextDocument.setHtml()`**：一律由 `Block` 模型经 `QTextCursor` 程序化构建，
   发布者 CSS 不得影响排版（ADR-002）。
4. **避头尾在入库期注入 U+2060**（`domain/html/kinsoku.py`）。任何字符偏移量都以
   **注入后**的文本为准（ADR-005 / ADR-011）。
5. **一节 = 一个 `QTextDocument` = 一条连续列**，没有页、没有页码；位置是距列顶的偏移，
   持久化用 `(section, block, char_offset)`（ADR-003 / ADR-011 / ADR-016）。
6. **Python 侧只光栅化，QML 侧只合成**：渲染单位是「页框 + 1 个 32 px 量子」，
   量子内滚动（`PageItem.pan`）**不得回到 Python**（ADR-008 / NFR-005）。
7. **可变状态只住在 `ReaderController`**（C-013）。QML 通过注入的 `ctl` 读写；
   新增 UI 能力先加控制器属性 / `Slot`，不要在 QML 里造第二份状态。
8. **图片按需解码 + LRU + 文件头预读尺寸**，禁止全量解码、禁止解压到临时目录（ADR-006）。
9. **可恢复错误继承 `BookError`**，UI 显示可读中文提示，不得把 traceback 甩给读者
   （FR-008 / NFR-020）。
10. Python 版本 `>=3.10,<3.15`；不引入需要更高版本或编译扩展的依赖。

## 常用命令（在仓库根目录，使用 `.venv`）

```bash
./run.sh ebooks/某本书.epub                        # 源码直跑（需图形环境）
PYTHONPATH=src .venv/bin/python -m ebook_reader     # 等价写法

QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/unit -q          # 纯 Python，约 1–2 s
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/integration -q  # 约 85 s，按需跑单个文件

packaging/build.sh --dir-only                      # 文件夹版（AppImage 的底料）
packaging/build-appimage.sh --build                # 一键出 AppImage
```

`ebooks/` 是参考书（测试数据，已被 `.gitignore` 忽略）；缺失时相关用例应 skip 而不是 fail。
