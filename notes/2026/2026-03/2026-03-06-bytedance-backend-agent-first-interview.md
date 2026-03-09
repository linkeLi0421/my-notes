---
title: Agent开发笔记-漏洞迁移
date: 2026-03-06
project: interview-prep
topic: agent-interview
id: 2026-03-06-agent-bug-migration-note
tags: [interview, ai-agent, search, memory, algorithms]
source: screenshot-summary
confidence: medium
---
## Agent开发笔记-漏洞迁移使用方式

- 这是一版整理稿，先覆盖题目脉络和面试速答，后续可以继续补充项目细节、追问展开和代码实现。
- 回答时优先讲四层：`定义`、`为什么`、`工程实现`、`适用场景 / trade-off`。
- 如果题目和你的项目有关，尽量从自己的项目设计、线上问题、指标和复盘切入，不要只背概念。

## 回答总原则

- Agent 题目不要只讲学术定义，要补上工业落地中的约束、观测、评测和兜底。
- 系统题不要只讲“是什么”，要讲到数据流、状态流、异常处理和一致性。
- 并发题不要只背 API，要讲调度、锁竞争、吞吐和适用场景。
- 搜索和微服务题容易被继续追问，回答时最好主动给出一个完整链路。

## 一、自我介绍与项目拷打

### 1. 自我介绍 + 实习拷打

> 我是 XX 大学 XX 专业的硕士，研究方向是利用 LLM Agent 自动化 C/C++ 大型项目的代码迁移和编译修复。我的核心项目是 **auto-bug-migration**：当一个 C/C++ 开源项目从 V1 升级到 V2 后，迁移过来的代码经常编译不过，我设计了一套基于 LangGraph 的 ReAct Agent 系统，能自动解析编译日志、定位出错的 patch hunk、调用静态分析 KB 和源码工具做根因分析，最后生成 override diff 并通过 OSS-Fuzz 做端到端验证。系统包含 14 个工具、15+ 条 guardrail 规则、动态 prompt 组装和多 Agent 并行修复能力。

- **追问”你做了什么”**：从零设计了整个 Agent 的状态机（LangGraph 三节点图）、14 个工具集（KB 查询 / 源码读取 / patch 操作 / OSS-Fuzz 测试）、动态 prompt 组装策略（14 个错误类型片段按需拼接）和 15+ 条 guardrail 体系，以及 `multi_agent.py` 多 Agent 编排层。
- **追问”为什么这样设计”**：编译错误类型多且修复流程差异大（比如”未声明标识符”和”struct 成员缺失”的修复策略完全不同），所以用动态 prompt 按错误类型注入专家规则片段，而不是一套通用 prompt；用 guardrail 拦截模型的高频错误模式，比如在函数体内插入 forward declaration。
- **追问”遇到什么问题”**：早期模型经常生成无效 patch——比如在函数体内写 `__revert_*` 的 prototype 声明、对非 `__revert_*` 的普通未声明函数用 `make_extra_patch_override` 加声明而不是重写函数体、忘记先 `read_artifact` 读最新 BASE slice 就直接生成 patch 导致 diff 对不上。后来通过 15+ 条 guardrail 检测 + 自动注入修复 prompt 逐一解决。
- **追问”效果如何量化”**：通过 OSS-Fuzz 端到端构建测试验证修复率，支持 `--auto-ossfuzz-loop` 迭代直到编译通过或达到最大轮次；用 `error_history` 对比修复前后的 target error 数量变化来判断是否真正修复而不是引入新错误。

### 2. Agent 项目是实习项目还是个人项目

- 这是我的**个人研究项目**（auto-bug-migration），核心目标是自动修复 C/C++ 开源项目跨版本迁移的编译错误。
- 我自己设计并实现了所有模块：
  - **LangGraph 状态图**：三节点图（`llm_node → tool_node → llm_node`），路由函数控制走工具分支还是终止。
  - **14 个工具**：覆盖静态分析 KB 查询（`search_definition`）、源码读取（`read_file_context` / `read_artifact`）、patch 操作（`make_error_patch_override` / `make_extra_patch_override` / `revise_patch_hunk`）、OSS-Fuzz 构建测试（`ossfuzz_apply_patch_and_test`）等全链路。
  - **动态 prompt 组装**（`prompting.py`）：1 个 base prompt + 13 个错误类型片段，根据当前错误类型、patch 模式、merged hunk 状态动态拼接。
  - **15+ 条 guardrail**：检测并修复模型的常见错误模式。
  - **Artifact 管理**：大输出（patch 文本、源码片段）offload 到文件，通过路径引用 + `read_artifact` 按需读取。
  - **Multi-agent 编排**（`multi_agent.py`）：按 `patch_key` 分组错误，独立 spawn Agent，最后合并 override diff 做统一 OSS-Fuzz 验证。
