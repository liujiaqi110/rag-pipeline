# 本地 RAG 问答（v1.1 公开发布版）

本仓库只包含**代码、配置示例、文档与测试**。检索语料和索引不在仓库中（属私有资料），需要放在单独的数据目录并用环境变量 `RAG_DATA_DIR` 指定。

## 组成

- 检索：BM25 + Dense（BAAI/bge-m3）RRF 融合（k=60，候选池 25/25），top-5，6000 字符上下文
- 生成：本地 Ollama `qwen2.5:7b`，Prompt `rag_v1.0`，temperature 0
- 安全层：引用 ID 精确校验、完全拒答引用清理、文档来源归属保护（`config/safety_layer.json`）

## 环境

- Windows + PowerShell，Python 3.12；依赖参考 `requirements-v1.1.txt`（不会自动安装）
- 本地 Ollama（127.0.0.1:11434）且已有模型 `qwen2.5:7b`（程序不会自动下载）
- 本地缓存的 `BAAI/bge-m3`
- 不需要任何云端 API Key

## 数据目录（私有，不随仓库发布）

```
<RAG_DATA_DIR>/
  corpus/clean_md/*.md          # 已脱敏的 Markdown 语料
  corpus/final_chunks.jsonl     # 由 scripts/03→05 生成
  index/sparse/bm25_index.pkl   # 由 scripts/06 生成
  index/dense/dense_index.pkl
```

从语料重建索引：

```powershell
$env:HF_HUB_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"; $env:PYTHONIOENCODING="utf-8"
$D="D:\path\to\rag_data"
python scripts\03_structure_split.py --input "$D\corpus\clean_md" --output "$D\corpus\draft_chunks.jsonl"
python scripts\04_boundary_guard.py --input "$D\corpus\draft_chunks.jsonl" --output "$D\corpus\fixed_chunks.jsonl"
python scripts\05_context_inheritance.py --input "$D\corpus\fixed_chunks.jsonl" --output "$D\corpus\final_chunks.jsonl"
python scripts\06_build_dense_sparse_index.py --input "$D\corpus\final_chunks.jsonl" --output-dir "$D\index" --dense
```

## 使用

```powershell
$env:RAG_DATA_DIR="D:\path\to\rag_data"
python -m ragas_eval.rag_service "你的问题"
python -m ragas_eval.rag_service "你的问题" --audit
python -m ragas_eval.rag_service "你的问题" --no-safety
```

## 测试

```powershell
python -m unittest discover -s ragas_eval/tests -t .
```

测试使用模拟生成器，不需要 Ollama 和数据目录。

运维、回退与故障排查见 `docs/RUNBOOK.md`，已知问题见 `KNOWN_ISSUES.md`。
