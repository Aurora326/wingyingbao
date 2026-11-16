# 上海银行企业网银采集

按建设银行 `ccb_automation` 的结构搭建，覆盖 **银行流水、电子回单、批量转账制单**。
平台契约已经对齐：`collectors.yaml` 里加了 `bosh`，账户 `bank_code = bosh` 就能建采集任务；制单走 `draft_command`。

## 目录

| 文件 | 作用 |
| --- | --- |
| `run_task.py` | 采集入口，读 `--task task.json` 写 `result_path` |
| `run_draft.py` | 制单入口，把平台付款文件转成上海银行导入文件并执行导入 |
| `main.py` | 主流程：机柜插盾 → 启动浏览器 → 登录 → 采集/制单 → 拔盾 |
| `browser.py` | 启动参数、登录页导航 |
| `login_manager.py` | 登录流程、证书弹窗、登录成功判断 |
| `account_detail.py` | 账户明细页公共操作；导出弹窗的确认按钮、日期区间等共享函数 |
| `statement_collector.py` | 流水采集：导出明细 Excel |
| `receipt_collector.py` | 回单采集：导出回单 |
| `payment_creator.py` | 批量转账制单：生成导入文件、上传、核对导入结果 |
| `config.ini` | 登录地址、机柜、下载路径、制单参数（本机配置，不入库） |

## 采集调试顺序

1. 把 U 盾插到对应机柜端口（或 `manual_mode = true` 手动插），`config.ini` 里填登录密码和 U 盾 PIN。
2. 跑 `python main.py -s`（仅流水）或 `python main.py -r`（仅回单）。
3. 登录页元素在 `login_manager.py`；明细页与导出按钮在 `account_detail.py`、`statement_collector.py`、`receipt_collector.py`，页面改版时需要重新取选择器。
4. 键盘输入统一走 AiKeyboard，输入前会校验系统焦点确实落在页面里，避免密码打进地址栏。

## 制单

- 平台付款协同生成批量 Excel（列：序号、申请编号、付款单位、付款账户、收款单位、收款账号、收款开户行、金额、币种、用途、项目）。
- `run_draft.py` 读取后按 `template/上海银行批量转账导入文件模板.xls` 生成导入文件，流程：打开批量转账新增申请页 → 校验付款方账号 → 点「导入文件」上传 → 读取“共导入 N 条，错误 M 条”。
- `save` 模式：导入完成后停下，不提交，供人工复核。
- 已知待补：
  - 付款方账号切换：页面默认账户与任务账户不一致时会直接报错停下，尚未实现下拉切换。
  - 导入出现行错误时会点「自动匹配开户行信息」，弹窗里的「选择全部 → 采纳」尚未自动点击；源头开户行信息规范后一般不会出现该弹窗。

## 平台侧

- 采集器清单：`platform/collectors.yaml` 里的 `bosh` 条目（含 `draft_command`）
- 账户：`bank_code = bosh`，绑定对应 UKey 设备
- 采集任务：`python run_task.py --task <task.json>`
- 制单任务：`python run_draft.py --task <task.json>`
