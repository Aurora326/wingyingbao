# 建行采集重构 Step 1 计划：零风险公共能力抽取与历史文件清理

范围：`建设银行/ccb_automation`、`common/`，不改登录、PIN、制单提交与页面交互顺序。

## 一、现状基线（只读核对结果）

| 位置 | 现状 | 行数 |
| --- | --- | --- |
| `statement_collector.py` | `_cleanup_old_archives` 189-252 | 64 |
| `receipt_collector.py` | `_cleanup_old_archives` 107-170 | 64（与流水仅差 config 取法和“流水/回单”字样） |
| 两采集器 | `_to_ccb_date` 完全相同 | 各 3 |
| 两采集器 | `_set_date_field_ccb` 完全相同 | 各 20 |
| 两采集器 | `_find_frame_containing`：流水 28 行（可见优先+match_all），回单 13 行（首个命中即返回） | 28 / 13 |
| 两采集器 `__init__` | PyInstaller 下载目录判断、归档目录创建 | 各约 12 |
| `payment_creator.py` | `DEFAULT_TEMPLATE_MAPPING` + `_load_template_mapping` + `_mapped_value` + `_format_amount`/`_amount_decimal` + `_generate_batch_import_file` | 约 190 |
| `payment_creator.py` | `_iter_contexts` / `_interact_text_in_context` / `_click_text_in_context` / `_click_button_by_texts` | 约 66 |
| `build_exe.py` | 未列入 `pack_plugin` 的排除名单，会作为源码打进插件 zip | 255 |
| 现有公共层 | `common/download_helper.py` 只有下载轮询；`common/collect/archive.py` 的 `cleanup_old_archives` 用 `retention_months*31` 天近似，和建行“逐月减”的语义不同，直接替换会改变删除日期 | — |

## 二、目标产物与接口

### 1. `common/download_helper.py`（扩展，不破坏现有函数）

```python
def resolve_browser_download_dir(base_dir: Path) -> Path
    # frozen → Path(sys.executable).parent/"downloads"；否则 base_dir/"downloads"；mkdir 后返回

def ensure_archive_dir(download_root: Path, archive_by_month: bool, now=None) -> Path
    # archive_by_month → download_root/YYYY/MM，mkdir；否则返回 download_root

def cleanup_old_archives(download_root: Path, retention_months: int,
                         label: str = "采集", now=None) -> int
    # 与建行现有实现逐月相减完全一致；日志保留“清理 N 个月前的{label}归档数据，截止日期: YYYY-MM”
```

注意：`base_dir` 由调用方传入自己的 `__file__` 目录，不能用 `common` 自身路径，否则开发环境下载目录会指错。

### 2. `common/frame_helper.py`（新增）

```python
def iter_contexts(page, include_page=True) -> Iterator[tuple[str, Any]]
    # 产出 ("page", page), ("frame[0]", frame) ... 与 payment_creator._iter_contexts 完全一致

def find_frame_containing(page, selectors, timeout=10.0, match_all=False,
                          prefer_visible=False, poll=0.5)
    # prefer_visible=True → 流水语义：先找可见元素，找不到回退首个命中
    # prefer_visible=False → 回单语义：首个包含选择器的 frame 立即返回
    # match_all=True → 要求所有选择器同时存在（流水 Step 4 / 日期页复用）

def set_input_value(frame, selector, value, wait_visible=True, settle=0.3)
    # 原样搬 _set_date_field_ccb 的 JS：去 readonly/disabled + focus/input/change/blur

def click_text_in_context(context, text, action="click", exact=True) -> dict
    # 原样搬 _interact_text_in_context 的 JS（返回 {clicked/hovered/tag/text/reason}）

def click_button_texts(page, texts, preferred_context=None, preferred_label="", delay=3.0) -> bool
    # 原样搬 _click_button_by_texts：locator 优先、JS 兜底、日志文字不变
```

只依赖鸭子类型，不 import Playwright，保持可离线测试。

### 3. `建设银行/ccb_automation/batch_template.py`（新增，纯逻辑）

```python
DEFAULT_TEMPLATE_MAPPING
class TemplateError(RuntimeError)
def format_amount(value) -> str
def amount_decimal(value) -> Decimal
def load_mapping(mapping_file: Path) -> dict          # 文件缺失/格式错 → 默认映射 + warning
def mapped_value(item, column_cfg, row_index, config: dict) -> str
def generate_import_file(template_path: Path, output_path: Path, items: list,
                         mapping_file: Path, config: dict) -> Path
```

`generate_import_file` 保留现有全部细节：临时副本绕 Windows 句柄、`xlrd(formatting_info=True)`、`xlutils.copy`、清空到 3002 行/15 列、逐行写入、`release_resources` 后保存、总金额日志。

`payment_creator.py` 保留同名薄包装（`_load_template_mapping`/`_mapped_value`/`_generate_batch_import_file`），内部调用 `batch_template`，`create_all()` 不改调用顺序。

### 4. 采集器改造

- `statement_collector.__init__`：归档目录、PyInstaller 下载目录、旧归档清理改为调用 `download_helper` 三个函数（保留原日志文字，label="流水"）。
- `receipt_collector.__init__`：同上（label="回单"），`debug_dir` 保留。
- 两采集器删除本地 `_cleanup_old_archives`、`_to_ccb_date`、`_set_date_field_ccb`；`_find_frame_containing` 改为一行委托并保持原签名（流水 `prefer_visible=True`，回单 `prefer_visible=False`），所有调用点不动。
- `_work_frame`、`match_all` 调用、5 秒/10 秒/15 秒超时参数保持不变。

