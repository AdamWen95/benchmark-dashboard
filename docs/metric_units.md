# 指标口径与字段映射

显式映射位于 `benchmark_dashboard/metrics.py`；采集后的原始响应保留在本地快照和 SQLite 中。本文件解释当前代码中的字段语义，不包含真实采集样本。

| 字段/类别 | 展示单位 | 处理 |
| --- | --- | --- |
| `artificial_analysis_intelligence_index` | 指数原值 | 不乘 100，不构造跨指标总分 |
| `artificial_analysis_coding_index` | 指数原值 | 不乘 100 |
| `artificial_analysis_math_index` | 指数原值 | 不乘 100 |
| 其他 `evaluations.*` | 原值 · 口径未确认 | 即使值位于 0–1，也不推断为百分比 |
| `price_1m_input_tokens` / `price_1m_output_tokens` | 美元/百万 Token | 分开显示，原值 |
| `price_1m_blended_3_to_1` | 美元/百万 Token | 字段名含 3:1；具体公式未直接确认，不按当前网页新混合比例重算 |
| `median_output_tokens_per_second` | Token/秒 | 原值 |
| `median_time_to_first_token_seconds` | 秒 | 原值 |
| `median_time_to_first_answer_token` | 原值 · 口径未确认 | 文档示例有此字段，但字段表未给出明确单位契约 |

官方 API 字段表明确了价格、速度和首 Token 延迟的单位。`evaluations` 只定义为评测分数，没有逐字段的 API 编码量纲。评测方法页的 pass@1 语义与文档示例中的 0.x 数值均不单独作为百分比转换的依据。以后若找到明确的对应字段契约，应修改映射并补充测试；不能按数据大小猜测。

API 文档没有列出的新增评测字段采用通用原值展示，并在 M0 报告中列为未知字段。未知非数值字段仅保留为 JSON，不参与排序或计算；已知数值字段出现字符串、布尔值、非有限值等会拒绝整批响应。

## 指标字典与覆盖说明

指标字典独立于采集流程，展示时不访问源站。`Metric` 为每个已展示项目提供字段键、中文名、源站原名、简短含义、原始单位、展示单位、方向、显示精度、可比性限制。未核验源站展开原名的评测项目以实际 API 字段键作为 `source_name`，英文易读写法只用于展示标签。已接入的 23 个字段如下；“未确认”项目不能依据数值大小推断能力高低，也不能自行乘 100。

| 字段键（`evaluations.` 前缀除最后六行外均适用） | 中文名 / 源站原名 | 原始及展示单位 | 方向与含义 |
| --- | --- | --- | --- |
| `artificial_analysis_intelligence_index` | 综合智能指数 / Artificial Analysis Intelligence Index | 指数原值 | 越高表示该源站指数报告值越高；并非所有任务的能力结论 |
| `artificial_analysis_coding_index` | 代码指数 / Artificial Analysis Coding Index | 指数原值 | 越高表示该源站编程指数报告值越高 |
| `artificial_analysis_math_index` | 数学指数 / Artificial Analysis Math Index | 指数原值 | 越高表示该源站数学指数报告值越高 |
| `mmlu_pro` | MMLU-Pro 评测 | 原值 · 口径未确认 | 源站该项评测值；方向未确认 |
| `gpqa` | GPQA 评测 | 原值 · 口径未确认 | 源站该项评测值；方向未确认 |
| `hle` | HLE 评测 | 原值 · 口径未确认 | 源站该项评测值；方向未确认 |
| `livecodebench` | LiveCodeBench 评测 | 原值 · 口径未确认 | 源站该项评测值；方向未确认 |
| `scicode` | SciCode 评测 | 原值 · 口径未确认 | 源站该项评测值；方向未确认 |
| `math_500` | MATH-500 评测 | 原值 · 口径未确认 | 源站该项评测值；方向未确认 |
| `aime` | AIME 评测 | 原值 · 口径未确认 | 源站该项评测值；方向未确认 |
| `aime_25` | AIME 25 评测 | 原值 · 口径未确认 | 已接入字段；定义、方向未充分确认 |
| `ifbench` | IFBench 评测 | 原值 · 口径未确认 | 已接入字段；定义、方向未充分确认 |
| `lcr` | LCR 评测 | 原值 · 口径未确认 | 已接入字段；定义、方向未充分确认 |
| `tau2` | Tau2 评测 | 原值 · 口径未确认 | 已接入字段；定义、方向未充分确认 |
| `tau_banking` | Tau Banking 评测 | 原值 · 口径未确认 | 已接入字段；定义、方向未充分确认 |
| `terminalbench_hard` | TerminalBench Hard 评测 | 原值 · 口径未确认 | 已接入字段；定义、方向未充分确认 |
| `terminalbench_v2_1` | TerminalBench v2.1 评测 | 原值 · 口径未确认 | 已接入字段；字段名不等于已确认测试版本 |
| `pricing.price_1m_input_tokens` | 输入价格 | 美元/百万 Token | 越低表示该项公开价格越低；不能单独推导总费用 |
| `pricing.price_1m_output_tokens` | 输出价格 | 美元/百万 Token | 越低表示该项公开价格越低；与输入价格分开 |
| `pricing.price_1m_blended_3_to_1` | 混合价格（字段名 3:1） | 美元/百万 Token | 保留历史字段名与原值；具体公式未直接确认，不代表实际用量或性价比 |
| `median_output_tokens_per_second` | 输出速度 | Token/秒 | 越高表示源站记录的输出速度越高 |
| `median_time_to_first_token_seconds` | 首 Token 延迟 | 秒 | 越低表示源站记录的首 Token 等待越短，不是完整回答耗时 |
| `median_time_to_first_answer_token` | 首回答 Token 延迟 | 原值 · 口径未确认 | 方法概念与 API 字段契约分开；字段方向未知，不据此给出可比优劣结论 |

