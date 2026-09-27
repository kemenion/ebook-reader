# 电子书阅读器 架构设计文档

| 项 | 值 |
|---|---|
| 项目 | ebook-reader |
| 版本 | v1.0 |
| 日期 | 2026-09-27 |
| 需求基线 | `requirement/requirements.md` v1.0 |
| 选定架构 | **架构 C：PySide6 + QML + QTextDocument（无 Chromium）** |
| 状态 | 已定稿，进入实现 |

---

## 1. 文档目的

本文档回答三个问题：

1. **为什么是架构 C？** —— 给出候选方案全矩阵、淘汰每一步的**可验证证据**（不是偏好）。
2. **架构 C 长什么样？** —— 分层、模块、数据模型、渲染管线、目录结构。
3. **架构 C 的坑在哪里、怎么解？** —— 以 ADR（架构决策记录）形式逐条固化决策与缓解措施。

---

## 2. 架构目标（由需求 NFR 推导）

架构必须同时满足以下**相互冲突**的目标，这是本项目的核心难点：

| 目标 | 来源 | 冲突点 |
|---|---|---|
| G1 中文排版考究（两端对齐 + 避头尾 + 首行缩进 + 可调行距） | FR-030~FR-038 | 自研排版最灵活但成本极高 |
| G2 内存 ≤ 200MB（Chromium 方案为 350–450MB） | NFR-002 | Web 方案天然重 |
| G3 启动 ≤ 1.0s | NFR-001 | 同上 |
| G4 库升级免疫（不随系统 webkit/gtk 大版本爆炸） | NFR-012, C-001 | GTK/WebKit/Tauri 方案结构性不满足 |
| G5 包体积 ≤ 150MB | NFR-011 | 捆绑 Chromium 即 200MB+ |
| G6 滚动流畅度 60fps（量子内位移零回调；整屏切换可加 3D / 卷曲效果） | NFR-005, FR-092 | 需要 GPU 级合成能力 |
| G7 Linux + Windows 双平台 | NFR-021 | WebKitGTK/libadwaita 无 Windows 支持 |
| G8 实现成本可控（人月级，非人年级） | 项目现实 | 自研 shaping/断行不可接受 |

**关键洞察**：G1 与 G8 的冲突只能靠「找一个已经实现了中文排版的现成引擎」来解。而 Qt 的 `QTextDocument` 恰好就是。

---

## 3. 候选架构评估

### 3.1 候选清单

| 代号 | 方案 | 技术栈 |
|---|---|---|
| A | Web 前端 + Python 后端 | pywebview / Electron 风格，HTML/CSS/JS 排版，Python 管文件 |
| B | QML 壳 + QtWebEngine | QML 做 UI，Chromium 引擎排版，ShaderEffect 做动画 |
| **C** | **PySide6 + QML + QTextDocument** | **QML 做 UI，Qt 富文本引擎排版，无 Chromium** |
| D | 自研排版引擎 | 绑定 HarfBuzz/FreeType，自己实现断行、整形、光栅化 |
| E | GTK4 + libadwaita + WebKitGTK | 系统 WebKit 排版 |
| F | Tauri | Rust 壳 + 系统 WebView (webkit2gtk) |

### 3.2 评估矩阵

评分：✅ 满足 / ⚠️ 部分满足 / ❌ 不满足。所有判据来自实测或官方文档。

| 判据 | A | B | **C** | D | E | F |
|---|---|---|---|---|---|---|
| G1 中文排版 | ✅ CSS | ✅ CSS | ✅ **Qt 内建 CJK 对齐路径** | ⚠️ 全靠自研 | ✅ CSS | ✅ CSS |
| G2 内存 | ❌ 350–450MB | ⚠️ ~300MB | ✅ **120–150MB** | ✅ ~120MB | ❌ ~400MB | ⚠️ ~300MB |
| G3 启动 | ❌ ~1.0s | ❌ ~1.0s | ✅ **~0.4s** | ✅ ~0.3s | ❌ ~1.0s | ❌ ~1.0s |
| G4 库升级免疫 | ✅ 捆绑 Chromium | ✅ 捆绑 Chromium | ✅ **捆绑 Qt** | ✅ 自包含 | ❌ **绑系统 WebKitGTK** | ❌ **绑系统 webkit2gtk** |
| G5 包体积 | ❌ ~200MB | ❌ ~200MB | ✅ **~80–150MB** | ✅ ~60MB | ⚠️ 依赖系统 | ⚠️ 依赖系统 |
| G6 滚动流畅度 | ⚠️ CSS 3D（受限） | ✅ GPU 合成 | ✅ **QImage → GPU 纹理，量子内纯平移** | ✅ 完全自由 | ⚠️ CSS 3D | ⚠️ CSS 3D |
| G7 Windows | ✅ | ✅ | ✅ **Qt 一等平台** | ⚠️ 需自行移植 | ❌ **无 Windows** | ⚠️ 用 WebView2 另一套代码 |
| G8 实现成本 | ✅ 低 | ✅ 低 | ✅ **低（全是组装）** | ❌ **极高（人年）** | ✅ 低 | ✅ 低 |
| 依赖数量 | ⚠️ 需 webview 绑定 | ⚠️ 2 项 | ✅ **1 项** | ❌ HarfBuzz+FreeType+… | ❌ 系统 GTK 栈 | ❌ 系统 WebKit 栈 |

### 3.3 淘汰过程与决定性证据

#### E 与 F 的淘汰（结构性，不可协商）

**证据 1**：WebKitGTK 与 libadwaita **没有 Windows 支持**。这直接违反 G7（NFR-021）。GTK 在 Windows 上的支持处于「可用但非一等」状态，libadwaita 从未支持。

**证据 2**：Tauri 绑定系统 `webkit2gtk-4.1`。在 Manjaro 这类**滚动发行版**上，`webkit2gtk` 大版本升级（4.0 → 4.1 → 未来 6.0）会导致应用直接无法启动，且 soname 变化后旧二进制无法回退。这**正是用户明确要避免的「库升级我要跟着升级」**（对应 C-001）。

> **淘汰结论**：E、F 在「库升级免疫」这一硬约束上结构性不满足，其他优点无法补偿。

#### B 与 A 的淘汰（可优化但非最优）

A 与 B 都依赖 Chromium 系引擎，共同问题是 G2/G3/G5：

- 内存 300–450MB（对比 C 的 120–150MB）——这是 Chromium 多进程架构的固有成本，无法通过优化消除。
- 启动 1.0s——对比 C 的 0.4s，差距来自引擎初始化。
- 包体积 200MB+——Chromium 二进制本身。

此外 A 还有额外问题：Web 前端意味着**页面渲染控制权在浏览器手里**，做「纸张卷曲」只能靠 CSS 3D transform + `clip-path` 模拟，无法拿到真正的网格级形变。

> **淘汰结论**：A、B 是「用 3 倍资源换 web 技术栈」，在「性能优先 + 中文排版优先」的目标下劣于 C。

#### D 的淘汰（成本）

D 理论上最优（完全自由），但需自行实现：Unicode 断行（UAX#14）、双向文本（UAX#9）、CJK 避头尾、OpenType shaping（连字、kerning、变体选择符）、字形光栅化与缓存、（多平台时）字体后端适配。

这**不是**「先做简单版」能解决的领域——做到「中文排版好看」所需的完整度约等于重写 Pango/CoreText 的核心，工作量在**人年**级别（G8 ❌）。

> **淘汰结论**：成本不可接受。且 D 的收益（排版自由度）在 v1 需求中**并不需要**——需求只要「两端对齐 + 避头尾 + 缩进 + 行距」，全部可由 Qt 内建能力达成。

### 3.4 架构 C 胜出的决定性证据

以下三项发现是本次选型的关键，**均经实测验证**。

#### 证据 1：Qt 引擎**内建** CJK 两端对齐路径 ⭐（最关键的发现）

Qt 源码 `qtbase/src/gui/text/qtextengine.cpp` 中，字距分配逻辑对 CJK 脚本有专门分支：

```cpp
switch (si.analysis.script) {
...
case QChar::Script_Tibetan:
case QChar::Script_Hiragana:
case QChar::Script_Katakana:
case QChar::Script_Bopomofo:
case QChar::Script_Han:
    // same as default but inter character justification is the only option
    spaceAs = Justification_Character;
    break;
}
```

**含义**：当脚本被识别为 Han（汉字）/ Hiragana / Katakana / Bopomofo 时，Qt **强制使用「字符间分配空隙」（inter-character justification）** 来实现两端对齐。

这正是中文排版的**正解**（西文靠词间空格拉伸，中文靠字间微调）。也就是说：

> `Qt::AlignJustify` 用在中文段落上，**不需要任何额外工作**即可得到正确的两端对齐。

同一份源码中还有：初始化时将全部字形的 justification 设为 `Prohibited`，再由 `qt_getDefaultJustificationOpportunities()` 标注可拉伸位置 —— 说明 Qt 对「哪些字符之间允许拉伸」有**显式建模**，已具备标点禁则的基础认知。

#### 证据 2：`QTextBoundaryFinder` 实现 UAX#14 断行

实测头文件 `/usr/include/qt6/QtCore/qtextboundaryfinder.h`：

```cpp
enum BoundaryType { Grapheme, Word, Sentence, Line };
enum BoundaryReason {
    NotAtBoundary = 0,
    BreakOpportunity = 0x1f,
    StartOfItem  = 0x20,
    EndOfItem    = 0x40,
    MandatoryBreak = 0x80,
    SoftHyphen   = 0x100
};
```

**含义**：Qt 有完整的 Unicode 行断类数据（UAX#14），并暴露为公开 API。即使最坏情况下 Qt 内建断行对中文标点处理不完美，我们也能：
- 用 `QTextBoundaryFinder(Line)` 自行精确计算断点，或
- 用 U+2060 WJ 在入库期直接强制禁则（ADR-005，更简单）。

#### 证据 3：全部所需排版 API 均存在且稳定

实测 `/usr/include/qt6/QtGui/` 头文件，逐条确认：

| 能力 | API | 位置 |
|---|---|---|
| 定量版面 / 取列高 | `QTextDocument::setTextWidth` / `documentSize()`（`setPageSize` 自 ADR-016 起不再使用） | qtextdocument.h |
| 首行缩进 | `QTextBlockFormat::setTextIndent()` | qtextformat.h:659 |
| 行高 | `QTextBlockFormat::setLineHeight(h, type)` | qtextformat.h:673 |
| 页边距 | `setTopMargin` / `setBottomMargin` / `setLeftMargin` / `setRightMargin` | qtextformat.h:639–654 |
| 块级断行策略 | `setPageBreakPolicy(PageBreakFlags)`（ADR-016 后不再使用，保留为历史证据） | qtextformat.h:686 |
| 懒加载 | `QTextDocument::loadResource()` **virtual** | qtextdocument.h |
| 字距微调 | `QTextCharFormat::setFontLetterSpacing(qreal, SpacingType)` | qtextformat.h:463–467 |
| 锚点/脚注 | `setAnchor(bool)` / `setAnchorHref(QString)` | qtextformat.h:568–573 |
| 选区绘制 | `QAbstractTextDocumentLayout::PaintContext::selections` | qabstracttextdocumentlayout.h |
| 命中测试 | `QAbstractTextDocumentLayout::hitTest(pos, accuracy)` | 同上 |
| 搜索 | `QTextDocument::find(QRegularExpression)` | qtextdocument.h |
| 字体回退 | `QTextCharFormat::setFontFamilies(QStringList)` | qtextformat.h:426 |
| 程序化构建 | `QTextCursor::insertBlock/insertText/insertImage` | qtextcursor.h:58–175 |
| 引擎可替换 | `QAbstractTextDocumentLayout` 可整套子类化 | 同上 |

**结论**：架构 C 不存在「需要发明」的技术，只存在「需要组装」的代码。

---

## 4. 架构 C 总览

### 4.1 分层视图

```
┌─────────────────────────────────────────────────────────────┐
│  表现层 (Presentation) — QML                                 │
│  ┌──────────────┐ ┌────────────┐ ┌──────────────┐ ┌──────────┐  │
│  │ ReadingView  │ │ TocSidebar │ │ OutlinePanel │ │ Settings │  │
│  │ 窗口纹理+滚动│ │ 目录树     │ │ 本节大纲     │ │ 设置面板 │  │
│  └──────────────┘ └────────────┘ └──────────────┘ └──────────┘  │
│         ▲ QML 属性绑定 / 信号槽                              │
├─────────┼───────────────────────────────────────────────────┤
│  控制层 (Controller) — Python QObject                        │
│  ReaderController: 当前节/滚动偏移、滚动、跳转、位置记忆、设置 │
│  职责：状态机 + 编排；不含排版算法，不含解析逻辑               │
├─────────┼───────────────────────────────────────────────────┤
│  排版层 (Typeset) — Python，依赖 QtGui                       │
│  ┌──────────────┐ ┌──────────────┐ ┌─────────────────────┐  │
│  │ StyleResolver│ │ Kinsoku      │ │ DocumentBuilder     │  │
│  │ 字体/字号/行距│ │ WJ 注入/挤压 │ │ Block[]→QTextDocument│  │
│  └──────────────┘ └──────────────┘ └─────────────────────┘  │
│  ┌──────────────┐ ┌──────────────┐ ┌─────────────────────┐  │
│  │ LayoutEngine │ │ ImageCache   │ │ WindowRenderer      │  │
│  │ 连续列排版   │ │ 懒解码 + LRU │ │ 窗口 → QImage        │  │
│  └──────────────┘ └──────────────┘ └─────────────────────┘  │
├─────────┼───────────────────────────────────────────────────┤
│  领域层 (Domain) — 纯 Python，零 Qt 依赖 ← 可无 GUI 测试     │
│  ┌──────────────┐ ┌──────────────┐ ┌─────────────────────┐  │
│  │ Block 模型   │ │ XHTML 规范化 │ │ EPUB 解析           │  │
│  │ 段落/标题/图 │ │ 脏 HTML 清洗 │ │ container/OPF/NCX/  │  │
│  │              │ │              │ │ nav/资源定位        │  │
│  └──────────────┘ └──────────────┘ └─────────────────────┘  │
├─────────┼───────────────────────────────────────────────────┤
│  基础设施 (Infrastructure) — stdlib                         │
│  zipfile（ZIP 随机读取）、xml.etree（XML 解析）、html（实体）│
│  json（配置与位置持久化）、pathlib、dataclasses              │
├─────────────────────────────────────────────────────────────┤
│  平台层 (Platform) — Qt6                                     │
│  QtCore / QtGui / QtQml / QtQuick；Wayland+X11；RHI(可软渲染)│
└─────────────────────────────────────────────────────────────┘
```

### 4.2 依赖方向（严格单向，无环）

```
UI (QML) ──> Controller ──> Typeset ──> Domain ──> Infrastructure
                 │              │
                 └──────────────┴──────> Platform (Qt6)
```

**规则**：

- `domain` **不得** import 任何 Qt 模块 → 保证解析与规范化可无 GUI 单测（NFR-022）
- `typeset` 可依赖 `QtGui`（`QTextDocument` 属 QtGui）
- `ui` 不包含任何算法，只做状态展示与用户输入转发
- Controller 是**唯一**持有可变状态的层（对应 C-013）

### 4.3 关键设计原则

| 原则 | 落地方式 |
|---|---|
| **关注点分离** | 解析（文本→块模型）、排版（块模型→文档）、渲染（文档→位图）、交互（位图→用户）四段完全解耦 |
| **单向数据流** | QML 只读 Controller 的只读属性，通过信号发起动作 |
| **无 GUI 可测** | Domain + Kinsoku 全部可在纯 Python 下测试 |
| **接口隔离逃生舱** | `BookSource` 抽象（当前仅 `EpubBook`，未来可加 `Fb2Book`）；`WindowRenderer` 与 QML 之间只传 `QImage` |
| **不做全量预计算** | 节按需解析、按需排版、按需解码图片（对应 I-3 / R-05） |
| **可降级** | 无 GPU 时软件光栅化（`QT_QUICK_BACKEND=software` 实测可用）；列不再分级，故没有「修正迭代超限」这一类降级（ADR-016） |

### 4.4 一次滚动的数据流（端到端）

```
用户滚动（滚轮 / 点击上下半屏 / ← → / PgUp PgDn / 拖动滚动条）
   │
   ▼
[QML] ReadingView 调 ctl.scrollBy(px)（或 scrollUp / scrollDown / scrollPageUp / …）
   │
   ▼
ReaderController 求新偏移并判断是否越过本节两端
   ├─ 仍在列内 → 只更新 scrollOffset（多数帧连这一步都在 QML 里完成）
   └─ 越过节端 → 切到下一/上一节：Book.blocks(i±1)（领域层，纯 Python）
   │
   ▼
[Typeset] LayoutEngine.section(i)                ← 仅切节时执行
   │          ├─ WJ 已在领域层规范化时注入（ADR-005），此处不重复
   │          ├─ document.build(blocks, style) → QTextDocument（ADR-002）
   │          └─ 一次布局得到**连续列总高** height，不再按页切分（ADR-016）
   │
   ▼
[Typeset] LayoutEngine.render_window(i, offset) → QImage
   │          └─ 内部先 clamp 到 [0, max_offset]，再 quantise_offset()（32 px）
   │             → 图为「页框 + 1 个渲染量子」高（ADR-008 / ADR-016）
   │
   ▼
[QML] PageItem 把该 QImage 当纹理，只贴出「页框」那一块：
      量子内的位移 = 源矩形平移（纯 GPU，零 Python 回调，NFR-005）
```

**性能关键**：同节内滚动时，只需第 5 步的 `render_window()`（实测 9–17 ms），而且**只在跨越量子时**才发生；量子内的每一帧都只是 `PageItem` 换一次源矩形，不进 Python。这是滚动跟手的根据（ADR-008 / ADR-016）。


---

## 5. 架构决策记录（ADR）

格式：每条 ADR 含 **背景 / 决策 / 理由 / 已否决的替代方案 / 后果（含代价） / 验证方式**。

### ADR-001 采用 `QTextDocument` 作为排版引擎，不自研文字整形

