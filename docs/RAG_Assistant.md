# 使用 PostgreSQL 构建 RAG 与记忆库

项目使用同一个 PostgreSQL 数据库保存三类数据：

- LangGraph 会话检查点和聊天历史
- 跨会话长期记忆 Store
- RAG 文档、元数据和百炼 Embedding 向量

RAG 使用百炼 `qwen3.7-text-embedding`，与聊天和语音复用同一个
`DASHSCOPE_API_KEY`，不需要 OpenAI 账号。当前实现使用 PostgreSQL 原生数组保存
向量并在应用层计算余弦相似度，因此本地 PostgreSQL 不需要安装 `pgvector` 扩展。

## 配置 PostgreSQL

本地运行需要以下环境变量：

```env
DATABASE_TYPE=postgres
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your-password
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=agent_service
RAG_COLLECTION_NAME=acmetech-employee-handbook
RAG_TOP_K=5
```

数据库必须提前创建。服务首次启动时会自动创建 LangGraph Checkpointer 和 Store
所需的表，RAG 导入脚本会自动创建 `rag_documents` 表。

Docker Compose 会自动启动 PostgreSQL，并将服务容器内的 `POSTGRES_HOST` 设置为
`postgres`。直接运行 Python 时使用 `127.0.0.1`。

## 导入文档

把 PDF 或 Word 文件放入 `data/`，然后从仓库根目录运行：

```sh
uv run python scripts/create_postgres_rag.py data
```

脚本会读取文档、分块、调用百炼生成 Embedding，并原子替换指定集合中的旧文档。
可以调整集合、分块大小和重叠大小：

```sh
uv run python scripts/create_postgres_rag.py data \
  --collection company-handbook \
  --chunk-size 2000 \
  --overlap 500
```

服务启动后选择 `rag-assistant` 即可查询 PostgreSQL 知识库。修改文档后重新运行导入
命令即可更新集合。

## 持久化说明

所有服务启动方式均将会话历史和长期记忆持久化到 PostgreSQL，服务重启后仍然存在。
`DATABASE_TYPE` 默认且只允许 `postgres`，PG 不可用时启动失败。原来的 `checkpoints.db` 不会被自动导入或删除。
