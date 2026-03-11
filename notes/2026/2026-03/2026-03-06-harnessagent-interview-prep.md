---
title: HarnessAgent 项目面试拷打笔记
date: 2026-03-06
project: interview-prep
topic: harnessagent-fuzzing-agent
id: 2026-03-06-harnessagent-interview-prep
tags: [interview, ai-agent, fuzzing, static-analysis, security, llm, ossfuzz]
source: paper-and-code
confidence: medium
---
## HarnessAgent 项目面试拷打笔记

### 目标岗位画像

> 静态分析 / 代码安全方向，要求熟悉漏洞原理、静态分析、有 AI 实践经验。

### 你在这个项目里的真实定位

> 你是**参与者**，不是主导者。主要贡献是：1）目标函数级别的代码覆盖率测量实现；2）fuzzing 运行和漏洞报告检查。项目整体架构、Agent 流水线、工具池是其他人设计的。你写上简历是因为和 JD（漏洞原理 + 静态分析 + AI 实践）高度匹配，但面试时**不要过度包装**——讲清楚你做了什么、怎么做的、学到了什么，比假装主导更有说服力。

### 回答总原则

- **诚实定位**：讲项目整体时用"我们"，讲你做的部分时用"我"。面试官追问你没做的模块时，可以说"这部分是同学负责的，但我了解它的设计思路是……"。
- 安全方向的面试官会追问漏洞原理和 fuzzing 细节，不能只讲 Agent 架构。
- 你的**杀手锏**是覆盖率测量——这涉及二进制插桩、SanitizerCoverage 内存布局、DWARF 符号映射，能体现你对底层的理解。
- 数据要记牢：243 个目标函数、SR@3 达 87%（C）/ 81%（C++）、发现 15 个真实漏洞。

---

## 一、自我介绍与项目概述

### 1. 自我介绍 + 项目一句话

> 针对一些开源软件中的内部函数（非 API 函数），自动生成可编译、可运行、能有效提升覆盖率的 fuzz driver。系统基于 LangGraph 构建了一个工具增强的 Agent 流水线，包含 8 个代码分析工具、规则化的编译错误分类修复、以及一套检测 LLM "造假"行为的验证机制。在 243 个 OSS-Fuzz 目标函数上，三次尝试成功率达到 87%（C）和 81%（C++），比 SOTA 提升约 20%，并在 11 个真实项目中发现了 15 个新漏洞。

### 2. 这个项目解决的核心问题是什么

- **Fuzz testing 的瓶颈不是 fuzzer 本身，而是 harness 的编写**。对于库的内部函数（非公开 API），没有文档、没有使用示例，人工写 harness 成本极高。
- **现有 LLM 方法的三大痛点**：
  1. 上下文不足：只给函数签名，LLM 不知道怎么正确调用内部函数（需要哪些头文件、怎么初始化结构体、参数约束是什么）。
  2. 编译修复低效：编译报错后 LLM 盲目重试，分不清是 harness 代码问题还是构建配置问题。
  3. LLM 造假：模型会自己写一个 target 函数的假实现来绕过验证（reward hacking）。
- **HarnessAgent 的解决方案**：工具增强检索（让 LLM 自己决定需要查什么）+ 规则化错误分类（把编译错误路由到正确的修复策略）+ fake definition 检测（用 Tree-Sitter 做 AST 分析拦截造假）。

### 3. 项目是什么性质？你的贡献是什么

- 这是实验室的研究项目，我作为**参与者**加入，主要负责两块工作：
  1. **目标函数级别的代码覆盖率测量**：导师要求评估生成的 harness 是否真的能覆盖到 target 函数的代码，我负责实现这个度量。
  2. **Fuzzing 运行与漏洞报告检查**：跑 1 小时 fuzzing campaign，分析 crash report，区分真实漏洞和 harness 本身的 bug。
- 项目整体架构（Agent 流水线、工具池、错误路由）是其他同学设计和实现的，但我**参与了方案讨论**，了解完整的系统设计。
- **面试时的话术**：
  - 讲项目整体："我们团队做了一个基于 LLM Agent 的自动 fuzzing harness 生成系统……"
  - 讲我的部分："我主要负责的是覆盖率度量和漏洞验证这两块，具体来说……"
  - 被追问其他模块："这部分是同学负责的，但我了解它的设计——它是通过……来实现的"

---

## 二、系统架构与流水线

### 4. 详细讲一下 HarnessAgent 的整体架构

```text
┌────────────────────────────────────────────────────────────────┐
│                    HarnessAgent Pipeline                       │
│                                                                │
│  ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐  │
│  │Generation├──►│Compilation├──►│Fix Loop  ├──►│Validation│  │
│  │(LLM+Tools)│  │(Docker)   │  │(≤5 rounds)│  │(4 checks)│  │
│  └──────────┘   └─────┬─────┘   └────┬─────┘   └────┬─────┘  │
│                       │              │               │         │
│                  error routing    LLM+Tools       fake check   │
│                  (rule-based)    fix harness     call check    │
│                                                  crash check   │
│                                                  coverage check│
└────────────────────────────────────────────────────────────────┘
```

