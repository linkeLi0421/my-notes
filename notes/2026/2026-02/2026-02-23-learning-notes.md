---
title: Epstein RAG 项目学习笔记
date: 2026-02-23
project: epstein_rag
topic: learning-notes
id: 2026-02-23-epstein-rag-learning-notes-4dca58fc
tags: []
source: chat
confidence: n/a
---


# Epstein RAG 项目学习笔记

## 1. async/await 核心概念

### 什么是 async/await？
- **async def**：声明一个异步函数（协程），不会立刻执行，返回协程对象
- **await**：在异步函数内部等待某个 I/O 操作完成
  - 同步等待：线程被卡住，什么都不能切换
  - async 等待：当前协程暂停，控制权还给事件循环；其他任务可以继续跑

### 为什么要用 async/await？
这个项目是 MCP 服务端，需要：
- 持续处理多个并发请求（数据库查询、向量检索、日志写入）
- 每个操作都涉及 I/O（数据库、网络、标准输入输出）
- 异步模型让同一线程在等待 I/O 时处理其他任务，提高效率

### 3 条记住的规则
1. `await` 只能写在 `async def` 里
2. 遇到 I/O（DB、网络、流）优先考虑异步接口
3. 最外层用 `asyncio.run(...)` 启动事件循环

---

## 2. MCP Server 架构（server.py）

### 文件路径
`mcp_server/server.py` - MCP 服务入口

### 关键对象

#### app = Server(config.server_name)
- 类型：`mcp.server.Server` 实例
- 作用：MCP 服务本体，管理所有工具和资源
- 不是 HTTP 应用，而是通过 stdio 通信的协议服务器

#### rag = RAGEngine()  
- 类型：`mcp_server.rag_engine.RAGEngine` 实例
- 作用：向量检索、文档索引的核心引擎
- 负责：chunk、embedding、ChromaDB 查询

### 装饰器系统（MCP SDK 预定义的注册钩子）

| 装饰器 | 注册的函数 | 作用 |
|--------|---------|------|
| `@app.list_tools()` | `list_tools()` | 返回可用工具列表 |
| `@app.call_tool()` | `call_tool(name, arguments)` | 执行指定工具 |
| `@app.list_resources()` | `list_resources()` | 返回资源列表 |
| `@app.read_resource()` | `read_resource(uri)` | 读取资源内容 |

### 暴露的 MCP 工具（给 AI Agent 用）

```
TOOLS = [
  - index_documents(folder_path)          # 索引文件夹
  - query_documents(query, top_k=5)       # RAG 查询
  - search_similar(query, top_k=5)        # 相似度搜索
  - get_document_summary(source)          # 获取文档摘要
  - list_indexed_documents()              # 列出已索引文档
  - delete_document(source)               # 删除文档
  - reset_index()                         # 重置索引
  - check_status()                        # 检查系统状态
]
```

### 暴露的 MCP 资源

```
RESOURCES = [
  - stats://queries      # 查询统计
  - stats://documents    # 文档统计
  - stats://jobs         # 任务统计
  - stats://system       # 系统健康
]
```

---

## 3. 执行流程解析

### 完整的启动顺序（main() 函数）

```python
async def main():
    # 1. 初始化数据库表
    logger.info("Initialising database tables...")
    await init_db()  # 等待数据库操作完成
    
    # 2. 启动 MCP 服务
    logger.info("Starting MCP server '%s'...", config.server_name)
    
    # 3. 进入无限循环（等待客户端消息）
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())
```

### 时间线

1. **启动阶段**（顺序执行）
   - Log 1: "Initialising database tables..."
   - 执行 `await init_db()`（DB 建表）
   - Log 2: "Starting MCP server..."

2. **运行阶段**（并发循环）
   - 进入 `async with stdio_server()` 创建 stdin/stdout 流
   - `await app.run(...)` 启动事件循环
   - 持续等待 → 处理请求 → 回包 → 继续等待...

### 关键点
- 启动阶段是"先等 init_db 完成，再继续"（串行）
- 运行阶段是"持续监听多个并发请求"（并行）
- 真正体现异步优势的是 `app.run` 的无限循环里

---

## 4. 核心概念解释

### stdio_server 是什么？
- 一个"异步上下文管理器"（async context manager）
- 作用：把终端的标准输入/输出包装成可异步读写的流
- MCP 客户端通过 stdio 发送 JSON-RPC 消息，服务端通过 stdio 回复
- 这不是 HTTP 端口服务，而是"进程间管道通信"

