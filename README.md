# Benchmark Dashboard · 模型指标面板

基于 Python、SQLite 和 Streamlit 的中文模型指标面板。数据来自 Artificial Analysis 官方 API，经结构校验后保存到本地，提供模型总览、指标对比、数据覆盖率、变化记录和可选日更简报。

网页只读已有结果。打开页面、筛选、刷新和切换模型均不会触发数据采集或分析模型请求。仓库提供源码、空配置模板和合成测试数据，不包含真实密钥、数据库、快照或已生成简报。

## 功能

- **模型总览**：按厂商、名称和配置标签筛选，展示单项指标、原始单位及数据质量提示。
- **模型对比**：选择 2–4 条记录，保留完整名称、稳定 ID、配置和指标依据；程序规则说明与 AI 简报分别标注。
- **数据概况**：查看全量与筛选后有限数值覆盖率、最近采集状态及来源。
- **变化记录**：比较最近两次成功采集，区分新增、本次未返回、元数据变化、数值变化、补齐、转缺失和口径变化。
- **日更与简报**：支持独立命令和 Linux systemd 调度；配置模板默认关闭，需要部署者自行配置、确认数据用途并启用。

## 安装

先将仓库克隆或解压到自己的目录，然后在项目根目录运行命令。使用 Python 3.13 或更新版本创建独立虚拟环境；依赖以 [requirements.txt](requirements.txt) 为准。环境说明见 [开发环境](docs/environment.md)。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --cache-dir .cache/pip -r requirements.txt
```

Linux / macOS：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --cache-dir .cache/pip -r requirements.txt
```

已有虚拟环境可直接复用；更换 Python 版本时应创建新的环境。

## 首次本地配置

仅在目标文件不存在时复制模板，保留已有配置：

```powershell
# Windows PowerShell
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
if (-not (Test-Path -LiteralPath 'daily_config.json')) { Copy-Item -LiteralPath 'daily_config.example.json' -Destination 'daily_config.json' }
if (-not (Test-Path -LiteralPath 'daily_schedule.json')) { Copy-Item -LiteralPath 'daily_schedule.example.json' -Destination 'daily_schedule.json' }
```

```bash
# Linux / macOS
test -e .env || cp .env.example .env
test -e daily_config.json || cp daily_config.example.json daily_config.json
test -e daily_schedule.json || cp daily_schedule.example.json daily_schedule.json
chmod 600 .env
```

三个实际配置文件均被 Git 忽略。`.env.example` 的密钥为空，分析开关关闭；`daily_config.example.json` 的日更开关关闭。`daily_schedule.example.json` 仅提供调度结构，使用前需自行设置起始日期和唯一作用域。复制文件不会安装或启用定时任务。

只查看空页面、运行离线预览和测试不需要密钥。需要实际采集或生成简报时，在本地编辑器中分别配置数据 API 和分析 API 的凭据，并先阅读 [数据使用说明](docs/data_usage_check.md)。不要将 `.env` 上传、打印到终端或写入报告。

## 启动网页与离线预览

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe scripts/run_local.py
```

Linux / macOS：

```bash
.venv/bin/python scripts/run_local.py
```

浏览器访问 [本地面板](http://127.0.0.1:8502)，按 Ctrl+C 停止。启动器仅监听回环地址；端口被占用时拒绝启动，可通过 `--port` 指定其他空闲端口。新安装的空库页面会显示无数据提示。

离线预览和状态检查示例（Windows 将解释器替换为 `.\.venv\Scripts\python.exe`）：

```bash
.venv/bin/python scripts/run_daily.py --status
.venv/bin/python scripts/run_scheduled_daily.py --preview
```

这些模式不读取密钥、不联网、不写业务数据库或请求标记。已有至少一份有效快照后，还可以使用 `scripts/run_daily.py --preview` 查看基于现有数据的日更事实预览；空库不能完成该预览。`run_scheduled_daily.py --check` 会在本地读取凭据和配置进行校验，但不会发送请求；未配置或未启用时报告校验失败是预期行为。

`scripts/collect.py` 用于显式采集；`scripts/run_daily.py` 和 `scripts/run_scheduled_daily.py` 的 `--execute` 模式会产生真实外部请求。它们不是安装检查步骤。采集与分析的用途、频率、费用和调度由部署者单独配置，本说明不启用任何请求。服务器示例见 [部署说明](docs/deployment.md)。

## 数据与分析边界

指标保留源站原值和来源。缺失显示“暂无”，不作为零分；未知量纲不按数值大小转换为百分比。价格为源站报告的美元/百万 Token，速度和延迟也不是当前机器或服务渠道的实测表现。指标映射与核验状态见 [指标口径](docs/metric_units.md) 和 [字段核验说明](docs/M2B_metric_verification.md)。

速度或延迟记录为 `0` 时，页面提示测量含义待确认，不用于性能排序或优劣判断。合法的零值仍计入有限数值覆盖率；有限数值覆盖率不等于可靠实测覆盖率。评测日期、版本或测试配置缺失时保持未知，不以采集时间替代。

新增记录不代表当天发布，本次未返回不代表下架。缺失转有值属于补齐，不计算从零提升；口径不一致时仅展示原值和限制。程序规则不合成跨指标总分，也不提供“所有场景最强”排名。

可选分析适配器使用 Modex `https://hk.modex-ai.cloud/v1` 和 `gpt-5.6-sol`。分析输入由程序整理必要事实、覆盖率及可比性限制，结果需人工复核。页面展示已有结果不调用模型。日更执行保留日期、作用域和尝试标记；同日失败不自动重试，无变化时不请求 AI，可用结果按版本和事实绑定复用。不要删除尝试记录来重新获得请求机会。

数据、快照、简报、尝试标记和备份保存在被忽略的本地目录。结果有效期控制是否作为当前简报展示，不自动删除历史文件；保留期限由部署者管理。

## 测试

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

```bash
# Linux / macOS
mkdir -p .cache
.venv/bin/python -m pytest -q --basetemp=.cache/pytest-local
.venv/bin/python -m pip check
```

自动化测试使用合成 fixtures、临时数据库和隔离路径，不需要真实 API 密钥。离线测试通过不代表外部服务可用、真实数据解释正确或某个部署环境已完成验收。

## 项目结构

| 路径 | 用途 |
| --- | --- |
| `app.py` / `benchmark_dashboard/ui.py` | Streamlit 入口与中文页面 |
| `benchmark_dashboard/client.py` / `config.py` | 数据接口、配置与安全错误信息 |
| `benchmark_dashboard/validation.py` / `metrics.py` | 结构校验、字段字典和量纲 |
| `benchmark_dashboard/store.py` | SQLite 保存与只读查询 |
| `benchmark_dashboard/changes.py` / `comparability.py` | 变化记录与可比性检查 |
| `benchmark_dashboard/insights.py` | 确定性规则说明 |
| `benchmark_dashboard/daily_run.py` / `daily_schedule.py` | 日更状态、请求边界与调度配置 |
| `benchmark_dashboard/briefing.py` / `modex_client.py` | 可选分析与结果校验 |
| `scripts/` / `deploy/` | 显式命令入口和部署示例 |
| `tests/` | 合成数据与自动化测试 |
| `docs/` | 指标、环境、数据使用及部署说明 |

数据来源：[Artificial Analysis](https://artificialanalysis.ai/)。发布源码不意味着可以一并再分发 API 数据、密钥或生成结果；提交前检查暂存区，保留 `.gitignore` 对本地配置和运行产物的排除。