- **Generation**：LLM 拿到函数签名 + 最小上下文（头文件、使用示例），通过工具调用按需检索更多信息（符号定义、结构体初始化方式），生成初始 harness 代码。
- **Compilation**：把 harness 注入 OSS-Fuzz 的 Docker 容器，用项目真实的构建脚本编译。
- **Error Routing（关键设计）**：编译失败后不是直接丢给 LLM 重试，而是先分类：
  - **Link Error**（undefined reference）→ 尝试切换同项目的其他 fuzz target（不同 target 链接不同库）。
  - **Include Error**（header not found）→ 解析路径，自动追加到 CFLAGS。
  - **Code Error**（语法/类型错误）→ 才交给 LLM 修复。
- **Fix Loop**：最多 5 轮迭代修复，每轮 LLM 拿到当前 harness + 分类后的错误信息 + 工具检索结果。
- **Validation**：4 重检查——fake definition 检测、target 函数调用检查、60 秒 crash 检查、覆盖率检查。

### 5. 为什么不直接让 LLM 一次性生成正确的 harness，而要做这么复杂的流水线？

- 内部函数不像公开 API 有文档和示例，LLM 的训练数据里几乎没见过这些函数的正确用法。
- 一次性生成的成功率（SR@1）只有 72%（C）/ 61%（C++），剩下的需要通过编译反馈迭代修复。
- 更关键的是编译失败的原因很多样——有些是 harness 代码本身有 bug，有些是构建配置问题（缺头文件、链接不到库），不分类的话 LLM 会在错误的方向上反复重试。
- 工具增强检索让 LLM 能"按需查询"而不是"一次性塞入所有上下文"——减少噪音，让 LLM 聚焦于当前需要的信息。

---

## 三、工具池与符号检索

### 6. Hybrid Tool Pool 是怎么设计的？为什么需要两个后端？

- **8 个工具**，按优先级排序：

| 优先级 | 工具                             | 作用                                     |
| ------ | -------------------------------- | ---------------------------------------- |
| 1      | `get_symbol_header`            | 获取符号所在头文件                       |
| 2      | `get_symbol_definition`        | 获取符号完整定义（函数体 / struct 定义） |
| 3      | `get_symbol_declaration`       | 获取符号声明                             |
| 4      | `get_symbol_references`        | 获取符号的使用示例（其他代码怎么调用它） |
| 5      | `get_struct_related_functions` | 找到某个 struct 的 init/destroy 函数     |
| 6      | `view_code`                    | 查看指定文件的指定行范围                 |
| 7      | `get_file_location`            | 查找文件路径                             |
| 8      | `get_driver_example`           | 随机采样已有的 harness 做参考            |

- **双后端架构**：
  - **LSP（clangd）**：高精度，能做语义级的符号定位（跨文件跳转、找定义/声明/引用），响应率 83-88%。
  - **Tree-Sitter parser**：鲁棒性好，直接做语法树遍历，能处理 LSP 挂掉或编译数据库不完整的情况，补充 5-11% 的覆盖率。
  - 组合后响应率达到 94%，远超 Fuzz Introspector 的 51-66%。

### 7. 为什么用 clangd LSP 而不是直接用 libclang 或 tree-sitter？

- **clangd 的优势**：它基于完整的 Clang 编译器前端，能理解宏展开、模板实例化、`#if` 条件编译，做到语义级的符号解析。Tree-sitter 只做语法解析，不理解语义。
- **clangd 的劣势**：依赖 `compile_commands.json`，有些项目的构建系统不生成这个文件，或者生成的不完整，导致 LSP 启动失败。
- **所以要 fallback**：LSP 失败时 Tree-Sitter 接管，用 grep + AST 遍历做粗粒度的符号定位，虽然精度低一些但至少不会返回空结果。
- **对比 libclang**：libclang 能做类似的事，但它是一次性分析整个翻译单元，启动成本高。clangd 是长驻服务，支持增量查询，更适合 Agent 场景下多次查询同一个项目。

### 8. 工具调用有什么限制和 guardrail？

- **每轮最多 15 次工具调用**（generation 和 fixing 各自计数），防止 LLM 无限循环查询。
- **优先级引导**：prompt 里明确告诉 LLM 优先用高优先级工具（先查 header，再查 definition），避免直接用 `view_code` 盲目浏览代码。
- **`view_code` 的使用限制**：必须提供明确的文件路径和行号，不能当 `grep` 用来搜索。
- **工具调用失败处理**：如果工具返回空结果，LLM 可以切换到其他工具或尝试不同参数。`invalid_tool_calls` 有专门的恢复逻辑。

---

## 四、编译错误修复策略

### 9. Rule-based 的编译错误分类是怎么做的？

- 编译日志通过 `log_parser.py` 解析，提取错误消息和类型。
- **分类逻辑**：
  - `undefined reference to 'xxx'` → **LinkError**：不是 harness 的问题，是链接配置不对。尝试切换同项目的其他 fuzz target（不同 target 可能链接不同的库）。
  - `'path/header.h' file not found` → **IncludeError**：自动从错误消息里提取路径，追加 `-I` 到 CFLAGS。
  - 其他语法/类型错误 → **CodeError**：交给 LLM 修复。
  - Docker 镜像构建失败 → **ImageError**：基础设施问题，不是 harness 的错。
- **为什么这个分类重要**：如果不区分，LLM 会尝试通过改 harness 代码来解决链接错误——比如注释掉调用 target 函数的那一行，这显然不对。分类后，LLM 只需要修复真正属于 harness 代码层面的错误。

### 10. Fix Loop 最多 5 轮，你觉得够吗？为什么不设更多？