### asyncio.run(main()) 的作用
1. 创建事件循环
2. 把 `main()` 协程放进去运行
3. 等 `main()` 完成（通常永不完成，因为 `app.run` 无限循环）
4. 退出时关闭事件循环和所有任务

可以理解成异步程序的"总开关"，类似同步程序的 startup 函数。

### 为什么说 app.run 是一个循环？
```
app.run(...) 内部持续做：
  ↓
等待一个消息从 stdin 进来
  ↓
识别消息类型（list_tools? call_tool? read_resource?）
  ↓
路由到对应的装饰器函数（@app.call_tool() 等）
  ↓
执行完毕，把结果 JSON 写到 stdout
  ↓
回到"等待一个消息"
  ↓
(循环直到进程被杀死)
```

---

## 5. 数据流向总结

### query_documents 请求举例

```
Claude Desktop (客户端)
  ↓
通过 MCP protocol 发送 JSON-RPC 请求到 stdio
  ↓
stdio_server 接收消息
  ↓
app.run() 识别是 "call_tool" 请求，工具名 "query_documents"
  ↓
@app.call_tool() 装饰器捕获
  ↓
_dispatch_tool(name="query_documents", arguments={...})
  ↓
_tool_query_documents(args)
  ↓
  1. 计时开始 QueryTimer()
  2. await rag.query() 向 ChromaDB 检索
  3. 组装响应文本
  4. await log_query() 写入 PostgreSQL
  ↓
把结果 JSON 写回 stdout
  ↓
Claude Desktop 收到响应
```

---

## 6. 关键文件导航

| 文件 | 作用 |
|------|------|
| `mcp_server/server.py` | MCP 服务入口、工具定义 |
| `mcp_server/rag_engine.py` | 向量检索、文档索引 |
| `mcp_server/logging_utils.py` | 日志写入 PostgreSQL |
| `mcp_server/models.py` | SQLAlchemy 数据模型 |
| `mcp_server/config.py` | 环境变量配置 |
| `services/pipeline.py` | 离线批处理建库 |
| `dashboard_backend/main.py` | Dashboard API 入口 |
| `dashboard_frontend/src/App.tsx` | 前端路由 |

---

## 7. RAG Engine 深度解析（rag_engine.py）

### 文件概览
`mcp_server/rag_engine.py` - 向量存储、文档索引、语义检索核心

### 设计模式：Lazy Initialization

```python
class RAGEngine:
    def __init__(self):
        self._client = None        # 不在 __init__ 时连接
        self._collection = None
        self._model = None
    
    def _get_client(self):
        if self._client is None:        # 延迟初始化
            self._client = chromadb.HttpClient(...)
        return self._client
```

**好处**：
- 加快启动速度（不需要等 ChromaDB 连接、加载模型）
- 避免启动时因连接失败导致整个进程崩溃
- 只在真正使用时才初始化资源

### 核心概念 1：分块（Chunking）

**目的**：把大文档分成可管理的小块，同时保留上下文

```
原文本：长 100,000 字

分块逻辑：
  chunk_size = 1000
  chunk_overlap = 200
  
  第 1 块：[0, 1000]
  第 2 块：[800, 1800]    ← 重叠 200 字
  第 3 块：[1600, 2600]   ← 重叠 200 字
  ...

好处：
  - "岛上的访客"不会被割成"岛上的"和"访客"
  - 数据库不会因巨大向量而爆炸
```

