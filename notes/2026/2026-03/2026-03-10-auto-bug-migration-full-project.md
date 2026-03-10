---
title: auto-bug-migration 全项目面试拷打笔记
date: 2026-03-10
project: interview-prep
topic: auto-bug-migration-full
id: 2026-03-10-auto-bug-migration-full-project
tags: [interview, ai-agent, compiler, oss-fuzz, patch-migration, langgraph, c-cpp]
source: codebase-analysis
confidence: high
---
## auto-bug-migration 全项目面试拷打笔记

> 之前的笔记（2026-03-06）只分析了 react_agent 部分。这份笔记覆盖**完整项目**：数据采集 → patch 生成 → Agent 修复 → 合并验证的端到端流水线。

### 回答总原则

- 这个项目的核心卖点不只是"LLM Agent 修 bug"，而是一套**完整的自动化漏洞迁移流水线**——最终目标是构建一个**包含尽可能多历史 bug 的单一版本**，作为 fuzzing benchmark。
- 关键约束：每个 bug 的迁移必须**最小化对源码的修改**，这样不同 bug 的 patch 才能兼容共存在同一个版本上。
- 面试时先讲目标（"构建多 bug 共存的 fuzzing benchmark"），再讲手段（"Agent 自动修复迁移编译错误"），最后讲工程（"patch 合并 + 兼容性分析"）。
- 数据流要能画出来：`OSV API → bug trace → diff 找被改函数 → 注入旧函数 → Agent 修编译错误 → target fuzz 出新 PoC → 最小化 → 合并 → multi-bug benchmark`。

---

## 一、项目全局

### 1. 一句话介绍这个项目

> auto-bug-migration 是一个端到端的自动化系统，**最终目标是为 C/C++ 开源项目构建一个高质量的 fuzzing benchmark——在同一个（新）版本上聚合尽可能多的已知历史漏洞**。核心做法：拿到 bug 版本的执行 trace，找出哪些函数在新版本中被修改了，把**旧版本的函数注入到新版本**中。这必然导致编译错误（版本间 API 变化、结构体重构、头文件变更），系统用 LLM Agent 自动修复这些错误。编译通过后做 **target fuzz 生成新的 PoC**（因为新版本的输入格式可能变了），验证 bug 可触发后**最小化 patch**（去掉不必要的修改），最后尝试**合并多个 bug 的 patch** 到同一版本。

### 2. 为什么需要这个项目？解决什么问题？

- **Fuzzing benchmark 的痛点**：现有的 fuzzing 评测通常只能在某个 commit 上测试当时存在的 bug，但不同 bug 分布在不同的 commit 范围内。如果想评估 fuzzer 在一个版本上能发现多少已知漏洞（作为 ground truth），需要把这些 bug 聚合到同一个版本——但手动操作极其困难。
- **为什么不能直接 cherry-pick**：bug 的修复代码依赖所在 commit 的 API，跨版本迁移时函数签名变了、结构体成员改了、头文件移了，直接 `git revert` / `cherry-pick` 大概率编译失败。
- **手动迁移成本极高**：一个 C/C++ 大项目（如 libxml2、opensc）的函数签名、结构体定义、宏可能跨版本变了几十处，手动修复一个 bug 的 revert patch 需要数小时，N 个 bug 就是 N 倍。
- **最小化修改的必要性**：如果迁移一个 bug 时对源码改动太大，就会和另一个 bug 的 patch 冲突，导致无法共存。所以系统需要找到**最小的编译修复方案**——只改必须改的，不动其他代码。

### 3. 整体架构是什么？

```text
┌────────────────────────────────────────────────────────────────────────┐
│                    auto-bug-migration Pipeline                          │
│                                                                         │
│  ┌──────────┐  ┌───────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐ │
│  │ 1. Data  ├─►│ 2. Trace  ├─►│ 3. Inject├─►│ 4. Agent ├─►│5. Fuzz │ │
│  │ (OSV API)│  │ + Diff    │  │ old func │  │ fix      │  │NewPoC  │ │
│  └──────────┘  └───────────┘  └──────────┘  └──────────┘  └───┬────┘ │
│                                                                │      │
│                                              ┌─────────┐  ┌───▼────┐ │
│                                              │ 7.Merge │◄─┤6.Mini- │ │
│                                              │         │  │ mize   │ │
│                                              └─────────┘  └────────┘ │
└────────────────────────────────────────────────────────────────────────┘
```

**七大阶段**：