- **验证方式**：用 OSS-Fuzz 的真实开源项目（如 libxml2）做端到端测试，Agent 生成 override diff 后自动在 Docker 中构建，对比修复前后的编译错误数量。
- **局限**：目前只处理编译期错误（compiler + linker），还没有覆盖运行时语义错误；对于需要深度理解业务语义的修复（如 API 行为变更），模型仍然可能生成编译通过但语义不正确的 patch。

## 二、Agent 原理与工程落地

### 3. Agent 在学术上由哪些部分组成

- 学术上可以抽象成 `感知 Perception + 记忆 Memory + 规划 Planning + 行动 Action + 反馈 Reflection` 五大组件。
- 在 LLM Agent 语境下一般落成：`LLM + Prompt / Policy + Tool Use + Memory + Planner + Verifier / Evaluator + Environment`。
- **结合我的项目来映射**：
  - **感知**：`build_log.py` 解析编译日志，提取结构化错误信息（file、line、msg、kind=compiler|linker），再通过 `get_error_patch_context` 把错误行映射到 patch hunk 和 BASE slice——这是 Agent 的输入感知层。
  - **记忆**：`AgentState` 维护了 `steps`（近期步骤，受 max_steps 裁剪）、`error_history`（跨 OSS-Fuzz 轮次的历史错误记录，最多 20 条）、`function_error_history`（按 `old_signature` 聚合的所有历史错误行），兼顾短期和长期。
  - **规划**：LLM 每轮输出一个 JSON `Decision`（`type: “tool”` 或 `type: “final”`），决定调用哪个工具或输出最终结论——本质是单步规划，靠 ReAct 循环实现多步推理。
  - **行动**：14 个工具覆盖了 KB 查询、源码读取、patch 生成、OSS-Fuzz 端到端测试。
  - **反馈 / 校验**：15+ 条 guardrail 在模型输出后做校验——如果检测到无效模式（比如在函数体内写 forward declaration、对非 `__revert_*` 函数用 `make_extra_patch_override`），会注入修复 prompt 让模型重试。OSS-Fuzz 构建结果也是一种环境反馈。

### 4. Agent 如何减少幻觉，在工业场景下怎么做

- 核心思路是少让模型凭空生成，多让模型基于工具返回的**确定性证据**来决策。
- **我的项目里具体做了这几层**：
  - **结构化输出**：强制 LLM 只输出 JSON（`{“type”:”tool”,”thought”:”...”,”tool”:”...”,”args”:{...}}`），不允许自由文本，JSON 解析失败就注入 repair prompt 重试。
  - **工具链约束**：patch 生成前必须先调用 `read_artifact` 读取最新 BASE slice，不能凭记忆写 patch。Guardrail 检查工具调用顺序：分析工具（`search_definition` / `read_file_context`）→ patch 工具（`make_error_patch_override`）→ 验证工具（`ossfuzz_apply_patch_and_test`）。
  - **15+ 条 guardrail**：针对模型的高频错误模式做拦截和自动修复，例如：
    - 不要在函数体内写 `__revert_*` prototype → 强制用 `make_extra_patch_override` 在文件作用域插入。
    - 不要发明新的 `__revert_*` 函数调用 → 检测到后强制修复。
    - 不要对普通未声明函数用 `make_extra_patch_override` → 强制走函数体重写。
    - 不要发明 `#define` 宏的占位值 → 强制用 `make_extra_patch_override` 引入真实宏定义。
  - **端到端验证**：每次生成 override diff 后强制调用 `ossfuzz_apply_patch_and_test`，在 Docker 里真实编译，用编译结果而不是模型自信度来判断是否成功。
  - **Policy 硬约束**：system prompt 里写死”不允许修改 V2 的类型定义（struct / typedef / enum / union），只能让 V1 代码适配 V2 语义”，避免模型走捷径去改上游头文件。

