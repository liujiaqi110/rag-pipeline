# 企业知识库管理平台（2.9）开发文档

分支：`feature/knowledge-platform-acl`。本文记录各阶段的目标、设计、完成情况和下一阶段任务。

## 阶段总览

| 阶段 | 内容 | 状态 |
|---|---|---|
| 第一阶段 | ACL 权限管理：身份模型、文档级策略、默认拒绝、检索候选阶段过滤、强制安全层、隔离测试 | **已完成**（本分支） |
| 第二阶段 | 企业知识库后端：FastAPI + Pydantic + SQLAlchemy（SQLite），认证、身份与 ACL 持久化、知识库/文档 API、ACL 问答 API、会话隔离、审计、OpenAPI、API 测试 | **已完成**（本分支） |
| 第三阶段 | 文档管理与自动索引：四种格式上传/批量上传、解析→清洗→切片（复用 03/04/05）→BM25/Dense 索引代际、版本、停用/删除、知识库管理者、故障恢复 | **已完成**（本分支） |
| 遗留 | Alembic 迁移与 PostgreSQL 验证、OIDC/SSO、异步任务队列、按租户建索引等（见各阶段“遗留问题”） | 待开始 |
| 后续 | 接入 4.1 RAG 模型微调实验 | 未开始，需确认后再进行 |

## 第一阶段：ACL 权限管理

### 新增模块

| 文件 | 作用 |
|---|---|
| `ragas_eval/acl.py` | `Principal`（用户、角色、部门、密级、启用状态）、`DocumentPolicy`、`decide()` 唯一判定规则、`AclPolicy`（加载并严格校验策略文件）、`AccessScope`（单次请求的授权文档/Chunk 集合）、`FilteredBM25View` |
| `ragas_eval/acl_retrieval.py` | `AclRetriever`：在候选阶段约束 BM25 / Dense / RRF |
| `ragas_eval/kb_service.py` | `KnowledgeBaseService`：平台入口，强制安全层、Prompt 闸门、输出闸门、ID 脱敏；CLI `python -m ragas_eval.kb_service --user <id> "问题"` |
| `examples/acl_corpus/` | 合成测试语料（6 篇虚构文档，每篇带唯一 canary 标记）与示例策略 `acl_policy.json`（8 个虚构用户） |
| `ragas_eval/tests/test_acl_policy.py` 等 3 个测试文件 + `acl_fixtures.py` | 45 个新增单元测试 |

唯一改动的旧代码：`rag_retriever.Retriever.dense` 增加可选参数 `allowed`（默认 `None`，行为与之前完全相同）。

### 访问规则（`acl.decide`，按顺序）

1. 用户不在目录中 → 拒绝（`unknown_user`）；`active=false` → 拒绝（`inactive_user`）。
2. 文档没有策略 → 拒绝（`no_policy`，**默认拒绝**）。
3. 命中 `deny.users / deny.roles / deny.departments` → 拒绝（**拒绝优先**）。
4. 用户密级 < 文档密级（public 0 / internal 1 / confidential 2 / restricted 3）→ 拒绝。密级是与授权同时满足的条件。
5. 命中 `allow.users / allow.roles / allow.departments` 或 `allow.all_authenticated` → 允许；否则拒绝。

没有内置超级用户：`executive` 角色也只能看到策略授予的文档。策略文件中出现未知字段、通配符 `*`、未知密级、非法类型或缺少 `policy_version` 时直接报错，服务不启动（fail closed）。

身份只来自策略文件中的用户目录。调用方只传 `user_id`，角色、部门、密级不能由请求携带。

### 检索阶段的权限过滤（不是在最终 top-5 上过滤）

如果只过滤最终 top-5，未授权 Chunk 会先占满 BM25/Dense 各 25 个候选名额和 RRF 排名，授权用户最终可能拿不到任何上下文；未授权文本也会在内存中靠近 Prompt 构建步骤。本实现：

- **BM25**：原始 `search_bm25` 不做修改，作用在只包含授权 Chunk 的 `FilteredBM25View` 上，未授权 Chunk 不参与打分。IDF 和平均文档长度沿用全库统计，因此授权 Chunk 的分数与无 ACL 时完全相同。
- **Dense**：`Retriever.dense(..., allowed=...)` 在填充候选池时跳过未授权 ID，并且在截取 k 个之前完成，所以 25 个名额全部是授权候选。
- **RRF**：原始 `rrf_fuse` 不做修改（k=60，候选池 25/25），融合两个授权候选池。
- 每个阶段的输出都再检查一次（`assert_within_scope`）；如果底层检索器不支持 `allowed` 参数，构造时直接拒绝，不会退化为事后过滤。
- `AclRetriever.bm25` 就是过滤后的视图，因此下游的 Pipeline 文本表、安全层用于文档范围识别的文档列表、引用校验用的 corpus ID 都只包含授权内容。这样“根据 <无权文档> 中……”不会变成判断文档是否存在的探测手段。