| 阶段 | 做什么 | 输入 | 输出 |
|------|--------|------|------|
| 1. 数据采集 | OSV API 拉取 bug 元数据 + PoC | OSV ID | bug metadata + PoC testcase |
| 2. Trace + Diff | 在 bug 版本跑 PoC 拿执行 trace，找出 trace 中哪些函数在新版本被修改 | bug commit + target commit + PoC | 被修改函数列表 |
| 3. 注入旧函数 | 把旧版本的函数代码注入新版本，生成初始 patch | 被修改函数列表 + V1/V2 源码 | patch bundle (`.patch2`) |
| 4. Agent 修复 | LLM Agent 修复注入导致的编译错误 | patch bundle + build errors | 编译通过的 patched binary |
| 5. Target Fuzz | 对 patched binary 做定向 fuzz，生成新 PoC（旧 PoC 输入格式可能不适用） | patched binary | 新 PoC（触发 bug） |
| 6. 最小化 | 去掉不必要的 patch hunk，保留最小的触发 bug 所需修改 | 完整 patch + PoC | 最小化 patch |
| 7. 合并 | 多 bug 的最小化 patch 合并到同一版本 | N 个最小化 patch | merged benchmark |

---

## 二、数据采集层（OSV API）

### 4. 漏洞数据从哪里来？

- **OSV.dev API**（`get_bugs.py`）：批量查询 OSS-Fuzz 项目的漏洞 ID，支持分批 100 个、指数退避重试、429 限速处理。
- **OSV 详情提取**（`osv_helper.py`，638 行）：
  1. `api.osv.dev/v1/vulns/{osv_id}` 获取漏洞记录（introduced commit、fixed commit）。
  2. 从 references 里找 `bugs.chromium.org` 链接 → 跟踪 JS 重定向到 `issues.oss-fuzz.com`。
  3. 解析 HTML 提取 Detailed Report（project、fuzz_target、sanitizer、crash type）。
  4. 下载 PoC testcase 文件。
- **输出格式**：
  ```json
  {
    "OSV-2020-525": {
      "introduced": "abc123",
      "fixed": "def456",
      "reproduce": {
        "project": "opensc",
        "fuzz_target": "pkcs15init_fuzzer",
        "sanitizer": "address (ASAN)",
        "reproducer_testcase": "https://oss-fuzz.com/download?testcase_id=..."
      }
    }
  }
  ```

### 5. 面试官追问：为什么不直接用 OSS-Fuzz 的 ClusterFuzz API？

- ClusterFuzz API 是 Google 内部的，外部只能通过 OSV.dev 和 oss-fuzz.com 的 Web 页面获取数据。
- OSV.dev 提供的是标准化的漏洞元数据（introduced/fixed commit），但不直接提供 PoC 下载链接——需要从 bugs.chromium.org 的 issue 页面里抓取。
- 这就是为什么 `osv_helper.py` 需要处理 JS 重定向和 HTML 解析。

---

## 三、Patch 生成层（revert_patch_test.py）

### 6. 初始 patch 是怎么生成的？

这是项目最复杂的模块（4,344 行），核心逻辑：

1. **跑 trace**：在 bug 版本（V1）上用 PoC 跑执行 trace，记录经过了哪些函数。
2. **Diff V1 vs V2**：对比 bug 版本和目标版本的源码，找出被修改的函数。
3. **取交集**：trace 中的函数 ∩ 被修改的函数 = 需要迁移的函数列表。
4. **提取旧函数**：把 V1 中这些函数的代码提取出来，生成"注入到 V2"的 patch。
5. **函数级拆分**：用 libclang 解析源码 AST，把大 patch 拆分成**函数粒度的 PatchInfo**——每个 hunk 对应一个函数的修改。
6. **依赖分析**：识别函数之间的调用关系，标记 `dependent_func`（哪些函数调用了这个被修改的函数）。
7. **符号重命名**：迁移的函数统一加前缀 `__revert_<commit>_<original_name>`，让新旧版本的函数可以共存，避免重定义冲突。
8. **序列化**：所有 PatchInfo 打包成 `.patch2` 文件（gzip 压缩的 pickle），支持增量缓存和恢复。

### 7. PatchInfo 数据结构长什么样？

```python
@dataclass
class PatchInfo:
    file_path_old: str              # V1 文件路径
    file_path_new: str              # V2 文件路径
    patch_text: str                 # 统一 diff 文本
    old_start_line / old_end_line   # V1 中的行范围
    new_start_line / new_end_line   # V2 中的行范围
    patch_type: Set[str]            # {"Function signature change", "Function added", ...}
    old_signature / new_signature   # 函数签名（修改前/后）
    dependent_func: Set[str]        # 调用了此函数的其他函数
    hiden_func_dict: Dict           # 隐式依赖
    recreated_function_locations: Dict[str, FunctionLocation]
```

