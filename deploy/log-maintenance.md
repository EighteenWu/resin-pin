# panso 日志与缓存维护

这是宿主机运维工具，与 resin-pin 网页进程独立。源码由本仓库管理，不需要构建镜像或重启业务容器。

## 保留策略

- 每 15 分钟检查 worker、scheduler 的 PM2 日志。使用系统 logrotate，按日或超过 20 MiB 时轮转，压缩历史文件，最多保留 14 份、最长 14 天。高流量下可能因份数限制少于 14 天。
- 轮转后调用 `pm2 reloadLogs` 重新打开日志描述符，不重启任务进程，不使用会丢失复制期间日志的 `copytruncate`。后台页面继续读取原有当前日志路径；历史日志保存在同一目录的 `.gz` 文件中。
- 首次强制轮转把已有大日志压缩保留，之后按照轮转文件的年龄和份数清理。原始记录日期可能早于轮转日期，因此首次压缩文件可能包含更早的记录；不会宣称按记录日期删除了这些内容。
- Resin 保留最近 14 个自然日的请求日志。仅删除全部记录已过期、文件修改时间也过期、已关闭且非当前活动库的 SQLite 日志文件；当前库和未 checkpoint 的库始终保留。活动库内更老的记录等该库正常轮转后再处理。
- 清理无主、过期且未打开的请求日志 WAL/SHM。`metrics.db`、Resin 配置、节点状态、缓存和平台数据不在删除范围内。
- Hermes 的指定 uv 下载缓存每七天调用官方 `uv cache clean` 清理，保留工具的使用中检查，不加 `--force`；安装环境、工具、会话和备份不删除。
- 失败登录记录用系统 logrotate 每日轮转、压缩、最多 14 份/14 天；仅删除过期的 `btmp` 归档，不改写当前文件。
- 不打开 PostgreSQL、Meilisearch、任何备份目录或业务数据卷。不会自动压缩索引，也不会自动重启容器。

## 验证与部署

本地测试：`python -m unittest discover -s tests -v`。

线上变更必须先得到具体命令计划的确认。源码提交并推送后，在 `/root/resin-pin` 拉取对应提交；不要运行会重新生成 `.env` 或重建网页容器的 `deploy/panso.sh`。

先执行只读预演：

```bash
TZ=Asia/Shanghai python3 deploy/host_log_maintenance.py
```

确认预演范围后安装并执行首次清理：

```bash
bash deploy/install-log-maintenance.sh
systemctl status panso-log-maintenance.timer --no-pager
```

安装器备份旧 btmp 策略及已有同名 systemd 单元，安装仓库内的文件，再强制轮转一次并启用计时器。每次执行只输出文件名和维护状态，不输出日志内容、代理令牌或配置环境。

验收时检查磁盘实际释放量、全部容器 ID 和启动时间、PM2 任务 PID、当前日志的新写入、压缩历史可读、Resin 健康和自动池节点数、公共 API 健康及业务资源/备份路径未变更。

## 停用与恢复策略

```bash
systemctl disable --now panso-log-maintenance.timer
```

从安装时报告的受保护配置备份恢复 btmp 策略及原有同名单元，再 `systemctl daemon-reload`。已删除的过期请求日志和下载缓存无法恢复；近期 worker 日志仍在压缩文件中，下载缓存可由工具重新获取。业务数据和备份不参与回滚。
