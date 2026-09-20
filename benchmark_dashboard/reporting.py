"""Reports contain summaries and schema paths, never headers or raw responses."""
import json
from pathlib import Path
from .acquisition import Acquisition, atomic_write
from .client import API_URL


def cell(value) -> str:
    return str(value).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ').replace('`', "'")


def write_m0_success(root: Path, result: Acquisition) -> None:
    valid = result.valid
    count = len(valid.records)
    lines = ['# M0 真实数据接入验收', '', '结论：通过。真实官方 API 请求及数据校验成功。', '',
             f'- HTTP 状态：{result.http_status}；请求次数：{result.attempts}',
             f'- 开始时间（UTC）：{result.started_at}', f'- 实际采集时间（UTC）：{result.collected_at}',
             f'- 接口：{API_URL}', f'- 模型记录数：{count}', '- 标识：来源 + 稳定模型 ID；未按名称合并。',
             '- 重复 ID、已知字段类型错误、空列表：未发现。',
             f'- 内容 SHA-256：`{valid.content_hash}`',
             f'- 本地原始快照：`data/snapshots/{result.snapshot_path.name}`（Git 忽略）',
             '- 哈希算法：完整响应规范化 JSON；模型列表按稳定 ID 排序；包括测试参数。', '',
             '## 指标覆盖', '', '| 实际/预期指标路径 | 非空数量 | 覆盖率 | 缺失/空值 |',
             '| --- | ---: | ---: | ---: |']
    for path, available in valid.coverage.items():
        lines.append(f'| {cell(path)} | {available} | {available/count:.1%} | {count-available} |')
    lines += ['', '## 实际字段清单', '', '响应顶层：' + '、'.join(map(cell, valid.top_fields)), '',
              '| 模型字段路径（含嵌套） | 出现次数（含空值） |', '| --- | ---: |']
    lines += [f'| {cell(path)} | {n} |' for path, n in valid.fields.items()]
    lines += ['', '## 未知字段与限制', '']
    lines += [f'- 未知字段（原样保留）：{cell(path)}' for path in valid.unknown_fields] or ['- 无未知字段。']
    lines += [f'- {cell(warning)}' for warning in valid.warnings]
    lines += ['- 部分模型缺少指标，允许保存；展示为“暂无”，不补零。',
              '- 指数保留原值；未确认 API 编码单位的评测保留原值并提示口径未确认，不按示例猜测百分比。',
              '- 评测版本、评测日期仅使用源站明确提供的对应字段；缺失标“源站未提供”，采集时间不作替代。',
              '- 本次仅本地开发验证；公司多人展示、长期历史缓存、向分析模型发送源数据的授权均待确认。',
              '', '## 源站测试参数', '',
              '```json', json.dumps(valid.payload.get('prompt_options'), ensure_ascii=False, indent=2), '```', '',
              '完整源站参数保留在被忽略的快照；各模型未知配置字段也原样保留。', '',
              '来源：[Artificial Analysis](https://artificialanalysis.ai/)；'
              '[官方 API 文档](https://artificialanalysis.ai/api-reference)。', '']
    atomic_write(root / 'reports' / 'M0_data_source_check.md', '\n'.join(lines))


def write_m0_failure(root: Path, started_at: str, message: str) -> None:
    # `message` must come from our fixed, sanitized error types, never exception repr / response text.
    atomic_write(root / 'reports' / 'M0_data_source_check.md', '\n'.join([
        '# M0 真实数据接入验收', '', '结论：未验收通过。', '', f'- 尝试时间（UTC）：{started_at}',
        f'- 接口：{API_URL}', f'- 原因：{cell(message)}',
        '- 未替换上一次有效快照或数据库。', '- 不使用合成 fixtures 作为真实运行数据。', ''
    ]))