**面试关键点**：patch 不是简单的 diff 文本，而是**结构化的、函数粒度的、带依赖关系的**数据对象。

### 8. 函数重命名（`__revert_*` prefix）的设计思路是什么？

- **问题**：把 V1 的旧函数注入到 V2 中，会和 V2 已有的同名新函数冲突（redefinition error）。
- **方案**：迁移过来的旧函数加前缀 `__revert_<commit_hash>_<original_name>`。
- **好处**：
  1. 新旧版本的函数可以共存——V2 的 `parse_config()`（新版）和注入的 `__revert_abc123_parse_config()`（旧版）不冲突。
  2. 调用点也做自动替换——触发 bug 的调用链改为调用重命名后的旧函数。
  3. crash 验证时通过 `signature_change_list.json` 记录新旧名映射，stack trace 匹配时自动适配。
- **对 benchmark 目标的意义**：重命名让不同 bug 的迁移代码互不干扰——bug1 迁移的 `__revert_aaa_parse_config` 和 bug2 迁移的 `__revert_bbb_parse_config` 可以共存。这是多 bug patch 能合并的前提。
- **trade-off**：增加了 patch 复杂度（每个调用点都要改），但避免了最棘手的命名冲突问题。

### 9. 面试官追问：为什么用 libclang 做 AST 解析而不是 tree-sitter？

- **libclang 的优势**：理解完整的 C/C++ 语义——宏展开、模板实例化、`#if` 条件编译、类型推导。能准确定位函数定义的起止行（包括复杂的多行宏定义函数）。
- **libclang 的劣势**：依赖编译环境，有些项目编译配置不完整时 parse 会失败。
- **实际做法**：多 fallback 策略——先试带 `-resource-dir` 的完整解析，失败后退化到无 resource-dir 的粗粒度解析。头文件用 `-fsyntax-only` 模式。
- **为什么不用 tree-sitter**：tree-sitter 只做语法解析，不理解 C 语义。比如一个被 `#ifdef` 包裹的函数定义，tree-sitter 会当作普通代码块，不知道在当前编译配置下是否存在。

---

## 四、编译错误分类与路由

### 10. 编译错误是怎么分类的？

`build_errors.py` 用正则从编译日志中提取结构化错误信息：

| 错误类型                       | 正则匹配模式                                | 示例                     |
| ------------------------------ | ------------------------------------------- | ------------------------ |
| `undeclared_identifiers`     | `use of undeclared identifier 'X'`        | 变量/类型未定义          |
| `undeclared_functions`       | `implicit declaration of function 'X'`    | 函数未声明               |
| `missing_struct_members`     | `no member named 'X' in 'struct Y'`       | 结构体成员不存在         |
| `function_call_issues`       | `too few/many arguments to function call` | 函数签名变化             |
| `incomplete_types`           | `incomplete type 'struct X'`              | 只有 forward declaration |
| `redefinition_of_enumerator` | `redefinition of enumerator 'X'`          | 枚举重复定义             |
| `duplicate_case_values`      | `duplicate case value`                    | switch case 重复         |

每种错误类型对应一个**动态 prompt 片段**（`prompts/system_*.txt`），注入到 Agent 的 system prompt 中。

### 11. 错误路由策略是什么？

不同错误类型走不同的修复路径：

```text
undeclared identifier
  ├─ 是 __revert_* 符号？→ make_extra_patch_override（加 prototype/extern 声明）
  └─ 是普通符号？→ 搜索 KB 找定义 → 重写函数体

missing struct member
  → 先搜索 V1/V2 的 struct 定义 → 确认字段变更 → 适配访问方式

function call issues
  → 比较 V1/V2 的函数签名 → 适配参数列表

incomplete type
  → 搜索完整类型定义 → 加 #include 或前置声明

linker error (undefined reference)
  → 不是代码问题，是链接配置问题 → 映射到正确的依赖函数

macro error
  → 隔离到 _extra_* hunk（不修改原函数体，额外添加宏定义）
```

### 12. 面试官追问：为什么不直接把所有编译错误丢给 LLM，让它自己判断？

- **实测结果**：不做分类时，LLM 会对 linker error 尝试修改源码（比如注释掉调用），对 struct member 错误直接删掉访问语句——都是"编译通过但语义错误"的假修复。
- **分类的价值**：
  1. 过滤掉 LLM 不应该处理的错误（linker error、image error）。
  2. 为 LLM 提供正确的修复策略（通过 prompt 片段注入），而不是让它自由发挥。
  3. 确保 LLM 在修复之前先查询必要信息（比如强制先搜索 struct 定义再修复 missing member）。