### 5. 多 Agent / 多异步任务下，如何防止上下文污染

- **我的项目里用了严格的隔离机制**：
  - **任务隔离**：`multi_agent.py` 按 `patch_key`（文件路径 + 函数签名）把编译错误分组，每个 `patch_key` 独立 spawn 一个 Agent 实例，各自有独立的 `AgentState`、`steps`、`error_history`，不共享对话上下文。
  - **Artifact 隔离**：每个 Agent 的输出写到独立目录 `data/react_agent_artifacts/multi_<run_id>/<patch_key>/`，不会交叉覆盖。
  - **结构化共享**：Agent 之间不共享原始对话历史，只共享结构化结果——每个 Agent 产出 `patch_override_by_key[patch_key] = override_diff_path`，最后由编排层统一合并。
  - **状态机约束**：每个 Agent 内部通过 `active_patch_key` 和 `active_old_signature` 锁定当前工作范围，guardrail 会阻止跨 `patch_key` 的工具调用（patch key scope enforcement）。
  - **合并与验证**：所有 Agent 完成后，`multi_agent.py` 收集所有 `patch_override_paths`，合并到一个 unified patch bundle，再跑一次完整的 OSS-Fuzz 端到端测试，确保多个 Agent 的修复互不冲突。

### 6. 讲一下 Agent 中的长短期记忆

- **短期记忆**（当前任务上下文，随步骤流转）：
  - `AgentState.steps`：最近几轮的 `decision + observation`，受 `max_steps` 限制，超出后旧步骤被裁剪，只保留最近的上下文。
  - `AgentState.grouped_errors`：当前 `patch_key` 下的结构化错误列表（file、line、msg、old_signature、kind）。
  - `AgentState.active_*` 字段：当前正在处理的文件路径、行号、函数签名、patch 类型等，就像”工作指针”。
  - **Artifact 引用**：大输出（patch 文本、源码片段、构建日志）不塞进上下文，而是 offload 到文件，通过 `artifact_path` 引用，Agent 用 `read_artifact` 按需读取指定行范围，节省 token。
- **长期记忆**（跨轮次 / 跨 Agent 可复用，不受步骤裁剪影响）：
  - `AgentState.error_history`：记录每个 `patch_key` 的历史错误（最多 20 条），跨 OSS-Fuzz auto-loop 轮次保留，用于判断修复是否产生了新回归。
  - `AgentState.function_error_history`：按 `old_signature` 聚合所有历史错误行，跨函数组轮次保留，帮助模型理解”这个函数之前报过哪些错”。
  - `AgentState.step_history`：全量步骤历史（区别于被裁剪的 `steps`），用于审计和回放。
  - **静态分析 KB**（`KbIndex`）：V1/V2 的 libclang JSON 索引，按 USR 和 spelling 建立查找表，是持久化的确定性知识库，跨所有 Agent 共享。
- **设计取舍**：短期记忆放在 LangGraph 的 state 里随步骤流转；长期记忆一部分放在 state 的不可裁剪字段里（`error_history`、`function_error_history`），一部分（KB、artifact 文件）放在磁盘上按需加载。这样既保证上下文窗口不被撑爆，又不丢失关键历史信息。

### 7. 了解过 Agent 的设计范式吗

- 常见范式：`ReAct`、`Plan-and-Execute`、`Reflexion`、`Toolformer`、`Multi-Agent`、`Workflow / State Machine`。
- **我的项目选择了 ReAct + State Machine + Multi-Agent 的组合**：
  - **ReAct**（核心循环）：LLM 每轮输出 `Thought → Action（tool call）→ Observation`，直到输出 `type: “final”` 或达到 `max_steps`。选择 ReAct 是因为编译错误的修复是探索性过程——需要先查 KB、读源码、理解 patch 结构，然后才能决定修复策略，无法预先确定步骤。
  - **State Machine**（LangGraph 状态图）：三个节点 `llm_node → tool_node → llm_node`，路由函数决定走 tool 分支还是 END。状态图的好处是状态转移可控、可序列化、可回放，recursion limit 动态计算为 `max(25, max_steps * 8 + 25)` 来适应不同复杂度。
  - **Multi-Agent**（编排层）：`multi_agent.py` 按 `patch_key` 分组错误，每组 spawn 独立 Agent，最后合并 override diff 做统一 OSS-Fuzz 验证。比单 Agent 串行处理更高效，也更容易隔离失败——一个 hunk 修复失败不影响其他 hunk。
  - **为什么不用 Plan-and-Execute**：编译修复的步骤高度依赖中间结果（比如必须先看到 `get_error_patch_context` 返回的 BASE slice 才能决定是重写函数体还是插入 forward declaration），预先拆好完整计划反而不实际。ReAct 的逐步决策更适合这种”看一步走一步”的探索性场景。
  - **动态 prompt 组装**也是一个重要设计：不同于固定 system prompt，我的 `prompting.py` 根据当前错误类型（undeclared symbol / struct member / incomplete type / linker error 等）从 14 个 prompt 片段中选择性拼接，相当于给 ReAct 循环注入了错误类型专家规则，让模型在不同错误场景下有不同的决策指导。