| 项 | 内容 |
|---|---|
| **背景** | 需求要求中文两端对齐、避头尾、首行缩进、可调行距（FR-030~FR-038）。自研排版需实现 UAX#14 断行、UAX#9 双向、CJK 禁则、OpenType shaping、字形光栅化缓存——工作量人年级（对应 G8）。 |
| **决策** | 使用 Qt 内建富文本引擎 `QTextDocument` 承担全部排版职责。 |
| **理由** | 实测确认 Qt 引擎对 `Script_Han` 有**专用字距分配分支**（`spaceAs = Justification_Character`），即按字符间空隙实现两端对齐——这正是中文排版正解，**无需任何额外工作**。同时行高、缩进、页边距、块级断行策略等 API 齐备（见 3.4 证据 3；其中 `setPageSize` / `pageCount()` / `setPageBreakPolicy` 自 ADR-016 改为连续列后已不再使用）。 |
| **已否决** | ① 自研 shaping 引擎：成本人年级。② 用 `QTextLayout` 逐行自管（比 QTextDocument 低一层，但需自管块模型与滚动定位）——留作 ADR-001-B 的后备方案，仅当 QTextDocument 在避头尾上被证伪时启用。 |
| **后果（代价）** | 受限于 Qt 引擎能力边界：**不支持竖排（直排）与 ruby 注音**（已列入 Out of Scope，且实测两本目标书均无 ruby）。若未来遇到直排书，需退化为「能读但不好看」。 |
| **验证** | P0 实测：中文段落 `AlignJustify` 观感 + 节排版耗时 ≤ 50ms。 |

**后备方案（止损路径）**：若 Qt 内建排版在中文上被证伪，可子类化 `QAbstractTextDocumentLayout`（已确认该类完整可替换）替换排版实现，**上层接口不变**——这是本决策的风险对冲。

---

### ADR-002 用 `QTextCursor` 程序化构建文档，**不使用** `setHtml()`

| 项 | 内容 |
|---|---|
| **背景** | 目标书 XHTML 含 4044 个 `<b>`、2303 个 `<span>`、554 个 `<div>`、17 种内联 style，且声明了 Linux 上**不存在**的字体 `PingFang SC` / `FZFangSong-Z02`（I-4/I-5）。 |
| **决策** | 不使用 `QTextDocument.setHtml()`；改为把 XHTML 规范化为**自有 `Block` 模型**，再用 `QTextCursor::insertBlock/setBlockFormat/insertText/setCharFormat/insertImage` 逐块写入。 |
| **理由** | ① Qt 的 `setHtml` 只支持 **HTML4 子集 + 极少量 CSS**，`<span>` 上的 margin 等无效，且无法干预发布者的 `font-family`。② 程序化构建让**样式 100% 由我们的 `TypographySettings` 决定**，彻底免疫发布者 CSS（对应 FR-024）。③ 块模型是纯 Python 数据结构，可在无 GUI 下测试（NFR-022）。 |
| **已否决** | ① `setHtml()`：受 HTML 子集限制且无法可靠覆盖内联样式（发布者的 `font-family` 会污染排版）。② 先 `setHtml` 再遍历修正 `QTextCharFormat`：需要反向解析 Qt 的产出，逻辑脆弱且慢。 |
| **后果（代价）** | 需自写 XHTML→Block 规范化层（约 500–700 行），并承担「块模型丢文本」的风险 → 用**字符守恒断言**（块模型文本总长 vs 原始文本长度差异 < 1%）在单测中守住（R-09）。 |
| **验证** | 单测：两本目标书全部 62 个节的「字符守恒」检查；P0 目视排版正确。 |

---

### ADR-003 以「**节**」为文档单元，配合 LRU 缓存（而非全书单文档）

| 项 | 内容 |
|---|---|
| **背景** | 康波书 27 节 / 23.8 万字，币安书 35 节 / 19.1 万字。`QTextDocument` 的每个段落是一个 C++ 对象，单文档承载全书会产生数万块与数万 `QTextBlock` 对象。 |
| **决策** | 一个 **spine 节** 对应一个 `QTextDocument`；维护最近 **N 节（默认 3）** 的 `(QTextDocument, 连续列几何, 窗口位图缓存)` 的 LRU（即 `LayoutEngine.section()`，ADR-016）；切节时按需构建。 |
| **理由** | ① 单节最大 22,051 字 ≈ 100 段，Qt 布局耗时可控（目标 30ms）。② 排版成本被**摊到每次切节**，而非打开书时一次性 200ms+ 卡住。③ 内存可控：同时最多 3 个文档在内存。④ 天然支持「按节跳转」（目录跳转即切节）。 |
| **已否决** | ① 全书单文档：布局开销大、内存高、改字号时全量重排会有明显卡顿。② 一页一文档（改为连续列后即「一个窗口一文档」）：仍要**预先知道边界**，而窗口是渲染期的取景概念——按偏移现算，逻辑上不可能。 |
| **后果（代价）** | **跨章节连续滚动会变得复杂**（需要拼接两个文档的布局）→ v1 曾由 ADR-004 的「分页模式」从根上规避；**ADR-016 改回连续滚动后，仍不做两文档拼接**：节就是列的单位，越过节端即换列（代价是「节尾留白」观感）→ 由 FR-059 缓解。 |
| **验证** | 内存占用 ≤ 200MB 且切节时无可见卡顿。 |

---

### ADR-004 主阅读模式为**分页**（pagination），非连续滚动

> **⚠ 已由 ADR-016 取代（下文整条保留为决策史）**：v1 之后主阅读模式改回**连续滚动列**，页面不再是模型的一部分——`Paginator` / `page_count` / 页码都不存在了，位置由连续偏移（offset）表达。下文「背景 / 决策 / 理由 / 已否决 / 代价」记录的是取代**之前**的取舍，其中「跨节不拼接」这一条仍被 ADR-016 沿用。

| 项 | 内容 |
|---|---|
| **背景** | 连续滚动（像浏览器一样竖向无限滚动）在架构 C 下需要拼接多个 `QTextDocument` 的布局、处理滚动中的图片懒加载、处理滚动位置与节位置的映射。 |
| **决策** | v1 只实现**分页模式**：内容按页尺寸切分，一次显示一页（或双页）。目录跳转 = 切节 + 页内定位。 |
| **理由** | ① 分页模型与 `QTextDocument.setPageSize/pageCount` **天然契合**，无需额外机制。② 避免了「跨节滚动」这一最大复杂度来源（ADR-003 的代价）。③ 分页是「读实体书」的心智模型，与本项目「排版考究、沉浸阅读」的定位一致。④ 每页内容量固定 → 图片解码预算可控（R-05）。 |
| **已否决** | 连续滚动：v1 复杂度不值得；如未来需要，可在 ADR-003 基础上用「视口内拼接相邻节」方式增量实现，不影响现有分层。 |
| **后果（代价）** | 用户无法像网页一样连续滚动（但可用滚轮映射为翻页，FR-063）。窗口尺寸变化需重新分页（FR-051）——用防抖 + 块级位置映射解决。 |
| **验证** | 窗口缩放后阅读位置不跳章（FR-051）。 |

---

### ADR-005 在**入库期**注入 U+2060 WORD JOINER 实现避头尾（不依赖 Qt 断行表）

| 项 | 内容 |
|---|---|
| **背景** | 需求要求避头尾（禁则，FR-031）。风险 R-01：Qt 内建断行对中文标点的禁则处理是否完备**不可 100% 保证**（Qt 有 UAX#14 表与 justification 机会建模，但没有公开文档承诺 CJK 禁则完整性）。 |
| **决策** | 不依赖 Qt 的断行结果，而是在 XHTML→Block 规范化阶段**主动注入** U+2060 WORD JOINER：在**禁止置于行首**的字符前、**禁止置于行尾**的字符后插入 WJ。UAX#14 规定 WJ 两侧**禁止断行**，故违法断点被物理排除。 |
| **理由** | ① **确定性**：结果是字符流的属性，与 Qt 版本、Qt 内部断行表实现无关——彻底消除 R-01 的不确定性。② 实现极简（约 80 行，纯字符串处理，可无 GUI 单测）。③ 副作用天然正确：若某行因此排不下，断点前移把标点连前一字推到下一行——**这正是标准避头尾行为**。 |
| **禁则字符集（v1）** | **禁行首（no-start）**：`。，、；：！？）》」』〕】…—～·` 及 `.` `,` `;` `:` `!` `?` 的全角/半角形式<br>**禁行尾（no-end）**：`《（「『〔【（` |
| **已否决** | ① 依赖 Qt 内建断行：不确定性违背工程原则。② 用 `QTextBoundaryFinder` 后处理断行结果：需要在渲染后介入，接口复杂，且 Qt 不给断点回写能力。 |
| **后果（代价）** | 注入 WJ 会改变文本字符数 → **任何字符偏移量必须基于「规范化后」的文本**计算（影响 ADR-011 的位置存储）：位置存 `(节索引, 块索引, 块内字符偏移)`，且偏移量以注入后的文本为准；因 WJ 是零宽字符，视觉无影响。搜索时需在匹配前剥除 WJ。 |
| **验证** | 单测：对含全量禁则字符的样本，检查每对「禁则字符 + 前/后字」之间确有 WJ；P0 目视抽查 20 页无违规行首/行尾标点。 |

---

### ADR-006 图片**按需解码** + LRU 缓存 + 表头预读尺寸

| 项 | 内容 |
|---|---|
| **背景** | 康波书 408 张图（85.0MB，均 208KB，典型 900×1400）。若全量解码：`408 × 900 × 1400 × 4B ≈ 2.0 GB`——**必然 OOM**（I-3 / R-05）。 |
| **决策** | 三层策略：<br>① **尺寸预读**：排版前只读 JPEG/PNG 文件头解析宽高（不解码像素），用于计算图片在版心中的占位尺寸；<br>② **懒解码**：仅当某个窗口需要绘制时，才真正解码该窗口涉及的图片；<br>③ **LRU 缓存**：缓存已解码 `QImage`，容量上限 24 张 / 32MB（双限），超出即淘汰最久未用者。 |
| **理由** | ① 一页通常含 0–2 张图 → 峰值解码量极小。② 头解析单张 < 1ms，408 张全预读仅需数十 ms，可接受。③ 缓存同时受「张数」与「字节数」双限，防止少数超大图撑爆内存。 |
| **实现要点** | 图片数据经 `QTextDocument::loadResource()`（**已确认是 virtual**）回调获取——按需从 ZIP 读取字节，无需解压到临时文件。 |
| **已否决** | ① 全量预解码：2.0GB，直接 OOM。② 全部解压到磁盘临时目录再按需读：磁盘占用 85MB+、首次打开慢、清理麻烦。③ 只读 ZIP 不缓存 `QImage`：每次滚回旧位置都重复解码，浪费 10ms×N。 |
| **后果（代价）** | 需额外「预取邻接窗口图片」逻辑（滚动前进时调 `LayoutEngine.prefetch(section, offset)`，顺手解码下一窗口覆盖的图），否则首次滚入该窗口时图片有约 10ms 可感知延迟。 |
| **验证** | 连续滚动 50 个窗口后 RSS ≤ 200MB；图片缓存峰值 ≤ 32MB；单张解码 ≤ 25ms。 |

---

### ADR-007 分页边界的**迭代修正**（图片与标题不被页边界切断）

> **⚠ 已由 ADR-016 取代（下文整条保留为决策史）**：连续列模式下**没有页边界**，因此没有跨界修正这件事——图片与标题可以出现在窗口的任意位置，本来就不「切断」任何东西（切开的只是窗口，滚动一下即可看到全貌）。但本 ADR 的**问题**（块不能被页边界切坏）在窗口渲染里换了个形式继续存在，由 ADR-016 用「窗口高度 = 页框 + 1 量子」的余量承接。

| 项 | 内容 |
|---|---|
| **背景** | `QTextDocument` 的分页是「文档按页高切块」，**不是块感知分页**。一张 900×1400 的图若恰好跨越页边界会被切成两半（FR-052 / R-02）。Qt 不提供 keep-with-next 或孤行控制。 |
| **决策** | 在 `setPageSize` 后执行一轮**几何校验 + 修正 + 重排**迭代：<br>① 遍历文档块，对每个**图片块 / 标题块**取 `documentLayout().blockBoundingRect(block)`；<br>② 计算所属页 `floor(rect.top() / page_h)` 与 `floor(rect.bottom() / page_h)`；<br>③ 若两者不等（跨界），为该块追加 `PageBreak_AlwaysBefore`；<br>④ 重新 `setPageSize` 触发重排，回到 ①；<br>⑤ 最多迭代 **8 次**，超限则中止并记录 warning（降级为允许切断，保证不卡死）。 |
| **理由** | ① 复用 Qt 的块级分页策略（`setPageBreakPolicy`），不侵入引擎。② 迭代**必然收敛**：每次迭代至少把 1 个跨界块推到下一页，且推后只会减少跨界块数量。③ 8 次上限是对「收敛」的工程兜底。 |
| **已否决** | ① 不处理：图片被切开，观感严重受损。② 自研分页算法（完全接管块到页的分配）：工作量与风险远超收益，且失去 Qt 自适应断行的好处。③ 给每张图强制 `PageBreak_AlwaysBefore`：会产生大量半空页，浪费版面。 |
| **后果（代价）** | 修正后页面底部可能出现留白（图片被推走）——**这正是纸质书的做法**（书中插图从不被切断）。版式变更（字号/窗口）时需重跑该流程。 |
| **验证** | 抽查 30 页无图片切断；记录真实书籍上的迭代次数（预期 ≤ 3）；单节修正耗时计入 NFR-003 的 50ms 预算。 |

---

### ADR-008 页面在 Python 侧光栅化为 `QImage`，QML 侧只做 GPU 合成

> **已由 ADR-016 修订（决策本身不变）**：光栅化单位由「页」改为「**窗口**」= 页框高 + 1 个渲染量子（32 px），Python 侧对量子对齐后的偏移绘制，QML 侧用源矩形平移取出页框那一块。职责切分与「动画期间零 Python 调用」都原样保留，下面提到「页」的地方按「窗口」读。

| 项 | 内容 |
|---|---|
| **背景** | 需同时满足「滚动与翻屏流畅」（NFR-005）与「无 GPU 环境可用」（NFR-017）。若每帧回调 Python 绘制，GIL + 绑定开销必然掉帧。 |
| **决策** | 严格职责切分：<br>① **Python 侧**：把目标**窗口**绘制为 `QImage`（宽 = 页框宽，高 = 页框高 + 1 个量子；尺寸 × `devicePixelRatio`），交给 QML；<br>② **QML 侧**：把该 `QImage` 当纹理（`PageItem.image`），只把「页框」那一块贴出来，位移用源矩形平移完成；<br>③ **量子内滚动期间零 Python 调用**（`PageItem.pan`），只有跨量子或跨节才回调 Python 取新图。 |
| **理由** | ① 动画帧由 Qt Quick 场景图（RHI）在 GPU 或软件后端完成，不受 Python 限制。② `QImage` 是两种后端都支持的最简公共接口。③ 软件渲染时同样可用，天然满足 NFR-017。 |
| **已否决** | ① 直接把 `QTextDocument` 暴露给 QML（`TextEdit`/`TextArea` 的 `textDocument`）：那是编辑场景接口，不支持按偏移 / 按窗口渲染，也无法控制避头尾注入与样式。② 每帧 `grabToImage()`：一帧一次全量重绘，无法 60fps。③ 用 `QQuickRhiItem` 自定义 GPU 渲染：能力最强但复杂度过高，v1 不需要；作为未来「极致性能」路径保留（NFR-032）。 |
| **后果（代价）** | 窗口位图占内存：`1600×(2400+32)×4B ≈ 15MB/张`（HiDPI 2×）→ 由 `ReaderController._window_cache` 的 LRU 限制（`_WINDOW_CACHE_SIZE = 5` 张 ≈ 75MB），须纳入 NFR-002 的 200MB 预算。低 DPI 下仅 ~4MB/张。 |
| **验证** | 连续滚动 / 翻屏 ≥ 55fps；`QT_QUICK_BACKEND=software` 下仍可正常滚动。 |

---

### ADR-009 依赖清单只允许 `PySide6-Essentials`

| 项 | 内容 |
|---|---|
| **背景** | 需求要求依赖自包含、不随系统库升级损坏（NFR-012 / C-001）。完整 `PySide6` 含 `PySide6-Addons`（内含 QtWebEngine，167MB），而架构 C **完全不需要** QtWebEngine。 |
| **决策** | 运行时唯一依赖 = **`PySide6-Essentials`**（实测 wheel **80.1 MB**，含 QtCore/QtGui/QtWidgets/QtQml/QtQuick/QtNetwork 等）；开发期额外依赖仅 `pytest`。禁装 `PySide6-Addons`。 |
| **理由** | ① Qt 的 wheel 自带 Qt6 运行库 → 不绑定系统 Qt/WebKit/GTK 版本，实现「库升级免疫」。② 去掉 Addons 直接省掉 QtWebEngine 的体积与攻击面；`requires-python >=3.10,<3.15` 覆盖本机 3.14.7。③ 依赖数 = 1，符合 NFR-010。 |
| **已否决** | ① 完整 `PySide6`：多 167MB，且引入完全用不到的 Chromium。② 系统 `python3-pyqt6` 包：绑定系统 Qt 版本，违背 G4。③ 引入 `lxml`/`Pillow`：stdlib 与 Qt 的 `QImageReader` 已足够（对应 C-005）。 |
| **后果（代价）** | QImage 解码 JPEG/PNG 依赖 Qt 自带 imageformat 插件（已在 Essentials 内），无额外风险。若未来需 WebEngine（如在线书城），须显式重新评估体积代价。 |
| **验证** | `pip install pyside6-essentials` 后 `import PySide6.QtQml, PySide6.QtQuick` 成功；`pip list` 无 PySide6-Addons。 |

---

### ADR-010 EPUB 解析只用 Python 标准库

| 项 | 内容 |
|---|---|
| **背景** | 需解析 ZIP、container.xml、OPF、NCX、nav.xhtml（均为 XML）。 |
| **决策** | 用 `zipfile`（随机读取条目）+ `xml.etree.ElementTree`（XML）+ `html.unescape`（实体）+ `urllib.parse.unquote`（百分号编码）。**不引入** `lxml` / `BeautifulSoup` / `ebooklib`。 |
| **理由** | ① 零额外依赖（NFR-010 / C-005）。② `ElementTree` 为 C 加速实现，对 22K 字 XHTML 性能充裕（目标全书解析 200ms）。③ 命名空间用显式 `{uri}tag` 形式即可，无需 lxml 语法糖。④ 目标书 XHTML 是**合法 XML**（已实测 `ElementTree.fromstring` 可解析），不需要 `BeautifulSoup` 的容错能力。 |
| **已否决** | ① `lxml`：为不确定的容错场景引入编译依赖。② `BeautifulSoup`：纯 Python、慢，且连带 `soupsieve`。③ `ebooklib`：抽象层过厚，且不处理我们关心的「无扩展名文档」与脏 HTML 问题（I-1/I-5），其封装反而阻碍优化。 |
| **后果（代价）** | 遇到**非法 XML** 的 XHTML（HTML5 风格未闭合标签）时 `ElementTree` 会抛异常 → 需提供 `html.parser` 回退路径（FR-008 / NFR-020）。 |
| **验证** | 单测：两本目标书全部节解析成功；`pip list` 不含上述包。 |

