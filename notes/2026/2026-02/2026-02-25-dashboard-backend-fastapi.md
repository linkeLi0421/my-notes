---
id: 2026-02-25-dashboard-backend-fastapi-04f77078
date: 2026-02-25
title: "Dashboard Backend (FastAPI) 笔记"
project: rag-backend
topic: dashboard-backend-fastapi
tags: [backend, fastapi, dashboard, rag]
source: chat
confidence: n/a
---
# Dashboard Backend (FastAPI) 笔记

后端主要由 3 个 Python 服务 + 2 个数据存储组成，围绕索引检索观测闭环。下面按模块讲清楚，并给你读代码的入口路径。

整体架构

核心 RAG 引擎：mcp_server/，负责索引和查询
观测 API：dashboard_backend/，FastAPI 提供查询/任务/健康/分析/WS
数据管线：services/，负责数据下载、解析、分块、写入向量库
数据存储：PostgreSQL（日志与任务）+ ChromaDB（向量库）
组合启动：docker-compose.yml（服务依赖与端口）

1) MCP Server（核心 RAG 引擎）

入口：server.py，通过 MCP stdio 暴露 8 个工具（index/query/search/delete/reset/status 等）
主要职责：
文档索引与检索：rag_engine.py
读取文本、按 chunk 切分、用 sentence-transformers 做 embedding
存到 ChromaDB，并能用向量相似度检索
结构化日志：logging_utils.py
将查询日志、索引任务、系统指标写入 Postgres
数据模型：models.py
query_logs, indexing_jobs, system_metrics 三张表
配置：config.py（DATABASE_URL/CHROMA_HOST/EMBEDDING_MODEL/CHUNK_SIZE 等）
2) Dashboard Backend（观测 API + WebSocket）

入口：main.py，FastAPI app + CORS + 路由注册
DB 访问：db.py（Async SQLAlchemy）
API 路由：
查询日志：queries.py
/api/dashboard/queries 列表与过滤
/api/dashboard/queries/stats 聚合统计
任务监控：jobs.py
列表、详情、进度、取消
健康/指标/分析：health.py
/health, /metrics, /analytics
向量搜索：search.py
直接调用 ChromaDB 搜索并记录到 query_logs
WebSocket：websocket.py
/ws/dashboard 推送实时更新与心跳
模型复用：models.py 会优先导入 mcp_server.models，确保表结构一致
配置：config.py（DASHBOARD_ 前缀环境变量）
3) Services（数据管线）

总管线：pipeline.py
下载数据  解析/分块  写入 ChromaDB
同时更新 Postgres 中的 indexing_jobs
下载器：dataset_downloader.py
GitHub 克隆或 ZIP 下载，带断点续传
文档处理：document_processor.py
PDF 用 PyMuPDF 抽取文本
分块、元数据抽取、文档类型分类
数据集准备：data_prep.py
从 DOJ 镜像下载 zip、解压 PDF、再走 pipeline
工具函数：utils.py
数据流（后端核心路径）

索引：pipeline 或 MCP index_documents  解析/分块  embedding  ChromaDB
同时写入 indexing_jobs、日志等到 Postgres
查询：MCP query_documents 或 Dashboard /search  ChromaDB 向量检索
结果 + 统计写入 query_logs
观测：Dashboard API 读取 Postgres，计算趋势、热力图、响应时间分布等
如果你想更快上手阅读，我建议按这个顺序看：

server.py（工具入口与主流程）
rag_engine.py（索引与检索核心逻辑）
health.py（聚合与统计）
pipeline.py + document_processor.py（数据进入系统的路径）
需要我进一步画一张调用链/时序图，或者挑一个具体 API/工具从请求到落库的完整路径讲解吗？

也许我们按照数据流向的顺序一个文件一个文件的看？我想对整个后端看的比较仔细一点

可以。按数据流我建议分两条路线，先走数据进入系统再走查询/观测：

索引/入库流：pipeline.py  dataset_downloader.py  document_processor.py  utils.py  rag_engine.py  logging_utils.py  models.py
查询/观测流：server.py  rag_engine.py  logging_utils.py  db.py  queries.py/health.py/search.py/websocket.py
我们先从索引/入库流的入口开始看。

File 1: services/pipeline.py