---

## 五、Multi-Agent 编排

### 13. 多 Agent 是怎么并行的？

`multi_agent.py`（~1,260 行）的编排逻辑：

1. **按 patch_key 分组**：一个 patch bundle 可能包含几十个 hunk（每个 hunk 对应一个函数），编译错误按出错位置映射到对应的 patch_key。
2. **独立 spawn Agent**：每个 patch_key 启动一个独立的 `agent_langgraph.py` 进程，并行度由 `REACT_AGENT_JOBS` 控制（默认 4-10）。
3. **Agent 独立修复**：每个 Agent 只负责自己 patch_key 对应的函数，生成 override diff。
4. **合并 + 统一验证**：所有 Agent 完成后，合并所有 override diff 到一个 patch bundle，做一次统一的 OSS-Fuzz 构建测试。
5. **迭代**：如果统一构建仍有错误（可能是 Agent 之间的修改互相影响），回到步骤 1 重新分组。

### 14. Agent 之间有状态共享吗？如何避免冲突？

- **无共享状态**：每个 Agent 进程独立运行，有自己的 `AgentState`、工具实例和 artifact 目录。
- **冲突避免**：
  - 每个 Agent 只修改自己 patch_key 对应的 hunk，不会碰其他 hunk。
  - `make_error_patch_override` 工具有 guardrail——生成的 override diff 必须在 mapped slice（当前 hunk 的行范围）内，不能越界修改相邻代码。
  - 如果两个 hunk 在同一个文件的相邻行——极端情况下可能有行号漂移问题，但 effective patch bundle 会在合并时重新计算行号映射。
- **最终一致性**：统一验证阶段会捕获所有遗漏的冲突。

### 15. 面试官追问：为什么不用一个大 Agent 一次修所有错误？

- **上下文窗口限制**：一个项目可能有 20-50 个 hunk，每个 hunk 的上下文（patch 文本 + 错误信息 + KB 查询结果）可能占 2-5K token，全部塞入一个 Agent 会挤爆上下文。
- **错误类型差异大**：undeclared identifier 的修复策略和 struct member missing 完全不同，一个 Agent 同时处理多种错误容易混淆。
- **并行加速**：10 个 Agent 并行，比 1 个 Agent 串行快 5-8 倍。
- **故障隔离**：一个 Agent 修复失败不影响其他 Agent 的结果。

---

## 六、ReAct Agent 内部（已有笔记的核心内容，精简版）

### 16. Agent 状态机是怎么设计的？

LangGraph 三节点图：

```text
START → llm_node ──(has tool_calls)──→ tool_node ──→ llm_node
                  └─(no tool_calls)──→ END
```

- `llm_node`：调用 LLM，根据当前 state（错误信息 + 历史 + prompt）生成文本或工具调用。
- `tool_node`：执行工具调用，结果写回 state。
- 路由函数：检查 LLM 输出是否包含 `tool_calls`，有则继续循环，无则终止。
- **终止条件**：无 tool_calls、达到 `max_steps`、或达到 `recursion_limit`。

### 17. 14 个工具分哪几类？

| 类别       | 工具                                                                                | 作用                                        |
| ---------- | ----------------------------------------------------------------------------------- | ------------------------------------------- |
| KB 查询    | `search_definition`                                                               | 在 V1/V2 的 libclang 分析结果中查找符号定义 |
| 源码读取   | `read_file_context`, `read_artifact`                                            | 读取源文件片段或 Agent 生成的 artifact      |
| Patch 操作 | `make_error_patch_override`, `make_extra_patch_override`, `revise_patch_hunk` | 生成/修改 override diff                     |
| 构建测试   | `ossfuzz_apply_patch_and_test`                                                    | 在 OSS-Fuzz Docker 中应用 patch 并编译      |
| 错误解析   | `parse_build_errors`                                                              | 结构化解析编译日志                          |
| 迁移工具   | `get_error_patch_context`, `list_patch_bundle`, `search_patches`              | Patch bundle 查询和上下文获取               |

### 18. 动态 Prompt 组装是怎么做的？

`prompting.py` 维护 1 个 base prompt + 13 个错误类型片段：

```text
system_base.txt                    ← 始终包含
+ system_undeclared_symbol.txt     ← 如果有 undeclared identifier 错误
+ system_struct_members.txt        ← 如果有 missing struct member 错误
+ system_func_sig_change.txt       ← 如果有 function signature change
+ system_macro.txt                 ← 如果有 macro 相关错误
+ system_incomplete_type.txt       ← 如果有 incomplete type
+ system_visibility.txt            ← 如果有 visibility warning
+ system_linker_error.txt          ← 如果有 linker error
+ ...
```

