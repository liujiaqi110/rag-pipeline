# 运维手册

## 安全层开关（先匹配者生效）

命令参数 `--safety` / `--no-safety` → 环境变量 `RAG_SAFETY_LAYER=on|off` → `config/safety_layer.json` → 关闭。

```powershell
python -m ragas_eval.rag_service "问题" --no-safety
$env:RAG_SAFETY_LAYER="off"
Remove-Item Env:RAG_SAFETY_LAYER
```

永久关闭：把 `config/safety_layer.json` 的 `enabled` 改为 `false`。

## 故障排查

| 现象 | 处理 |
|---|---|
| `index not found` | 设置 `RAG_DATA_DIR` 指向含 `index/` 的数据目录 |
| `generation_error` / `unavailable` | 启动 Ollama，`ollama list` 检查 |
| `model_missing` | 本机没有 `qwen2.5:7b`；程序不会自动下载 |
| 中文乱码 | `$env:PYTHONIOENCODING="utf-8"` |
| `insufficient_scoped_evidence` | 问题指定了某份文档但检索结果中没有，属于检索失败的如实报告 |