## 三、项目深挖追问（面试官高频追击）

### 8. 修复成功率是多少？跑了多少个项目？

> 这是面试官判断你"是否真跑过"的核心问题，必须有数据。

- 测试项目：<!-- TODO: 填入你实际跑过的项目列表，如 libxml2, libpng, ... -->
- 测试规模：<!-- TODO: 总共跑了多少个 patch bundle / 多少个 patch_key -->
- 修复成功率：<!-- TODO: 比如 "在 libxml2-e11519 上，XX 个 patch_key 中 XX 个一次修复通过，XX 个经过 auto-loop 2-3 轮后修复，XX 个最终失败" -->
- 平均轮次：<!-- TODO: 一个 patch_key 平均跑几轮 ReAct 步骤？auto-loop 平均迭代几次？ -->
- Token 成本：<!-- TODO: 单次 patch_key 修复大约消耗多少 token？一个完整 multi-agent run 大约多少？ -->

### 9. 和不用 Agent、直接让 LLM 一次性生成 patch 相比，提升多少？

> baseline 对比是论文 / 面试必问。

- **Baseline 1（one-shot）**：把编译错误和源码直接丢给 LLM，让它一次性输出 patch。
  - 问题：<!-- TODO: one-shot 的修复率大概是多少？主要失败在哪？（猜测：缺乏 V1/V2 diff 信息、不知道 patch 语义、无法验证） -->
- **Baseline 2（无 guardrail 的 ReAct）**：有工具但没有 guardrail 和动态 prompt。
  - 问题：<!-- TODO: 去掉 guardrail 后修复率下降多少？最常见的失败模式是什么？ -->
- **我的系统的提升**：
  - 动态 prompt 让模型在不同错误类型下做出更精准的决策，而不是用一套通用 prompt 处理所有情况。
  - Guardrail 拦截了模型的高频错误（如函数体内写 forward declaration），减少了无效 patch 生成。
  - OSS-Fuzz 端到端验证 + auto-loop 允许模型在失败后根据新的编译日志迭代修复，而不是一锤子买卖。

### 10. 什么类型的错误你的 Agent 修不了？为什么？

- **语义级 API 变更**：V2 改了某个函数的行为（比如参数含义变了），但函数签名没变，编译能过但运行时语义不对。Agent 只能看到编译错误，无法感知运行时语义差异。
- **大范围重构**：如果 V2 把整个模块拆分或合并了，涉及几十个文件的联动修改，单个 patch_key 级别的 Agent 视野不够。
- **复杂的宏展开错误**：C 预处理器的宏嵌套展开导致的错误，libclang JSON 可能没有完整捕获宏展开链路，KB 查不到。
- <!-- TODO: 补充你实际遇到过的具体失败 case，比如"在 libxml2 的某个函数上，因为 XX 原因修复失败" -->

### 11. Guardrail 误拦截过吗？怎么处理的？

- <!-- TODO: 有没有遇到 guardrail 误判的情况？比如某个 case 里模型的做法其实是对的，但被 guardrail 拦截了？ -->
- 处理方式：guardrail 的设计原则是**宁可误拦也不放过**，因为模型重试的成本远低于生成一个无效 patch 再走 OSS-Fuzz 验证的成本。
- 如果发现某条 guardrail 误拦率太高，可以通过环境变量（如 `REACT_AGENT_ENABLE_UNDECLARED_SYMBOL_GUARDRAIL`）关闭单条 guardrail 做 A/B 测试。