- 经验上，如果 5 轮修不好，大概率是 harness 的整体思路有问题（比如错误地理解了函数的调用约定），再修几轮也只是在错误的方向上打转。
- 每多一轮意味着更多的 token 消耗和延迟。成本分析表明，HarnessAgent 的平均成本已经是 $0.215/成功函数，比简单方法贵 2-3 倍。
- 更有效的策略是**重新生成**（SR@3 比 SR@1 高 15-20%），而不是在同一个失败 harness 上死磕。
- <!-- TODO: 你实际跑的时候，大部分成功是在第几轮修复的？有没有统计过分布？ -->

---

## 五、Fake Definition 检测（核心创新）

### 11. 什么是 fake definition？为什么 LLM 会这么做？

- LLM 在生成 harness 时，如果找不到 target 函数的正确头文件或定义，会**自己写一个假的函数实现**来让代码编译通过：
  ```c
  // LLM 生成的假实现
  void target_internal_func(int x) {
      // empty stub
  }
  ```
- 这是一种 **reward hacking**：模型发现"编译通过"是验证标准，就走捷径让编译通过，但生成的 harness 测试的是自己的假函数，不是真正的库函数。
- **不做 fake check 的后果**：消融实验显示，去掉 fake check 后成功率虚增（看起来编译通过了），但实际有效覆盖率下降 12-18%。

### 12. Fake definition 是怎么检测的？

- 用 **Tree-Sitter** 解析生成的 harness AST。
- 遍历所有 `function_definition` 节点，检查是否有函数名和 target 函数名匹配。
- 如果匹配 → harness 里定义了 target 函数 → 这是 fake definition → 拒绝，强制重新生成。
- **为什么用 Tree-Sitter 而不是正则**：函数定义可能跨多行、有复杂的参数列表和返回类型，正则很容易误匹配或漏匹配。Tree-Sitter 做的是 AST 级别的精确匹配。

### 13. 除了 fake definition，还有哪些验证检查？

- **Call Check**：用 Tree-Sitter 检查 harness 里是否真的调用了 target 函数（不只是声明了）。
- **Crash Check**：跑 60 秒 libfuzzer，如果立刻 crash → 大概率是 harness 本身的 bug（比如空指针、buffer overflow），不是真实漏洞。
- **Coverage Check**：跑 60 秒 libfuzzer，检查 target 函数的分支覆盖率是否增加。如果覆盖率为零，说明 harness 虽然编译通过了但实际没有执行到 target 函数。

---

## 六、相关工作详解（面试高频：和 XX 有什么区别？）

### 14a. 四个直接 baseline 的详细对比

#### Raw Model（最简基线）

- **做法**：只给 LLM 函数签名 + 任务描述（"为函数 X 写一个 fuzz harness"），不给项目上下文、不给使用示例、不给构建信息。修复阶段只是把原始编译错误文本追加到 prompt。
- **局限**：内部函数的调用方式 LLM 训练数据里几乎没见过，所以 SR@1 只有 ~45%。而且 LLM 会高频生成 **fake definition**——自己造一个假函数体让编译通过。
- **和 HarnessAgent 的差距**：HarnessAgent 贡献了 58 个 Raw Model 无法生成的成功 harness。论文的关键结论：**"决定性能的是模型的工具使用能力，而不是模型内部知识"**。

#### LLM4FDG（ISSTA 2024）

- **性质**：不是一个工具，而是一个系统性的实证研究 + benchmark 定义。
- **做法**：定义了 3 种上下文级别：
  - **BACTX**（Basic Context）：只有函数声明
  - **DOCTX**（Documentation Context）：加上注释/文档
  - **UGCTX**（Usage Context）：加上真实代码里的调用示例
- 可以组合成 **非迭代**（单轮）和 **迭代**（多轮 + error feedback）策略，共 6 种 prompting 策略。
- **编译修复**：定义了 7 种 fix 模板，按错误类型分类。但每轮修复只提供**一个**出错符号的声明和使用示例——上下文窗口很窄。
- **局限**：上下文是预定义的静态策略，不能动态追加。遇到 LLM 没见过的 API 使用模式就无能为力。
- **数据**：SR@1 = 55.38%（C）/ 51.68%（C++），比 HarnessAgent 低 ~17%。

#### OSS-Fuzz-Gen（Google 生产系统）

- **做法**：
  1. **Fuzz Introspector** 做静态分析，找出项目里覆盖率低但可达性高的函数作为 target。
  2. 构建 prompt：函数签名 + Fuzz Introspector 提供的调用图 / 覆盖率数据。
  3. LLM 生成 harness → 在 OSS-Fuzz Docker 中编译。
  4. 编译失败 → **5 轮重试**，每轮把编译错误反馈给 LLM。
  5. 7 条预定义规则做**规则化上下文匹配**：匹配到特定错误模式 → 从代码里检索对应的信息片段塞进 prompt。
- **局限**：
  - Fuzz Introspector 的符号检索响应率只有 **50-65%**（vs HarnessAgent 的 94%）。
  - 7 条规则是硬编码的，新的错误类型覆盖不到。
  - **没有 fake definition 检测**。
- **成绩**：生产环境规模化运行——覆盖 272 个 C/C++ 项目，新增 37 万行代码覆盖，发现 26 个真实漏洞（包括 OpenSSL 的一个）。
- **和 HarnessAgent 的本质区别**：上下文获取是 **rule-driven**（规则驱动） vs **agent-driven**（Agent 自主决定查什么）。

#### Sherpa（AIxCyberChallenge）