测试 `test_acl_retrieval.TestCandidateStageFiltering` 构造了 30 个高度匹配的机密 Chunk 和 8 个公开 Chunk：不加 ACL 时，top-5 和两个 25 名额候选池全部是机密 Chunk，事后过滤 top-5 会得到 0 条；候选阶段过滤后，公开用户能得到完整的 top-5。

### 生成与输出的防线

1. **安全层强制开启**：平台入口始终使用 `SafeRagPipeline`。`safety=False`、`RAG_SAFETY_LAYER=off` 和 `config/safety_layer.json` 都不起作用，绕过尝试会记录在审计信息 `override_attempts_ignored` 中。
2. **Prompt 闸门**（`PromptGuardLLM`）：调用模型前检查每个 `<资料 编号=…>` 块，编号必须在授权范围内，而且每个资料块都必须带编号，否则不调用模型（状态 `blocked`）。问题中伪造的资料块同样会被拦截。
3. **输出闸门**：`retrieved_ids / context_ids / valid_citations` 再检查一次；然后对整个响应（包括审计块的键和值）做脱敏，所有不在授权范围内的语料 Chunk ID 都替换成 `[已隐藏]`。
4. **错误信息**：只输出异常类型名称，不输出异常消息。用户不存在、已停用、没有任何授权文档时返回同一段文字（`access_denied`），不调用模型，也不会暴露用户是否存在。
5. **审计块**：只包含授权 ID 和计数（`acl.authorized_documents / authorized_chunks`），不列出用户无权访问的文档名称。

### 缓存与会话隔离

- 每次请求重新构建 `AccessScope`、`AclRetriever`、Pipeline 和 Prompt 闸门，服务实例不保存任何用户的请求结果。
- 底层检索器的查询向量缓存在每次请求开始时清空；由于底层对象是共享的，请求在锁内串行执行（本地 Ollama 本来就是串行生成）。
- `AccessScope.cache_key` = 主体指纹 + 策略哈希。以后增加结果缓存时必须用它作为键，不能只用查询文本作为键。
- 测试覆盖：交替请求（alice → bob → alice）结果一致且互不包含对方的内容；6 线程 24 个混合用户的并发请求互不串用。

### 验收结果

| 验收项 | 结果 | 对应测试 |
|---|---|---|
| 未授权文档不进入 Prompt | 通过：9 个用户 × 7 个探测问题，Prompt 中没有出现无权文档的 canary 或 Chunk ID | `TestNoLeakage.test_unauthorised_content_never_reaches_prompt_or_output` |
| 未授权文档不进入引用、错误信息、调试输出 | 通过 | 同上，以及 `test_model_citing_a_foreign_real_chunk_is_blocked_and_redacted`、`test_error_output_contains_no_unauthorised_data` |
| 测试灵敏度（阳性对照） | 旧版单租户服务确实会泄漏 HR 和法务内容 | `test_positive_control_the_unprotected_service_would_leak` |
| 合法用户正常检索已授权知识 | 通过：6 类授权路径（部门、角色、全员、拒绝后剩余） | `TestAuthorisedAccess` |
| 无缓存或会话串用 | 通过 | `TestIsolation` |
| 全权限用户的结果与旧安全流水线一致 | 通过 | `test_mock_llm_output_unchanged_for_full_access_principal`、`test_full_access_is_identical_to_the_unfiltered_retriever` |
| 原有 RAG 回归 | 原有 57 个测试全部通过（6 个跳过，与修改前相同）；示例集 BM25 评估的聚合指标与已提交的 `build/eval` 完全一致 | `python -m unittest discover -s ragas_eval/tests -t .` |

测试总数：102 个（原有 57 个 + 新增 45 个），本机全部通过，跳过 6 个（都是原有测试，依赖不随公开仓库发布的研究输出）。CI 环境没有安装 numpy，新增测试中有 2 个需要 numpy 的会跳过；已在屏蔽 numpy 的环境中验证，其余 43 个新增测试都能通过。

### 已知限制（第一阶段）