### 12. 模型在 auto-loop 里死循环（反复生成相同错误 patch）怎么办？

- **检测机制**：`error_history` 会记录每轮 OSS-Fuzz 后的 target error 列表。如果连续两轮的 target error 完全相同（数量和内容），说明 Agent 没有取得进展。
- **退出条件**：`--max-agent-retries`（默认 6）限制最大重试次数；`ossfuzz_runs_attempted` 计数器防止无限 loop。
- **根因**：死循环通常是因为模型对某种错误类型缺乏理解（比如不知道 `git apply --reverse` 的 diff 方向），或者 KB 里缺少关键符号的定义。
- <!-- TODO: 你实际遇到过死循环吗？最后是怎么解决的？ -->

### 13. 为什么用 LangGraph 而不是自己写循环？

- **状态管理**：LangGraph 的 `StateGraph` 提供了类型化的状态定义（`TypedDict`），状态在节点间自动传递，不需要手动管理全局变量。
- **可序列化**：状态图可以序列化和恢复，方便调试和回放（虽然我主要用 `step_history` 做审计）。
- **路由清晰**：`add_conditional_edges` 把路由逻辑和节点逻辑分开，比 `if/else` 嵌套更可读。
- **递归限制**：LangGraph 内置 recursion limit，防止无限循环。
- **取舍**：LangGraph 引入了框架依赖，但编译修复的状态转移比较复杂（100+ 个 state 字段），手写循环会更难维护。我也提供了 `langgraph_shim.py` 作为 fallback，在不安装 LangGraph 时用纯 Python 模拟基本的图执行。

### 14. `make_error_patch_override` 里 `new_func_code` 是怎么生成的？

- `new_func_code` 是由 LLM 直接生成的——模型读完 BASE slice（通过 `read_artifact`）和 V1/V2 的符号定义（通过 `search_definition`）后，在 `thought` 中分析修复策略，然后在 `args.new_func_code` 里写出修改后的完整函数体。
- **关键约束**：
  - `new_func_code` 必须基于最新 BASE slice 派生，不能凭记忆写——guardrail 检查 `read_artifact` 是否在 `make_error_patch_override` 之前调用过。
  - 在 merged/tail hunk 中，`new_func_code` 只能重写 active function 的 mapped slice，不能粘贴整个 hunk。
  - 不能包含 unified-diff headers（`diff --git`、`@@`、`---/+++`）。
- 工具内部会把 `new_func_code` 嵌入原 patch hunk 的对应位置，生成一个完整的 applyable override diff。

### 15. Effective patch bundle 是怎么维护 hunk 行号映射的？

- 每次 `make_error_patch_override` 生成 override 后，工具会更新 patch bundle 里对应 hunk 的 `patch_text` 和行号范围，写成一个 `*.effective.patch2` 文件。
- 后续工具调用（如第二次 `get_error_patch_context`）使用 effective bundle 而不是原始 bundle，这样错误行号映射始终是准确的。
- 这解决了"override 改变了函数长度后，后续错误的行号会偏移"的问题。

### 16. KbIndex 用 libclang JSON，为什么不直接用 tree-sitter 或 LSP？

- **libclang 的优势**：它是 Clang 编译器的 API，能准确解析 C/C++ 的全部语法（包括宏展开、模板实例化、`#if` 条件编译），tree-sitter 只做 AST 解析，不做语义分析。
- **离线生成**：libclang JSON 可以在构建环境里一次性生成，Agent 运行时只需要读 JSON 文件，不需要完整的编译环境。
- **USR（Unified Symbol Resolution）**：libclang 给每个符号一个唯一标识符，可以跨文件精确匹配同一个函数/类型的 V1 和 V2 版本。tree-sitter 没有这个能力。
- **取舍**：libclang JSON 不够轻量（生成和解析都比 tree-sitter 慢），而且需要 V1/V2 各跑一次完整分析。如果场景只需要简单的 AST 查询，tree-sitter 更合适。

### 17. 用的什么模型？为什么？试过不同模型的效果差异吗？