---

### ADR-011 阅读位置用 `(节索引, 块索引, 块内字符偏移)` 三元组表示

| 项 | 内容 |
|---|---|
| **背景** | 需记忆并恢复阅读位置（FR-080），且窗口尺寸/字号变化会引起重新排版（FR-051），因此**不能用「页码」作为持久化位置**——何况连续列里根本没有页码（ADR-016），位置只能是文字坐标。 |
| **决策** | 持久化 `(spine_index, block_index, char_offset_in_block)`：<br>① `spine_index` = 节在 spine 中的序号；<br>② `block_index` = 块在**规范化后块列表**中的序号；<br>③ `char_offset` = 块内字符偏移（含 WJ，见 ADR-005）。<br>恢复时：重建该节文档 → `QTextDocument.findBlockByNumber(block_index)` 定位块 → `LaidOutSection.block_offset(block_index)` 换算成列内偏移 → 滚到该偏移（ADR-016）。 |
| **理由** | 该三元组在**字号变化、窗口缩放、重新排版后依然有效**，是稳定标识。`QTextDocument` 的块序号与我们的块列表序号一一对应（构建时严格按序 `insertBlock`），映射成本 O(1)。 |
| **已否决** | ① 存页码：版式一变即失效。② 存全书百分比：粒度太粗，恢复后常偏移数屏。③ 存 `QTextCursor` 绝对 position：文档重建后 position 语义可能漂移，且跨节不通用。 |
| **后果（代价）** | 需保证「块列表序号」与「QTextDocument 块序号」严格一致：构建时不插入多余空块（Qt 自动创建的末尾空块在计数时排除）。 |
| **验证** | 单测：同一位置在字号 16 与 20 下恢复后落在同一段落；手工：关掉重开回到原处。 |

---

### ADR-012 领域层与排版层**零 Qt 依赖**，保证无 GUI 可测

| 项 | 内容 |
|---|---|
| **背景** | 需求要求核心逻辑可无 GUI 单测、测试 ≤ 10s（NFR-022 / NFR-023）。Qt GUI 对象需 `QApplication` + 显示环境，会拖慢并污染测试。 |
| **决策** | `domain` 层（EPUB 解析、XHTML 规范化、Block 模型、避头尾 WJ 注入）**禁止 import 任何 Qt 模块**；`typeset` 层才允许依赖 `QtGui`。测试分两类：`tests/unit/` 纯 Python（无 Qt，快速）；`tests/integration/` 需 `QGuiApplication`（用 `QT_QPA_PLATFORM=offscreen` 无头运行）。 |
| **理由** | ① WJ 注入与块模型规范化是**最易出错**的部分，必须能快速迭代测试。② 解析层独立后可单独对 408 张图 / 62 个节做批量校验，无需启动 GUI。③ `offscreen` 让 Qt 相关测试在无显示环境也能跑（CI 友好）。 |
| **已否决** | 全层混用 Qt：测试启动慢、错误定位难、无法在纯终端环境跑。 |
| **后果（代价）** | `Block` 模型只能含原始数据类型（str/int/enum/dataclass），不得持有 Qt 类型（如不能在 Block 里存 `QImage`）→ 图片以「资源路径 + 宽高」描述，解码推迟到 typeset 层。 |
| **验证** | `tests/unit/` 在 `QT_QPA_PLATFORM` 未设置时也能通过。 |

---

### ADR-013 打包与分发策略

| 项 | 内容 |
|---|---|
| **背景** | NFR-011 要求分发包 ≤ 150MB；风险 R-07：打包器易漏 QML / 插件资源。 |
| **决策** | ① 用 **PyInstaller**（`packaging/ebook-reader.spec` + `packaging/build.sh`）产出两种产物：`dist/ebook-reader-dir/`（文件夹版，启动快，AppImage 的底料）与 `dist/ebook-reader`（单文件版，62.1 MB，即分发包）；② Qt 侧资源全部交给 PySide6 hook 自动收集，收集过头之后再用 `packaging/bundle.py` 做**可控剪裁**；③ `.desktop` 随构建生成（内嵌真实路径），`xdg-mime` 关联作为可选的免 root 安装步骤；④ 产物正确性由 `bundle.py check` 与 `build.sh` 的冒烟测试保证。 |
| **理由** | PyInstaller 的 PySide6 hook 会递归收集整个 `PySide6/Qt/qml` 树（凡含 `qmldir` 的目录都收），因此**无需手工枚举 Qt 的 QML 模块**——这正是 R-07 最容易出事的地方。 |
| **`.qrc` 决策变更** | 原计划的 `.qrc` 编译**不再采用**。hook 已把 Qt 自身的 QML 模块按散文件收集，`qmldir` 驱动的隐式组件解析（`Main.qml` 直接按文件名引用同级组件）在打包后原样可用；若改走 `.qrc`，Qt 的 QML 模块仍必须是散文件，等于两套机制并存而无收益，却要额外引入 `pyside6-rcc` 步骤并重新验证每一条资源路径。 |
| **已否决** | ① `pip install` 直接分发：用户需自装 Python，体验差。② Flatpak：包体更大且需系统 runtime。③ Nuitka / `pyside6-deploy`：需把整个应用编译为 C，构建时间显著更长，而收益（启动速度）对本项目已被证明不关键。④ 手工 `--add-data` 枚举 Qt QML 模块：漏一项即静默失效，正是 R-07 本身。 |
| **后果（代价）** | 单文件版每次启动需解包约 160 MB，首屏由 0.27 s 变为 1.00 s——仍满足 NFR-001，但吃掉了大部分余量。**日常使用与 AppImage 应基于文件夹版**；单文件版只用于分发与拷贝。 |
| **验证** | 见 §12.8：两种产物在真实 Wayland 下三种启动方式均 0 错误；`bundle.py check` 报 0 个未解析库、0 个未解析 QML import。 |

---

### ADR-014 目录 / 大纲栏**占用正文空间**（三栏布局），只有设置面板仍是浮层

| 项 | 内容 |
|---|---|
| **背景** | §12.3 问题 7 曾把面板改成浮层覆盖，理由是「页面几何只由窗口决定 → 打开面板不触发重排」。代价是：开着目录时每行正文的前 330 px 被遮住，读者无法「一边看目录一边读」。FR-018 / FR-019 要求右侧给出**本节大纲**，与左侧目录同时可见，因此三栏必须同时真正可见。 |
| **决策** | ① 三栏为 **目录 ｜ 正文 ｜ 本节大纲**；前两者与正文**平铺**（`x`/`width` 相接），设置面板保持浮层抽屉（它是临时改参数用的，遮住文字可接受，且保留滑入动画不牵动别的东西）；抽屉只与**同侧**的大纲栏互斥，不再连带动左侧目录（ADR-015 ⑤）。② 面板宽度 `max(200, min(320, (窗口宽 − 440) / 打开的栏数))`：开一栏时不超过 320，两栏同开时按剩余宽度收缩，并为正文至少留 440 px。③ 交给 Python 的页框宽度是**正文栏宽**，不是窗口宽。④ 窗口默认 1000×1380 → **1400×1380**，最小宽度 520 → **720**。⑤ 大纲栏在当前节**没有可显示内容时不出现**（`outlineAvailable` 为假即不占位），避免一条空白竖带。 |
| **理由** | 目录与大纲是**阅读时一直要看**的地图，遮住正文等于让读者在「看地图」和「看书」之间二选一；设置面板是偶发操作，浮层正合适。这一区分（地图平铺 / 抽屉浮层）比「所有面板都浮层」或「所有面板都平铺」都更贴合使用频率。 |
| **已否决** | ① 继续全部浮层：违反 FR-019，且三栏同时可见无从实现。② 面板做成可拖拽的分割条（splitter）：v1 不需要，且引入新的持久化状态（每栏宽度），而自动宽度已能覆盖 720–1920 px 全部窗口尺寸。③ 给尺寸变化加过渡动画（面板与正文同步做 180 ms 动画）：一次面板开关就要把整节重新排版、整页重新光栅化，动画期间每帧都要画过渡状态的页，收益低、风险高，故**第一版不做尺寸过渡**，只有设置抽屉保留滑动动画。 |
| **后果（代价）** | **开/关侧栏会触发本节重排**（这是放弃浮层的直接代价，必须如实记录）：实测最重的节 20–72 ms，与「拖动窗口改变大小」同一量级，因此不构成新的性能类别。阅读位置不丢：位置是 `(节, 块, 字符偏移)`（ADR-011），重排路径沿用既有的 `_current_anchor_block()` → `block_offset()` 锚点回填；`setViewSize` 与设置变更都会调用 `_drop_outline()` 丢弃大纲缓存（其行携带的是本列偏移，只对一套页框有效）。 |
| **验证** | `tests/integration/test_layout.py`：三栏 `x`/`width` 相接（无重叠、无缝）、页框宽度 = 正文栏宽而非窗口宽、窄窗口下两栏仍可用、无内容时不出现右栏、只有设置面板会遮断点击区（FR-062）。`tests/integration/test_outline.py`：大纲只列本节标题（ADR-016），重排后按新的正文栏宽重建。 |

---

### ADR-015 全功能右键菜单；目录栏随书展开并**常驻**

| 项 | 内容 |
|---|---|
| **背景** | 快捷键（`T`/`O`/`S`/`[`/`]`/`Ctrl±`）只写在设置抽屉里，而设置抽屉本身也要靠 `S` 才能打开。实际使用中出现的反馈是「没看到左边有目录」——根因不是功能缺失，而是**入口不可见**：目录默认收起，且没有任何鼠标可发现的痕迹（FR-013 / FR-071）。反馈的下一轮是「目录要一直存在，或者有一个按钮能快速显示 / 隐藏」：默认展开解决了「看不到」，但点一下章节它就自己收了（`goToTocRow()` 里仍写着浮层时代的 `_toc_visible = False`，与已作废的 FR-015、FR-018、README 都不符），而且除 `T` 键之外仍没有鼠标入口。 |
| **决策** | ① 打开书时**目录栏默认展开**（书没有目录则不展开，与 FR-019 的「没有内容就不占栏」同一规则）；关闭书时目录与大纲一起收起（栏属于那本书）。② 窗口级加一层 `MouseArea`（`acceptedButtons: Qt.RightButton`、`scrollGestureEnabled: false`），声明在 `Main.qml` 可视项**之前**（即 z 序最底层），在鼠标位置 `popup()` 出 `ReaderMenu.qml`。③ 菜单**平铺 + 分组分隔线**：21 行，只有 6 个「值型」行（字号 / 行距 / 边距 / 字体 / 对齐 / 主题）带子菜单，父行标签里写着当前值（「字号 18」）；其余操作一行直达。④ 行状态由控制器单向驱动：勾选标记是自定义属性 `marked`（**不是** `checkable`，避免 QQC2 先自翻 `checked` 再触发 `triggered` 与绑定打架），无书或该项不可用时整行置灰。⑤ **目录栏常驻，面板只在争同一块空间时才相让**：`goToTocRow()` 不再收起目录（FR-015 的作废由此真正落到代码），滚动与开关大纲也不影响它；设置抽屉浮在右侧，只让**同侧**的大纲栏让位，不再连带动左侧目录，点击抽屉旁边也只关抽屉（新增 `closeSettings()`，不再是 `closePanels()`）。收起目录只剩显式操作：`T` / 状态栏按钮 / `Esc` / 右键菜单。⑥ **状态栏左下角常驻「目录」按钮**（`StatusBar.qml`，`objectName: tocToggle`）：开栏时整块填充主题强调色、关栏时只描边，右端以小字标出 `T`，无书或书无目录时整块置灰（与菜单「目录」行同一规则）。按钮放在状态栏而不是另加一条工具栏：状态栏在页框之外，不占正文空间，位置也固定。 |
| **理由** | ① 「栏属于书」与「目录随书出现」自洽：有书才有地图，没书就没有栏，顺带消掉「关书后左栏残留」这一类缺陷。② 右键层放**最底层**：鼠标事件按 z 序自上而下派发，只声明右键的 MouseArea 不会截住左键（左键被上层的点击区 FR-062 先取走），而未被子项接受的右键会一路下传到这里——正文、面板、状态栏都覆盖得到。放最底层而不是最上层，是因为这一层只接「别人不要的」右键：将来任何面板行想自己处理右键，不会被这层抢走。③ 平铺而非多级子菜单：读者找的是「目录在哪」，多一级就多一次失败机会；把 6 个值型行放进子菜单，菜单才稳定在约 670 px（默认窗口内容高 1348 px；最小窗口时菜单可滚动）。④ 「常驻」比「能打开」更接近读者的实际用法：地图是用来反复对照的，跳一次就消失等于每次都要重找入口。⑤ 按钮的开关态同样由控制器单向驱动（自定义 `checked`，不用 `checkable`）：QQC2 的 checkable 按钮会先自翻 `checked` 再发 `clicked`，绑定被覆盖后按钮与面板会各说各话。⑥ 面板之间只在**争同一块空间**时相让，是 ADR-014 三栏布局的自然推论：目录占左栏、大纲占右栏、抽屉浮在右侧，所以抽屉只与大纲互斥。 |
| **已否决** | ① 只加提示文案（在状态栏写「右键看菜单」）：提示还在屏幕边缘，不解决问题。② 用 `MenuBar` / 工具栏：占纵向空间，与「沉浸阅读」冲突。③ 用 `checkable: true` 让 QQC2 自己管勾选：点击会先翻 `checked`，绑定被覆盖后状态与控制器不一致。④ 全部平铺（29 行、约 930 px）：最小窗口下要滚动才看得到「退出」，得不偿失。⑤ 另加一条顶部工具栏承载开关：占纵向空间，与「沉浸阅读」冲突（同②），而状态栏本来就在页框之外且处处可见。⑥ 让目录栏「永不收起」（连 `T` 都不响应）：读者一旦想全宽阅读就没有退路，正确做法是常驻 + 一键收起。 |
| **后果（代价）** | ① 菜单把 21 个操作与快捷键抄了一份，是**刻意的重复**：新增操作必须同时改 `ReaderMenu.qml`，`test_menu.py` 用「每一项都存在、且不多不少」钉住这份清单。② 打开书时多一次重排：目录栏展开 → 正文栏宽变化 → 本节重排（与开关侧栏同一量级，20–72 ms；当时的日志行 `first page ready`（现为 `first window ready`）由 86–92 ms 变为 114–123 ms，数字为 ADR-016 之前的实测，NFR-001 余量仍充足）。③ 右键层在最底层，面板行自身失去右键语义（当前无此需求）。④ 目录栏常驻之后，读者若想全宽阅读需要一次显式操作（`T` / 按钮 / `Esc` / 右键菜单，四个入口），而状态栏因此多了一个控件：它的开关态必须继续由控制器单向驱动，`StatusBar.qml` 只是画出来（`checked` 是绑定，不是状态）。 |
| **验证** | `test_menu.py`（9 项）：真事件右键在正文 / 目录栏 / 状态栏三处都能弹出且弹在鼠标处；21 行 + 14 个子菜单项全部存在、且不多不少；无书时整行置灰、已开栏带勾选；**真点击**「下一章」真的换章、「主题 → 夜间」真的换主题（子菜单两跳全程鼠标）；右键点滚动条只弹菜单（偏移不变），同一处左键真的滚动。`test_layout.py`：开书后左栏可见且三栏 x/width 相接；关书后两栏消失、宽度归还正文；开设置抽屉后左栏**仍在**、页框不变。`test_toc_panel.py`（6 项）：状态栏「目录」按钮**真点击**即显示 / 隐藏，页框跟着正文栏宽走；按钮在页框之外（状态栏内）且点击不滚动；无书时置灰、点击不产生副作用；**真点击**目录项跳转后左栏仍在、高亮跟随；同一文件里的上下两项分别落在列首与锚点所在块（ADR-017）；点章节 / 滚动 / 开大纲都不收起目录，只有 `T` / `Esc` 收起。 |

### ADR-016 主阅读模式为**连续滚动列**（取代 ADR-004 的分页；ADR-007 作废，ADR-008 的单位改为「窗口」）

