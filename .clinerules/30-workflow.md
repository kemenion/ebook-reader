# 修改流程（按顺序执行）

## 1. 定位

- 先搜后改：`search` 找到实现点、调用方与现有测试；**读调用方**再决定签名。
- 确认改动落在哪一层：解析与规范化 → `domain`；排版与渲染 → `typeset`；状态与编排 → `app`；
  展示 → `qml`。跨层改动要说明理由。
- 找到对应的 `FR` / `ADR`。改的行为与某条 ADR 冲突时，**先停下来报告**，并给出「改 ADR」的方案，
  不要静默偏离已定决策。

## 2. 实现

- 最小改动：复用既有函数、常量与缓存；只碰与任务相关的代码，不做顺手重构 / 全局格式化。
- 新的可调数字提成常量并写清依据；新的用户可见行为写进 `ReaderController`（属性 / `Signal` / `Slot`）
  再由 QML 绑定。
- 不新增依赖；确实必要时先说明为何标准库与 PySide6 做不到，等确认后再动 `pyproject.toml`。
- 改动涉及解析结果（如避头尾开关、节的划分）时，同步处理**缓存失效**（书的块缓存 + 引擎的节缓存）。

## 3. 验证

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/unit -q
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/integration/<相关文件>.py -q
```

- 改了 QML 时，注意 Qt 的告警：绑定错误、快捷键解析失败、未知属性都算缺陷。
- 能启动的场景用 `./run.sh ebooks/<书>.epub` 目视确认（无显示环境则说明未做目视验证）。
- 在结论里写清：跑了什么、结果如何、未验证的部分是什么。

## 4. 文档同步（行为变了就必须做）

| 变化 | 更新 |
|---|---|
| 用户可见功能 / 快捷键 / 交互 | `README.md` 特性段 |
| 布局、模块职责、新决策、缺陷修复 | `arch/architecture.md`（ADR、缺陷表、性能数字） |
| 需求口径（FR / NFR）变化 | `requirement/requirements.md` |

文档里的实测数字（节数、行数、耗时、体积）改动后要重测再写，不要沿用旧值。

## 5. 提交

- 一个提交一件事；标题用简短英文祈使句（如 `Keep the contents map where the reader left it`），
  正文说明动机与取舍（可中文）。
- 不提交 `ebooks/`、`build/`、`dist/`、`.venv/`、任何缓存或打包产物。

## 明确不做

- 不换 UI 技术栈、不引入 WebView / Chromium / GTK 系组件。
- 不为「可能的将来」抽象接口；不为单个调用点造工厂 / 注册表 / 插件系统。
- 不在 QML 里复制业务规则；不在 `domain/` 里 import Qt；不在 `typeset/` 里做解析。
- 不为了通过测试而放宽断言或删掉既有用例；先判断是哪一边错了。