- <!-- TODO: 填入你实际使用的模型，比如 GPT-4o / Claude 3.5 Sonnet / DeepSeek-V3 等 -->
- **选择理由**：<!-- TODO: 为什么选这个模型？代码生成能力？价格？上下文窗口长度？ -->
- **模型对比**：<!-- TODO: 有没有 A/B 测试过不同模型？比如 GPT-4o vs Claude 在修复率、轮次、幻觉率上的差异？ -->
- 代码里通过 `models.py` 的 `ChatModel` 抽象支持 OpenAI API compatible 接口，切换模型只需要改 endpoint 和 model name，方便做对比实验。

### 18. 14 个 prompt 片段加起来多少 token？会不会挤占工具输出的上下文空间？

- 实际运行中，一次 LLM 调用的 system prompt 是动态拼接的子集，不是全部 14 个片段一起注入。典型的 patch-scope + undeclared_symbol 场景大约拼接 4-5 个片段。
- <!-- TODO: 估算一下典型场景的 system prompt token 数，比如"base + tools + patch_scope + undeclared_symbol ≈ XXX tokens" -->
- Artifact offload 机制是另一层保护：大的工具输出（patch 文本、源码）不进上下文，通过文件引用 + `read_artifact` 按需读取指定行范围（`start_line` + `max_lines`），只把需要的部分拉进来。

### 19-追问. 多 Agent 之间的 override 有冲突怎么办？两个 hunk 改了同一个文件的相邻行？

- **当前设计**：每个 Agent 只修改自己 `patch_key` 对应的 hunk，不同 hunk 在 patch bundle 里通过 `patch_key` 唯一标识，不会直接冲突。
- **间接冲突**：两个 Agent 可能都通过 `make_extra_patch_override` 往同一个文件的 `_extra_*` hunk 追加声明。这种情况下 `multi_agent.py` 在合并 override 时按 patch_key 顺序拼接，最终由 `ossfuzz_apply_patch_and_test` 做全量验证——如果合并后编译失败，auto-loop 会重新解析错误继续修复。
- <!-- TODO: 你实际遇到过这种冲突吗？ -->

### 20-追问. 如果要支持 Rust/Java 的编译错误，架构上需要改什么？

- **需要改的**：
  - **KbIndex**：替换 libclang JSON 为对应语言的 AST/符号分析工具（Rust: rust-analyzer / syn，Java: JavaParser / Eclipse JDT）。
  - **build_log.py**：重写编译错误的解析正则（Rust 的错误格式和 GCC/Clang 完全不同）。
  - **prompt 片段**：错误类型分类和修复策略要重写（Rust 没有 forward declaration 的概念，Java 没有宏）。
  - **OSS-Fuzz 测试**：需要对应语言的构建环境。
- **不需要改的**：
  - LangGraph 状态图结构、ReAct 循环、guardrail 框架、artifact 管理、multi-agent 编排——这些都是语言无关的。
  - 这说明架构的分层是合理的：语言相关的知识被隔离在 KB、error parser 和 prompt 片段中，Agent 核心循环是通用的。

## 四、记忆、上下文与 ReAct

### 19. 如果要进行记忆压缩，通常有哪些方法

- 摘要压缩：把长对话压成高密度摘要。
- 结构化提取：只保留事实、约束、决策和未完成事项。
- 分层记忆：近期原始消息保留，远期消息只保留摘要或索引。
- 检索式记忆：把历史内容向量化或建立倒排索引，按需召回而不是全量塞进上下文。
- 重要性打分和 TTL：高价值信息长期保留，低价值信息定期淘汰。

### 20. 什么样的信息应该放在长期记忆，什么样的信息放在短期记忆

- 长期记忆适合放稳定、跨任务可复用的信息，比如用户偏好、身份资料、配置习惯、业务规则、历史结论。
- 短期记忆适合放当前任务专属的信息，比如本轮目标、最近几轮对话、中间 observation、临时变量和当前计划。
- 判断标准通常是两个：**是否跨任务复用，是否相对稳定不容易过期。**
- 如果把临时信息写进长期记忆，后面很容易造成误召回和上下文污染。

### 21. 当对话轮数很多、上下文窗口不足时，有哪些处理策略

- 直接截断：只保留系统提示词和最近几轮消息，简单但信息损失大。
- 历史摘要：把旧对话压缩成摘要，只保留关键事实、决策和未解决问题。
- 分层记忆：最近消息保留原文，历史内容保留摘要或向量索引。
- 检索回填：需要时再从长期记忆中检索相关内容，而不是把全部历史带入。
- 状态外置：把任务进度、工具结果、关键事实单独放到结构化状态里，减少对原始对话的依赖。

