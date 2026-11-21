# 已知问题与限制

- 质量评估结果均为 provisional：GT 由机器审核，答案由本地模型自动评判，尚无独立的人工验证集。研究原始记录不随本仓库发布，聚合结果见 `docs/experiments/`。
- 没有 RAGAS 评估，也没有 Faithfulness 或 Answer Relevancy 分数。包名 `ragas_eval` 是历史遗留。
- 生成存在不确定性：temperature 0 并固定 seed 时，仍有一部分回答不能逐字复现。
- 模型可能把资料中的“用途”当作“选择理由”这类无证据推断；安全层不改写语义。
- 安全层只保证引用编号真实存在于上下文中，不保证语义支持。
- 文档来源保护只识别显式写法（“<文档> 中/里”“根据 <文档>”“（<文档>）”）。
- 跨文档列举题的检索常常凑不齐全部文档。
- `scripts/10_eval_retrieval.py` 是历史评测器：
  - `search_hybrid`（union_safe）有已知排序缺陷，不是 RRF；
  - `compute_source_hit` 是文件级命中，不是 Recall。

  两者只为复现而保留。
- `config/rag_config.yaml` 的大部分字段不被代码读取（见 `docs/CONFIG.md`）；`scripts/03` 的 `load_config()` 从未被调用。
- 公开示例知识库规模很小，检索指标接近饱和，只能验证功能，不能比较策略优劣。
- 知识库 ACL（第一阶段，见 `docs/KNOWLEDGE_PLATFORM.md`）：
  - BM25 的 IDF 使用全库统计，未授权文档的词频可能轻微影响授权 Chunk 的排序；
  - 策略粒度是文档，没有 Chunk 级策略和部门层级继承；
  - 用户目录是 JSON 文件，没有身份认证，调用方需要自行完成认证；
  - `kb_service` 的请求串行执行；
  - 旧的 `rag_service` 和 `retrieval_eval` 没有 ACL，不能作为平台入口使用。