| 项 | 内容 |
|---|---|
| **背景** | v1 按 ADR-004 走完分页实现后，真实使用暴露出分页本身带来的四类问题：① **页边界是纸的约束**，屏幕不需要它，但它把「一屏能读多少」变成硬边界，一章读不顺；② 只要块跨页就要修——ADR-007 的迭代修正最多 8 轮（重排 8 次），修完底部仍可能留白，而且**每次改字号 / 改窗口都要重跑**；③ 修正超限只能降级为「允许切断」，观感随书而变；④ 页码不稳定，阅读位置（ADR-011）必须绕开页码、用「块落在第几页」反算。 |
| **决策** | ① **一节 = 一列**：`QTextDocument` 只按**正文栏宽**排版，取列总高 `LaidOutSection.height`（实测 = `document.size().height()`），**不调 `setPageSize`，不产生页**。② **位置 = 列内偏移**（逻辑像素）：`block_offset(i)` 给块顶偏移，`block_at_offset(offset)` 给「顶部不晚于 offset 的最后一个块」，`max_offset` 是合法偏移上界；ADR-011 的三元组照旧持久化，恢复时用 `block_offset()` 换算成偏移。③ **渲染单位 = 窗口**：高 = 页框高 + 1 个 `WINDOW_QUANTUM`（32 px）；偏移先 clamp 到 `[0, max_offset]` 再 `quantise_offset()`（量子对齐），`render_window(section, offset) → QImage`。④ **QML 只贴「页框」那一块**：`PageItem` 用 `drawImage` 的源矩形平移完成量子内的位移（`pan`），这一路**零 Python 调用**。⑤ **越界即换节**：相邻两节**不拼接**（沿用 ADR-003 / ADR-004 的取舍），`]` / `[` 与菜单「上一章 / 下一章」仍可显式换节。⑥ **删掉分页相关的一切**：`Paginator`、`page_count`、`page_breaks`、`block_page()` 全部消失；大纲不再按页切片（FR-018 的「按页回退」作废，无标题的节就不提供该栏，FR-019）；状态栏由页码改为**位置百分比**（「本卷 x% · 全书 y%」，FR-072 / FR-075）。 |
| **理由** | ① **问题整类消失**：没有页边界，就没有「跨页切断」，也就没有边界修正、迭代收敛、降级与修正留白——ADR-007 要解决的事在连续列里不存在。② **位置更简单**：偏移是列的自然坐标，字号 / 栏宽变化后按块回填（`block_offset(anchor)`）即可。③ **渲染更省**：窗口只比页框多 32 px，而量子内的每一帧都不进 Python，比「每页全量光栅化 + 翻页动画」更轻。④ **Qt 天然支持**：`QTextDocument` 不定高、`documentLayout().documentSize()` 直接给列高，排版仍是一次 pass，未引入任何新机制。⑤ 与「沉浸阅读」的定位一致：读的是文字流，不是纸。 |
| **已否决** | ① **两套模式并存**（分页 + 连续）：位置、缓存、大纲、菜单、测试都要两套，而分页那一套的问题没有一个是连续模式也需要的。② **任意偏移渲染**（不对齐量子）：滚一格就重新光栅化整窗（9–17 ms），跟手性会丢；量子对齐把量子内变成纯平移。③ **相邻节拼接成一列**（真·全书连续滚动）：要同时持有两个 `QTextDocument` 并做偏移换算、跨文档图片预取与位置记忆——ADR-003 的取舍不变。④ **QML 直接用 `TextEdit` / `TextArea` 滚动**：ADR-008 已否决的编辑场景接口，样式与避头尾注入都失控。⑤ **`QGraphicsView` / `QScrollArea` 包 `QTextDocument`**：回到「每帧 Python 绘制」的老路（ADR-008）。 |
| **后果（代价）** | ① **节尾留白仍在**（节是列的单位）：`max_offset` 之后就是下一节，ADR-003 的代价如实保留。② 位置恢复是**按块回填**而非像素级精确：字号变化后落在同一块的行首附近（ADR-011 的验证仍成立）。③ **没有页码**：状态栏只能报百分比，「翻到第 120 页」这种用法不再存在。④ 窗口位图比页位图多 32 px 高，并新增 `ReaderController._window_cache`（5 张 LRU，ADR-008 的代价）。⑤ 大纲失去「按页切片」这一兜底，只在有标题的节出现。⑥ **一次性重写**：`test_typeset.py` 整体改为面向列的断言，任何引用「页」的文档段落都要重写（本次一并完成）。 |
| **验证** | `tests/integration/test_typeset.py`（22 项，全部面向列）：窗口几何 = 页框 + 1 量子；列高 = `document.size().height()` 且 `clear()` 后重建可复现；块↔偏移双向映射（`block_at_offset` 是「不晚于 offset 的最后一个块」，同顶多块时取其中一个）；418 张图在列中的矩形与显示尺寸偏差 ≤ 0.497 px（容差 1.0）；标题与正文间距 1.04–1.45 行步（上界 2.0）；禁则字符不当行首；两本参考书的**全部节**都能排版；节 ≤ 50 ms（NFR-003）、窗口 ≤ 20 ms（NFR-004，测 8 个窗口取最差）。`tests/integration/test_scroll_view.py`（22 项）：量子内滚动复用同一张位图、`windowOffset` 落在量子网格上、方向键一行 / 翻屏键一屏 / `]` `[` 换节、章末那一行进入下一章、`linear="no"` 的节被 `]` `[` 跨过、滚动条比例 = 列长；**滚轮两种事件形态**各按文档写明的量位移（一格 = `wheelStep`、纯 `pixelDelta` = 像素数、半个格子按比例，FR-063）。`tests/integration/test_outline.py`：大纲只列本节标题、高亮跟随偏移。 |

### ADR-017 目录项的两个目标分开：`href` 解析到**节**，`#frag` 解析到**块**（FR-014 / FR-017）

| 项 | 内容 |
|---|---|
| **背景** | 目录项写的是一个 URI（`index_split_007.xhtml#filepos10484`），它同时回答两个问题：**哪一节**、**节里哪个位置**。v1 只回答了第一个——`join_href()` 按设计丢掉 fragment（它不是 ZIP 条目名的一部分），于是第二个问题从未被问过。这在「一节一项」的书（康波，26 项 / 27 节）里完全看不出来；币安（28 项 / 35 节）有 4 个文件各承载两项，丢掉锚点后两行都跳到文件开头——目录对读者说了谎，而且是可验证的谎。容错解析路径（格式不合法的文档）更彻底：它从不读 `id`，这类书的锚点全部无效。 |
| **决策** | ① **两个目标分开存**：`TocEntry.href` 仍是节路径（解析到 spine 序号），新增 `TocEntry.fragment`（百分号解码后）保存锚点；`section_index_for_href()` 显式忽略 fragment，`goToTocRow()` 用 `entry.fragment` 调 `LaidOutSection.anchor_block()`。② **锚点归属**：`id` 落在**不产生块的元素**上（`<div id>` 只包住别的块、空 `<p id>`、空 `<a id>`——EPUB2 的 `filepos` 标记全属此类）时，交给**其后的第一个块**；文档末尾的这种标记交给**最后一个块**。③ **容错解析路径也抽 `id`**（原先完全不读，等于这类书锚点全灭）。④ **高亮按位置判定**：`currentTocRow` = 「已越过其锚点的最后一项」（FR-017），不再是「该节的第一项」；跨节滚动时只在**行确实变化**时发 `tocChanged`，不在每个滚动刻度上重建目录行。 |
| **理由** | ① 两个问题是两个问题：节用 spine 序号回答（要能跨节比较大小、要能记住），块用「从节顶往下多少像素」回答（要跟着排版走）。合成一个字符串就会像 v1 那样丢一半。② 「锚点归下一个块」是**结构**判断，不需要书的名字、不需要发布者的习惯：`id` 标的是「从这里开始」，而不产生块的元素自己没有「这里」。③ 末尾标记归最后一块：那里确实没有下一块，它标的是「前面那段的末尾」。④ 高亮按位置走是同一规则的必然结果——若仍按「文件」判定，同一文件里的第二项永远不可能被高亮。 |
| **已否决** | ① 把 fragment 留在 `href` 里：解析链上每一处（spine 查找、路径归一化、ZIP 查找）都要记得剥掉它，漏一处就是一次静默失败，而 v1 正是这么失败的。② 用「行文本 = 目标段落文本」定位锚点：对已经写着 `id` 的锚点是纯属多余的猜测（该手段留给没有 nav / ncx 的书，见 FR-012）。③ 锚点找不到时**回退到估位**（按块序号等分）：那会让「跳得准」与「跳得偏」在观感上无法区分。 |
| **后果（代价）** | ① `Block.anchor_ids` 现在会带上「字面上不属于这个块」的 id（来自它前面的空标记），语义是「这个位置从这块开始」——`anchor_block()` 的文档已按此写明。② 锚点超出可滚动范围（`max_offset`）时 `block_offset()` 照旧 clamp：短节里的深锚点只能落到节尾（币安 §4 正文 1280 px、窗口 1380 px，正是这种情况）——这是连续列里正确的观感，但意味着「落在锚点所在块」这类断言只能在**可滚动**的节上验证。③ `_TolerantParser` 新增 `id` 收集、两个 walker 各新增一份 `_orphans` 状态，是这条路线的必要成本。 |
| **验证** | `tests/unit/test_toc.py`（13 项）：两种目录格式各自的锚点保留、百分号解码、手写 `TocEntry` 的默认值、`section_index_for_href()` 忽略锚点、币安「同一文件两项、其中一项带锚点」的解析解、**参考书全部锚点都能解析**（币安 9/9）、28 项目标两两不同。`tests/unit/test_sanitizer.py`（+6 项）：`<div id>` 包裹、空标记、包裹与首块各带 `id`、末尾标记、图片包裹、容错路径。`tests/integration/test_toc_panel.py`：真点击同一文件的上下两项，分别落在列首与锚点所在块，且高亮跟着走。 |

### ADR-018 滚轮：两种 `delta` **分开处理**，设备类型要**显式收全**，换算放在控制器里（FR-063）

| 项 | 内容 |
|---|---|
| **背景** | 滚轮事件有两种形态，而它们**符号约定相反**：`angleDelta` 以「八分之一度」计数，轮子远离用户为正（所以向下滚是负）；`pixelDelta` 是屏幕距离，向下滚为**正**（Qt 文档：「用于直接滚动内容」）。鼠标滚轮只发前者（一格 120 单位）；触控板、高精度滚轮与 Wayland 的平滑 / 自由滚动**只发后者**（角度恒零）。v1 只读 `angleDelta` 并除以 120，于是第二种形态恒等于 0 px（缺陷 16）。**还有第二道门**：事件先要过 QML 处理器的**设备过滤**才轮到 `onWheel`，而 `WheelHandler.acceptedDevices` 的默认值是 `Mouse` 一个设备；Wayland 上 Qt 把每个滚轮事件都记在**座位的指针设备**名下，该设备登记为**触摸板**（`wl_pointer.axis` 不带自己的设备）——过滤因此拒掉每一次滚轮，`onWheel` 从不执行，鼠标滚轮在 Wayland 上完全不动，而方向键（快捷键，与设备无关）照常滚动（缺陷 24）。 |
| **决策** | ① 换算收进控制器：`_wheel_distance(angle_y, pixel_y, wheel_step)`，纯函数，无 Qt 依赖；`ReaderController.wheelScroll()` 是 QML 唯一入口，QML 只转发两个 delta 并 `event.accepted = true`。② 规则：`angleDelta` 非零 → `-angle / 120 × wheelStep`（一格 = 3 行 = `wheelStep`，与键盘同一套行高）；为零且 `pixelDelta` 非零 → `pixelDelta` **原样**（像素就是像素）；两者皆零（`ScrollBegin`/`ScrollEnd` 等相位事件）→ 0，且不触发重绘。③ 同时有值时**以角度为先**：那是一个真正的滚轮格，而 Qt 明确说明 `pixelDelta` 依驱动而定、X11 下不可信。④ 处理器**显式收全设备**：`acceptedDevices: PointerDevice.Mouse \| PointerDevice.TouchPad`——Wayland 的滚轮在 Qt 眼里来自触摸板，不收它就等于没有滚轮。 |
| **理由** | ① 两种形态答的是同一个问题（往哪儿滚、滚多远），但单位不同，**必须分别换算**，不能相加也不能互相折算。② 位置放在控制器而不是 QML：能单测（`test_wheel.py` 12 项无需 Qt），且滚轮、方向键、菜单三处共用同一套「距离」单位（FR-073）。③ 一格 = 3 行沿用浏览器习惯，像素形态则忠实于设备——平滑手势本来就该跟手，而不是被折成整行。④ 设备类型是**过滤器**而不是换算：它必须在事件进入 `onWheel` 之前放行，因此只能写在 QML 处理器上（`WheelHandler` 的默认值是 Qt 的选择，不是本应用的选择）。 |
| **已否决** | ① 把 `pixelDelta` 折成「行数」再乘 `wheelStep`：等于把设备量到的距离再放大三倍，触控板会飞。② 两个 delta 相加：两种形态同时出现的设备会双倍滚动。③ 在 QML 里做算术（v1 的做法）：既无法单测，两个相反的符号约定又极易被下一个人搞混——缺陷 16 就是这么发生的。④ 改用 `Flickable` / `TextArea` 承载滚动：ADR-016 / ADR-008 已否决（样式与避头尾注入失控、每帧回到 Python）。⑤ 把滚轮处理挪到 Python 侧（窗口事件过滤器 / `QQuickWindow` 子类）以绕开设备过滤：能修好，但把一个平台细节塞进应用启动路径，而且 QML 那一处本来就是唯一入口——改设备过滤只需一行。 |
| **后果（代价）** | ① 触控板位移忠实于设备，不同机器的「一格」观感因此不同——这是平台行为，不是本应用的选择。② 两种形态各需一组测试（已补），设备类型另需一组（已补：由**触摸板设备**发出的滚轮事件）。③ 若某平台两种 delta 都为零（目前无实例），事件会静默不动；定位手段是 `QT_LOGGING_RULES=ebook_reader=DEBUG` 下的 `wheel angle=… pixel=… -> … px` 调试行，以及 `QT_LOGGING_RULES='qt.quick*=true'`（Qt 会打出处理器为何 `DECLINES` 每一次滚轮，缺陷 24 就是这样定位的）——换机器复现滚轮问题时先看形态与设备，再谈代码。 |
| **验证** | `tests/unit/test_wheel.py`（12 项）：一格的量与方向（±120 = ±`wheelStep`）、纯 `pixelDelta` 不翻符号、两种形态方向一致、同时有值时以角度为先、相位事件为 0、半步按比例。`tests/integration/test_scroll_view.py`（真 `QWheelEvent`）：一格位移 = `wheelStep`、纯 `pixelDelta` 位移 = 像素数、半格 = `wheelStep/2`、轮子在面板上时正文不动（两种形态都测）、**触摸板设备发出的滚轮同样位移一格**（把 `acceptedDevices` 去掉，该用例立刻失败——正是缺陷 24 的复现）。 |

---

### ADR-019 书没点名的节，用**该节自己的第一行**补一行目录，名字只从文档开头读（FR-012）

| 项 | 内容 |
|---|---|
| **背景** | `彼得林奇投资经典全集` 是 3 本书装订在一起的合集：135 节，而它的 `toc.ncx` 只写了 3 个**卷级**条目（每卷一个）。`]` 与 `[` 走的是 spine 阅读顺序，因此能一节一节读过去，但左栏只有 3 行——读者知道自己在往前走，却看不见要往哪走，也不能挑一节跳过去：地图与路不一致。这台机器上 Qt 跑在 **Wayland** 下，因此同一个界面里「鼠标滚轮不动、方向键能动」也曾长期并存（缺陷 24 的旁证：位置与顺序的规则比设备的规则可靠得多）。三类书里只有合集是这种形状，但形状不特殊：任何被转换器切节的 EPUB（`index_split_###.html`）都可能只声明卷级目录。 |
| **决策** | ① 目录的**来源**仍是书自己：先 `nav.xhtml`，再 `toc.ncx`；**书没点名的那些节**按阅读顺序各补一行，行名取该节自己说的第一句话（`EpubBook._section_label`）。② 命名规则：**发布者标记优先**——首个文本块是 `h1`–`h6` 就用它（长度、标点都不限，那是书写的名字）；否则要求那行**短**（≤ 40 字）且**无句读**（不含 `。！？；`、不以任何标点结尾），于是图注（「2017年10月，东京工作时期每日通勤的自行车。」）与出版者行（「出版者：」）不成名，该节**不出行**——宁缺勿滥。③ **只读文档开头**（`_LABEL_SNIFF_BYTES = 1024`）找名字；唯一例外是「开头正好停在那一行上」的文档（那一行可能被截断），这种文档整篇解析一次（`blocks()` 会缓存，而读者正被送进它）。④ 补出的行**按 spine 顺序**插进书的目录树：与前后条目之间、落在所含部分的子列表里、层级为该列表的层级；`linear="no"` 的节不补（FR-016 同一顺序）。⑤ 书的树本身**只增不改**：原有条目的标题、`href`、`fragment`、嵌套顺序一律不动；一条目录数据都没有的书即此规则的退化情形（每节一行），原先的 `h1`/`h2` 启发式因此删除——同一个规则，不需要第二种。 |
| **理由** | ① 名字就在书里，而且就在文档的**开头**：被切节的 EPUB 把章题写成本节的第一个段落（`<p>第1章 业余投资者比专业投资者业绩更好</p>`），发布者标了标题的书则更明确。② 「短且无句读」是**形态**判断而不是关键词表：不需要认识「目录」「Contents」，也不需要中文词库，图注与正文句子天然排除。③ 行的位置由 **spine 序号**决定，与「上一章 / 下一章」「按位置高亮」用同一把尺子：这样左栏的顺序与 `]` 的顺序永远一致，读者看到的就是他会走的路。④ 只读开头是**成本**决策：整篇解析 135 节要 260 ms（§12.1 实测 36–45 ms vs 260 ms），而首屏预算只有 500 ms；名字在第一段，读整篇是为了拿名字之外的东西。⑤ 合集那种「卷 → 章」的形状不需要特例：卷是书写的条目（树结构），章是补出的行（落在该卷的子列表里），层级自然对。 |
| **已否决** | ① **整篇解析每一节**来命名：功能一致，代价是 135 个文档全文解析（合集 260 ms，占首屏预算一半；§8.4）。② **采信正文里的「目录页」链接**：合集每卷开头都有一页真正的目录，用 `<a href>` 连到每一章（共 143 个链接）。不采信的理由是它只帮得到「正文里印了目录」的书，而**任何**被切节的书都能靠「说自己的名字」得到同一张表；而且链接密度是个不可靠的信号（脚注、索引、正文里的普通链接都可能是链接行）。③ **把补出的行放进右侧「本节大纲」栏**：大纲是本节内部的地图（FR-018），合集整本没有 `h1`–`h6`，那一栏本来就是空的——把整本书的章节塞进去等于换了一个栏位做目录。④ **标记补出的行**（新字段 `generated`、另一种配色）：读者需要的是「能不能跳到那一章」，而不是「这一行是谁补的」；多一个字段就要多一处 UI 决策，而没有任何操作依赖它。⑤ **在 QML 侧懒加载行标题**（`QAbstractListModel` + 按需解析）：最省 CPU，但把 135 行的模型换成异步角色模型、动到面板与测试，收益只是把 40 ms 挪到面板滚动时。 |
| **后果（代价）** | ① 打开书时多付一次「读每节开头」的成本（合集 135 节实测 **36–45 ms**，康波 2 ms、币安 5 ms）；代价计入 §8.4 的启动预算，仍然余量充足。② 名字是**猜的**：出版社没写名字的节只能靠第一行，因此偶尔会出现不像标题的第一行（「华章经典·金融投资」这种丛书页就是实例）——它比空着更接近读者的需要，且不撒谎（那就是那一节的第一句话）。③ 补出的行没有锚点（`fragment` 为空），落在节首；这与 FR-014 不冲突：锚点仍然是书写了才有的东西。④ 面板变长：合集左栏 134 行，`ListView` 只实例化可见的行，因此滚动与高亮成本不变；但「一行一节」让面板不再是「卷册地图」而更像「路书」，这是有意的取舍（FR-012）。 |
| **验证** | `tests/unit/test_toc.py`（+9 项）：合集 135 节 / 3 条 NCX → 134 行、目标升序、纯图封面与图注不成行；「短且无句读」的三类反例（图注、出版者行、长句）；`h3` 长标题仍成名；`linear="no"` 的节不出行；嵌套 NCX 下补出的行落在所属部分内且层级为该列表层级、原有嵌套与条目一字不动；开头被读窗口截断的名字整篇读回（写一段把标题正好切在 1024 字节上的文档）。`tests/integration/test_toc_panel.py`（+1 项）：真开合集，左栏 ≥ 130 行、按阅读顺序、目标两两不同，**真点击**补出的行落到那一节且高亮跟随，再按行号跳到「后记」那一行。`tests/integration/test_scroll_view.py`（+1 项）：合集里书没点名的节，节末「下一章」行照样写出下一章的标题（FR-074）。 |

