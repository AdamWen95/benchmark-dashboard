# 部署说明

本仓库提供 Linux systemd 服务和安装脚本示例。示例采用专用账户 `benchmark-dashboard`、项目目录 `/opt/benchmark-dashboard` 和回环监听地址 `127.0.0.1`。这些值是公开示例，不代表已有部署；安装前检查目标机器的目录、账户、端口、服务及脚本参数。

## 准备

在目标机器创建独立 Python 虚拟环境，安装依赖，并按 [README](../README.md) 仅为尚不存在的配置复制 `.env.example`、`daily_config.example.json` 和 `daily_schedule.example.json`。将实际密钥保存在目标机器，限制文件权限；不要打包本机 `.env`、数据库、快照、历史简报或虚拟环境。

创建运行需要的 `data/` 与 `.cache/` 目录，使用服务账户运行离线测试和预览。已有部署应先备份将修改的文件并核对当前配置；SQLite 备份使用一致性备份方式，不以开发机器的数据库覆盖已有数据。

`deploy/benchmark-dashboard.service` 是只读网页服务示例，网页不能读取 `.env`，数据目录只读。`scripts/install_background_service.sh` 带有账户、目录、配置和既有单元检查。它不是通用升级器；存在不匹配的服务或覆盖文件时应先审查差异，不要删除保护检查以强行安装。相关安装器还对模板执行精确哈希校验；自定义账户或路径时，需要同步审查模板、校验哈希和对应测试，不能只修改模板。

默认回环监听不提供其他机器访问。需要反向代理时，结合自己的域名、认证、访问范围和 Nginx 配置审查网关脚本；不要直接套用到共享站点。保留 CORS、XSRF 和 WebSocket 转发所需配置，只重载相关服务。

## 日更配置

`daily_config.example.json` 默认 `enabled: false`。`daily_schedule.example.json` 给出配置结构，起始日期与作用域必须由部署者设置。示例 timer 使用 `Asia/Shanghai` 每日 09:00；修改日程时需要同时核对 JSON 和 timer。

`deploy/benchmark-dashboard-daily.service` 与 `.timer` 是独立调度示例，不会因文件存在而自动运行。安装和启用前应明确数据来源、分析传输范围、频率及费用。`--execute` 会产生真实外部请求，不能用于安装或连通性检查。

日更设计为每天最多一次采集；数据变化且有效简报无法复用时，最多一次分析。失败当天不自动重试，timer 的 `Persistent=false` 不补跑停机期间错过的计划，service 的 `Restart=no` 不重启失败任务。配置开关不能代替 systemd 安装，单元文件也不能代替业务配置。

## 验证与维护

在项目根目录进行离线检查：

```bash
.venv/bin/python -m pip check
.venv/bin/python scripts/run_daily.py --status
.venv/bin/python scripts/run_scheduled_daily.py --preview
```

已有有效快照时，还可以运行 `scripts/run_daily.py --preview` 检查日更事实预览；空库不支持这一模式。已经配置并准备启用日更时，可运行 `scripts/run_scheduled_daily.py --check` 进行本地凭据和配置校验；它不联网、不改业务数据。未启用时校验失败不表示网页故障。

安装后的只读状态检查：

```bash
systemctl status benchmark-dashboard.service --no-pager
systemctl show benchmark-dashboard.service --property=ExecStart --property=DropInPaths
systemctl status benchmark-dashboard-daily.timer --no-pager
systemctl show benchmark-dashboard-daily.timer --property=NextElapseUSecRealtime --property=LastTriggerUSec
```

健康检查 URL 取决于实际端口及 `server.baseUrlPath`，以有效配置为准。HTTP 健康响应和离线测试不能代替真实浏览器验收；检查页面读取、切换、刷新与代理 WebSocket。

维护已有调度时保留其实际配置、尝试标记和数据。停止 timer 不会终止已经运行的日更 service；先核对运行状态，再执行维护。恢复计划不应通过手动执行日更来“测试”，也不删除记录追补错过或失败的请求。
