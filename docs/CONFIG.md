# 配置字段实际作用（v1.2 审计）

| 位置 | 读取者 | 作用 |
|---|---|---|
| `config/rag_config.yaml` → `embedding.model_name`、`fallback_model_name`、`normalize_embeddings`、`batch_size` | `scripts/06_build_dense_sparse_index.py` | 建 Dense 索引时使用的模型和参数 |
| `config/rag_config.yaml` → `index.index_parent_chunks` | `scripts/06` | 是否把父 Chunk 也放进索引（默认否） |
| `config/rag_config.yaml` → `embedding.model_name`、`embedding.device` | `ragas_eval/config.py` | 研究工具使用的评估配置 |
| `config/rag_config.yaml` → `service_retrieval` | **无**（只是说明） | 记录正式服务写在代码中的参数；有回归测试保证它与代码一致 |
| `config/rag_config.yaml` → `retrieval.*`、`index.hybrid.*` | **无** | 旧 v0.3 评测器的历史字段。union_safe 不是 RRF；0.45/0.55 权重从未生效 |
| `config/rag_config.yaml` → `chunking.*` | **无** | `scripts/03` 定义了 `load_config()` 但从未调用，实际使用脚本内的常量 800/160/100/1200，与配置中的值相同 |
| `config/rag_config.yaml` → 其余段落 | **无** | v0.1 的设计说明 |
| `config/safety_layer.json` → `enabled` | `ragas_eval/rag_service.py` | 安全层开关。优先级：命令参数 > 环境变量 `RAG_SAFETY_LAYER` > 本文件 > 关闭 |
| 环境变量 `RAG_DATA_DIR` | `ragas_eval/rag_retriever.py` | 数据目录，内含 `index/sparse/bm25_index.pkl` 和 `index/dense/dense_index.pkl`；默认为 `<repo>/data` |

## 正式服务的检索与生成参数（写在代码中）

| 参数 | 值 | 位置 |
|---|---|---|
| 融合 | RRF | `rag_service.DEFAULT_MODE = "rrf"` |
| RRF k / BM25 候选池 / Dense 候选池 | 60 / 25（去掉零分）/ 25 | `rag_retriever.SERVICE_RRF`、`hybrid_rrf.RRFParams` |
| 上下文 | top-5，6000 字符 | `rag_pipeline.PipelineConfig` |
| 生成 | Ollama `qwen2.5:7b`，temperature 0，seed 42，num_predict 512，num_ctx 8192 | `rag_pipeline.PipelineConfig.options` |
| Prompt | `rag_v1.0` | `rag_pipeline.SYSTEM_PROMPT` |

v1.2 整理配置时，正式检索的行为保持不变：在研究题集的 48 道题上，v1.2 代码对私有索引的 BM25、Dense、RRF top-10 与修改前逐题一致（48/48，结果哈希相同）。这次比对在本地完成，比对用的数据不公开。
