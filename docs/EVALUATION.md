# 检索评估入口与指标定义

## 命令

```powershell
python scripts/build_example_index.py
python -m ragas_eval.retrieval_eval validate --data build/example_data
python -m ragas_eval.retrieval_eval run --data build/example_data --modes bm25 --out build/eval
```

第一条命令只建 BM25 索引，不需要模型。

`run` 会把结果写入 `--out` 目录：`retrieval_eval.json`（含逐题结果）和 `retrieval_eval.md`。

如果本地已经缓存了 BAAI/bge-m3，还可以评估 Dense 和 RRF：

```powershell
$env:HF_HUB_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"
python scripts/build_example_index.py --dense --force
python -m ragas_eval.retrieval_eval run --data build/example_data --modes bm25,dense,rrf --out build/eval
```

Dense 和 RRF 使用正式服务同一个检索类（`ragas_eval.rag_retriever.Retriever`），参数为 RRF k=60、BM25 和 Dense 候选池各 25。BM25 使用同一个 `search_bm25`，只保留分数大于 0 的结果。

## 标注格式（`examples/questions.jsonl`）

每行一道题，字段如下：

- `id`、`category`
- `question`
- `answerable`
- `reference_answer`
- `evidence_slots`：证据槽列表。每个槽有若干备选组 `groups`，一个组由若干证据项组成，证据项写作 `{"doc": 文件名, "quote": 原文片段}`。

评估时，`quote` 会忽略空白，与构建好的语料做匹配，解析成对应的 Chunk ID。因此重新切分语料后，标注仍然有效。找不到原文片段时，`validate` 会报错。

- 无答案题：`answerable: false`，并写明 `refusal_rule.correct` 和 `refusal_rule.incorrect`。
- 限定文档的题：类别为 `doc_scoped`，并填写 `scope_doc`。

## 指标定义

下文中，top 表示检索结果，R 表示所有证据项解析出的相关 Chunk 的并集。

| 指标 | 定义 |
|---|---|
| Hit@K | top-K 中至少有一个相关 Chunk 记 1，否则记 0 |
| Recall@K | \|R ∩ top-K\| / \|R\|，即 Chunk 级召回 |
| MRR@10 | top-10 中第一个相关 Chunk 名次的倒数；没有则为 0 |
| nDCG@K | 二值相关度：DCG = Σ rel_i / log2(i+1)；IDCG 为 min(\|R\|, K) 个相关 Chunk 排在最前时的 DCG |
| SlotFullCov@K | **所有**证据槽都在 top-K 内被覆盖才记 1。槽之间为 AND，组之间为 OR，组内的证据项之间为 AND；一个证据项只要它的任一 Chunk 出现即算覆盖。对于多证据问题，只命中任意一个 Chunk 不算完整 |
| SlotRecall@K | 被覆盖的证据槽所占比例 |
| 完成名次 | 所有证据槽都被覆盖时的最小 K（只看 top-10） |
| 失败原因（按 top-5 判断，即生成上下文的大小） | `covered`：全部覆盖；`truncation`：缺失的证据在候选池中，但排名靠后；`recall_failure`：缺失的证据不在候选池中；`mixed`：两种情况都有 |

**不是 Recall 的旧指标**：`scripts/10_eval_retrieval.py` 的 `compute_source_hit`（原名 `compute_recall`）只判断 top-K 中是否有某个 Chunk 来自期望的**文件**。即使检索到的是这份文件里错误的 Chunk，它也记为命中。它属于 Source Hit@K，不是 Recall@K；新的评估入口不使用它。

### 示例：Recall@K 与 SlotFullCov@K 的区别

> 这只是教学示例，A1、A2 等都是虚构的 Chunk 名称，**不是**本项目的真实实验结果。

某问题需要两个证据槽：

- **槽 A**：可以由证据 A1 和 A2 **共同**满足（组 1：A1 AND A2），**或者**由完整的替代证据 A3 **单独**满足（组 2：A3）；
- **槽 B**：必须由 B1 满足。

写成标注结构就是 `slots = [[[A1, A2], [A3]], [[B1]]]`。

假设 top-K 返回 A3 和 B1：

| 指标 | 计算 | 结果 |
|---|---|---|
| 槽 A | 组 2（A3）完整 → 覆盖 | ✓ |
| 槽 B | B1 → 覆盖 | ✓ |
| SlotFullCov@K | 两个槽都覆盖 | **1** |
| SlotRecall@K | 2 / 2 | 1.0 |
| Recall@K | R = {A1, A2, A3, B1}，命中 {A3, B1} | **2/4 = 0.5** |

Recall 只有 0.5，但回答这道题所需的证据其实已经齐全。原因是普通 Chunk Recall 把所有备选证据都放进同一个相关集合 R：A1+A2 与 A3 是**等价的两条路**，只需要走通一条，而 Recall 要求两条路都走，于是没检索到的备选路径（A1、A2）被算作缺失。证据存在等价替代时，Recall 会**低估**完整覆盖程度。

反过来也可能高估：如果 top-K 返回 A1 和 A3（没有 B1），Recall 同样是 2/4 = 0.5，但槽 B 没被覆盖，SlotFullCov@K = 0，生成时会缺少回答所需的证据。

所以两个指标要一起看：Recall@K 反映检索到的相关 Chunk 有多少，SlotFullCov@K 反映生成所需的证据是否齐全。上面的数字可以用 `ragas_eval/retrieval_metrics.py` 现有的函数直接算出，本示例没有改动其评分逻辑。

## 适用范围

公开示例共 6 篇原创文档、36 个子 Chunk、22 道题（18 道可答，4 道无答案），只用于**功能验证**：确认建库、检索、评分和问答流程可以跑通。它不是研究项目的 39 题评估集，也不能用来证明泛化能力。在这样小的语料上，各模式的指标都接近饱和，不能据此比较优劣。

生成侧的检查（`python -m ragas_eval.example_qa`）同样只做确定性的功能检查，不评估答案质量，也不产生 Faithfulness 之类的分数。