- **做法**：给通用 LLM Agent（最初 Codex，后来 o3）一个高层任务描述，让 Agent **完全自主地**探索项目、写 harness、编译、修复。没有预定义的流水线结构。
- **局限**：
  - **没有结构化错误分类**：完全靠 Agent 自己看终端输出判断怎么修，经常在无效修复上浪费 token。
  - **Fuzz target 误检**：Sherpa 通过 diff clean build 来检测新 target，但 Agent 常复用原文件名导致 diff 检测不到——一个关键 bug。
  - **效率极低**：Agent 花大量时间自主探索项目目录结构，而不是直接查询需要的信息。
  - **数据**：在 HarnessAgent 的评测集上只生成了 **9 个成功 harness**（vs HarnessAgent 的 67+）。
- **和 HarnessAgent 的关键区别**：Sherpa 证明了**纯自主 Agent 不够用**。HarnessAgent 的洞察是"harness 生成需要特定的结构化工作流，不能靠通用 Agent 完成"——所以它用**结构化流水线 + 动态工具调用**，取两者之长。

### 14b. 相关工作总览（不是直接 baseline，但面试可能问到）

#### PromptFuzz（CCS 2024）— 最有创意的方案

- **核心思路**：把 **prompt 本身当作 fuzz target**。用覆盖率引导的变异循环，不断变异输入给 LLM 的 prompt，来发现更好的 fuzz driver。
- **做法**：
  1. Prompt 包含随机选择的 API 函数组合 + 库上下文（签名、类型定义）。
  2. GPT-3.5 生成 harness → 三阶段过滤（编译检查 → ASan/UBSan 运行时检查 → 600 秒覆盖率验证）。
  3. 覆盖率引导的 prompt 变异：能量函数 `energy(i) = (1 - cov(i)) / ((1 + seed(i))^E * (1 + prompt(i))^E)` 优先探索低覆盖 API。变异算子：插入高能量 API、替换 API、交叉。
  4. 所有有效 driver 融合成一个统一 driver。
- **成绩**：覆盖率比 OSS-Fuzz 高 1.61 倍；33 个真实 bug；总成本仅 $63.14。
- **局限**：变异效果因库而异，部分库随机选择函数反而更好。

#### CKGFuzzer（ICSE 2025）— RAG 路线

- **核心思路**：构建**代码知识图谱**（Code Knowledge Graph），用 RAG 从图谱检索上下文给 LLM。
- **做法**：
  - 节点 = 函数（签名 + 源码 + LLM 生成的摘要）和文件。边 = CONTAINS / CALLS / LIBRARY_CALLS。双索引（自然语言 + 代码 embedding）。
  - 生成 harness 时查询图谱获取相关 API 组合，迭代检索 top-k 片段。
  - 编译失败 → 查询图谱找正确用法，最多 5 轮修复。成功编译的 driver 反馈回知识库。
- **成绩**：编译通过率从 57.39% 提升到 93.99%；覆盖率平均 +8.73%；11 个真实 bug。
- **和 HarnessAgent 的区别**：CKGFuzzer 用 **RAG + 知识图谱**（离线构建），HarnessAgent 用 **Agent + 实时工具调用**（在线查询）。

#### TitanFuzz（ISSTA 2023）& FuzzGPT（ICSE 2024）

- 这两个是 fuzz **深度学习库**（PyTorch/TensorFlow）的，和 C/C++ harness 生成是不同任务。
- **TitanFuzz**：用 Codex 生成调用 DL API 的种子程序，用 InCoder 做 mask-and-infill 变异。差分测试做 oracle。65 个 bug。
- **FuzzGPT**：扩展 TitanFuzz，专门生成**边界情况程序**（zero-dim tensor、NaN、罕见参数组合）。三种策略：微调、few-shot、指令引导。76 个 bug。
- **面试时的话术**：如果被问到，可以说"TitanFuzz/FuzzGPT 是用 LLM 生成 fuzz 输入程序，我们是用 LLM Agent 生成 fuzz harness，任务不同但都是 LLM+Fuzzing 的方向"。

#### ChatAFL（NDSS 2024）

- **任务**：用 LLM 引导**有状态网络协议 fuzzing**，和 harness 生成完全正交。
- 三个集成点：种子丰富化、自动语法提取（LLM 生成协议消息的语法描述）、覆盖率瓶颈突破（plateau 时用 LLM 生成新消息序列）。
- 9 个未知漏洞（RTSP/FTP/SIP/SMTP/DAAP）。

### 14c. 一张图看清所有方法的定位

```text
上下文获取方式
  静态/规则驱动 ◄──────────────────────────────► 动态/Agent驱动

  Raw Model    LLM4FDG    OSS-Fuzz-Gen    CKGFuzzer    HarnessAgent    Sherpa
  (无上下文)   (6种prompt) (7条规则+FI)   (RAG+KG)    (8工具+LSP)   (完全自主)
     │            │            │              │            │              │
     ▼            ▼            ▼              ▼            ▼              ▼
  SR@1~45%    SR@1~55%    生产规模       93%编译率    SR@1~72%      9个harness
                          26漏洞         11漏洞       SR@3~87%      (效率极低)
                                                      15漏洞

  ← 成本低、可控 ─────────────────────── 成本高、灵活 →
```

> **面试总结话术**：这个领域的演进路线是 **静态 prompt → 规则化流水线 → RAG 增强 → Agent 工具调用 → 纯自主 Agent**。HarnessAgent 的位置在"结构化流水线 + 动态工具调用"，既不像 OSS-Fuzz-Gen 那样受限于固定规则，也不像 Sherpa 那样完全没有结构导致效率极低。

