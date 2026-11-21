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

## 示例知识库与评估（v1.2）

```powershell
python scripts/build_example_index.py --dense --force
python -m ragas_eval.retrieval_eval run --data build/example_data --modes bm25,dense,rrf --out build/eval
python -m ragas_eval.example_qa --data build/example_data --out build/example_qa
```

| 现象 | 处理 |
|---|---|
| `Dense index was NOT built` | 本机没有缓存 BAAI/bge-m3；自行下载，或加 `--allow-download` |
| `exists; use --force` | 输出目录已存在；`--force` 只会替换由该脚本创建的目录 |
| `annotation errors` / `quote not found` | 标注里的原文片段在语料中找不到；修改标注或语料后重新建库 |

## 知识库 ACL（kb_service）

```powershell
python scripts/build_example_index.py --docs examples/acl_corpus/docs --out build/acl_example_data --dense
$env:RAG_DATA_DIR="build/acl_example_data"; $env:RAG_ACL_POLICY="examples/acl_corpus/acl_policy.json"
python -m ragas_eval.kb_service --user eng_alice "发布前需要做哪些检查？" --audit
```

| 现象 | 处理 |
|---|---|
| `PolicyError` | 策略文件缺失或不合法（未知字段、通配符、未知密级）；服务按设计拒绝启动 |
| `access_denied` | 用户不在目录中、已停用或没有任何授权文档（三种情况返回相同的提示） |
| `blocked` | 权限闸门拦截（例如问题中伪造了资料块）；不调用模型 |
| 关闭安全层无效 | 设计如此：kb_service 忽略 `--no-safety` / `RAG_SAFETY_LAYER=off`，见审计中的 `override_attempts_ignored` |

## 知识库后端（kb_platform）

```powershell
.venv\Scripts\python -m pip install -r requirements-api.txt
$env:KB_DATABASE_URL="sqlite:///./build/kb_platform.db"
.venv\Scripts\python -m kb_platform.cli create-admin --username admin   # 需先设置 KB_ADMIN_PASSWORD
.venv\Scripts\python -m uvicorn kb_platform.app:create_app --factory --port 8000
```

| 现象 | 处理 |
|---|---|
| 登录总是 401 | 用户名或密码错误、账号已停用或已被锁定（连续失败 5 次，锁定 15 分钟），几种情况返回同一条消息；看审计 `auth.login` |
| 修改用户后要求重新登录 | 设计如此：角色、部门、密级、启用状态或密码变更后，旧 Token 立即失效 |
| 问答返回 `access_denied` | 用户在该知识库中没有可读文档，或 ACL 配置错误（审计中 `reason=policy_error`） |
| 503 `index_unavailable` | `RAG_DATA_DIR` 下没有 Dense 和 BM25 索引 |
| 历史回答显示“内容已隐藏” | 该回答生成后用户的权限或文档 ACL 已变更 |

## 测试

- `python -m unittest discover -s ragas_eval/tests -t .`：软件测试，与 CI 相同。
- `.venv\Scripts\python -m unittest discover -s kb_platform/tests -t .`：知识库后端 API 测试（CI 中同样运行）。
- `python -m unittest discover -s tests_local -t .`：本地重型集成测试，需要 bge-m3 和 Ollama，不在 CI 中运行。
