# 企业知识库管理平台（2.9）开发文档

分支：`feature/knowledge-platform-acl`。本文记录各阶段的目标、设计、完成情况和下一阶段任务。

## 阶段总览

| 阶段 | 内容 | 状态 |
|---|---|---|
| 第一阶段 | ACL 权限管理：身份模型、文档级策略、默认拒绝、检索候选阶段过滤、强制安全层、隔离测试 | **已完成**（本分支） |
| 第二阶段 | 平台化：策略管理 API、文档入库流程带策略、审计日志持久化、多租户索引 | 待开始 |
| 第三阶段 | 接入 4.1 RAG 模型微调实验（在 ACL 约束下构造训练/评估数据） | 待开始 |

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

## 第二阶段：平台化（下一步）

1. 策略管理：策略的版本化存储、变更审核（双人复核）、变更后立即使授权范围失效（`policy_sha256` 已包含在 `cache_key` 中）。
2. 入库流程带策略：新文档没有策略时保持默认拒绝，并在入库报告中列出“未配置策略”的文档；可选按 `owner_department` 生成默认策略草稿，草稿需要人工批准后才生效。
3. 审计日志持久化：记录每次请求的用户、策略版本、授权计数、是否拦截，不记录无权文档的信息。
4. 身份对接：用 OIDC/SSO 的 claims 映射角色和部门；服务端校验 token，不信任客户端自报的身份。
5. 按租户或密级分别建索引，消除全库 IDF 的影响；评估性能。
6. 让 `retrieval_eval` 支持 ACL：按用户统计 Recall 和泄漏数（目标是泄漏数为 0）。
7. 增加 `tests_local` 的 ACL 集成用例（真实 bge-m3 Dense + Ollama）。

## 第三阶段：接入 4.1 RAG 模型微调实验

1. 训练/评估样本只从授权上下文中构造，每条样本记录 `user_id` 和 `policy_version`，禁止把跨权限的上下文拼成同一条样本。
2. 拒答样本：用“无权访问”的问题构造正确拒答的训练数据，让模型不会凭记忆回答受限内容。
3. 微调后的模型重新跑一遍本文的泄漏测试（canary 探测）：模型权重本身也不能记住受限语料中的 canary。
4. 训练语料只使用合成或已脱敏的公开数据；不读取私有仓库中的敏感语料。