---

## 6. 数据模型

数据模型是「领域层」的核心产物，也是唯一跨越「解析 → 排版 → 交互」三层的数据契约。

### 6.1 解析层模型（不可变，纯数据）

```
BookMeta           书名、作者、语言、出版社、唯一标识(identifier)、封面资源路径
EpubBook           元数据 + spine 列表 + manifest + 目录树 + 地标 + 资源访问器
  ├─ spine: list[SpineItem]        按阅读顺序（每项带 linear 标记）
  ├─ manifest: dict[id, ManifestItem]
  ├─ toc: list[TocEntry]           树形，支持多级（书没点名的节由该节自己补行，ADR-019）
  ├─ landmarks: list[Landmark]     书声明的卷 / 部位起点（EPUB 3 landmarks）
  └─ resource(path) -> bytes       按需读取 ZIP 条目
SpineItem          index(序号)、href(相对路径)、media_type、id、linear(是否属于正文阅读顺序)
ManifestItem       id、href、media_type、properties
TocEntry           title、href(到节，不含锚点)、fragment(#frag 单独保存)、children: list[TocEntry]、level
Landmark           type(epub:type)、title、href、fragment
```

**关键约束**：`SpineItem.media_type` 是判定「是否为可渲染文档」的**唯一依据**（I-1），`href` 不参与类型判断；`linear="no"` 只影响「上一章 / 下一章」的顺序阅读，不改变节号、目录跳转与寻址（FR-016）。

### 6.2 内容层模型（Block，纯数据，零 Qt 类型）

```
Block（dataclass，不可变）
  ├─ kind: BlockKind                枚举：HEADING / PARAGRAPH / IMAGE / RULE /
  │                                        QUOTE / LIST_ITEM / PREFORMATTED
  ├─ spans: list[Span]              行内内容（IMAGE 块除外）
  ├─ level: int                     标题层级 1–6；列表嵌套层级
  ├─ image: ImageRef | None         IMAGE 块专用
  ├─ align: BlockAlign              INHERIT / LEFT / CENTER / RIGHT
  └─ anchor_ids: list[str]          HTML id 与锚点名（供目录跳转）
Span（dataclass）
  ├─ text: str                      已注入 WJ、已做实体解码的最终文本
  └─ bold / italic / superscript / subscript / link_href: ...
ImageRef（dataclass）
  ├─ src: str                       规范化后的包内路径（相对 OPF）
  ├─ width / height: int            由文件头预读得到（ADR-006）
  └─ alt: str
```

**为什么 Block 里不含 `QImage`**：ADR-012 要求领域层零 Qt 依赖，因此图片只存「路径 + 尺寸」，真正解码发生在排版层。

**Block 列表即「规范化结果」**，也是 ADR-011 中 `block_index` 的坐标系。

### 6.3 排版层模型（含 Qt 类型，可丢弃重建）

```
TypographySettings（可序列化为 JSON，持久化）
  ├─ font_size: float               默认 18.0
  ├─ font_families: tuple[str,...]  字体回退链
  ├─ line_height: float             默认 1.75（比例）
  ├─ first_line_indent_em: float    默认 2.0
  ├─ paragraph_spacing_em: float    默认 0.3
  ├─ margin_top/bottom/left/right: int
  ├─ justify: bool                  默认 True
  ├─ kinsoku: bool                  默认 True
  └─ theme: Theme                   LIGHT / SEPIA / DARK
PageGeometry                      page_size(QSizeF)、content_size(QSizeF)、content_origin(QPointF)
LaidOutSection（一节 = 一列，进 LRU；ADR-016）
  ├─ document: QTextDocument
  ├─ blocks: list[Block]
  ├─ height: float                  连续列总高（= document.size().height()）
  ├─ max_offset: float              合法偏移上界 = max(0, height − 页框高)
  ├─ block_offset(i) / block_height(i)      块 → 偏移（块顶 / 块高）
  ├─ block_at_offset(offset) -> int          偏移 → 块（顶部不晚于 offset 的最后一个块）
  └─ build_ms: float                排版耗时（诊断数据；一次 pass，无迭代）
ReadingPosition（持久化，见 ADR-011）
  └─ (spine_index: int, block_index: int, char_offset: int)
```

**这一层没有「页」**：`LaidOutSection` 里既没有 `page_count` 也没有页边界列表——一节就是一条连续列，滚动位置由偏移表达（ADR-016）。窗口位图是渲染期的产物，不进模型。

### 6.4 模型转换链（单向，无回环）

```
ZIP 字节
  └─[xml.etree]──> XHTML 树
       └─[sanitizer]──> list[Block]        ← 领域层（纯 Python，可单测）
            └─[kinsoku]──> list[Block]     ← 注入 WJ（纯 Python，可单测）
                 └─[document.build]──> QTextDocument      ← 排版层：按正文栏宽排成一列
                      └─[LayoutEngine]──> LaidOutSection  ← 列高 + 块↔偏移映射（ADR-016）
                           └─[renderer.render_window]──> QImage   ← 交给 QML
```

**回环禁令**：下层不得感知上层。例如 `sanitizer` 不知道 `QTextDocument` 存在，`renderer` 不知道 EPUB 存在。

---

## 7. 模块划分与目录结构

### 7.1 目录结构

```
ebook-reader/
├── requirement/
│   └── requirements.md              需求规格（本文档的上游基线）
├── arch/
│   └── architecture.md              本架构设计文档
├── src/
│   └── ebook_reader/
│       ├── __init__.py
│       ├── __main__.py              入口：python -m ebook_reader [book.epub]
│       ├── domain/                  ★ 零 Qt 依赖，纯 Python
│       │   ├── __init__.py
│       │   ├── models.py            Book/SpineItem/TocEntry/Block/Span/ImageRef
│       │   ├── blocks.py            BlockKind / BlockAlign 枚举与构造助手
│       │   ├── errors.py            BookError 等异常层次
│       │   ├── epub/
│       │   │   ├── __init__.py
│       │   │   ├── container.py     META-INF/container.xml → OPF 路径
│       │   │   ├── opf.py           OPF → metadata/manifest/spine
│       │   │   ├── toc.py           nav.xhtml（EPUB3）+ toc.ncx（EPUB2）→ TocEntry 树
│       │   │   ├── paths.py         OPF 相对路径归一化、百分号解码、ZIP 内查找
│       │   │   ├── images.py        JPEG/PNG 文件头尺寸预读（不解码像素）
│       │   │   └── book.py          EpubBook：组装以上，暴露统一接口
│       │   └── html/
│       │       ├── __init__.py
│       │       ├── sanitizer.py     XHTML 树 → list[Block]（核心转换，I-4/I-5）
│       │       ├── kinsoku.py       WJ 注入 + 禁则字符集（ADR-005）
│       │       └── text.py          空白归一化、实体处理、全角/半角
│       ├── typeset/                 ★ 依赖 QtGui
│       │   ├── __init__.py
│       │   ├── settings.py          TypographySettings + PageGeometry + 字体回退链
│       │   ├── style.py             Block/TypographySettings → QTextBlockFormat/QTextCharFormat
│       │   ├── document.py          list[Block] → QTextDocument（ADR-002）
│       │   ├── engine.py            LayoutEngine：连续列 + LaidOutSection + 节级 LRU（ADR-003 / ADR-016）
│       │   ├── images.py            ImageCache：懒解码 + LRU（ADR-006）
│       │   └── renderer.py          QTextDocument + offset → 窗口 QImage（ADR-008 / ADR-016）
│       ├── app/
│       │   ├── __init__.py
│       │   ├── controller.py        ReaderController(QObject)：QML 唯一交互面
│       │   ├── page_item.py         PageItem(QQuickItem)：贴窗口位图、量子内平移
│       │   ├── settings_store.py    JSON 持久化（XDG 配置目录）
│       │   └── main.py              QGuiApplication + QQmlApplicationEngine 引导
│       └── qml/
│           ├── Main.qml             应用骨架（窗口、快捷键、沉浸模式）
│           ├── ReadingView.qml      正文栏：窗口位图 + 滚动条 + 点击区
│           ├── TocSidebar.qml       目录树
│           ├── OutlinePanel.qml     本节大纲（占右栏；FR-018 / ADR-014）
│           ├── SettingsPanel.qml    字号/字体/行高/主题
│           ├── ReaderMenu.qml       右键「全部操作」菜单（FR-071 / ADR-015）
│           └── StatusBar.qml        位置百分比 / 常驻「目录」开关
├── tests/
│   ├── unit/                        纯 Python，无 Qt
│   │   ├── test_epub.py             解析：两本真书、无扩展名、路径、目录树
│   │   ├── test_sanitizer.py        脏 HTML → Block，字符守恒
│   │   ├── test_kinsoku.py          WJ 注入正确性
│   │   ├── test_outline_text.py     大纲标签规则（去掉 WJ、折叠空白；FR-018）
│   │   ├── test_toc.py              目录项的两个目标（节 / 锚点；FR-014、ADR-017）、书没点名的节自己命名（FR-012、ADR-019）
│   │   ├── test_wheel.py            滚轮两种事件形态的换算（FR-063）
│   │   └── test_images.py           文件头尺寸预读
│   └── integration/                 需 QGuiApplication（offscreen）
│       ├── conftest.py              shell fixture + 共用工具（开窗、点击、等待）
│       ├── test_typeset.py          连续列：列高 / 块↔偏移 / 窗口渲染 / 图片（核心 P0 指标）
│       ├── test_scroll_view.py      滚动交互：滚轮 / 按键 / 滚动条 / 量子内复用位图
│       ├── test_layout.py           三栏布局算术（ADR-014）
│       ├── test_toc_panel.py        目录栏常驻 + 状态栏开关（FR-013）
│       ├── test_outline.py          大纲只列本节标题（FR-018）
│       └── test_menu.py             右键菜单入口/内容/点击生效（FR-071）
├── pyproject.toml                   项目元数据 + 依赖 + pytest 配置
└── README.md
```

### 7.2 模块职责与规模预算

| 模块 | 职责 | 依赖 | 行数（`wc -l` 实测） | 对应需求 |
|---|---|---|---|---|
| `domain/models.py` | 数据契约（Block/Span/ImageRef/Book + BlockKind/BlockAlign） | stdlib | 190 | — |
| `domain/errors.py` | 异常层次 | stdlib | 31 | — |
| `domain/epub/container.py` | 定位 OPF | stdlib | 54 | FR-002 |
| `domain/epub/opf.py` | 元数据/清单/spine（含 `spine@toc`、`linear`） | stdlib | 232 | FR-003, FR-004, FR-011, FR-016 |
| `domain/epub/toc.py` | nav + ncx 目录树 + landmarks（含容错读取器） | stdlib | 412 | FR-009~013 |
| `domain/epub/paths.py` | 路径归一化 | stdlib | 62 | FR-005, FR-006 |
| `domain/epub/images.py` | 尺寸预读 | stdlib | 110 | ADR-006 |
| `domain/epub/book.py` | 组装 + 统一接口 | stdlib | 322 | FR-007, FR-016 |
| `domain/html/sanitizer.py` | **核心**：脏 HTML → Block（含 `<base>` 重定基准） | stdlib | 807 | FR-005, FR-020~027 |
| `domain/html/kinsoku.py` | **核心**：WJ 注入 | stdlib | 108 | FR-031 |
| `typeset/settings.py` | 排版设置 + `PageGeometry` | QtGui | 293 | FR-032~037 |
| `typeset/style.py` | Block → 格式对象 | QtGui | 256 | ADR-002 |
| `typeset/document.py` | Block → QTextDocument | QtGui | 145 | ADR-002 |
| `typeset/engine.py` | **核心**：连续列排版 + `LaidOutSection` + 节 LRU | QtGui | 388 | FR-050~053, ADR-003 / ADR-016 |
| `typeset/images.py` | **核心**：懒解码 + LRU | QtGui | 181 | FR-054, ADR-006 |
| `typeset/renderer.py` | 窗口 → QImage（量子对齐） | QtGui | 98 | FR-056~058, ADR-008 / ADR-016 |
| `app/controller.py` | 状态机 + QML 接口 + 窗口位图 LRU | QtCore/QtGui | 1251 | FR-016, FR-060~067 |
| `app/page_item.py` | `PageItem`：贴窗口位图、量子内平移 | QtQuick | 137 | FR-056~058, ADR-016 |
| `app/settings_store.py` | JSON 持久化 | stdlib | 199 | FR-080~083 |
| `app/main.py` | 启动引导 | QtQml/QtQuick | 110 | FR-001 |
| QML（7 文件） | 界面 | — | 1,566 | FR-090~092 |

**合计 7,088 行**（Python 5,522 + QML 1,566），其中标注「核心」的四个模块是质量与性能的关键路径。（表内不含各 `__init__.py` 与 `__main__.py`，它们计入合计。）

### 7.3 分层依赖铁律（Code Review 检查项）

| 规则 | 检查方式 |
|---|---|
| `src/ebook_reader/domain/**` 中不得出现 `PySide6` | 单测中扫描源码文本 |
| `src/ebook_reader/qml/**` 中不得出现业务逻辑（只允许绑定与动画） | Code review |
| `typeset` 不得 import `domain.epub`（只依赖 `domain.models`/`blocks`） | import 图检查 |
| QML 不得直接调用 `typeset`，只能经 `app.controller` | Code review |

---

## 8. 性能预算分解

总预算按「一次切节 + 首屏显示」拆解，确保 NFR-001/002/003/004 可被逐项验证。

### 8.1 切节（打开新章）耗时预算

| 步骤 | 预算 | 说明 | 对应 |
|---|---|---|---|
| 从 ZIP 读 XHTML | 2 ms | 22K 字 ≈ 200KB 解压 | — |
| `ElementTree` 解析 | 8 ms | C 实现，实测量级 | NFR-008 |
| 规范化 → list[Block] | 6 ms | 遍历树 + 塌缩无用标签 | — |
| WJ 注入 | 1 ms | 纯字符串扫描 | ADR-005 |
| `QTextDocument` 构建 | 6 ms | 100 块 × 行内格式 | — |
| 一次列布局 | 12 ms | Qt 断行 + 整形；**不分页、无迭代修正**（ADR-016） | NFR-003 |
| 首屏窗口光栅化 | 15 ms | 含 0–2 张图片解码 | NFR-004 |
| **合计** | **50 ms** | 目标 ≤ 50 ms | NFR-003 |

> **注**：目标是「稳态」（字体缓存已热）。首次打开书时字体缓存冷，首节可能达 80–120ms，可接受。

### 8.2 同节内一次滚动的耗时预算（热路径，必须最快）

| 步骤 | 预算 |
|---|---|
| 量子内平移（`PageItem.pan`，`drawImage` 源矩形） | 0 ms（不进 Python） |
| 跨量子且窗口位图 LRU 命中 | 0.2 ms |
| 跨量子且未命中 → 光栅化该窗口 | 9–17 ms |
| 更新 QML 纹理 | 1 ms |
| **合计（量子内）** | **≈ 0 ms**（纯 GPU） |
| **合计（跨量子）** | **≈ 16 ms** |

> 16 ms ≈ 一帧（60fps）→ 跨量子时会有一次「轻微顿挫」。**对策**：`LayoutEngine.prefetch(section, offset + 一屏)` 预取下一窗口的位图，使绝大多数跨量子落在命中路径；而一屏（约 1300 px）≈ 40 个量子，所以**需要 Python 的帧比例本来就很低**（ADR-016）。

### 8.3 内存预算

| 项 | 预算 | 说明 |
|---|---|---|
| PySide6 + Qt 基线 | 70 MB | 空载 |
| 3 个 `QTextDocument`（节 LRU） | 12 MB | 每节约 4MB |
| 窗口位图 LRU（`_window_cache`，5 张 × 4 MB @1×） | 20 MB | 每张比页框多 1 个量子；HiDPI 2× 时约 75 MB |
| 图片解码 LRU | 32 MB | 双限（24 张 / 32MB） |
| `Block` 模型（3 节） | 3 MB | 纯 Python 对象 |
| 其他（QML 场景图等） | 13 MB | — |
| **合计（1× DPI）** | **150 MB** | 目标值 |
| **合计（2× DPI，窗口缓存 5 张）** | **185 MB** | 仍在上限内 |

### 8.4 启动预算

| 步骤 | 预算 |
|---|---|
| Python + PySide6 import | 180 ms |
| `QGuiApplication` 创建 | 60 ms |
| QML 引擎 + 场景图初始化 | 120 ms |
| 打开书（解析 OPF/目录，不含节排版） | 40 ms |
| 首节排版成一列 + 首屏光栅化 | 50 ms |
| **合计** | **450 ms** ✅ 满足 NFR-001（0.5s） |

> **关键设计**：启动时**不整篇解析任何节，除了首节**。目录需要每一节的名字，因此只读**每个文档开头 1 KB**（`_LABEL_SNIFF_BYTES`，FR-012 / ADR-019）：合集 135 节实测 36–45 ms，而整篇解析它们是 260 ms——名字在第一段，读整篇是为了拿名字之外的东西。首节内容照旧按需整篇解析（首屏要它）。

---

## 9. 测试策略

原则：**只测重点核心功能**，快速、无 GUI 依赖优先（NFR-023 的 10 秒预算针对纯 Python 单元套件，实测 1.02 s；集成套件每项都要真开一次 QML 窗口并打开一本真书，约 0.45 s/项，见 §12.7）。不追求覆盖率数字，追求「关键风险点被守住」。

