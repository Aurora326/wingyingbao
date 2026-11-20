# 建设银行采集向上海银行样板看齐：分阶段方案

上海银行 `bosh_automation` 是当前约定的黄金样板；建设银行 `ccb_automation` 约 6000 行、长期排障积累，不能整体重写。本方案按“先契约、后结构、最后高风险链路”的顺序推进，每阶段都可独立上线和回滚。

## 任务 253 成功日志给出的裁剪依据

任务 253（ccb，statements，2026-10-21~11-20）在 171 秒内成功走完。日志证明的真实主路径：

- 登录：在 frame `NECV6B2BMainPlat_08` 里找到 `#LOG_PWD`，点击 `#LOGIN_BOX_BTN`；PIN 原生窗口处理 1 次；登录成功判定由“密码框存在但不可见”得出。
- 采集：首页 → 账户查询 → 全选账户 → 交易明细；日期 JS 写入 `#StDt/#EdDt`，在 frame `NECV6B2BMainPlat_08_FW` 点 `input[name=Submit4]`。
- 下载：`#dlAll` hover → JS 点 `<li onclick="submitSelect(...)">Excel下载</li>`；`expect_download` 60 秒未捕获，**文件轮询兜底实际救了这次任务**，从 `common/downloads` 取到 GUID 临时文件后按文件头改名为 .xls。

因此裁剪原则不是“删掉所有兜底”，而是按日志分三类：

- **A 已被证明必需**：frame 扫描定位控件、PIN 原生窗口、下载文件轮询、文件头判格式。保留并固化为主路径。
- **B 每次都执行但从未命中**：`_dismiss_browser_ui_panels` 在“未检测到弹窗”时仍盲点 (320,65)(310,70)(330,60)(326,64)(307,70) 五个坐标；`_check_login_success` 登录后连跑两遍、每次几十条 selector 全 count=0；`navigate_to_login` 在每个阶段都探测一次“立即登录”并告警。这些是噪音和误操作风险，应删除或默认关闭。
- **C 只有失败场景才触发**：证书弹窗、旧版立即登录页、中间登录页、空数据页、USBKey 确认框、交易签名 PIN。成功日志天然看不到它们，不能因为“这次没用到”就删；做法是保留但收敛为“主路径失败后一次性升级”，并加显式日志和截图，而不是静默多策略重叠。

## 不能动的部分


以下能力是建行特有，任何“向样板看齐”都不能删：

- 原生 U 盾 PIN 窗口整条链路（`pin_inputter.py`），以及 PIN 错误识别后停止重试的防锁盾逻辑。
- 证书弹窗的多级 Win32 兜底、`--auto-select-certificate-for-urls`、400 SSL 证书未识别分支。
- iframe/多窗口密码框发现（`_find_password_input_context`、`_iter_contexts`）。
- 旧版「立即登录」页提交路径与中间登录页分支。
- 营销弹层、公告子窗口、USBKey 确认框、对账弹窗的关闭能力要保留；但“未检测到弹窗仍执行”的固定坐标盲点属于 B 类，应删除，只在探测命中后按窗口矩形点击。
- 制单 15 列模板映射、行别 01/02、临时副本绕 Windows 句柄、批量名称、行内校验、save/submit 双模式。
- 回单“勾选 → 电子回执打印/预览 → 回执打印 → 一页打2个 → page.pdf()”链路，以及 PDF 必须保持可提取文本。
- 下载文件真实格式探测（xls/xlsx/html）、归档目录与保留期清理、expect_download 失败后的目录轮询。
- 平台错误码契约：若保留 `_无数据_` 标记文件，run_task 的 no_data 判定不要同时改成哨兵，二选一并统一。

## 第 1 阶段：结果契约与健壮性（低风险，先做）

1. 空数据契约：让流水/回单的空结果稳定变成 `ok=true, no_data=true`。现在流水写 `_无数据_` 标记但 `collect()` 按文件名过滤掉，run_task 收到空列表后 no_data=False；二选一——保留标记并让 run_task 识别，或改为返回“当前查询结果为空”哨兵，两者不能一边删一边仍过滤。
2. `_check_has_data` 拆分两种失败：结果页根本没出现 = 异常；DOM 空态容器可见 = 无数据。空态选择器按真实空页面抓一次，不默认“找不到 frame 就当有数据”。
3. 制单结果结构化：`_finish_batch_action`/`create_all` 统一返回 `(ok, message)`，所有失败路径带“失败:”前缀；修复“下一步点击失败”被 run_draft 当成功回传的缺陷。
4. 制单截图归属：`_save_debug_snapshot` 返回本次路径，`create_all` 增加“截图”键；删除 run_draft 按 mtime 扫 `debug/*.png`。
5. run_draft 对齐样板：把 task 的 `payer_account_no/payer_name` 写进 payments 配置；平台任务默认 `action=save`，不允许 config.ini 的 submit 直接生效。
6. 采集健壮性小修：账户全选后校验 `is_checked()`；日期写后回读；`controller.connect()` 返回 False 时抛错而不是继续起浏览器；`finally` 里 `browser.close()` 包 try/except；`print_results` 按 dict 打印 payments。
7. 登录提交去重：删除 `browser.navigate_to_login` 里的“查找密码框+提交”段，统一走 `main → login()`；保留新版/旧版两条分支和中间登录页，PIN 错误不自动重试。