角色：主编排器，完成下载  解析/分块  写入 ChromaDB，并持续更新 indexing_jobs 表
入口：Pipeline.run()/run_async()；CLI main() 支持参数（repo、chunk 大小、并发数、chroma host 等）
Phase 1 下载：通过 GitHubDatasetDownloader 获取仓库文件，统计 total_files，无文件则直接标记任务失败
Phase 2 处理：DocumentProcessor 并行/串行处理，每个文件产出 DocumentChunk，实时更新进度，支持取消
Phase 3 写入：_index_chunks() 使用 chromadb.HttpClient 或 HTTP API fallback 批量 upsert（只发 documents+metadatas，未显式传 embeddings，依赖 Chroma 侧的 embedding 行为）
任务更新：_update_job() 走原生 SQL 先 UPDATE 后 INSERT的 upsert，DB 不可用时仅 debug 记录，不阻断流程
补充：还有一个可选入口是 data_prep.py（从 DOJ ZIP 下载+解压+索引），它会复用 Pipeline 逻辑。

如果这个顺序 OK，我下一步讲 dataset_downloader.py。如果你想先切到查询流（server.py）也可以说。

查询观测流

查询/观测流（文件级顺序）

下面按查询  记录  观测的路径来讲。我先从第一份 server.py 详细讲起；如果你觉得节奏合适，我下一条继续 rag_engine.py，一直往后走。

File 1: server.py（查询入口与调度中心）

角色：MCP Server 的主入口，暴露工具（tools）与资源（resources），把查询/索引/状态等请求分发到具体实现。
初始化：
创建 Server 实例与 RAGEngine：app = Server(...)，rag = RAGEngine()
日志级别由 config.py 控制。
工具声明：TOOLS 列表定义 8 个工具（query_documents, search_similar, index_documents, check_status 等），MCP 客户端会基于这个 schema 调用。
请求入口：
@app.call_tool()  _dispatch_tool()
这里是查询流的起点：_tool_query_documents() / _tool_search_similar()
查询流程（核心路径）：
_tool_query_documents(args)
读取 query/top_k
调用 rag.query()（在 rag_engine.py）
组装返回文本 + sources
调用 log_query() 写入 Postgres（在 logging_utils.py）
返回 JSON 结果
资源（observability）：
@app.list_resources() / @app.read_resource()
stats://queries, stats://jobs, stats://system 等资源读取统计信息，内部也调用 logging_utils 与 rag 相关方法。
运行方式：
main() 使用 MCP stdio 运行，不是 HTTP 服务；通过 python -m mcp_server 启动。
如果你同意这个顺序，我下一条继续看 rag_engine.py（向量检索细节 + ChromaDB 接入），然后是 logging_utils.py（写日志/统计），再到 dashboard_backend 读取这些日志做观测。

你还是讲dashboard_backend部分的代码吧，我想知道这里的FASTapi是做什么的，为什么要用他

好的，我们就聚焦 dashboard_backend。我会先说明它在系统里的职责，再解释为什么用 FastAPI，并结合具体文件讲清楚它做什么。

dashboard_backend 在系统里做什么

它是观测层的后端：从 Postgres 读取 MCP 记录的查询日志、索引任务、系统指标，然后提供给前端 Dashboard。
主要能力：
查询日志 API（列表、过滤、统计）
索引任务监控（进度、详情、取消）
系统健康与指标（DB、向量库、活跃连接）
分析报表（趋势、热力图、响应时间分布）
WebSocket 实时推送
为什么用 FastAPI（而不是 Flask/Django）

异步友好：这里大量是 DB 查询 + HTTP 调用（例如健康检查中访问 ChromaDB），FastAPI 的 async 支持让 I/O 更高效。
内置数据校验/序列化：Pydantic 自动校验请求参数并输出响应模型，减少手写 schema。
自动 API 文档：FastAPI 自带 /docs，很适合后台系统调试。
依赖注入：Depends(get_db) 让数据库会话的生命周期管理更清晰。
WebSocket 支持：实时 Dashboard 用到 WebSocket，FastAPI 原生支持。
具体代码怎么组织（按文件）

