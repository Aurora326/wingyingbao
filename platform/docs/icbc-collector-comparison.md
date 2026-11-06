# 工商银行采集器：两套实现对比

对比对象：

- **旧套**：`工商银行/icbc_automation/main.py` 及其 `browser.py`、`statement_collector.py`、`receipt_collector.py` 等，当前平台入口 `run_task.py` 实际调用的就是这套。
- **新套**：`driver.py`（`IcbcDriver`）+ `profiles/icbc.yaml` + `common/collect/*` 通用框架。目前没有任何生产入口调用它，只有测试引用。

> 2026-11-06：新套已整体归档到 `_archive/new_driver/{icbc,citic,cib}/`，冻结不再维护；生产只保留旧套入口。下文中的新套路径均指归档前的位置。

数据来源：文件逐一阅读、`wc -l` 统计、`platform/data/app.db` 的历史任务与运行记录、`pytest` 实跑结果。

## 1. 来历（git 时间线）

| 日期 | 提交 | 事件 |
|---|---|---|
| 10-29 | `8f7aa0a` / `fc4d8a5` / `b11e244` / `f9e5d94` | 新套最初是平台内建驱动，后移到各银行项目；入口 `run_task.py` 调用 `common.collect.run_task.main(IcbcDriver, ...)` |
| 10-29 | 真机运行 | 新套在真机上跑通流水采集：任务 22/25/26/27/32/33/37/41/42/43/45 成功，任务 45 下载的 xlsx 已归档并被平台解析入库 |
| 10-30 | `c0fc232` | 纳入客户侧旧自动化源码（main.py/browser.py 等），`run_task.py` 改为 `from main import ICBCAutomation`，此后生产入口全部走旧套 |
| 10-30 | 真机运行 | 旧套：任务 46 第 5 次尝试成功（4 个 xlsx 入库），任务 47/48/49 均登录失败 |
| 11-03 | `f7335f7` | 对新套补测试与回单参数修复，但入口仍未切回 |

结论：**不是简单的"新旧并存"，而是新套先跑通、第二天被旧套替换了入口**。

## 2. 代码量（实测，单位：行）

### 旧套（入口实际执行）

| 文件 | 行数 | 说明 |
|---|---:|---|
| run_task.py | 99 | 平台契约封装 |
| main.py | 271 | 流程编排、重试、记录 |
| browser.py | 1050 | 浏览器启动、登录、弹窗、PIN |
| statement_collector.py | 761 | 流水导航/查询/下载/归档 |
| receipt_collector.py | 563 | 回单（从未真机运行） |
| config_loader.py | 330 | ini/json/yaml 配置 |
| logger.py | 71 | loguru 封装 |
| run_recorder.py | 178 | 运行记录 JSON |
| cabinet_controller.py | 5 | 转发 common |
| **合计** | **3328** | 另有无引用死代码 393 行（login_manager 222、popup_handler 62、pin_inputter 109） |

### 新套

