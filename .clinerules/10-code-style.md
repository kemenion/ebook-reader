# 代码规范

## Python

- 每个模块：`from __future__ import annotations`，模块 docstring 用**英文**回答「这个模块为什么存在、
  它负责什么、不负责什么」，并引用相关 `ADR-xxx` / `FR-xxx` / `NFR-xxx`。
- 公共模块用 `__all__` 明确出口；包级 `__init__.py` 只做再导出（重对象可像 `domain/__init__.py`
  那样用 `__getattr__` 延迟导入）。
- 命名：`snake_case` 函数/变量、`PascalCase` 类、`_leading_underscore` 私有实现、
  `_UPPER_SNAKE` 模块常量。**每个魔法数字都提成常量，并用 `#:` 注释写清它为什么是这个值**
  （单位、实测依据、与别处常量的对应关系）。可调参数集中放在模块顶部。
- 类型注解写全公共接口；用内建泛型与 `X | None`，不用 `typing.List` / `Optional`。
- 数据模型用 `@dataclass(frozen=True, slots=True)`（见 `domain/models.py`）；
  运行期可变状态只放在 `ReaderController`，模型保持不可变。
- 行宽 ≤ 100 列，4 空格缩进，双引号，多行调用补尾逗号；不引入新的格式化 / lint 依赖。
- 注释用英文，写**取舍与原因**（「为什么不是另一种做法」），不复述代码；面向读者的字符串
  （UI 文案、错误提示）用中文。README / 架构文档用中文。
- 异常：只捕获能处理的具体异常；确需兜底时写 `except Exception as exc:  # noqa: BLE001 — 理由`，
  并保证失败可降级（缺一张图不能毁掉一页）。
- 日志用 `_log = logging.getLogger(__name__)`；`print` 只用于 `main()` 里「启动失败」这类
  必须在终端可见的场景。
- 热路径（每帧、每跳、每次滚动回调）**不做 I/O、不做重复计算、不发多余信号**：
  先查 `controller.py` / `engine.py` 里已有的缓存与量子化机制，不要新增每帧进 Python 的路径。

## QML

- 文件头用注释说明这个界面元素负责什么、与哪些 `FR` 对应。
- 控制器只能通过 `Main.qml` 注入的 `ctl` 访问。子组件一律 `property var ctl: null` 向下传，
  派生值写成 `readonly property ...` 且在绑定里用 `ctl ? ... : 安全默认值` 保护
  （加载期 `ctl` 还不存在，未保护的绑定会刷一片 `Cannot read property of null`）。
- 颜色 / 尺寸等从控制器取，并给出安全兜底色；配色只能来自 `typeset/settings.py` 的色板，
  不要在 QML 里另写一套主题色。
- 所有可见文字用中文；快捷键在 `Main.qml` 声明，并在 `ReaderMenu.qml` 标出同一拼写，
  两者必须一致（`QKeySequence` 拼错的快捷键会静默失效）。
- 滚轮处理器显式声明 `acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad`（ADR-018）。
- 测试需要读取的项加 `objectName`（`readingView`、`tocPanel`、`pageArea`、`scrollBar` 等），
  命名与集成测试里的 `item(window, "...")` 一致。
- QML 不写业务逻辑：判断、状态迁移、持久化都放到控制器；QML 只做布局、绑定、手势转发。

## 持久化与兼容

- 配置是**可手改的 JSON**（`app/settings_store.py`）：读取时对缺失键、错类型、越界值一律
  回退默认或夹紧，绝不能抛异常；写入用临时文件 + `os.replace` 原子替换。
- 旧配置文件必须继续可读；新增键放进合适的段（`layout` / `settings` / `window` / 每本书），
  不扰动其他段的内容。
- 阅读位置存 `(section, block, char_offset)`，不存页码 / 像素（ADR-011）。