### 5. `payment_creator.py` 辅助函数收口

- 删除 `_iter_contexts`、`_interact_text_in_context`、`_click_text_in_context`、`_click_button_by_texts`，全部调用点改调 `frame_helper`；日志文字保持一致。
- `_click_batch_file_entry`、`_select_uploaded_success_rows`、`action=save` 分支不动。

### 6. 历史文件

`build_exe.py` 移到 `建设银行/tools/build_exe.py`（插件目录不再打包它）。该脚本内部按自身目录推导路径，归档后不再维护；若运维确认完全不用，可直接删除。

### 7. 测试（仓库根新增 `tests/`，离线运行，不依赖 PostgreSQL）

`tests/conftest.py`：把仓库根、`建设银行/ccb_automation` 加入 `sys.path`；不加载 `platform/tests/conftest.py`。

- `tests/test_frame_helper.py`：用 FakePage/FakeFrame 覆盖 `iter_contexts`、`match_all`、可见优先/首个命中差异、`set_input_value` 事件派发、`click_text_in_context` 命中最小可见元素、`click_button_texts` 的 locator→JS 降级。
- `tests/test_download_archive.py`：`resolve_browser_download_dir` 的 frozen/开发分支；`ensure_archive_dir` 两种模式；`cleanup_old_archives` 逐月边界（1 月→12 月）、保留 0 个月、空目录清理、日志 label。
- `tests/test_batch_template.py`：用 `xlwt` 在 tmp 生成模板，验证默认映射、外部 mapping 载入失败回退、金额格式化（`9010`→`9010.00`、带逗号）、`_seq`、`default_config` 取值、3002 行清空、生成文件表头与数据行。

运行方式：`python -m pytest tests -q`（项目根）。

## 三、行数预估

| 文件 | 改前 | 改后（预估） |
| --- | --- | --- |
| statement_collector.py | 774 | ~690 |
| receipt_collector.py | 704 | ~620 |
| payment_creator.py | 1583 | ~1390 |
| common/download_helper.py | 147 | ~225 |
| common/frame_helper.py | 0 | ~170 |
| batch_template.py | 0 | ~210 |
| build_exe.py（插件内） | 255 | 0（移到 tools/） |
| 新增测试 | 0 | ~300（不计产品代码） |

## 四、行为等价红线

1. `find_frame_containing` 两种语义不能合并成一种：流水需要“可见优先 + 全部选择器同时存在”，回单是“首个命中立即返回”。
2. `cleanup_old_archives` 必须逐月相减，不能改用 `retention_months*31` 天近似。
3. `set_input_value` 的事件顺序（focus→input→change→blur）与 0.3 秒等待必须保留。
4. `batch_template` 的 3002 行清空、15 列、临时副本、`release_resources` 顺序必须保留；制单 save 模式回归以生成文件字节数为参照（允许时间戳差异时对比行列内容）。
5. 不修改登录管理器、PIN 输入器、制单导航/勾选/提交逻辑。
6. `run_task.py` / `run_draft.py` / `manifest.json` / `collectors.yaml` 契约不动。

## 五、风险与部署注意

- **common 部署形态**：客户端插件运行时从 `plugins/common/releases/<version>` 取公共模块（`plugin_manager.py:264-269`），当前仓库没有 common 打包脚本。本次新增 `common/frame_helper.py` 后，真机插件包需要同步更新 common 版本，否则客户端 `import common.frame_helper` 会失败。开发机走仓库根目录不受影响；Step 1 实施时先确认 common 的发布流程，必要时补一个 common 打包脚本（不在本 Step 代码范围内，单独确认）。
- **build_exe 移动**：若柜台仍有离线 exe 使用习惯，移动后原路径脚本不可用；先确认。
- **测试目录**：放在仓库根 `tests/`，不会进入插件 zip；`platform/tests` 依赖 PostgreSQL，不复用。

## 六、手工回归清单（Step 1 完成后）

1. 流水：真实建行 UKey 跑一次 statements（如任务 253 同区间），确认文件名、归档目录（`downloads/statements/YYYY/MM`）、日志中“清理…流水归档数据”、frame 定位与日期设置正常。
2. 回单：跑一次 receipts（有数据区间），确认 PDF 生成、`receipt_preview.png` 位置、归档与清理日志（“回单”字样）正常。
3. 制单：`action=save` 跑一次 draft，确认导入文件生成、页面停在下一步前、截图与 HTML 正常；**不执行 submit**。
4. 打包：`python platform/scripts/pack_plugin.py 建设银行/ccb_automation --private-key ...` 对比 zip 文件清单，确认不含 `build_exe.py`，公共模块未被打入插件。
5. 日志抽查：`find_frame_containing` 相关日志、`已点击按钮`、`已通过 JS 点击按钮` 文字与改动前一致。

## 七、已确认决策

1. `build_exe.py` 直接删除（git 可恢复），插件目录不再保留打包脚本。
2. common 本次只保证仓库与开发机运行；真机 common 的打包/版本发布流程后续单独处理。
3. 离线单元测试放在仓库根 `tests/`，独立 conftest，不依赖 PostgreSQL。