新增七项说明只进入 `DISPLAY_METRICS`；原 `METRICS` 的 16 项校验集合保持不变。新增项在采集校验及既有 M0 报告中仍是未知字段，增加中文解释不会改变数据验证行为。遇到未来未知字段则显示字段键、保守含义、未确认单位和未知方向。

价格、速度与延迟均为公开平台报告值，并非使用者的服务渠道、自建部署或实际使用表现。指标方向只解释数值的语义，不证明不同测试配置可比。源站缺少的评测时间、测试版本、推理配置继续显示“源站未提供”；不能把采集时间、模型发布日期或指标键中的文字代为填入。

覆盖表必须使用同一有效快照全量记录，并标出快照时间与统计范围。`valid_count / total` 的分母是该快照模型记录数，不是品牌或模型家族数。整数、浮点数、Decimal 的有限值（包括 `0`）计入有效；空值、布尔值、字符串和非有限值不计入。空库覆盖率为 0。传入筛选子集时仅得到该子集覆盖率，页面不能将其标成全量。缺分不意味着能力较差。

## 精度、差值与并列

原始记录保留不变。数值处理仅接受有限数值类型，不把字符串隐式转换成数字。展示、排序并列及变化检测共用 `normalized_value`：先按显式 multiplier 转换到展示单位，再用 Decimal 的 ROUND_HALF_EVEN 规则保留 8 位有效数字；微小的非零价格保持非零，二进制浮点的 `.1 + .2` 与 `.3` 视作相同。差异低于展示精度时不制造可见变化。

`numeric_delta` 保留原始前后值，并以字符串序列化展示单位的差值和相对百分比：指数差为“指数点”；仅已确认且显式乘 100 的比例项差值为“百分点”；当前字典没有此类比例映射；未确认量纲只标“原值差（单位未确认）”。相对百分比单独采用 `(新值 - 旧值) / abs(旧值) × 100`，基准为 0 或任一侧无合法数值时不计算。绝对差和相对百分比不等同于能力提升或下降；完整可比性仍由变化记录及对比说明规则检查。

`prompt_options` 是响应给出的速度/延迟测试参数，页面保留原值；不要把它当作所有评测的统一配置。模型完整名称和 `slug` 保留，避免遗漏名称中的配置标签。

采集时间、模型发布日期、评测日期分别处理。API 的 `release_date` 表示发布日期；逐条评测日期或版本缺失时显示“源站未提供”，不得用采集日期、发布日期或网页当前的指数版本代替。没有跨版本趋势或“最佳模型”结论。

来源：[官方 API 文档](https://artificialanalysis.ai/api-reference)、[评测方法](https://artificialanalysis.ai/methodology/intelligence-benchmarking)、[方法总览](https://artificialanalysis.ai/methodology)。

## 核验元数据

映射版本为 `m2b-metrics-v1`。代码记录的核验日期用于标明字典证据的时间，不表示已重新核验当前网页。逐字段依据和未知项见 [字段核验说明](M2B_metric_verification.md)。

`Metric` 增加独立的 `verification_status`、`official_sources`、`verified_at` 和 `conversion_formula`。`confirmed` 状态表示已核对字段定义与编码单位；`partial` 表示概念或部分定义有依据，但该 API 字段的定义、编码或计算契约仍不完整；`unknown` 表示尚无足够的逐字段核验。核验日期只记录字典检查日期，不是评测日期。所有现有字段的显示转换公式仍为 `display_value = raw_value`，不乘 100，不重算源站指数。

字典将输入价格、输出价格、输出速度、首 Token 延迟 4 个字段标为已确认。三个指数、混合价格、LiveCodeBench、两种 TerminalBench 字段及首回答 Token 延迟共 8 个字段为 `partial`。其余 11 项为 `unknown`，不把页面上的当前方法或相似字段自动套用到已有快照。

历史 `Metric.confirmed` 布尔字段继续表示 M0–M2A 的展示单位兼容处理，不能当作 M2B 完整核验结论。三个指数继续显示“指数原值”，但分析不把这些 `partial` 字段作为已确认能力事实。简报中的方向性观察须同时满足 `verification_status == 'confirmed'` 和共享可比性规则；其他字段只保留原值、覆盖与限制，分析方向为未知。

首回答 Token 在方法说明中有单独概念，但 API 字段表没有直接列出它的编码契约；因此该字段方向从旧展示的 `lower` 收紧为 `unknown`。混合价格仍保留字段名 `3_to_1`，当前方法页的 `7:2:1` 不能证明旧字段公式，页面也不再把推定公式写成已确认事实。

单位核验不证明版本、配置或价格条件一致；速度测试参数与评测推理配置仍分开。原 `METRICS` 的 16 个校验键和 `DISPLAY_METRICS` 的 23 个展示键没有增加或减少，原始记录与数据库均不修改。