每个片段包含该错误类型的**修复策略、工具使用指南和常见陷阱**。按当前编译错误类型动态拼接，避免无关信息占用 token。

### 19. 15+ 条 Guardrail 是做什么的？

拦截 LLM 的高频错误模式，**核心目的是确保修改最小化**（改多了会和其他 bug 的 patch 冲突）：

- 生成的 override diff **超出 mapped slice 范围** → 拒绝并要求重新生成（最关键的最小化约束）
- 修改了函数签名 → 拒绝（签名由 patch bundle 决定，Agent 不能改）
- 在函数体内插入 forward declaration → 强制移到函数外
- 对非 `__revert_*` 函数用 `make_extra_patch_override` → 应该用 `make_error_patch_override` 重写函数体
- 没先 `read_artifact` 读最新 BASE slice 就生成 patch → 注入提醒
- 丢失 `__revert_*` 前缀 → 强制保留

---

## 七、OSS-Fuzz 集成与 Docker 构建

### 20. OSS-Fuzz 的 Docker 环境是怎么管理的？

`fuzz_helper.py`（2,954 行）管理所有 Docker 操作：

1. **镜像版本固定**：硬编码了 2019-2022 年的 base-builder 和 base-runner 镜像 SHA256 digest。根据目标 commit 的时间戳选择对应年代的镜像，确保构建环境和当时一致。
   ```python
   BASE_BUILDER_IMAGES = [
       ('2019-05-13', 'sha256:bd7e28...', '1.0.0'),
       ('2021-08-23', 'sha256:859b69...', '1.0.0'),
       ('2022-07-30', 'sha256:5b714b...', '1.1'),
       ...
   ]
   ```
2. **构建流程**：`build_image_impl()` → `build_fuzzers_impl()` → 编译产物在容器内的 `/out` 目录。
3. **Patch 注入**：通过 Docker volume 或 Dockerfile 模板把 patch 文件注入容器，在编译前 `git apply`。

### 21. 面试官追问：为什么要固定 Docker 镜像版本？

- OSS-Fuzz 的 base-builder 会持续更新（新 Clang 版本、新 sanitizer 选项）。如果用 latest 镜像编译 2019 年的代码，可能因为新编译器的 breaking change 编译失败——这不是 patch 的问题，而是环境不匹配。
- 固定镜像确保**可复现性**：同一个 commit + 同一个镜像 = 相同的编译结果。
- 同时也固定了 OpenSSL 版本（1.0.0 / 1.1），避免 TLS 依赖问题。

---

## 八、Trace 分析、Target Fuzz 与最小化

### 22. 怎么确定哪些函数需要迁移？

**不再对比两个版本的 trace**。实际做法更直接：

1. **在 bug 版本上跑 PoC**：用函数级插桩收集执行 trace（经过了哪些函数）。
2. **Diff V1 和 V2**：对比 bug 版本（V1）和目标版本（V2）的源码，找出哪些函数被修改了。
3. **取交集**：trace 中出现的函数 ∩ 被修改的函数 = **需要迁移的函数**。
4. 把这些函数的 **V1（旧）版本代码注入到 V2（新）版本**中 → 必然产生编译错误 → 交给 Agent 修复。

**为什么只看 trace 中的函数**：不在 trace 中的函数即使被改了也不影响 bug 的触发，不需要迁移。这天然就是一种最小化——只动必须动的。

### 23. 为什么需要 target fuzz 生成新 PoC？旧 PoC 不能直接用吗？

- **旧 PoC 可能不适用**：新版本的输入解析逻辑可能变了（比如新增了格式校验、改了 header 字段长度、换了编码方式），旧 PoC 的输入在新版本上可能在到达漏洞函数之前就被拒绝了。
- **做法**：编译通过后，以旧 PoC 为种子做定向 fuzzing（target fuzz），让 fuzzer 自动变异输入来适应新版本的格式，生成能在新版本上触发同一个 bug 的新 PoC。
- **判定标准**：fuzzer 触发了 crash，且 crash 类型和原始 bug 一致（同类型的 ASan 报告 + stack trace 经过了被迁移的旧函数）。

### 24. 最小化是怎么做的？

bug 触发后，当前的 patch 可能包含不必要的修改（比如迁移了一些 trace 里有但实际不影响 bug 触发的函数）。最小化就是找到**触发 bug 所需的最小 patch 子集**：