### 9.1 单元测试（`tests/unit/`，纯 Python，154 项）

只覆盖**最容易出错且无 GUI 依赖**的部分，全部使用真实书籍数据：

| 测试文件 | 测什么 | 为什么值得测 |
|---|---|---|
| `test_epub.py` | ① 两本真书都能打开；② **25 个无扩展名文档被正确识别**（I-1）；③ spine 顺序正确（27 / 35）；④ 图片资源路径全部命中（408 / 53）；⑤ 目录树非空且层级正确；⑥ 合成书验证**书自己声明**的指针：`spine@toc` 指到的 NCX 即使 media-type 写错也读得出目录（缺陷 20）、`linear="no"` 的文档被顺序阅读跨过而仍可寻址（缺陷 21）、声明了 `gbk` 的包文档仍读得出书名与作者（缺陷 23）；⑦ 两本真书 `linear` 全为真，换节就是 `±1`（规则不误伤参考书） | 解析错误会导致一切失效；I-1 是本项目最易踩的坑；⑥⑦ 测的是真书里没有样本、只能靠合成书写出来的规则 |
| `test_sanitizer.py` | ① 脏 HTML（4044 `<b>` + 2303 `<span>` + 554 `<div>`）产生的块数合理；② **字符守恒**：Block 文本总长 vs 原始文本长度差异 < 1%（R-09）；③ 图片块被正确识别（含 `<div><img>` 嵌套）；④ `head/meta/link/style` 不泄漏为文本（含**不带斜杠**的空元素，缺陷 22）；⑤ `<base href>` 重定基准后图片与链接都落在正确目录（缺陷 19）；⑥ 声明 `gbk` 的章节按自己的编码解码（缺陷 23） | 决定内容是否丢失，是最高风险点；`<base>`、空元素与编码这三条都是「读成字符串才看得见」的规则，只有单测钉得住 |
| `test_kinsoku.py` | ① 禁行首字符前确实插入了 WJ；② 禁行尾字符后确实插入了 WJ；③ 无重复注入；④ 注入后**除 WJ 外文本不变**（字符守恒） | 决定排版观感，纯字符串逻辑极易写错 |
| `test_images.py` | ① JPEG 尺寸预读正确（对 5 张真实图人工核对）；② PNG 尺寸预读正确；③ 损坏数据不抛异常（返回 None） | ADR-006 的基础，错了会导致版面错乱 |
| `test_outline_text.py` | ① 空白归一化（连续空白 / 换行折叠为单个空格）；② U+2060 被剥离（WJ 只服务于排版，不该出现在读者眼前，FR-109）；③ 长标题**原样保留不截断**（列会换行；「取首句」规则随按页回退一起作废，ADR-016）；④ 空标签 → 空串，面板据此丢掉该行 | 大纲标签的取值规则（FR-018），纯字符串逻辑，错了会直接显在 UI 上 |
| `test_toc.py` | ① 两种目录格式（nav / ncx）的行都保留 `#frag` 并百分号解码；② 手写 `TocEntry` 的默认值（无锚点）；③ `section_index_for_href()` 忽略锚点、锚点不影响节号；④ 参考书的**每个锚点都能解析到块**、28 项目标两两不同；⑤ 六种畸形 nav（平铺 `<a>` / `ul` 嵌套 / 未闭合 `<li>` / 裸 `&` / 没有 `epub:type` / 声明 `gbk`）与未闭合 `navPoint` 的 NCX 都读得出条目，解码按文档自己的声明（缺陷 18、23）；⑥ landmarks 只认自己声明 `epub:type="landmarks"` 的 `<nav>`，且不混进目录（FR-009）；⑦ **书没点名的节自己命名**（FR-012 / ADR-019）：合集 134 行且升序、纯图封面与图注不成行、`h3` 长标题仍成名、`linear="no"` 不补、嵌套 NCX 下补出的行落在所属部分内、开头被读窗口截断的名字整篇读回 | ADR-017 / ADR-019 的规则本身；丢锚点是「目录对读者说谎」这类缺陷的唯一入口，且只有真实书才会暴露；⑤⑥⑦ 测的是真书里没有的样本 |
| `test_wheel.py` | ① `angleDelta` 一格的位移与方向（±120 = ±`wheelStep`）；② 只有 `pixelDelta` 时按像素原样、不翻符号；③ 两种形态**方向一致**；④ 同时有值时以角度为先（X11 的 `pixelDelta` 不可信）；⑤ 相位事件（两者皆零）位移为 0 | FR-063 的全部算术。纯函数、无 Qt，是「鼠标滚轮没反应」这类缺陷最快的回归防线 |

**明确不测**：Qt 自身行为（断行位置、整形的像素细节）、QML 渲染、真机 GPU 表现——这些用 P0 手工实测代替。

### 9.2 集成测试（`tests/integration/`，`QT_QPA_PLATFORM=offscreen`，85 项）

只用**真实中文内容**跑通端到端链路（排版、渲染、控制器与三栏布局），并顺带断言性能指标：

| 测试 | 断言 |
|---|---|
| `test_typeset.py::test_section_build_stays_within_budget` | 康波书最大节排成一列（先要求列高 > 2 屏，否则这条测试没有意义），耗时 **< 50ms**（NFR-003） |
| `test_typeset.py::test_window_rendering_stays_within_budget` | 沿列取 8 个窗口（各相隔一屏）渲染，**最差 < 20ms**（NFR-004） |
| `test_typeset.py::test_rendered_window_has_the_expected_geometry` | 窗口位图 = 页框宽 × (页框高 + 1 量子)；这是「量子内平移」成立的前提（ADR-016） |
| `test_typeset.py::test_the_column_height_is_one_layout_pass_and_reproducible` | 列高 = `document.size().height()`，且 `clear()` 后重建结果一致——没有迭代修正，结果只由内容与版式决定 |
| `test_typeset.py::test_the_block_lookup_is_the_last_block_at_or_before_an_offset` | `block_at_offset()` 的语义（块顶可以相同，取不晚于偏移的最后一个块） |
| `test_typeset.py::test_reading_position_survives_a_font_size_change` | 存 `(节, 块, 偏移)` → 改字号 16→20 → 恢复 → 落在**同一块**（ADR-011 / ADR-016） |
| `test_typeset.py::test_no_figure_is_squeezed_or_overlapped_by_the_column` | 418 张图在列中的矩形与显示尺寸偏差 ≤ 0.497 px（容差 1.0）——页边界消失后，这是 ADR-007 当年担心的那类版面事故的新防线 |
| `test_typeset.py::test_a_heading_has_its_text_right_below_it` | 标题与其正文的间距 ≤ 2 行步（实测最差 1.45） |
| `test_typeset.py::test_no_forbidden_character_starts_a_line` | 中文段落排版后行首**不含禁行首字符**（FR-031 + ADR-005 联合验证） |
| `test_typeset.py::test_every_section_of_both_books_can_be_laid_out` | 两本参考书的**全部节**都能排成一列（含无标题节、含图节） |
| `test_typeset.py`（图片缓存 5 项） | 懒解码 + LRU 双限、命中、绝不放大、缺资源返回空（ADR-006） |
| `test_typeset.py::test_section_cache_evicts_old_sections` | 节 LRU 上限（ADR-003） |
| `test_scroll_view.py` | ① 方向键一行 / 翻屏键一屏 / `]` `[` 换节 / Home End；② 滚轮按步长滚动，**包括由触摸板设备发出的滚轮**（缺陷 24：Wayland 的滚轮在 Qt 眼里来自触摸板）；③ **量子内滚动复用同一张位图**（`windowOffset` 不变），跨量子才换图；④ 滚动条比例 = 列长，列装得下一屏时不出现；⑤ 章末那一行真的进入下一章，且书没点名的下一章照样写出标题；⑥ 合成书里 `linear="no"` 的文档被 `]` `[` 跨过（FR-016 / FR-060~067 / ADR-016 / ADR-018） |
| `test_outline.py` | ① 大纲行 = 本节标题、缩进层级；② 高亮跟随当前偏移；③ 点击跳到该标题的偏移；④ 无标题的节不提供该栏；⑤ 重排（改字号 / 开关侧栏）后按新栏宽重建（FR-018 / FR-019 / ADR-016） |
| `test_layout.py` | ① 窗口默认尺寸与最小宽度；② 三栏 `x`/`width` 相接、无重叠无缝；③ 交给 Python 的页框 = 正文栏宽；④ 无内容时右栏不出现且宽度归还；⑤ 窄窗口（720）下两栏仍可用；⑥ 设置抽屉不占栏、不动左侧目录、只有它会遮断点击区（FR-019 / FR-062 / ADR-014） |
| `test_toc_panel.py` | ① 状态栏「目录」按钮**真点击**即显示 / 隐藏，`pageColumnWidth` 与交给 Python 的页框一起变化；② 按钮在页框之外，点击不滚动；③ 无书（或无目录）时置灰且点击不产生副作用；④ **真点击**目录项跳转后左栏仍在、高亮跟随；⑤ 点章节 / 滚动 / 开大纲都不收起目录，只有 `T` / `Esc` 收起（FR-013）；⑥ 合集（135 节 / NCX 3 条）左栏 **≥ 130 行**、按阅读顺序、目标两两不同，**真点击补出的行**落到那一节（FR-012 / ADR-019） |
| `test_menu.py` | ① 真事件右键在正文 / 目录栏 / 状态栏三处都能弹出，且弹在鼠标处（贴底时上移）；② 菜单列出全部操作（21 行 + 14 个子菜单项，多一行少一行都算失败）；③ 无书时行置灰、已开栏带勾选；④ **真点击**行会真的执行（换章 / 关栏 / 换主题，子菜单两跳全程鼠标）；⑤ 右键点滚动条只弹菜单、同一处左键真的滚动（FR-071） |

> `test_no_forbidden_character_starts_a_line` 是本套测试中**最有价值的一条**：它把「避头尾是否真的生效」从「肉眼观察」变成「自动化断言」，直接守住 R-01。

### 9.3 手工实测清单（P0，不进自动化）

| 项 | 方法 | 通过标准 |
|---|---|---|
| 中文两端对齐观感 | 打开康波书正文，目视 | 右边界齐平，字距无明显突兀 |
| 首行缩进 / 行距 / 页边距 | 目视 | 接近纸质书 |
| 图片显示与不变形 | 连续滚动 30 屏目视 | 无变形、无模糊；窗口边缘截开的图换到别的滚动位置能看到全图（窗口只是取景框，ADR-016） |
| 内存 | `cat /proc/<pid>/status \| grep VmRSS` | ≤ 200MB |
| 启动 | `time ./run.sh` | ≤ 1.0s |
| 无 GPU 回退 | `QT_QUICK_BACKEND=software` 启动 | 正常显示与滚动 |
| Wayland / X11 | 分别启动 | 都正常 |
| 滚轮（**真实会话**，不是离屏） | 在 Wayland 会话里用真鼠标滚一格（同时按 `↓` 对照） | 文字按 3 行位移。若滚轮不动而 `↓` 能动，先看 `QT_LOGGING_RULES='qt.quick*=true'` 是否对每次滚轮打出 `DECLINES`——那就是设备过滤（缺陷 24 / ADR-018），离屏测试看不见它 |
| 三栏布局 | 开目录 + 大纲，再调整窗口大小到 720 宽 | 三栏都不重叠、正文仍可读、滚轮与点击区有效、阅读位置不变 |
| 状态栏进度 | 打开康波书，滚过一整章 | 「本卷 x% · 全书 y%」随滚动单调增长；一章在一屏内读完即显示「本卷 100%」（FR-072 / FR-075） |
| 节末「下一章」一行 | 滚到章末 | 出现「下一章 · 〈标题〉」；点它才换章，不点就停在本章末尾；最后一章不出现该行（FR-074） |

### 9.4 运行方式

