# 银行采集程序接入契约

平台调度 UKeyAgent 执行各银行的采集程序。银行程序只需要遵守本文的入口、任务文件和结果文件约定，平台不关心页面细节。

## 注册采集程序

`platform/collectors.yaml`（本机配置，不入库，结构见 `collectors.example.yaml`）：

```yaml
bosh:
  command: ["python", "run_task.py"]        # 采集任务入口
  draft_command: ["python", "run_draft.py"] # 制单任务入口，没有制单能力可省略
  cwd: C:\wangyingbao\上海银行\bosh_automation
```

Agent 只按 `bank_code` 查这张表，路径和 Python 解释器由本机决定。

## 采集任务

Agent 把任务写成 `task.json`，以 `python run_task.py --task <task.json>` 调用。

任务文件主要字段：

| 字段 | 说明 |
| --- | --- |
| `task_id` / `attempt` | 平台任务编号与尝试次数 |
| `type` | `statements` / `receipts` / `reconciliation` |
| `bank_code` / `account_no` / `account_name` | 账户信息 |
| `start_date` / `end_date` | 查询区间（YYYY-MM-DD） |
| `device_backend` / `ter_id` / `gf3_port` / `gf3_cabinet` | UKey 机柜参数 |
| `ter_id` / `mb_user` / `mb_exe` / `gf3_exe` | 本机机柜程序参数 |
| `work_dir` / `profile_dir` / `archive_root` / `log_path` | 本次运行的工作目录 |
| `result_path` | 结果文件写到哪里 |

**登录密钥不写进任务文件**，由 Agent 通过环境变量传入：`BANK_USERNAME` / `BANK_PASSWORD` / `BANK_PIN`。

结果写回 `result_path`：

```json
{
  "contract_version": 1,
  "task_id": 244,
  "ok": true,
  "files": ["C:\\...\\上海银行_银行流水_2026-10-14_2026-11-13.xlsx"],
  "no_data": false,
  "error_code": "",
  "message": ""
}
```

- `files` 指向下载到本机的文件，Agent 会上传为平台产物并解析入库。
- 查询结果为空时 `ok: true` 且 `no_data: true`。
- 失败时 `ok: false`，`error_code` 用短码（如 `COLLECT_FAILED`），`message` 写人能看懂的原因。

## 制单任务

Agent 以 `python run_draft.py --task <task.json>` 调用。任务文件比采集任务多：

| 字段 | 说明 |
| --- | --- |
| `file_path` | 平台生成的付款 Excel（序号、申请编号、付款单位、付款账户、收款单位、收款账号、收款开户行、金额、币种、用途、项目） |
| `batch_no` / `doc_count` / `amount` | 批次信息 |
| `payer_account_no` / `payer_name` | 付款账户 |
| `result_path` / `work_dir` | 结果与工作目录 |

结果格式：

```json
{
  "contract_version": 1,
  "ok": true,
  "error_code": "",
  "message": "批量转账: 成功: 已导入 3 笔，待人工复核",
  "bank_batch_no": "",
  "files": []
}
```

`files` 可放截图等辅助文件。`ok: true` 时对应制单任务的单据转为「已制单」；失败会把单据退回「可制单」，可以改完再发。

## 公共约定

- 键盘输入统一走 `common/keyboard_helper.py`（AiKeyboard），不要用 `keyboard` 等注入库。
- 机柜控制统一走 `common/cabinet_controller.py`，类型支持 `mb` / `gf3` / `mock` / `other`。MB 控制器插入后会校验端口是否真正共享；仍不成功会重启 `MbiCltSvr` 客户端服务再试一次（重启电脑后“插入成功但不识别”的常见故障）。
- 配置加载统一走 `common/config_loader.py`，平台下发的区间、密钥、机柜参数由各银行 `run_task.py` 覆盖到老配置上。