- **贪心最小化**（`minimize_greedy`）：逐一移除 patch hunk，每次移除后重新编译 + 跑 PoC。如果仍然能触发 bug → 说明这个 hunk 不是必须的，可以去掉。
- **函数依赖感知**（`minimize_func_list_greedy`）：考虑函数调用关系——如果函数 A 调用了函数 B，那 B 的 hunk 不能在 A 之前被移除。
- **Trace 过滤**（`filter_patches_by_trace`）：先用 trace 粗筛，只保留 trace 中出现的函数对应的 hunk + 传递闭包的依赖。
- **最小化的意义**：patch 越小，和其他 bug 的 patch 冲突概率越低，后续合并成功率越高。

### 24a. 面试官追问：最小化后还有误报吗？

- **可能有**：最小化保证的是"移除某个 hunk 后 bug 不触发"，但不保证触发的 bug 和原始 bug 完全一致——可能是迁移引入的新 bug 而不是原始 bug。
- **缓解措施**：检查 crash 的 stack trace 是否经过了被迁移的旧函数（`__revert_*`），如果 stack 完全在新版本的代码里，说明不是原始 bug。
- **终极验证**：人工审核 crash report，确认 crash 类型和触发路径和原始 bug 一致。

---

## 九、Patch 合并层（patch_merge.py）

### 25. 多 bug 的 patch 怎么合并？（项目的终极目标）

项目的最终目标是**在同一个版本上聚合最多的 bug**，所以 patch 合并是整条流水线的终点。难点在于不同 bug 的 patch 可能冲突：

1. **构建兼容性图**：
   - 节点 = 每个 bug 的 patch bundle。
   - 边 = 两个 patch 是否兼容。
   - **兼容条件**：两个 patch 修改的函数完全不重叠，或者两个 bug 在同一个 commit 上都能触发（共享 commit）。
2. **求最大团（maximal clique）**：用 **Bron-Kerbosch 算法** 找所有完全兼容的 patch 子集。
3. **选最大团**：选包含最多 bug 的兼容组，合并成一个 unified diff。
4. **Patch 刷新**：如果某些 patch 是在不同 commit 上生成的，需要"刷新"——在共享 commit 上重新运行 `revert_patch_test` 生成新 patch。
5. **输出**：`patch/group_<commit>_final.diff` + `merged_<commit>.json`（函数名映射）。

### 26. 面试官追问：为什么用 Bron-Kerbosch 而不是贪心？

- **问题本质是最大团问题**（NP-hard），但实际图很小（通常 <50 个 bug），Bron-Kerbosch 完全可行。
- **贪心的问题**：贪心选择局部最优的兼容对，可能错过全局最大的兼容组。比如 A-B 兼容、A-C 兼容、B-C 不兼容——贪心可能先选 A-B，错过了 A-C 组合（如果 C 还兼容更多其他 patch）。
- **实际性能**：几十个 bug 的兼容性图，Bron-Kerbosch 在毫秒内完成。

### 27. Patch 兼容性具体怎么判断？

两个 patch P1（修复 bug1）和 P2（修复 bug2）兼容的条件：

```text
if P1 和 P2 修改的函数集合没有交集:
    → 一定兼容（互不影响）
elif P1 和 P2 修改了相同函数:
    if bug1 和 bug2 都在某个 commit C 上能触发:
        → 在 commit C 上两个 patch 可以共存 → 兼容（需要刷新到 commit C）
    else:
        → 不兼容
```

---

## 十、静态分析工具链

### 28. libclang 分析结果（`*_analysis.json`）的结构是什么？

Agent 的知识库（KB）是通过 libclang 预先解析 V1/V2 源码生成的 JSON 文件：

```json
[
  {
    "kind": "FUNCTION_DEFI",
    "spelling": "parse_config",
    "usr": "c:@F@parse_config",
    "location": {"file": "/src/config.c", "line": 42, "column": 1},
    "extent": {"start": {"line": 42}, "end": {"line": 85}},
    "is_definition": true
  }
]
```

`KbIndex`（`core/kb_index.py`）加载这些 JSON，支持按符号名、USR、文件路径查询。Agent 调用 `search_definition("parse_config", version="v2")` 时，KbIndex 查找 V2 的分析结果，返回函数的完整定义位置，然后 `SourceManager` 从源码中读取对应的代码片段。

### 29. GumTree 做了什么？

`gumtree.py`（293 行）：当函数在版本间被**移动或重构**时，简单的行号对比无法定位对应关系。GumTree 做 AST 级别的 diff：