- BM25 的 IDF 和平均文档长度使用全库统计。打分不对外输出，但理论上未授权文档的词频会轻微影响授权 Chunk 的排序。如需严格隔离，可以按租户分别建索引（第二阶段）。
- Dense 打分仍然对整个矩阵做一次矩阵乘法（开销很小），只是未授权 Chunk 不会进入候选。
- 策略的粒度是文档（`source_file`），没有 Chunk 级或段落级策略，也没有部门层级继承。
- 旧的 `rag_service`（单租户，没有 ACL）和 `retrieval_eval` 保持不变，**不能**作为平台入口使用。
- 请求串行执行；多进程或多副本部署时，需要为每个工作进程单独创建检索器实例。
- 用户目录是 JSON 文件，尚未对接 LDAP、SSO 或 IAM，也没有身份认证（调用方需要自行完成认证，再传入 `user_id`）。
- `tests_local/` 中的重型集成测试（bge-m3 + Ollama）本阶段没有新增 ACL 用例，本机也没有运行。

## 第二阶段：企业知识库后端（kb_platform）

### 技术选型与结构

FastAPI + Pydantic v2 + SQLAlchemy 2.0，默认 SQLite（`KB_DATABASE_URL`，测试使用内存库）。模型只用可移植的列类型（String、Integer、Boolean、Text、DateTime(timezone)、JSON），切换到 PostgreSQL 只需改 `KB_DATABASE_URL`（需要另装驱动，尚未实测）。依赖见 `requirements-api.txt`，安装在独立的 `.venv` 中，不影响原有环境。

| 文件 | 作用 |
|---|---|
| `kb_platform/models.py` | 用户、角色、部门、知识库、文档、文档授权（allow/deny × user/role/department/all_authenticated）、Token（只存哈希）、会话、消息、审计日志、ACL 版本号 |
| `kb_platform/security.py` | scrypt 密码哈希；随机不透明 Token，库中只存 SHA-256；过期、注销、停用即失效；连续失败锁定 |
| `kb_platform/acl_store.py` | 数据库 → 第一阶段 `AclPolicy`：每次请求只包含**当前用户**和**当前知识库的有效文档**；配置错误抛出 `PolicyError`，调用方一律拒绝 |
| `kb_platform/rag_backend.py` | 唯一的问答通道：第一阶段 `KnowledgeBaseService`（候选阶段 ACL + 强制安全层）；答案缓存的键为（主体指纹 + 策略哈希, 知识库, 问题哈希） |
| `kb_platform/audit.py` | 审计写入（白名单字段，问题只存哈希和长度）；日志过滤器屏蔽 Bearer Token、password/token 键值和 Token 形状的字符串 |
| `kb_platform/routers/*.py` | auth / admin / knowledge / chat / audit 五组路由 |
| `kb_platform/app.py` | 应用工厂、统一错误格式、请求 ID、`Cache-Control: no-store` |
| `kb_platform/cli.py` | `init-db`、`create-admin`（管理员只能用 CLI 创建）、`seed-example`（导入合成用户，随机密码只写入本地文件）、`export-openapi` |
| `docs/openapi.json` | OpenAPI 快照；有测试保证它与代码一致 |

### API 清单（前缀 `/api/v1`）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/health` | 公开 | 健康检查，不含数据 |
| POST | `/auth/login` | 公开 | 用户名密码登录，返回 Bearer Token；各种失败返回同一条消息 |
| POST | `/auth/logout` | 登录 | 注销当前 Token |
| GET | `/auth/me` | 登录 | 当前身份（只来自 Token） |
| POST | `/auth/password` | 登录 | 修改密码，所有 Token 失效 |
| POST/GET | `/admin/roles` | 管理员 | 创建 / 列出角色 |
| POST/GET | `/admin/departments` | 管理员 | 创建 / 列出部门 |
| POST/GET | `/admin/users` | 管理员 | 创建 / 列出用户 |
| GET/PATCH | `/admin/users/{username}` | 管理员 | 查看；修改角色、部门、密级、启用状态、重置密码（旧 Token 失效）。`is_admin` 不能通过 API 修改 |
| POST/GET | `/knowledge-bases` | 创建：管理员；列表：登录 | 普通用户只看到至少有一篇可读文档的知识库 |
| GET | `/knowledge-bases/{kb_id}` | 登录 | 不可见时返回 404 |
| POST/GET | `/knowledge-bases/{kb_id}/documents` | 登记：管理员；列表：登录 | 新文档没有任何授权（默认拒绝）；普通用户只看到可读文档 |
| GET/PATCH | `/documents/{doc_id}` | 查看：登录；修改/归档：管理员 | 无权限与不存在返回相同的 404 |
| GET/PUT | `/documents/{doc_id}/acl` | 管理员 | 查看 / 整体替换 ACL；主体必须已存在，不允许通配符 |
| POST/GET | `/sessions` | 登录 | 在可读知识库中创建会话 / 只列出自己的会话 |
| GET/DELETE | `/sessions/{id}` | 会话所有者 | 他人会话返回 404；权限变更前生成的回答会被隐藏 |
| POST | `/sessions/{id}/ask` | 会话所有者 | ACL 问答，只返回 answer / citations / status |
| GET | `/audit` | 管理员 | 审计日志，可按 action、actor 过滤 |