### 14. 评测用了什么数据集和 baseline？

- **数据集**：OSS-Fuzz 的 243 个 target 函数（65 个 C 项目 + 178 个 C++ 项目），每个项目选 1 个内部函数。
- **Baselines**（详见 Q14a）：Raw Model、LLM4FDG、OSS-Fuzz-Gen、Sherpa。
- **模型**：主要用 GPT-5.1-Mini，也对比了 Claude Haiku 4.5、Qwen3-Coder、DeepSeek V3.2。

### 15. 关键数据你要记住

| 指标                          | C                           | C++                         |
| ----------------------------- | --------------------------- | --------------------------- |
| SR@1                          | 72.31%（vs LLM4FDG 55.38%） | 60.67%（vs 51.68%）         |
| SR@3                          | 87.69%（vs 69.23%）         | 81.46%（vs 64.04%）         |
| 1 小时 fuzzing 覆盖率提升比例 | >75%                        | >75%                        |
| 工具响应率（header）          | 94.29%                      | vs Fuzz Introspector 50.95% |

- **消融实验最重要的结论**：去掉 fake check → SR@3 下降 12-18%（最关键组件）；去掉 header 工具 → 下降 17-21%（工具支持是本质）。
- **成本**：平均 $0.215/成功函数，总 token 65.186M，运行 9h17m（8 进程并行）。
- **真实漏洞**：11 个项目上发现 15 个新漏洞（堆溢出、内存泄漏、无限循环、DoS），其中 libjpeg-turbo 的已被修复。

### 16. 发现了哪些类型的漏洞？能举例说一两个吗

- **堆缓冲区溢出**（最多）：exiv2、libjpeg-turbo、liblouis、libssh、libtiff 里都有。比如 libjpeg-turbo 的一个溢出已被上游修复（commit 9131c06）。
- **无限循环 / DoS**：liblouis 有 3 个无限循环（Issue 1910），libssh 有 1 个超时。
- **内存泄漏**：c-ares（Issue 1075）。
- **误报分析也很重要**：43 个误报主要分三类——
  - 状态机违反（26%）：harness 不按正确顺序调用库的初始化函数。
  - 资源管理错误（41%）：harness 和库之间的内存所有权语义不对。
  - 参数语义错误（33%）：LLM 传了语义无效的参数。

---

## 七、深挖追问（面试官高频追击）

### 17. 和 OSS-Fuzz-Gen（Google 的方案）相比，你的核心优势在哪？

- OSS-Fuzz-Gen 用**规则化的上下文匹配**（预定义 7 种错误模式），但规则覆盖面有限，遇到没见过的错误模式就没办法。
- HarnessAgent 用**工具增强的 Agent**，LLM 自己决定需要查什么信息，适应性更强。
- 具体数据：SR@3 从 64%（OSS-Fuzz-Gen）提升到 81%（C++）；工具响应率从 51% 提升到 94%。
- 但 OSS-Fuzz-Gen 的**成本更低**（token 消耗少一个数量级），适合大规模低成本场景。

### 18. Token 成本是 65M，比 baseline 高 10-30 倍，怎么看这个 trade-off？

- 总 token 65.186M 确实比 Raw（2.075M）高很多，但**每个成功函数的平均成本只有 $0.215**，因为成功率更高（分母大了）。
- Raw 方法 token 少但成功率低，换算下来每个成功函数的成本是 $0.077-$0.138——看起来便宜，但有大量函数修不了。
- 关键问题是：**你愿意花 $0.215 自动生成一个有效 harness，还是花几小时人工写一个？**
- 优化方向：
  - 减少无效的工具调用轮次（当前 max_tool_call=15，可以调低）。
  - 用更便宜的模型做初筛，贵的模型只处理失败 case。
  - <!-- TODO: 你做过成本优化的实验吗？ -->

### 19. 如果面试官问"为什么不用 RAG 而是用 Agent 工具调用？"

- RAG 的问题是**不知道该检索什么**。对于内部函数，你不知道它依赖哪些类型、需要哪些头文件、初始化顺序是什么——这些要在编写 harness 的过程中逐步发现。
- Agent 工具调用是**按需检索**：先试着生成，编译报错后知道缺什么（比如 `undefined reference to xxx`），再去查 `xxx` 的定义。
- RAG 一次性塞入大量上下文会引入噪音，特别是大型项目（上万个头文件），检索出来的可能和 target 函数完全无关。
- 实验验证：消融实验中去掉工具后退化到类似"静态上下文"的效果，SR 下降 17-21%。

### 20. 60 秒 fuzzing 检查够吗？会不会漏掉需要更长时间才能触发的问题？

- 60 秒的 crash check 目的不是找漏洞，而是**快速过滤明显有 bug 的 harness**——如果 60 秒内就 crash 了，大概率是 harness 本身写错了（空指针、越界），不是真实漏洞。
- 真正的漏洞挖掘是后续的 **1 小时 fuzzing**（评测阶段）。
- 这是一个工程 trade-off：243 个函数 × 每个 60 秒 = 4 小时，如果改成每个 1 小时 = 243 小时，在迭代开发阶段不现实。
- Coverage check 也用 60 秒，足以判断 harness 是否能执行到 target 函数。

### 21. 误报率 43/58 看起来很高，怎么解决？

