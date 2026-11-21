# RAG Pipeline：本地中文检索增强问答（v1.2）

[![tests](https://github.com/liujiaqi110/rag-pipeline/actions/workflows/tests.yml/badge.svg)](https://github.com/liujiaqi110/rag-pipeline/actions/workflows/tests.yml)

在本机离线运行的中文 RAG 工程项目，包含：结构化切分、BM25 + Dense 混合检索（RRF）、本地 Ollama 生成、引用与拒答安全层、检索评估工具和自动化测试。

## 1. 项目定位与适用场景

- **定位**：可复现、可审计的 RAG 工程样例。重点在于检索融合的缺陷定位与修复、引用 ID 校验、拒答处理和来源归属保护，以及如实标注的评估流程。
- **适用场景**：个人或团队文档的本地问答、RAG 检索策略实验、RAG 安全后处理参考实现。数据不出本机，不需要云端 API Key。
- **不适用**：本项目没有经过人工验证的正确率，不能作为生产级问答系统的质量依据（见第 6 节）。

## 2. 系统架构

```
Markdown 文档
  → scripts/03 结构切分 → 04 边界修正 → 05 上下文继承（final_chunks.jsonl）
  → scripts/06 建索引：BM25（index/sparse） + Dense bge-m3（index/dense）
问题
  → 检索：BM25 top-25 + Dense top-25 → RRF 融合（k=60）     ragas_eval/rag_retriever.py, hybrid_rrf.py
  → 上下文：top-5 Chunk，6000 字符预算                       ragas_eval/rag_pipeline.py
  → 生成：本地 Ollama qwen2.5:7b，Prompt rag_v1.0             ragas_eval/local_llm.py
  → 安全层：引用 ID 精确校验 / 完全拒答清理 / 来源归属保护    ragas_eval/rag_safety.py
  → 输出：answer + citations + status（--audit 输出审计信息）  ragas_eval/rag_service.py
```

## 3. 技术栈

Python 3.12 · sentence-transformers + BAAI/bge-m3 · 自实现 BM25（中文按字切分） · Ollama（qwen2.5:7b，Q4_K_M） · unittest · GitHub Actions。

运行依赖见 `requirements.txt`，测试依赖见 `requirements-ci.txt`。`requirements-v1.1.txt` 只是研究环境的版本记录。

## 4. 实际默认检索策略

正式服务（`python -m ragas_eval.rag_service`）的检索参数**写在代码中**，不读取 `rag_config.yaml`：

| 项目 | 值 |
|---|---|
| 融合 | **RRF**，k=60 |
| 候选池 | BM25 top-25（去掉零分）+ Dense top-25 |
| 上下文 | top-5，6000 字符 |

`scripts/10_eval_retrieval.py` 中的 `search_hybrid`（union_safe）是**有缺陷的历史实现，不是 RRF**；`compute_source_hit` 只是文件级命中，**不是 Recall**。各配置字段的实际作用见 [docs/CONFIG.md](docs/CONFIG.md)。

## 5. 从零运行公开示例

仓库自带一套原创的小型示例知识库（`examples/docs/`，6 篇通用软件技术说明），以及 22 道示例题和证据标注（`examples/questions.jsonl`）。它不含个人信息，也不含第三方内容。

```powershell
python -m pip install -r requirements.txt
```

**只用 BM25（不需要模型）**：

```powershell
python scripts/build_example_index.py
python -m ragas_eval.retrieval_eval validate --data build/example_data
python -m ragas_eval.retrieval_eval run --data build/example_data --modes bm25 --out build/eval
```

**加上 Dense 和 RRF**：需要 BAAI/bge-m3。程序默认离线运行、不会下载模型；如果本机还没有该模型，可以自行下载，或在建库时加上 `--allow-download` 明确允许下载。

```powershell
$env:HF_HUB_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"; $env:PYTHONIOENCODING="utf-8"
python scripts/build_example_index.py --dense --force
python -m ragas_eval.retrieval_eval run --data build/example_data --modes bm25,dense,rrf --out build/eval
```

**本地真实问答**：需要已运行的 Ollama，并且已经执行过 `ollama pull qwen2.5:7b`（程序不会自动拉取模型）。

```powershell
$env:RAG_DATA_DIR="build/example_data"
python -m ragas_eval.rag_service "Docker 多阶段构建有什么好处？"
python -m ragas_eval.rag_service "Docker 多阶段构建有什么好处？" --audit
python -m ragas_eval.example_qa --data build/example_data --out build/example_qa
```

**使用自己的私有语料**：数据目录的结构与上面相同。

```powershell
python scripts/build_example_index.py --docs D:\my_docs --out D:\my_rag_data --dense
$env:RAG_DATA_DIR="D:\my_rag_data"
```

指标定义与标注格式见 [docs/EVALUATION.md](docs/EVALUATION.md)。

**示例集只用于功能验证**：它不是研究项目的 39 题评估集，也不能证明模型的泛化能力。在这么小的语料上，检索指标接近饱和。

## 6. 真实实验结果及局限

以下结果来自研究项目，使用私有语料（7 份文档、149 个子 Chunk）与 39 道机器审核题。所有结果均为 `review_type=automated`、`human_approved=false`、`evaluation_status=provisional`。

| 结论 | 数据 | 详情 |
|---|---|---|
| 旧 union_safe Hybrid 比单独的 BM25 还差 | FullCov@10：0.6154（旧 Hybrid）、0.7436（BM25）；相对 BM25 退化 6 题、改善 0 题 | [retrieval_ab.md](docs/experiments/retrieval_ab.md) |
| RRF 修复了排序退化 | RRF 相对旧 Hybrid：改善 20 题、退化 0 题；MRR 0.7772（RRF）、0.759（Dense）、0.5431（旧 Hybrid） | 同上 |
| RRF 与 Dense 互有胜负 | RRF 的 FullCov@5 为 0.8462，Dense 为 0.8205；Dense 的 FullCov@10 为 0.8974，RRF 为 0.8718；逐题改善 10、退化 8 | 同上 |
| 端到端（96 次真实生成，本地 qwen2.5:7b） | correct：RRF 24/39，Dense 20/39；无答案题正确拒答：RRF 7/8，Dense 4/8（自动评判） | [e2e_evaluation.md](docs/experiments/e2e_evaluation.md) |
| 新 Prompt 未通过回归门禁 | rag_v1.1 新增可答题误拒，并出现模板泄漏，因此未采用 | 同上 |
| 安全层离线回归（260 条历史输出） | 11 条无效引用全部拦截（其中 4 条是旧校验漏掉的）；合法引用误删 0 条；完全拒答清理 14 条 | 同上 |

**指标口径说明**：研究实验与公开示例的 Recall@K 计算方式不同。

- 研究版：对每个充分证据集合分别计算 top-K 的 Chunk 覆盖比例，取其中的最大值；
- 公开示例版（`ragas_eval/retrieval_metrics.py`）：取全部标注相关 Chunk 的并集 R，计算 |R ∩ top-K| / |R|；
- 存在多种等价证据组时，两者的数值可能不同，因此**不能直接横向比较**两套实验的 Recall@K；
- 判断多证据问题的证据是否完整，请同时参考 FullCov@K（研究版）或 SlotFullCov@K（公开示例版）。示例见 [docs/EVALUATION.md](docs/EVALUATION.md)。

失败归因见 [failure_analysis.md](docs/experiments/failure_analysis.md)，局限见 [limitations.md](docs/experiments/limitations.md)。

**本项目没有 RAGAS 自动评估，也不报告 Faithfulness 或 Answer Relevancy 分数。** 包名 `ragas_eval` 是历史遗留。

## 7. 安全层

默认开启，配置在 `config/safety_layer.json`。开关优先级：命令参数 `--safety` / `--no-safety` > 环境变量 `RAG_SAFETY_LAYER` > 配置文件 > 关闭。

- **引用 ID 精确校验**：回答中的引用必须与本次上下文中某个完整的 Chunk ID 完全相同。截短、加长或伪造的 ID 会被拦截，并在输出后复扫。
- **完全拒答清理**：按句判断回答是否为完全拒答；完全拒答时清除所有引用。部分回答保留引用；无法判断时只做标记，不删除内容。
- **来源归属保护**：问题明确指定文档时（“<文档> 中”“根据 <文档>”“（<文档>）”），只用该文档的 Chunk 生成；该文档一个 Chunk 都没检索到时，不调用模型，直接报告“检索不足”。
- **用户输出只含 answer、citations、status**：被拦截的 ID 只出现在 `--audit` 审计信息中。

安全层**不保证**引用在语义上支持对应陈述。

## 8. 测试与 CI

```powershell
python -m unittest discover -s ragas_eval/tests -t .
```

软件测试共 57 个，只用标准库和 pyyaml，不需要 Ollama、模型或私有数据。

```powershell
python -m unittest discover -s tests_local -t .
```

本地重型集成测试需要已缓存的 bge-m3 和运行中的 Ollama，**不在 CI 中运行**。

GitHub Actions（`.github/workflows/tests.yml`）在 push 和 pull request 时运行，环境为 Ubuntu + Python 3.12，执行以下步骤：

1. 运行单元测试；
2. 用示例语料建立 BM25 索引；
3. 校验示例标注；
4. 运行 BM25 检索评估。

工作流不使用任何凭据，不下载模型，不需要 GPU。

## 9. 已知问题

见 [KNOWN_ISSUES.md](KNOWN_ISSUES.md) 与 [docs/experiments/limitations.md](docs/experiments/limitations.md)。运维、回退与故障排查见 [docs/RUNBOOK.md](docs/RUNBOOK.md)。

## 10. 私有语料与索引

研究项目使用的课程笔记语料、索引、题集和生成记录可能涉及第三方版权或个人信息，**不包含在本仓库中**。正式服务通过 `RAG_DATA_DIR` 读取单独存放的私有数据目录。

本仓库只包含代码、配置、文档、测试和原创示例数据。`.gitignore` 排除了 `data/`、`build/`、`*.pkl`、`index/`、`chunks/` 等目录和文件。

本项目尚未选择开源许可证。