### 22. 你设计的 Agent 是怎么实现 ReAct 模式的？详细讲讲

#### 整体架构

```text
┌─────────────────────────────────────────────────────────────┐
│  multi_agent.py（编排层）                                    │
│  按 patch_key 分组错误 → spawn 独立 Agent → 合并 override    │
└────────────────────────┬────────────────────────────────────┘
                         │ 每个 patch_key
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  LangGraph StateGraph（三节点图）                             │
│                                                             │
│  ┌──────────┐   tool   ┌───────────┐   always  ┌────────┐ │
│  │ llm_node ├─────────►│ tool_node ├──────────►│llm_node│ │
│  └────┬─────┘          └───────────┘           └────┬───┘ │
│       │ final                                       │      │
│       ▼                                             │      │
│     [END]                                    (循环) │      │
│                                                     │      │
│  路由: llm_node 输出 Decision                        │      │
│    type=="tool"  → tool_node                        │      │
│    type=="final" → END                              │      │
│    达到 max_steps → 强制 END                         │      │
└─────────────────────────────────────────────────────────────┘
```

#### ReAct 循环实现

1. **Thought → Action**：LLM 每轮**必须**输出一个 JSON `Decision`：

   ```json
   {"type":"tool", "thought":"需要查看 V2 的 struct 定义...", "tool":"search_definition", "args":{"symbol_name":"struct xmlParserCtxt","version":"v2"}}
   ```

   或终止：

   ```json
   {"type":"final", "thought":"所有 target error 已修复", "summary":"...", "next_step":"..."}
   ```
2. **Action → Observation**：`tool_node` 调用 `ToolRunner.call()`，执行对应工具，返回 `ToolObservation(ok, tool, args, output, error)`。大输出自动 offload 到 artifact 文件，返回 `{artifact_path, sha256, bytes, lines}` 引用。
3. **Observation → Thought**：observation 追加到 `AgentState.steps`，连同历史步骤、grouped_errors、active context 一起喂给 LLM 进入下一轮。
4. **终止条件**：`type=="final"` / 达到 `max_steps` / 连续重试超限。

#### 动态 Prompt 组装（关键设计）

- 不是一套固定 system prompt，而是 `prompting.py` 根据当前错误上下文**动态拼接**：
  - `system_base.txt`（必选）：输出格式、policy 约束
  - `system_tools.txt`（必选）：注入当前可用工具的 schema
  - `system_patch_scope.txt`（if patch 模式）：解释 `git apply --reverse` 语义
  - `system_mapped_slice_rewrite.txt`（if patch + active_old_signature）：rewrite vs. extra_patch 决策树
  - **按错误类型选择**：`system_undeclared_symbol.txt` / `system_struct_members.txt` / `system_incomplete_type.txt` / `system_linker_error.txt` / `system_func_sig_change.txt` / `system_conflicting_types.txt` / `system_macro.txt` / `system_visibility.txt` / `system_missing_prototypes.txt`
  - `system_merged_tail.txt`（if merged hunk）：只修改 active function 的切片
- 这样不同错误类型拿到不同的专家指导，避免上下文被无关规则占满。

#### 14 个工具（按调用顺序分层）

| 层次   | 工具                                                       | 作用                                                        |
| ------ | ---------------------------------------------------------- | ----------------------------------------------------------- |
| 分析层 | `parse_build_errors`                                     | 解析编译日志为结构化错误                                    |
| 分析层 | `get_error_patch_context`                                | 把 error line 映射到 patch hunk，返回 BASE slice            |
| 分析层 | `search_definition`                                      | 在 KB 中查询 V1/V2 的函数/struct/typedef 定义               |
| 分析层 | `read_file_context`                                      | 从源码 checkout 读取指定行范围                              |
| 分析层 | `read_artifact`                                          | 读取 artifact 文件的指定行范围（bounded read）              |
| 分析层 | `list_patch_bundle` / `get_patch` / `search_patches` | 浏览和搜索 patch bundle                                     |
| 生成层 | `make_error_patch_override`                              | 重写 patch hunk 的 mapped slice（核心修复工具）             |
| 生成层 | `make_extra_patch_override`                              | 扩展 `_extra_*` hunk（加 forward decl / typedef / macro） |
| 生成层 | `revise_patch_hunk`                                      | 外科手术式编辑非 `__revert_*` hunk                        |
| 生成层 | `make_link_error_patch_override`                         | 处理 linker 的 undefined reference                          |
| 验证层 | `ossfuzz_apply_patch_and_test`                           | 合并所有 override → Docker 构建 → 解析新日志              |