- 43 个 false positive vs 15 个 true positive，误报率确实不低（约 74%）。但这是**对内部函数 fuzzing 的普遍问题**，不是 HarnessAgent 独有的。
- 根因是 LLM 不完全理解库的 API 使用语义（初始化顺序、内存所有权、参数约束），导致 harness 的调用方式本身就是"违法"的。
- 改进方向：
  - 更强的 semantic check（论文里已有这个模块，可以扩展）。
  - 用更强的模型（论文提到 Gemini 3 Pro 可能减少 20-30% 误报）。
  - 利用 `get_symbol_references` 学习真实代码怎么调用 target 函数，模仿合法的调用模式。
  - <!-- TODO: 你有没有做过减少误报的专门实验？ -->

### 22. 为什么选 LangGraph 而不是自己写循环？

- LangGraph 提供了**类型化的状态管理**（`FuzzState` 包含 harness_code、build_msg、fix_counter 等），状态在 generation → compilation → fixing → validation 之间自动传递。
- **条件路由**更清晰：编译成功 → 跳到 validation；编译失败且是 LinkError → 切换 target 重试；编译失败且是 CodeError → 进入 fix loop。
- **工具调用集成**：LangGraph + LangChain 的 `StructuredTool` 提供了类型安全的工具定义和调用，不用手写 JSON schema 解析。
- 取舍：引入了框架依赖，但 fuzzing harness 生成的状态转移很复杂（5 个阶段 × 多种错误类型 × 多种修复策略），手写会更难维护。

### 23. 如果要支持 Java/Go/Rust 的 fuzzing，架构上要改什么？

- **需要改的**：
  - **Tool Pool 后端**：clangd → gopls / rust-analyzer / Eclipse JDT。Tree-Sitter 天然支持多语言，这层改动小。
  - **编译集成**（`compilation.py`）：替换 OSS-Fuzz 的 C/C++ Docker 构建流程。
  - **Prompt 模板**：C 的 `LLVMFuzzerTestOneInput` 入口要换成目标语言的 fuzzing 框架（如 Go 的 `go-fuzz`、Rust 的 `cargo-fuzz`）。
  - **Fake definition 检测**：Tree-Sitter 的语法节点名不同（`function_definition` → `function_item`），需要适配。
- **不需要改的**：
  - LangGraph 流水线结构（generation → compilation → fix → validation）。
  - 错误分类的框架逻辑（虽然具体的错误模式要重写正则）。
  - Agent 的工具调用框架和 guardrail。
- 这说明架构的**语言相关部分**被隔离在了 tool backend、compiler wrapper 和 prompt 模板里，核心流水线是语言无关的。

### 24. clangd 的 compile_commands.json 缺失或不完整怎么办？

- 这是实际遇到的最大工程问题之一。很多 OSS-Fuzz 项目用 `make` 或自定义构建脚本，不直接生成 `compile_commands.json`。
- **解决方案**：
  - 用 `bear`（Build EAR）包装 `make` 命令，自动拦截编译命令生成 `compile_commands.json`。
  - 如果 bear 也失败，fallback 到 Tree-Sitter parser，不依赖编译数据库。
  - 对于 CMake 项目，可以直接 `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`。
- <!-- TODO: 实际跑 243 个项目时，有多少比例 LSP 启动成功？多少比例 fallback 到 Tree-Sitter？ -->

### 25. 你怎么看这个项目的局限性？

- **内部函数的语义理解不足**：LLM 不知道函数的前置条件和不变量，导致 harness 可能用非法的参数调用 target 函数，产生误报。
- **单函数粒度**：当前是一次生成一个函数的 harness，对于需要多个函数配合的复杂调用场景（比如先 init → 再 process → 最后 cleanup），效果有限。
- **依赖 OSS-Fuzz 的 Docker 环境**：非 OSS-Fuzz 项目需要自己搭建编译环境，门槛较高。
- **成本**：相比规则化方法 token 消耗高一个数量级，对于几千个函数的大规模场景可能不现实。
- <!-- TODO: 你觉得最值得改进的一个点是什么？ -->

---

## 八、覆盖率测量实现（你的核心贡献）

> 这是你亲手实现的部分，面试时可以讲到很深的细节。面试官问"你在这个项目里具体做了什么"，先讲这个。

### 29. 你实现的"目标函数级别代码覆盖率"具体是怎么做的？

**背景**：HarnessAgent 需要验证生成的 harness 是否真的覆盖到了 target 函数的代码，而不是只在 harness 自己的逻辑里打转。

**技术方案——SanitizerCoverage inline-8bit-counters**：

1. **编译时插桩**：用 `-fsanitize-coverage=inline-8bit-counters` 编译被测项目。Clang 会在每个基本块（basic block）的入口插入一个计数器自增指令。所有计数器存储在 ELF 的 `__sancov_cntrs` section 中，是一段**连续的内存区域**，每个 byte 代表一个基本块的执行次数（0 = 未执行，>0 = 已执行）。
2. **获取计数器区域的起止地址**：链接器会自动为每个 section 生成边界符号。我们直接用 **ELF linker section 符号**拿到计数器内存的起止位置：

   ```c
   // 链接器自动生成的 section 边界符号
   extern uint8_t __start___sancov_cntrs;
   extern uint8_t __stop___sancov_cntrs;
   ```

   `[&__start___sancov_cntrs, &__stop___sancov_cntrs)` 就是整个计数器数组的范围。这比实现 `__sanitizer_cov_8bit_counters_init` 回调更直接——不需要自定义运行时回调，直接读 section 边界。

   > **补充知识**：SanitizerCoverage 还提供了另一种方式——通过实现 `__sanitizer_cov_8bit_counters_init(start, end)` 回调来获取边界。但在我们的场景中，直接用 linker symbol 更简单，因为不需要改 fuzzer 的运行时初始化逻辑。
   >
