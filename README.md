# ebook-reader

自包含、高性能、中文排版考究的桌面 EPUB 阅读器。

**不使用** Chromium / WebKit / Electron / GTK。排版由 Qt 自身的富文本引擎完成，
运行时依赖只有 **1 个**：`PySide6-Essentials`。

## 为什么值得做

| | 本阅读器 | Calibre / Electron | Foliate / Tauri |
|---|---|---|---|
| 排版引擎 | Qt `QTextDocument`（自带 CJK 字间对齐） | Chromium | 系统 WebKitGTK |
| 运行时依赖 | **1 个 Python 包** | 捆绑 Chromium | 绑定系统 webkit2gtk |
| 滚动发行版升级后 | 不受影响 | 不受影响 | **可能直接打不开** |
| 内存 | ~150 MB | 350–450 MB | ~400 MB |
| 避头尾（禁则） | **实测 645 行 0 违规** | 依赖 CSS，通常不做 | 依赖 CSS |

## 特性

- EPUB 2 与 EPUB 3（含无扩展名文档、脏 HTML、内联样式）
- 中文两端对齐、首行缩进、可调行距/字号/页边距
- **避头尾**：通过注入 U+2060 WORD JOINER 在字符流层面强制禁则，与 Qt 版本无关
- 图片按需解码（408 张图 / 85 MB 的书只占 ~27 MB 图片缓存）
- 图片与标题不被页边界切断
- 日间 / 米色 / 夜间三主题，宋体 / 黑体 / 楷体三字体
- 目录侧栏、页码与全书进度、阅读位置记忆（跨字号、跨窗口尺寸都有效）

## 安装与运行

```bash
python3 -m venv .venv
.venv/bin/pip install PySide6-Essentials      # 唯一运行时依赖

# 运行（源码直接跑）
PYTHONPATH=src .venv/bin/python -m ebook_reader 你的书.epub
```

## 快捷键

| 键 | 作用 |
|---|---|
| `←` `→` / `PgUp` `PgDn` / `空格` | 翻页 |
| `Home` / `End` | 本节首页 / 末页 |
| `[` / `]` | 上一章 / 下一章 |
| `T` / `S` | 目录 / 设置 |
| `Ctrl` `+` / `-` | 字号 |
| `F11` | 全屏 |
| `Esc` | 关闭侧栏 |
| `Ctrl+O` / `Ctrl+Q` | 打开文件 / 退出 |

鼠标点击屏幕左半翻上一页、右半翻下一页；滚轮也可翻页。

## 测试

```bash
.venv/bin/pip install pytest
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest        # 约 2.3 秒，106 项
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/unit -q   # 纯 Python，无 Qt
```

集成测试直接断言设计文档里的性能指标（单节排版 ≤ 50 ms、单页渲染 ≤ 20 ms）与质量指标
（0 张图片被切断、0 个违法行首、阅读位置跨字号可恢复），因此文档与代码不会脱节。

## 项目结构

```
requirement/requirements.md    需求规格（含实测基线与可测量指标）
arch/architecture.md           架构设计（含 13 条 ADR 与 P0 实测记录）
src/ebook_reader/
├── domain/                    零 Qt 依赖：EPUB 解析 + XHTML 规范化 + 避头尾
├── typeset/                   依赖 QtGui：文档构建 + 分页修正 + 渲染 + 图片缓存
├── app/                       QML 控制器、设置持久化、启动引导
└── qml/                       界面（无业务逻辑）
tests/unit/ tests/integration/
```

## 已知限制（有意为之）

- 不支持 **竖排（直排）** 与 **ruby 注音**：Qt 6 无此能力，自研成本为年级
- 不支持表格：实测两本目标书均为 0 个 `<table>`
- 仅分页模式，无连续滚动
- 仅 EPUB，不支持 PDF / MOBI / AZW3

详见 `arch/architecture.md` 第 2.2 节与 ADR-001 / ADR-004。

## 实测数据（P0 门禁）

| 指标 | 实测 |
|---|---|
| 单节排版（22,000 字，稳态）中位数 | **14.7 ms** |
| 单页光栅化中位数 | **8.1 ms** |
| 避头尾违法行首 | **645 行 → 0** |
| 图片被页边界切断 | **全书 428 页 → 0** |
| 全书解析（27 节 / 23.8 万字 / 408 图） | **118 ms** |
| 图片缓存峰值 | 27.3 MB |
| 测试 | 106 项 / 2.32 s / 全绿 |
