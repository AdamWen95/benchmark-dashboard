# 指标口径核验说明

指标映射版本：`m2b-metrics-v1`。具体核验日期与来源记录在 `benchmark_dashboard/metrics.py` 中，表示字典依据的时间，不表示当前网页已重新核验。

本说明描述项目字段字典所依据的公开接口和方法文档，以及这些证据的解释范围。网站当前说明不能证明某条已有快照采用了当前版本。

## 核验状态

- `confirmed`：字段含义与编码单位有直接官方说明；仍须由原有可比性规则检查记录上下文。
- `partial`：概念、字段存在或部分单位可核对，但字段定义、编码或计算契约仍不完整。保留原值，不作为 M2B 的方向性或能力结论。
- `unknown`：尚未获得足够的逐字段直接依据。保持未知，不按字段名或数值范围猜测。

状态与 M0–M2A 的历史 `Metric.confirmed` 展示单位标记分开。兼容显示“指数原值”并不等于已核准该指数在当前快照中的组成和编码。M2B 必须读取新增核验状态；不把 `partial` 当作 `confirmed`。

## 逐字段结论

| 已有字段 | 已确认的内容 | 仍未知的内容 | 直接来源 | 对展示和分析的影响 |
| --- | --- | --- | --- | --- |
| `evaluations.artificial_analysis_intelligence_index` | API 示例列出该键；方法页将智能指数解释为多项评测综合结果 | 未见逐字段 API 编码契约；本地记录使用的指数版本、权重、评测日期和推理配置不能由当前网页补填 | [API 说明](https://artificialanalysis.ai/api-reference)、[智能评测方法](https://artificialanalysis.ai/methodology/intelligence-benchmarking) | `partial`；兼容原指数值，不乘 100、不重算、不作能力判断 |
| `evaluations.artificial_analysis_coding_index` | API 示例列出该代码指数键；方法说明提及 Coding Index | 未找到该字段完整计算/编码契约；本地组成、版本与配置未知 | [API 说明](https://artificialanalysis.ai/api-reference)、[智能评测方法](https://artificialanalysis.ai/methodology/intelligence-benchmarking) | `partial`；只显示已有指数原值和限制 |
| `evaluations.artificial_analysis_math_index` | API 示例列出该数学指数键 | 当前字典未找到该字段直接公式及编码说明；不能套用今天的其他能力指数 | [API 说明](https://artificialanalysis.ai/api-reference)、[智能评测方法](https://artificialanalysis.ai/methodology/intelligence-benchmarking) | `partial`；保留原值，不合成数学能力结论 |
| `pricing.price_1m_input_tokens` | 价格对象以美元/百万 Token 表达；输入价格与输出价格分开 | 每条记录的完整计费条件、渠道适用性与实际用量未给出 | [API 说明](https://artificialanalysis.ai/api-reference)、[定义总览](https://artificialanalysis.ai/methodology) | `confirmed`；美元/百万 Token，原值；越低仅表示该项源站公开价更低 |
| `pricing.price_1m_output_tokens` | 同上，作为输出 Token 价格单独记录 | 缓存、长上下文、折扣等条件以及实际账单未知 | [API 说明](https://artificialanalysis.ai/api-reference)、[定义总览](https://artificialanalysis.ai/methodology) | `confirmed`；不与输入价合并推导总费用 |
| `pricing.price_1m_blended_3_to_1` | 价格单位已知，API 示例存在该键 | 字段名中的 3:1 不是完整计算契约；当前定义页采用另一种 7:2:1 混合方式，不能据此反推旧字段 | [API 说明](https://artificialanalysis.ai/api-reference)、[定义总览](https://artificialanalysis.ai/methodology) | `partial`；原值保留，标签注明“字段名 3:1”，不重算、不作性价比判断 |
| `median_output_tokens_per_second` | API 明确 Token/秒；方法页定义输出阶段速度 | 本地记录的服务条件是否足够一致仍由 `prompt_options` 等显式上下文检查 | [API 说明](https://artificialanalysis.ai/api-reference)、[性能评测方法](https://artificialanalysis.ai/methodology/performance-benchmarking) | `confirmed`；越高仅表示该项源站输出速度报告值更高，不代表使用者的服务渠道表现 |
| `median_time_to_first_token_seconds` | API 直接声明秒；方法页区分首 Token 等待与完整响应耗时 | 本地网络、渠道延迟与实际体验未知 | [API 说明](https://artificialanalysis.ai/api-reference)、[性能评测方法](https://artificialanalysis.ai/methodology/performance-benchmarking) | `confirmed`；原值秒，越低仅表示该项等待较短 |
| `median_time_to_first_answer_token` | 方法页单独解释首回答 Token 概念，区别于首 Token；API 示例存在该键 | API 字段表未直接说明此键的编码契约；方法概念不能单独证明该字段单位及方向 | [API 说明](https://artificialanalysis.ai/api-reference)、[性能评测方法](https://artificialanalysis.ai/methodology/performance-benchmarking) | `partial`；原单位未确认，方向 `unknown`；不与首 Token 互相替代 |
| `evaluations.livecodebench` | AA 方法页说明代码生成及 pass@1；上游仓库存在多个数据版本和时间窗口 | AA API 对该键没有明确 0–1 或 0–100 编码承诺，本地版本、窗口与配置未知 | [AA 智能评测方法](https://artificialanalysis.ai/methodology/intelligence-benchmarking)、[LiveCodeBench 官方仓库](https://github.com/LiveCodeBench/LiveCodeBench)、[API 说明](https://artificialanalysis.ai/api-reference) | `partial`；原值及未知方向；不乘 100，仍归属 AA 来源，未接入上游榜单 |
| `evaluations.terminalbench_hard` | AA 方法页单列 Hard 子集，使用任务测试判断成功 | API 字段表未说明此键的量纲与版本映射；本地测试版本不能从网页当前说明推定 | [AA 智能评测方法](https://artificialanalysis.ai/methodology/intelligence-benchmarking)、[API 说明](https://artificialanalysis.ai/api-reference) | `partial`；保留独立字段、原值及未知方向 |
| `evaluations.terminalbench_v2_1` | AA 方法页另有 Terminal-Bench 2.1 方法说明 | 键中的 v2_1 不足以证明该条数据的测试版本，未见 API 字段编码契约 | [AA 智能评测方法](https://artificialanalysis.ai/methodology/intelligence-benchmarking)、[API 说明](https://artificialanalysis.ai/api-reference) | `partial`；不与 Hard 合并，不依据相邻字段推定百分比或版本 |

其余 11 个已有展示字段继续为 `unknown`：`mmlu_pro`、`gpqa`、`hle`、`scicode`、`math_500`、`aime`、`aime_25`、`ifbench`、`lcr`、`tau2`、`tau_banking`（均属于 `evaluations`）。这些字段尚未完成逐字段编码核验，不借用相似名称或示例数值将其升为确认。

## 转换、版本与证据边界

所有当前映射的 `conversion_formula` 都是 `display_value = raw_value`，`multiplier` 保持 1。只有显示精度沿用 8 位有效数字，原值仍保留在记录和事实依据中。`0` 是合法数值；`None`、布尔值、字符串及非有限值不充作成绩。未知量纲不改变覆盖率的合法数值统计。

官方方法的总体概念、网页版本、核验日期和本地采集时间是不同信息。没有显式的记录元数据时，评测日期、数据集版本和配置仍为空/未知。来源单位出现冲突时，继续使用 M2A 共享规则仅并列原值；不会因字典有已知单位而覆盖源站声明。

官方 LiveCodeBench 仓库的说明也表明版本与时间范围可以变化，这只能说明必须保留此类限制，不能证明本地 AA 字段采用了哪套上游设置。本项目仍只有 Artificial Analysis 一个数据来源。

补充参考包括 [能力指数方法页](https://artificialanalysis.ai/methodology/capability-indices) 和 [Terminal-Bench 运行说明](https://www.tbench.ai/run)；它们不作为上述字段直接编码契约的替代，未据此回填未知信息。

## 离线验证

`tests/test_metric_verification.py` 使用内存合成记录，验证新增核验状态、未确认原值与零值、首 Token 字段分离、映射不改源数据、不回填版本、显式单位冲突仍受约束，以及原 16 个校验键不因说明增加而扩张。离线通过不等于真实 API 或真实分析服务联调通过。测试命令见 [README](../README.md)。
