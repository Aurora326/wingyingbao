# 备份与恢复

## 备份什么

每次备份生成一个目录，包含：

- `app_<时间戳>.dump`：PostgreSQL 自定义格式的完整数据库备份
- `artifacts/`：流水、回单等归档文件（按大小增量复制）

备份目录默认是数据目录下的 `backups/<时间戳>/`。容器部署时 `backups` 已挂载到宿主机 `platform/backups/`，容器或数据卷损坏时备份仍可取回。

## 自动备份

平台调度器每天 02:30 触发一次自动备份，保留最近 14 天，过期目录自动清理。结果会写入平台设置，在「平台管理 → 系统维护 → 数据备份」可以看到最近一次备份的时间和大小。

调度器未启用（`PLATFORM_ENABLE_SCHEDULER=0`）时不会自动备份，需要外部计划任务调用命令行。

## 手动备份

页面：平台管理 → 系统维护 → 数据备份 → 立即备份。

命令行（在 `platform/` 目录、能访问数据库的机器上）：

```bash
python backup.py
```

指定目录或保留天数：

```bash
python backup.py --target /path/to/backups --keep-days 30
```

列出已有备份：

```bash
python backup.py --list
```

容器内执行：

```bash
docker exec bank-platform python backup.py --list
```

## 恢复

恢复会删除并重建现有数据库对象，只应在确认备份文件可用后执行。操作前先停止写入，避免恢复期间产生新数据。

1. 停止平台容器：

```bash
cd platform && docker compose stop platform
```

2. 确认备份文件存在并检查大小：

```bash
python backup.py --list
```

3. 执行恢复（宿主机需安装 PostgreSQL 客户端；也可以进入容器执行）：

```bash
python backup.py --restore backups/20260101_023000/app_20260101_023000.dump
```

4. 启动平台并检查：

```bash
docker compose start platform
docker compose logs -f platform
curl http://127.0.0.1:8800/health
```

5. 登录平台，核对账户数量、最近流水、付款单据和审计日志。

## 恢复演练

建议每季度做一次演练，不要等到真出事才第一次使用恢复流程：

1. 在另一台机器或临时数据库上恢复最新备份。
2. 对比关键表行数：`bank_account`、`statement`、`receipt`、`payment_doc`、`alert_event`。
3. 检查归档文件能否打开。
4. 记录演练时间、耗时、发现的问题。

## 注意

- 备份文件包含账户与付款数据，按敏感资料管理，不要放进 Git 或公共网盘。
- 备份在宿主机本地目录，建议再同步到另一台机器或对象存储；同一块磁盘损坏时两者会一起丢。
- 恢复命令不校验目标库是否为空，执行前确认连接的是正确的数据库。