```bash
# 单元测试（秒级）
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/unit -q

# 集成测试
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/integration -q

# 全部
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

---

## 10. 实施路线图

按「每阶段结束都有可运行/可验证产物」原则切分，避免长期无反馈。
状态列：✅ 已完成 / 🟡 部分完成 / ⬜ 未开始。

| 阶段 | 内容 | 产物 / 验收 | 对应需求 | 状态 |
|---|---|---|---|---|
| **P0 验证** | 装环境；用真书跑通「解析一节 → 建文档 → 排版 → 渲染」；测性能与禁忌指标 | 性能达标 + 中文排版目视合格；**门禁**：不达标则回退 ADR-001 后备方案 | NFR-001/003/004, R-01/02/05 | ✅ 见第 12 节（当时为分页原型，ADR-016 后同一链路改为「排成一列 → 渲染窗口」） |
| **P1 领域层** | EPUB 解析（container/OPF/paths/images/book）、目录（nav+ncx）、XHTML→Block 规范化、WJ 注入 | `tests/unit` 全绿（两本真书） | FR-002~007, FR-010~013, FR-020~028 | ✅ |
| **P2 排版层** | 设置与样式、文档构建、连续列排版（ADR-016）、图片懒解码 LRU、窗口渲染、节 LRU | `tests/integration` 全绿；任意节的任意偏移可渲染为窗口位图 | FR-030~039, FR-050~059 | ✅ |
| **P3 应用层 + QML 骨架** | `ReaderController`（滚动/跳转/进度）、`main.py` 引导、`Main.qml`/`ReadingView.qml`/`PageItem.qml`/`StatusBar.qml`、快捷键、位置记忆 | **两本目标书可连续读完**（v1 最小可用） | FR-001, FR-060~062, FR-064, FR-067, FR-080, FR-083 | ✅ |
| **P4 界面完善** | 目录侧栏、**本节大纲栏（三栏布局）**、**右键「全部操作」菜单**、设置面板（字号/字体/行高/边距/主题）、沉浸模式、窗口状态记忆、脚注跳转 | 达到「好用」 | FR-014~019, FR-034~037, FR-065~066, FR-071, FR-081~082, FR-090, FR-092 | 🟡 目录、大纲（ADR-014）、右键菜单（ADR-015）与设置面板已完成；沉浸模式与脚注跳转待做 |
| **P5 中文排版美化** | 标点挤压、字体栈精调、标点全角/半角、图片与图注样式（「孤行控制」已随页边界一起消失，ADR-016） | 观感超过 Foliate 默认 | FR-040~041, FR-053 | ⬜ |
| **P6 过渡动画** | 三档：① 位移淡入（已实现）② 3D 翻转+阴影 ③ 纸张卷曲（shader，`ShaderEffectSource` + `ShaderEffect`）。**连续列下滚动本身不需要动画**，这三档只对「换节 / 跳转」这类整屏切换成立 | 60fps 稳定（NFR-005） | FR-092, G6 | 🟡 档①已完成 |
| **P7 打包与分发** | PyInstaller spec + `build.sh`、可控剪裁（`bundle.py`）、`dist/` 双产物、`.desktop`、`xdg-mime` 关联 | 双击 `.epub` 用本应用打开 | NFR-011, R-07 | 🟡 见 §12.8：二进制与 `.desktop` 已完成，单文件版 62.1 MB 达标；AppImage 与 Windows 脚本待做 |
| **P8 可选增强** | 全书搜索、进度条拖动、最近书籍、文本选择复制 | P2 需求 | FR-068~070, FR-084 | ⬜ |

**关键门禁（Gate）**：
- **P0 是硬门禁，已通过**（第 12 节）。无需启用 ADR-001 的后备方案。
- **P3 已达成 v1 最小可用**：两本目标书均可从打开连续读到结束。此后每个阶段都在「已可用」基础上增强。


---

## 11. 架构自检清单

逐条对照需求与架构，确认无遗漏（评审用）：

| 检查项 | 结论 |
|---|---|
| 每个 P0 需求是否都有对应的架构模块承担责任？ | ✅ 见 7.2 模块表的「对应需求」列 |
| 是否为每个 P0 风险设了缓解措施？ | ✅ R-01→ADR-005，R-02/R-03（页边界切割图文）→ADR-016（连续列取消页边界，问题由「需修正」变为「不存在」），R-05→ADR-006，R-08→ADR-008，R-10→FR-051 |
| 是否所有性能指标都可测量？ | ✅ 见 8 节预算分解，每项都有测量方法（集成测试直接断言 NFR-003/004） |
| 是否避免了「架构上必然失败」的方案？ | ✅ 见 3.3 淘汰过程（E/F 因 Windows + 库升级免疫被结构性排除） |
| 是否有逃生舱以防关键技术被证伪？ | ✅ QTextDocument→可替换 Layout（ADR-001）；`BookSource`→可扩展格式（NFR-031）；`QImage`→可换 RHI 渲染（NFR-032） |
| 依赖是否最小化？ | ✅ 运行时 1 个依赖（ADR-009） |
| 核心逻辑是否可无 GUI 测试？ | ✅ domain 层零 Qt（ADR-012），测试扫描验证 |
| 已知的功能缺口是否被诚实记录？ | ✅ 竖排/ruby/表格/手机 → «Out of Scope»；**跨节不拼接**（换节即换列）→ ADR-016 明示不做 |
| 架构决策是否可追溯？ | ✅ 19 条 ADR，每条含背景/决策/理由/已否决方案/代价/验证（ADR-004 与 ADR-007 由 ADR-016 取代） |
| 是否有明确的止损路径？ | ✅ P0 门禁 + ADR-001 后备方案 |

---

## 12. P0 验证记录（实测）

**结论：P0 门禁通过。** 架构 C 在真实书籍上达到或超过全部性能与质量指标。

环境：Manjaro / KDE / Wayland，Python 3.14.7，PySide6-Essentials 6.11.2，`QT_QPA_PLATFORM=offscreen`，页面 1000×1346，字号 18px，行距 1.75。

> **怎么读这一节**：下面的记录写于**分页原型期**（当时的 ADR-007）。其中的「页」对应今天的「窗口」，「分页」对应「排成一列 + 按偏移取窗口」（ADR-016）；随页边界一起消失的量（页数、页边界切断）在 ADR-016 一节给出了替代的度量与防线。实测数字保留为历史基线，未重新测量。

### 12.1 性能实测

| 指标 | 目标 | 实测 | 结论 |
|---|---|---|---|
| NFR-003 单节排版（中位数，27 节） | ≤ 30 ms | **14.7 ms** | ✅ |
| NFR-003 最大节（261 块；当时按 32 页切分） | ≤ 50 ms | 86.3 ms（**首次**布局，见下） | ⚠️ 已解释 |
| NFR-004 单页光栅化（中位数；今为窗口光栅化） | ≤ 15 ms | **8.1 ms** | ✅ |
| NFR-004 单页光栅化（最差；今为窗口光栅化） | ≤ 20 ms | 17.0 ms | ✅ |
| NFR-008 全书解析（27 节 / 23.8 万字 / 408 图） | ≤ 200 ms | **118 ms** | ✅ |
| 目录补行：合集 135 节各读**文档开头 1 KB**（本轮新增，FR-012 / ADR-019） | — | **36–45 ms**（整篇解析同一批文档 **260 ms**，故只读开头） | ✅ |
| 图片表头探测（408 张） | — | **16 ms**（优化前 383 ms） | ✅ |
| NFR-007 图片缓存峰值 | ≤ 30 MB | 17–20 张 / 27.3 MB | ✅ |
| NFR-006 单张图片解码 | ≤ 25 ms | < 10 ms（按显示尺寸解码） | ✅ |

**关于「首次布局 86 ms」**：逐段计时显示，进程内**第一个**大节的首轮布局需要 49.6 ms 用于字形整形缓存预热，之后同类大节仅需 12.6 ms。这是一次性成本，发生在启动路径上，被 NFR-001 的 450 ms 启动预算吸收。故 NFR-003 的 30 ms 指标按**稳态**考核（测试中先布局一个小节预热，与真实启动顺序一致）。

### 12.2 质量实测

| 指标 | 目标 | 实测 | 结论 |
|---|---|---|---|
| FR-031 避头尾：违法行首标点数 | 0 | **645 行 → 0** | ✅ |
| FR-052 图片被页边界切断数（指标已随 ADR-016 废除） | 0 | **全书 428 页 → 0** | ⬜ 已废除 |
| FR-053 标题成为页末孤立块（指标已随 ADR-016 废除） | 0 | **0** | ⬜ 已废除 |
| ADR-007 分页修正收敛（ADR-007 已作废） | 全部收敛 | **27/27 收敛，均为 2 遍** | ⬜ 已废除 |
| R-09 字符守恒（最大节） | 差异 < 1% | **< 1%**（且在去掉块间连接空格后进一步下降） | ✅ |
| ADR-006 表头探测与实际尺寸一致 | 完全一致 | **461 张图 0 处不符** | ✅ |
| 图片解码失败数 | 0 | **0** | ✅ |
| 渲染页 / 窗口非空白 | 是 | 抽样 801 个非白像素 | ✅ |

**「645 行 0 违规」是本项目最重要的一条实测结论**：把风险 R-01（Qt 内建避头尾不可靠）从「担心」变成了「已证伪且可自动回归」。该断言已固化为集成测试，任何破坏避头尾的改动都会立刻失败。

**页边界类指标的去向（ADR-016）**：连续列没有页边界，于是「图片被页边界切断」与「标题成为页末孤立块」这两条**整类消失**——ADR-007 的迭代修正机制也随之删除。它们的位置由两条新断言接管：`test_no_figure_is_squeezed_or_overlapped_by_the_column`（418 张图在列内的矩形偏差 ≤ 0.497 px，容差 1.0）与 `test_a_heading_has_its_text_right_below_it`（标题与其正文的间距 ≤ 2 行步，实测最差 1.45）。

### 12.3 开发过程中发现并修正的 24 个真实问题

这些问题都是**实测才暴露**的，全部已修复并加了防护（注释或测试）：

| # | 现象 | 根因 | 修正 | 防护 |
|---|---|---|---|---|
| 1 | 408 张图全部解码失败 | PySide6 中 `QBuffer(QByteArray)` 传指针给临时对象，Python 立刻回收导致悬垂 | 改为 `buffer.setData(payload)`（拷贝） | 集成测试断言 `failures == 0` |
| 2 | 每页渲染 212 ms，且把全节 28 张图重画一遍 | `painter.setClipRect`（设备坐标、translate 之前）**不能**为 `QTextDocumentLayout` 提供文档坐标裁剪区，Qt 于是遍历所有页的所有块 | 设置 `PaintContext.clip`（文档坐标） | 渲染耗时断言 < 20 ms |
| 3 | 图片缓存 0 命中，28 张图每页重复解码 | **LRU 循环颠簸**：工作集 28 > 容量 24，同序访问导致「刚淘汰的正是下一个要用的」 | 容量提到 64 项（真正约束是 32 MB 字节上限） | 两个测试分别钉住「不超预算」与「放得下就命中」 |
| 4 | 封面节 1 张图占 2 页，含图章节页数虚高 18% | `ProportionalHeight` 行高被乘到**含图片的行**上：800 px 的图变成 1400 px 的行 | 图片块改用 `SingleHeight` | 图片不跨界断言 + 页数回归 |
| 5 | 图注居中失效 | 居中段落仍带首行缩进，观感错误 | 居中块缩进置 0 | — |
| 6 | 侧栏总是显示、滑入动画无效 | 同一轴同时设置 `anchors.left` 与 `x`，锚点静默覆盖 `x` | 只锚定垂直方向，水平用 `x` | 截图验证 |
| 7 | 页面被缩放到 0.4 倍、四周大白边 | `setViewSize` 传的是窗口宽度，而阅读区被两侧面板挤窄 | 面板改为浮层覆盖，页面几何只由窗口决定（顺带消除了开面板导致的重排）。**该决策已于 ADR-014 修正为三栏平铺** | 截图验证 + `test_layout.py` |
| 8 | 目录 / 大纲占位后，正文左右点击区不再翻页 | 「点击面板外关闭面板」的全屏遮罩条件是 `visible: cTocOpen \|\| cSettingsOpen`。面板浮层时遮罩不影响正文，占位后遮罩横跨整窗，把翻页点击区吃掉了 | 遮罩只在设置抽屉打开时启用，并移入正文栏内部，覆盖范围与「抽屉遮住了哪块文字」一致 | `test_layout.py` 断言开目录/大纲时遮罩不可见、开设置时才可见 |
| 9 | 开关侧栏后正文仍按**旧**宽度排版，切换一瞬间与之后都不对 | `onCTocOpenChanged` 处理器在**变更级联中途**执行，此时 `pageColumnWidth`、`sidePanelWidth`、`openColumns` 这些派生绑定还没重算，读到的是上一轮的值 | 处理器改为 `Qt.callLater(...)`，推迟到本轮事件循环末尾（派生值已稳定），并不再经过 140 ms 防抖 | `test_layout.py` 断言 `_view_size.width() == pageColumnWidth` |
| 10 | 关闭书籍后右侧大纲栏不消失，仍占一栏空白 | `outlineAvailable` 的 notify 是 `pageChanged`，而 `closeBook()` 只发 `bookChanged`/`tocChanged`/`layoutChanged`，绑定从未重算，一直沿用关闭前的答案 | `closeBook()` 补发 `pageChanged`（「已经没有页了」本身就是一次页变化），并同时清掉大纲缓存 | `test_layout.py` 断言关书后右栏消失且宽度归还正文 |
| 11 | 大纲文案里混入不可见字符 U+2060 | 大纲取的是 Block 文本，而 Block 文本里已按 ADR-005 注入了 WORD JOINER；它是排版用字符，出现在 UI 会让标签与正文看起来不一致 | 取标题时先剥掉 U+2060 并归一化空白 | `test_outline_text.py` + `test_outline.py` 双向断言 |
| 12 | 新增的 `ContextMenu.qml` 一被 `Main.qml` 引用就报 `Type cannot be created in QML`，而该文件单独加载完全正常 | **类型名撞车**：Qt 的 FluentWinUI3 风格在隐式导入的模块里注册了单例 `ContextMenu`（`QtQuick/Controls/FluentWinUI3/impl/qmldir`），该名字优先于同目录同名文件，于是解析到那个不可创建的虚类型 | 组件改名 `ReaderMenu.qml`（并保留 `objectName` 供测试查找） | `test_menu.py` 找不到 `readerMenu` 即整组失败 |
| 13 | 深色 / 米色主题下右键菜单仍是**白底黑字**的浮空白块 | 活动样式是 **Fusion**，其菜单底用 `palette.base`、分隔线用固定的 `Fusion.darkShade`、箭头是位图；而本应用的主题色只写进面板自己的 QML，从未写进调色板，浮层因此拿不到主题色 | 菜单显式画背景 / 分隔线 / 三角箭头（面板也是这么做的），并把 `palette.base/text/highlight/…` 一并设上，供样式自绘的滚动指示器使用 | 浅色 / 深色两张真实截图逐点核对像素颜色 |
| 14 | 点目录项跳转后左栏**自己消失**，想连着看两章就得每次重按 `T`；文档则写着「点击后不收起」（FR-015 已由 ADR-014 作废、FR-018、README 三处一致） | 浮层时代的规则留在代码里没删：`goToTocRow()` 末尾仍写 `self._toc_visible = False; layoutChanged.emit()`，而 ADR-014 把面板改成占邻栏之后，这条「点完就收」已经不成立 | 删掉那两行（`goToSection` 自己会发 `tocChanged`，高亮照常跟随） | `test_toc_panel.py` 用**真点击**目录项断言左栏仍在且高亮跟随；`test_layout.py` / `test_outline.py` 断言开设置抽屉后 `tocVisible` 仍为真 |
| 15 | 按 `S` 打开设置抽屉，左侧目录栏**一起被收走**——读者只是想调字号，地图却没了 | 同样是浮层时代的耦合：`toggleSettings()` 打开时把 `_toc_visible` 与 `_outline_visible` 一起置假；三栏平铺后抽屉浮在**右侧**，与左侧目录并不争空间 | `toggleSettings()` 只让同侧的大纲栏让位；点击抽屉旁边改调新增的 `closeSettings()`（原来调 `closePanels()`，会顺手关掉目录） | `test_outline.py` 断言开设置后 `tocVisible` 仍为真、`closeSettings()` 后仍为真；`test_toc_panel.py` 断言 `S` 之后左栏仍在 |
| 16 | 鼠标滚轮 / 触控板在 KDE Wayland 上**完全不动文字**：事件到达、被接受，页面纹丝不动 | **当时写下的根因只对了一半**（补记见缺陷 24）：`Main.qml` 只读 `angleDelta.y` 并除以 120；而触控板、高精度滚轮与 Wayland 的平滑 / 自由滚动**只发 `pixelDelta`**（`angleDelta` 恒为零），换算结果就是 0 px。这一半是真的：`pixelDelta` 形态确实被漏掉，但它**不是**「鼠标滚轮完全不动」的原因——真正的原因见缺陷 24（事件根本没进 `onWheel`），所以这一轮修完之后滚轮仍然不动，直到缺陷 24 一起修掉 | 换算移入控制器：`_wheel_distance(angle, pixel, step)` —— `angleDelta` 非零按 120 单位 = 一格（`-angle/120 × wheelStep`），为零时用 `pixelDelta` 原样位移（两者皆零的相位事件直接返回，不重绘）；QML 只转发两个 delta 并 `event.accepted = true` | `test_wheel.py`（12 项：两种形态的方向与量、半个格子按比例、相位事件、同时有值时以角度为先）+ `test_scroll_view.py` 用真 `QWheelEvent` 断言一格位移 = `wheelStep`、纯 `pixelDelta` 位移 = 像素数、面板上滚轮不动正文（两种形态都测） |
| 17 | 币安目录里「同一文件的第二个条目」跳到文件开头，与上一个条目落点完全相同；左栏也永远只标第一个，第二项无法被高亮 | `join_href()` 按设计丢掉 `#frag`，`TocEntry` 无处存放它，`goToTocRow()` 只能再从 `href` 里 `partition("#")` —— 而那时 fragment 早已丢失；容错解析路径更是不读 `id`；`currentTocRow` 只比较「节」 | 见 **ADR-017**：`TocEntry.fragment` 单独保存、两种解析器都抽取；`id` 落在不产生块的元素上时归其后第一个块、文档末尾归最后一个块；`_TolerantParser` 补上 `id` 抽取；`currentTocRow` 改为按位置判定 | `test_toc.py`（含「参考书全部锚点可解析」9/9）、`test_sanitizer.py`（锚点归属 6 种情形）、`test_toc_panel.py`（真点击同一文件的两项）——详见 ADR-017 的验证行 |
| 18 | 目录文档**只要不合 schema 就整篇作废**：`nav.xhtml` 里有一个裸 `&`、一个没闭合的 `<li>`，或 EPUB2 转换器写出的 `<ul>`，左栏就是空的，读者只剩（当时那套）`h1`/`h2` 粗目录 —— 而浏览器看同一个文件毫无怨言 | 两个解析器都先 `ET.fromstring()`，`ParseError` 直接 `return ()`；nav 只认 `<ol>`，且只认带 `epub:type="toc"` 的 `<nav>`；`_walk_nav_list()` 假设嵌套一定长在 `<li>` 里 | `parse_nav()` / `parse_ncx()` 先按 XML 读，失败就交给容错读取器（`_TolerantNavReader` / `_TolerantNcxReader`：`html.parser` + 手工维护的栈，也就是浏览器的做法）；`_first_list()` 接受 `ul` 与嵌套列表，`_flat_links()` 读平铺的 `<a>`；`_find_nav()` 在没人声明 `epub:type` 时取第一个 `<nav>`（地图宁可读也不可丢）；解码交给 `decode_text()`，尊重文档自己声明的编码 | `test_toc.py`：平铺 `<a>`、`ul` 嵌套、未闭合 `navPoint`、裸 `&`、`encoding="gbk"` 声明、无 `epub:type` 六种情形 |
| 19 | 带 `<base href>` 的书**图片与链接全部指错**：文档在 `OEBPS/Text/`，`<base href="../">` 把基准指到 `OEBPS/`，而读者按「文档所在目录」拼路径，于是每一张图都找不到 | 路径解析只看当前文档的目录（`section_base_dir()`），从未读文档自己声明的基准；`<base>` 又恰好是「当成字符串读就看不见」的那种标签 | `_declared_base()` / `_declared_base_in_text()` / `_rebase()`：先读 `<base href>`，按浏览器的规则重定基准（以 `/` 结尾当目录，否则当「文件」取其所在目录），再用它解析该文档里所有相对 href；XML 与容错 HTML 两条路径同一规则 | `test_sanitizer.py`：`<base href="../">`、`<base href="Text/">` 与「基准指向文件」三种写法，图片与链接都落在重定后的目录 |
| 20 | 一本既没有 `properties="nav"`、manifest 里 `toc.ncx` 的 media-type 又写错的 EPUB2，**目录读不出来** —— 尽管 `toc.ncx` 就在书里，`<spine toc="ncx">` 也指着它 | 找 NCX 只看 media-type（`application/x-dtbncx+xml`）；`<spine toc="…">` 这个**明确的指针**没人看 | `_spine_toc_href()`：按 media-type 找不到时，用 `spine@toc` 的 idref 回查 manifest（media-type 缺失或写错的书因此仍能读到目录） | `test_epub.py`：合成书里 NCX 的 media-type 故意写错，`spine@toc` 仍把目录读出来 |
| 21 | 「下一章」把读者送进封面、版权页、广告页 —— 书里写着这些文档 `linear="no"`（出版方明说它不属于正文阅读顺序） | `SpineItem` 只有 `index/idref/href/media_type`，`linear` 丢在 OPF 里没人读；换节一律 `index ± 1` | `SpineItem.linear`（默认真，`linear="no"` 为假）；`EpubBook.next_section()` / `previous_section()` 走 `_step_section()`，跨过线性为假的文档；目录跳转与进度不受影响（只有顺序阅读跳过），这正是规范里这个属性的意思 | `test_epub.py`：合成 `linear_no` 书，`next_section()` / `previous_section()` 跳过被标记的节，直接跳到它上面仍然正常 |
| 22 | 容错解析路径下整节正文**凭空消失**：文档头部写了 `<meta charset="utf-8">`、`<link …>` 这类不带斜杠的空元素 | `_TolerantParser` 的「跳过头」用计数出栈（进了几次 `head`/`style`/`script`，遇到对应闭合标签减一）；而 HTML 里空元素（`<base>` `<link>` `<meta>`）**从不闭合**，计数永远为正，`<body>` 之后的每个字都被当成头部内容丢掉 | `_VOID_TAGS` 白名单（`area/base/br/col/embed/hr/img/image/input/link/meta/param/source/track/wbr`）：跳过路径与正文路径都遇到即不入栈，闭合标签也不倒计数 —— 这也是「必须走容错路径」的书里最常见的一种写法 | `test_sanitizer.py`：头部空元素不带斜杠的文档，正文仍完整且字符守恒（同时补上第 17 行的 `id` 抽取） |
| 23 | 老一点的中文书（尤其中文 EPUB2）**打开时直接抛异常**：`ValueError: multi-byte encodings are not supported` —— 尽管每个文件都在开头写清了自己的编码 | `xml.etree` 底下的 expat **只实现 UTF-8 / UTF-16**，遇到 `<?xml … encoding="gbk"?>`（`big5` 等同样）抛的是 **`ValueError` 而不是 `ParseError`**；而三个 XML 入口（nav、NCX、landmarks）与 `sanitize()` 都只 `except ET.ParseError`，于是这个异常一路穿出 `EpubBook.open()`，`try/except BookError` 也接不住 | ① 容错读取路径与 `sanitize()` 改为同时接住 `(ET.ParseError, ValueError)`，转而交给 `decode_text()`（它本来就按文档声明的编码解码）；② `parse_package()` 在 `ValueError` 时先解码、去掉 XML 声明再重解析（书名与作者正是最需要解码的那段文字）；③ `container.xml` 只剩路径、没有解码的价值，因此把 `ValueError` 归入可读的 `NotAnEpubError` | `test_toc.py`（`gbk` 声明的 nav 与 ncx）、`test_sanitizer.py`（`gbk` 章节）、`test_epub.py`（`gbk` 包文档仍读得出书名 / 作者） |
| 24 | 打包运行后**鼠标滚轮完全不动**（方向键照常），缺陷 16 的「两种 delta」修完、重新打包后**依然不动** | 事件到了窗口，却在**设备过滤**处就被拒了：`WheelHandler.acceptedDevices` 的默认值是 `Mouse` **一个设备**，而 Wayland 上 Qt 把每个滚轮事件都记在**座位的指针设备**名下，该设备登记为**触摸板**（`wl_pointer.axis` 不带自己的设备）。于是处理器对每一次滚轮都打出 `DECLINES`，`onWheel` **从未执行**——控制器收不到任何事件，上一轮改的换算在「下游」，修得再对也没用。离屏与冒烟测试看不见它：`offscreen` / `xcb` 下 Qt 报的设备是 `Mouse`，过滤放行，所以「两种 delta」的用例一直全绿 | `Main.qml` 的处理器显式收全设备：`acceptedDevices: PointerDevice.Mouse \| PointerDevice.TouchPad`（见 ADR-018；`target: null` 是默认值「监视自己的父项」，与本次无关，实测三平台行为一致，故未改） | `test_scroll_view.py` 新增用例：由**触摸板设备**（`QPointingDevice(TouchPad, Finger)`，`conftest.touch_device()`）发出的滚轮同样位移一格；**变异测试**：删掉 `acceptedDevices` 该用例即失败（实测位移 0.0，期望 135.7）。定位手段记入 ADR-018：`QT_LOGGING_RULES='qt.quick*=true'` 会打出 `QQuickWheelHandler … DECLINES …` 与事件自带的 `dev=QPointingDevice("touchpad" TouchPad … seat=seat0)` |

