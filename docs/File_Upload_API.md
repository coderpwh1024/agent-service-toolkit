# 文件上传与 URL 查询

服务端接收 multipart 文件，将内容上传到七牛云，确认成功后在 PostgreSQL 保存元数据。两个接口均使用现有 Bearer 认证，只允许 App Token 用户操作本人文件；管理令牌不能冒充用户。无需新增生产依赖。

## 表设计与初始化

DDL 位于 [create_file_uploads.sql](../src/service/sql/create_file_uploads.sql)，表名为 `file_uploads`。

| 字段 | PostgreSQL 类型 | 约束与含义 |
| --- | --- | --- |
| `id` | BIGINT IDENTITY | 自增主键，约束 `pk_file_uploads` |
| `user_id` | BIGINT NOT NULL | 文件所属用户，必须为正整数 |
| `url` | TEXT NOT NULL | 上传确认后的完整 HTTP/HTTPS 公开地址 |
| `bucket_name` | TEXT NOT NULL | 上传时使用的七牛 Bucket |
| `object_key` | VARCHAR(128) NOT NULL | 服务端生成的 `users/{user_id}/files/{随机值}` |
| `original_name` | VARCHAR(255) NOT NULL | 去除路径后的文件名，接口限制最多 255 字符，不允许控制字符 |
| `content_type` | VARCHAR(127) NOT NULL | 客户端声明的 MIME 类型，缺省为 `application/octet-stream` |
| `size_bytes` | BIGINT NOT NULL | 实际文件字节数，必须大于零 |
| `uploaded_at` | TIMESTAMPTZ NOT NULL | 七牛确认成功后写入元数据的时间，默认当前时间 |
| `create_by`、`update_by` | BIGINT NOT NULL | 创建人、最后更新人，首次写入均为当前用户 ID |
| `create_time`、`update_time` | TIMESTAMPTZ NOT NULL | 数据库审计时间，默认当前时间 |
| `is_delete` | SMALLINT NOT NULL DEFAULT 0 | `CHECK (is_delete IN (0, 1))`，查询只返回有效记录 |

表包含项目约定的六个必备字段，所有列均有注释。对象键是不可复用的存储标识，使用全局唯一约束 `uk_file_uploads_object_key`，逻辑删除后仍保留唯一性。单条查询按主键查找并核验用户与删除标记，无额外低选择性索引。不新增外键，沿用现有 App Token 身份模型，避免要求通过可信服务签发的用户标识必须先存在于邮箱注册表。

启用功能后，服务启动使用事务、10 秒锁等待上限和 PostgreSQL advisory lock 幂等执行 DDL。也可以由数据库管理员在目标数据库内执行：

```sh
psql -v ON_ERROR_STOP=1 --single-transaction -f src/service/sql/create_file_uploads.sql
```

连接信息应通过已有安全连接配置传入，不写入命令或文档。DDL 只新增本功能的表、约束和注释，不修改现有业务表或回填用户数据。可先建表再滚动升级应用；回退时关闭功能并保留表与七牛对象，不删除数据。后续任何更新或逻辑删除都必须同步 `update_by` 和 `update_time`。当前不提供删除接口。

## Nacos 与七牛配置

复用现有 Nacos `storage.qiniu`，新增 `storage.files`：

```yaml
storage:
  files:
    enabled: true
    max_bytes: 20971520
  qiniu:
    ak: replace-with-qiniu-access-key
    sk: replace-with-qiniu-secret-key
    bucket_name: replace-with-bucket-name
    public_base_url: https://cdn.example.com
    upload_token_ttl_seconds: 3600
```

对应环境变量为 `FILE_UPLOAD_ENABLED`（默认 `false`）、`FILE_UPLOAD_MAX_BYTES`（默认 20 MiB，范围 1 字节至 100 MiB），以及已有的 `QINIU_*`。Nacos 在启动时覆盖本地业务配置；扁平配置 `FILE_UPLOAD_ENABLED`、`FILE_UPLOAD_MAX_BYTES` 也受支持。启用时必须提供长度至少 32 字符的 `APP_TOKEN_SECRET`、完整七牛配置和 PostgreSQL 配置，否则启动失败。