交互式文档：`/docs`（Swagger UI）、`/redoc`、`/openapi.json`。

### 安全设计与验证

| 要求 | 实现 | 测试 |
|---|---|---|
| 不能通过请求参数指定其他用户 | 身份只来自 Bearer Token；请求体 `extra="forbid"`（带 `user_id`、`roles`、`clearance` 一律返回 422）；查询参数和 `X-User-Id` 等请求头被忽略 | `TestNoIdentitySpoofing` |
| 不能绕过 ACL 调用旧入口 | `kb_platform` 中没有任何模块导入 `rag_service`（AST 检查）；后端只构造 `KnowledgeBaseService`；`RAG_SAFETY_LAYER=off` 无效 | `TestLegacyEntryNotExposed`、`test_safety_layer_cannot_be_switched_off` |
| 未授权信息不进入 Prompt、引用、错误信息 | 复用第一阶段的候选阶段过滤和两道闸门；元数据也按 ACL 过滤；校验错误不回显输入；500 只返回通用消息，日志只记录异常类型 | `TestQuestionAnswering`、`TestMetadataVisibility`、`TestErrorHygiene` |
| 缓存和会话按用户与权限范围隔离 | 缓存键包含主体指纹和策略哈希，权限变化后键随之改变；会话绑定所有者；历史回答带 scope_key，权限变化后隐藏 | `TestCacheAndSessionIsolation`、`test_revocation_takes_effect_and_hides_old_answers` |
| 权限配置错误时默认拒绝 | 库中出现非法授权类型或密级时 `PolicyError` → 整个知识库对所有人不可见，问答返回 `access_denied`，审计记录 `policy_error` | `test_broken_configuration_fails_closed` |
| 日志中不出现 Token、密钥、敏感原文 | 审计字段白名单；问题只存哈希；失败登录只记录**已存在的**用户名（误把密码输入到用户名框时不会被记录）；日志过滤器 | `TestLogsAndAudit` |

测试发现并已修复：失败登录最初会把用户名框中的任意内容写入审计，而随机生成的密码恰好也符合用户名格式。现在只记录数据库中存在的用户名。