验证：空区间 statements/receipts 各跑一次（期望 no_data=true、水位不推进）；有数据区间确认入库与水位；draft save 正常/失败各一次（平台任务 review、失败退回可制单、截图是本次的）；日志中登录只提交一次。

## 第 2 阶段：页面对象与登录加固（中风险）

1. 新建 `建设银行/ccb_automation/account_detail.py`（CCBStatementPage）：迁入 frame 查找、广告弹窗关闭、导航、日期设置、查询提交、空数据判断，提供 `open()/query()/has_data()`；流水与回单共用，先“只搬不重写”，保留 `_work_frame` 时序、Submit4→Submit1 顺序和 frame 诊断日志。
2. 删除回单采集器里的重复实现与死代码（`_ensure_on_result_page`、`_set_date_field_ccb`、`_set_date_field_by_playwright`、未调用的 `_has_data` 等），预计减少 300+ 行。
3. 登录安全输入渐进接入：把样板 `_browser_hwnd/_force_foreground/_focus_input/_fill_security_field` 抽成 `common/secure_input.py`，建行只在新版证书表单分支 opt-in；旧版“立即登录”路径保留现有兜底。`_focus_input` 的 DOM 校验必须在密码框所在 frame 内执行。
4. 弹窗盲操作先加前台校验：按 PID/窗口标题确认前台是浏览器后再发送 Tab/Enter/Esc 或坐标点击；固定 1920x1080 坐标改为按浏览器窗口矩形换算，旧兜底保留。
5. 清理抢焦点浏览器：插盾前快照 chrome/edge PID，10 秒后只关闭新增进程；按标题关“建设银行”官网窗口先只记录日志，观察一周再默认启用。

验证：100%/125%/150% DPI 与多屏下各登录一次，日志记录前台窗口/hwnd/activeElement/native_focus；旧版页面仍能登录；人为切走焦点时 fail-closed 且密码未进地址栏；流水/回单/制单三条任务各回归一次，比较重构前后文件名、行数、日期区间和 frame 日志。

## 第 3 阶段：制单与 U 盾统一（高风险，平台配合）

1. 制单导入门禁：只放在 submit 分支（“上传成功记录”页在下一步之后才出现）；save 模式用“文件已上传/已到导入页”的轻量确认，避免白等 90 秒。
2. 制单成功/未知语义：机械臂按压后等待“提交成功/交易成功”文本；读不到时不要默认成功。平台需先支持 `SUBMIT_UNKNOWN → timeout_review` 且不释放单据，否则改判定会带来重复付款风险。
3. 交易密码返回值纳入门禁：`_fill_transaction_password_and_confirm()` 失败时不得继续点确认、输 PIN、按 OK。
4. U 盾 PIN 统一：先抓取交易签名阶段真实窗口标题补进 `PIN_TITLES`，再把 `payment_creator` 的 pygetwindow 盲输入改为复用 `pin_inputter`；保留 `_find_pin_error_dialog`、CapsLock/IME 处理、物理 OK 三次重试。
5. 制单导航：人工确认批量文件录入页真实 URL 后，`goto` 直达作为第一策略，菜单 hover/坐标链保留兜底；观察直达成功率后再决定是否收缩兜底。
6. save 语义确认：建行当前停在“下一步前”供出纳改批量名称；是否改成和样板一样停在确认页，需要出纳确认后再动。

验证：draft save 正常/失败；伪造 `SUBMIT_UNKNOWN` 确认平台进 timeout_review 且单据不释放；submit 在测试环境跑一笔，确认交易密码失败时不会继续；U 盾任务期间打开记事本，确认没有按键落错窗口。

## 第 4 阶段：公共层与交付清理（最后）

1. 两家 `run_task.py` 的 `_apply_task`、结果组装抽到 `common/collect/contract.py`，提升插件 `min_agent_version` 并保留一版内联兜底。
2. 样板与建行的 `click_visible_text/click_confirm/date_range` 收敛到 common，建行保留 iframe/格式参数。
3. `build_exe.py`、`run_recorder.py`、`records/` 在运维确认不再使用后移除；`build_exe` 的 frozen Chromium 路径当前实际不可用。

验证：旧 Agent 上跑三种任务各一次；插件签名与 manifest 版本正确；删除后插件包不含 exe/records；上海银行样板无回归。

## 执行建议

- 第 1、2 阶段可以先做，收益明确且不动资金链路。
- 第 3 阶段前先和出纳确认 save 的定义，并让平台支持 `timeout_review`，否则不要改提交成功判定。
- 每阶段结束跑一遍基线任务并保存 result.json、平台任务详情、日志与截图，作为下一阶段对比依据。