#### 15+ 条 Guardrail（模型输出校验）

每次 LLM 输出 Decision 后、执行工具前，guardrail 函数链检查：

- **工具顺序约束**：patch 生成前必须先 `read_artifact` 读 BASE slice
- **No local `__revert_*` prototypes**：检测函数体内 forward declaration → 强制用 `make_extra_patch_override`
- **No new `__revert_*` symbol invention**：模型不能凭空创造新的 `__revert_*` 调用
- **Block `make_extra_patch_override` for non-`__revert_*`**：普通未声明函数必须走函数体重写
- **Macro define guardrail**：不允许发明 `#define` 占位值
- **Struct member guardrail**：`no member named` 错误必须先查 V1+V2 struct 定义
- **Step budget enforcement**：剩余步数不足时阻止非关键工具调用
- **Patch key scope enforcement**：阻止跨 `patch_key` 的工具调用
- 检测到违规后，注入修复提示让 LLM 在下一轮修正。

#### 状态管理与记忆分层

```text
AgentState
├─ 短期（随 max_steps 裁剪）
│  ├─ steps: 最近 N 轮 decision + observation
│  ├─ grouped_errors: 当前 patch_key 的错误列表
│  └─ active_*: 当前工作指针（file, line, signature, patch_type）
├─ 长期（不裁剪，跨轮次保留）
│  ├─ error_history: 每个 patch_key 的历史错误（max 20）
│  ├─ function_error_history: 按 old_signature 聚合的错误
│  └─ step_history: 全量步骤记录（审计用）
└─ 外部持久化
   ├─ KbIndex: libclang JSON → 按 USR/spelling 索引
   └─ Artifacts: patch 文本 / 源码片段 / 构建日志 → 磁盘文件
```

#### Multi-Agent 编排

- `multi_agent.py` 解析 patch bundle，按 `patch_key`（文件 + 函数签名）分组错误。
- 每个 `patch_key` spawn 独立 Agent（独立 state、独立 artifact 目录）。
- 每个 Agent 产出 `patch_override_by_key[patch_key] = override_diff_path`。
- 合并阶段收集所有 override，统一跑一次 OSS-Fuzz 全量验证。
- 支持 `--auto-ossfuzz-loop`：如果全量验证仍有错误，重新解析日志，继续迭代。

#### 与通用 ReAct 的关键区别

| 维度     | 通用 ReAct         | 我的实现                                                |
| -------- | ------------------ | ------------------------------------------------------- |
| Prompt   | 固定 system prompt | 14 个片段按错误类型动态拼接                             |
| 校验     | 无或简单格式检查   | 15+ 条 guardrail + 自动修复 prompt                      |
| 验证     | 依赖模型自判断     | OSS-Fuzz Docker 真实编译验证                            |
| 记忆     | 全量对话历史       | 分层（steps 裁剪 + error_history 保留 + artifact 外置） |
| 多 Agent | 通常单 Agent       | 按 patch_key 隔离 spawn + 结果合并                      |
| 输出     | 自由文本           | 强制 JSON schema，解析失败自动 repair                   |

## 五、算法题与反问

### 23. 手撕：给定一个数 n 和一组数字 a，求 a 中元素组成的小于 n 的最大值

- 这题的核心思路是“数位贪心 + 回溯”，也可以理解成受限数位构造。
- 从高位到低位尝试填数，当前位优先选择不超过 `n[i]` 的最大数字。
- 如果某一位选到了严格更小的数字，后面的位全部填集合中的最大数字即可。
- 如果当前位没有可选数字，就回退到前一位，把前一位降一档，再把后面补成最大数字。
- 如果所有同长度方案都不行，就返回长度减一且每位都是集合最大数字的结果。

```text
以 n = 23121, a = {2, 4, 9} 为例：
第 1 位先放 2，和 n 相等，继续看下一位。
第 2 位 n 是 3，可选里 <= 3 的只有 2，于是放 2。
这时已经严格小于 n，后面全部放最大值 9。
最终得到 22999。
```