### 本地运行

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-api.txt
.venv\Scripts\python -m unittest discover -s kb_platform/tests -t .
$env:KB_ADMIN_PASSWORD="<至少10位，含字母和数字>"
.venv\Scripts\python -m kb_platform.cli create-admin --username admin
.venv\Scripts\python -m kb_platform.cli seed-example
.venv\Scripts\python -m uvicorn kb_platform.app:create_app --factory --port 8000
```

真实问答还需要 `RAG_DATA_DIR` 指向包含 Dense 索引的数据目录（`python scripts/build_example_index.py --docs examples/acl_corpus/docs --out build/acl_example_data --dense`）、本机 bge-m3（需要安装 sentence-transformers）和运行中的 Ollama。没有这些时，问答接口返回 503 `index_unavailable`，其余接口照常可用。

### 第二阶段遗留问题

1. 数据库迁移：目前使用 `create_all`，还需要引入 Alembic，并在真实 PostgreSQL 上运行一遍测试。
2. 身份：本地账号 + 不透明 Token；尚未对接 OIDC/SSO，没有 MFA，登录接口除按账号锁定外没有按 IP 限流。
3. 文档入库：API 只管理元数据；索引仍需离线用 `scripts/build_example_index.py` 构建。登记的 `doc_key` 必须与索引中的 `source_file` 一致，没有登记的索引文档对所有人不可见。
4. 检索器是进程内共享对象，请求在锁内串行执行；多副本部署时，每个进程各自加载索引，答案缓存也是进程内的（不跨进程共享，因此不会串用，但命中率会下降）。
5. 会话历史在数据库中保存问题和回答原文（这是用户自己的数据，不是日志）；尚未实现保留期限和加密存储。
6. 按租户或密级分别建索引、`retrieval_eval` 支持 ACL、`tests_local` 增加真实模型的 ACL 用例，仍未完成。
7. 真实 bge-m3 + Ollama 的端到端问答在本机没有验证（缺少 sentence-transformers 和模型）；已用真实 uvicorn + HTTP + 合成语料 + 模拟 LLM 完成冒烟测试。

## 第三阶段：文档管理与自动索引

### 开始前检查结论

- **Token**：第二阶段的需求原文没有指定 JWT；实现的是服务端保存哈希的随机不透明 Token。它能立即注销或撤销（登出、停用、权限变更后旧 Token 立即失效），而 JWT 在过期前通常无法撤销，除非另加黑名单。本阶段**没有改为 JWT**；如需 JWT（例如对接 OIDC），列为遗留项。
- **撤权后的历史会话**：每条回答都记录了生成时的 scope_key（主体指纹 + 策略哈希），撤权后历史回答被隐藏（第二阶段测试已覆盖，本阶段又在上传文档上验证了一遍）。
- **缓存**：第二阶段的缓存键不包含索引内容版本，文档内容更新后可能命中旧答案。本阶段把**索引代际 ID** 加入缓存键，并在每次索引切换后清空缓存。
- **认证边界**：上传接口的表单里即使带 `user_id`、`created_by`，`created_by` 也只取 Token 中的用户（有测试）。

### 处理流水线

```
上传（大小 / 扩展名 / 内容魔数校验）→ 服务器命名存储 build/kb_storage/uploads/<文档>/<版本>/original.<ext>
→ 解析（md / txt：UTF-8、GB18030；pdf：pypdf 按页；docx：python-docx，标题样式转 #，表格转 a | b）
→ 清洗（NFC、换行、单独成行的页码、PDF 断行合并；不删除“机密/Copyright”等可能是正文的内容）
→ 切片：原样调用 scripts/03 chunk_document → 04 fix_chunks → 05 enrich_chunks + build_parent_child
→ Chunk 元数据：source_file（= 文档唯一 doc_key）、页码（PDF）、段落序号、Chunk ID（03/05 原算法）
→ 索引代际：scripts/06 SimpleBM25 + 与 06 相同格式的 Dense（bge-m3，按版本缓存向量）
→ 校验后在一个事务中激活 → 可检索
```

- 切片没有重写：有测试证明对公开示例文档，平台切片结果与 `scripts/build_example_index.py` 的 Chunk ID、文本、父子关系完全一致。
- 检索没有重写：`PlatformRetriever` 继承 `rag_retriever.Retriever`，只改变索引文件和模型的加载方式，`dense / bm25_full / rrf` 是原方法。
- 新增的检索相关代码只有 `RagPipeline` 的显式 `bm25` 模式（`KB_RETRIEVAL_MODE=bm25`），供没有向量模型的部署使用；默认仍是 RRF。RRF 模式下缺少 Dense 时返回 503，**不会悄悄退化为 BM25**。

### 一致性规则

1. 数据库是唯一的事实来源。索引代际从数据库构建：每个**启用**的上传文档取最新的待索引版本，没有则取当前版本。
2. 新代际写入临时目录，重新加载校验后再改名；然后在**一个事务**里：新代际设为 active、版本设为 indexed 并成为当前版本、旧版本设为 superseded、已删除文档的 Chunk 和文件被清除。
3. 任何一步失败：旧代际和所有当前版本保持不变，待索引版本标记为 `index_failed`，可通过 `POST /api/v1/index/rebuild` 恢复。
4. 停用、归档、删除立即从 ACL 策略中排除（重建之前也查不到），并从下一代索引中移除。
5. 缺少本地模型：`KB_DENSE=auto` 时构建只含 BM25 的代际，并记录 `dense_error=embedding_model_unavailable`；RRF 问答返回 503 `dense_unavailable`；安装模型后重建即可恢复。`KB_DENSE=required` 时构建失败，旧索引保持不变。

### 权限

- 新上传的文档**没有任何读取授权**，上传者和平台管理员也读不到内容。
- 授权方式保留四种：global（`all_authenticated`）、department、role、user。
- 操作权限（上传、版本、修改、停用、删除、修改 ACL）：平台管理员，或该知识库的**知识库管理者**（新增，由平台管理员指定）。管理权限不包含阅读权限：管理者能看到 Chunk 的元数据，但看不到正文，问答中也检索不到，除非另有 ACL 授权。
- 索引构建不涉及 ACL：每次请求仍由第一阶段的引擎按数据库策略在候选阶段过滤。

### 新增 API（前缀 `/api/v1`）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/knowledge-bases/{kb_id}/documents/upload` | 管理者 | 单文件上传并自动处理、建索引；`on_duplicate=reject|new_version|skip`，`build_index=true|false` |
| POST | `/knowledge-bases/{kb_id}/documents/batch-upload` | 管理者 | 批量上传；逐个文件返回结果，单个失败不影响其他文件，最后统一建一次索引 |
| POST/GET | `/documents/{doc_id}/versions` | 管理者 | 上传新版本 / 版本列表 |
| GET | `/documents/{doc_id}/status` | 可读用户或管理者 | 处理与索引状态、是否可检索 |
| GET | `/documents/{doc_id}/chunks` | 可读用户或管理者 | Chunk 列表（页码、段落）；正文只给有阅读权限的用户 |
| POST | `/documents/{doc_id}/disable`、`/enable` | 管理者 | 停用 / 启用 |
| DELETE | `/documents/{doc_id}` | 管理者 | 删除：立即失效，重建成功后清除 Chunk 和文件 |
| GET/POST/DELETE | `/knowledge-bases/{kb_id}/managers` | 平台管理员 | 知识库管理者 |
| GET | `/index/status` | 平台管理员 | 当前代际、最近代际、向量模型状态、待索引/失败版本数 |
| POST | `/index/rebuild` | 平台管理员 | 从数据库重建：恢复失败、补建 Dense、清除已删除文档 |