1) 入口：main.py
   创建 FastAPI 应用，设置日志、CORS、异常处理、路由注册。
   生命周期 lifespan()：
   启动时 init_db() 初始化表（开发场景）
   关闭时 close_db() 释放连接池
   注册路由：
   queries, jobs, health, search, websocket
   它的定位：一个总线，聚合所有 API 与中间件。
2) 数据库会话：db.py
   使用 sqlalchemy.ext.asyncio 的 create_async_engine。
   get_db() 作为 FastAPI 依赖，自动给每个请求提供 AsyncSession。
   它的定位：统一管理 DB 连接与会话复用。
3) API 路由
   a) 查询日志：queries.py

/api/dashboard/queries：分页 + 搜索 + 时间范围过滤
/api/dashboard/queries/stats：统计（总数、平均/中位/P95、趋势、热门查询、响应时间分布）
/api/dashboard/queries/{id}：查询详情
b) 任务监控：jobs.py

/api/dashboard/jobs：列表 + 状态过滤
/api/dashboard/jobs/{id}：详情
/api/dashboard/jobs/{id}/progress：进度 + ETA
/api/dashboard/jobs/{id}/cancel：取消（写 DB + websocket 广播）
c) 健康/指标/分析：health.py

/api/dashboard/health：检查 DB + MCP + ChromaDB 状态
/api/dashboard/metrics：读取 system_metrics，输出 CPU/MEM/Disk + 活跃连接
/api/dashboard/analytics：趋势、热力图、文档类型分布等
d) 搜索：search.py

/api/dashboard/search：直接调用 ChromaDB 搜索并写入 query_logs
这是个用户在 Dashboard 上进行搜索的入口
e) WebSocket：websocket.py

/ws/dashboard：实时推送查询/任务/指标更新
内置 heartbeat 保活机制
4) Pydantic 模型：schemas.py
所有 API 的响应模型在这里定义
好处：返回结构固定，前端有强约束契约
5) 配置：config.py
使用 pydantic_settings.BaseSettings
环境变量统一 DASHBOARD_ 前缀（与 MCP 配置解耦）
一句话总结
FastAPI 在这里的核心价值是：异步 + 强类型 + WebSocket + 自动文档，适合构建 实时观测后台 API。

如果你想逐文件深读，我建议从 main.py  db.py  health.py  queries.py 的顺序开始。
你想继续从哪一个文件深入？

main.py开始，我是一个没接触过后端只知识的新手

好的，我们就从 main.py 开始，用新手能懂的方式讲清楚它在做什么、为什么要这样写。

这份文件的作用：整个后端 API 的总入口
它就像一个前台总台：

启动时做准备工作
注册一堆接口
接受请求、记录日志、处理错误
允许前端访问（CORS）
下面按代码顺序解释：

1) 导入依赖
   from fastapi import FastAPI, Request
   from fastapi.middleware.cors import CORSMiddleware
   from fastapi.responses import JSONResponse
   FastAPI：创建 API 服务本体
   Request：用于读取请求信息（比如日志里要打印路径）
   CORSMiddleware：让前端网页能跨域访问这个后端
   JSONResponse：出错时返回 JSON 数据
2) 配置与生命周期（启动和关闭）
   @asynccontextmanager
   async def lifespan(app: FastAPI):
   logging.basicConfig(...)
   await init_db()
   yield
   await close_db()
   启动时：
   配日志
   init_db() 初始化数据库表（只在开发阶段有用）
   关闭时：
   close_db() 释放连接池
   你可以理解为：服务开机先准备数据库连接，关机时收尾。
3) 创建 FastAPI 应用
   app = FastAPI(
   title="Epstein RAG Dashboard API",
   ...
   lifespan=lifespan,
   )
   这就是 整个后端服务实例
   lifespan 让服务启动/退出时自动执行前面的准备/收尾逻辑
4) CORS 跨域配置
   app.add_middleware(
   CORSMiddleware,
   allow_origins=settings.cors_origins,
   ...
   )
   为什么需要？
   前端是 localhost (line 3000)，后端是 localhost (line 8001)，浏览器默认不允许跨域请求。
   CORS 就是告诉浏览器：这个后端允许这些前端域名访问。
5) 请求日志中间件
   @app.middleware("http")
   async def log_requests(request: Request, call_next):
   logger.debug("%s %s", request.method, request.url.path)
   response = await call_next(request)
   return response
   每次请求进来，都打印一下请求方式和路径
   方便调试，比如你能看到 GET /api/dashboard/queries