- 输入：V1 和 V2 的同一个函数的源码。
- 输出：AST 节点的对应关系（哪些旧节点匹配哪些新节点）。
- 用途：确定"V1 的第 42 行"对应"V2 的第 67 行"，即使中间插入/删除了很多代码。

---

## 十一、工程细节（加分项）

### 30. 数据缓存和增量处理

- **Pickle 缓存**（`utils.py`）：每个项目的 patch bundle 缓存到 `data/patches/<target>_patches.pkl.gz`。下次运行时加载缓存，只处理新的 bug。
- **安全反序列化**（`patch_bundle.py`）：用 `RestrictedUnpickler` 白名单只允许反序列化 `PatchInfo` 和 `FunctionLocation`，防止 pickle 反序列化攻击。
- **路径验证**：`ensure_allowed_path()` 限制 patch bundle 只能从 `data/tmp_patch` 和 `data/react_agent_artifacts` 目录读取。

### 31. Artifact offloading

- LLM 工具调用的输出（patch 文本、源码片段、错误日志）可能很长（几千 token），直接放在对话历史中会挤爆上下文。
- 解决方案：大输出 offload 到文件（`data/react_agent_artifacts/multi_<run_id>/<patch_key>/`），对话中只保留文件路径引用。Agent 需要时通过 `read_artifact` 工具按需读取。

### 32. Patch 最小化（详见 Q24）

最小化的详细策略已在 Q24 中描述。这里补充实现层面：`utils.py` 的 `minimize_greedy` / `minimize_func_list_greedy` / `filter_patches_by_trace` 三个函数配合使用。最小化的判定标准不是"编译通过"，而是"bug 仍可触发"——需要跑 target fuzz 验证。

---

## 十二、面试高频追问

### 33. 整个系统跑一个 bug 需要多长时间？

- **数据采集 + Trace**：几分钟（OSV API + 在 bug 版本跑 PoC 收集 trace）。
- **Patch 生成**：1-5 分钟（diff + libclang 解析 + 注入旧函数 + 依赖分析）。
- **Agent 修复**：5-30 分钟（取决于错误数量和复杂度）。每个 Agent 超时 `REACT_AGENT_TIMEOUT=1800s`。
- **Target Fuzz**：取决于 fuzzer 效率。可能几分钟就触发，也可能跑几个小时。
- **最小化**：每次移除一个 hunk 都要重编 + 重跑 fuzz，O(N) 次迭代，可能 10-30 分钟。
- **总计**：简单 bug 约 30 分钟，复杂 bug（编译错误多 + fuzz 难触发）可能几小时。
- <!-- TODO: 你实际跑过的项目数量和平均时间？ -->

### 34. 修复成功率是多少？

- <!-- TODO: 你的实际成功率数据。按项目/按错误类型的统计。 -->
- **影响因素**：
  - 简单错误（undeclared identifier、missing include）→ 修复率高。
  - 复杂错误（struct 重构、API 行为变更）→ 修复率低。
  - 错误数量越多（>10 个 hunk），整体成功率越低（需要所有 hunk 都修复才算成功）。

### 35. 这个项目和 Google 的 OSS-Fuzz-Gen 有什么区别？

|                      | auto-bug-migration                          | OSS-Fuzz-Gen           |
| -------------------- | ------------------------------------------- | ---------------------- |
| **最终目标**   | 构建多 bug 共存的 fuzzing benchmark          | 提升 fuzzing 覆盖率    |
| **任务**       | 跨版本 patch 迁移的编译修复                  | 自动生成 fuzz harness  |
| **输入**       | 已有的 revert patch + 编译错误               | 目标函数签名           |
| **LLM 的角色** | 修复编译错误（受 patch 结构约束，最小化修改）| 从零生成代码           |
| **上下文来源** | libclang KB + V1/V2 源码对比                 | Fuzz Introspector      |
| **验证标准**   | 编译通过 + crash 复现 + 多 patch 兼容        | 编译通过 + 覆盖率      |
| **最大挑战**   | 多 bug patch 兼容共存 + 最小化修改           | LLM 的 fake definition |

### 36. 为什么现有的 fuzzing benchmark 不够用？你的方案比它们好在哪？

- **现有 benchmark 的问题**：
  - **Magma / LAVA**：人工注入 bug 或用静态插桩注入，bug 不够真实，fuzzer 可能"学会"这些人工模式。
  - **按 commit 选取**：每个 commit 上只有当时存在的 bug，数量有限（可能只有 1-3 个），统计显著性不够。
  - **FuzzBench**：评估 fuzzer 的覆盖率效率，但不直接评估 bug 发现能力。