已有接口没有删除；`PATCH /documents/{id}` 的 `status` 增加了 `disabled`，`DocumentOut` 增加了 `source`、`original_filename`、`current_version_id` 字段（均为新增）。

### 测试

| 类别 | 数量 | 说明 |
|---|---|---|
| P1 `ragas_eval/tests` | 102（通过 96，跳过 6） | 跳过的都是依赖未发布研究输出的原有测试 |
| P2 + P3 `kb_platform/tests` | 65（全部通过） | 其中 P3 新增 26 个：解析与切片 11 个、上传/索引 API 15 个 |
| 真实集成 `tests_local` | 4（全部通过，本机运行，CI 不运行） | `test_kb_platform_real.py` 2 个（真实 bge-m3 + 真实 Ollama qwen2.5:7b），原有 `test_local_integration.py` 2 个 |

`kb_platform/tests` 中的 Dense 使用**哈希假向量**（`FakeEmbedder`，不是语义模型），只用来覆盖 Dense 代码路径；语义检索效果只以 `tests_local/test_kb_platform_real.py` 的结果为准。

### 第三阶段遗留问题

1. 处理是同步的（上传请求内完成解析和建索引）；大文件或大量文档需要任务队列（Celery/RQ）和进度查询。
2. 每次建索引都是全量重建 BM25（向量按版本缓存，不会重复编码）；文档量大时需要增量索引或分片。
3. PDF 不支持 OCR（扫描件返回 `no_text`）；PDF 表格和多栏排版的解析质量没有评估；中文 PDF 只在真实环境中用 pypdf 验证过文本型文件（测试用 PDF 是英文的，因为生成中文 PDF 需要嵌入字体）。
4. 页码通过在清洗后的文本中查找 Chunk 文本来定位；被 04 边界修正大幅改写的 Chunk 可能定位不到（页码为空）。
5. 单进程：构建锁和检索器缓存都在进程内；多副本部署需要分布式锁和共享存储。
6. 数据库新增了列（`documents.source / original_filename / current_version_id`），没有 Alembic；第二阶段生成的本地 SQLite 库需要删除后重新初始化。
7. 被删除文档的审计记录和版本元数据会保留（用于审计），文件和 Chunk 会被清除；尚未提供“彻底清除元数据”的接口。

## 后续阶段：接入 4.1 RAG 模型微调实验（未开始，需确认）

1. 训练/评估样本只从授权上下文中构造，每条样本记录 `user_id` 和 `policy_version`，禁止把跨权限的上下文拼成同一条样本。
2. 拒答样本：用“无权访问”的问题构造正确拒答的训练数据，让模型不会凭记忆回答受限内容。
3. 微调后的模型重新跑一遍本文的泄漏测试（canary 探测）：模型权重本身也不能记住受限语料中的 canary。
4. 训练语料只使用合成或已脱敏的公开数据；不读取私有仓库中的敏感语料。
