# 测试规范（只测核心，不铺量）

原则：**守住关键风险点，不追求覆盖率**（与 `arch/architecture.md` §9 一致）。测试成本要低、
意图要单一、失败时要能一句话说清哪条规则被破坏。

## 放哪一层

| 类型 | 目录 | 判据 |
|---|---|---|
| 纯逻辑 / 纯 Python | `tests/unit/` | 不 import Qt 就能证明的规则（解析、清洗、避头尾、目录、配置、配色、滚轮与滚屏算术） |
| 需要真实组件树 | `tests/integration/` | 只有 `Main.qml` 真的加载后才存在的缺陷（布局、滚动、选区、自动滚屏、菜单、面板） |

**先问「单元测试能不能证明」**：能，就只写单元测试。集成测试单价约 0.5 s/项，不要为了
「看起来完整」再补一份；一个规则只在**最便宜的那一层**测一次。

## 约定

- 用例名是一个完整句子：`test_the_chosen_width_survives_a_restart`；docstring 写被守住的规则
  与 `FR` / `ADR` 编号。
- 优先复用既有设施，不要另造：
  - 合成书 `build_epub`（一条规则一本小书）；
  - 参考书 fixture（缺失时自动 skip，不许改成 fail）；
  - `shell` / `controller` / `pump()` / `item()` / `warnings`（`tests/integration/conftest.py`）。
- 配置永远写到 `tmp_path`，绝不读写用户真实配置文件。
- 一次回归只加**一个**最便宜层面的用例；修 bug 时先让它红，再让它绿。
- 断言行为与数字，不断言 Qt 内部实现、不比对整张渲染位图的像素。

## 不要做

- 不引入 `hypothesis` / `freezegun` / `mock` 框架或任何新测试依赖（只有 `pytest`）。
- 不为同一规则堆参数化矩阵（既有的禁则字符全集除外）、不为凑数写「构造函数默认值」这类无风险用例。
- 不用 `sleep` 等待（除已存在的真实时钟用例）；等待用 `pump()`。
- 不测 Qt 自身的断行 / 整形结果、不测 QML 绘制细节、不测 GPU 表现——这些靠手工 P0 实测。

## 验证命令

```bash
# 1) 每次改完必跑（快，无 GUI）
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/unit -q

# 2) 改到 UI / 排版 / 交互时，只跑相关文件
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/integration/test_layout.py -q
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/integration/test_scroll_view.py -q

# 3) 发版前才跑全套（约 85 s）
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

新增测试后必须实际运行并在结论里给出结果；失败就先修，不要留「预期失败」。