- **auto-bug-migration 的优势**：
  - 使用 **OSS-Fuzz 的真实历史漏洞**（有 CVE、有 PoC、有修复 commit），不是人工注入的。
  - 在同一个版本上聚合 N 个 bug，可以直接统计 fuzzer 在固定时间内发现了几个——**比覆盖率更直接的评测指标**。
  - 每个 bug 都有 PoC testcase 作为 ground truth，可以自动判定是否触发。
  - 最小化修改确保 bug 的触发路径和原始版本尽量一致，不会因为迁移引入 artifact。

### 37. 项目最大的技术挑战是什么？

1. **最小化修改 vs 编译通过的矛盾**：LLM 倾向于"大刀阔斧改代码让编译通过"，但改多了会破坏和其他 bug 的 patch 兼容性。Guardrail 的 mapped slice 约束就是为了强制最小化。
2. **多 bug patch 兼容性**：每个 bug 独立迁移后，合并到同一版本时可能冲突——两个 bug 的 patch 改了同一个函数。需要兼容性图 + 最大团算法来选择最大兼容子集。
3. **多 hunk 依赖管理**：一个 bug 的 revert patch 可能涉及 20+ 个函数，它们之间有调用依赖。修复 hunk A 可能引入新的编译错误，影响 hunk B。
4. **版本间语义差异**：相同的函数名在 V1 和 V2 可能有不同的签名、不同的结构体成员、不同的宏定义。Agent 需要同时理解两个版本的代码。
5. **Docker 环境的历史兼容性**：2019 年的代码需要 2019 年的编译器和依赖，Docker 镜像的版本选择是关键。

### 38. 如果从头重新设计，你会改什么？

- <!-- TODO: 你自己的反思。可以考虑以下方向：-->
- 可能的改进方向：
  - **修改量度量**：引入一个显式的"patch 最小化 score"（如 diff 行数），作为 Agent 的优化目标而不仅仅是编译通过——修改越少，和其他 bug 兼容的概率越高。
  - **更细粒度的错误路由**：当前是按错误类型分类，未来可以按"错误 + 上下文"做更精细的路由。
  - **增量编译**：当前每次 Agent 修改都要完整重编，增量编译可以大幅减少验证时间。
  - **语义验证**：编译通过不代表语义正确，可以加运行时 test 或 differential testing。
  - **更好的 Agent 间协调**：当前 Agent 完全独立，未来可以让 Agent 共享"已发现的类型映射"信息。

### 39. 代码量有多大？你一个人写的吗？

- 整个项目约 **~38,000 行 Python**（`react_agent/` 约 20,000 行 + 其余约 18,000 行）。
- 核心模块（Agent、multi-agent、prompt 系统、guardrail）是我设计和实现的。
- 数据采集（OSV API）、Docker 管理（fuzz_helper）、patch 合并（patch_merge）也是项目的一部分。
- <!-- TODO: 根据你的实际情况调整——哪些是你写的，哪些是复用/参考的。 -->

---

## 十三、数据流速查表

```text
OSV.dev API
    │ osv_helper.py
    ▼
bug metadata + PoC ──────────────────────────┐
    │                                         │
    │ 在 bug 版本跑 PoC → trace              │
    │ diff V1/V2 → 找被修改的函数             │
    │ trace ∩ 修改函数 → 注入旧函数到新版本   │
    ▼                                         │
PatchInfo (.patch2) ← 编译必然报错            │
    │                                         │
    │ multi_agent.py (N 个并行 Agent 修复)    │
    ▼                                         │
patched binary (编译通过)                     │
    │                                         │
    │ target fuzz (以旧 PoC 为种子) ◄────────┘
    ▼
bug 触发? ──(no)──→ 失败，换策略
    │
   (yes)
    │
    │ minimize (贪心去 hunk，保持 bug 可触发)
    ▼
最小化 patch ──→ data/patches/*.pkl.gz
                    │  (repeat for each bug)
                    │
                    │ patch_merge.py (Bron-Kerbosch 求最大兼容集)
                    ▼
                group_*_final.diff
                    │
                    ▼
           ┌─────────────────────┐
           │  Fuzzing Benchmark   │
           │  同一版本，N 个 bug  │
           │  共存且均可触发      │
           └─────────────────────┘
```

---

## 十四、后续待补充

- <!-- TODO: 你实际跑过的项目列表和成功率数据（Q33/Q34） -->
- <!-- TODO: 你的个人贡献范围（Q38） -->
- <!-- TODO: 从头重新设计的反思（Q37） -->
- <!-- TODO: 和直接 LLM 生成 patch 的 baseline 对比数据（如果有的话） -->