使用已配置的 Nacos CLI Profile 管理目标环境，先通过 `nacos-cli --help` 核实命令。`config-get` 返回完整配置，含有秘密，操作时应直接捕获到内存或受保护的临时文件，不在终端、聊天或日志展示。修改完整配置时保留其他节点，并通过 stdin 提交：

```sh
nacos-cli --profile local config-set agent-service-toolkit.yaml DEFAULT_GROUP < /secure/path/complete-config.yaml
```

`local` 是示例 Profile 名称，应替换为本机已配置的目标 Profile。CLI 管理 Nacos 配置，实际文件由服务通过[七牛 Python SDK](https://developer.qiniu.com/kodo/sdk/python)上传。AK/SK 和上传 Token 不会返回给客户端。配置更新后需重启服务。

Bucket 服务账号需要上传和删除对象权限。URL 采用现有公开域名语义，已获得 URL 的人可直接访问文件；私有 Bucket 签名下载不在当前接口范围内。使用独立的文件域名并配置适合业务的下载策略；MIME 类型是客户端声明，不代表内容安全扫描。对象键不包含原始文件名，不覆盖已有文件。

## 上传接口

`POST /files`，`Content-Type: multipart/form-data`，字段名 `file`，成功 HTTP 状态码为 `201`。响应沿用项目统一格式，业务 `code` 为 `200`：

```sh
curl -X POST "$AGENT_URL/files" \
  -H "Authorization: Bearer $APP_TOKEN" \
  -F "file=@./example.pdf"
```

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "id": 123,
    "url": "https://cdn.example.com/users/7/files/generated-object-key",
    "original_name": "example.pdf",
    "content_type": "application/pdf",
    "size_bytes": 1024,
    "uploaded_at": "2026-10-24T08:00:00Z"
  }
}
```

上传前检查实际文件长度，拒绝空文件和超限文件；单个文件在配置上限内读入内存，通过后台线程调用同步 SDK，避免阻塞事件循环。SDK 上传凭证限定对象键、大小上限和只允许新建。FastAPI 会先解析 multipart，部署网关还应配置请求体上限，限制解析阶段的磁盘与带宽消耗。

上传未确认成功时不写元数据，并尝试清理本次随机对象。数据库明确失败时尝试补偿删除。数据库连接在提交时中断可能无法确定是否已经提交，此时保留对象并记录对象键，避免删除已入库的文件；应由运维按数据库记录与七牛对象对账。请求取消时等待正在执行的上传与元数据写入完成后再传播取消。进程崩溃或补偿失败仍可能留下孤立对象，本接口不承诺跨 PostgreSQL 与七牛的原子事务，也不提供幂等重试键。

## 查询文件 URL

`GET /files/{file_id}/url`，成功 HTTP 状态码为 `200`，返回与上传相同的元数据结构，其中 `data.url` 为文件 URL。该接口只查询 PostgreSQL，不重新上传文件或访问任意外部地址。

```sh
curl "$AGENT_URL/files/123/url" -H "Authorization: Bearer $APP_TOKEN"
```

| HTTP 状态码 | 情况 |
| --- | --- |
| `401` | 缺失、过期或无效的 Token，或用户 ID 不是有效正整数 BIGINT |
| `403` | 使用管理令牌或其他非 App 用户身份 |
| `404` | 文件不存在、属于其他用户或已逻辑删除 |
| `413` | 文件超过配置大小上限 |
| `422` | 缺少文件、空文件、无效文件名、MIME 类型或文件 ID |
| `502` | 七牛上传失败或响应未确认成功 |
| `503` | 功能未开启或数据库不可用 |

## 验证

```sh
PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/service/test_files.py tests/service/test_nacos.py -q
./scripts/smoke_test.sh postgres
```

PostgreSQL 目标包含 `tests/smoke/test_file_uploads_postgres.py`，使用独立临时 schema 验证真实主键、唯一性、大小及删除标记约束、审计字段、用户隔离与逻辑删除。该目标不调用七牛。真实七牛验证应使用人工构造的无敏感测试内容，并清理测试对象。