6) 全局异常处理
   @app.exception_handler(Exception)
   async def global_exception_handler(request, exc):
   return JSONResponse(status_code=500, content={"detail": "Internal server error"})
   避免服务器崩掉
   统一返回 JSON 错误信息，前端更好处理
7) 注册 API 路由
   app.include_router(queries.router)
   app.include_router(jobs.router)
   app.include_router(health.router)
   app.include_router(search.router)
   app.include_router(websocket.router)
   这一步相当于把各个模块的功能挂到总入口上。
   每个 router 是一个功能组，比如：

查询日志
索引任务
健康检查
搜索
WebSocket 实时消息
8) 根路径 /
@app.get("/")
async def root():
    return {...}
访问 http://localhost:8001/ 可以看到服务说明和有哪些接口。

一句话总结
main.py 就是 后端服务的启动器 + 总入口：

启动时准备数据库
注册 API
处理跨域、日志、异常
把路由挂到同一个服务器上
要继续的话，我建议下一步看 queries.py（查询日志接口）或 health.py（健康与指标）。
你想从哪一个继续？

queries.py

好的，我们看 queries.py。这份文件负责 查询日志的 API，也就是前端想看用户问了什么、响应多快、热门问题有哪些等数据时会访问这里。

我会按新手能懂的方式从上到下讲。

File: queries.py

1) 这个文件的目的
   它提供三类接口：

查询列表：最近查询日志（带分页、过滤）
查询统计：总量、平均/中位/P95、趋势、热门查询
查询详情：单条查询记录
2) 关键依赖
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, text, case, literal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
APIRouter：把这一组接口归类
Depends(get_db)：自动拿到数据库会话
select + func：SQLAlchemy 的查询构建器
Query(...)：声明 URL 参数，带校验/默认值
3) 路由前缀
router = APIRouter(prefix="/api/dashboard/queries", tags=["queries"])
所以所有路径都以 /api/dashboard/queries 开头。

核心接口讲解
A. 查询列表：GET /api/dashboard/queries
async def get_recent_queries(
    limit=50, offset=0, search=None, client_type=None, time_range=None, db=Depends(get_db)
)
用途：获取查询日志列表

参数含义

limit：每页多少条（默认 50）
offset：从第几条开始
search：搜索关键词（对 query_text 做模糊匹配）
client_type：过滤来源（比如 dashboard 或 mcp）
time_range：时间范围（如 24h, 7d）
查询步骤

query = select(QueryLog)
根据 search、client_type、time_range 加过滤条件
先用 count() 拿总量
再排序 + 分页
返回 QueryListResponse
返回结构

{
  "queries": [...],
  "total": 1234,
  "page": 1,
  "page_size": 50
}
B. 查询统计：GET /api/dashboard/queries/stats
async def get_query_statistics(time_range="24h", db=Depends(get_db))
用途：统计分析数据

返回内容包括：

total_queries：总次数
avg_response_time_ms：平均响应时间
median_response_time_ms：中位数
p95_response_time_ms：P95
query_trend：按小时分桶的趋势
popular_queries：最常见查询
response_time_distribution：响应时间分布
重点逻辑

func.percentile_cont 计算中位数和 P95（这是 PostgreSQL 的统计函数）
用 date_trunc("hour", ...) 按小时统计
用 case() 统计响应时间分桶（<0.5s, 0.5-1s, ）
C. 查询详情：GET /api/dashboard/queries/{query_id}
async def get_query_detail(query_id: UUID, db=Depends(get_db))
用途：获取单条查询日志

如果找不到，会 raise HTTPException(404)
4) 时间范围解析
def _parse_time_range(time_range: str) -> str | None:
    mapping = {
        "1h": "1 hour",
        "24h": "24 hours",
        "7d": "7 days",
        ...
    }
这只是把前端传来的短格式变成 SQL 能理解的 interval。

新手可以这样理解
queries.py 就是查日志的 API
它不会自己去记录日志，而是读取 query_logs 表里的历史记录
统计接口只是对这些记录做分组和聚合
如果你想继续，我推荐下一个文件是：

health.py（系统健康与仪表盘指标）
search.py（Dashboard 里直接搜索的入口）
你想先看哪个？