| 文件 | 行数 | 说明 |
|---|---:|---|
| driver.py | 840 | 工行专属全部逻辑 |
| profiles/icbc.yaml | 125 | 选择器、坐标、超时参数 |
| **工行专属合计** | **965** | 约为旧套的 29% |
| common/collect/* | 1521 | 所有银行共享：base/browser/ui/flow/download/archive/result/profile/pin/gf3/mb/run_task |

旧套额外依赖公共模块（`common/cabinet_controller` 527、`common/windows_ui` 515、`common/keyboard_helper` 332），新套依赖前两者和 `common/collect`。**按每新增一家银行计算：旧套约 3300 行/家，新套约 1000 行/家。**

## 3. 功能逐项对比

| 能力 | 旧套 | 新套 |
|---|---|---|
| 浏览器启动 | 自带逻辑；参数含 `--auto-select-certificate-for-urls`（自动选 UKey 证书）、`--kiosk-printing`、`--ignore-certificate-errors`；扩展检测 | 通用 `BrowserSession`；扩展解包/已安装判断、残留 Chrome 清理更完整；**没有证书自动选择参数**（可加进 profile `extra_args`） |
| 证书弹窗 | Win32 双击证书行 + 确定；Chrome UIA 兜底；失败不回退 | 相同逻辑；找不到时**额外回退按 Enter**（有取消证书弹窗的风险） |
| U盾 PIN | 切窗口级英文布局（WM_INPUTLANGCHANGEREQUEST）+ 最多 3 次点击并校验 Edit 焦点（GUITHREADINFO）后才输入；失败不提交 | 点击/聚焦一次 + 清空 + AiKeyboard，失败退 pyautogui；只按 Shift 尝试英文；**无焦点校验** |
| 密码错误提示 | 识别「用户提示」，选否/取消，终止不重试 | 相同 |
| 登录态判定 | 元素 + URL 启发式 | 元素 + 连续两次确认为准，逻辑更严格 |
| 首页广告遮罩 | 关闭 | 相同 |
| 菜单导航 | 菜单点击 + 直接 URL 兜底 | 菜单点击 + iframe 定位；**无 URL 兜底** |
| 日期设置 | 键盘输入 + JS 写 datepicker 内部状态 + 隐藏字段 | 键盘输入 + JS 隐藏字段兜底（datepicker API 函数写了但未调用） |
| 查询结果判断 | 等表格渲染 + 无数据文案 | 轮询行数 + 无数据文案 |
| 流水下载 | `detailDownXlsx()` 直调；GUID 无后缀文件识别；**只有表头时重试一次** | 同样直调 + 多帧/多页点击 + HTML 下载重试；**只有表头时直接判为空，不重试** |
| 下载失败排查 | 仅日志 | 截图 + 逐帧按钮探针 + 页面文本转储 |
| 回单下载 | 「电子回单」页 → 全选 → 直接下载；代码含 TODO，**从未真机运行** | 明细查询页 → 回单 PDF → 「下载设置」确定 → 客户服务/文件异步下载轮询取件；**排除当天**（工行限制）；同样未真机验证 |
| 多账户 | 读取账户下拉但从不切换账户，循环下载同名文件互相覆盖（实际无效） | 单账户/任务，与平台"一账户一任务"模型一致 |
| 归档命名 | `工商银行_银行流水_YYYYMMDD.xlsx`，多账户会覆盖 | `工商银行_银行流水_YYYYMMDD_账号.xlsx`，按月归档、保留期、无后缀文件类型嗅探 |
| 错误码 | 统一 `COLLECT_FAILED` | `LOGIN_FAILED` / `SELECTOR_NOT_FOUND` / `DOWNLOAD_FAILED` / `TIMEOUT` / `NOT_IMPLEMENTED` 等，平台可按码决定是否重试 |
| 重试 | 内部 `max_retries`（配置默认 1）+ 表头重试 | 交给平台/Agent 的 3 次租约重试；内部只对 HTML 下载重试 |
| 关闭浏览器 | 先关闭错误/密码/证书弹窗，8 秒看门狗杀 Chrome | 直接关闭；**无弹窗清理、无看门狗**，弹窗卡死时可能拖到平台 900 秒超时 |
| 配置与密钥 | `config.ini` 明文存 PIN；平台运行时用环境变量覆盖 | profile YAML 只存选择器/参数；凭据只从任务环境变量取 |
| 运行记录 | `records/*.json` | 无，依赖平台 TaskRun 日志 |
| 手动运行 | `python main.py -s / -r` | 需构造 task.json 或用平台 |
| 测试 | 无 | 单测 27 过 / 4 失败（3 个因缺样本文件，1 个旧导入路径）；模拟端到端 5 过 / 2 跳过（下载与回单"待真机验证"） |
| 真机证据 | 10-30 流水成功 1 次（试了 5 次），回单 0 次 | 10-29 流水成功 11 次（其中 1 次真实文件入库），回单 0 次 |

## 4. 新套相对旧套的工程性回退（切换前应补）

1. **PIN 输入安全**：缺旧套的焦点校验和窗口级英文布局切换，中文输入法下可能把 PIN 打错或打到别处。
2. **浏览器关闭兜底**：缺弹窗清理和 8 秒看门狗，弹窗模态卡死时只能等平台超时。
3. **证书自动选择**：旧套启动参数 `--auto-select-certificate-for-urls` 未迁入（可在 profile 的 `extra_args` 补）。
4. **表头-only 重试**：旧套会等待后重导一次，新套直接判空，实测 11-02 日志里这个分支真实触发过。
5. **导航 URL 兜底**：旧套在菜单点击失败后可直连 URL，新套没有（profile 里 `statement/receipt` URL 也是空的）。
6. 回单链路新套同样未经真机验证，只是流程细节更贴近实际页面（`button#pdf`、下载设置、异步下载）。

## 5. 旧套的硬伤

- 每加一家银行要复制 3000+ 行，公共修复无法共享。
- 回单代码是模板级实现（TODO、通用选择器），从未跑通。
- 多账户循环实际不切换账户，文件名互相覆盖。
- 明文 PIN 存在 `config.ini`。
- 错误码单一，平台无法区分"登录失败"和"下载失败"，重试策略只能一刀切。
- 零测试。

## 6. 建议

**方向：以新套为准，旧套保留一个验证周期后删除。** 不建议现在就删任何一套，也不建议继续在旧套上加功能。

落地步骤（建议一次真机窗口完成）：

1. 把第 4 节的 5 个加固点补进 `driver.py` / `common/collect`（PIN 焦点校验与布局切换、关闭看门狗、证书自动选择、表头重试、URL 兜底）。
2. 增加入口开关：新 `run_task.py` 默认调 `IcbcDriver`，旧的改名 `run_task_legacy.py`；`collectors.yaml` 或任务 `options` 可按需回退。
3. 真机各跑 3 次流水 + 3 次回单，核对归档文件、平台解析结果、UKey 是否正常归还。
4. 稳定 1～2 周后删除旧套（约 3300 行有效代码 + 393 行死代码）和 `config.ini` 中的明文凭据。
5. 新套补两条端到端测试（下载、回单）后，去掉测试里的 `DOWNLOAD_SKIP`。

如果近期有交付压力、不能安排真机验证，则**暂时维持旧套跑生产，但停止在旧套上新增功能**，把修复同步到新套，等验证窗口再切。