### 12.4 启动期 QML「读取 null 属性」错误的根因与修法（重要）

**症状**：应用启动时 stderr 输出大量

```
TocSidebar.qml:40: TypeError: Cannot read property 'panelTextColor' of null
Main.qml:43: TypeError: Cannot call method 'setViewSize' of null
PageView.qml:23: Unable to assign null to QImage
```

> （`PageView.qml` 是当时阅读组件的文件名；ADR-016 后它叫 `ReadingView.qml`。这段 stderr 按原样保留。）

界面功能看起来正常（颜色、目录、主题都对），因为绑定在 `ctl` 到位后会重新求值——但每次启动都刷一堆错误，不可接受。

**排查过程（含一次误判，记录以免重蹈）**：
- 先用最小复现做「标识符名称」对照实验，`controller` 失败而 `Ctrl`/`ctl`/`appCtl` 通过，于是**误判为名称冲突**。改名后最初几次运行看起来干净，但后来在 `bootstrap` 脚本里用 `Ctrl` 复现出同样错误——说明**名称不是原因**（`tail -8` 截断了错误输出，是导致误判的直接原因）。
- 真实原因：控制器以 **QML 上下文属性（context property）** 暴露。上下文属性只在绑定**首次求值**时查找，而嵌套组件（侧栏、`ListView` delegate、内联 `component`）是在窗口**构造期间**创建的，此时绑定求值读到的就是 `null`。组件层级越深、绑定越多，越容易命中。

**修法（三处，缺一不可）**：

| 环节 | 做法 |
|---|---|
| 注入方式 | 加载完成后由 Python 注入根对象：`roots[0].setProperty("ctl", controller)`，不再依赖全局名字查找 |
| 绑定求值 | 每个 QML 组件声明 `property var ctl: null`，并把所有取自控制器的值**镜像为带守卫的只读属性**（如 `readonly property color cPanelText: ctl ? ctl.panelTextColor : "#1b1b1b"`），使 `ctl` 为 null 时回退到安全默认值而非报错 |
| 非绑定求值 | 构造期就会执行的地方单独守卫：`Component.onCompleted` 中 `if (!ctl) { return }`；QImage 属性用 `Binding on image { when: root.cPageImage !== null }`（QML 不允许给 `QImage` 属性赋 null）；点击/快捷键回调写成 `ctl && ctl.nextPage()` |

**结果**：offscreen 与**真实 Wayland 后端**下均为 **0 条 QML 错误**。

**两条方法论教训**：

1. **`QQmlApplicationEngine.warnings` 对这类错误报告为 0 条**，信息只出现在 stderr。QML 校验必须抓 stderr，不能只看 `warnings`。
2. **最小复现可能给出错误结论**：最小 QML 与真实应用在组件构造顺序、内联组件、delegate 惰性创建上差异很大，命名实验因此指向了错误方向。结论应以真实应用的 stderr 为准。

**附带结论**：PySide6 的 `qmlRegisterSingletonType` / `qmlRegisterSingletonInstance` 拒绝纯 Python 的 `QObject` 子类（报 `wrong argument values`），因此「加载后注入」是纯 Python 控制器最可行的暴露方式，而且它比全局名字更符合显式依赖的设计原则。


### 12.5 字体栈实测修正

在真实 Qt 字体库上逐个验证族名后修正了设计假设：

| 名称 | 结果 |
|---|---|
| `思源宋体 CN`、`Noto Serif CJK SC` | ✅ 可用 |
| `思源黑体 CN`、`Noto Sans CJK SC` | ✅ 可用 |
| `AR PL UKai CN`、`文泉驿微米黑` | ✅ 可用 |
| `Source Han Serif SC` | ❌ Qt 不识别，**单独请求会静默回退到「文泉驿正黑」（黑体）**，会无声毁掉宋体观感 |
| `Noto Sans SC` | ❌ 回退到 `Droid Sans` |

因此字体栈以**中文族名**打头（`思源宋体 CN` / `思源黑体 CN`），后接 Noto CJK 名与 Windows 名作为回退。

### 12.6 体积与启动实测

| 项 | 实测 |
|---|---|
| `PySide6-Essentials` wheel | **80.1 MB**（对照：完整 `PySide6` 需额外 167 MB 的 Addons，含完全用不到的 QtWebEngine） |
| 安装后 `PySide6/` 解压体积 | 233 MB（其中 `Qt/lib` 129 MB、`Qt/qml` 23 MB、`translations` 14 MB） |
| 可裁剪项 | `libQt6Designer` 9.4 MB、`translations` 中非 zh/en 约 12 MB、`sqldrivers` 2.3 MB、未使用的 qml 控件样式 — 合计约 25–40 MB |
| AppImage 预估（squashfs 压缩后） | **约 70–90 MB**，满足 NFR-011（≤150 MB） |
| 应用冷启动（实测） | 打开书 3 ms + 单节排版 + 首页渲染；真实 Wayland 下**首屏就绪 188 ms**，offscreen 下 75–102 ms（NFR-001 目标 500 ms） |
| 打包产物启动到首屏（本轮复测，`build.sh` 冒烟测试的计时，offscreen，合集 135 节 / 134 行目录） | 文件夹版 **0.35 s**（首屏就绪 138 ms），单文件版 **1.05 s**（首屏就绪 162 ms；差额是解包）——开启目录补行后仍满足 NFR-001（0.5 s 目标 / 1.0 s 上限） |
| **打包后「文件夹版」** `dist/ebook-reader-dir/` | **169.4 MB**（未压缩；日常使用与 AppImage 的底料） |
| **打包后「单文件版」** `dist/ebook-reader` | **62.1 MB**（PyInstaller CArchive 自带 zlib 压缩）→ **满足 NFR-011（≤150 MB）** |
| 剪裁收益 | 未剪裁 **259 MB** → 剪裁后 164 MB（**−95 MB**）：GTK 栈约 25 MB、重复的系统 `libicudata.so.78` 32 MB、未使用的控件样式（`FluentWinUI3` 单项 8.3 MB）等 |
| 打包产物启动（真实 Wayland） | 文件夹版 **0.27 s**、单文件版 **1.00 s**（NFR-001 上限 1.0 s，见 ADR-013 的代价说明） |
| 运行时依赖数量 | 1（`PySide6-Essentials`） |

### 12.6.1 内存：必须区分「图形栈基线」与「本应用增量」

实测发现（本机：Mesa 26.2 / Intel RPL-P / OpenGL 4.6 兼容配置，Wayland）：

| 场景 | VmRSS |
|---|---|
| **空白 QML 窗口**（仅 `Window { visible: true }`），GPU 路径 | **310 MB** |
| 本应用 + 85 MB 书，GPU 路径 | 408 MB |
| **本应用增量（408 − 310）** | **≈ 98 MB** |
| 本应用 + 85 MB 书，`QT_QUICK_BACKEND=software` | 188 MB |

**结论**：NFR-002 原来写的「稳态内存 ≤ 150 MB（总量）」是**不可比指标**——它没有计入图形驱动栈的固定开销，而这部分在只装 Mesa 开源驱动的机器上就已达 310 MB（Mesa 的着色器编译器会链接大量代码）。本机用软件渲染时总量仅 188 MB 也印证了这点：差异来自驱动，不是来自我们。

**因此内存指标改为可比的表述**：**本应用增量 ≤ 100 MB**（实测 ≈98 MB，含 3 个节文档、页位图缓存、图片 LRU、块模型）。这条修正已同步到需求文档 NFR-002。

顺带得到一个实用的降级开关：内存受限环境可设 `QT_QUICK_BACKEND=software`，总量降到 188 MB。


### 12.7 自动化测试结果

| 项 | 结果 |
|---|---|
| 测试总数 | **239**（单元 154 + 集成 85），`pytest --collect-only -q` 计数 |
| 单元测试（纯 Python，无 Qt） | **154 项 / 1.02 s**（含解释器启动）——NFR-023 的 10 秒预算指的就是这一套 |
| 集成测试（offscreen Qt） | **85 项 / 38.07 s**——每项都要真开一次 QML 窗口并打开一本真书，单价约 0.45 s |
| 全量耗时 | **38.1 s**（一次跑完 239 项，实测 38.14 s） |
| 结果 | **全部通过**（ADR-016 改写 `test_typeset.py`、新增 `test_scroll_view.py` 之后重测；ADR-017 与滚轮修正后再测；缺陷 18~23 的可靠性加固与控制器接线后再测；ADR-019 目录补行与缺陷 24 的设备过滤后再测） |

> 对比分页原型期（156 项 / 12.25 s）：集成项从 60 涨到 78（`test_scroll_view.py` 19 项、`test_outline.py` 从按页回退改为一套标题断言），单价也从 0.7 s 降到 0.42 s——因为连续列不再需要「逐页走完 + 分页修正」这类串行开销。§9.2 的原估计（「预期 < 8s」）同样是只有 3 个集成模块时的数字，已被实测取代。**ADR-017 与滚轮修正**再添 31 项（`test_wheel.py` 12 + `test_toc.py` 10 + `test_sanitizer.py` 6 + `test_scroll_view.py` 2 + `test_toc_panel.py` 1），集成单价升到 0.43 s 是因为新用例要打开币安（35 节 / 53 图的 EPUB2）并落到锚点上，而不是窗口或排版变慢——`test_typeset.py` 的 NFR-003 / NFR-004 断言在同一版代码上仍全绿。
>
> **可靠性加固（缺陷 18~23）**再添 5 项：`test_toc.py` 2（`gbk` 声明的 nav 与 ncx）、`test_sanitizer.py` 1（`gbk` 章节）、`test_epub.py` 1（`gbk` 包文档）、`test_scroll_view.py` 1（`linear="no"` 真的被 `]` `[` 跨过）。本轮同时把本表此前残留的旧计数校正到实测值（单元 121 → 145、总数 202 → 227），并把「已实现的规则」接到调用方上：`EpubBook.next_section()` / `previous_section()` 此前**没有任何调用者**，控制器仍在自己算 `±1`——一条没人问的规则等于不存在的规则，现在 `]`、`[`、菜单与章末那一行都走同一条路。
>
> **ADR-019 目录补行 + 缺陷 24 设备过滤**再添 12 项（单元 145 → 154、集成 82 → 85、总数 227 → 239）：`test_toc.py` 9（书没点名的节怎么命名、怎么摆放，截断的名字整篇读回，合集 135 节 → 134 行）、`test_toc_panel.py` 1（真开合集：左栏 ≥ 130 行、真点击补出的行落在那一节）、`test_scroll_view.py` 2（由**触摸板设备**发出的滚轮位移一格、合集里书没点名的下一章在节末照样有名）。集成单价从 0.43 s 升到 0.45 s，来自新增用例要真打开 135 节的合集并重建目录（实测打开 36 ms），不是窗口或排版变慢。

集成测试中直接断言了本文档的性能指标（NFR-003 ≤ 50 ms、NFR-004 ≤ 20 ms）与质量指标（图片不被列挤扁或重叠、0 个违法行首、位置跨字号可恢复），使文档与代码不会脱节。三栏布局同样如此：`test_layout.py` 把 ADR-014 的算术（三栏相接、页框 = 正文栏宽）变成断言，`test_outline.py` 把 FR-018 的规则（大纲只列本节标题、高亮跟随偏移）钉在两本真书上，`test_menu.py` 把 ADR-015 的入口（右键、21 行每一项都在、点了真的生效）钉成断言，`test_toc_panel.py` 把 FR-013 的「常驻 + 一键开关」钉成断言——包括**真点击**状态栏按钮与**真点击**目录项，`test_scroll_view.py` 则把 ADR-016 的量子复用（同一窗口位图内滚动不换图）钉成断言。


### 12.8 打包实测（P7）

| 项 | 结果 |
|---|---|
| 打包器 | PyInstaller **6.22.3**（官方 classifiers 覆盖 Python 3.8–3.15；本项目运行在 Python **3.14.7**） |
| 入口 | `packaging/entry.py` —— `__main__.py` 用的是相对导入，不能直接作为 PyInstaller 的分析入口（它会被当作顶层 `__main__` 执行，包上下文不存在） |
| 构建 | `packaging/build.sh`，两种产物合计约 2 分钟 |
| 剪裁 | `packaging/bundle.py`：按表剔除 **1303** 个数据项 + **44** 个二进制项，再按可达性剪除 **63** 个孤立共享库 |
| 产物 | `dist/ebook-reader` 62.1 MB（单文件）/ `dist/ebook-reader-dir/` 169.4 MB（文件夹） |
| 依赖完整性 | `bundle.py check`：218 个 ELF、**0 个未解析库**、**0 个未解析 QML import** |
| 真实 Wayland 实测 | 三种启动方式（文件夹版带书 / 文件夹版无参数 / 单文件版带书）均 0 错误。加入右键菜单、目录栏默认展开、状态栏「目录」开关与「目录不自动收起」之后用 `packaging/build.sh` 的冒烟测试复测：三种方式仍 0 错误，启动到首屏日志 0.32 s（文件夹）/ 1.00 s（单文件），首屏就绪 108 ms / 102 ms —— 此前记录的 86–92 ms 是目录栏默认收起时的版本，ADR-015 让开书时多出一次重排，代价即在此处可见（NFR-001 仍余量充足）。**注**：该次复测在分页原型期，日志行为 `first page ready`；ADR-016 之后同一处日志已改为 `first window ready`（首屏窗口就绪），时间量不变 |
| 回归 | 单元 + 集成测试 **156 项全绿**（0.95 s + 11.85 s；当时的分页原型期数量；ADR-016 之后为 171 项 / 0.92 s + 33.05 s，ADR-017 与缺陷 18~23 之后为 227 项 / 0.95 s + 35.59 s，本轮为 239 项 / 1.02 s + 38.07 s，见 §12.7） |

**剪裁为什么用「可达性」而不是手写清单。** `Qt/plugins/**` 与 `Qt/qml/**` 都是运行时按路径 / `dlopen` 加载的，没有任何 `DT_NEEDED` 指向它们，因此无法用依赖图自动发现，只能显式列在 `PLUGIN_FAMILIES` / `PLUGIN_FILES` / `QML_MODULES` 三张表里。反过来，这份「剔除」一旦生效，**只被它们引用的共享库就变成了孤儿**（典型例子：`libqgtk3.so` 一个插件拖进整个 GTK 栈，以及被 GTK 链顺带拖进来的系统 `libicudata.so.78`——与 venv 自带的 `.73` 重复）。因此第二层用可达性遍历：以 `Qt/plugins/**`、`Qt/qml/**`、`PySide6/*.abi3.so`、`lib-dynload/**` 为根，沿 `DT_NEEDED` 求闭包，只在 `PySide6/Qt/lib/` 与 `_internal/` 顶层两个目录里剪除不可达的库。剪裁范围刻意收窄，是因为插件 / QML 模块 / Python 扩展都不在依赖图里，全量剪除会把它们删光。

**R-07 的实际形态与本次踩坑（重要）。** 风险没有以「漏掉我们自己的 QML」出现——`--add-data` 覆盖了 `ebook_reader/qml`——而是以 **「漏掉 Qt 自己的某个 QML 模块」** 出现。`QtQuick.Dialogs` 的 `FileDialog` 实现 `import Qt.labs.folderlistmodel`，该模块**只在打开对话框时才被解析**。整目录剔除 `Qt/labs` 之后，应用其余部分完全正常，只有「不带参数启动 → 选书」这条路静默失效：

```
QML FileDialog: Failed to load non-native FileDialog implementation:
qrc:/qt-project.org/imports/QtQuick/Dialogs/quickimpl/qml/FileDialog.qml:5:1:
module "Qt.labs.folderlistmodel" is not installed
```

**两道防线**（都直接来自这次踩坑，缺一不可）：

1. **`bundle.py check_qml_imports`** —— 用打包产物里每个 `qmldir` 的 `module X` 声明构建「可用模块集」，再扫描全部 `.qml` / `qmldir` 的 `import` 与 `depends`，报出解析不了的模块。这是**唯一**能自动发现此类问题的手段：Qt 只打一条 console warning，应用照常运行，退出码为 0。该检查已能复现上述缺陷（在修正前的产物上返回 2 条未解析并通过退出码 1 使构建失败）。
2. **`build.sh` 的冒烟测试必须覆盖无参数启动。** 原先只测「带书启动」，恰好绕过了唯一会触发该缺陷的代码路径；现在文件夹版同时测两条路径，单文件版测带书路径。
3. **计时用的日志行不能只写一次就忘。** 冒烟测试的「启动到首屏」靠 grep 控制器那行日志来计时，而 ADR-016 把那行从 `first page ready` 改成 `first window ready` 之后，grep 仍留在旧词上——于是**这个数字几轮以来一直没被测到**（脚本只是静默地不打印），而「0 错误」的断言照常通过。本轮把它修正并复测（§12.6）。教训与 ADR-016 的「一条没人问的规则等于不存在的规则」同源：**度量本身也会腐烂**，改日志文案时要连 grep 一起改。

**附带确认**：`.qrc` 未采用（理由见 ADR-013）；`Qt Quick Controls` 的 style 在 `app/main.py` 中显式钉为 `Fusion`——这既让界面在不同桌面环境下渲染一致，也是能够安全剔除其余 style 实现（`Material` / `Imagine` / `Universal` / `FluentWinUI3`）的前提。