所以这里的fastapi就像是，定义一下网页里不同的子页面都是做什么的，然后每个子页面那些按钮点下去是调用了什么函数？

差不多是这个意思，但需要稍微纠正一下：

FastAPI 在这里不是定义网页页面，而是定义网页会去请求的接口。

前端页面（React 里的 SystemHealth.tsx, StatCard.tsx, ResponseTimeChart.tsx）负责画界面和按钮；
FastAPI 负责提供数据和动作的入口。

可以这样对比理解：

你看到的东西
属于谁
作用
页面布局/卡片/图表
前端（React）
画出来、展示给用户
点击按钮后获取数据
前端调用
发送请求
/api/dashboard/queries
后端（FastAPI）
返回查询日志
/api/dashboard/health
后端（FastAPI）
返回系统健康状态
更贴近你的说法：
FastAPI 的路由 = 前端功能的入口
前端按钮/页面 = 触发这些入口的地方
比如：

例子 1：页面加载健康状态
前端 SystemHealth.tsx 会请求：

GET /api/dashboard/health
FastAPI 里对应：

@router.get("/health")
async def get_system_health(...)
例子 2：查询日志页面
前端可能请求：

GET /api/dashboard/queries?limit=50
FastAPI 里对应：

@router.get("")
async def get_recent_queries(...)
所以一句话总结：
FastAPI 不负责页面是什么样子，而是负责页面需要的数据从哪来、怎么拿。

如果你愿意，我们可以把某个前端组件和后端接口对上，比如从 SystemHealth.tsx 看它请求了什么 API，然后跳回 health.py 一一对应讲。

那我点了一个按钮，网页的url改变了，这是什么现象

这是**前端路由（clientside routing）**的现象。

简单理解：

URL 变了，但页面没整页刷新  多半是前端（React）自己在切换页面，不是后端在返回新页面。
URL 变了，而且整页重新加载  这才是传统后端路由。
在 React 里通常是这样触发的：

点击按钮  navigate("/queries")
或者 `<Link to="/queries">`
浏览器地址栏变了，但实际上只是前端组件切换
如果 URL 变化带 ? 参数（比如 ?page=2），也可能只是状态变化，并不一定换页面。

要不要我帮你在前端里定位是哪一行代码做了路由切换？通常在 main.tsx 或 App.tsx 里。

SystemHealth.tsx 看它请求了什么 API，然后跳回 health.py 一一对应讲。

已处理 54s
下面我按前端  API  后端函数一一对应讲清楚：

请求链路（SystemHealth.tsx  FastAPI）

SystemHealth.tsx
useEffect 里调用 fetchHealth()，并且每 30 秒轮询一次
api.ts
fetchHealth()  api.get("/health")
api 的 baseURL 是 "/api/dashboard"
所以实际请求路径是：GET /api/dashboard/health
后端对应：health.py
@router.get("/health")  get_system_health(...)
SystemHealth.tsx 真正依赖的返回字段
它主要用的是这两个：

health.components（逐条显示系统组件状态）
可选的 health.metrics.disk_usage（>80% 时显示磁盘警告）
health.py 里 /health 做了什么
health.py 的 get_system_health 函数会：

检查 PostgreSQL：SELECT 1
检查 MCP Server：看 query_logs 是否有最新记录
检查 Indexing Jobs：统计 processing 的任务数
检查 ChromaDB：请求 collection 和 count
汇总总体状态：
有 error  unhealthy
有 warning/unknown  degraded
否则 healthy
返回结构（简化）：
{
  "status": "healthy|degraded|unhealthy",
  "uptime_seconds": 123.4,
  "components": [
    {"name":"PostgreSQL","status":"connected"},
    {"name":"MCP Server","status":"running"},
    {"name":"Indexing Engine","status":"idle"},
    {"name":"Vector Database","status":"connected"}
  ]
}
注意一个小细节

SystemHealth.tsx 里会检查 health.metrics.disk_usage，但 /health 并不返回 metrics。
真正返回 metrics 的是 GET /api/dashboard/metrics（也在 health.py 里）。
所以现在磁盘报警基本不会触发，除非你把 /metrics 的结果合并进来，或者扩展 /health 返回 metrics。