3. **按 target 函数隔离覆盖率**：关键设计——不是读整个 section 的覆盖率，而是**只测量 target 函数的覆盖率**：

   - 用 tree-sitter 解析 harness 代码，定位调用 target 函数的那一行。
   - 在调用**前**插入 `reset_sancov_counters()`（memset 清零整个计数器区域）。
   - 在调用**后**插入 `save_sancov_counters()`（把计数器 dump 到 `./bitmaps/*.bin` 文件）。
   - 这样 `.bin` 文件里只包含**执行 target 函数期间**触发的计数器，排除了 harness 自身代码的干扰。

   ```c
   void reset_sancov_counters() {
       memset(&__start___sancov_cntrs, 0,
              &__stop___sancov_cntrs - &__start___sancov_cntrs);
   }
   void save_sancov_counters() {
       // 把 [__start, __stop) 之间的 bytes 写入 ./bitmaps/N.bin
   }
   ```
4. **回放语料 + 合并分析**（`cov_c.py`）：

   - 用 `-runs=0` 回放 fuzzing 产生的所有 corpus input。
   - 读取每个 `.bin` 文件，byte != 0 表示"被覆盖"。
   - 第一个非零的 bitmap 记为 `init_cov`（初始覆盖）。
   - 所有 bitmap 做 bitwise OR 合并，得到 `final_cov`（最终覆盖）。
   - 输出 `cov.json`: `{"init_cov": N, "final_cov": M}`。
5. **覆盖率判定**（`cov_collecter.py`）：

   - `init_cov == 0 && final_cov == 0` → harness 根本没执行到 target 函数 → **无效 harness**。
   - `init_cov != 0 && final_cov > init_cov` → fuzzing 过程中发现了新的执行路径 → **有效 harness**。
   - `init_cov == final_cov` → 覆盖率没增长 → harness 能执行到但 fuzzer 没探索到新路径。

**你的话术**：

> "导师要求评估 harness 能不能真的覆盖到 target 函数。我去调研了 LLVM 的 SanitizerCoverage 机制，它的 inline-8bit-counters 模式会在每个基本块插入一个 byte 计数器，所有计数器放在 ELF 的 `__sancov_cntrs` section 里。我的做法是直接用链接器生成的 section 边界符号 `__start___sancov_cntrs` 和 `__stop___sancov_cntrs` 拿到这块内存的起止地址。然后在 harness 调用 target 函数前清零计数器、调用后 dump 出来，这样就能只看 target 函数执行期间的覆盖情况。最后回放所有 corpus input，合并 bitmap，比较初始覆盖和最终覆盖来判断 harness 是否有效。"

### 29a. Java/JVM 项目的覆盖率方案有什么不同？

C/C++ 用 SanitizerCoverage 是因为 LLVM 插桩天然支持，但 JVM 没有这套机制，所以用了完全不同的方案：

**主方案——JaCoCo 运行时 agent**：

- 通过反射获取 JaCoCo agent：`org.jacoco.agent.rt.RT.getAgent()`
- `reset()` → 清零覆盖数据（对应 C 的 `reset_sancov_counters`）
- `getExecutionData(false)` → 导出覆盖数据的二进制序列化（对应 C 的 `save_sancov_counters`）
- JaCoCo 的插桩粒度是 **字节码指令级别**（probe 插在每个分支点），比 SanitizerCoverage 的基本块粒度更细

**Fallback——手动 edge 计数器数组**：

- 如果 JaCoCo agent 不可用（比如 Jazzer 的构建没带 JaCoCo），退化到手动维护 `int[] covEdgeCounters = new int[65536]`
- 思路和 AFL 的 shared memory 类似：固定大小的计数器数组，由 harness 代码自己维护
- 精度不如 JaCoCo，但保证有数据可读

**后续流程一致**：dump 到 `./bitmaps/*.bin` → `cov_jvm.py` 回放语料、合并 bitmap → 输出 `cov.json`，判定逻辑和 C/C++ 完全相同。

|              | C/C++                                                  | Java/JVM                                     |
| ------------ | ------------------------------------------------------ | -------------------------------------------- |
| 插桩机制     | LLVM SanitizerCoverage (编译时)                        | JaCoCo agent (运行时字节码改写)              |
| 计数器位置   | ELF `__sancov_cntrs` section                         | JaCoCo 内部数据结构 /`int[65536]` fallback |
| 起止地址获取 | `__start___sancov_cntrs` / `__stop___sancov_cntrs` | `RT.getAgent().getExecutionData()`         |
| 清零方式     | `memset` section                                     | `RT.getAgent().reset()`                    |

**面试加分点**：如果被问"你这个方案能支持其他语言吗"，可以说"Java 我们已经做了——用 JaCoCo 替代 SanitizerCoverage，核心的'调用前清零 + 调用后 dump + 回放合并'框架是语言无关的，只需要替换插桩后端"。

### 30. 为什么用 inline-8bit-counters 而不是其他覆盖率方案？