结构（[mcp_server/rag_engine.py](https://github.com/linkeLi0421/Epstein_Rag/blob/main/mcp_server/rag_engine.py#L57-L88)）：
```python
_chunk_text() 返回：
[
  {
    "id": "file.pdf_ae3f1",                           # 唯一标识（hash 生成）
    "text": "The flight logs show...",                 # 文本内容
    "metadata": {
      "source": "file.pdf",
      "chunk_index": 0,
      "char_start": 0,
      "char_end": 1000
    }
  },
  ...
]
```

### 核心概念 2：Embedding（向量化）

**目的**：把文本转成数学向量，在高维空间中表示语义

```
输入：文本 "flight logs"
           ↓
SentenceTransformer (all-MiniLM-L6-v2)
           ↓
输出：384 维浮点向量
  [0.12, -0.03, 0.45, 0.33, ..., -0.15]
```

**关键点**：
- 同样含义的文本 → 相似的向量 → "距离"小
- 384 维是平衡点：细节丰富，计算成本不过高
- SentenceTransformer 是预训练模型，已学会语义映射

### 核心概念 3：ChromaDB（向量数据库）

**是什么**：存储向量 + 文本 + 元数据，支持快速相似度搜索

```
Collection: "epstein_documents"
┌──────────────────────────────────────────────────┐
│ id          | text          | metadata | embedding│
├──────────────────────────────────────────────────┤
│file.pdf_ae3f1│"The flight..."│{source..}│[0.12...]│
│file.pdf_b2d7e│"[continued]..."│{source..}│[0.15...]│
│file2.md_c5f3a│"At the island"│{source..}│[0.18...]│
└──────────────────────────────────────────────────┘
```

**HTTP 架构**：
```
MCP Server (localhost:5001)
    ↓ HTTP
ChromaDB (localhost:8000)
    ↓ 磁盘
向量索引文件 + 元数据
```

### 索引流程（建库）

[mcp_server/rag_engine.py](https://github.com/linkeLi0421/Epstein_Rag/blob/main/mcp_server/rag_engine.py#L91-L115)：

```
index_folder()
  ↓
遍历所有 .txt, .md, .pdf 文件
  ↓
对每个文件：index_file()
  ├─ 读文件内容
  ├─ _chunk_text() 分块（带 200 字重叠）
  ├─ model.encode() 生成 384 维向量
  ├─ collection.upsert(
  │    ids=[...],
  │    documents=[...],
  │    metadatas=[...],
  │    embeddings=[...]
  └─ )
```

**upsert 特点**：
- id 存在 → 更新（覆盖）
- id 不存在 → 插入
- 重新索引同一文件无需手动删除

### 查询流程（检索）

[mcp_server/rag_engine.py](https://github.com/linkeLi0421/Epstein_Rag/blob/main/mcp_server/rag_engine.py#L151-L188)：

```
query("who visited the island?", top_k=5)
  ↓
1. 把查询文本也转成 384 维向量
   model.encode(["who visited..."])
   ↓
2. ChromaDB 搜索（cosine 距离）
   collection.query(
     query_embeddings=[...],
     n_results=5,
     include=["documents", "metadatas", "distances"]
   )
   ↓
3. 内部用 HNSW 索引找最近的 5 个向量
   ↓
4. 返回：
   {
     "ids": [["file.pdf_ae3f1", "file.pdf_b2d7e", ...]],
     "documents": [["text1", "text2", ...]],
     "distances": [[0.05, 0.12, 0.18, ...]]
   }
   ↓
5. 转化距离为相似度
   similarity = 1 - distance
   [0.95, 0.88, 0.82, ...]
```

### 距离 vs 相似度

| 概念 | 说法 | 范围 | 意义 |
|------|------|------|------|
| **距离** (ChromaDB) | "cosine distance" | [0, 2] | 越小越相似 |
| **相似度** (应用层) | "similarity score" | [0, 1] | 越大越相似 |

```
distance = 0.05  →  similarity = 1 - 0.05 = 0.95 ⭐
distance = 0.50  →  similarity = 1 - 0.50 = 0.50 🤔
distance = 1.00  →  similarity = 1 - 1.00 = 0.00 ❌
```

### 文档管理

| 操作 | 代码 | 作用 |
|------|------|------|
| 列表 | `collection.get(include=["metadatas"])` | 遍历所有文档名 |
| 删除 | `collection.get(where={"source": "file.pdf"}); delete()` | 删除单个文件的所有 chunk |
| 重置 | `client.delete_collection()` | 清空整个索引库 |
| 状态 | `collection.count()` | 查看已索引 chunk 数 |

### 关键设计决策

| 问题 | 答案 | 原因 |
|------|------|------|
| 为什么分块有重叠？ | 避免语义断裂 | "岛上的访客"被分成"岛上的"+"访客"就丢意义了 |
| 384 维而不是 10 维？ | 平衡精度和效率 | 维度越高越精细，但计算成本越高 |
| 为什么用 HTTP ChromaDB？ | 支持分布式 + 持久化 | Docker 容器可独立扩展，数据不随进程丢失 |
| 为什么 cosine 距离？ | 对文本最友好 | 文本向量多数高维，cosine 适合高维空间 |

### 完整数据流（从文件到答案）

```
用户文件：/data/docs/flight_logs.pdf
  ↓
index_documents("/data/docs")
  ├─ 分块：[chunk1, chunk2, chunk3, ...]
  ├─ 向量化：每个 chunk 生成 384 维向量
  └─ ChromaDB 存储
       ↓
    query_logs 表
  ({file: "flight_logs.pdf", chunks: 245, indexed_at: "2026-02-23"})
      ↓
用户提问："who visited the island?"
  ↓
query_documents("who visited...")
  ├─ 向量化查询：[0.10, -0.02, 0.47, ...]
  ├─ ChromaDB 搜索：top-5 相似 chunk
  ├─ 返回结果：[{text: "...", similarity: 0.95, source: "flight_logs.pdf"}, ...]
  ├─ log_query() 写日志到 PostgreSQL
  └─ 返回给 Claude Desktop
```

---

## 8. Sentence-Transformers（句向量模型）

### 它在本项目里的角色

SentenceTransformer 负责把文本编码成向量（embedding），用于：
- **建库**：对每个 chunk 调用 `model.encode(texts)` → 存入 ChromaDB
- **查询**：对 query 调用 `model.encode([query_text])` → 用向量近邻搜索找到最相似 chunk

对应代码：
- 模型加载：[mcp_server/rag_engine.py](https://github.com/linkeLi0421/Epstein_Rag/blob/main/mcp_server/rag_engine.py#L42-L53)
- 建库向量化：[mcp_server/rag_engine.py](https://github.com/linkeLi0421/Epstein_Rag/blob/main/mcp_server/rag_engine.py#L104-L105)
- 查询向量化：[mcp_server/rag_engine.py](https://github.com/linkeLi0421/Epstein_Rag/blob/main/mcp_server/rag_engine.py#L155-L157)

### 结构（Structure）：模块化流水线

一个典型 Sentence-Transformers 模型可以看作：

```
Text
  ↓
Tokenizer（分词成 token ids）
  ↓
Transformer Encoder（如 MiniLM/BERT，输出每个 token 的向量）
  ↓
Pooling（把 token-level 向量聚合成 sentence embedding）
  ↓
(可选) Dense/Projection（投影/降维）
  ↓
(常见) Normalize（向量归一化，便于 cosine 相似度）
  ↓
Sentence Embedding（固定维度，如 384 维）
```

记号化描述：
- Encoder 输出：$H \in \mathbb{R}^{L \times d}$（$L$ token 数，$d$ 隐藏维度）
- Mean Pooling 常见形式：

$$
s = \frac{\sum_{i=1}^{L} m_i H_i}{\sum_{i=1}^{L} m_i}
$$

其中 $m_i$ 是 mask（padding token 不计入）。

### 为什么适合做向量检索（Bi-Encoder）

Sentence-Transformers 常用 **Bi-Encoder（双塔）**：
- 文档块离线编码一次得到向量（可缓存/入库）
- 查询时只编码 query 一次，再做近邻搜索

这比 Cross-Encoder（把 query+doc 拼在一起逐个打分）在大规模检索里更快。

### 训练（Training）：常见目标与 Loss

训练目标：让语义相近的句子向量更近、语义不相近的更远。常见数据形态：
- 成对（pair）：(a, b, label)
- 三元组（triplet）：(anchor, positive, negative)
- 排序/检索：query 对应多个 relevant / irrelevant

常见 loss（不同模型/训练阶段会混用）：

- CosineSimilarityLoss（拟合相似度标签）：
  - 目标：$\cos(u,v)$ 接近标注的相似度 $y$
- TripletLoss（三元组，拉近正样本、推远负样本）：

$$
\max(0, \text{margin} - \text{sim}(a,p) + \text{sim}(a,n))
$$

- MultipleNegativesRankingLoss（检索常用）：
  - 一个 batch 中 (a_i, p_i) 是正对，其他 p_j 自动当成负样本
  - 目标：让 a_i 最偏好自己的 p_i（softmax 归一化）

说明：你项目里用到的 `all-MiniLM-L6-v2` 是常见的轻量句向量模型配置，但“精确到训练数据集/步骤”的细节需查该模型的 model card 才能完全确定。

---

## 9. HNSW（向量近邻检索索引）

### HNSW 是什么？

HNSW = **Hierarchical Navigable Small World**，一种用于近似最近邻（ANN）的图索引结构。
- 向量库如果“暴力”算最近邻，需要对每个向量都算距离，复杂度约 $O(N)$（N 很大时很慢）
- HNSW 用图结构把搜索加速到“少量节点的距离计算”，实现高效 top-k 检索

### 直觉：多层图 + 贪心搜索

可以把它想成“多层高速路/城市道路”：
- **高层**：节点更稀疏，能快速跳远定位大致区域
- **低层**：节点更密集，在局部做精细搜索

典型查询过程：
1. 从最高层入口点开始
2. 在该层做贪心跳转：如果某个邻居更接近 query，就移动过去
3. 一层层往下，逐渐精细化
4. 在最底层维护候选集合，返回 top-k

### 为什么适合文本向量检索

文本 embedding 维度高（如 384 维），精确最近邻代价大。
HNSW 在高维空间里通常能提供很好的速度/召回折中。

### HNSW 与“距离度量”的关系

HNSW 本身是索引/搜索结构；“相似/距离怎么计算”由度量决定。
本项目在创建 collection 时指定：

- `metadata={"hnsw:space": "cosine"}`（见 [mcp_server/rag_engine.py](https://github.com/linkeLi0421/Epstein_Rag/blob/main/mcp_server/rag_engine.py#L35-L41)）

这意味着 ChromaDB 在 HNSW 里用 cosine 空间进行距离计算。

### 关键参数（理解层面）

不同实现/数据库命名略有差异，但 HNSW 常见有这些控制点：
- **M**：每个节点保留的邻居数（图更密→召回更好但内存更大）
- **ef_construction**：建图时的候选宽度（越大索引质量越好但构建更慢）
- **ef_search**：查询时的候选宽度（越大召回越好但查询更慢）

你在本仓库里没有显式设置这些参数（只设置了 `hnsw:space`），说明使用的是 ChromaDB 默认值。

### 在本项目的具体落点

当你调用：

- `collection.query(query_embeddings=[...], n_results=k, ...)`

ChromaDB 会：
1. 使用 HNSW 索引快速找到近邻候选
2. 计算 cosine 距离并排序
3. 返回 `ids/documents/metadatas/distances`

然后应用层把 `distance` 转成 `similarity = 1 - distance`（见 [mcp_server/rag_engine.py](https://github.com/linkeLi0421/Epstein_Rag/blob/main/mcp_server/rag_engine.py#L176-L183)）。

---

## 10. logging_utils.py（数据库日志与统计工具层）

### 它的定位

`mcp_server/logging_utils.py` 不是“只有查询数据库”，更准确是：
- **写数据库（logging）**：记录 MCP Server 的运行事件（查询、索引任务、指标）
- **读数据库（stats 聚合）**：提供一些汇总统计给 MCP 资源（`stats://...`）
- **非数据库**：也包含即时采样的系统状态（psutil）和计时工具

它依赖的表结构来自 [mcp_server/models.py](https://github.com/linkeLi0421/Epstein_Rag/blob/main/mcp_server/models.py)。

补充：`mcp_server/models.py` 本身就是 **PostgreSQL schema 定义**，在代码里定义了三张表：
- `query_logs`
- `indexing_jobs`
- `system_metrics`

并通过 `init_db()` 在启动时创建这些表（不存在才创建）。

### 写数据库：把运行事件落到 PostgreSQL

- `log_query(...)` → 插入一条 `query_logs`
  - 由 MCP 工具 `query_documents` / `search_similar` 调用（见 [mcp_server/server.py](https://github.com/linkeLi0421/Epstein_Rag/blob/main/mcp_server/server.py)）
  - 保存：query_text、response_text、sources(JSONB)、response_time_ms、client_type、session_id

- `create_indexing_job(...)` → 插入一条 `indexing_jobs`
  - 索引开始前创建 job，初始 `status="pending"`

- `update_indexing_job(job_id, ...)` → 更新 `indexing_jobs`
  - `status="processing"` 且 started_at 为空时写入 started_at
  - `status in ("completed","failed")` 时写入 completed_at
  - 同时更新 processed/failed/total/progress/current_file/error_message 等

- `log_system_metrics()` → 插入多条 `system_metrics`
  - 使用 psutil 采样 CPU/内存/磁盘百分比

统一模式：
```
async with async_session() as session:
    session.add(...)
    await session.commit()
```

### 读数据库：聚合统计（MCP stats:// 资源用）

- `get_query_stats()`
  - total count（总查询数）
  - avg response_time_ms（平均耗时）
  - 最近 10 条 queries（用于快速展示）

- `get_job_stats()`
  - 总 job 数
  - 按 status 计数（pending/processing/completed/failed）
  - 活跃 job 列表（pending + processing）

### 非数据库：即时系统状态与计时

- `get_system_stats()`：不读 DB，直接 psutil 采样并返回 dict（更像即时健康快照）
- `QueryTimer`：同步上下文管理器，计算耗时毫秒数

---

## 下一步学习方向

推荐按以下顺序深入学习：

1. ✅ **server.py** - MCP 服务结构（已完成）
2. ✅ **rag_engine.py** - RAG 核心逻辑 + ChromaDB（已完成）
3. ⏭️ **logging_utils.py & models.py** - 数据落库
4. **dashboard_backend/api/queries.py** - 统计接口
5. **services/pipeline.py** - 离线建库流程