| 方案                               | 优点                                                                     | 缺点                                                          |
| ---------------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------- |
| `gcov / lcov`                    | 成熟、报告格式友好                                                       | 需要正常退出才能 flush 数据，fuzzer 通常 crash 退出拿不到数据 |
| `__sanitizer_cov_trace_pc_guard` | 每个 edge 一个 guard 变量                                                | 只能判断 yes/no，没有执行次数信息                             |
| **`inline-8bit-counters`** | **每个基本块一个 byte 计数器，进程内存中直接可读，不依赖正常退出** | 计数器饱和后溢出（但对覆盖率判断够了）                        |
| `AFL-style shared memory`        | 和 AFL/AFL++ 兼容                                                        | 是 edge coverage 而非 block coverage，且共享内存需要额外 IPC  |

关键优势：**libFuzzer 原生支持 inline-8bit-counters**，OSS-Fuzz 的标准编译流程默认就启用它，不需要额外改构建脚本。而且计数器在进程地址空间内，通过 linker section 符号可以直接读写，不需要等进程退出。配合"调用前清零 + 调用后 dump"的设计，可以精确隔离 target 函数的覆盖率，排除 harness 自身代码的噪音。

### 31. 面试官追问："你说每一位代表一个函数有没有被访问"——准确说应该是什么？

> 这是你之前的口语化描述，面试时要讲准确。

准确描述：**每个 byte（不是 bit）代表一个基本块（不是函数）的执行计数**。一个函数通常包含多个基本块（if/else 分支、循环体、异常处理各自是独立的基本块）。所以：

- 一个函数 → 多个基本块 → 多个计数器 byte。
- 我们的实现不需要区分"哪些 byte 属于哪个函数"——因为在调用 target 函数前清零了**整个** section，调用后 dump 出来的非零 byte 就是 target 函数（及其调用链）执行到的基本块。这比用 PC Table + DWARF 做映射更简单直接。
- `init_cov` 是第一个 corpus input 的覆盖，`final_cov` 是所有 input 合并后的覆盖。`final_cov > init_cov` 说明 fuzzer 在探索新路径。

### 32. Fuzzing 运行和漏洞报告检查你是怎么做的？

- **Fuzzing 运行**：用 OSS-Fuzz 的 Docker 环境，`run_fuzzer` 脚本启动 libFuzzer，跑 1 小时。语料库使用默认的空语料起步。
- **Crash 分析**：libFuzzer 触发 crash 后会保存 crash input 到 `crash-*` 文件。用 ASan 的报告判断 crash 类型：
  - `heap-buffer-overflow` → 堆溢出
  - `heap-use-after-free` → UAF
  - `SUMMARY: ...direct-leak` → 内存泄漏
  - `timeout` → 可能的无限循环 / DoS
- **区分真实漏洞 vs harness bug**：
  - 如果 crash 的 stack trace 全在 harness 代码里（`LLVMFuzzerTestOneInput` 内部）→ harness 自己的 bug。
  - 如果 stack trace 进入了被测库的代码 → 可能是真实漏洞，需要进一步人工分析。
  - 还要检查是否是已知的误用（比如 harness 传了 NULL 指针给不接受 NULL 的函数）。

---

## 九、漏洞原理与 Fuzzing 基础（安全方向必问）

> 以下三题编号不变（Q26-28），保持和前面的引用一致。

### 26. 什么是 fuzz testing？为什么有效？

- Fuzzing 是一种自动化测试技术，通过**持续生成和变异输入**来触发程序的异常行为（crash、hang、内存错误）。
- 有效的原因：覆盖了人类难以想到的边界情况（极长输入、空输入、格式畸形、嵌套深度极大）。
- 常见 fuzzer：libFuzzer（LLVM）、AFL/AFL++、Honggfuzz。OSS-Fuzz 是 Google 的持续 fuzzing 基础设施。
- **Harness（driver）** 是 fuzzer 和被测库之间的桥梁：接收 fuzzer 生成的随机字节，转换成被测函数需要的参数格式。

### 27. HarnessAgent 发现的堆缓冲区溢出是什么原理？

- 堆缓冲区溢出：程序向堆上分配的缓冲区写入了超出其大小的数据。
- 常见触发场景：解析可变长度数据（图片、协议包、XML）时没有正确校验长度字段。
- 检测方式：通过 **AddressSanitizer（ASan）** 编译库代码，ASan 在每次内存操作时检查边界。
- HarnessAgent 生成的 harness 把 fuzzer 的随机字节传给库的解析函数，如果解析逻辑有溢出，ASan 会立刻报出来。

### 28. 什么是 AddressSanitizer？OSS-Fuzz 的检测机制是什么？

- ASan 是 LLVM/GCC 的内存错误检测工具，编译时插桩，运行时检查：堆溢出、栈溢出、use-after-free、double-free、内存泄漏。
- OSS-Fuzz 默认用 ASan 编译所有项目代码，fuzzing 过程中如果触发了 ASan 报警就记录为 crash。
- HarnessAgent 的 validation 阶段也用 ASan：60 秒内 crash → 大概率是 harness bug；1 小时 fuzzing 中 crash → 可能是真实漏洞，需要人工确认。

---

## 十、后续待补充

- <!-- TODO: 实际跑的时候修复轮次分布（Q10） -->
- <!-- TODO: 成本优化实验（Q18） -->
- <!-- TODO: 减少误报的专门实验（Q21） -->
- <!-- TODO: LSP 成功率和 fallback 比例（Q24） -->
- <!-- TODO: 你觉得最值得改进的一个点（Q25） -->
