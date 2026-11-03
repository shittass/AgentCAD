# AgentCAD 代码结构图（Code Map）

**基线**：`fe2cdaa`（fork `shittass/AgentCAD` 的 main，上游 `59b8ba6`）  
**范围**：`src/agentcad` 73 个 Python 文件、19,508 行；外加 `tests/`、`scripts/`、`.github/workflows/`、`installer/`、`web/viewer/`、`examples/` 的组织方式。  
**方法**：12 个子代理分批（每批 ≤4 个并发）分区读码：事务骨架、操作层、验证、规格与工程域、接口、输出、平台、测试、仓库与 CI、跨模块调用链、跨切面机制、状态与恢复；每条结论落到 `file:line`。成稿后另用 2 个独立子代理对照代码校验（断言抽查 + 覆盖/一致性），校验结论写回本文。文中的所有结构统计（文件数、行数、依赖边、循环依赖、ezdxf 直接导入者）由 `scripts/code_map_stats.py` 生成，口径见其 docstring；移动模块后重跑即可更新。

## 这份文档回答什么

- 一个调用从 MCP / CLI / HTTP 进来，到 DXF 落盘、校验、写 journal，中间经过哪些函数（第 2 章）。
- 73 个文件各管什么、属于哪一层、谁依赖谁（第 1、3 章与附录）。
- 哪些不变量必须成立（原子性、修订号单调、句柄稳定、路径沙箱……），由哪段代码负责（第 3、4、5 章）。
- 要新增一种操作 / 一条验证规则 / 一个导出格式 / 一个 MCP 工具，改哪些文件（第 6 章）。
- 哪里最容易改坏、磁盘与性能的代价在哪、测试缺口在哪（第 7、8 章）。

## 与 docs/architecture.md 的关系

[architecture.md](architecture.md) 约 120 行，讲“是什么、为什么这么设计”：分层图、数据模型、结果信封、验证器语义、安全边界。本文件是它的逐文件展开，讲“代码在哪、调用怎么走”，不重复它的设计论述。建议顺序：先 architecture.md 建立概念，再用这份文档定位代码。

## 怎么读

| 你的目的 | 从哪看 |
|---|---|
| 只想改一个功能 | 第 6 章扩展指南 → 第 3 章对应小节 |
| 接手全局 | 第 1 章分层 → 第 2 章生命周期 → 第 3 章模块目录 |
| 排查事务、丢数据、锁、恢复 | 第 4.1、4.2 节与第 5 章 |
| 排查“改完变慢” | 第 7 章热点表（附 `benchmarks/` 实测锚点） |
| 加测试 / 找没测到的文件 | 第 8 章测试地图 |

维护约定：目录结构或主要入口改动后，同步更新第 3 章与第 10 章附录（文件清单由脚本从 `src/agentcad` 生成，行数以生成时为准）。

## 目录

- 这份文档回答什么
- 与 docs/architecture.md 的关系
- 怎么读
- 1. 分层与依赖方向
- 2. 一次调用的生命周期（跨模块调用链）
- 3.1 事务骨架：service / engine / store / locking / policy / guards / errors
- 3.2 操作层：ops/*、model、units、geom、constraints
- 3.3 验证与度量：validate/*、quality、verification、measure、fits、tolerances
- 3.4 规格、工程域与注释：spec / docspec / drawing_frontend / stdlib / engineering/*
- 3.5 接口与进程：cli、mcp_server、http_api、worker、sessions、progress、viewer、viewer_link、demo、discovery、mcp_config
- 3.6 输出、图像与脚本：plot、delivery、images、image_dimensions、_image_runner、_script_runner、scripting、metadata
- 3.7 平台、安装与后端：install、setup_gui、desktop、backends/*
- 4. 跨切面机制（锁、修订、错误、单位、标签、元数据、指标、策略、分层）
- 5. 状态、文件与恢复（.agentcad/、检查点、journal、提交、recover）
- 6. 扩展指南：改什么、按什么顺序
- 7. 热点、代价与风险清单
- 8. 测试地图
- 9. 仓库、构建、CI 与文档管线
- 10. 附录：文件清单
- 11. 校验记录与维护

## 1. 分层与依赖方向

### 1.1 五层结构

```
外部调用方：Agent（MCP / CLI / HTTP）/ Python 脚本 / 测试
        │
        ▼
接口层   cli.py  mcp_server.py  http_api.py  worker.py          （2,950 行 / 11 文件）
        │   只做协议适配、进程调度与串行化；所有业务都下沉到 service.Runtime
        ▼
服务层   service.py（1,590 行，唯一业务门面：结果信封、指标、锁、journal 编排）
        ├─ 事务   engine.py → ops/*（声明式操作，预校验 → 内存执行 → 失败丢弃）
        ├─ 状态   store.py + locking.py（原子提交、检查点、journal、崩溃恢复）
        ├─ 校验   validate/* + verification.py（只读磁盘的独立验证器）
        ├─ 规格   spec.py / docspec.py → 操作计划 + requirements
        ├─ 领域   engineering/*（P&ID、建筑、机械、plant 读取）
        ├─ 输出   plot.py / delivery.py（SVG/PDF/PNG、交付门禁）
        └─ 策略   policy.py（沙箱路径、只读、白名单、预算）
        │
        ▼
内核     ezdxf（DXF 读写）；可选外部程序 AutoCAD accoreconsole/COM、FreeCAD
```

### 1.2 依赖统计（用 AST 从源码算出，基线 `fe2cdaa`）

- 73 个模块、19,508 行；模块之间的内部依赖边 **262 条**，其中 179 条在模块加载时建立，83 条是函数内延迟导入。
- 被依赖最多（入度）：`errors` 49、`model` 25、`geom` 15、`policy` 15、`units` 12、`ops.registry` 10、`store` 7。
- 依赖别人最多（出度）：`service` 35、`cli` 11、`viewer` 10、`ops`（包 `__init__`）9、`validate.checks` 8、`ops.edit` 8、`delivery` 8。
- 叶子模块（不依赖任何内部模块）：`errors`、`geom`、`appearance`、`drawing_frontend`、`desktop`、`worker`、`backends`（包）、`_fc_view`、`engineering`（包）、包 `__init__`。
- **直接 `import ezdxf` 的模块有 30 个**，几乎覆盖每一层：`service`、`store`、`engine`、`ops/*`、`validate/*`、`plot`、`viewer`、`engineering/*`、`model`。也就是说 ezdxf 没有被收拢进单一“内核适配层”，而是作为数据模型贯穿全仓。这是有意的（操作直接操作 DXF 实体、验证器要读实体属性），代价是升级 ezdxf 时要全仓核对，收益是不用为每个能力写一层 DTO。

以上统计由 `scripts/code_map_stats.py` 生成（口径写在该文件 docstring：模块=文件、边=去重后的模块对、`from . import a, b` 逐个别名展开、`TYPE_CHECKING` 不计、顶层 `if/try` 内的导入算模块级）。移动模块或增删导入后重跑它，按输出更新本节与 1.5。

### 1.3 分层规则（实测，不是文档宣称）

- **适配层不碰内核**：`http_api` 只导入 `service/errors/policy`；`mcp_server` 导入 `service/worker/backends/install/policy`；`cli` 导入 `service/viewer/install/mcp_config/mcp_server…`，三者都**不直接**导入 `ezdxf`、`ops`、`engine`。注意这只是直接导入层面：`http_api.py:20` 与 `mcp_server.py:18` 在模块级导入 `service`，所以导入适配层仍会间接加载 ezdxf（只有 `cli.py:307` 是延迟导入）；`tests/test_g_interfaces.py:208-221` 的 AST 守卫也只覆盖直接导入，拦不住这类间接依赖，更拦不住 `store→sessions`、`validate.checks→plot` 这样的越层。
- **只有 5 个模块导入 `service`**：四个入口 `cli`、`demo`、`http_api`、`mcp_server`，加上 `viewer`——而 `viewer` 是在函数内导入 `service._journal_line` / `service.diff_docs` 这两个纯函数（viewer.py:281、viewer.py:378），不构成业务依赖。
- **`engine` 只被 `service` 与 `engineering.records` 导入**；**`ops` 只被 `engine`、`service`、`discovery`、`engineering.records` 导入**。想绕过事务层直接跑操作，入口只有这几个。
- **`validate` 不依赖 `service`**，只依赖 `store/policy/model` + 磁盘文件；这是“独立验证”的结构保证——验证器无法读内存里的中间态，只能读落盘结果。
- 一处跨层细节：`validate/checks.py:217` 在函数内导入 `plot.font_cmap/plot_fonts`，用于检查“PDF 字体缺字形导致文字画不出来”。方向是“校验器 → 出图器”，但只在函数内、只取字体表，加载期无环。

### 1.4 循环依赖：5 组，全部靠函数内导入断开

| 环 | 顶层（加载期）方向 | 断开方式 |
|---|---|---|
| `service` ↔ `viewer` | 两边都是函数内导入 | 无加载期环；`viewer` 起服务时按需取 `service` 的纯函数 |
| `model` ↔ `tolerances` | 两边都是函数内导入 | `model.py:253`（取公差信息）、`tolerances.py:66`（取尺寸实测） |
| `ops.edit/draw/annotate` 三角 | 四条边全部在函数内 | 无加载期环；执行函数互相调用 |
| `validate.checks` ↔ `validate.drafting` | `checks→drafting` 顶层，反向函数内 | 无加载期环 |
| `engine` + `engineering.*` + `ops` + `ops.engineering` 大环 | 顶层边与函数内边混用（`ops/__init__.py:2`、`engine.py:27`、`engineering/pid.py:9-10`、`engineering/mechanical.py:13` 是顶层边） | `engineering.records` 同时依赖 `engine` 与 `ops`，是这条环的根；仍有函数内边断开，故无加载期环 |

**结论**：加载顺序永远安全（0 个加载期环），但“分层单向”是靠函数内导入与测试维持的约定，不是 import 纪律强制的。新增模块不要引入**顶层**回边；需要回指上层时照现有做法在函数内 import，并在注释里写清原因。

### 1.5 体量分布

| 子系统 | 文件 | 行数 | 占比 |
|---|---:|---:|---:|
| 操作层 `ops/*` + `model/units/geom/constraints` | 14 | 4,510 | 23.1% |
| 规格与工程域 `spec/docspec/engineering/*/stdlib/tabular/…` | 14 | 3,464 | 17.8% |
| 接口与进程 `cli/mcp_server/http_api/viewer/worker/progress/…` | 11 | 2,950 | 15.1% |
| 事务骨架 `service/engine/store/locking/policy/guards/errors` | 8 | 2,669 | 13.7% |
| 验证与度量 `validate/*/quality/verification/measure/fits/tolerances` | 10 | 2,208 | 11.3% |
| 平台与后端 `install/setup_gui/desktop/backends/*` | 8 | 2,187 | 11.2% |
| 输出与媒体 `plot/delivery/images/scripting/…` | 8 | 1,520 | 7.8% |
| **合计** | **73** | **19,508** | **100%** |

最大的 12 个文件：`service` 1,590、`viewer` 1,159、`ops/edit` 1,076、`install` 919、`ops/library` 673、`store` 579、`validate/checks` 574、`ops/annotate` 501、`mcp_server` 496、`engineering/mechanical` 485、`plot` 466、`engineering/architecture` 462。前 12 个文件共 8,980 行，占全仓 **46%**；其中 3 个（service / store / ops.edit）承载了绝大多数事务与编辑逻辑，是改动的风险集中区。

### 1.6 从依赖图能直接得到的三个判断

1. **`errors.py` 是事实上的契约层**：49 个模块导入它。错误码与 `AgentCADError` 的字段（code/category/retryable/partial_changes/hint）是全仓最广的公共接口，改它等于改所有模块。
2. **`service.py` 是唯一编排中心**（35 条出边）：任何跨越“执行 + 落盘 + 校验 + 记账”的流程都必须经过它；绕过它直接调 `engine` 或 `store`，就会丢掉信封、指标、journal 与复检。
3. **分层方向靠约定维持**：5 组环全靠延迟导入；`ezdxf` 直接出现在 30 个模块。判断一个改动是否“越层”，主要看它是否新增了**顶层**回边、是否让适配层导入了 `ops/engine/ezdxf`，而不是看目录名。

## 2. 一次调用的生命周期（跨模块调用链）

**规模**：12 个文件 / 6,259 行

**职责与边界**
这一层回答“一个请求从进程边界进来到文件落盘、页面刷新，中间经过了谁”。它由四段构成：协议适配（`mcp_server.py`、`cli.py`）只做签名声明与进程调度，不含 CAD 逻辑；`service.Runtime` 是唯一业务门面，负责信封、指标、写锁、journal 编排与结果校验；`engine`/`store` 把“批量原子性”与“提交原子性”拆成两级；`validate`、`plot`、`progress`、`viewer` 是只读或单向输出方。它不做 CAD 几何运算（在 `ops/*`）、不做事务语义判断（在 `engine`/`store` 内），也不保存跨进程状态（除 `.agentcad/` 下的 head/journal/checkpoint）。四种入口（CLI、MCP stdio、HTTP、Python）都收敛到同一组 `Runtime` 方法，HTTP 侧靠 `threading.Lock` 串行（`http_api.py:38`），MCP 侧靠 `CADWorker`（`worker.py:8-16`）。唯一的上行依赖出现在 `service` 与 `viewer` 之间，且两边都是函数内导入：`service._viewer_link` 取 `viewer.serves_root`/`viewer_url`/`start_background`（`service.py:259`、`268-269`），`viewer` 取 `service.diff_docs`/`_journal_line`（`viewer.py:281`、`viewer.py:378`）。

**文件清单**
| 文件 | 行数 | 角色 | 职责 |
|---|---:|---|---|
| `mcp_server.py` | 496 | 入口 | FastMCP 工具声明、`CADServer.add_tool` 串行化、discovery profile、stdio 启动 |
| `service.py` | 1590 | 核心 | `Runtime` 全部工具方法、`@tool` 信封包装、锁/journal/校验/视图编排 |
| `engine.py` | 204 | 核心 | 批次预校验 + 内存执行 + 失败整批丢弃；`reconcile` 就地更新 |
| `store.py` | 579 | 核心 | 状态目录、写锁、检查点、提交事务（pending）、journal、恢复 |
| `validate/__init__.py` | 107 | 核心 | `validate_file`：独立重读、审计、覆盖率、requirements、recognized |
| `validate/checks.py` | 574 | 核心 | 通用完整性/制图规范/打印可读性检查，`run_generic_checks` 汇总 |
| `cli.py` | 411 | 入口 | argparse 命令表、`Runtime` 分发、JSON 信封输出与退出码映射 |
| `plot.py` | 466 | 工具 | 无外部依赖的 PDF 写出器与 PNG 栅格化，纸张/比例/字体替换 |
| `scripting.py` | 178 | 支撑 | 脚本静态白名单检查、子进程启动、超时与结果读取 |
| `progress.py` | 212 | 支撑 | `Activity` 事件写入 `activity.jsonl`、路径脱敏、操作分组 |
| `viewer.py` | 1159 | 入口 | 只读观察端：HTTP 路由、SSE、变更轮询、版本/3D 缓存 |
| `spec.py` | 283 | 数据 | 规格校验、`compile_plate`/`compile_spec` 产出操作计划与 requirements |

**关键入口**
- `CADServer.add_tool`（`mcp_server.py:107-111`）— 所有 MCP 工具函数被换成 `worker.run(fn, …)`；`@wraps` 保留原签名，FastMCP/pydantic 仍按原注解校验（`mcp_server.py:102`）。
- `tool(fn)`（`service.py:67-107`）— 把一个 `Runtime` 方法包装成 `{ok, tool, …payload, error?, metrics}` 信封，并登记进 `TOOLS`（`service.py:105`）；`_activity` 决定这次调用是否上报进度（`service.py:228-249`）。
- `engine.execute`（`engine.py:50-100`）— 契约：先对全部 op 做 `prevalidate`（`engine.py:31-47`），再顺序执行；任何一步抛错都带 `op_index`/`op`/`partial_changes=False`（`engine.py:75-79`），调用方丢弃内存文档。
- `store.commit_bytes`（`store.py:396-406`）— 契约：检查写权限与体积上限、可选的 `overwrite_ok`，然后走 `_replace_tracked`（`store.py:408-468`）这一唯一提交路径（`commit` 与 `restore` 都复用它）。
- `validate_file`（`validate/__init__.py:40-107`）— 契约：只吃磁盘路径，自己建 `Policy(root=path.parent)` 重读（`validate/__init__.py:46-48`），不接锁、不写状态。
- `progress.Activity`（`progress.py:114-187`）— 契约：只追加、永不抛错（`progress.py:145-146`），`step` 限流 50 ms（`progress.py:32`、`148-154`）。
- `cli.run`（`cli.py:238-395`）— 契约：返回 `(envelope, exit_code)`，`main` 只打印非 `{"ok": True}` 的结果（`cli.py:405-406`）。

**数据结构与不变量**
- **单写者两级串行**：同一进程内所有工具共用一个 1 线程执行器（`worker.py:8-13`）；跨进程/跨入口靠每张图的 `store.lock`（`store.py:213-252`）。两者都已占用时，后来者等待而不是失败。
- **修订号单调**：`revision = head.revision + 1` 在 pending 标记里预先写入（`store.py:421-422`），替换成功后才落 head（`store.py:455-467`）；`guards.check` 用 `revision` + 文件 sha 双重比较（`guards.py:37-49`），不一致即 `STALE_REVISION`。
- **journal seq 单调**：`max(head.seq, 文件末尾 seq) + 1`，先追加记录再写 head，head 写失败只吞掉权限错误（`store.py:324-337`）。
- **句柄稳定**：`reconcile` 路径下同 tag 的实体就地改属性、保 handle（`engine.py:135-138`、`165-196`），几何相同则完全不动（`engine.py:129-134`）；所有 diff 以 handle 为键（`service.py:1571-1585`）。
- **提交可判定**：pending 记录目标 sha256；`finish_pending` 用当前文件哈希区分 `completed`/`discarded`，其余情况交回 `recover`（`store.py:513-534`）。替换前的任何失败由 `_abandon` 删掉本次检查点与 pending（`store.py:470-494`）。
- **路径沙箱**：`Policy.resolve` 拒绝 root 之外、`.agentcad/` 之内与错误后缀（`policy.py:38-63`）；导出目标不得等于源文件（`service.py:975-976`）。
- **验证器只读**：`validate_drawing` 全程不取锁、不写 journal（`service.py:636-648`），因此对同一文件可在事务外随时复算。

**主要调用链**

### A. MCP `tools/call apply_operations`（含 CADWorker 调度与 progress）

**触发点**：MCP 客户端按名字调用 `apply_operations`（工具定义 `mcp_server.py:196-220`，说明文本 `mcp_server.py:214-218`）。

1. `NotesClient.call_tool` 先记下 `clientInfo`（`mcp_server.py:92-94` → `76-86`），写进程会话文件（`sessions.py:69-97`）。
2. `CADServer.add_tool` 生成的 `serialized` 把同步函数丢进 `CADWorker.run`（`mcp_server.py:109-110`）；`run_in_executor` 进单线程池，`asyncio.shield` 让取消只放弃等待、不放弃正在跑的 CAD 工作（`worker.py:12-16`）。
3. 进入 `Runtime.apply_operations`（`service.py:434-489`）：`check_write`（445-446）→ 可选 `create` 先建图（448-449 → `499-510`，内部再调一次 `create_drawing`，各自独立 journal）。
4. 取图与锁（`service.py:450-451`）；先记录 `loaded_sha`、读盘、跑 `guards.check`（452-456），文件在读取期间被改则 `STALE_REVISION`。
5. `engine.execute`（459）：`prevalidate` 全量校验（`engine.py:31-47`）→ 逐 op `progress.step`（`engine.py:61` → `progress.py:202-206`）→ 派发（`engine.py:72`）。
6. `dry_run` 直接回预览，带 `revision`/`preconditions` 供下一次调用回灌（`service.py:464-467`）。
7. `dxf_bytes` 序列化（468）；`strict` 时先把这份字节写成临时快照再校验（469 → `619-627`），不通过则落 `failed/` 并 `VALIDATION_FAILED`（470-477）。
8. 替换前二次 guard（480），防止几何计算/校验期间被外部改动；`commit_bytes`（481）→ `_replace_tracked`（`store.py:408-468`）：结算旧 pending（415-418）→ 检查点（424-425）→ 写 pending（426-428）→ 临时文件 fsync 后 `os.replace`（432-443）→ 提交副本（447-454）→ 清 pending 并写 head（455-466）。
9. 写成功 journal（482 → `store.py:324-337`），解锁；非 strict 时用同一份字节做独立校验（483-484）。
10. 信封补 `changes`/`results`/`validation`（compact）/`journal_seq`/`sha256`/`revision`（485-489），回到包装层算指标（`service.py:100-102`）。

**分支/失败点**：`READ_ONLY`（`policy.py:65-67`）；`STALE_REVISION`（`guards.py:50-61`；读取期间被外部改动则由 `service.py:455-457` 直接抛）；`UNKNOWN_OP`/`OP_NOT_ALLOWED`/`BUDGET_EXCEEDED`（`engine.py:41-46`、`96-98`）；`FILE_EXISTS`（`service.py:352-353`）；`VALIDATION_FAILED`（validation）；`FILE_BUSY` 按替换前/后分 `retryable=true` 与 `partial_changes=true`（`store.py:56-64`、`429-431`、`459-466`）；意外异常统一降级成 `INTERNAL_ERROR` 并留 8 帧 traceback（`service.py:84-87`）。

**副作用**：`.agentcad/drawings[-v2]/…/{head.json, journal.jsonl, checkpoints/cp-NNNN.dxf, committed.dxf}`、临时 `*.agentcad-tmp`、`failed/*.dxf`、`activity.jsonl`、`sessions/<pid>.json`；成功后追加 `viewer_url`（`service.py:96-99`）、`live`（`93-95`）。

### B. `draw_from_spec`（spec.compile_spec → engine → commit → 带 requirements 的验证）

**触发点**：`mcp_server.py:275-281`（`spec` + `output`），或 `cli.py:347-348` 的 `draw` 命令。

1. `check_write`（`service.py:717`）。
2. `spec.compile_spec`（718 → `spec.py:257-270`）；`plate` 走 `compile_plate`（`spec.py:147-254`），先 `check_plate`（`91-127`）收集 issues，再 `raise_if_issues` 映射成 `SPEC_CONFLICT`/`UNITS_UNDEFINED`/`SPEC_INVALID`（`130-136`）。计划含固定 `LAYERS` 前缀（`spec.py:30-37`）与稳定 tag（`outline`、`hole.*`、`dim.*`）。
3. 解析输出路径，已存在且未 `overwrite` → `FILE_EXISTS`（719-722）。
4. `setup_document`（723 → `service.py:152-181`）：版本/模板/单位显式校验，写 `$INSUNITS`、`$MEASUREMENT`、`$MIRRTEXT=0`、DIMSTYLE 与文字样式。
5. 加锁（724），`engine.execute`（726）；失败只写一条失败 journal 后抛出，磁盘未动（727-729）。
6. 把意图写回文档：`part_spec` 与 `plan_tags`（730-731），随 DXF 一起移动。
7. `dxf_bytes` → `commit_bytes`（732-733）→ journal 记录 `spec` 与完整 `plan`（734-735）；解锁。
8. 解锁后才校验：`_validate_bytes(requirements=compiled["requirements"], contour_layers=["OUTLINE"] if plate)`（736-737），报告附 `file`（738）。
9. 返回 `plan_size`、`requirements`、`validation`、`journal_seq`（739-740）。

**分支/失败点**：规格先行失败，不产生任何文件；`revise_from_document` 在 `status != "ok"` 时转成 `SPEC_CONFLICT`/`SPEC_INCOMPLETE`（`service.py:793-800`）。

**副作用**：新 DXF + 检查点（若覆盖）+ journal（含 spec 与 plan）+ 一次相对路径的独立验证；**与 `apply_operations` 不同，这里没有 `strict`**——第 8 步失败时文件已经提交（`service.py:733` 早于 `736`），只能靠 `rollback` 撤销。

### C. `validate_drawing`

**触发点**：`mcp_server.py:258-273`；CLI `validate`（`cli.py:338-346`）。

1. `_drawing(path)` 必须存在，后缀 `.dxf`（`service.py:645` → `197-199` → `policy.py:38-63`）。
2. `_drawing_validation`（`service.py:650-672`）：显式 `requirements` 优先；否则当 `use_stored_spec` 且检查的是模型空间时，先 `read_dxf` 再 `get_meta(doc, "part_spec", strict=True)`（657-663），有存储规格就重新 `compile_spec` 取 requirements，来源标 `stored_part_spec`，轮廓层默认 `["OUTLINE"]`（664-667）。存储规格非法时失败关闭，不降级成零需求。
3. `validate_file`（668 → `validate/__init__.py:40-107`）：自建 policy 重读；解析失败本身成为一条 `file` 类 issue 并提前返回（49-55）。
4. `doc.audit()` 的结构错误记为 `DXF_AUDIT_ERROR`（58-61）；抽取特征（63-68）。
5. `run_generic_checks`（69-70 → `checks.py:420-574`）：单位未定义、退化几何、重复实体、未注册 XDATA/超 16 KB、标注文本与实测矛盾、`DIMENSION_STALE`、`DIMENSION_NOT_ATTACHED`、`TEXT_PLOTS_AS_BOXES`、`PRINTED_TEXT_TOO_SMALL`、`LAYOUT_VIEWPORT_MISSING`、`OPEN_CONTOUR`、`OUT_OF_BOUNDS`。
6. 未检查的布局追加 `LAYOUT_NOT_VALIDATED` 警告并写入 `coverage`（73-81 → `21-37`）。
7. requirements 逐条比对（82-86）；`recognized` 汇总轮廓/圆/标注（87-99）；`passed = errors==0 and failed==0`（100-105）。
8. 服务层补 `file`（相对路径）与 `requirements_source`（`service.py:670-671`），默认返回 `compact_report`，`detail=true` 时才给全量（648 → `1542-1568`）。

**分支/失败点**：`FILE_NOT_FOUND`、`BAD_EXTENSION`、`PATH_OUTSIDE_WORKSPACE`/`PATH_RESERVED`（policy）；需求字段未知不静默丢弃，走 warnings（`service.py:1519-1524`）。CLI 对“验证不通过”返回退出码 1，而非错误码（`cli.py:391-392`）。

**副作用**：无。不建检查点、不写 journal、不取锁——这是它可被任意调用的前提。

### D. `rollback`（检查点恢复与预回滚快照）

**触发点**：`mcp_server.py:313-316`；CLI `rollback`（`cli.py:364-365`）。

1. `check_write`（`service.py:858`）。
2. `_drawing(path, must_exist=False)`（859）；`list_checkpoints` 读 head（860 → `store.py:255-258`），空则 `NO_CHECKPOINT`（861-862）。
3. 未指定 `checkpoint` 时：从最新往回找第一个 sha256 与当前文件不同的检查点（864-866）——留下一个“和现文件相同”的检查点（`FILE_BUSY` 的残留）不会让重试变成空操作。
4. 加锁（867），`store.restore`（868 → `store.py:536-561`）：源为 `committed.dxf` 或该检查点（540）；与 head 记录的哈希不符即 `CHECKPOINT_CHANGED`（542-548），防篡改。
5. **预回滚快照**：`restore` 内部 `checkpoint()` 先试读当前文件，读不通就跳过快照（`store.py:550-557`），避免把损坏内容存成检查点。
6. 走同一条 `_replace_tracked`（559）：结算旧 pending → 建快照检查点 → 写 pending → 替换 → 刷新提交副本 → 写 head。
7. journal 记录 `rollback` 与目标 `checkpoint`（`service.py:869`），返回 `restored`/`checkpoint_before`/`sha256`（870）。

**分支/失败点**：`NO_CHECKPOINT`（862）、`CHECKPOINT_NOT_FOUND`（`store.py:372-378`）、`CHECKPOINT_CHANGED`（546-548）、`FILE_BUSY`（同 A 的两段语义，`store.py:429-443`、`459-466`）；`recover` 侧对 `pending` 的结算见 `store.py:496-534` 与 `service.py:908-917`。

**副作用**：新增一个“回滚前”检查点；drawing 被字节替换；committed 副本与 head 更新；新增 journal 记录；`viewer_url`（属于 `VIEWER_TOOLS`，`service.py:46`）。

### E. `export_drawing` 的 svg 与 pdf 两条分支

**触发点**：`mcp_server.py:337-369`（png 还会把文件塞回结果，`364-368`）；CLI `export`（`cli.py:372-375`，格式缺省取输出后缀）。

公共前置（`service.py:941-977`）：`table`/`image_size` 与格式的搭配校验（957-963）→ `check_write`（964）→ 源文件必须存在（965）→ 后缀白名单（967-971，`UNSUPPORTED_FORMAT`）→ `FILE_EXISTS`（973-974）→ `SAME_FILE`（975-976）→ 加锁（977）。

**svg 分支**（1051-1065）：延迟导入 `svg.SVGBackend` 与 `DrawingFrontend`，缺 Pillow 等依赖时转 `DEPENDENCY_MISSING`（1056-1058，category `dependency`，CLI 退出码 6）→ 白底 `Configuration`（1061）→ **只画模型空间**（1062）→ `layout.Page(0,0,mm,margins=5)`（1063）→ 写文本（1064）→ journal（1071）→ 返回字节数（1072）。

**pdf 分支**（997-1017）：`scale>0`、`orientation` 枚举（999-1002）→ `_plot_layouts`（1003 → `1422-1434`；`layout=None` 取“有内容的图纸布局，否则 Model”，`*` 取全部图纸布局，未知名 `LAYOUT_NOT_FOUND`）→ 先算文字警告再出图（1004），因为 `plot._cjk_styles` 会就地替换 SHX 字体（`plot.py:243-254`）→ `plot_pdf`（1005 → `plot.py:286-335`）：逐布局 `_render` 进 recorder（276-283），图纸布局按其 page setup 1:1（294-299 → `209-217`），模型空间按 `scale` 或“最大能装下的标准比例”（306-316 → `183-223`），变换到点单位并裁剪（317-322），回放给 `_PdfPage` 写内容流（38-145），`write_pdf` 手写 PDF 并 `tmp.replace`（147-180）→ 出图后再查一次 `PRINTED_TEXT_TOO_SMALL`（1007-1014，阈值 `checks.MIN_PRINTED_TEXT`，`checks.py:308`）→ journal（1015）→ 返回 `pages` 与 `warnings`。

**分支/失败点**：`UNKNOWN_PAPER`、`PLOT_TOO_LARGE`（`plot.py:191`、`201-202`）、`LAYOUT_NOT_FOUND`（`service.py:1433`）、`MULTIPLE_LAYOUTS`（png 用 `layout="*"` 时，1021-1022）、`DEPENDENCY_MISSING`（svg）。

**副作用**：只写目标文件与一条 journal 记录；pdf 期间对内存文档的字体替换不落盘（`plot.py:288` 只改正在绘制的副本）。

### F. `run_script` 沙箱（policy 开关、子进程、事务化）

**触发点**：`mcp_server.py:233-246`；CLI `script`（`cli.py:323-329`，需全局 `--allow-scripts`，`cli.py:44-46`、`264-265`）。

1. `check_scripts_allowed`（`service.py:569` → `policy.py:69-73`）→ `SCRIPTS_DISABLED`（默认关闭，`policy.py:26`）。
2. 非 `dry_run` 时 `check_write`（570-571）。
3. 静态检查 `scripting.check_script`（572 → `scripting.py:81-130`）：AST 白名单导入（26-27）、禁用名（28-29）、禁用属性（38-53，含帧对象、`attrgetter`、`numpy` 文件读写、`odafc`）、禁字符串注解（125-130）；失败为 `SCRIPT_EMPTY`/`SCRIPT_SYNTAX`/`SCRIPT_REJECTED`，带行号。
4. 取图并**在持有写锁的情况下**准备任务目录 `<state>/scripts/job-<ms>`（573-576）。
5. `scripting.run`（578 → `133-153`）：把原图复制成 `in.dxf`、脚本写 `script.py`，`subprocess.run([sys.executable, "-I", _script_runner.py, job], cwd=job, shell=False, stdin=DEVNULL, timeout=policy.script_timeout)`（141-142）；超时 `SCRIPT_TIMEOUT`（143-145），没有 `result.json` 则 `SCRIPT_CRASHED`（147-150）。
6. 子进程 `_script_runner.main`：缩水 builtins（`_script_runner.py:20-26`）、受限 import 钩子（29-33）、以 `doc`/`msp` 执行用户代码（48-52）、保存 `out.dxf`（53）、结构化回写 `result.json`（60-66）。
7. 失败转 `SCRIPT_FAILED`，附 `script_lines`/`stdout`/`traceback`，并写失败 journal（`service.py:579-587`）；无论成败都 `rmtree(job)`（588-589）。
8. 严格重读产物（583）、实体预算（590-593，`BUDGET_EXCEEDED`）、与 `before` 做 `diff_docs`（594）。
9. `dry_run` 只回 diff（599-600）；否则 `dxf_bytes` → 可选 strict 校验（601-610）→ `commit_bytes`（613）→ journal 带**完整脚本**与 `code_sha256`（611-614）→ 解锁后独立校验（615-616）。

**分支/失败点**：`SCRIPTS_DISABLED`（policy）、`SCRIPT_REJECTED`（input）、`SCRIPT_TIMEOUT`（input）、`SCRIPT_CRASHED`（internal）、`SCRIPT_FAILED`（input）、`VALIDATION_FAILED`（validation）。

**副作用**：临时脚本目录（结束即删）、检查点、committed 副本、journal 里保留整段代码与其哈希、`failed/`（strict 失败时）。注意 `scripting.py:11-12` 明说这套检查防误用、不是安全边界。

### G. viewer 实时更新（`activity.jsonl` → 页面）

**触发点**：任一计入 `PROGRESS_TOOLS` 的工具调用（`service.py:48-51`），或文件本身发生变化。

1. `Runtime` 在 `@tool` 包装里 `progress.begin`（`service.py:78`），`Activity.__init__` 记录 run id 并在必要时把 `activity.jsonl` 轮转到 `.1`（`progress.py:117-134`，阈值 512 KB）。
2. `emit` 追加一行 JSON（`progress.py:136-146`），内容含 `run/n/ts/tool/drawing/state` 与会话标签（`sessions.tag`，`sessions.py:45-55`）；`engine` 每步调 `progress.step`（`engine.py:61`），`_validate_bytes` 前调 `progress.checking`（`service.py:622`），结束时 `finish` 写 `done`/`error`（`progress.py:156-187`，错误消息经 `scrub` 去绝对路径，`46-55`）。
3. viewer 侧 `ChangeFeed` 构造时只读日志尾部 64 KB 作为基线，丢掉首条半行（`viewer.py:663`、`677`、`686`）。
4. 后台线程每 0.3 s `poll`（`viewer.py:785-794`）：`_read_activity` 从 `_log_pos` 续读、只处理完整行、文件变小视为轮转重读（666-697），新事件进 `deque(maxlen=400)` 并 `_emit(..., "activity", ...)`（697）。
5. 同一轮 `poll` 用 `signature`（`path`/`journal`/`head` 的 `(mtime_ns,size)`，`244-254`）产生 `added`/`changed`/`removed`；3D 文件沉降 `SETTLE=1.0 s` 后 `record_save` 记一个版本（`viewer.py:746-770`、`494-529`）。
6. 页面取数：`GET /api/activity` 回 `feed.activity`（`viewer.py:860-861`）；`GET /api/events?progress=1` 走 SSE，`Last-Event-ID`/`since` 作为游标，先发 `retry: 1000`，每 15 s 一条 `: keep-alive`（871-873、`997-1016`）。
7. `viewer_url` 的来源：`service._viewer_link`（`251-275`）先验自己启动的实例仍服务同一 root，再读 `viewer.json`，必要时 `start_background`（`viewer.py:1117-1144`，端口 8770-8779 顺序试探，`AGENTCAD_VIEWER_PORT` 可指定）。

**分支/失败点**：进度写失败被吞（`progress.py:145-146`）；viewer 起不来只让 `viewer_url` 缺失，不影响已提交的结果（`service.py:274-275`、`viewer.py:1131-1132`）；`serves_root` 校验失败则不对外给链接（`viewer_link.py:20-39`、`viewer.py:1133-1135`）。

**副作用**：`activity.jsonl`(+`.1`)、`.agentcad/viewer.json`、`viewer-cache/`（渲染缓存与 3D 版本副本）；**不写任何 DXF**。

### H. CLI 命令分发（`cli.main` → `Runtime`）

**触发点**：`agentcad <cmd> …`（`cli.py:398-407`）。

1. `main` 从 `--compact` 决定缩进，重配 stdout 为 UTF-8（399-403），调 `run`（404）。
2. `run` 解析参数（239）；`setup`/`doctor` 走独立分支并自行打印（240-241 → `203-235`）；`demo` 同理（242-257）。
3. `mcp` 且未给 `--root` 时改用 `ensure_workspace()`，避免用客户端启动目录当工作区（258-260）。
4. 构造 `Policy`（261），`--read-only`/`--allow-scripts` 直接改字段（262-265）。
5. 不经过 `Runtime` 的四个命令：`mcp`（266-269 → `serve_stdio`）、`serve`（270-277）、`viewer`（278-295）、`mcp-config`（296-306）。
6. 其余命令在此处才 `from .service import Runtime`（307-308）——因此 `create/inspect` 之外的命令不会为加载 ezdxf 付代价；`Runtime(policy)` 不带 `auto_viewer`，CLI 不会顺手起 viewer。
7. `if/elif` 表把命令映射到 `Runtime` 方法（310-388），全部按位置传参；`validate` 还会解析 `--bounds`、`--contour-layers`（339-346），`apply` 允许 `--ops` 传 `{"operations": [...]}`（334-335），`constraints` 允许传 `{"constraints": [...]}`（353-354）。
8. 退出码：`EXIT` 把错误类别映射为 1/2/3/4/5/6（16、389-390）；`validate` 未通过或 `acad-audit` 失败返回 1（391-392）。
9. `main` 只在结果不是 `{"ok": True}` 时打印信封（405-406），然后 `sys.exit(code)`（407）。

**分支/失败点**：`_die` 输出 `CLI_USAGE` 并退出 2（`cli.py:33-35`）；JSON 参数/文件读不动同样走 `_die`（19-30）；端口占用转 `PORT_IN_USE`（292-294）。

**副作用**：设计上只有被调 `Runtime` 方法的副作用（见 A/B/E/F）；`viewer --open` 会启动或复用一个 viewer 并打开浏览器（278-286 → `viewer_link.py:57-66`）。

**扩展点**
- 新增 MCP 工具：在 `Runtime` 上写 `@tool` 方法（自动登记 `TOOLS`，`service.py:105`），再在 `build_server` 里声明同签名的 `@mcp.tool`（`mcp_server.py:114-434`）；串行化由 `add_tool` 自动接管，不要自己起线程。
- 让新工具被 viewer/进度/AutoCAD 跟随：改 `LIVE_SYNC_TOOLS`/`VIEWER_TOOLS`/`PROGRESS_TOOLS` 三张表（`service.py:42-51`），进度文案在 `web/viewer` 构建产物里，runtime 只写数据（`progress.py:9-15`）。
- 新增导出格式：`service.py:967-968` 的后缀表 + 一个分支 + `cli.py:128-134`/`mcp_server.py:338-356` 的枚举；新增纸张或比例策略改 `plot._sheet`/`STANDARD_SCALES`（`plot.py:183-202`、`222-223`）。
- 新增规格类型：`spec.compile_spec`（`spec.py:257-270`）加分支，返回 `{units, operations, requirements}` 即可复用 B 的整条链路。
- 新增策略开关：`Policy` 字段（`policy.py:13-32`）+ `check_*` 方法 + CLI 全局 flag（`cli.py:44-46`）；不要把手写数值散在调用点。
- 新增状态文件/目录：只改 `DrawingStore._set_dir`（`store.py:156-161`），否则迁移逻辑（`213-252`）与旧目录兼容会不一致。

**风险与可疑之处**
- [高] `run_script` 全程持有该图纸的写锁（`service.py:574` 起，`578` 在锁内跑子进程），上限是 `script_timeout` 默认 60 s（`policy.py:27`）；叠加 `CADWorker` 的单线程（`worker.py:10`），一个脚本会把**同一进程内的所有工具调用**（含只读）一起堵住。影响：MCP 客户端的并行读请求会排队到脚本结束；60 s 的默认值让最坏等待远超仓库基准里 ~50 ms 的 `apply_operations`。建议：缩短默认超时或把子进程运行移到锁外、只在提交时短暂取锁，并在文档里写明这条耦合。
- [中] `_validate_bytes` 把候选字节写到**系统临时目录**再交给 `validate_file`（`service.py:621-627`），而 `validate_file` 自建 `Policy(root=path.parent)`（`validate/__init__.py:46-48`），因此校验用的是从环境变量默认值来的预算，而不是调用方 `Runtime.policy` 的预算。影响：把 `max_file_bytes` 调小的部署仍会校验大文件；校验读的也不是同一份路径策略。建议：让 `validate_file` 接受显式 `Policy` 或至少继承 limit。
- [中] `export_drawing` 的 `layout` 参数对 `svg` 静默无效（`service.py:1051-1065` 只画 `doc.modelspace()`），而 `pdf`/`png` 会解析它（`997-1027`）。影响：`format="svg", layout="A4-sheet"` 不报错、不提示，得到的是模型空间图。建议：svg 分支显式拒绝非 Model 的 `layout`，或在 journal/结果里标明实际绘制的布局。
- [中] `_cjk_styles` 就地改写内存文档的文字样式（`plot.py:243-254`），正确性依赖 `service.py:1004` 先算 `_plot_text_warnings` 再出图。影响：任何绕过服务层直接调 `plot.plot_pdf` 的代码（如 `delivery.py` 或后续新增导出路径）会拿到已替换的字体表，`TEXT_PLOTS_AS_BOXES` 检查随之失真。建议：把字体替换改为 `plot_fonts(doc)` 的显式入参，或在 `plot_pdf` 内部先取快照。
- [中] `CADWorker.close()`（`worker.py:18`）在 `src/` 内没有调用点（仅 `mcp_server.py:104` 构造），`serve_stdio`（`mcp_server.py:481-487`）也没有收尾钩子。影响：进程退出时要等线程池里最后一项跑完（Python 在解释器退出时 join 工作线程），一个卡住的脚本会让 stdio 会话迟迟不退出。建议：在 `serve_stdio` 的 `finally` 里 `worker.close()`，并给长任务设可取消的执行路径。
- [低] `_activity` 用 `try/except Exception` 整体吞掉失败（`service.py:248-249`），`PROGRESS_TOOLS` 里写错参数名或被改名的形参不会报错，只是页面从此没有进度。证据：`service.py:231-247` 依赖 `sig.bind_partial` 与 `bound.get(arg)`。建议：开发模式下把这类失败记一条 stderr 警告。
- [低] 默认 `rollback` 选“最新且与当前文件不同”的检查点（`service.py:864-866`），对**被外部改过**的文件同样成立，于是不加提示地丢掉外部修改；`modified_outside_runtime` 只在 `status()`/timeline 里出现（`store.py:577-578`、`viewer.py:305-307`）。建议：`rollback` 在 `status()["modified_outside_runtime"]` 为真时回一条 warning。

**测试对应**
- `tests/test_g_interfaces.py` — 四入口同一实现（`test_interfaces_share_one_implementation`）、CLI 端到端（`:28`）、官方 MCP 客户端（`:96`）、HTTP（`:182`）。
- `tests/test_t_workflow_guards.py` — `test_cancel_does_not_overlap_workers`（`:133`）覆盖 worker 串行与“取消不释放槽位”；同文件还覆盖 `preconditions` 指纹（`:163`）。
- `tests/test_p_busy_files.py` — `FILE_BUSY` 的三段时间语义（`:56`、`:70`、`:81`）、下一次提交先结算（`:116`）、rollback 走同一事务（`:178-222`）。
- `tests/test_f_repro_recovery.py` — 检查点哈希不符（`:104`）、迁移后仍可回滚（`:136`、`:153`）、批次失败后回滚（`:232`）、崩溃恢复（`:247`）。
- `tests/test_d_errors.py` — 失败批次不改文件且入 journal（`:102`）、错误类别可区分（`:90`）。
- `tests/test_a_spec_to_drawing.py` / `tests/test_b_revision.py` — B 的端到端与独立验证（`:15`、`:45`）、修订可逆与 `rollback`（`:63`）。
- `tests/test_e_tamper.py` — 验证器识破移动孔、改外框、删标注、伪造文字（`:27-75`）。
- `tests/test_j_compact_results.py` — 默认紧凑/按需全量的信封（`:17`、`:43`）。
- `tests/test_k_pdf.py` — PDF 页面与比例、CJK 字体、打印文本过小、PNG（`:18`、`:53`、`:121`）。
- `tests/test_layouts_and_scripts.py` — 脚本默认关闭与事务性（`:112`、`:118`）、静态检查（`:169`）、超时不落盘（`:192`）。
- `tests/test_q_viewer_progress.py` — 进度四态、脱敏、MCP 首个变更起 viewer（`:50`、`:69`、`:157`）；`tests/test_o_viewer.py` — 变更流、SSE、版本映射（`:65`、`:146`、`:208`）。
- `tests/test_security.py` — 路径穿越、只读、操作白名单、预算、并发写锁（`:11`、`:29`、`:40`、`:50`、`:62`）。

## 3.1 事务骨架：service / engine / store / locking / policy / guards / errors

**规模**：8 个文件 / 2669 行（service.py 1590、store.py 579、engine.py 204、policy.py 94、errors.py 87、guards.py 62、locking.py 51、__init__.py 2）

**职责与边界**
这一层是 AgentCAD 的事务内核：`Runtime` 把「一次工具调用」变成「加锁 → 读盘 → 乐观复检 → 内存执行 → 序列化 → 落盘 → 记账 → 独立复验 → 信封」这条唯一路径，并持有全部写锁、journal、检查点、错误分类与预算判定。它不解释 DXF 语义（操作定义在 `ops/*`）、不做验证规则（`validate/*` 只读文件）、不渲染、不解析适配层参数：CLI 是手写 if/elif 调用绑定方法（cli.py:307 起），MCP 用手写类型化包装加 `mcp.add_tool(getattr(rt, name))` 批量注册（mcp_server.py:432-434），HTTP 按 `TOOLS` 名派发（http_api.py:110-133），三者消费同一个 `@tool` 信封。向下它调 engine（纯内存事务）、store（字节持久化）、guards（修订号/实体指纹前置条件）、policy（沙箱与预算）、locking（OS 写锁）、validate（提交后独立复验）。关键切分是：engine 不碰磁盘、store 不碰 ezdxf 语义（只用 `readfile`/`write`/`bbox`/`TagCollector`）、policy 不碰文件内容——任何越界都会让「失败时磁盘不变」这条承诺失效。

**文件清单**
| 文件 | 行数 | 角色 | 职责 |
|---|---|---|---|
| service.py | 1590 | 核心 | `@tool` 信封与指标、`_lock` 重入、apply_operations/run_script/recover/rollback/export 的事务编排、viewer 与 AutoCAD 联动、验证报告压缩 |
| store.py | 579 | 核心 | head/journal/checkpoints 目录布局、`replace_file` 重试、`_replace_tracked` 提交协议、pending 结算、检查点恢复、legacy 状态迁移 |
| engine.py | 204 | 核心 | 批量预校验（预算/白名单/coerce）、顺序内存执行、`$i` 引用、spec 协调模式（就地更新/几何未变/删除陈旧标签） |
| policy.py | 94 | 支撑 | 根目录沙箱、`.agentcad` 保留、后缀白名单、只读、op 白名单、各类预算、环境变量默认值 |
| guards.py | 62 | 支撑 | 实体指纹（dxftags + attribs 的 sha256）与 `expected_revision`/`preconditions` 的 STALE_REVISION 判定 |
| locking.py | 51 | 支撑 | `write.lock` 上的非阻塞 OS 字节锁（msvcrt/flock），legacy `lock` 标记一律不抢占 |
| errors.py | 87 | 工具 | 8 个封闭错误类别、`AgentCADError` 字段与 `to_dict` 稳定契约、5 个类别工厂 |
| __init__.py | 2 | 数据 | 仅 `__version__`，被 store.VERSIONS 写进每条 journal 记录（store.py:38） |

**关键入口**
- `tool(fn)`（service.py:67-107）— 信封装饰器：`{"ok", "tool", **payload}`，异常转 `error`，末尾补 `metrics`，并把**未包装的原函数**注册进 `TOOLS`（105）且打 `__agentcad_tool__`（106）。
- `Runtime._lock(store)`（service.py:201-213）— 写锁生命周期；同一路径在当前调用链内复用外层 store（203-207），否则 `store.lock(migrate=not read_only)`。
- `Runtime.apply_operations`（service.py:434-489）— 全仓库的事务样板，顺序见调用链 1。
- `Runtime._validate_bytes`（service.py:619-634）— 把待提交字节写成临时文件交给 `validate_file`，与本进程内的对象无关；OSError 降级为 `VALIDATION_SNAPSHOT_ERROR` 报告而非抛出。
- `Runtime._save_failure`（service.py:215-223）— 失败现场写 `<state>/failed/`，吞掉所有异常返回 None。
- `Runtime._activity`（service.py:228-249）— 只对 `PROGRESS_TOOLS` 里登记的、带字符串路径参数且非 dry_run、非同图嵌套的调用建进度对象；解析失败一律返回 `(None, None)`。
- `Runtime._viewer_link`（service.py:251-275）/ `_live_sync`（282-299）— 提交之后的旁路动作：viewer 端口探活与后台拉起、AutoCAD 重载；两者都吞异常，不得让已提交的改动失败。
- `engine.prevalidate`（engine.py:31-47）/ `engine.execute`（engine.py:50-100）— 预校验只读 `REGISTRY`/`coerce`/`Policy`；execute 保证「先全量校验、后顺序执行、失败整体丢弃」。
- `engine._reconcile_op`（engine.py:109-138）/ `update_in_place`（165-196）— 协调模式的实体级语义：单实体就地改、几何相同丢弃新实体、类型不同则删除重建、多实体操作整体替换。
- `DrawingStore.commit_bytes`（store.py:396-406）→ `_replace_tracked`（408-468）— 唯一的替换协议实现，`commit`（384-394）与 `restore`（536-561）都走它。
- `DrawingStore.checkpoint`（store.py:350-367）/ `journal`（324-337）/ `settle_pending`（496-511）/ `finish_pending`（513-534）/ `status`（563-579）。
- `DrawingStore.head`（255-258）/ `_write_head`（260-264）— head 的缺省结构与「写临时文件再 `replace_file`」的原子写法；缺省 `{"seq":0,"sha256":null,"pending":null,"checkpoints":[]}` 就是全仓库对 head 的最小契约。
- `DrawingStore._legacy_head`（163-211）— 旧目录的归属判定器：只有日志里每个 `drawing` 都指向本图（按 normcase、大小写别名、同 inode 与同卷逐级比对）且 head 的 sha/检查点都能被日志解释时才返回，否则放弃迁移。
- `DrawingStore.lock`（store.py:213-252）— legacy 迁移的临界区；`migrate=False`（只读模式）时只用旧目录不搬迁（219-223）。
- `store.replace_file`（store.py:67-81）/ `busy_error`（56-64）/ `read_dxf`（84-108）/ `dxf_bytes`（111-115）/ `finalize`（118-133）。
- `store._journal_tail`（266-296）/ `journal_page`（303-309）— 从文件尾部按块倒读、按字节偏移分页；游标必须是记录边界（274-279），viewer 的时间线用这条入口（viewer.py:289）。
- `guards.check`（guards.py:22-62）与 `guards.fingerprint`（14-19）。
- `policy.Policy.resolve`（policy.py:38-63）/ `check_write`（65-67）/ `check_op_allowed`（81-84）/ `state_dir`（86-87）/ `_env_set`（90-94）。
- `locking.drawing_lock`（15-40）/ `refuse_legacy_marker`（43-51）。
- `AgentCADError.to_dict`（errors.py:50-67）— CLI/MCP/HTTP 共同消费的错误形状；`compact_report`/`_brief`（service.py:1533-1568）是成功信封的收缩器，`MAX_ISSUES=50`（1514）。
- `diff_docs`（service.py:1571-1590）/ `_journal_line`（1489-1511）— 前者按 handle 跨全部布局比对并给出逐字段 old/new，run_script、`autocad_live pull`、`get_diff` 共用；后者决定 history 概览显示哪些字段。

**数据结构与不变量**
- 信封键域固定：`ok`/`tool` 先写、payload 随后展开（service.py:81），因此工具函数返回同名键会覆盖信封；`metrics` 最后写（100）并追加进 `Runtime.metrics`（102）；`live`/`viewer_url` 只在成功且非 dry_run 时补（93-99）。
- 写事务独占：同一图纸同时只有一个写者；同调用链靠 `ContextVar _held_store` 复用（189、203-207）；锁由 OS 在进程退出时释放，不按时间或 PID 判活（locking.py:1、19-20、43-51）。
- 修订号单调：只在 `_replace_tracked` 替换完成后 +1（store.py:421、458），`restore`（rollback/recover）同样 +1（559），所以 revision 计的是提交次数而非内容版本；head 缺省为 0（258）。
- pending 先行：目标 sha 与 revision 在替换**之前**写入 head（store.py:422-428）；结算只用字节比对——文件 sha 等于目标 sha 判 `completed`，等于旧 head sha 判 `discarded`，都不等则返回 None 交给检查点回退（513-534）。
- 「已提交」与「已记录」分离：替换成功后 committed 副本忙只产生 `warnings`（store.py:447-454），head 忙则是 `applied_not_recorded` + `partial_changes=true` + `retryable=false`，要求调 `recover`（56-64、459-466）。
- 检查点先写 head 再删文件：`checkpoint` 在 head 写失败时删掉刚拷的副本并重抛（350-367），`_abandon` 先改 head 成功后才 unlink（470-494），保证「head 列出的检查点必有对应文件」。
- journal seq 取 `max(head.seq, 文件末尾 seq)+1`（327），先 append 再写 head，head 写失败不丢记录（331-337）；记录自带 `drawing`/`versions`/`client`/`conversation`（329-330），时间戳是 UTC 毫秒（49-50）。
- journal 有三种记录形态：成功（`ok:true` + `sha256`/`checkpoint_before`/`changes`）、失败（`ok:false` + `error` + `file_unchanged`）、`recover`（结算中断提交，可带 `completed_commit`/`completed_seq`，496-511）。
- engine 的失败语义：任何 op 抛出 AgentCADError 时补 `op_index`/`op` 并置 `partial_changes=false` 后重抛（engine.py:75-79），库异常转 `INTERNAL_ERROR` 带 6 层 traceback（80-82）；内存 doc 由调用方丢弃，磁盘从未被触碰。
- engine 的两级预算：批操作数在 `prevalidate` 一次性判定（engine.py:34-35），实体总数在执行末尾、任何提交之前判定（96-98）。
- guards 的前置条件：`expected_revision` 必须是非负 int（guards.py:23-24），`preconditions` 只接受非空的 `sha256`/`entities` 组合（26-35）；指纹跨保存/重载稳定，因为两侧都从文件重读。
- 沙箱：`resolve` 后必须 `is_relative_to(root)`，任一路径分量 casefold 为 `.agentcad` 即拒绝，`suffixes` 为白名单，`must_exist` 决定 FILE_NOT_FOUND（policy.py:52-62）。
- 状态目录规则：相对路径不含 `__` 的图沿用平铺 `drawings/<a__b>`，含 `__` 的图走 `drawings-v2/<父目录>/.agentcad/<图名>`，只有当 v2 不存在且旧 head 能被日志证明归属时才回落到旧目录（store.py:142-154、163-211）。
- 错误类别是 8 元封闭集合（errors.py:19），`to_dict` 省略空的可选键（59-66）；`retryable` 只在锁冲突、`FILE_BUSY`、`FILE_READ_ERROR`、AutoCAD 未运行处为真。
- dry_run 不解锁一条捷径：仍在锁内读盘、仍跑完整 `engine.execute`，只是跳过序列化与提交，并回传 `preview.preconditions` 的全量指纹快照（service.py:464-467），调用方可原样回传；`create` 与 dry_run 同时给出且图不存在时直接拒绝（506-508）。
- 旁路动作受开关与成功态共同约束：`live`/`viewer_url` 只在 `ok` 且非 dry_run 时补，`live` 还要求 `policy.live_sync`（service.py:93-99）；进度上报在只读模式下完全不建（232-233）。
- head 是唯一权威：revision/sha/pending/checkpoints 四件事同写一份文件，任何一次 `_write_head` 失败都只能通过 `recover` 或下一次提交的 `settle_pending` 收敛，不存在「另一份索引」可以对账。
- 只读模式连状态目录都不动：`check_write` 在 create/apply/run_script/rollback/recover/export/import 前逐一调用（service.py:349、445、570、858、907、964、1077、1198），并且 `store.lock(migrate=False)` 时不搬迁 legacy 目录（store.py:216-223）。
- 迁移是一次性换名而非就地写：先把 head/journal/committed 与 head 声明的检查点复制进 `.migrate-` 临时目录（store.py:225-244），再对整目录 `replace_file(staged, self._state)`（245），失败留下的是旧目录而不是半个新目录。

**主要调用链**
1. 入口到信封：适配层调用绑定方法 → wrapper 计时并 `json.dumps` 入参算 `input_bytes`（service.py:74-76）→ `_activity` 建进度（77、228-249，同图嵌套调用默认不重复上报）→ `progress.begin`。
2. 前置：`policy.check_write`（445）→ `_drawing`（450，`policy.resolve(..., suffixes=(".dxf",))`）→ `_lock`（451）。
3. 读盘与乐观复检：`sha256_file(p)`（452）→ `store.load()`（453）→ `guards.check(store, doc, expected_revision, preconditions)`（454）→ 再比一次 sha 捕获「读盘期间被换」（455-456）。
4. 执行：`engine.execute(doc, operations, unit, policy)`（459）→ `prevalidate`（engine.py:52 → 34 批量预算、41 已知 op、42 近似名提示、45 op 白名单、46 coerce）→ 逐 op（57-88：66-67 切布局、69-72 协调或直调、75-82 补错误上下文、87 记 `$i` 引用）→ 末尾实体预算（96-98）。
5. 失败即记账：engine 抛错 → 写一条 `ok:false`/`file_unchanged:true` 的 journal 再重抛（service.py:460-463），磁盘未改。
6. 序列化：`dxf_bytes(doc)`（468 → store.py:111-115，写前 `finalize` 补 `$EXTMIN/$EXTMAX`，避免导入器按 1e20 视口缩成点）。
7. 严格模式：`_validate_bytes` 建临时文件独立验证（469、619-634），不通过则把现场写进 `failed/` 并抛 `VALIDATION_FAILED`（470-477）；提交前始终再跑一次 `guards.check`（480，只带 sha256）拦住计算期间的外部改动。
8. 提交：`commit_bytes`（481）→ `_replace_tracked`：`settle_pending`（415-418）→ revision+1（421）→ 建检查点（424）→ 写 pending（426-428）→ 临时文件 fsync 后 `replace_file`（432-439）→ 刷新 committed 副本（447-450）→ 清 pending 写 head（455-466）。
9. 记账与复验：`store.journal({**attempt, "ok": True, **info})`（482）→ 解锁后按同一 `data` 重验（483-484）→ 组装 payload（485-489）→ wrapper 补 metrics（100-102）。
10. 崩溃恢复：`recover`（892-938）先看 legacy 标记（900-902）→ `status`（903）→ 需要时在锁内 `settle_pending`（908-917）→ 否则按 `committed.dxf` + 检查点倒序挑第一个哈希匹配且可读者（918-927）→ 现文件存入 `failed/`（929-932）→ `restore`（933）→ journal（934）。
11. 回滚：默认取最后一个哈希不同于当前文件的检查点，跳过遗留的「未变更副本」（853-866）→ `restore` 内部复核检查点哈希（543-548）→ 走同一条 `_replace_tracked`（559）→ journal（869）。
12. 多图批处理：`apply_batch`（512-560）先整体校验 job 形状、路径与去重（524-537），再逐个走完整事务，单 job 失败只让该 job 不动（539-556），`create` 造出的空图即便后续失败也保留。
13. 约束强制：`apply_constraints(mode="enforce")`（674-712）**绕过 engine**，在锁内重读、逐个 `enforce_one` 顺序改图（688-699），再把全部约束复检一遍，不满足就抛 `CONSTRAINT_CONFLICT` 且不提交（700-706），满足才走 `store.commit`（707）——所以这条路径没有 `coerce`、没有逐 op 的结果结构，只有 `touched` 列表。
14. 脚本编辑：`run_script`（562-617）依次 `check_scripts_allowed`（569）→ 静态检查 `scripting.check_script`（572）→ 锁内起独立进程对副本执行（576-578）→ 严格重读 `out.dxf`（583）→ 全布局实体预算（590-593）→ `diff_docs` 出变更（594）→ 可选 strict 验证（602-610）→ `commit_bytes`（613）→ journal 记完整脚本 + `code_sha256`（611-614）→ 解锁后独立复验（615-616）。
15. 严格失败：`_save_failure` 落盘现场（471）→ 写 `ok:false` + `failure_snapshot` 的 journal（472-473）→ 抛 `VALIDATION_FAILED` 携带压缩后报告（474-477），此时检查点尚未创建、图纸文件未被替换。

**扩展点**
- 新增工具：在 `Runtime` 上加 `@tool` 方法（范例 service.py:346-360）；同时改 cli.py 的 if/elif 分支、mcp_server.py 的类型化包装或把它加入 432-433 的 `add_tool` 列表；要进度条就登记 `PROGRESS_TOOLS`（48-51）与绘图参数名，要 viewer 链接/自动重载就加入 `VIEWER_TOOLS`（46）/`LIVE_SYNC_TOOLS`（42-43）。
- 新增策略开关：`Policy` 加 dataclass 字段 + 环境变量默认值（policy.py:15-32）与一个 `check_*` 方法（65-84），调用点放在 `engine.prevalidate`（31-47）或 `commit_bytes` 入口（396-400），不要散在工具里。
- 新增错误类别：`CATEGORIES`（errors.py:19）+ 工厂（70-87）+ `cli.EXIT`（cli.py:16）三处必须同步，否则类别通过 assert 但退出码落到默认值。
- 新的状态文件/持久化格式：改 `_set_dir`（store.py:156-161），并同步 `_legacy_head` 的迁移判据（163-211）、模块文档串（1-11）与 `status`（563-579）。
- 新的提交后步骤（通知、索引、回读）：包一层 `_replace_tracked` 或在 `commit_bytes` 之后加，不要在各工具里复制序列；`_save_failure` 可复用于失败现场。
- 新的失败恢复分支：`finish_pending` 的三种结局（store.py:522-531）是唯一判定点，`recover`（892-938）只负责选源，新分支应加在判定处而非工具里。
- 新增 guard 维度（例如图层集合或 DXF 版本）：改 `guards.check`（22-62）并同步 `inspect_entities` 暴露给 agent 的字段（service.py:429-430、`preview.preconditions` 464-467），否则 agent 拿不到可回传的前置条件。
- 新增 journal 记录类型：`_journal_line`（service.py:1489-1511）决定 history 概览显示什么，不改它新记录在历史里只剩 seq/ts；跨版本读取还要照顾 `_journal_tail` 的逐块解析（store.py:266-296）。
- 新增预算/阈值：优先加在 `Policy`（policy.py:15-32）并在 `prevalidate`（engine.py:31-47）或 ops 的 `ctx.policy` 使用点上判定，别在工具层复制 `if n > limit`；现有 `delete_confirm_threshold`/`max_new_entities_per_batch` 就是由 ops 自己消费的（ops/edit.py:324-334、ops/analysis.py:109）。

**风险与可疑之处**
- [中] 实体预算只数模型空间 — engine.py:96-98 用 `len(doc.modelspace())`，而 run_script 路径数全部布局（service.py:590-593）；`max_new_entities_per_batch` 只在 ops/edit.py:324 一处检查。影响：图纸空间可把单图实体推到远超 `max_entities`，唯一兜底是 `commit_bytes` 的 256 MB 文件上限（store.py:400-401）。建议：execute 改用 `layout_spaces(doc, "*")` 计数。
- [中] `guards.check(doc=None)` 只能配 sha256 — service.py:480 传 `None` 且只带 sha256 所以不触发，但 guards.py:38-40 在 `wanted` 非空时会遍历 `doc.layouts`；任何新增的「第二次复检带 entities」都会变成 AttributeError → INTERNAL_ERROR。建议：`doc is None and wanted` 时抛 BAD_PARAMETER。
- [中] 失败路径的 journal 写入无保护 — service.py:461-463、472-473、585-587 都是先 `store.journal(...)` 再 `raise`，而 journal 的 `open(..., "a")` 与 head 写入只吞 PermissionError（store.py:331-336）。影响：磁盘满或 ACL 变化时，精确的几何错误被 OSError 覆盖成 INTERNAL_ERROR。建议：记账失败降级为 warning 并保留原错误。
- [中] 策略环境变量解析发生在信封之外 — policy.py:18-19、27 的 `int()/float()` 在 `Policy()` 构造时抛 ValueError，而 Runtime 在 cli.py:307-308 构造，早于任何 `@tool`。影响：`AGENTCAD_MAX_ENTITIES=abc` 直接 traceback，拿不到结构化错误。建议：解析失败转 `policy_error` 并点名变量。
- [中] 同进程不可重叠锁却报「另一个进程」 — locking.py:31-32 的文案与 `_lock` 只按 path 复用（service.py:203-207）叠加：`test_z_locking.py:49-54` 证明同进程两个句柄即冲突。影响：将来任何跨图嵌套编辑（A 的锁内调 B）会被自己的锁拒绝，且错误信息指向错误的对象。建议：加进程内持有者注册表并区分文案。
- [中] dry_run 与实体级 preconditions 的代价是 O(全图) — guards.py:38-40 在 `preview` 或 `wanted` 非空时会遍历全部布局的全部实体（过滤只在 `wanted` 内），`preview` 还对每个实体做 dxftags 序列化求指纹；test_t_workflow_guards.py:66 专门为 strict 路径规避全量指纹，说明这是已知热点。影响：大图上一次预览即一次全量 tag 遍历。建议：预览按调用方点名的 handle 抽样，或走分页。
- [低] 未走到替换的提交会留下一个会被结算的 pending — 写完 pending 后替换失败（store.py:429-431）、`_abandon` 又撞上 head 占用时直接 return（489-492），下一次提交的 `settle_pending` 会写一条 `outcome: "discarded"` 的 recover 记录（496-511）。影响：history 里出现一条没有对应变更的 recover；test_p_busy_files.py:273-297 只断言重试成功，未断言这条记录。建议：pending 区分「尚未尝试替换」。
- [低] checkpoint id 是位置编号、可被重用 — store.py:356-357 用 `len(checkpoints)+1`，`_abandon` 会 pop 掉最后一项（486），之后同一 id 指向另一份字节，而 journal 的 `checkpoint_before` 以 id 记录（422、505）。建议：改为单调计数器。
- [低] engine 直接读写 `Ctx` 的私有缓存 — `ctx._tags = None`（engine.py:121、127）是协调模式的标签缓存失效开关；改名或换实现时不会报错，只会让 reconcile 读到陈旧标签、误判「标签已存在」。建议：给 Ctx 提供公开的 `invalidate_tags()`。
- [低] 读路径不加锁，信封内不同字段可能来自不同版本 — `read_drawing`（service.py:381-383）先 `load()` 再 `status()`（407），后者重新读 head 并重新哈希文件（store.py:563-579）。影响：正好撞上别的 writer 时，`entities`/`metadata` 与 `status.committed_sha256`/`revision` 可以描述两个版本。建议：读类工具一次性取 `(head, sha, doc)` 快照。
- [低] 信封二次序列化 — service.py:76（入参）与 101（整个 payload）各 `json.dumps` 一次；`list_operations(detail=true)` 与大图纸 `read_drawing` 的代价随载荷线性增长。建议：复用序列化结果或按需算 `output_bytes`。
- [低] 错误工厂与断言不一致 — errors.py:38 的 `assert category in CATEGORIES` 在 `python -O` 下被剥离；8 个类别只有 5 个工厂（70-87），`validation`/`internal`/`dependency` 直接构造 `AgentCADError`（service.py:86、474；engine.py:81、195）。建议：补齐工厂，类别改成显式抛错。
- [低] 状态目录与图纸路径绑定，改名即失联 — store.py:142-154 从 workspace 相对路径派生状态目录，仓库里没有重绑定工具。影响：移动或重命名 DXF 后 head/journal/checkpoints 成为孤儿，`rollback`/`history` 对新路径从零开始。
- [低] `Runtime.metrics` 无上限增长 — service.py:102 每次调用追加一条，`get_metrics` 只回放最后 200 条（1394）。影响：长驻 MCP 进程内存随调用数线性增长。建议：改用环形缓冲。
- [低] `store.status()` 每次都重哈希并重解析 — store.py:563-579 被 `read_drawing`（407）与 `recover`（903）调用；大图上 `read_drawing` 的代价约等于两次解析加一次全文件哈希。建议：读类工具复用已加载 doc 的可读性结论。

**测试对应**
- tests/test_p_busy_files.py — `_replace_tracked` 的全部分支：committed 副本忙仍提交（43）、图纸忙 retryable 且文件不变（56）、head 在替换前/后忙的两种错误形状（70、81）、applied_not_recorded 由下一次提交或 `recover` 结算（116、141）、journal 撞 head 忙仍保留 seq（152）、rollback 的忙路径（178-201）、检查点与 head 一致（273）、settle 被阻塞时保留标记（299）、recover 记录携带 operations 与 completed_seq（332-346）。
- tests/test_z_locking.py — 存活进程的旧锁不可被时间抢占且崩溃自动释放（12）、同进程两个句柄不可重叠（49）、legacy `lock` 标记不被删除也不被读取（59、69、88）。
- tests/test_f_repro_recovery.py — journal 从尾部读跨块 UTF-8 记录（67）、legacy 迁移的四种归属判定（94、136、159、195、217）、检查点哈希不符时拒绝 rollback（104）、崩溃恢复到最近有效检查点（247）、批内失败后 rollback（232）。
- tests/test_t_workflow_guards.py — `expected_revision`/preconditions 的 STALE_REVISION（19、32、38）、strict 只验证一次且不对全部实体算指纹（66）、验证快照不可用时保留提交结果（88）、block 属性指纹拒绝陈旧编辑（163、189）。
- tests/test_d_errors.py — 错误类别可区分（90）、失败批次文件不变且进 journal（102）。
- tests/test_security.py — `PATH_OUTSIDE_WORKSPACE`/`PATH_RESERVED`（14-17）、`BUDGET_EXCEEDED`（54-58）、`DRAWING_LOCKED` 且 retryable（67）。
- tests/test_e_tamper.py::test_external_modification_is_flagged（75）— `status.modified_outside_runtime` 对运行期外改动的判定。
- tests/test_g_interfaces.py — CLI/MCP/HTTP 共用一套实现与错误码映射（49 起）。
- tests/test_j_compact_results.py — 信封收缩：validate 默认压缩（17）、失败 requirement 与警告不被裁掉（34）、strict 失败也用压缩报告（43）、history 概览（65）。
- tests/test_l_fewer_turns.py — `apply(create=...)`（17、28）、信封里的 validation 与 `validate_drawing` 等价（35）、`apply_batch` 的家族绘制/失败隔离/运行前预校验/200 job 上限（57-89）。
- tests/test_o_viewer.py — 提交到版本的映射（65）、时间线分页在重复 seq 下仍有序（83）、`replace_file` 等待读者（281）、读者存在时仍能提交（302）。

## 3.2 操作层：ops/*、model、units、geom、constraints

**规模**：14 个文件 / 4,510 行（`ops/` 10 文件 3,531 行，注册 71 个操作；`model`/`units`/`geom`/`constraints` 4 文件 979 行）

**职责与边界**

这一层是“agent 能做的全部事情”的唯一清单：`ops/registry.py` 用一个 `@op` 装饰器把“参数声明、参数校验、JSON Schema、执行函数”绑成一份定义，其余 8 个 `ops/*.py` 只写具体几何。它不做事务、不做锁、不碰磁盘——`engine.execute` 拿一批已经 `coerce` 过的参数字典，按注册表名字找函数、在内存 `Drawing` 上调用，失败就整批丢弃（engine.py:52-82）；持久化、检查点、journal、独立验证都在 `service`/`store`/`validate`。`model.py` 是这一层的数据契约：实体 → 字典、XDATA 标签、布局归属、选择器解析；`units.py` 只负责“长度字符串 ↔ 图纸单位”；`geom.py` 是不依赖 ezdxf 的 2D 图元内核（线段/圆/交点/偏移）；`constraints.py` 是独立于操作注册表的第二条能力线，由 `service.apply_constraints` 直接调用（service.py:674）。调用方向单向：ops/model/units/geom 只依赖 `errors`，`constraints` 额外依赖 `model`+`geom`，本层不导入 `engine`/`service`/`store`。

**文件清单**

| 文件 | 行数 | 角色 | 职责 |
|---|---:|---|---|
| ops/__init__.py | 5 | 入口 | 导入 8 个模块即完成注册；再导出 `REGISTRY/Ctx/catalog/coerce` |
| ops/registry.py | 352 | 核心 | `Param`/`OpDef`/`@op`/`Ctx`/`coerce`/`catalog`，公共参数与选择器校验 |
| ops/draw.py | 413 | 核心 | 15 个操作：线/多段线/多边形/圆环/构造线/填充/矩形/圆/弧/椭圆/样条/点/单行与多行文字 |
| ops/edit.py | 1,076 | 核心 | 21 个操作：move/copy/rotate/scale/mirror/array/delete/modify/stretch/offset/trim/extend/fillet/chamfer/explode/break/join/draw_order/purge，另有变换与尺寸重渲染基础设施 |
| ops/annotate.py | 501 | 核心 | 11 个操作：尺寸、引线、表格、文字样式、中心标记、出图比例、标题栏；`render_dimension`/`store_measurement` 是全仓共用设施 |
| ops/library.py | 673 | 支撑 | 7 个行业库操作：P&ID 符号与管线、GD&T 三件套、标准孔、标准件；几何数据来自 `stdlib.py` |
| ops/sheet.py | 143 | 支撑 | 4 个布局操作：图纸设置、CHSPACE、建布局、开视口 |
| ops/structure.py | 191 | 支撑 | 7 个图层/块操作，含 `define_block` 的嵌套操作执行 |
| ops/analysis.py | 114 | 支撑 | 3 个派生/清理操作：`trace_boundary`（计入 draw）、`edit_hatch`、`remove_duplicates` |
| ops/engineering.py | 63 | 适配 | 3 个薄壳，参数 `spec` 原样转交 `engineering/{pid,architecture,mechanical}.draw` |
| model.py | 378 | 核心 | 实体序列化、XDATA 标签与 `tag_index`、OCS 反转归一、布局归属、`select` 选择器、尺寸实测 |
| units.py | 77 | 工具 | `UNITS` 表、别名、`parse_length`、`convert`、`$INSUNITS` ↔ 单位名 |
| geom.py | 232 | 工具 | `Seg`/`Circ`、线线/线圆/圆圆交点、角度区间、多边形面积与内外判定、斜接偏移 |
| constraints.py | 292 | 支撑 | 15 种可检查约束 + 12 种可强制约束、点引用解析、顺序直接修改 |

按类别的注册分布（`category` 与文件名无关，由装饰器决定）：edit 23、draw 14、annotate 13、library 7、layout 4、block 4、layer 3、engineering 3。其中 37 个操作带 `common_draw`（额外接受 layer/color/linetype/lineweight/tag/if_tag_exists），71 个全部接受 `layout`。

**关键入口**

- `Param`（registry.py:21）与 `Param.json_schema`（registry.py:32）——15 种类型；`length`/`dim` 输出 `["number","string"]`，`selector` 只给描述不给 `type`，所以 JSON Schema 校验器拦不住选择器，真正的校验在 `coerce`。
- `OpDef.schema`（registry.py:85）/ `all_params`（registry.py:92）——合并 `params` + `COMMON_DRAW`（registry.py:106）+ `COMMON_ALL`（registry.py:100）后生成带 `required`、`additionalProperties:false` 的 schema。
- `op(name, category, description, params, *, common_draw=False)`（registry.py:119）——往 `REGISTRY[name]` 写一条 `OpDef`，返回原函数（所以模块内仍可直接调用，如 library.py:571 调 `add_center_mark`）。
- `Ctx`（registry.py:128）——一次批次的执行上下文：`doc`、`msp/layout`、`unit`、`policy`、`refs`、`added/modified/deleted`、惰性标签索引 `_tags`。
- `Ctx.use_layout`（registry.py:145）与 `Ctx.space_of`（registry.py:155）——前者是“新实体去哪”，后者是“原实体在哪”；派生型操作必须用后者。
- `Ctx.attribs`（registry.py:192）/ `finish`（registry.py:211）/ `check_tag`（registry.py:232）/ `tags`（registry.py:186）——公共属性、落标签并记 created、`if_tag_exists` 三态（error/skip/replace）、批内标签索引。
- `coerce(opdef, raw, unit, index)`（registry.py:272）——唯一的参数规范化入口：拒绝未知参数、补默认值、折算长度、范围检查，并把 `op`/`op_index` 填进异常。
- `catalog()`（registry.py:351）——`service.list_operations`（service.py:328）与 `discovery.search`（discovery.py:47）的数据源。
- `edit.transform`（edit.py:125）/ `mirror_text`（edit.py:86）/ `normalize_extrusion`（edit.py:57）/ `rerender_dimension`（edit.py:25）——变换后的统一收尾：MIRRTEXT=0 的文字改对齐不改字形、OCS 归一、尺寸重渲染并重写 group 42。
- `edit.GEOM_FIELDS`（edit.py:362）与 `DIM_FIELDS`（edit.py:369）——`modify` 的可编辑字段白名单，未列字段报 `UNKNOWN_FIELD`。
- `edit._budget`（edit.py:323）——单批新增实体数上限，但只有 array 系列调它（edit.py:258、281）；copy/offset/explode 等会放大实体数的路径没有统一入口。
- `annotate.setup_text_style`（annotate.py:25）与 `apply_annotation_scale`（annotate.py:31）——Standard 样式固定 `gbenor.shx` + 大字体 `gbcbig.shx`；`annotation_scale` 同时写 `$DIMSCALE`、`$LTSCALE` 与 AGENTCAD 标注样式。
- `annotate.ensure_title_block`（annotate.py:443）/ `TITLE_FIELDS`（annotate.py:432）——180×32 mm 标题栏块定义，中英各一份；`add_title_block` 先按 `UNITS` 折成图纸单位再以块参照插入（annotate.py:489-499）。
- `sheet.PAPER`（sheet.py:14）——A0–A4 纸面尺寸表，`plot.py` 也直接引用（plot.py:26）。
- `analysis.remove_duplicates`（analysis.py:84）——除几何键外还拼 DXF 属性、XDATA、块属性、多段线顶点的 JSON 指纹（analysis.py:94-104），避免“几何相同但语义不同”被误删。
- `draw.restack`（draw.py:232）——按 SORTENTS 重排绘图次序，`add_hatch(behind=true)`（draw.py:228）与 `draw_order`（edit.py:994）共用。
- `annotate.render_dimension`（annotate.py:101）与 `_RenderingStyle`（annotate.py:87）——绕开 ezdxf 把 `DIMRND=0` 当“取整”的偏差；`store_measurement`（annotate.py:108）写 group 42，角度按 AutoCAD 约定存弧度。
- `library.draw_prims`（library.py:42）/ `_break_for_symbol`（library.py:164）——stdlib 图元解释器，以及在管线上切出符号缺口并返回两个端口。
- `model.select`（model.py:320）/ `_match`（model.py:302）——选择器唯一解析点；`entity_to_dict`（model.py:116）与 `geometry_of`（model.py:169）是回给 agent 的实体字典（`detail=False` 时只回 id/type/layer/layout/tag）。
- `model.get_tag`/`set_tag`（model.py:43/54）与 `tag_index`（model.py:105）——XDATA `AGENTCAD` 下的 `(1000,"tag=x")`。
- `model.measure_dimension`（model.py:260）——从定义点重算尺寸，不信任文字与 group 42。
- `units.parse_length`（units.py:56）——`"8mm"`/`"0.5 in"`/`8"` 的唯一解析点，图纸单位未知时报 `UNITS_UNDEFINED`（units.py:72-75）。
- `geom.intersections`（geom.py:151）/ `offset_polyline`（geom.py:209）/ `angular_sector`（geom.py:102）/ `Seg.param`（geom.py:75）——trim/extend/offset/角度标注共用的几何原语。
- `constraints.check_constraints`（constraints.py:198）/ `enforce_one`（constraints.py:228）/ `get_point`/`set_point`（constraints.py:48/75）——点引用 `"<选择器>@<点>"` 的读写；`set_point` 对 center/insert/location 走整体平移。

**数据结构与不变量**

- **一份声明、三处消费**：`OpDef.params` 同时驱动 `coerce`（校验）、`schema()`（对外契约）、`engine` 的 `opdef.fn(ctx, **params)` 调用。每个执行函数的形参必须覆盖 `all_params()` 的全部键，多出来的走 `**p`；缺一个就是运行期 `INTERNAL_ERROR`，没有任何加载期或测试期检查（engine.py:72）。
- **预校验先于执行**：`prevalidate` 把整批 `coerce` 完才进 `execute`（engine.py:52）；任一步参数错，整批不执行、磁盘不变。`op_index` 在预校验（engine.py:46）与执行（engine.py:75）两处都会写进异常。
- **长度只在 coerce 折算**：`length` 参数进 ops 时已是图纸单位的浮点数（registry.py:290）。两个例外必须留在 op 内解析——`add_table` 的 `col_widths`（annotate.py:356）与尺寸的 `text_height`（edit.py:387），因为值藏在 list/object 里。
- **单位显式**：带单位的字符串遇到 `unit is None` 直接报错，不默认按 mm 解释（units.py:72-75）；纯数字始终是图纸单位。
- **三张变更表互斥**：`created` 进 `added`；`touched` 仅在句柄不在 `added`/`modified` 时记；`removed` 若句柄在 `added` 里就直接摘掉（等于本批没做过），否则进 `deleted`（registry.py:161-184）。
- **实体归属布局**：新实体只能加进 `ctx.layout`；从已有实体派生的必须用 `ctx.space_of(e)`——copy（edit.py:165）、offset（edit.py:578）、trim（edit.py:689）、fillet（edit.py:773）、chamfer（edit.py:795）全部照此。
- **整体视口不可删**：`Ctx.removed` 内部无条件调 `guard_main_viewport`（registry.py:172），`delete`（edit.py:333）与 `change_space`（sheet.py:62）另有显式检查；“整体视口”以布局的 `viewport_handle` 为准，`id` 只在没有该句柄时兜底（model.py:73-81）。
- **标签语义**：一实体一标签，存 XDATA；复制不继承（edit.py:166-168）；同批 `_tags` 惰性建立，`removed`/`finish`/`set_properties`/`explode` 四处手动置空（registry.py:180、215；edit.py:357、817）。
- **句柄稳定**：handle 是唯一 ID；规格修订走 `update_in_place` 保原句柄（engine.py:165）；`break`/`trim` 让第一段继续用原实体，只有新段是新句柄（edit.py:903-912）。
- **`$i` 只指向更早的 op**，且取该 op 结果里 `created` → `modified` → `existing` 的第一个非空列表（engine.py:87）；指向不存在或未来的 op 报 `BAD_REFERENCE`（model.py:350-352）。
- **精度契约**：`ROUND=6`，`r()` 把回读的坐标/长度截到 6 位并把 `-0.0` 归一为 `0.0`（model.py:27-29）；ops 内部计算不受影响，只有 `entity_to_dict`/`geometry_of` 对外时做舍入。
- **预算都在 policy**：批次 op 数（engine.py:34）、单批新增实体数（edit.py:323）、删除确认阈值 50（edit.py:334、policy.py:22）、实体总数（engine.py:97）。
- **角度与坐标系**：API 一律度、逆时针、WCS；`flipped_ocs`（model.py:137）把 extrusion (0,0,-1) 的圆/弧/多段线映射回 WCS，被编辑前先 `normalize_extrusion` 归一化（edit.py:57）。
- **范围检查只对标量**：`coerce` 的 gt/ge/le 只在 `float` 或 `int` 分支执行（registry.py:337-346），`point`/`points` 的坐标不查范围，只查有限性（registry.py:262）。
- **未列类型静默返回空几何**：`geometry_of` 对不在分派表里的类型回 `{}`（model.py:219），不报错——外来实体（如 3DSOLID）会以空几何出现在 `inspect_entities` 里。
- **标签前缀构成组**：`check_group` 以 `<tag>` 加 `.` 认组（library.py:111），`add_table`（annotate.py:371）、`add_process_line`（library.py:354）、`add_standard_hole`（library.py:558）据此生成 `.<suffix>` 子标签；所以 `tag:x` 与 `tag:x.deco` 是两个独立标签，`select("tag:x")` 不会带出装饰实体。
- **尺寸的双写**：每次创建或编辑尺寸都必须 `render_dimension` → `store_measurement`（annotate.py:62-63、edit.py:47-48），即渲染块 `*D<n>` 与 group 42 同步重写，否则 strict 校验报 `DIMENSION_STALE`。
- **填充非关联**：边界随后被改不会更新填充（draw.py:190 的说明即此意），`edit_hatch` 只改 island style（analysis.py:65-75）。
- **点参数的宽容形态**：`_point` 同时接受 `{"x","y"}` 与 `[x,y]`（registry.py:256-257），但 schema 只声明数组（registry.py:50）——dict 写法能用，不在这层的对外契约里。

**主要调用链**

1. `service.apply_operations` → `engine.prevalidate`（engine.py:31）→ 逐个 `coerce(REGISTRY[name], raw, unit, i)`（registry.py:272）→ `policy.check_op_allowed`（engine.py:45）→ 返回参数列表。
2. `engine.execute`（engine.py:50）：`ctx.op_index = i` → `params.pop("layout")`（engine.py:63）→ `ctx.use_layout(layout)`（registry.py:145）+ `ACTIVE_LAYOUT.set(...)`（engine.py:67）→ `opdef.fn(ctx, **params)`（engine.py:72）→ 结果写入 `ctx.refs[i]`（engine.py:87）→ `finally` 复位布局与 contextvar。
3. 单条创建：`add_circle`（draw.py:286）→ `_guard`/`check_tag`（draw.py:19 → registry.py:232）→ `ctx.attribs`（registry.py:192，顺带按需建层）→ `ctx.layout.add_circle` → `_single` → `ctx.finish`（registry.py:211）→ `set_tag`（model.py:54）→ `ctx.created`。
4. 选择器：op → `model.select`（model.py:320）→ `layout_spaces(doc, sel.get("layout", ACTIVE_LAYOUT.get()))`（model.py:338 → model.py:84）→ `_match`（model.py:302）；`tag:` 走全布局线性扫描（model.py:355），`$i` 走 `ctx.refs` + `entitydb`（model.py:353），结果去重保序（model.py:367-374）。
5. Schema 对外：`service.list_operations` 默认只回紧凑索引，读 `d.params`（service.py:331）；detail 或按 category 才走 `catalog()`（service.py:328 → registry.py:351 → OpDef.schema → Param.json_schema）。
6. 约束：`apply_constraints(mode="check")`（service.py:683）→ `check_constraints`（constraints.py:198）→ `_measure`（constraints.py:106）→ `_pref`/`_one` → `select`；`mode="enforce"` 逐条 `enforce_one`（constraints.py:228）→ `set_point`（constraints.py:75）→ 全量复检（service.py:700）→ 有违反则 `CONSTRAINT_CONFLICT` 不保存，否则 `store.commit`（service.py:707）。
7. 块内建几何：`define_block`（structure.py:117）→ 临时把 `ctx.layout` 换成 block（structure.py:132）→ 每条子操作再走一次 `coerce`（structure.py:138）→ `d.fn(ctx, **params)`（structure.py:140），`ctx.layout` 在 `finally` 复位。
8. 清理：`purge`（edit.py:1046）→ `_in_use`（edit.py:1010，扫所有块与布局、再按句柄跟标注样式引用的文字样式/线型/箭头块）→ 每轮重算并删到没有新可删为止（edit.py:1054-1075）。
9. 库符号上管：`add_pid_symbol`（library.py:274）→ `select` 取管线 → `_distance` 选最近一段（library.py:300）→ `_break_for_symbol`（library.py:164）切缺口并 `touched`/`removed` → 用 `ctx.space_of(pipe)` 建块参照 → 返回 `ports`（library.py:309）。
10. 边界追踪：`trace_boundary`（analysis.py:22）→ 遍历 `ctx.layout` 收集 LWPOLYLINE/LINE/ARC → `_segments` 串链（analysis.py:32-48）→ `poly_metrics` 取面积最小者 → 新建闭合多段线（analysis.py:55）。

**扩展点**

- **新增操作**：在 8 个 `ops/*.py` 之一写 `@op(name, category, description, params, common_draw=?)` + `def fn(ctx, <参数同名>, **p)`；不要新建文件——新文件必须同时在 `ops/__init__.py:2` 加 import 才会注册，否则静默不可见。`category` 决定 `list_operations(category=...)` 分组与 `allowed_operations` 白名单（policy.py:81），与文件名无关。
- **新增参数类型**：三处同改——`Param.json_schema`（registry.py:32）、`coerce` 的类型分支（registry.py:289-330）、必要时 `_point`（registry.py:255）；漏改 `coerce` 会让该类型参数原样传给 op。
- **新增公共参数**：改 `COMMON_DRAW`（registry.py:106）会一次性加到 37 个操作的 schema 上，改 `COMMON_ALL`（registry.py:100）加到全部 71 个；`OpDef.all_params` 没有按 op 关掉的机制。另注意 `engine.py:68` 只对 `common_draw` 的 op 读 `tag`，给非 draw op 加 `tag` 不会触发 reconcile。
- **新增选择器形态**：`model.select`（model.py:320）加分支 + `_match`（model.py:302）加过滤键 + `coerce` 的 `selector` 分支（registry.py:324）放宽入参类型；三处缺一处就会出现“能传但选不中”。
- **新增约束**：可检查的进 `CHECK_TYPES`（constraints.py:27）+ `_measure`（constraints.py:106）分支；可强制的再进 `ENFORCE_TYPES`（constraints.py:29）+ `enforce_one`（constraints.py:228）。两个集合原样出现在 `list_operations` 的 `constraint_types`（service.py:312）与 `apply_constraints` 的提示里。
- **新增行业库**：几何数据进 `stdlib.py`，绘制入口复用 `draw_prims`（library.py:42）、`_layer_default`（library.py:95）、`check_group`（library.py:105，按 `<tag>.*` 整组 skip/replace）、`_anno`（library.py:31，1 纸面 mm 对应的图纸长度）。
- **新增单位**：`UNITS`（units.py:16）+ `ALIASES`（units.py:23）+ `CODE_TO_UNIT`（units.py:30）三张表；`parse_length` 的正则已允许中文别名区间（units.py:32）。
- **新增块内可用操作**：`DRAW_OPS_IN_BLOCK`（structure.py:86）是 `define_block` 的白名单，新 draw 操作若要能在块定义里用，必须同时加进这个集合，否则报 `INVALID_BLOCK_ENTITY`。

**风险与可疑之处**

- [高] **过滤对象不校验键名，写错键等于“选中当前布局全部实体”** — `coerce` 只检查是不是 dict（registry.py:324-326）；`select` 只拒绝空 dict（model.py:335-337）；`_match` 只读 `type/layer/tag/tag_prefix`（model.py:302-317），`layout`/`all` 单独读（model.py:338-342）。于是 `{"layr":"HOLES"}`、`{"all":false}` 这类写法会静默命中整个活动布局。影响：`rotate`/`scale`/`mirror` 只受“新增实体数”预算约束（edit.py:323）就会作用到全部实体；`delete` 虽有 50 条阈值（edit.py:334），一次误选正好少于 50 条时也不会触发确认。建议：在 `coerce` 的 selector 分支对 dict 做键白名单校验，与 `EMPTY_SELECTOR` 用同一个报错点。
- [中] **整体视口的保护有缺口** — 只有 `delete`（edit.py:333）与 `change_space`（sheet.py:62）显式调 `guard_main_viewport`；`move/rotate/scale/mirror` 走的 `transform`（edit.py:125）没有该检查，而 `select` 能正常选到 VIEWPORT 句柄（model.py:357-361，其 owner 属于布局）。影响：整体视口被平移/缩放后，布局句柄仍指向它，不会触发 `LAYOUT_VIEWPORT_MISSING`，但与 architecture.md “拒绝删除或移走整体视口”的表述不一致；`tests/test_layouts_and_scripts.py:372` 只覆盖 delete 与 change_space。建议：在 `transform` 入口补同一个 guard，或把文档改成“只禁删除”。
- [中] **约束的失败没有全部结构化** — `check_constraints` 只吞 `AgentCADError`（constraints.py:211-216），`float(c["value"])` 遇到字符串或 None 抛 `TypeError`；`enforce_one` 的 distance 分支在两点重合时 `G.unit` 抛 `ValueError`（constraints.py:247、geom.py:41-45）。影响：`apply_constraints` 会以未包装异常结束，结果里没有 `error.code`，CLI 也拿不到按类别映射的退出码。建议：在 `_measure`/`enforce_one` 内转成 `input_error`，或在 service 的 enforce 循环里补 `except (TypeError, ValueError)`。
- [中] **标签缓存失效靠手工置空，新增写标签路径容易漏** — `ctx._tags` 在 `removed`（registry.py:180）、`finish`（registry.py:215）、`set_properties`（edit.py:357）、`explode`（edit.py:817）各写一次 `None`，`engine.update_in_place` 之后再由调用方补置空（engine.py:121、127）。影响：任何“改标签但不删实体”的新路径漏一处，本批后续的 `tag:` 选择器与 `if_tag_exists` 会读旧索引，表现为重放不幂等。建议：把写标签收口成 `Ctx.set_tag`，由它统一失效。
- [低] **`dim` 参数类型是死代码** — `json_schema`（registry.py:60-62）与 `coerce`（registry.py:327-330）都实现了它，注释说“由 3D 后端解析表达式”，但 71 个操作无一使用，`src/` 下也没有 3D 后端。影响：schema 里多一个永不触发的分支，读代码的人会去找不存在的实现。
- [低] **`$i` 在同时 created+modified 的操作上只绑定 created** — `engine.py:87` 是 `res.get("created") or res.get("modified") or ...` 的短路链；`trim`（edit.py:655）、`fillet`（edit.py:777）、`chamfer`（edit.py:798）、`break` 的 `_pieces`（edit.py:912）都同时返回两者。影响：`$3` 拿不到被修改的原实体，与 architecture.md“本批第 i 步创建（或修改）的实体”的措辞有落差。
- [低] **`common_all` 是不可达开关，隐式建层也不进 changes** — `op()` 装饰器没暴露 `common_all`（registry.py:119），`all_params` 的 `not self.common_all` 分支（registry.py:93-94）永远不成立；`ctx.attribs` 对未知图层直接 `doc.layers.add`（registry.py:195-198），而 `engine` 只回实体句柄（engine.py:99），拼错图层名既不报错也不出现在结果里。
- [低] **`stretch` 不接选择器** — 它按窗口扫整个活动布局 `ctx.msp`（edit.py:470），是唯一没有 `target` 的编辑类操作；与其余操作“先 `select` 再改”的模型不一致，也无法用 `allowed_operations`/句柄审计的口径预估它改了什么。

**测试对应**

- `tests/test_h_operations.py` — 基础操作端到端（线/多段线/矩形/圆/弧/椭圆/样条/文字，27-113）、尺寸实测（113）、引线/表格/标题栏（141）、变换与阵列（161-251）、删除阈值（265）、stretch/offset/trim/extend/fillet/chamfer/explode（289-367）、图层与块（376-408）、`UNKNOWN_OP`/`UNKNOWN_PARAM` 诊断（413）、`$i` 批内引用与 `BAD_REFERENCE`（420）、单位换算与显式性（429）。
- `tests/test_i_common_ops.py` — 填充与孤岛、多边形三种定尺、圆环、构造线不计入 extents、break、join、draw_order、purge、中心标记、文字样式，以及 `define_block` 内的 draw 操作（244）。
- `tests/test_constraints.py` — 15 种检查类型（27）、强制子集后复检（47）、冲突不保存（58）、约束输入错误（68）。
- `tests/test_m_library.py` — 内联符号切断管线与随管转向（24-51）、清掉被盖住的气流箭头（64）、GD&T 规则与随标注比例缩放（107-140）、标准孔/标准件真实尺寸与英寸图纸（154-206）、多实体操作的重放（234）、管上符号拒绝 `replace`（251）。
- `tests/test_layouts_and_scripts.py` — 布局参数下的操作与尺寸（52-76）、`create_layout`/`add_viewport`（213）、sheet 操作错误（244）、标注比例与纸面字高（269-348）、整体视口的删除保护与判定依据（372-413）。
- `tests/test_autocad_golden.py` — 与 AutoCAD 逐属性对照的黄金用例（镜像文字、三点弧方向等变换与标注细节）。
- `tests/test_d_errors.py` — 缺参数（81）与错误类别可区分（67/90）；`tests/test_security.py` — 操作白名单（40）与预算（50）；`tests/test_g_interfaces.py` — 适配层不导入 `ops/engine`（208）与紧凑目录体积（224）。

## 3.3 验证与度量：validate/*、quality、verification、measure、fits、tolerances

**规模**：10 个文件 / 2,208 行

**职责与边界**

这一层回答四个问题：文件本身是否完整（`validate/checks.py`）、显式需求是否满足（`validate/requirements.py`）、交付物是否与源图一致（`quality.py`）、数值与公差配合是否可信（`measure.py`、`tolerances.py`、`fits.py`）。

它不编辑图纸：本区域对 `Drawing` 只有读操作，写路径仅两处例外——`tolerances.apply_tolerance` 写 DIMTOL/DIMLIM 覆盖与 `AGENTCAD_TOLERANCE` xdata（由 `ops/annotate.py:57`、`ops/edit.py:392` 在操作内部调用），以及 `quality.deliver` 写交付目录与 `manifest.json`（`quality.py:86-119`）。

`validate_file` 自己从磁盘重解析（`validate/__init__.py:46-48`），拿不到 engine 的内存文档、操作计划或 spec，"独立验证"由依赖方向而不是纪律保证。

调用方只有 `service.Runtime`（`service.py:619`、`650`、`989`、`1007`、`1046`、`1090`、`1252`、`1314`、`1356`）；反向不成立：`validate/*` 不导入 `service`。

它向下依赖 `store.read_dxf`、`policy.Policy`、`model`（布局/句柄/VD 表）、`geom`、`units`，以及只为取字体表的 `plot.font_cmap/plot_fonts`（`validate/checks.py:217`，函数内导入，加载期无环）。

它不做的事：不修图（`refine_drawing` 的修复动作走 `apply_operations`，`service.py:1362`）、不做拟合或插值补全缺失几何、不判断工程合理性（那是 `engineering/*.read` 的 critique，`quality.py:36-51`）、不负责沙箱（沙箱在 `policy.resolve`，`service.py:198`）。

**文件清单**

| 文件 | 行数 | 角色 | 职责 |
|---|---:|---|---|
| `validate/__init__.py` | 107 | 入口 | `validate_file` 的唯一入口：读盘、audit、抽特征、跑通用检查、算需求、汇总 `passed`；`unchecked_layouts` 报未检查布局 |
| `validate/checks.py` | 574 | 核心 | 32 个 `issue(...)` 调用点：退化几何、未定义块/层、重复实体、XDATA 上限、标注文字/显示/附着/陈旧、出图可读性、纸面字高、布局视口、轮廓闭合、边界；`issue()` 与 `BlockSnaps` 也在这里 |
| `validate/features.py` | 317 | 核心 | 从实体抽 `Features`：闭合轮廓（多段线或 LINE/ARC 首尾成环）、孔、标注实测值/方向/锚点、捕捉点/曲线/线段、文字 |
| `validate/drafting.py` | 384 | 核心 | 制图规范：标注重叠/尺寸线相交（含聚类与中心豁免）、回转体台阶/倒角缺横线 |
| `validate/requirements.py` | 158 | 核心 | 7 种需求类型的比对，输出 expected/actual/deviation/tolerance/entities；未知字段转 `warnings` |
| `quality.py` | 120 | 核心 | `score` 线性评分；`assess` 交付前复检（模型空间 + 每个非空图纸布局 + 工程域 critique）；`deliver` 导出、逐件校验、写 manifest |
| `verification.py` | 128 | 核心 | `verify_dimensions`：按 handle 把物理量绑到标注定义点，独立于显示文字报告 `dimension_unbound/mismatch/override_mismatch` |
| `measure.py` | 196 | 支撑 | `measure_entity` 精确面积/周长（多段线含 bulge、填充含洞、圆/弧/线）；`spatial_filter` 窗口/多边形选择 |
| `fits.py` | 63 | 数据 | H7/h6/g6 极限偏差表（10 < d ≤ 500 mm），带来源页码；范围外与未收录类一律拒绝 |
| `tolerances.py` | 161 | 适配 | 写/清 DIMTOL·DIMLIM·DIMTP·DIMTM，保存原观感 xdata；`check_dimension_tolerance` 是重绘与落盘共用的只读校验 |

**关键入口**

- `validate_file(path, *, requirements, bounds, tolerance=1e-6, contour_layers, layout)`（`validate/__init__.py:40`）— 输入只有路径与选项，返回 `{file, readable, issues, requirements, coverage, recognized, summary, passed, duration_ms}`；解析失败本身就是一条 `file` 类 issue 而不是异常（`:49-55`）。
- `unchecked_layouts(doc, checked)`（`validate/__init__.py:21`）— 列出"有几何内容但不是被检查布局"的布局，喂给 `coverage` 与 `LAYOUT_NOT_VALIDATED`（`:73-81`）；`geometric` 统计排除 `TEXT/MTEXT/INSERT`（`:34`）。
- `run_generic_checks(doc, feats, *, bounds, tolerance, contour_layers, layout)`（`validate/checks.py:420`）— 通用检查的聚合点；新增检查挂在这里的一次 `out.extend(...)`（`:475-479`）。
- `issue(code, severity, message, entities=None, **details)`（`validate/checks.py:123`）— 全区域唯一的 issue 工厂：默认 `category="geometry"`，`details` 只在非空时出现；`file`/`coverage`/`input` 类别由调用点事后改写（`__init__.py:50,60,65,77`；`checks.py:411`），这是唯一的例外机制。
- `geometry_key(e, q)`（`validate/checks.py:135`）— 几何规范化键（量化坐标、线段方向无关、标注含 kind/角度/两定义点/实测），`DUPLICATE_ENTITY` 的唯一判据；返回 `None` 表示该类型不参与查重。
- `BlockSnaps`（`validate/checks.py:60`）— 惰性构建的块内几何捕捉网格：`walk` 递归 `virtual_entities` 到 4 层（`:80-108`），只在顶层几何上找不到锚点时才用（`:541`）；`has_xref` 为真时整条附着检查放弃（`:539-540`）。
- `extract(doc, layout=None) -> Features`（`validate/features.py:136`）— 只读实体；`layout` 指图纸空间时按该布局抽（`layout_spaces`），与 `checks` 共用同一份特征，避免二次遍历。
- `measure_dimension(e) -> (kind, value|None)`（`validate/features.py:74`）— 由定义点重算：linear 投影到 `angle`、aligned 取两基点距离、diameter/radius 取 `defpoint→defpoint4`、angular/angular3p 走扇形、ordinate 取 X/Y 分量。`model.measure_dimension`（`model.py:260`）是同功能的另一份实现，返回裸 float。
- `dimension_direction(e)` / `dimension_anchor_points(e)`（`validate/features.py:107`、`:119`）— 附着检查的两个前提：测量方向（决定"边是否横跨"）与被测几何的参考点（不一定等于定义点）。
- `pick_outline(feats, layer)` / `holes_in(feats, outline, layer)`（`validate/features.py:309`、`:316`）— 面积最大的轮廓即外轮廓；孔 = 圆心落在该轮廓内的圆。
- `check_requirements(feats, reqs, counts)`（`validate/requirements.py:46`）— `counts` 为当前空间的 `(type, layer, handle)` 三元组，只有 `entity_count` 用；`dimension` 比的是 `displayed`（`:121`）而非实测。
- `_res(...)`（`validate/requirements.py:36`）— 需求结果的唯一构造器；`source` 只在需求带 `source` 时透传（`:41-42`），文档来源靠它回溯。
- `drafting_issues(doc, feats, layout)`（`validate/drafting.py:25`）— 全部为 warning，失败的图仍然 `passed`；`DIMENSION_OVERLAP` 走并查集聚类（`:211-231`），`DIMENSION_LINES_CROSS` 用文本高度格网去重（`:196-209`）。
- `_model_text_heights(doc)`（`validate/checks.py:311`）— 给字高检查用的 `(handle, kind, height, (x, y), DIMSCALE)` 五元组流；`TEXT`/`MTEXT`/块属性/标注文字都在内，单条实体异常直接跳过（`:332-333`），因此它永远不会让校验失败。
- `score(report)` / `assess(rt, p, doc)` / `deliver(rt, path, output_dir, formats, min_score, strict)`（`quality.py:13`、`:20`、`:61`）— `score` 只读 `summary`；`assess` 会重开文件、恢复 stored spec 语义、把每个图纸布局各验一遍；`deliver` 是唯一同时持写锁与导出锁的路径。
- `verify_dimensions(doc, requirements, tolerance)`（`verification.py:31`）— 需求模式严格（只允许 `id/handle/value/tolerance`，`:37-45`），返回 `{rows, findings, accepted}`；`accepted` 要求 `rows` 非空且全 pass 且无 findings（`:127`）。
- `measure_entity(e)` / `spatial_filter(...)`（`measure.py:104`、`:163`）— 前者只支持 LWPOLYLINE/HATCH/CIRCLE/ARC/LINE，其余抛 `UNSUPPORTED_MEASUREMENT`（`:129`）；后者 `window` 按包围盒包含/相交、`polygon` 只支持 `mode="window"`（`:170-171`）。
- `lookup(nominal, designation)` / `table(size)`（`fits.py:28`、`:55`）— `"25 H7/g6"` → 孔/轴极限与间隙；`size=None` 返回目录（`:21`）。
- `apply_tolerance` / `check_dimension_tolerance` / `tolerance_info`（`tolerances.py:19`、`:82`、`:101`）— 写入端与读取端共用 `_stored`/`_own` 一套反解，保证"AgentCAD 写的"与"别人写的"用不同规则。

**数据结构与不变量**

- 报告形状固定为 `file/readable/issues/requirements/coverage/recognized/summary/passed/duration_ms`（`validate/__init__.py:44,81,88,100-105`）；`passed = errors == 0 and requirements_failed == 0`，**warning 不阻断 `passed`**（`:105`）——但 `quality.score` 把每条 warning 折成 −3 分，`deliver_drawing(min_score=90)` 仍会因警告拒绝（`quality.py:13-17,79-85`），"通过"与"可交付"是两个判据。
- `coverage` 是显式声明，不是推断：`{checked_layout, unchecked_layouts}`（`validate/__init__.py:81`），`quality.assess` 之后被重写成"全部非空布局都已检查"（`quality.py:54`）。
- issue 的最小结构是 `code/severity/category/message/entities`，`details` 可选；`severity` 只有 `error`/`warning` 两种取值，没有 `info`。`category` 实际出现 `geometry`（默认）、`file`、`coverage`、`input`、`engineering`（`quality.py:49`）——前四种在同一份报告里混用，消费方必须容忍未声明的类别。
- 需求结果的 `status` 只有 `pass`/`fail`/`error` 三值；`error`（未知类型、缺字段）也计入 `requirements_failed`（`validate/__init__.py:102`），因此需求写错会让整份报告不通过，而不是被忽略。
- 报告的对外体积受控：`compact_report` 把 issue 截到 50 条并给 `issues_omitted`（`service.py:1514,1527-1530`），通过的只留 id（`:1519-1524`）；`detail=true` 才给全量。这是"一次校验的输出不能压过图本身"的约束。
- 只读解耦：`validate_file` 只接受 `Path`，内部 `read_dxf(path, Policy(root=path.parent))`（`:46-48`）；该 `Policy` 只用于 `max_file_bytes`（`store.py:89`），**不做路径沙箱**——沙箱由调用方在 `policy.resolve` 阶段完成（`service.py:198`）。
- 尺寸值分三栏且互不替代：`measurement`（定义点实测）、`scaled_measurement = measurement × DIMLFAC`（角度尺寸强制 1.0，`features.py:180-181`）、`displayed`（从 `*D` 图形块读出的可见值，`features.py:182` → `dimension_display.read_display`）。只有 `displayed` 能证明"图上印出来的数字"，group 42 仅作旁证。
- 重复实体按几何规范化比较：坐标以 `tolerance` 为量化步四舍五入，线段方向无关（`checks.py:143-145`），标注按 `dimtype & 0x0F` + 角度模 180 + 两个定义点 + 实测值（`:160-164`）。
- 公差的所有权用 xdata `AGENTCAD_TOLERANCE` 标记（`tolerances.py:15`）：`_own` 只认挂在实体上的 `dimtol/dimlim`，不认样式继承（`:127-129`）。AgentCAD 写过的公差在外部编辑后仍按原规则校验（精度损失会变成 `TOLERANCE_PRECISION_LOSS`），别人的公差只拒绝非有限值（`:93-96`）。
- 公差写入前必须能反解：`_validated_values` 要求 `lower ≤ upper`、精度 0..8、显示值不发生 `TOLERANCE_PRECISION_LOSS`（`tolerances.py:61-65`），`limits` 模式还要求 `nominal ± deviation` 在同样精度下可见（`:73-75`）；文本覆盖不含 `<>` 时拒绝（`:76-78`）。
- fit 表是封闭枚举，不是公式：`CLASSES=("H7","h6","g6","H7/h6","H7/g6")`、10 行尺寸段、`10 < nominal ≤ 500`（`fits.py:14-18,31-34`）；没有外推分支。
- `verify_dimensions` 的绑定语义：锚点必须落在捕捉点、圆/弧（含弧段角度判定）或与测量方向相交的边上（`verification.py:71-90`）；绑定失败即 `actual=None`、`bound=false`，绝不用显示值兜底。
- `measure` 的精确性承诺只覆盖解析可闭式求解的类型：bulge 用 `θ=4·atan(b)`、`R=chord(1+b²)/(4|b|)` 与圆缺面积（`measure.py:15-28`），填充按 `hatch_style` 的奇偶深度加减（`:84-93`），不支持 `SplineEdge` 时直接拒绝（`:81-82`）。
- 布局名写错不会崩，但会静默改检查对象：`extract` 抛 `LAYOUT_NOT_FOUND` 时 `validate_file` 记一条 `input` 类 error、回退模型空间并把 `layout` 置 `None`（`validate/__init__.py:62-68`），随后 `coverage` 与 `recognized` 都按模型空间填。
- 出图可读性按"纸面尺寸"判定：`MIN_PRINTED_TEXT = 2.5` mm（`checks.py:308`），模型空间文字按视口比例 `s = vp.height / view_height` 换算（`:370`），图纸空间文字直接比（`:387-392`）；`dimscale=0` 的标注视为"随视口缩放"而跳过（`:295`、`:328`），这是审阅后加的豁免而不是遗漏。
- 轮廓闭合与越界是"按需"检查：`OPEN_CONTOUR` 只在传入 `contour_layers` 时跑（`checks.py:548-552`），`OUT_OF_BOUNDS` 只在传入 `bounds` 时跑（`:555-573`），且逐实体用 `bbox.extents(fast=True)` 并与 `is_main_viewport` 排除整体视口（`:558`）；两者默认关闭，所以"没有这两类 issue"不等于轮廓闭合或几何在框内——`validate_drawing` 只在调用方给了参数或从 stored spec 推出 `contour_layers` 时才具备这两项覆盖（`service.py:667`）。

**主要调用链**

1. `apply_operations` → `_validate_bytes(data)`（`service.py:469`/`:484`）→ 写 `candidate.dxf` 到临时目录（`:624-627`）→ `validate_file`（`:40`）→ `read_dxf` + `doc.audit()`（`:46-61`）→ `extract`（`:63`）→ `run_generic_checks`（`:69`）→ `unchecked_layouts`（`:73`）→ `check_requirements`（`:86`）→ `summary/passed`（`:100-105`）。校验对象是**待提交的字节**，不是磁盘上可能已被别的进程改过的文件；`strict` 因此在写盘前就能拒绝。
2. `validate_drawing` → `_drawing_validation`（`service.py:650`）→ 若 `use_stored_spec` 且目标是模型空间：再 `read_dxf` 一次取 `part_spec`（`:657-666`），编译出 requirements 并把 `contour_layers` 默认设为 `["OUTLINE"]`（`:667`）→ `validate_file`（`:668`）→ 出口处 `compact_report`/`_brief`（`:1533-1568`）裁到 50 条 issue 与"仅失败需求"。
3. `deliver_drawing` → `quality.deliver`（`quality.py:61`）→ `check_write`（`:62`）→ 目录必须为空（`:71-73`）→ `_lock`（`:74`；`service._lock` 是 contextvar 可重入，`:202-207`，所以 `export_drawing` 内部再取同一把锁不会自锁）→ `sha256_file`（`:75`）→ `assess`（`:77`）→ `_drawing_validation`（`:22`）+ 每个非空图纸布局再验一遍（`:29-35`）+ 三个工程域 reader 的 critique（`:36-51`）→ `score`（`:78`）→ 门槛（`:79-85`）→ 逐格式 `export_drawing` + `verify_artifact`（`:87-105`）→ 源文件 sha 复核（`:106-107`）→ 写 manifest（`:108-119`）。
4. 校验内部顺序（`checks.py:475-479`）：`annotation_issues` → `plot_text_issues` → `printed_text_issues` → `layout_viewport_issues` → `drafting_issues`；随后是全 entitydb 的公差遍历（`:484-490`，刻意的：块内与未引用的标注也要查，且走存储实体而不是会被重复/变换的 virtual INSERT 副本），再 `BlockSnaps`（`:493`）+ 尺寸一致性（`:494-545`），最后轮廓闭合（`:548-552`）与边界（`:555-573`）。
5. 单条尺寸的判定顺序（`checks.py:494-545`）：可测性（`DIMENSION_UNMEASURABLE`）→ 文字与实测是否一致（`DIMENSION_TEXT_MISMATCH`，单值无文字时升级为 error，`:506-509`）→ 图形块显示值（`DIMENSION_DISPLAY_UNVERIFIED`/`DIMENSION_RENDER_MISMATCH`）→ `<>` 前缀重复（`DIMENSION_DOUBLE_PREFIX`）→ group 42（`DIMENSION_STALE`）→ 锚点附着（`DIMENSION_NOT_ATTACHED`，每个标注只报一次，`:545`）。
6. 度量入口：`measure_entities` → `select` → `measure_entity`（`service.py:1330-1336`）；`inspect_entities(window=/polygon=)` → `spatial_filter`（`:423-425`）；`standard_library(item="iso_fit")` → `fits.table`（`stdlib.py:310-312`）。
7. 公差写入：`ops/annotate.py:57`（新建标注）与 `ops/edit.py:392`（修改）→ `apply_tolerance` → `override.set_tolerance/set_limits`（`tolerances.py:36-42`）；重绘前 `ops/edit.rerender_dimension` 先 `check_dimension_tolerance`（`edit.py:25-27`），`set_annotation_scale` 对每个标注先查后退（`annotate.py:406-417`）。

**扩展点**

- 新增一条通用检查：在 `checks.py` 写 `def x_issues(doc, layout=None) -> list[dict]`，用 `issue(...)` 产出，然后在 `run_generic_checks` 的聚合段（`:475-479`）挂一行。按布局生效就用 `layout_spaces(doc, layout)[0]`；要覆盖全图（含块）就遍历 `doc.entitydb`（公差检查的写法，`:484`）。severity 决定它能否阻断 `strict`，成本决定是否该只给 warning。
- 新增需求类型：`requirements.py:25-33` 的 `FIELDS` 加键（顺带获得"未知字段告警"），再在 `check_requirements` 里加分支并用 `_res(...)` 组装结果；漏登记会落到 `:149-150` 的 `status="error"`，直接让整份报告不通过。
- 新增特征种类：`Features`（`features.py:56-67`）加字段 + `extract` 加实体分支 + `snap_points`（`:203`，供未专门处理的实体类型提供捕捉点）；下游 `pick_outline`/`holes_in`（`:309`、`:316`）可直接复用轮廓。
- 新增一条制图规范：`drafting_issues`（`drafting.py:25-31`）末尾追加一个 `_x(doc, space, feats, issue)`；成对比较必须自己带剪枝（现有两条分别用包围盒扫描线 `:171-209` 与轴/轮廓筛选 `:286-298`），否则会退化成平方复杂度。
- 新增交付格式：`quality.py:67` 的格式白名单 + `service.export_drawing` 的 suffix 表（`service.py:967`）+ `delivery.verify_artifact` 加一个 parity 分支（`delivery.py:135`）。
- 新增配合类或尺寸段：`fits.py` 的 `CLASSES`/`ROWS` 与 `catalog()`（`:14-25`）同步改，且必须写明来源页码——该模块的设计前提是"不推算、只抄表"。
- 新增公差模式：`_validated_values` 的 `mode` 白名单（`tolerances.py:62`）+ `_stored` 的反解（`:119-124`）+ `dimension_display._primary` 的显示值解析（`dimension_display.py:26-79`）；三处缺一就会出现"能写不能读"或"能读不能交付"。
- 新增测量类型：`measure_entity` 加分支（`measure.py:104`）+ 保证解析闭式可解；`spatial_filter` 若要支持多边形"相交"模式，需改 `:170-171` 的拒绝分支与 `:186-191` 的保守判定。

**风险与可疑之处**

- [中] `run_generic_checks` 没有兜底异常处理，一处 ezdxf 异常会让整次校验变成内部错误 — `validate/__init__.py:69` 无 try；`checks.py:484-490` 只把 `AgentCADError` 转成 issue，而 `tolerances.py:97` 只捕 `TypeError/ValueError/OverflowError`，`_override` 只捕 `DXFTableEntryError`（`:112-116`）。影响：畸形外来图（缺样式表、异常 xdata）使 `validate_drawing`/`deliver_drawing` 抛非结构化错误，已收集的 issues 全部丢失。建议：在 `validate_file` 主体外包一层 `except Exception` → `VALIDATOR_CRASH`（category `internal`），保留已有 issues。
- [中] 只含块参照的图纸布局不会被告知"未检查" — `unchecked_layouts` 统计"几何实体"时排除 `TEXT/MTEXT/INSERT`（`validate/__init__.py:34`），而真实打印布局常常就是"模型的缩放副本装在一个 INSERT 里"。影响：`validate_drawing` 报告 `coverage` 干净，实际没看那张纸。缓解：`quality.assess` 对每个非空布局单独复检（`quality.py:29-35`），所以交付路径不受影响；单次校验受影响。建议：把 `INSERT` 计入 geometric，或在 `coverage` 里单列 insert-only 布局。
- [中] 带 bulge 的多段线弧段不进 `features.curves`，落在弧上的标注锚点必然报 `DIMENSION_NOT_ATTACHED` — `features.py:145-154` 只写入顶点与弦线段，没有弧；`verification.py:18-27` 反而专门用 `virtual_entities()` 建了含真实弧的 `curves`。影响：槽口/腰形孔上的尺寸产生假警告（warning 不阻断，但污染 `score`）。建议：`extract` 对 bulge 段生成 `(center, radius, handle)` 进 `f.curves`，与 `verification` 共用同一份几何。
- [中] `measure_dimension` 有两份实现，已经出现算法分歧 — `model.py:260-297`（angular3p 用 `atan2` 加 360 回绕）与 `validate/features.py:74-104`（用 `G.angle_of` + `G.ccw_span`）。当前 `tolerances.TOLERANCED={0,1,3,4}`（`tolerances.py:12`）不含角度，所以两套口径还没在交付路径上直接对撞；`checks.geometry_key`（`checks.py:161`）与 `tolerances._validated_values`（`tolerances.py:66-71`）走的是 `model` 版，`DIMENSION_*` 系列走 `features` 版。影响：改一处修 bug、另一处保持旧行为。建议：保留一份（`features` 版带 kind，信息更全），让 `model.measure_dimension` 变成薄封装。
- [低] `validate_file` 不做路径沙箱，安全性完全依赖调用方先 `policy.resolve` — `validate/__init__.py:46-48` 用 `Policy(root=path.parent)` 构造一次性策略，仅 `max_file_bytes` 生效（`store.py:89`）。这是为了让 `_validate_bytes` 能验临时目录里的 `candidate.dxf`（`service.py:624-627`）而必须如此。建议：在 docstring 里写明"path 必须是已解析路径"，避免新入口直接把用户字符串传进来。
- [低] 报告里的 `recognized.entity_count` 恒为模型空间实体数，与 `layout` 参数无关 — `validate/__init__.py:97` 固定用 `len(doc.modelspace())`，而同一份报告的其他字段（`outline`/`circles`/`dimensions`）都来自被检查布局。影响：检查图纸布局时"实体数"对不上眼前这张纸，Agent 容易据此判断漏检。建议：改成 `len(space)` 并保留 `model_entity_count`。
- [低] 交付成本随布局数线性放大 — `assess` 对每个非空布局重跑一次完整校验（`quality.py:29-35`），而 `_drawing_validation` 为了读 `part_spec` 又额外 `read_dxf` 一次（`service.py:657`），`validate_file` 内部还要再解析一次：N 个布局 = N+2 次 ezdxf 解析 + N 次 `doc.audit()`。影响：多布局图纸交付变慢（与第 7 章热点一致）。建议：一次解析出 `doc` 后按布局复用，只把 `extract`/`checks` 重跑。
- [低] 标注重叠检查在超过 2000 个标注时静默跳过 — `drafting.py:18,30`（`len(dims) <= MAX_DIMENSIONS` 才跑）。影响："没有 `DIMENSION_OVERLAP`"在大图纸上不等于没问题，且报告里没有任何说明。建议：跳过时补一条 `CHECKS_SKIPPED` warning。
- [低] `fits` 只认 mm — `fits.lookup` 不接受 drawing unit 参数（`fits.py:28-34`），而 `standard_library` 也不读图纸单位；inch 图纸上返回的仍是 mm 极限值。影响：调用方必须自己换算（测试里的 `test_y_tolerances.py:311-323` 全部用 mm 图）。建议：`catalog()` 明示 `units="mm"`，或让 `table` 接受单位并显式换算出"图纸单位"。

**测试对应**

- `tests/test_drafting_checks.py`（195 行）— `DIMENSION_OVERLAP`/`DIMENSION_LINES_CROSS`/`TURNED_EDGE_MISSING` 的全部场景：三种直径过同心、两条同心直径豁免、outside 直径不穿圆心、共线重叠、成串尺寸不算重叠、10 条堆叠聚成一条警告、肩部/倒角缺线、无 Ø 的平板不触发、孔内径不触发、400 条网格 30 s 内出单条警告、中心线分段与跨 0° 合并。
- `tests/test_dimension_display.py` — 显示值不可读/被隐藏/被改/科学计数/角度格式都会变成 `DIMENSION_RENDER_MISMATCH` 并让 `deliver_drawing` 返回 `DELIVERY_REJECTED`。
- `tests/test_native_delivery.py` — 几何块被删/被隐藏时 `verify_dimensions` 不 `accepted`、交付被拒（`:167-174`）。
- `tests/test_w_delivery.py`（184 行）— `assess` 的 stored spec 恢复（`requirements_source`）、三个工程域的 critique 与 `DOMAIN_LAYOUT_UNSUPPORTED`、图纸空间完整性、五种导出格式的 parity 破坏与 `DELIVERY_PARITY_FAILED`、`strict=False` 仍可产出带失败门的 manifest（`:59-60`）。
- `tests/test_y_tolerances.py`（347 行）— 公差写入/重开/修改/清除后观感还原、精度损失阻断、外部改坏的 mark、块内与未引用块的公差只报一次、fits 表逐行与边界（18/30/…/500 与 500.001）、未收录类拒绝。
- `tests/test_layouts_and_scripts.py` — `LAYOUT_NOT_VALIDATED` 的双向（检查图纸空间时报 Model，反之报 Sheet，`:42-49`）、`PRINTED_TEXT_TOO_SMALL` 与提示文案（`:257-358`）、`LAYOUT_VIEWPORT_MISSING`（`:423-428`）、`OUT_OF_BOUNDS`（`:398-403`）。
- `tests/test_autocad_findings.py` — 外来图上的 XDATA 两项、`ARC_ANGLE_NOT_NORMALIZED`、`TEXT_GLYPHS_MISSING`、`ANNOTATION_TOO_SMALL`、`DIMENSION_DOUBLE_PREFIX`、hole/angular 需求。
- `tests/test_k_pdf.py` — `TEXT_PLOTS_AS_BOXES`、`TEXT_CARET_CODE` 与按实际出图布局复查（`:139-188`）。
- `tests/test_e_tamper.py` — `DIMENSION_NOT_ATTACHED`（`:49`）与 `DIMENSION_TEXT_MISMATCH`（`:64`）；`tests/test_j_compact_results.py` — `compact_report` 的 issue 截断与"只列失败需求"。
- `tests/test_t_workflow_guards.py` — 校验期间文件被外部改动（`:50-81`）、`measure_entities` 的面积口径（`:126-130`）、多边形选择（`:338-341`）；`tests/test_d_errors.py:26`、`tests/test_l_fewer_turns.py:44` 覆盖 `OUT_OF_BOUNDS` 与 apply/validate 结果一致。
- 未覆盖（全仓 `tests/` 无对应 code）：`UNDEFINED_BLOCK`、`INVALID_COORDINATE`、`DIMENSION_UNMEASURABLE`、`CONSTRUCTION_GEOMETRY`、`UNITS_UNDEFINED` 仅出现在创建路径（`test_a_spec_to_drawing.py:80`、`test_c_document.py:84`）而非校验路径。

## 3.4 规格、工程域与注释：spec / docspec / drawing_frontend / stdlib / engineering/*

**规模**：14 个文件 / 3,464 行（`engineering/` 7 文件 2,185 行，其余 7 文件 1,279 行）

**职责与边界**

这一层把“意图”编译成“可执行的操作计划 + 可独立核对的需求”，再把意图落到 P&ID、建筑平面、回转体装配、外来工厂图与制图注释上。`spec.py` 是规格词汇表的唯一权威（kind、参数、tag 命名、requirements 形状）；`docspec.py` 是文档 → 事实 → 规格的确定性通道，带 page/line/section/snippet 溯源与引文校验；`engineering/{pid,architecture,mechanical}` 各自提供 `draw(ctx, spec)` 与 `read(doc)`，前者在既有事务内建实体，后者只读已保存图纸并产出 `critique`；`engineering/plant.py` 不建图，只提供 `describe`/`takeoff` 两个外来图分析入口；`dimension_display.py` + `appearance.py` 是“图上到底显示什么、是否可见”的只读判据，供验证器使用；`stdlib.py` 是被 `ops/library.py` 消费的行业数据表；`tabular.py` 把图上文字/属性导成 CSV/XLSX；`drawing_frontend.py` 只影响出图（PDF/PNG/SVG/viewer）里 DIMENSION 图形块的属性继承。

它不做：不读写磁盘、不持锁、不写 journal、不决定原子性——这些全部属于 `service.Runtime`/`store`/`engine`。`engineering.draw` 不自己开口子，几何一律经 `records.operations`（records.py:66-75）走 `engine.prevalidate` + `ops.REGISTRY`，因此预算、白名单、单位折算是外层的事；只有少数直接 `ctx.layout.add_*` 的实体例外（见“风险与可疑之处”）。调用方是 `Runtime` 的 `draw_from_spec`/`revise_drawing`/`extract_requirements`/`revise_from_document`/`read_engineering`/`understand_drawing`/`plant_takeoff`/`standard_library`/`export_drawing`，以及 `quality.assess`（quality.py:36-49）与 `validate/*`。它调用 `engine`（execute、plan_tags）、`ops.registry`、`model`/`units`/`geom` 与 ezdxf。

**文件清单**

| 文件 | 行数 | 角色 | 职责 |
|---|---:|---|---|
| spec.py | 283 | 核心 | 规格校验与编译：`plate`/`plan` → `{units, operations, requirements}`；`merge` 供修订 |
| docspec.py | 295 | 核心 | PDF/TXT/MD → 事实（规则抽取或 agent 引文）→ 板规格，带冲突/缺失/歧义判定与溯源 |
| drawing_frontend.py | 16 | 适配 | `Frontend` 子类：画 DIMENSION 时压入属性上下文，让块内 0/BYBLOCK 图形按标注自身图层出图 |
| stdlib.py | 330 | 数据 | 螺纹/间隙/沉孔/紧固件/轴承表、P&ID 符号图元、GD&T 特征、出处与 `index`/`table` |
| tabular.py | 125 | 工具 | 三张表（entities/pid_lines/bom）→ 行；CSV（防公式）与最小 XLSX 写出 |
| dimension_display.py | 191 | 核心 | 从已保存的标注图形块读“可见主值”，读不出就明确给出不可验证状态 |
| appearance.py | 39 | 支撑 | 保存态可见性：alive/invisible/父链/图层 off-frozen-不打印/有效透明度 |
| engineering/__init__.py | 1 | 支撑 | 声明读回只接收已保存几何 |
| engineering/records.py | 97 | 支撑 | 实体级领域记录（每域一个 XDATA app，超限落 XRECORD）、数值/点校验、`operations` 转发、制图约定 |
| engineering/mechanical.py | 485 | 核心 | 回转体零件三视图 + A3 图框标题栏 + 派生 BOM + 气泡 + 读回一致性 |
| engineering/mechanical_annotations.py | 418 | 核心 | 制造注释契约：生成尺寸并绑定源顶点/圆，读回按“显示精度”复核 |
| engineering/architecture.py | 462 | 核心 | 正交墙/门窗/楼梯/房间泛洪识别/尺寸链/房间与门窗表 |
| engineering/pid.py | 279 | 核心 | 端口连接式 P&ID 生成，读回用端口世界坐标重建图 |
| engineering/plant.py | 443 | 核心 | 外来工厂图：单位证据、聚簇、标签定位、比例判定、Manhattan 路由与电缆估算 |

**关键入口**

- `spec.compile_spec`（spec.py:257-270）——唯一编译入口，返回 `{units, operations, requirements}`；`plate` 转 `compile_plate`，`plan` 要求非空 `operations` 且原样透传 `requirements`，其他 kind 报 `SPEC_INVALID`。契约：返回的 `units` 已 `normalize_unit`，`operations` 里的 `tag` 即后续 `plan_tags` 的全部来源。
- `spec.compile_plate`（spec.py:147-254）——先 `check_plate` 收集 issues 再 `raise_if_issues`（130-136）映射 `SPEC_CONFLICT`/`UNITS_UNDEFINED`/`SPEC_INVALID`；计划以固定 6 条 `LAYERS`（spec.py:30-37）开头。tag 词汇表：`outline`、`hole.<bl|br|tr|tl>`（或 `hole.<i>`/自定义）、`cl.<hole>.h|v`（中心线，`center_marks` 默认开）、`dim.width|height|corner_radius|holes<g>.{dia,x,y,pitch_x,pitch_y}`、`sheet.trim|border`、`title_block`、`notes`。requirements 只由规格数值推导（166-176、188-226），与操作计划无耦合。
- `spec.merge`（spec.py:273-283）——深合并，列表整体替换、`null` 删键；`revise_drawing` 的全部语义建立在“列表不可局部编辑”上。
- `docspec.extract_facts`（docspec.py:108-145）——按 `PATTERNS`（32-48）逐行抽取；每条事实带 `page/line/section/text/match/span`。`verify_citations`（148-179）只做“引文在该页出现”（含跨行拼接）这一件事。
- `docspec.build_spec`（docspec.py:185-295）——把事实并成规格：单位必须唯一可定（196-203），数值冲突即 `SPEC_CONFLICT` 并放弃该字段（208-226），`overrides` 记为 `caller_override` 并撤销对应 issue（272-280），最后复用 `spec.check_plate` 做几何一致性（282-289）；`status` 取 ok/conflict/incomplete（291-292）。
- `engineering.records.put`/`get`（records.py:9-24 / 27-45）——每域一个 XDATA app `AGENTCAD_<DOMAIN>`，JSON 以 `sort_keys`、`ensure_ascii`、`allow_nan=False` 序列化后按 240 字符切块存 group 1000；原始 JSON 超过 8000 字节时改存实体自有 XRECORD，XDATA 只留哨兵 `$AGENTCAD_XRECORD_V1`。`get` 对损坏/非对象记录抛 `INVALID_DOMAIN_RECORD`。
- `engineering.records.operations`（records.py:66-75）——域内建实体的唯一正门：先 `engine.prevalidate(items, ctx.unit, ctx.policy)` 再 `REGISTRY[op].fn`，因此域代码与普通批次享受同一套参数/白名单校验。
- `engineering.records.drawing_conventions`（records.py:78-97）——把 `$DIMTXT=2.5`、`$DIMDSEP='.'`、`HIDDEN` 线型与 `DIM`/`HIDDEN` 图层写进文件头与标注样式。
- `engineering.mechanical.draw`（mechanical.py:286-359）/ `read`（362-479）——仅模型空间、仅 mm、字段白名单 `sheet/parts/fasteners/bom`（289-292）、最多 100 件（293-294）；读回按记录角色分派，并核对 BOM 单元格 tag `assembly.bom.r<item>c<col>`（433-456）与气泡指向（450-470）。
- `engineering.mechanical_annotations.draw`（mechanical_annotations.py:219-372）/ `check`（375-418）——生成时同时记录“几何快照 + 原生定义点 + 渲染文本 + 源顶点引用”，读回时四者都必须仍然成立。
- `engineering.architecture.draw`（architecture.py:154-400）/ `read`（403-462）/ `detect_rooms`（99-151）——房间面积由墙体实体+门窗阈值矩形（`ARCH_THRESHOLD`，182-183 设为 off 且不打印）泛洪算出，标签只作为待核对的声明。
- `engineering.pid.draw`（pid.py:106-194）/ `read`（197-279）——生成时端口坐标存进节点记录，读回用 `matrix44` 变换后按 1e-6 匹配线端，因此设备被移动会掉成 dangling 而不是“凭 id 认定连通”。
- `engineering.plant.describe`（plant.py:111-187）/ `takeoff`（296-443）——不依赖任何域记录，只吃外来几何与文字；输出带 `method` 与 `warnings` 的估算，`scale_check`（215-233）明确区分 scaled/schematic/insufficient_evidence。
- `stdlib.index`/`table`（stdlib.py:293-305 / 308-330）——`standard_library` 工具的数据源；未知 item/size 抛 `KeyError`，由 service 翻译成 `INVALID_SIZE`/`UNKNOWN_ITEM`（service.py:1272-1277）。
- `tabular.drawing_rows`（tabular.py:23-69）/ `write_table`（80-125）/ `csv_cell`（72-77）/ `value`（15-20）——`value` 只把“无前导零、≤15 位有效数字”的十进制串转数，其余保持文本。
- `dimension_display.read_display`（dimension_display.py:101-191）——状态机 `read|hidden|missing|unverifiable|unsupported_angular_format`；只有“唯一一个可见 TEXT/MTEXT + 十进制/分数格式 + 前后缀模板逐字匹配”才给 `value`，并给出 `resolution`（129、138、188-189）。
- `appearance.is_visible`（appearance.py:22-39）——`container=True` 时跳过自身透明度检查（DIMENSION 作为容器，只有继承 BYBLOCK 的子实体受其透明度影响）。
- `drawing_frontend.DrawingFrontend.draw_composite_entity`（drawing_frontend.py:5-16）——只对 DIMENSION 覆写，`push_state`/`pop_state` 包住父类实现。
- `docspec.load_document`（docspec.py:56-92）——三道拒绝：不存在/超 `max_document_bytes`（58-61）、PDF 无可抽取文字即 `PDF_NO_TEXT`（70-72）、后缀不在 pdf/txt/md（79-80）；`.txt` 按 `\f` 分页，标题行同时决定后续所有事实的 `section`（86-90）。
- `engineering.records.number`/`point`（records.py:48-57 / 60-63）——域内数值与点校验的唯一入口，拒绝 bool、非有限值、`positive` 时的非正数；域代码用它保证进入记录的都是有限坐标。

**数据结构与不变量**

- **规格 → 计划 → 标签是单向且可重算的**：`plan_tags` 只是 `{o["tag"]}`（engine.py:199-200），`draw_from_spec` 提交时把它和原始 `spec` 一起写进图纸（service.py:730-731），`revise_drawing` 用旧 `plan_tags` 判“陈旧”（service.py:759-761）。推论：任何带计划 tag 的实体在语义上都属于规格，其 handle 不承诺稳定，只有“未被删除”才稳定。
- **requirements 来自规格、不来自计划**：`draw_from_spec`/`revise_drawing` 用本次编译的 `requirements` 验证（service.py:738-739、767-768），存储规格则在下次验证时重新编译（service.py:665）；因此改计划不改规格不会放宽验收。
- **`dimension` 类需求比的是“显示值”**：`validate/requirements.py:114-127` 只在 `displayed is not None` 的标注里挑最接近的一条，`actual` 是文字解析值；几何实测值与 group 42 另由 `DIMENSION_RENDER_MISMATCH`/`DIMENSION_STALE` 判定（checks.py:510-532）。三者分立是本层注释可信度的基础。
- **协调模式只有三种结果**：新实体与旧实体几何等价 → 丢弃新实体、记 `unchanged`；同类型不同值 → `update_in_place` 就地改字段、保 handle，DIMENSION 额外重渲染（engine.py:129-138、165-196）；类型不同或多实体操作 → 删除重建（engine.py:115-128）。
- **领域记录与实体同生共死**：记录存在实体自身（XDATA/扩展字典），因此复制、块化、另存都随实体走；代价是 `read` 必须对“记录存在但键不全”的情况免疫——当前只在 JSON 层校验（records.py:40-43），键级校验缺失。
- **域记录只允许出现在模型空间**：`quality.assess` 扫描非模型布局与块内实体，发现 `AGENTCAD_<DOMAIN>` 一律记 `DOMAIN_LAYOUT_UNSUPPORTED`（quality.py:41-49），这也是三个 `draw` 都硬性要求模型空间的原因（mechanical.py:287、architecture.py:155、pid.py:107）。
- **制造注释契约必须自证完整**：`_contract_valid`（mechanical_annotations.py:42-89）要求 `enabled` 为 bool、`bool(annotations) == enabled`、`kinds == required.keys()`、`required` 非空当且仅当 enabled、源句柄含零件本体；`length.0` 与 `outer_diameter.0` 是最小覆盖集。任何一条不成立即 `MANUFACTURING_RECORD_INVALID`，而不是“跳过检查”。
- **数值比较用显示精度而非几何容差**：`_precision`（151-155）拒绝超过 8 位小数，`_nominal_matches`（158-163）按该精度格式化比较，`_same`（127-141）对 float 只给 ulp 级容差——目的是“显示 8 位小数的尺寸不能被微米级容差掩盖”。
- **可见性判定先看保存态**：`is_visible` 依次判 alive、`invisible`、父实体可见性、图层存在、图层 off/frozen/`plot=0`、有效 alpha；有效 alpha 由 BYLAYER（图层透明度）或 BYBLOCK（递归父实体）解析（appearance.py:12-19）。
- **表格导出永不产出公式**：CSV 对以 `= + - @` 或空白开头的字符串前置 `'`（tabular.py:72-77）；XLSX 文本一律 `t="inlineStr"`，数字才 `t="n"`，并转义 `_xHHHH_` 与控制字符（90-99）。
- **外来图分析的尺寸预算**：plant 限 20,000 个有界实体、5,000 条唯一线段（plant.py:113-116、307-310），并要求“推断单位为 mm”（300-301）、拒绝带 bulge 的多段线（248-252）；architecture 限 200 段墙与 40,000 格（185-186、107-108）；pid 限 2,000 个符号与管线（116-117）；mechanical 限 100 件（293-294）。
- **房间面积只有一处真相**：生成时声明面积与泛洪面积偏差超过 0.1% 就整批拒绝（architecture.py:313-314），读回时再按标签插入点重新泛洪并逐字重算标签文本（411-429，兼容旧 `m2` 写法）；`ROOM_LABEL_COVERAGE` 要求“每个检测到的面都有标签”（446-447）。
- **估算必须自带证据等级**：plant 的每条 run 都带 `status`（A=已路由且每段都有直接尺寸标注，B=已路由但部分段缺标注，C=未能在布置图定位）、`layout_m` 与 `length_source=layout_rmst`（plant.py:376-390、438），电缆行缺标签时记 `ELECTRICAL_TAG_MISSING` 警告而不是猜一个长度（413-417）。
- **域内拒绝都发生在建实体之前**：纸张/视图/剖面/特征/孔系等检查全在每个 `operations()` 调用之前完成（mechanical.py:75-109、218-226；architecture.py:190-211；pid.py:110-117），因此失败时事务里没有半成品实体需要回收。
- **库表是“画出来”和“查得到”的同一份数据**：`ops/library.py` 的绘制与 `stdlib.table` 的查询共用 `part_dims`/`hole_dims`/`PID`/`GDT`（library.py:659、547、139、438 对 stdlib.py:92-154、168-286），所以查表得到的标称尺寸就是块几何的尺寸；`tests/test_standard_sources.py` 逐行对照公开标准，改表必须同时改该测试与 `docs/standard-tables.md`。

**主要调用链**

1. 规格编译：`service.draw_from_spec`（service.py:715-742）→ `spec.compile_spec`（257）→ `compile_plate`（147）→ `check_plate`（91）逐项产出 issues → `expand_holes`（55）展开孔位 → 追加 `LAYERS`、轮廓、孔、中心线、标注、图纸边框、标题栏、备注 → 返回 `operations`/`requirements` → `engine.execute`（engine.py:50）逐条 `prevalidate`+`REGISTRY[op].fn`。
2. 修订：`service.revise_drawing`（743-772）→ `get_meta(part_spec)` → `spec.merge`（273）→ 重新 `compile_spec` → 单位一致性检查（758）→ `engine.execute(..., reconcile=True, previous_tags=plan_tags)`（760）→ 每步按 tag 命中走 `_reconcile_op`（109）→ 旧计划里消失的 tag 整批删除（89-95）→ 写回 `spec`/`plan_tags`（764-765）→ 用新 requirements 验证（767-768）。
3. 文档通道：`service.extract_requirements`（776-787）→ `docspec.load_document`（56）→ `extract_facts`（108）或 `verify_citations`（148）→ `build_spec`（185）→ 返回 `facts`/`spec`/`issues`/`traceability`；`revise_from_document`（789-827）在此之上补 `dimensions`/`name` 默认值，已有规格图走 `revise_drawing`，否则走 `draw_from_spec`，最后按 `validation.requirements` 逐条回填 `document_source`（819-826）。
4. 工程域生成：`apply_operations` 的 `draw_pid`/`draw_arch_plan`/`draw_mechanical_assembly`（ops/engineering.py:6、19、50）→ `engineering.<domain>.draw(ctx, spec)` → `records.drawing_conventions` → `records.operations` 建实体（每步都经 prevalidate）→ `records.put` 写领域记录 → 返回 `created` 列表，事务仍由外层 engine 收口。
5. 制造注释：`mechanical._part`（71）→ `mechanical_annotations.draw`（255）→ 每条尺寸先 `override.update/ render()` 再 `_rendered` 比对文本、`_source_values` 复测源几何（297-306）→ 契约写回零件记录（mechanical.py:257-259）→ 读回时 `check(doc, contract, handle)`（394）产出 `MANUFACTURING_*` issue 并进入 `critique`。
6. 读回与交付闸门：`service.read_engineering`（1300-1315）→ `pid/architecture/mechanical.read` → `critique`；`quality.assess`（quality.py:20-58）另加 `validate_file`、非模型布局扫描，把 critique 升级成 `category=engineering` 的 error，`passed` 由 error 数与 requirements_failed 决定（quality.py:51-57）。
7. 出图：`plot._render`（plot.py:278-282）、`viewer._svg`（viewer.py:426-428）、`export_drawing` 的 svg 分支（service.py:1054-1062）都构造 `DrawingFrontend` → 对 DIMENSION 压属性上下文 → 后端 Recorder/SVG。
8. 表格导出：`export_drawing`（service.py:1030-1033）→ `tabular.drawing_rows`（按 `table` 分派，pid/bom 分支现场 import 域 reader）→ `write_table`；`delivery` 用同一 `drawing_rows` 反向逐格核对导出文件（delivery.py:153-157）。
9. 显示值：`validate/features.py:182` 对每个 DIMENSION 调 `read_display` → `checks.py:510-518` 区分“读不到/读到了但与实测不符” → `requirements.py:114-127` 用显示值判定 `dimension` 需求。

**扩展点**

- 新增规格 kind：只在 `spec.compile_spec`（spec.py:257-270）加分支，并实现 `compile_<kind>`（返回 `{units, operations, requirements}`）与 `check_<kind>`（返回 issues）；务必给每个计划实体稳定 tag，否则 `plan_tags`/`revise_drawing` 的删除语义失效。service 无需改动（`draw_from_spec`/`preflight` 自动可用）。
- 新增领域：写 `engineering/<domain>.py` 暴露 `draw(ctx, spec)` 与 `read(doc)`；在 `ops/engineering.py` 注册薄壳 op；在 `quality.assess` 的域名三元组（quality.py:36-40）与 `service.read_engineering` 的 kind 分派（1304-1309）各加一行；用新的 `records.put(..., "<domain>", ...)` app 名。
- 新增标准件/符号：`stdlib.PARTS`/`PID`/`GDT` 加数据 → `provenance()` 加出处（stdlib.py:70-83）→ `ops/library.py` 加绘制 op → `stdlib.index()` 暴露（293-305）→ `docs/standard-tables.md` 记版本；`tests/test_standard_sources.py` 会逐行对照公开表。
- 新增导出表：`tabular.drawing_rows` 加分支（tablename 与列顺序），并同步 `service.export_drawing` 的 `table` 白名单（service.py:956）。
- 新增显示解析格式：在 `dimension_display._primary`/`_number` 加分支，并且只在“能确定是主值”时返回；确定不了就保留 `unverifiable`，因为 `checks.py` 会按状态给出 error/ warning 的差别。
- 新增注释类型：在 `mechanical_annotations.draw` 里加 `kind` + 源引用，再在 `_requirements`（92-108）与 `_contract_valid`（42-89）同步最小覆盖集，否则旧记录会被判 `MANUFACTURING_RECORD_INVALID`。

**风险与可疑之处**

- [中] 读回路径对记录键不做校验，坏记录变成 `INTERNAL_ERROR` 而不是可读 critique — `pid.py:211`（`record["tag"]`）、`architecture.py:416-418`（`record["area"]`、`record["number"]`）、`mechanical.py:385-392`（`record["views"]`、`record["profile"]`）；`records.get` 只保证 JSON 是 dict（records.py:40-43），`Runtime` 把 KeyError 统一包成 `INTERNAL_ERROR`（service.py:84-86），而 `quality.assess` 也走同一 reader（quality.py:36-49）。影响：手工改坏或跨版本记录会让“交付检查”本身报内部错误。建议：reader 用 `.get()` 并把缺失键记成 `*_RECORD_INVALID` critique。
- [中] `verify_citations` 只证明引文存在，不证明引文陈述了该值 — docspec.py:161-173 命中后直接 `citation_verified=True`，`value`/`unit` 完全不参与比对（176-178）；因此 agent 可以给一段真实存在但与数值无关的引文。影响：`citation_verified` 被当作数值依据时会误信。建议：命中后对 span 内数字做一次一致性校验，不一致降级为 `unverified`。
- [中] 规则抽取的数值模式无上下文约束 — `docspec.py:35` 把任意 `8 x 20` 当板宽×高，`45-46` 会把 `M6 holes` 抽成 6 个孔；只有同字段多值才触发冲突（219-224），单值场景被静默采信。影响：文档 → 规格给出错误尺寸且 `status=ok`。建议：要求同一逻辑行/同 section 内的“plate/板/part”上下文，或把这类命中标成 `review_required`。
- [中] 协调模式以 tag 为唯一身份，不校验 tag 来源 — `engine.py:69` 只看 `ctx.tags().get(tag)`，`service.py:759-761` 只用旧 `plan_tags` 判陈旧。影响：在规格图纸上用 `apply_operations` 给手工实体打同名 tag，下次 `revise_drawing` 会把它当规格实体改写或删除（推测：真实工作流里 agent 混用两条路径时会发生）。建议：把 plan 归属写进记录（如 `plan_gen`），reconcile 命中前校验归属。
- [中] 房间识别是“格子 × 矩形”双重循环 — architecture.py:107-116 的 `any(... for b in boxes)` 在内层，只限制了 40,000 格（108）却没限制 boxes 数量（每段墙一个、每个洞口一个，84-95）。按 200 段墙估算约 8×10⁶ 次矩形测试（推测：Python 下为秒级），而 `read` 每次调用都重跑（architecture.py:404），`read_engineering` 还会先跑 `validate_file`。建议：按 y 预分桶或先把墙栅格化。
- [低] 部分实体绕过 op 注册表直接建 — `architecture.py:274-280`（墙矩形/填充）、`303`（楼梯线）、`315`（房间文字）、`pid.py:174`（管线多段线）、`mechanical.py:338-346`（气泡文字/引线）直接调 `ctx.layout.add_*`，不经 `coerce`，因此 `tag`/`if_tag_exists`/`layer` 等公共参数校验不适用；可追踪性只由 `ctx.created` 与领域记录补回。影响：同一条能力有两套校验强度。建议：把这些收敛成 op，或至少集中到 records 的一个受检封装。
- [低] `records.put` 从大记录缩回小记录时遗留 XRECORD — records.py:18-24 只在 `len(raw) > 8000` 时写扩展字典，小分支不删同名 XRECORD；`get` 读 XDATA 不受影响，但文件里留下过期副本。建议：小分支删除同名 XRECORD。
- [低] `spec.SHEETS` 只有 A4/A3/A2（spec.py:29），而布局操作支持 A0–A4；规格里写 `"A1"` 直接 `SPEC_INVALID_VALUE`（125-126）。影响：大件板无法用规格直接出图。建议：复用 `ops/sheet.py` 的纸张表。
- [低] `dimensions` 只判断是否等于 `"none"`（spec.py:177-178），拼写变体（`"None"`、`"partial"`）会静默生成全套标注；同类问题见 `kind` 之外未校验的 `title_block` 字段值（237-242）。
- [低] `docspec` 的单位判定要求“全文档唯一”（196-203）：同一文档混用 `mm` 与 `in` 且没有单位声明时判 `UNITS_UNDEFINED`，即使逐值换算可行（`resolve` 里已有换算分支 214-218）。保守但会把可解析文档判成 incomplete。
- [低] `drawing_frontend` 依赖 ezdxf `Frontend` 内部（`draw_composite_entity` 可覆写、`ctx.push_state`，drawing_frontend.py:12-16）；升级 ezdxf 后若内部改名会静默退回“0/BYBLOCK 未解析”的出图，只有 `tests/test_dimension_appearance.py` 的跨后端一致性会暴露。
- [低] `tabular` 的 XLSX 列名用 `chr(65+col)`（tabular.py:91），超过 26 列会越界；当前所有表 ≤6 列（tabular.py:90、35），属潜在而非现存缺陷。
- [低] 机械读回要求“恰好一个”图框/标题栏/BOM（mechanical.py:429-432）；多画一个图框或第二张 BOM 会让整份 `critique` 变成单条 `SHEET_STRUCTURE`（只报计数、不带 handle），随后被 `quality.assess` 升级成 error 阻断交付。建议：附带多出来的 handle 与角色。
- [低] 标准件尺寸没有可信度字段，孔有 — `hole_dims` 每条结果带 `reference_status`/`note`/`component_references`（stdlib.py:121-154），而 `part_dims`（92-118）只有裸数值加一处 `standard` 字段；同一份库表里两种数据的“已核对/推荐”边界表达不一致，使用方只能靠 `provenance()` 的整条字符串判断。
- [低] plant 的单位推断在两处给出不同强度的结论 — `describe` 只把它当 warning 返回（plant.py:117-125、184），`takeoff` 则以同一推断为硬前置并抛 `UNSUPPORTED_UNITS`（300-301）；同一张图先 `understand_drawing` 得到“可分析”，再 `plant_takeoff` 直接失败，且没有提示是单位推断造成的。建议：让 `takeoff` 的错误 details 带上 `describe` 的 units 证据。

**测试对应**

- tests/test_a_spec_to_drawing.py — 板规格生成 + 独立验证（:15）、验证报告形状（:45）、导出重读一致（:56）、缺单位拒绝（:74）。
- tests/test_b_revision.py — 改宽度后边距保持（:16）、修订幂等且可逆（:57）、矛盾修订不动文件（:68）、外来图走低层 stretch（:75）。
- tests/test_c_document.py — PDF 抽取保留来源（:26）、文档→图逐项核对（:38）、中文 Markdown（:54）、冲突拒绝（:68）、缺失不臆造（:79）、引文校验（:96）、按文档修订已有图（:111）。
- tests/test_m_library.py — stdlib 符号/标准件/标准孔/GD&T 的绘制与查询（:24-234），多实体 op 重放（:234）。
- tests/test_standard_sources.py — 紧固件与轴承逐行对照公开表（:9、:27）、孔参考的“已核对/推荐”边界（:41）、provenance 范围与英寸换算（:59、:76）。
- tests/test_u_engineering.py — pid 连通性与移动设备（:10）、外来管网尺寸与缺失标签（:36）、改墙后重算房间（:60）、BOM/气泡发现缺件（:89、:150）、m² 标签兼容（:133）、圆弧 T 接（:173、:199）、电气标注未归属不算内部错误（:210）。
- tests/test_mechanical_annotations.py — 注释透明/缺失阻断交付（:37、:91）、亚微米与细名义值（:136、:151）、大坐标与 ULP（:169、:182）、重复直径逐处核对（:196）、精度上限（:217）、三视图不重叠与可关闭注释（:276）、记录损坏或不可见引线（:295）、大记录落 XRECORD（:312）。
- tests/test_dimension_display.py — 显示值读取的全部接受/拒绝路径：可见性（:20、:46）、科学计数（:63、:177）、缺图形不得反推（:91）、前后缀与数字边界（:110、:157）、堆叠公差与分数（:171、:189）、舍入增量（:195）、角度格式（:213、:244、:259）。
- tests/test_dimension_appearance.py — 同一标注在验证/SVG/PDF/viewer 下文字一致（:27），即 `DrawingFrontend` 的间接覆盖。
- tests/test_native_delivery.py — DWG 往返后域记录（:12）、大记录（:70）、八位小数（:86）、透明度（:154）、科学计数（:177）仍可验证，覆盖 `read_display` 与注释契约的持久性。
- tests/test_w_delivery.py — 交付用域 critique 阻断（:27）、非模型布局的域记录（:148）、坏 XLSX（:168）、多格式与源逐格一致（:12、:114）。
- tests/test_t_workflow_guards.py:234 — `tabular.value` 不把 `00042`/16 位数字转成数，CSV/XLSX 对 `=1+1`、`@NOW()` 做无公式化处理。
- tests/test_e_tamper.py — 外部改动后标注文字/实测值矛盾被检出（:59、:68），是 `dimension_display` + 需求比对的端到端回归。

## 3.5 接口与进程：cli、mcp_server、http_api、worker、sessions、progress、viewer、viewer_link、demo、discovery、mcp_config

**规模**：11 个文件 / 2950 行

**职责与边界**
这一层把同一个 `service.Runtime` 暴露成四种入口（CLI 子进程、MCP stdio、HTTP JSON、Python 直接调用），并附带三个"围绕 Runtime 的进程设施"：把同步 CAD 调用串行化的 `CADWorker`、把当前会话写进 `<root>/.agentcad/sessions/` 的 `sessions`、把调用进度写进 `activity.jsonl` 的 `progress`。
它不做几何、不做事务、不碰 ezdxf 实体：所有变更都落到 `Runtime` 的 34 个 `@tool` 方法（实测 `len(TOOLS) == 34`），本层只负责参数形状、传输、鉴权、串行化与呈现。
上游调用者是外部 Agent（Claude Code / Codex / Cursor）和 shell 用户；下游只有 `Runtime`、`DrawingStore`（只读）、`Policy`、`sessions/progress`，以及可选的 FreeCAD/AutoCAD 后端。
`viewer` 是唯一的"反向"组件：它不服务 Agent，而是读 workspace 给人类看，并额外承担 3D 零件版本记录（把 FreeCAD 产物写进 `DrawingStore` 的 journal）。
`demo` 是 `viewer` + `Runtime` 的固定脚本，`discovery` 是给 LLM 的检索排序，`mcp_config` 只生成客户端配置文本。

**文件清单**
| 文件 | 行数 | 角色 | 职责 |
|---|---|---|---|
| cli.py | 411 | 入口 | 31 个子命令的 argparse 定义；把命令映射到 `Runtime` 方法，按错误类别映射退出码，stdout 一条 JSON |
| mcp_server.py | 496 | 入口 | FastMCP stdio 服务；26 个手写工具适配器 + 9 个直挂 `Runtime` 方法 = 35 个工具；`full`/`discovery` 两种 profile；启动时建 `Session` |
| http_api.py | 165 | 入口 | stdlib `ThreadingHTTPServer`；`GET /v1/tools`、`POST /v1/<tool>`；Host/Origin/bearer-token 三段鉴权；全局执行锁 |
| worker.py | 19 | 核心 | 单线程 `ThreadPoolExecutor`；`asyncio.shield` 让取消只停止等待、不释放槽位 |
| sessions.py | 177 | 支撑 | 每进程一个 `<pid>.json` 会话文件；clientInfo → 友好名；`(pid, created)` 存活判定；`tag()` 供 journal/progress 标注来源 |
| progress.py | 212 | 支撑 | `activity.jsonl` 追加写；操作摘要 `op_brief`/`group_ops`；绝对路径脱敏 `scrub`；50 ms 节流与 512 KB 轮转 |
| viewer.py | 1159 | 核心 | 只读 web 查看器：Workspace（版本寻址、SVG/thumbs/3D）、ChangeFeed（stat 轮询 + SSE）、HTTP 路由、3D 版本落盘 |
| viewer_link.py | 66 | 支撑 | 只用标准库判断"这个 root 是否已有 viewer 在跑"，避免桌面图标为了开个链接加载 ezdxf |
| demo.py | 105 | 工具 | `agentcad demo`：在 `<workspace>/demo` 建带标记的目录，开 viewer，按 4 步画板并修订 |
| discovery.py | 60 | 工具 | 工具/操作/AutoCAD 命令别名的检索与打分（100/20/3 三档） |
| mcp_config.py | 80 | 工具 | 从当前解释器生成 Claude `.mcp.json`、Codex `config.toml`、通用 `mcpServers` 条目 |

**关键入口**
- `cli.run`（cli.py:238）— 唯一分发点；`setup`/`doctor` 在构造 `Runtime` 之前就返回（cli.py:240-241），`Runtime` 推迟到 cli.py:307 才导入，所以 `setup`/`doctor`/`viewer`/`mcp-config` 这些命令不必付 ezdxf 的导入成本。
- `EXIT`（cli.py:16）与退出码合成（cli.py:389-395）— 类别→码的唯一表；`validate` 未通过、`acad-audit` 未通过特判为 1。
- `cli.main`（cli.py:398）— `--compact` 决定缩进；除 `{"ok": True}` 外都打印一条 JSON 到 stdout。
- `mcp_server.build_server`（mcp_server.py:114）— 建 `Runtime(policy, auto_viewer=True)` 与 `CADServer`；26 个 `@mcp.tool`（mcp_server.py:118-430）+ 9 个 `mcp.add_tool(getattr(rt, name))`（mcp_server.py:432-434）。
- `CADServer.add_tool`（mcp_server.py:107）— 用 `@wraps` 把同步工具包成 `async`，全体交给 `self.worker.run`。
- `NotesClient._note`（mcp_server.py:76）/ `list_tools`（:88）/ `call_tool`（:92）— 借 `get_context().session.client_params` 读 clientInfo；任何异常吞掉，绝不影响请求。
- `DiscoveryServer`（mcp_server.py:97）与三个工具（mcp_server.py:445-478）— `search_tools`/`tool_schema`/`call_tool`；profile 由 `AGENTCAD_TOOL_PROFILE` 决定（mcp_server.py:435-439）。
- `serve_stdio`（mcp_server.py:481）— 设置全局 `SESSION`、`freecad.remember()`，再 `run("stdio")`；`python -m agentcad.mcp_server` 走 `main`（mcp_server.py:490）。
- `http_api.make_server`（http_api.py:144）— 绑定前先校验 token 形态（:146-148）与非回环 host 必须有 token（:149-151）；`serve` 从 `AGENTCAD_HTTP_TOKEN` 取 token（http_api.py:160）。
- `Handler.do_POST`（http_api.py:104）— 名字查 `TOOLS`、单 `Content-Length`、≤8 MB、`signature().bind()` 预检、全局锁内执行（http_api.py:129-135）。
- `http_api.make_handler`（http_api.py:35）— 每次 `make_server` 建一个 `threading.Lock`（:38）串行化全部请求；`_refuse` 会先读掉有限请求体再关闭连接，否则 Windows 的 RST 会吞掉 JSON 拒绝（:54-72）。
- `Viewer`（viewer.py:1053）— 前台阻塞形态（`serve`，:1092）；`start_background`（:1117）— MCP 首次变更时在 daemon 线程自启，端口 8770-8779 后回退任意端口；`open_or_serve`（:1147）— 桌面图标形态，先复用已有 viewer 再自启。
- `Workspace.snapshot`（viewer.py:310）— `(sha, 缓存副本)`；页面所有渲染与 3D 任务都从这里取文件。
- `Workspace.timeline`（viewer.py:279）— 把 journal 记录翻成 `{seq, tool, ok, version, available, before, steps}`；`export_html`（:560）生成可转发的自包含单文件。
- `Workspace.record_save`（viewer.py:494）— 3D 零件的"提交"：在 `st.lock()` 内 `checkpoint("saved")` + `journal(tool="save")`（:509-526），不经过 `Runtime`。
- `viewer_link.serves_root`（viewer_link.py:20）— 用 `GET /api/ping` 的 `root` 字段确认端口属于本 root，避免把别的 viewer 的链接交出去；`viewer_url`（:51）在其后拼 `#<drawing>`，`open_running`（:57）是桌面图标的复用入口。
- `sessions.Session`（sessions.py:58）— 进程内单例；`tag`（:45）/ `friendly`（:34）/ `alive`（:128）/ `list_sessions`（:156）分别是标注、命名、存活与聚合。
- `progress.Activity`（progress.py:114）— `emit`（:136）/ `step`（:148）/ `finish`（:156）；`begin`/`end`/`checking`（:194-212）用 `ContextVar` 绑定当前调用，所以嵌套调用天然不重复上报（service.py:239-241）。
- `worker.CADWorker.run`（worker.py:12）— 唯一的执行入口；`close`（:18）在全仓没有被调用。
- `discovery.search`（discovery.py:41）— 工具/操作/别名的打分检索。
- `mcp_config.server_entry`（mcp_config.py:15）/ `codex_toml`（:25）/ `generic_config`（:40）/ `mcp_config`（:46）— 四个客户端配置出口。
- `demo.prepare`（demo.py:51）/ `draw`（demo.py:60）/ `run`（demo.py:74）— 只清带标记的目录，然后按 `STEPS`（:20-42）逐步提交。
- `cli._json_arg`（cli.py:19）/ `_die`（cli.py:33）— 所有 `--ops`/`--spec`/`--requirements` 参数的唯一解析与用法错误出口。

**数据结构与不变量**
- 统一信封由 `@tool` 装饰器生成（service.py:67-107）；本层任何适配器都不得自己拼 `ok/tool/error`，只能在其上换形状（`export_drawing` 追加 `inline_image`、discovery 的 `call_tool` 解包 `[dict, Image]`）。
- 工具集合在进程内是静态的：`TOOLS[name] = fn` 在 import 时填充（service.py:105），HTTP 的 `/v1/tools` 由 `inspect.signature` 现场取参数名（http_api.py:100）。
- MCP 工具集 = `TOOLS` ∪ `{agentcad_status}` = 35（实测），但其中 26 个是**重写的适配器**，参数集可以与 `Runtime` 不同：`export_drawing` 多出 `inline_image` 这一只属于 MCP 的参数（mcp_server.py:354-356，`Runtime` 只有 `image_size`/`table`，service.py:944-945），`validate_drawing` 少了 `use_stored_spec` 与 `contour_layers`（mcp_server.py:259-273）。
- 单 worker：任何时刻只有一个 CAD 调用在执行（worker.py:10）；`asyncio.shield` 保证客户端取消只停止等待、不释放槽位（worker.py:14-16）。
- HTTP 鉴权严格先于读体与执行：Host（且拒绝带 userinfo/path/query 的畸形 host，http_api.py:77-86）→ `Origin` 存在即拒绝（:88-89）→ 单条 `Authorization` 常量时间比较（:90-93）。
- viewer 只读图纸：`Viewer.__init__` 先把 `writable` 算出来，随后强制 `policy.read_only = True`（viewer.py:1059-1062）；写只落在 `.agentcad/`（viewer-cache、3D 版本）与根目录新文件（`import_file`，viewer.py:185-212）。
- 版本 = sha256：`versions()` 只含 checkpoint 内容与当前文件（viewer.py:270-276），不在此集合的版本返回 `VERSION_NOT_FOUND`（viewer.py:319-321），所以页面永远显示真正落过盘的字节。
- 缓存副本内容寻址、只创建一次，并且只在本进程见到 `os.replace` 返回后才交出（viewer.py:322-336）——Windows 上共享违规的规避点。
- 会话：一进程一文件、退出删除（sessions.py:99-103）；存活要求 `(pid, created)` 匹配（sessions.py:128-135），因此 pid 复用不算会话；无 label 且无工具调用记录的会话不进列表（sessions.py:168-169）。
- 进度行 append-only、约 200 字节、任何异常都吞掉（progress.py:136-146）；每操作最多 50 ms 一行、首末必写（progress.py:148-154）；512 KB 轮转为 `.1` 只留一代（progress.py:129-134）。
- 会话文件写盘节流 2 s，除非 client/label 变了（sessions.py:87-89、76-85）。
- viewer 的 Host 白名单是 `{"127.0.0.1","localhost","::1"}`（viewer.py:1071、viewer_link.py:17）；两个 POST 另需 loopback `Origin` 与 `X-AgentCAD-Viewer: 1`（viewer.py:955-960），`--allow-remote` 直接把它们关掉（viewer.py:956、806-807）。
- CSS/JSON 注入防护：导出页把 `<` 转义成 `\u003c` 再填进模板，且数据最后替换（viewer.py:595-602），所以标签或错误信息里的占位符不会变成脚本。
- MCP 配置条目的形状固定：`{command: <sys.executable 绝对路径>, args: ["-m","agentcad.mcp_server"], env: {AGENTCAD_ROOT, PYTHONIOENCODING}}`，`read_only`/`allow_scripts` 只在为真时追加环境变量（mcp_config.py:15-22）；codex 版额外写 `default_tools_approval_mode = "approve"`（:32）。
- `/v1/tools` 的形状是 `{ok, tools: {名字: [参数名...]}}`（http_api.py:99-100），参数名来自 `TOOLS` 里未绑定函数的签名并去掉 `self`，因此它反映的是 **Runtime** 的参数集，不是 MCP 适配器的参数集。
- 进度事件形状固定：`run`（8 位 hex）/`n`/`ts`/`tool`/`drawing`/`state` + `sessions.tag()` + 各状态字段（progress.py:117-141），`state ∈ {working, checking, done, error}`；`done` 带 `changes`/`ops`/`total`/`passed`/`issues`（:168-185），`error` 的 message 截到 300 字并脱敏（:163-166）。
- 会话文件字段固定为 `pid/client/client_raw/version/label/started/last_seen/last_tool/drawing/created`（sessions.py:8-13、63-64）；`list_sessions` 只回吐其中 8 个键（:170-171）。
- `viewer.json` 是 `{url, pid}`（viewer.py:1082），`stop` 只在 pid 仍是自己时才删除它（:1111-1112）。
- `demo` 只清理由自己建的目录：非空且没有 `MARKER` 就抛 `FileExistsError`（demo.py:17、52-55），CLI 把它映射成 `FOLDER_NOT_EMPTY` 退出码 2（cli.py:256-257）。
- `discovery.search` 的契约是 `1 ≤ limit ≤ 30` 且 query 至少有一个词（discovery.py:43-44），结果按 `(-score, name)` 排序后截断（:60）；打分 100 = 命中 AutoCAD 别名、20 = 命中工具名、3 = 命中描述里的词（:56-58）。

**主要调用链**
1. CLI：`main`（cli.py:398）→ `run`（:238）→ `Policy`（:261）→ 延迟导入 `Runtime`（:307）→ `rt.<tool>(...)`（:310-386）→ `@tool` 生成信封 → `EXIT` 映射（:389-395）。`mcp` 分支不建 `Runtime`，直接 `serve_stdio`（:266-269）。
2. MCP：`agentcad mcp` → `ensure_workspace()`（cli.py:258-260）→ `serve_stdio`（mcp_server.py:481）→ `Session`（sessions.py:58）→ `build_server`（mcp_server.py:114）→ 每次 `call_tool` 先进 `NotesClient._note`（:92-94）→ `CADWorker.run`（worker.py:12）→ `Runtime` 方法 → 结果形状转换后回客户端。
3. HTTP：`serve`（http_api.py:159）→ `make_server`（:144）→ `make_handler`（:35）→ `do_POST`（:104）→ `_authorized`（:74，Host/Origin/token）→ 读体与 JSON 解析（:120-128）→ `bind` 预检（:131）→ `execution_lock` 内 `getattr(rt, name)(**kwargs)`（:132-133）→ 200 + 信封。
4. viewer 自启：`Runtime` 提交成功 → `VIEWER_TOOLS` 命中（service.py:96-99）→ `_viewer_link`（service.py:251）→ `start_background`（viewer.py:1117，端口 8770-8779 逐个试，`serves_root` 复核）→ 写 `viewer.json`（viewer.py:1079-1084）→ 结果带 `viewer_url = url#<drawing>`。
5. 变更推送：`ChangeFeed.run`（viewer.py:785）每 0.3 s `poll`（:715）→ `signature()` 三个 stat（:244-254）→ `_emit`（:699）→ SSE `_events`（:997-1016）把 `id:`/`data:` 写给页面；页面异常则靠 `Last-Event-ID` 续传。
6. 进度：`@tool` 入口 `_activity`（service.py:228）→ `progress.begin`（progress.py:194）→ 引擎每步 `progress.step`（progress.py:202）→ `finish`（:156）写 `done`/`error` → viewer `_read_activity`（viewer.py:666）按文件偏移增量读成 `activity` 事件。
7. 3D 版本：`_poll` 发现零件 mtime 变化（viewer.py:746-752）→ `record_save`（:494）在 `st.lock()` 内 `checkpoint("saved")` + `journal(tool="save")`（:509-526）→ `timeline` 把 `save` 记录当作一次提交展示。
8. 会话标注：`Session.note`（sessions.py:69）在 `tools/list` 与每次工具调用时被调（mcp_server.py:88-94）→ 更新 `CURRENT` → `store.journal` 合并 `sessions.tag()` 去掉 `session` 键（store.py:330），`progress.Activity.emit` 则保留全部键（progress.py:140）→ viewer `/api/sessions`（viewer.py:862-864）把它们列出来。
9. 演示：`agentcad demo`（cli.py:242-257）→ `install.workspace_root` → `demo.run`（demo.py:74）→ `prepare` 清目录 → `Viewer(...).start()` 占 8780+ → `draw` 用 `Runtime.apply_operations` 跑 4 步（demo.py:64-71）→ `linger` 600 s 后自停。

**扩展点**
- 新工具（要同时进 CLI/MCP/HTTP/discovery）：只写 `Runtime` 上带 `@tool` 的方法（service.py:302 起的模式），`TOOLS` 自动登记 → HTTP 自动可用、discovery 自动可搜；要改 MCP 侧参数就再写一个 `@mcp.tool` 适配器；不需要适配器时把名字加进 mcp_server.py:432-434 的九元组。
- 新 CLI 子命令：`build_parser`（cli.py:38）加 subparser + `run` 里加一个 `elif c == ...` 分支。
- 新 HTTP 路由：`Handler.do_GET`（http_api.py:96）或 `do_POST`（:104）；鉴权不要绕过 `_authorized`（:74）。
- 新 viewer 端点：加进 `ROUTES`（viewer.py:84）并在 `do_GET` 的 `if u.path == ...` 链（viewer.py:860-910）里给分支；静态资源走 `UI_TYPES` 白名单（viewer.py:89-90、846-858）。
- 新进度字段：`op_brief`（progress.py:58）加数值/字符串键，或 `Activity.finish`（:156）加汇总字段；文案在页面里，不在 runtime 里。
- 新客户端识别名：`sessions.CLIENTS`（sessions.py:27）；新 MCP 客户端配置：`mcp_config.mcp_config`（mcp_config.py:46）+ `cli` 的 `--client` choices（cli.py:182）+ `install.CLIENTS`。
- 新检索别名：`discovery.ALIASES`（discovery.py:8）；打分权重在 discovery.py:56-58。
- 新会话字段（例如客户端版本、任务阶段）：`Session.note`（sessions.py:69）与 `CURRENT` 新增键，并把它加进 `list_sessions` 的白名单（sessions.py:170-171），否则页面拿不到。
- 新的 3D 后端动作（例如剖切视图）：`Workspace._fc` 的 `action` 名（viewer.py:473-483）+ `FREECAD_JOBS` 的 lane（:93）+ `backends/freecad.run` 的动作分派。
- 新 HTTP 状态语义：`do_POST` 的 `TypeError` → `INVALID_ARGUMENTS` 与 `AgentCADError` 的 403/404/413 映射（http_api.py:134-135、:989-995）是两条独立分支，改错误码要同时改这两处。

**风险与可疑之处**
- [中] `mcp-config --client codex --write` 只是子串查 `[mcp_servers.<name>]` 后盲目追加，不解析 TOML、不读 BOM、也不删旧表（mcp_config.py:57-64）；`setup` 走的是另一条会 `tomllib` 校验并替换整表的路径（install.py:280-289）。影响：`mcp_servers.agentcad` 写成带空格的 `[ mcp_servers.agentcad ]` 时会被判为不存在，追加后 TOML 出现重复表而失效。建议：codex 分支复用 `install.write_codex_config`。
- [中] claude 分支假设已有文件的 `mcpServers` 是对象：`existing.setdefault("mcpServers", {})[name] = entry`（mcp_config.py:77），若原值是列表/字符串会抛 `TypeError`，而 CLI 只捕获 `AgentCADError`（cli.py:299-306），结果是裸 traceback 而非 JSON 信封。影响：`--write` 在损坏配置上崩，退出码 1 但无结构化错误。建议：写入前校验 `mcpServers` 类型并给 `BAD_CONFIG`。
- [中] MCP 适配器按**位置**把参数传给 `Runtime`（mcp_server.py:193、219、273、330、362-363），而 `Runtime` 的签名顺序在别处定义（service.py:411-414、637-640、941-945）。当前逐个对得上，但没有任何测试把两侧钉住（tests 只跑 `inspect_image`/`export_drawing` 等少数工具）。影响：`Runtime` 一次参数重排会让 MCP 静默传错值。建议：适配器改关键字传参，并加一条比对 `inspect.signature` 的测试。
- [中] `CADWorker` 没有超时、取消不会停止正在跑的线程、`close()` 在全仓无调用点（worker.py:10-19）。影响：一次卡住的 CAD 调用会让该 MCP 进程的**所有**工具调用永久排队；进程退出时 `ThreadPoolExecutor` 的 join 又会被这条线程拖住。建议：给工具调用加超时并把取消语义写进 MCP 层。
- [中] viewer 的轮询成本随文件数线性增长：0.3 s 一轮（viewer.py:643），每个文件 3 次 `stat`（:244-254、729-731），目录重扫 1 s 一次（:725），上限 `MAX_FILES = 2000`（:73）——满载约每 0.3 s 6000 次 stat。影响：大 workspace 上 viewer 自身抢 I/O，图纸提交的 `os.replace` 更容易撞上 `FILE_BUSY`。建议：按 mtime 分层或对目录做 inotify/ReadDirectoryChanges 退避。
- [低] `Viewer.__init__` 直接改传入的 `Policy`（`policy.read_only = True`，viewer.py:1062）。今天安全：`start_background` 先克隆了一份（viewer.py:1125），CLI 用完即退（cli.py:288）；但 `open_or_serve`（viewer.py:1147）用的是调用方的 policy。影响：任何"先开 viewer 再写图纸"的新调用方会静默变成只读。建议：在 `Viewer` 内部 `copy` Policy。
- [低] `agentcad viewer --open --allow-remote` 会静默忽略 `--allow-remote` 与 `--host`：`--open` 走 `open_or_serve(policy, a.port)`（cli.py:285-286、viewer.py:1147），只有非 `--open` 分支才传 `allow_remote`（cli.py:288）。
- [低] `/api/events?since=abc` 会在 `int()` 处抛 `ValueError`（viewer.py:872），落进 `do_GET` 的兜底 `except Exception` 变成 500 `INTERNAL_ERROR`（:917-919），而同类参数错误在 `/api/timeline` 上是 400（:884-889）。影响：参数错误被报成服务端错误。
- [低] `progress.scrub` 只认 Windows 盘符、UNC 和固定前缀的 POSIX 路径（progress.py:39-43）；`/data/secret/x.dxf` 这类路径会原样进 `activity.jsonl` 与 viewer 页面。影响：脱敏是不完整的，只是"常见路径不泄漏"。
- [低] `viewer_link.serves_root` 信任 `/api/ping` 返回的 `root` 字符串（viewer_link.py:35-37），而 `viewer.json` 位于 workspace 内、可由同工作区的其他进程改写（viewer.py:1079-1082）。影响：本机恶意进程若抢先占用该端口并回显正确 root，runtime 会把它的地址当成 viewer 链接交给用户。仅 loopback，风险有限。
- [低] 两个全局描述同一个会话：`mcp_server.SESSION`（mcp_server.py:69）与 `sessions.CURRENT`（sessions.py:31）；`Session.__init__` 会 `CURRENT.clear()`（sessions.py:62）。同一进程里若建两个 `Session`（`build_server` 被测试多次调用）后者覆盖前者，`SESSION.note` 会写进新文件。
- [低] 进度日志轮转在 `Activity.__init__` 里做 `os.replace` 到 `.1`（progress.py:129-134），每行 emit 又按路径重新打开，因此一次 run 的事件可能被切到两个文件；viewer 侧按"文件变小即从头读"兜底（viewer.py:674-677）。影响：并发调用下的时间线可能缺一小段。
- [低] `ChangeFeed` 每个事件列表只留最近 1000 条（viewer.py:707），SSE 完全依赖 `Last-Event-ID` 续传（:1008、:1011-1013）。影响：落后超过 1000 个事件的标签页静默漏事件，只能靠下次全量刷新补齐。

**测试对应**
- tests/test_g_interfaces.py — CLI 端到端（:28）、官方 MCP 客户端跑 stdio（:96）、`mcp_config` 生成的配置真能起服务（:126）、generic 配置（:159）、HTTP `/v1/tools` 与 `/v1/<tool>`（:182）、适配层无 CAD 逻辑的 AST 断言（:208-221）。
- tests/test_x_http_security.py — Host/Origin/Content-Type/Content-Length/Transfer-Encoding 的拒绝矩阵（:38-57）、`/v1/tools` 也需鉴权（:60）、非回环绑定在开 socket 前就被拒（:68）、token 形态与最小长度（:74）、带 token 的远程绑定仍鉴权（:81）、畸形 JSON/参数可恢复（:96）、早期拒绝的响应不被 Windows RST 吃掉（:105）。
- tests/test_o_viewer.py — timeline 与 sha 版本的映射与分页（:65、:83）、旧版本渲染与 diff（:101）、越界/未知版本拒绝（:122）、外部修改标记（:132）、ChangeFeed 的 added/changed/removed（:146）、HTTP 端点（:173）、SSE 推送（:208）、viewer 不写 workspace（:229）、Host 头拒绝（:391）、`--allow-remote` 必需（:401）、链接只信本 root 的 loopback viewer（:411）、IPv6（:446）、UI 白名单（:469）、拖入文件不覆盖（:524）、只有页面能 POST（:554）、只读/远程 viewer 不收文件（:574）、拒绝仍能拿到响应（:593）、3D 版本记录（:699、:735）、FreeCAD 缓存与 lane（:847、:961）、工作区缓存回退 temp（:1033）。
- tests/test_q_viewer_progress.py — 一次批量产生的 working/checking/done（:50）、失败事件的路径脱敏（:69）、`scrub` 单测（:77）、嵌套 create 只算一次 run（:84）、只读不写进度（:106）、`group_ops`（:114）、feed 与 HTTP 都供进度（:123）、日志轮转跟随（:144）、MCP 首次变更自启 viewer（:157）、`AGENTCAD_VIEWER=0` 退出（:180）、HTML 导出自包含且不泄漏路径（:194）、占位符不被替换成脚本（:321）。
- tests/test_q_sessions.py — 友好名映射（:32）、会话文件/`tag`/列表（:40）、journal 记录带来源（:60）、无会话时不打标（:71）、viewer 列出会话（:79）、`agentcad_status` 命名会话（:91）、pid 复用不算会话（:141）。
- tests/test_t_image_inspection.py — `full` 与 `discovery` 两种 profile 下的内联图片与元数据（:119-142、:145-163）。
- tests/test_t_quick_benchmark.py — MCP 启动后工具名包含 `search_tools` 等（:98），是本区域工具集规模的旁证。
- tests/test_l_live_session.py — `live` 动作经 CLI/MCP/HTTP 同一 `Runtime` 路径的行为（:119-433），覆盖 `autocad_live` 适配器。
- tests/test_q_install.py — `setup` 写出的配置能被真实启动（:130）、坏配置不动（:139）、codex 表替换（:255-276），是 `mcp_config` 的对照实现。

## 3.6 输出、图像与脚本：plot、delivery、images、image_dimensions、_image_runner、_script_runner、scripting、metadata

**规模**：8 个文件 / 1,520 行

**职责与边界**

本区域是"图出门"与"用户代码进门"的两条窄通道：`plot.py` 用纯 Python（zlib + 自写 PDF 对象表）把布局画成 PDF，用 Pillow 画成 PNG；`delivery.py` 在交付时把产物**重新读一遍**并与源图的快照逐项对比；`images.py` + `image_dimensions.py` 把工作区里的位图裁切、放大并核对调用方给出的标注读数；`scripting.py` 与两个 `_runner.py` 构成"静态检查 → 独立进程 → 严格重读"的脚本逃逸口；`metadata.py` 负责随 DXF 携带的 `AGENTCAD` 字典。

它不画几何、不选布局、不打分：布局选择在 `service._plot_layouts`（`service.py:1422`），质量分与交付门禁在 `quality.py:13/61`（见 3.3 节），本区域只提供被它们调用的两件东西——出图结果字典和 `verify_artifact` 的一致性判决。

SVG 分支**不在**本区域：`service.export_drawing` 直接用 `ezdxf.addons.drawing.svg.SVGBackend`（`service.py:1051-1065`），`delivery` 只做结构检查。

调用方只有 `service.Runtime` 的四个方法（`service.py:377`、`578`、`998`/`1019`、`1374`）、`validate/checks.py:217`（只借 `plot.plot_fonts`/`font_cmap` 查字形）和 `mcp_server.py:147-151`（把 `image_base64` 换成 MCP 图像块后返回）；`http_api.py:109-133` 按工具名转发，没有本区域的专门代码。适配层（`cli.py`/`mcp_server.py`/`http_api.py`）不导入本区域任何模块。

反向只有一个方向：本区域导入 `policy`/`store`/`model`/`tabular`/`guards`/`units`/`errors`，不导入 `service`；两个 runner 是被 `subprocess` 拉起的进程入口，只依赖 `ezdxf`、`PIL`、`numpy` 和 `agentcad.model`/`agentcad.scripting`。

**文件清单**

| 文件 | 行数 | 角色 | 职责 |
|---|---:|---|---|
| `plot.py` | 466 | 核心 | 纯 Python 出图：recorder 录制布局 → 页码/比例/偏移 → 自写 PDF 对象表（`_PdfPage`、`write_pdf`）或 Pillow 光栅（`_PngCanvas`）；SHX 中文替换（`_cjk_face`/`_cjk_styles`）与字形表（`plot_fonts`/`font_cmap`） |
| `delivery.py` | 199 | 核心 | `verify_artifact` 的六种格式复核；`dxf_snapshot` 把 handle 指纹、块/层/线型/样式表、扩展字典（含嵌套与环）、页面设置压成可比快照；`_xlsx_rows` 手解 XLSX 包结构 |
| `images.py` | 123 | 入口 | `inspect_image`：沙箱内读图、EXIF 校正、裁切/白底合成/旋转/缩放，返回 base64 PNG 与像素比例；可选把区域交给 numpy 脚本量测 |
| `image_dimensions.py` | 288 | 支撑 | 尺寸读数的结构校验、锚点对齐、按 (view, axis) 建图求解相对标高；`raster_probe` 暴露墨迹交叉候选；`evidence_preview` 生成"原图 + 标注"双联面板 |
| `_image_runner.py` | 151 | 适配 | 图像脚本子进程入口：注入只读 numpy 数组、`origin`、四个 `draw_*` 帮手，回收 `result`（图像存 `result.png`，数值截断 16000 字符） |
| `_script_runner.py` | 70 | 适配 | ezdxf 脚本子进程入口：`readfile(in.dxf)` → `exec` → `saveas(out.dxf)`，回收 `result` 与 stdout |
| `scripting.py` | 178 | 核心 | 语言注册表 `LANGUAGES`、AST 静态白名单 `check_script`、两种子进程启动器 `run`/`run_image`（`-I`、无 shell、超时） |
| `metadata.py` | 45 | 数据 | rootdict → `AGENTCAD` 字典 → XRECORD（JSON 按 250 字符切进 group 1）的读、写、枚举 |

**关键入口**

- `plot_pdf(doc, out, layouts, *, paper=None, orientation="auto", scale=None, monochrome=False, title="")`（`plot.py:286`）— 每个布局一页；返回 `{pages:[{layout, placement, paper_mm, scale, scale_text, empty, clipped?}], bytes, font_substitution?}`。`placement` 只有 `layout page setup`/`scale`/`fit` 三种（`:299/316`），调用方按它判断"这张纸是不是 1:1 出图"。
- `plot_png(doc, out, layout, *, size=1600, monochrome=False)`（`plot.py:436`）— 单布局光栅，长边 `size` 像素；返回 `{layout, pixels, empty, bytes}`。刻意不提供比例信息，仅供"看自己画了什么"（`:437-438`）。
- `_sheet(paper, orientation, need, wide)`（`plot.py:183`）— 幅面的唯一决策点：显式幅面 → 直接返回；`auto` → 在 A4…A0 里选能装下 `need+2*MARGIN` 的最小者，装不下就 `PLOT_TOO_LARGE`（`:201`）。
- `_layout_paper(lay)`（`plot.py:209`）— 图纸空间页设置换算成 `(x0, y0, w, h)`；无页设置返回 `None`，让该布局退回"按内容拟合"。
- `write_pdf(pages, out, title) -> int`（`plot.py:147`）— 自建 catalog/pages/内容流/xref/Info，内容流 zlib 压缩，先写 `*.agentcad-tmp` 再 `replace`（`:176-180`）；PDF 侧唯一的落盘函数（PNG 侧对应 `plot_png` 的同款临时文件+替换，`:458-461`）。
- `_render(doc, name, monochrome) -> (layout, player)`（`plot.py:276`）— PDF 与 PNG 共用的唯一渲染入口：选空间、读 `current_style_sheet` 决定单色（`:278-280`）、用 `DrawingFrontend` 录进 `Recorder` 并拿到可变换/可裁剪的 player。任何新后端都应该从这里接入，而不是另写一遍 `draw_layout`。
- `_cjk_styles(doc) -> str | None`（`plot.py:243`）— 图纸里有非 ASCII 文字就把 SHX 样式改成已安装的 CJK TrueType 并 `discard("bigfont")`（`:250-254`），返回被替换的字体名；**只改内存副本**，这是"出图不污染源文件"的唯一实现点。
- `plot_fonts(doc)` / `font_cmap(font)`（`plot.py:257`/`:265`）— 出图会用哪个字体、该字体有哪些码位；`validate/checks.py:217-238` 借它实现 `TEXT_PLOTS_AS_BOXES`，所以"检查用的字体"与"画图用的字体"是同一份判定（`plot.py:261`）。
- `verify_artifact(doc, target, fmt, exported) -> dict`（`delivery.py:135`）— 六个分支各自返回 `{check, passed, ...}`；任何不一致都被统一改写成 `DELIVERY_PARITY_FAILED`（category `validation`，`details={format, artifact}`，`:197-199`）。
- `dxf_snapshot(doc)`（`delivery.py:58`）— 交付一致性的比较单位：`units`、页眉子集、`metadata`、`extension_records`、每布局的 `{handle: fingerprint}`、每块的实体指纹与 BLOCK/BLOCK_RECORD 全 tag、四张符号表的全 tag、页面设置（去掉 `extmin/extmax`）。
- `_xlsx_rows(path)`（`delivery.py:78`）— 不信任 zlib/zip 结构与单元格内容：成员集必须恰好是那 6 个（`:84-87`）、`testzip` 通过（`:88`）、两处 relationship 与 workbook/Content-Types 逐属性比对（`:93-112`）、行号/列号必须是 `A1` 形式（`:116-122`）、禁止公式（`:123-124`）。
- `_extension_records(doc)`（`delivery.py:34`）— 从所有实体的扩展字典出发做图遍历（软链接可成环，`records` 兼作访问集，`:50`）；悬空句柄抛 `ValueError`（`:48`）。
- `inspect_image(policy, path, region, max_size, rotate=0, dimensions=None, code=None)`（`images.py:23`）— 只读、不写工作区；返回 `{source, source_sha256, source_size, region, rotate, preview_size, mime_type, image_base64, pixel_scale|script, dimension_evidence?, panels?}`。数字型脚本结果会**不带图**提前返回（`:63-66`），此时 `rotate`/`preview_size`/`image_base64` 都不存在。
- `check_dimensions(readings, box, source_crop=None)`（`image_dimensions.py:53`）— 最多 12 条读数（`:54`）、字段封闭（`:58`）、只支持水平/垂直（`:84`）；输出 `{checks, groups, pixel_tolerance, consistent, semantics_verified:false, note}`，`consistent` 在三处被拉低：端点未对齐（`:182-183`）、隐式比例区间为空（`:194-195`）、同一面名被锚在不同标高（`:196-198`）。
- `raster_probe(crop, box, row)`（`image_dimensions.py:113`）— 在尺寸线中心 ±16 px 的条带里找连续墨迹，沿尺寸方向最多扫 4096 源像素（`:130`），对比度不足回退到最小值（`:144`）；**不**识别箭头、不修改图（`:116`）。
- `evidence_preview(crop, box, rotate, max_size, evidence)`（`image_dimensions.py:237`）— 双联面板，总宽高受 `max_size` 约束（`:239-241`），源坐标刻度线穿过旋转与缩放（`preview_point`，`:224`）。
- `_measure(policy, code, crop, origin) -> (dict, Image | None)`（`images.py:98`）— 把裁切区域按源分辨率存成 PNG 交给 `run_image`；只有脚本返回图像时才读回 `result.png`（`:113-116`），并把 `script_lines/stdout/traceback` 原样带进错误（`:109-112`）。
- `check_script(code, language="ezdxf")`（`scripting.py:81`）— `SCRIPT_EMPTY`/`SCRIPT_SYNTAX`/`SCRIPT_REJECTED` 三种拒绝，消息带行号与提示（`:93-96`）。
- `run(source, code, job, timeout)` / `run_image(code, crop, origin, job, timeout)`（`scripting.py:133`/`:156`）— 写作业目录 → `subprocess.run([sys.executable, "-I", runner, job], shell=False, stdin=DEVNULL, timeout=...)` → 读 `result.json` 并补 `seconds`；`SCRIPT_TIMEOUT`/`SCRIPT_CRASHED` 两个失败码（`:143-150`、`:171-175`）。
- `LANGUAGES["ezdxf"] / LANGUAGES["image"]`（`scripting.py:65-78`）— 唯一的语言注册表：`imports`/`attrs`/`private` 被静态检查读取（`:84`），`imports`/`attrs` 又被 runner 的 import hook 读取（`_image_runner.py:95`），`hint` 只出现在拒绝消息尾部（`:96`）。加语言时四个键各归各的消费方，改一处必须同步另一处。
- `SAFE_BUILTINS`（`_script_runner.py:20-26`）— 两个 runner 共享的内建白名单（图像 runner 直接 `from agentcad._script_runner import SAFE_BUILTINS`，`_image_runner.py:93`），所以"给脚本减少内建"只有一份定义；`__build_class__` 单独补回（`_script_runner.py:47`、`_image_runner.py:113`）。
- `_PdfPage` / `_PngCanvas`（`plot.py:38`、`:341`）— 两份 `BackendInterface` 实现，把同一份录制结果落成矢量内容流或像素。它们的空实现（`draw_image`/`set_background`/`configure`/`enter_entity`/`exit_entity`）就是当前"不支持的能力清单"，补哪一项等于扩一次出图语义。
- `_as_image(value)` / `_drawing(canvas, paint)`（`_image_runner.py:28`、`:46`）— 图像脚本唯一的出图途径：数组→RGB 的归一化（bool、非 uint8、单通道都在这里处理），以及"在副本上画"的语义（`:49-52`）。四个 `draw_*` 都是它的一行包装（`:57-74`）。
- `set_meta(doc, key, value)` / `get_meta(doc, key, default, *, strict=False)` / `all_meta(doc)`（`metadata.py:15`/`:27`/`:41`）— `strict=True` 把"存了但不是 JSON"升级为 `INVALID_METADATA`，`service._drawing_validation` 正是用它让存储的 part_spec 语义 fail-closed（`service.py:662-666`）。

**数据结构与不变量**

- 单位换算只有一处常量：`PT = 72/25.4`（`plot.py:28`）；`plot_pdf` 报告里的 `paper_mm` 以 mm 计，页面 MediaBox 以 pt 计，`delivery` 用 `mm * 72/25.4` 反算并只容忍 0.3 pt 偏差（`delivery.py:170-172`）。任何新的出图后端必须同时满足这两个数字。
- 幅面只能来自 `ops.sheet.PAPER`（`plot.py:191`）或 `auto` 里选出的 ISO A 系列（`:195-200`）。"比例"在 fit 模式下必须落在 `STANDARD_SCALES` 内或恰好等于拟合值（`:313`），这就是测试断言 `scale_text ∈ {1:1, 2:1, 5:1}` 的依据（`tests/test_k_pdf.py:22`）。
- 内容越界不是错误而是一个标记：`clipped=true`（`plot.py:319-322`）——但 `delivery` 把 `clipped` 视为交付失败（`delivery.py:178-179`），所以"能被导出的"严格宽于"能被交付的"。
- 出图不得改动磁盘图纸：`_cjk_styles` 只改内存副本里的样式（`plot.py:243-254`，被 `tests/test_k_pdf.py:71-73` 固定）；代价是"打印字高/字形"类警告必须在绘图**之前**算（`service.py:1004`）。
- DXF 一致性以句柄为键（`delivery.py:67-69`）：保存/重载后 handle 必须不变，任何重编号都会让交付失败。扩展字典的可见内容不止句柄——`_extension_records` 存在的理由就是"只保句柄无法证明被引用的制造记录还在"（`:37-39`）。
- 图像坐标的唯一口径是"EXIF 方向校正后的源像素"（`images.py:46-48`、`image_dimensions.py:5`）；`region` 必须非空且落在源图内（`images.py:54`），预览长边 ≤ `max_size`、PNG ≤ 2 MB（`:78-87`），输入 ≤ 32 MB 且 ≤ 3200 万像素（`:17-18`、`:44-45`）。
- 脚本的三道门顺序不可交换：policy 开关（`service.py:569`）→ 静态检查（`service.py:572`）→ 子进程（`scripting.py:141`）；子进程里再叠一层 `SAFE_BUILTINS` + import hook（`_script_runner.py:45-47`、`_image_runner.py:111-112`），并冻结输入画布（`_image_runner.py:110`）。
- `placement == "layout page setup"` 只在不给 `paper`、不给 `scale` 时成立（`plot.py:294`）：显式指定幅面或比例会把这个图纸布局拉回 fit/scale 分支（`:300-316`），于是 1:1 的纸被重新排版。调用方要判断"这张纸是否原样出图"，必须看 `placement` 而不是看 `layout` 名字。
- 能导出的格式多于能交付的格式：`export_drawing` 认 8 种（`service.py:967-968`，含 png/dwg），`deliver` 只认 6 种（`quality.py:67`）。这不是疏漏（png 是给 agent 看的预览、dwg 依赖外部 AutoCAD），但两处集合必须同时被改，否则会出现"导出成功、门禁不认识"。
- 元数据的字节表示是规范化的：`json.dumps(ensure_ascii=True, sort_keys=True)` + 每 250 字符一个 group 1（`metadata.py:12,23-24`）；读取时只拼 group 1（`:33`），因此任何外来工具插入的 group 1 都会让整条元数据变成"非法"。XRECORD 句柄**不稳定**（每次写都新建），任何以句柄为键的东西都不能指向元数据。
- 作业现场只是一次性的：`run_script` 的 job 建在 `store.dir/scripts/job-<epoch-ms>`（`service.py:576`）并在 `finally` 整目录删除（`:589`）；图像脚本的 job 建在系统临时目录（`images.py:105`）同样 `finally` 删除（`:117-118`）。因此 runner 的 `out.dxf`/`result.json`/`result.png` 只能当中间件读，不能当持久物引用；两者都用 `ignore_errors=True`，Windows 上的句柄残留会静默留下目录。
- 进程失败语义只有两级：脚本自身异常也会写出 `{ok:false}` 的 `result.json`（`_script_runner.py:60-66`、`_image_runner.py:120-126`），只有 runner 自己崩了才没有该文件——`scripting.run` 正是用"文件不存在"区分 `SCRIPT_CRASHED` 与 `SCRIPT_FAILED`（`scripting.py:146-150`、`:171-175`）。任何新 runner 必须保持这个契约，否则错误会从"输入错误"降级成"内部错误"。
- PDF 不携带字体：页面 `/Resources` 是空字典（`plot.py:162`），因为 ezdxf 的 `Recorder` 没有 `draw_text`（1.4.5 的方法表），文字在录制阶段已经变成填充轮廓。后果是 PDF/PNG 里的文字不可搜索、不可选、与系统字体无关——这是"零外部依赖"的代价，也解释了字形检查为何要自己拿 `font_cmap` 查码位，而不是问 PDF。

**主要调用链**

1. PDF：`export_drawing(format="pdf")`（`service.py:997`）→ `_plot_layouts`（`:1422`，默认"有内容的图纸布局，否则 Model"）→ `_plot_text_warnings`（`:1437`，在改字体前）→ `plot.plot_pdf`（`plot.py:286`）→ `_cjk_styles`（`:288`）→ 逐布局 `_render`（`:276`：`DrawingFrontend` + `Recorder`，`:282`）→ `player.bbox()`（`:292`）→ 选幅面（`_layout_paper` `:294` / `_sheet` `:302-313`）→ `player.transform(布局矩阵 × PT)`（`:317`）→ 越界判断与 `crop_rect`（`:319-322`）→ `player.replay(_PdfPage)`（`:324`）→ 拼页 → `write_pdf`（`:331`）→ 回填 `PRINTED_TEXT_TOO_SMALL` 警告（`service.py:1007-1014`）→ journal（`:1015`）。
2. PNG：`export_drawing(format="png")`（`service.py:1018`）→ `layout=="*"` 多布局直接拒绝（`:1021-1022`）→ `plot.plot_png`（`plot.py:436`）→ `_render`（`:441`）→ 翻 y 轴并拟合（`:450-451`）→ `_PngCanvas` 重放（`:456`，填充走逐轮廓 XOR 的奇偶规则，`:367-390`）→ 双倍超采样后 LANCZOS 缩半（`:457`）→ 临时文件替换（`:459-461`）。
3. 交付：`deliver_drawing`（`service.py:1374`）→ `quality.deliver`（`quality.py:61`）→ 目录必须为空（`:72-73`）→ `assess` + `score`（`:77-78`）→ 逐个格式 `export_drawing`（`:90`）→ `verify_artifact(doc, target, fmt, result)`（`delivery.py:135`）→ 格式分支（dxf `:137`：重读 + 双快照 + `audit()`；json/csv/xlsx `:147-161`；pdf `:162-181`：页数/MediaBox/是否还有绘制算子/是否 clipped；svg `:182-193`：根元素与 viewBox + 至少一个几何元素）→ 源文件 sha 复核（`quality.py:106`）→ manifest v2（`:108-119`）。
4. 看图：`inspect_image`（`service.py:363`）→ `images.inspect_image`（`images.py:23`）→ `policy.resolve(suffixes=…)`（`:32`）→ `Image.open` + `exif_transpose`（`:41-47`）→ 裁切、白底合成（`:56-59`）→ 二选一：`_measure`（`:62`，经 `scripting.run_image` → `_image_runner.main`）或 `check_dimensions` + `evidence_preview`（`:69-75`）→ 旋转（`:70-72`）→ 缩放/双联面板（`:76-80`）→ base64（`:83-95`）。MCP 侧把 `image_base64` 弹出换成图像块（`mcp_server.py:148-150`）。
5. 脚本：`run_script`（`service.py:563`）→ `check_scripts_allowed`（`:569`）→ `check_script`（`:572`）→ `scripting.run`（`:578`→`scripting.py:133`）→ `subprocess.run(-I)`（`:141`）→ `_script_runner.main`（`_script_runner.py:36`：`readfile(in.dxf)` → 受限 builtins 环境里 `exec` → `saveas(out.dxf)` → `result.json`）→ `read_dxf(out.dxf)`（`service.py:583`）→ 实体预算（`:591`）→ `diff_docs`（`:594`）→ `commit_bytes` + journal（`:611-614`）→ 提交后独立校验（`:616`）。
6. 元数据写入：`draw_from_spec`/`revise_drawing` → `set_meta(doc,"part_spec")` + `set_meta(doc,"plan_tags")`（`service.py:730-731`、`:764-765`）→ rootdict `AGENTCAD` 字典 → 旧键 `remove` + 新 XRECORD 分块（`metadata.py:20-24`）。读取有两条：`read_drawing` 的无条件 `all_meta`（`service.py:398`、`:407`）与校验/交付路径的严格读取（`:662`）。
7. SVG（只在本区域擦边）：`export_drawing(format="svg")` 完全不走 `plot.py`，而是 `svg.SVGBackend` + `layout.Page(0, 0, mm, margins=5)`（`service.py:1051-1065`）；交付侧只验根元素、`viewBox` 与"至少一个几何元素"（`delivery.py:182-193`），**不**比对几何，所以它是六种交付格式里保真度最弱的一份（`scope` 字段自己也这么说，`:192-193`）。

**扩展点**

- 新增交付格式（例如 DWF）：三处同改——`quality.py:67` 的格式集合、`service.export_drawing` 的 suffix 表（`service.py:967`）、`delivery.verify_artifact` 加一个分支并返回 `{check, passed, ...}`（`delivery.py:135`）。只改前两处会让该格式"能导出但不过门禁"；`deliver` 会直接以 `BAD_PARAMETER` 拒绝（`quality.py:63-69`）。
- 新增出图后端：实现 `BackendInterface`（`_PdfPage`/`_PngCanvas` 是两份现成模板，`plot.py:38`、`:341`），复用 `_render` + `transform` + `crop_rect` 三件套；`draw_image`/`set_background` 目前都是显式空实现（`:125-126`、`:414-421`），要做光栅底图就在这里补。
- 新增/调整中文字体候选：`CJK_FONTS`（`plot.py:220`）与 `STANDARD_SCALES`（`:222`）是唯二的可调表；字形检查会自动跟随（`plot_fonts`，`:257`），不需要第二份名单。
- 新增脚本语言：`scripting.LANGUAGES` 加条目（`scripting.py:65-78`，`imports`/`attrs`/`private`/`hint` 四个键）、写一个 `_xxx_runner.py`、在 `check_script` 的调用点传入語言名。**必须**同时把新 runner 加进 `tests/test_security.py:127` 的 `exec_sites` 白名单，否则全仓扫描会失败（该测试要求每个文件恰好一处 `exec(compile(code, "agent_script.py", "exec"), env)`，`:134`）。
- 新增一种尺寸读数或证据字段：改 `check_dimensions` 的字段白名单（`image_dimensions.py:58,71-78`）、`MCP` 里的参数说明（`mcp_server.py:128-132`）与 `service.inspect_image` 的 docstring（`service.py:370-375`），再在 `evidence_preview` 里画出新锚点（`:269-279`）。
- 新增随图元数据键：直接用 `set_meta(doc, key, value)`，无需注册；但它会自动进入 `dxf_snapshot["metadata"]`（`delivery.py:65`）与 manifest 的 `validation`，并且**只要写一次就永久占一个 XRECORD 位置**（见风险 1）。
- 新增一条出图警告：若警告来自几何（字高、字形、引线），写在 `validate/checks.plot_text_issues`（`validate/checks.py:209`），`service._plot_text_warnings` 会自动继承并让"出图布局"也覆盖到（`service.py:1437-1450`）；若警告来自出图结果本身（页数、比例、clipped），追加到 `service.export_drawing` 的 `warn` 组装处（`service.py:1007-1014`、`:1023`）——两处的语义不同，不要混用。
- 让 PNG 进交付：`quality.py:67` 加 `"png"`、`delivery.verify_artifact` 加一个 png 分支（当前落到 `Unknown delivery format`，`delivery.py:194-195`），并解决命名冲突——`quality.py:89` 固定用 `<stem>.<fmt>`，而 PNG 只能一布局一文件，多布局时必然互相覆盖，需要先把文件名模板改成带布局后缀。
- 调整中文或比例策略：只改 `CJK_FONTS`（`plot.py:220`）与 `STANDARD_SCALES`（`plot.py:222`）两张表；如果改的是"哪些样式算 SHX"，则改 `_plotted_with_cjk`（`:239-240`），因为字形检查与出图替换共用它。

**风险与可疑之处**

- [高] `set_meta` 覆盖已有键会泄漏旧 XRECORD：`DICT` 用 `rootdict.add_new_dict("AGENTCAD")` 创建，而 ezdxf 的 `add_new_dict(..., hard_owned=False)` 是默认值，所以 `d.remove(key)` 只删字典项、不删实体（`metadata.py:19-22`）。实测：同一键连写 20 次 → 文件里留下 20 个 XRECORD，大小 18.7 KB → 28.4 KB，旧 payload 明文仍在（`b"OLD_MARKER" in bytes` 为真）。影响：每次 `revise_drawing`/`draw_from_spec` 都把上一版 `part_spec` 的完整 JSON 追加进图纸（模型看不到，但文件在泄露历史与膨胀）；`dxf_snapshot` 只读被引用的元数据（`delivery.py:65`）、`_extension_records` 只走实体扩展字典（`:40`），所以**交付门禁无法发现**。建议：`add_new_dict("AGENTCAD", hard_owned=True)`，或在 `remove` 前 `doc.objects.delete_entity(old)`。
- [中] 交付目录一旦有半成品就无法重试：`deliver` 在质量门禁通过后才 `mkdir`，随后边导出边校验（`quality.py:86-105`），任一格式失败都会留下非空目录，而下一次调用被"目录必须为空"直接拒绝（`:72-73`）。`PDF clipped` 这类必然失败的情形（`delivery.py:178-179`）正好走这条路。影响：一次偶发 `DELIVERY_PARITY_FAILED` 之后必须人工清目录才能再交付。建议：产物先写同级临时目录，全部校验通过后整体 rename。
- [中] `run_script` 的返回值没有上限，与图像脚本不对称：`_image_runner.py:139-142` 把 `result` 截到 16000 字符并置 `result_truncated`，`_script_runner.py:54-59` 只做 `json.dumps` 可行性检查，`service` 原样回传并写进 journal（`service.py:598`、`:614`）。影响：脚本里一句 `result = msp.query("LINE")...` 就能把巨量文本灌进模型上下文与 journal；`run_script` 对 `code` 长度也没有任何预算（全仓无 `len(code)` 检查）。建议：与图像路径对齐截断，并给 `code` 与 `result` 各设字节上限。
- [中] 两个 runner 都假定 `agentcad` 已安装：启动参数是 `sys.executable -I <runner>`（`scripting.py:141`、`:165`），`-I` 会让 `sys.path` 既不含脚本目录也不含 `PYTHONPATH`（本机 Python 3.13 实测确认），而 runner 必须 `import agentcad.model` / `agentcad.scripting`（`_script_runner.py:42`、`_image_runner.py:93-94`）。影响：源码树 + `PYTHONPATH=src` 的运行方式下，`run_script` 与 `inspect_image(code=...)` 一律落到 `SCRIPT_CRASHED`（category `internal`，`scripting.py:147-150`），错误信息只说"进程没留下结果"。建议：runner 自带路径引导，或错误里显式区分"import 失败"。
- [中] `plot_rotation` 只换了纸的宽高，没有旋转内容：`_layout_paper` 在 `plot_rotation ∈ {1,3}` 时交换 `w/h`（`plot.py:215-217`），但 `plot_pdf` 的图纸空间矩阵是纯平移 `Matrix44.translate(-x0,-y0,0)`（`:297-299`）。推测（未用真实 90° 页设置的图核对）：竖排纸上的内容会横着印。建议：补一张 `plot_rotation=1` 的用例，或在该分支加 90° 旋转矩阵。
- [低] 光栅图与打印样式表被静默丢弃：`draw_image`/`set_background` 是空实现（`plot.py:125-132`、`:414-421`），`monochrome` 只认 `monochrome.ctb`（`:278-280`）。影响：带底图或自定义 CTB 的图纸，PDF/PNG 与 AutoCAD 输出不一致，而报告里没有任何提示。
- [低] `_cjk_face` 扫全图而不是被出图的布局（`plot.py:229-233`）：任一布局出现中文，就把**所有** SHX 样式换成 TTF，纯 ASCII 的那张纸也跟着换字体观感；`plot_fonts` 同理（`:257-262`）。影响：字形检查与出图观感一致，但多布局图纸的单页预览会与源意图有细微差异。
- [低] XLSX 校验用 `chr(65 + col)` 拼单元格地址（`delivery.py:121`）：超过 26 列就会把合法文件判成"位置与源不符"。当前 `entities` 表只有 4 列（`tabular.py:54`），`pid_lines`/`bom` 是 6 列（`:27`、`:35`），所以只是潜在——但 `deliver` 固定用 `entities`（`quality.py:90` 不传 `table`），换表就会暴露。
- [低] `pypdf` 在 try 内部导入（`delivery.py:163`）：缺包时报 `DELIVERY_PARITY_FAILED`（category `validation`），而不是像 SVG 那样有 `DEPENDENCY_MISSING` 的语义（`service.py:1055-1058`）；另外 `pyproject.toml:12` 已把 pillow 列为核心依赖，`service.py:1058` 的"pip install .[preview]"提示与 `pyproject.toml:18` 的 extra 都是过时的。
- [低] 扩展字典的悬空句柄被改写成"产物不一致"：`_extension_records` 抛 `ValueError("Missing extension record")`（`delivery.py:48`），由 `:197-199` 统一包装成 `DELIVERY_PARITY_FAILED` 并指向 `target.name`。影响：真正坏的是**源**图纸时，诊断却指向导出物。建议：把源快照与产物快照的失败分开命名。
- [低] JSON/CSV 的一致性范围只是"投影"：JSON 导出只写模型空间（`service.py:1067-1068`），CSV 只写 `entities` 表；`verify_artifact` 比的就是这份投影（`delivery.py:148-161`）。影响：图纸空间里的实体丢失不会被这两种格式发现（DXF/SVG/PDF 分支能发现一部分），manifest 的 `passed` 容易被读成"整图一致"。
- [低] 大填充的 PNG 预览是逐轮廓开销：`_PngCanvas._fill` 对每个轮廓单独建一张 `1` 位图再 `logical_xor` 贴回（`plot.py:381-389`），注释明说是为上千个小岛准备的。影响：密排 hatch 的 PNG 出图时间随轮廓数线性增长；PDF 路径不受影响（一次 `f*`，`:119-123`）。
- [低] 图像脚本的作业目录是全仓唯一写到 `AGENTCAD_ROOT` 之外的路径：`tempfile.gettempdir()/agentcad-img/<uuid>`（`images.py:105`），注释解释了 Windows 260 字符限制；`agentcad-img` 父目录从不清理，`TMPDIR` 不可写时表现为通用的 `SCRIPT_CRASHED`。它确实只写临时区、`finally` 里删作业目录（`:117-118`），所以不违反"不写工作区"，但绕过了沙箱的路径断言。
- [低] dxf 快照与 `finalize` 只靠两个键的排除维持对齐：`export_drawing` 在 dxf/csv/json 分支先 `finalize(doc)`（`service.py:1028`）再写盘，而期望快照是在 `finalize` 之前算的（`quality.py:77`、`:95`）。现在能对齐，是因为 `dxf_snapshot` 的 `header` 白名单不含 `$EXTMIN/$EXTMAX`、`pages` 又显式剔除 `extmin/extmax`（`delivery.py:62-74`），而 `finalize` 恰好只写这两处（`store.py:118-133`）。影响：`finalize` 一旦多写一个头变量或布局属性，交付就会开始误报 `DXF parity mismatch`。建议：在 `dxf_snapshot` 里把"派生量"集中成一个显式名单并加注释。
- [低] 预览预算按"长边像素"而不是"编码体积"控制：`max_size` 只决定缩放倍数（`images.py:78-80`），一张 40×20 的裁切被放大到 1536 后 base64 仍可能撞上 2 MB 上限并报 `IMAGE_PREVIEW_TOO_LARGE`（`:86-87`），而放大本身不产生信息（工具描述已声明，`service.py:369`）。影响：极端小区域 + 唯一色噪声时，用户会被建议"用更小的 max_size"，其实需要的是"不要放大"。
- [低] `inspect_image` 的错误分类把"图像脚本失败"压成 `input`：`_measure` 把所有 `res["ok"] == false` 一律抛 `AgentCADError(..., category="input")`（`images.py:108-112`），而 `run_script` 走的是 `SCRIPT_FAILED`（`service.py:580`）。影响：同一个脚本错误在两条入口下的 `category` 与退出码不同，CLI 消费者要分情况处理。

**测试对应**

- `tests/test_k_pdf.py`（190 行）— 出图主链：fit 只出标准比例（`:18-25`）、显式比例选最小幅面与 `PLOT_TOO_LARGE`/`clipped`（`:28-34`）、图纸布局 1:1 一页一张与 `LAYOUT_NOT_FOUND`（`:37-50`）、单色与 `PRINTED_TEXT_TOO_SMALL`（`:53-60`）、中文替换不改源文件（`:63-73`）、沙箱/重名/扩展名（`:76-80`）、PNG 奇偶填充留孔与 `image_size` 边界（`:83-106`）、PNG 单布局（`:109-118`）、`TEXT_PLOTS_AS_BOXES`/`TEXT_CARET_CODE` 用实际字体表（`:121-190`）。
- `tests/test_t_image_inspection.py`（314 行）— `images.py` 全分支：裁切/EXIF/旋转/预览比例、非法 region 六种、沙箱外路径、输入与预览预算、CLI 与 MCP 内联图（`:32-163`）；图像脚本的数值/图像/截断三种返回、`draw_*` 帮手、50 条拒绝用例（`:215-262`）、runner 二次防线（`:270-278`）、超时（`:281-285`）、与 `rotate`/`dimensions` 互斥（`:288-295`）。
- `tests/test_w_image_dimensions.py`（283 行）— `image_dimensions.py`：由连通读数推出未标注间隙（`:45`）、错配面不改结论（`:59`）、锚点对齐不等于语义成立（`:71`）、`raster_probe` 独立于猜测端点（`:81-162`）、单位换算/反向端点/断开分量（`:164`）、环与比例冲突（`:179`）、旋转下的面板与源坐标（`:197`）、非法锚点不写盘（`:233`）、CLI 与 MCP 证据一致（`:255`）。
- `tests/test_w_delivery.py`（184 行）— `delivery.py` 的判决：stored spec 复原与漏改孔拒绝（`:10-23`）、三个工程域 critique（`:26-60`）、五种 DXF 损坏面（model/paper/block/attribute/style，`:63-92`）、嵌套扩展字典与软链接环（`:95-111`）、五种格式的坏产物（`:114-125`）、`set_meta` 写入非法 spec 后交付仍拒（`:128-134`）、图纸空间完整性（`:137-144`）、XLSX 包结构五种破坏（`:161-183`）。
- `tests/test_native_delivery.py`（200 行）— 经 DWG 往返后的交付：大制造记录、8 位小数、透明度与隐藏文字、科学计数标注（依赖 `backends/autocad.py`，本区域只被间接调用）。
- `tests/test_layouts_and_scripts.py`（463 行）— `scripting.py`：默认关闭（`:112-115`）、事务语义与 `dry_run`/journal/rollback（`:118-130`）、静态检查 27 条拒绝（`:133-174`）、脚本异常不改文件与 `strict` 拒绝（`:177-189`）、超时（`:192-196`）。
- `tests/test_security.py`（164 行）— 全仓扫描固定本区域的形状：`exec` 只允许在两处 `main`（`:127-134`）、`subprocess` 只允许在 `scripting.py` 且恰好两个 `shell=False` + 两个 `timeout=`（`:145-148`）、`service.py` 必须先调 `check_script`（`:162`）。
- `tests/test_y_tolerances.py`、`tests/test_mechanical_annotations.py`、`tests/test_dimension_display.py`、`tests/test_dimension_appearance.py` — 用 `deliver_drawing` 当断言终点，间接覆盖 `verify_artifact` 的 dxf/json/svg 分支（如 `test_w_delivery.py` 之外的 `DELIVERY_PARITY_FAILED` 场景：`test_mechanical_annotations.py:328`）。
- `tests/test_g_interfaces.py` — 适配层不含 CAD 逻辑：`cli.py`/`mcp_server.py`/`http_api.py` 的 import 里不得出现 `ezdxf`/`ops`/`engine`（`:208-221`），所以本区域的能力只会经 `Runtime` 暴露；`test_cli_end_to_end` 另有 `agentcad export plate.dxf plate.svg` 的端到端（`:54`），`test_http_api`（`:182`）覆盖 `POST /v1/<tool>` 的转发。
- 未覆盖（`tests/` 中无对应用例）：`plot_rotation ∈ {1,3}` 的页设置分支（`plot.py:215-217`）、`_PngCanvas` 的大填充性能、`_xlsx_rows` 超过 26 列、`metadata.set_meta` 的重复写入（泄漏 XRECORD，见风险 1）、以及 `run_script` 的巨型 `result`（无上限，见风险 3）。

## 3.7 平台、安装与后端：install、setup_gui、desktop、backends/*

**规模**：8 个文件 / 2187 行

**职责与边界**
这一层只做三件与画图无关的事：把 AgentCAD 接到别的应用上（`install` 写 MCP 配置、`setup_gui` 把它做成网页向导）、把"平台"抽象成图标与文件管理器（`desktop`、`install.create_shortcut`）、以及在装了外部 CAD 程序时借用它们（`backends/*`：AutoCAD 无头 `accoreconsole`、运行中的 AutoCAD COM 会话、FreeCAD 子进程与 3D 预览）。
它不做几何、不碰事务：`backends` 只接收已提交/AI 指定的文件路径，把结果写成另一份文件再交回 `service.Runtime` 或 `viewer`；`install` 只写用户目录下的配置文件与 `<root>/.agentcad/` 里的启动器。
上游是 `cli.py`（`setup`/`doctor`/`demo`/`mcp-config`，cli.py:203-234、242-250、296-303）和双击安装器 `installer/AgentCAD-Setup.cmd`（:77 起 `uvx ... agentcad setup --gui`）；下游是 `mcp_config`（只借用文本生成，install.py:265、155）、`policy`/`store`（freecad.py:229、autocad 的 job 目录位于 `policy.state_dir()/acad`，service.py:984）和 `viewer`（viewer.py:474-488）。
没有这些后端时被跳过的功能：DWG 导入/导出与 `acad_audit`（`autocad`）、`autocad_live` 六个动作（`acad_live`，需 Windows + pywin32）、viewer 的 3D 网格/缩略图/版本比较（`freecad`）。DXF 全链路不依赖任何一个。
平台并不对称：`acad_live` 只有 Windows/COM，`autocad` 只有 Windows 且有可执行文件假设，`freecad` 三平台都试；图标三平台各一套代码（Windows `.lnk`+`.ps1`+`.js`、macOS `.app`、Linux `.desktop`），向导页三平台同一份。

**文件清单**
| 文件 | 行数 | 角色 | 职责 |
|---|---|---|---|
| install.py | 919 | 核心 | 工作区解析、安装形态识别（git/archive/editable/ephemeral）、launcher 选择、四客户端配置写入、桌面图标、skill、`setup`、`doctor` |
| setup_gui.py | 233 | 入口 | `setup --gui` 的本地网页向导：`Wizard` 状态机 + 单文件 HTTP 路由，后台线程跑 `install.setup` |
| desktop.py | 28 | 工具 | `open_folder` / `reveal`：固定程序（Explorer/Finder/xdg-open）打开目录或选中文件 |
| backends/__init__.py | 0 | 工具 | 空包标记；没有门面，调用方一律直接 import 子模块（service.py:279、viewer.py:474） |
| backends/acad_live.py | 260 | 适配 | 运行中 AutoCAD 的 COM 会话：status/open/reload/pull/zoom/command 的底层原语 |
| backends/autocad.py | 183 | 适配 | 无头 `accoreconsole.exe`：DXF↔DWG、AutoCAD 自身 AUDIT；唯一会拼 `.scr` 作业脚本的地方 |
| backends/freecad.py | 244 | 适配 | FreeCAD 发现与子进程运行器：job 目录、输入输出拷贝、超时杀进程树、安装位置记忆 |
| backends/_fc_view.py | 320 | 适配 | 在 FreeCAD 自己的 Python 里跑的另一半：读 FCStd/STEP/BREP/STL，出网格 JSON、摘要或 PNG |

**关键入口**
- `install.setup`（install.py:668）— 唯一的"写配置"入口；签名固定 `clients, root, launcher, source, dry_run, read_only, allow_scripts, name, runner, progress, shortcut`，返回 `SetupResult`；`progress(step, state, info)` 的 step 序列是 `workspace, autocad, <每个客户端>, shortcut`（:679-749），网页向导的进度条直接按这个序列画。
- `install.install_info`（install.py:54）— 从 `importlib.metadata` 的 `direct_url.json` 判 `version/source/editable`，`_is_ephemeral`（:80）按前缀猜 uvx/pipx 缓存。
- `install.launcher_command`（install.py:91）/ `choose_launcher`（:103）— `(command, args)` 的唯一生成点；`uvx`/`pipx` 走 `--from`/`run --spec`，`python` 走 `sys.executable -m agentcad.mcp_server`；`uvx`/`pipx` 会先 `shutil.which` 取绝对路径（:97-98），因为桌面应用看不到 shell 的 PATH。
- `install.starts_offline`（install.py:128）— 是否加 `uvx --offline`；条件四者缺一不可：launcher 是 uvx、当前副本是 ephemeral、显式或安装来源的 source 与 `info["source"]` 相等、且不是 `git+`。
- `install.server_entry`（install.py:138）— 写进客户端配置的 `{command,args,env}`；env 只有 `AGENTCAD_ROOT`、`PYTHONIOENCODING=utf-8`，按需追加 `AGENTCAD_READ_ONLY`/`AGENTCAD_ALLOW_SCRIPTS`。
- `install.write_json_config`（:223）/ `write_codex_config`（:280）/ `_drop_toml_table`（:240）/ `_register_with_claude`（:324）— 三个写者，分别对应 JSON 文件、Codex TOML、`claude mcp` CLI。
- `install.create_shortcut`（:519）— 三平台图标；Windows 分支还要 `write_launcher`（:404）生成 `launch-<action>.ps1` + `.js`。
- `install.doctor`（:857）+ `_client_entry`（:815）/ `_check_entry`（:846）— 逐项体检；`ok` 只由 `required=True` 的检查决定（:907）。
- `setup_gui.Wizard`（setup_gui.py:41）— `__init__` 里就 bind 端口（:54-57），所以 `PORT_IN_USE` 在 CLI 构造期就返回（cli.py:214-216）；`serve`（:132）先打印 `{"ok":true,"setup":url}` 供安装器正则抓取（installer cmd:99）。
- `setup_gui.Wizard` 的路由（setup_gui.py:185-228）— 八个端点：`GET /`（:189，静态页）、`/app/assets/<file>`（:192-196，文件名白名单 `[A-Za-z0-9._-]+` + 后缀白名单）、`/api/info`、`/api/generic`、`/api/doctor`、`/api/state`（:197-207）、`POST /api/start`、`/api/open-folder`、`/api/quit`（:219-227）。页面本身是预构建产物 `viewer_static/app/setup.html`，源在 `web/viewer`（docstring:6），本仓库内改不了。
- `install.find_autocad_path`（install.py:660）— `setup` 的 `autocad` 步骤与 `doctor` 共用的探测；非 Windows 直接返回 `None`（:661-662）。
- `install.detect_clients`（:194）/ `ask_clients`（:797）/ `self_command`（:111）— 交互层：探测靠 `shutil.which` 与目录存在性；提问只接受编号，回车取默认集合；`self_command` 生成"再跑一次"的命令行（`doctor` 的 fix 文案里的 `agentcad setup` 会被替换成它，:916）。
- `install.viewer_command`（:377）— 把 MCP 的 `(command,args)` 改写成 `agentcad --root <root> <action>`：`uvx`/`pipx` 时把末尾的 `agentcad-mcp` 换成 `agentcad`（:381），这是图标与 MCP 配置共用同一 launcher 的方式。
- `desktop.open_folder`（desktop.py:12）/ `reveal`（:21）— 只接受调用方已校验过的路径；`reveal` 在 Linux 退化为打开父目录（:28）。
- `acad_live.connect`（acad_live.py:69）/ `com_thread`（:96）/ `Session`（:140）— COM 连接、每线程 `CoInitialize`、以及 `open/close/reload/save_copy/zoom/command` 原语。
- `autocad.find_accoreconsole`（autocad.py:35）/ `require`（:71）/ `dxf_to_dwg`（:121）/ `dwg_to_dxf`（:129）/ `audit_dxf`（:142）— 无头后端全部对外符号。
- `freecad.find_freecad`（freecad.py:44）/ `require`（:100）/ `available`（:109）/ `run`（:185）/ `remember`（:131）/ `recall`（:147）— 发现、执行与"跨进程记住安装位置"。
- `freecad.run`（:185）的契约值得单独记住：`job` 是纯数据（写进 `job.json`），`inputs`/`outputs` 是 `{job 键: 外部路径}` 的映射（:197-202），`timeout` 缺省取 `AGENTCAD_FREECAD_TIMEOUT`（:28）；进程级失败抛 `AgentCADError`（`FREECAD_TIMEOUT`/`FREECAD_FAILED`），业务失败以 `{"ok": false, "error": {...}}` 返回（:240），超过 120 s 或异常退出都会 `_kill_tree`（:214-220）。
- `acad_live.Session.save_copy`（:224）与 `reload`（:214）是 `pull`/`reload` 的全部底层语义：`save_copy` 会把 AutoCAD 里的文档 `SaveAs` 到临时 DXF 并返回 `(doc, view, dirty)`，调用方负责关文档、提交、再重开。
- `_fc_view.main`（_fc_view.py:304）与 `ACTIONS`（:301）— FreeCAD 侧的动作表 `mesh|summary|render`；模块顶层 `AGENTCAD_FC_JOB` 存在即执行（:319），因此它被 import 也会跑。

**数据结构与不变量**
- `SetupResult`（install.py:638）是 `setup` 的唯一返回形状；`ok` 定义为"没有任何客户端 status 为 error"（:650-652），因此图标失败（单独捕获，:737-748）与 skill 失败（:729-734）都不改变 `ok`。
- 进度契约：`progress(step, state, info)` 只在一个步骤开始（`"run"`）与结束（`"done"`/`"error"`）时被调用，`workspace`/`autocad` 两步必定成对（install.py:679-689），客户端步骤按传入顺序逐个成对（:691-736），`shortcut` 仅在 `shortcut=True` 时存在（:737-749）；`info` 里不含 `commands`（向导侧主动剔除，setup_gui.py:99）。
- 配置写入的原子性只到单文件：`_write` 用 `名字.agentcad-tmp` + `os.replace`（install.py:216-220），没有重试；JSON 以 `utf-8-sig` 读（:204），写回 `ensure_ascii=False, indent=2`。
- 幂等是硬要求：entry 与已有值相等时返回 `unchanged` 且不落盘（install.py:228-229、288-289、704），`write_skill` 只覆盖带 `SKILL_MARK` 的文件（:630-631），`create_shortcut` 用 `Test-Path` 区分 `added`/`updated`（:540、556）。`tests/test_q_install.py:93` 连跑两次断言这一点。
- TOML 双解析：写入前必须解析旧文件，写入后必须解析新文件且 `mcp_servers.<name>` 与目标 entry 逐字段相等，否则抛 `BAD_CONFIG` 不写（install.py:284-297）。这是"永不写坏 TOML"的落地方式。
- 环境与路径不变量：所有写入路径都来自 `Path.home()`、`APPDATA`、`LOCALAPPDATA`、`XDG_CONFIG_HOME`、`CODEX_HOME`（install.py:171-199、616-622），测试靠替换这些环境变量把真实用户目录隔离（tests/test_q_install.py:23-45）。
- 命令行不变量：`cmd_unsafe`（install.py:316）承认 Windows 上 `.cmd`/`.bat` 即使 `shell=False` 也会被 `cmd.exe` 解释 `& | < > ^ % " !`，命中就放弃 CLI 路径改直写 `~/.claude.json`（:695-701）。
- 引号不变量：`PS_QUOTES`（install.py:385）把 U+2018/2019/201A/201B 也当引号加倍，`_cmd_line`（:397）给每个参数加双引号并处理尾部反斜杠，`_desktop_quote`（:510）按 freedesktop 规范二次转义。三条各有测试（tests/test_q_setup_gui.py:285、297、460）。
- 向导鉴权不变量：所有请求先过 Host ∈ LOOPBACK（setup_gui.py:173），`/api/*` 还必须带 `X-AgentCAD-Token` 且用 `secrets.compare_digest` 比较（:177-178）；POST 体上限 65536（:215）。
- AutoCAD 作业不变量：脚本只含固定名 `in.dxf`/`out.dwg`/`result.txt` 与 `__JOBDIR__` 替换（autocad.py:91-95），调用方文本永不进脚本或命令行；作业目录非 ASCII 时退到 `%TEMP%/agentcad-acad`（:81-86），因为 `accoreconsole` 读不了非 ASCII 脚本路径。
- AutoCAD 会话不变量：文档一律只读打开（`Documents.Open(path, True)`，acad_live.py:185），否则 runtime 的 `os.replace` 会被 AutoCAD 拒绝；系统变量只读（:168、204-210）。
- `pull` 的语义边界：`save_copy` 会把 AutoCAD 文档改名成临时 DXF（acad_live.py:235），所以调用方必须 close 再 open（service.py:1241 附近）；只允许"视图变化"被静默丢弃，判据是 `DBMOD ∈ {8,16,24}` 且视角是未旋转的正交 2D（acad_live.py:197-212）。
- FreeCAD 输出不变量：成功时才把 `job[key]` 复制到目标目录下的临时目录，再 `store.replace_file`（freecad.py:229-238）——共享缓存不会被读到半截文件，`replace_file` 负责 Windows 上的读占用重试（store.py:67-81）。
- FreeCAD 配置只记在用户目录：`memo_path` 是 `%LOCALAPPDATA%\AgentCAD\freecad.txt` 或 `$XDG_STATE_HOME`（freecad.py:113-120），且只接受"绝对、本地、确实含 FreeCAD 安装"的目录（:123-128）——工作区里的文件不能决定启动哪个程序。
- FreeCAD 启动不变量：命令行只有解释器 + 固定脚本 + job 目录三元（freecad.py:204），job 数据走 `job.json`（:203）；非 Windows 用 `start_new_session=True` 以便整组杀（:211、172-177），Windows 用 `taskkill /F /T`（:163-169）。
- `_fc_view` 的依赖上限：只允许 FreeCAD 模块、numpy、标准库（_fc_view.py:1-10），PNG 自己写（:274-286，zlib+struct）以避免 Pillow。
- Windows 图标是一对文件、两种编码：`launch-<action>.ps1` 用 `utf-8-sig` 写（install.py:423，没有 BOM 时 Windows PowerShell 按 ANSI 读，中文文案会坏），`launch-<action>.js` 用 `utf-16` 写（:426-428，中文用户目录）；`.lnk` 的参数直接指向 `.js`（:534），两者必须同目录同前缀。
- `create_shortcut` 的返回是 `{"status": "added|updated|would be …", "path": …}`（:579）；`dry_run` 也会真的调用 PowerShell，只是脚本里不含 `CreateShortcut`/`Save`（:541-546），因此 dry-run 的 `had` 仍来自真实 `Test-Path`。
- 体检结论与真正的可启动性是两件事：`_check_entry`（install.py:846-854）只验证 `command` 是存在的文件或在 PATH 上、`AGENTCAD_ROOT` 是目录；它不验证 `python -m agentcad.mcp_server` 能否 import（解释器换过、包被删掉时 `doctor` 仍报 ok）。这是已知的判定边界，不是遗漏的检查。
- `install.SKILL`（install.py:584-613）是写给 Agent 的散文指令（"先等 5 秒、最多 12 次、别急着报失败"），不是配置数据：它随 `write_skill` 落到 `~/.claude/skills/agentcad/SKILL.md` 或 `$CODEX_HOME/skills/...`（:616-634），改动会直接改变客户端里的对话行为，而测试只断言首次启动（tests/test_q_install.py:130）不会因此失败。
- 向导的生命周期由两个独立条件决定：`POST /api/quit` 置 `done`（setup_gui.py:224-226），或 `last_seen` 超过 `IDLE_EXIT=3600 s` 且当前不在 running（:37、140-141）；`serve` 每 5 s 轮询一次（:139）。安装器正是靠"进程退出"结束窗口（installer cmd:109-111），所以退出路径必须真的让进程结束。
- AutoCAD ProgID 的不变量：`progids`（acad_live.py:46-66）枚举 HKCR 的 `AutoCAD.Application.<N>`、按 N 数值倒序，再把无版本号的 `AutoCAD.Application` 放最后；`AGENTCAD_ACAD_PROGID` 可整体覆盖。`connect` 只在前者全部失败且 `start=True` 时才尝试启动（:81-93），因此"找不到就启动新实例"不会误伤已有会话。
- 程序发现的"不信当前目录"不变量：`find_freecad` 丢弃非绝对或 UNC 的 `glob` 结果（freecad.py:65-66），`_on_path`（:78-97）自己遍历绝对 PATH 目录并排除 cwd 和 UNC —— 因为 Windows 上 `shutil.which` 会先看当前目录，工作区里放一个 `freecadcmd.exe` 就能被启动。

**主要调用链**
1. 双击安装（Windows）：`installer/AgentCAD-Setup.cmd:7` 取 `main.zip` → `:77` `uvx --refresh-package agentcad-runtime --from <zip> agentcad setup --gui` → cli.py:204 `install` → cli.py:211 `Wizard(...)` → setup_gui.py:54 bind → `serve` :134 打印 URL → cmd:99 正则取 URL 开浏览器 → 页面 `POST /api/start`（setup_gui.py:219）→ `Wizard.start` :82 建 `steps` 并起线程 → `_run` :103 调 `install.setup` → 各客户端分支 → `to_dict` 回填 `state`（:109）。
2. `setup` 写 Claude Code：install.py:695 若有 `claude` 就走 CLI → `claude_cli_commands` :300 → `cmd_unsafe` :316 → `_register_with_claude` :324（add 失败才 remove+add，第二次也失败则用 `_claude_user_entry` 还原）→ 否则 :715 直写 `~/.claude.json` 的 `{"type":"stdio", **entry}`。
3. `setup` 写 Codex：install.py:713 → `codex_table` :264（uvx/pipx 额外加 `startup_timeout_sec = 120`）→ `write_codex_config` :280 → `_drop_toml_table` :240 删掉全部旧拼写 → `_write`。
4. `create_shortcut`（Windows）：install.py:528 → `windows_script_host` :436 读 HKLM/HKCU 决定 wscript 还是 `conhost --headless` :531-537 → 生成带 `New-Object -ComObject WScript.Shell` 的 PowerShell 单行 :538-546 → 复制 `logo.ico`、`write_launcher` :550 → `launch-<action>.ps1` 占位符替换 :416-421 → `.js` 以 UTF-16 写 :427-428。
5. 双击图标：wscript 跑 `.js` → `powershell -File launch-*.ps1`（install.py:433）→ 先 `viewer.json` + `/api/ping` 复用已有 viewer（viewer_static/launch.ps1:29-48）→ 否则跑 `--offline` 快路径（:78）→ 进程退出且日志没有 `"viewer"` 就退回完整命令（:93-97）→ 打开成功后再按 24 h 节流刷新（:18-22）。
6. DWG 导出：cli `export --format dwg` → service.py:979 校验版本 → `autocad.dxf_to_dwg` :121 → `_run` :79 建 job、拷 `in.dxf`、写 `job.scr`、起 accoreconsole、按 `done=1` 判定成功 :107 → `_finish` :172 临时文件+`os.replace` → 回到 service.py:986-990 再 `dwg_to_dxf` 回读并 `validate_file`。
7. `autocad_live pull`：mcp_server.py:384 → service.py:1128 `com_thread` → `live_connect`（service.py:277-280，测试可替换）→ `Session.save_copy` :224 → `store.commit` → `Session.reload` :214 保留视图重开。
8. 3D 预览：viewer.py:474 `_fc` → `freecad.run` :185 → `Popen([python,-I,_fc_view.py,jobdir])` :204-211 → `_fc_view.main` :306 读 `job.json` → `action_mesh`/`action_render` → 写 `result.json` → freecad.py:221-238 读回并发布输出。
9. `doctor`：cli.py:207 → install.py:857 → `install_info` :863 → 逐个 import 依赖 :869-880 → `find_autocad_path` :660（→ autocad.py:35）→ `_client_entry` :815 / `_check_entry` :846。
10. 给别的 MCP 应用生成配置：cli.py:300-302 → `install.generic_mcp_config` :151 → `install_info` + `choose_launcher` + `server_entry` :163-166 → `mcp_config.generic_config`（mcp_config.py:40）。同一段逻辑在向导里有第二份：`Wizard.generic`（setup_gui.py:65-71）。

**扩展点**
- 加一个客户端：`install.CLIENTS`+`LABELS`（install.py:28-29）、`config_path`（:186）、`detect_clients`（:194）、`setup` 的分支（:709-717）、`skill_path`（:616）、`_RESTART`（:753）。网页向导不需要改——`Wizard.info`（setup_gui.py:60-63）按 `install.CLIENTS` 生成列表，`start` 也用同一元组过滤（:83）。
- 加一种 launcher：`launcher_command`（:91）+`choose_launcher`（:103）+`starts_offline`（:128），并同步 `write_launcher` 与 `mac_app` 里两处 `launcher == "uvx"` 分支（:410-414、473-486）——三处目前是复制关系。
- 加一个新后端：新建 `backends/<name>.py`，暴露 `require()`（缺失时抛 `category="dependency"`）与 `run()`，由 `Runtime` 的 `@tool` 方法惰性 import（service.py:979、1078、1101、1128）。必须同时把文件路径加进 tests/test_security.py:123 的 `launchers` 集合，否则 `subprocess` 扫描失败。
- 加 FreeCAD 动作：`_fc_view.ACTIONS`（:301）注册函数 + `viewer._fc` 调用方（viewer.py:473-484）；输入输出仍只能通过 `job.json` 的键名传（freecad.py:197-202）。
- 加平台图标：`create_shortcut` 的 `platform` 分支（:528/557-578）+ `mac_app`（:457）+ `_desktop_quote`（:510）。
- 加一条 `doctor` 检查：`doctor` 内唯一的 `add(name, status, detail, fix, required)` 闭包（install.py:860-861）；`render_doctor`（:911-918）按 status 对齐打印，`fix` 文本里的 `agentcad setup` 会被替换成 `self_command(info)`（:916）。
- 加一个向导端点：`do_GET`/`do_POST` 的分支（setup_gui.py:185-228）+ `_send`/`_json`（:159-170）；挂在 `/api/*` 下自动继承 token 与 Host 校验（:172-183），静态文件则必须同时登记进 `STATIC_FILES`（:38）或通过 `/app/assets/` 的文件名与后缀白名单（:195）。

**风险与可疑之处**
- [高] 安装源不固定，且没有完整性校验 — `installer/AgentCAD-Setup.cmd:7` 把 `refs/heads/main.zip` 写成默认源（分支头），`:84` 又用 `irm https://astral.sh/uv/install.ps1 | iex` 直接执行远程脚本；`install.py:25` 的 `REPO` 也只到仓库根。影响：同一份安装器在不同日期装出不同代码，无法复现，也无法确认下载内容。建议固定 release tag（必要时加 sha256），并把 `--from` 的选择留给显式参数。
- [中] `uvx --refresh-package` / `--offline` 是两个版本敏感的 uv 能力，但没有探测 — install.py:410-414 与 :474 直接拼进命令，`launch.ps1:21` 把刷新输出丢到 `nul`、失败无提示。旧 uv 上第一次点击只是"没反应/超时"。建议在 `install_info` 里读 `uv --version`，低于门槛时退回非 offline 启动并提示。
- [中] 多版本 AutoCAD 时"用哪个"不确定 — `find_accoreconsole`（autocad.py:40-43）先把 `glob` 结果排序，再追加注册表命中，最后取 `hits[-1]`；`_registry_locations` 文档声称"oldest first"（:47）但并未排序，实际顺序是 `winreg.EnumKey` 的枚举顺序。影响：DWG 往返用的 AutoCAD 版本不可控，`audit_dxf` 的结论也随之漂移。建议收集 `(版本号, 路径)` 后按版本排序，或强制 `AGENTCAD_ACCORECONSOLE`。
- [中] `dxf_to_dwg` 的版本参数没有守卫 — autocad.py:122 `DWG_VERSIONS[version]` 直接下标，未知版本抛 `KeyError`（不是信封错误）；目前只有 service.py:981-983 先做了检查。直接调用方（脚本、测试、将来的新工具）会拿到裸异常。
- [中] 配置写入无重试、跨客户端无回滚 — install.py:220 只 `os.replace` 一次，Windows 上目标文件被 Claude Desktop/Codex 打开就 `PermissionError` → 该客户端记 `NO_PERMISSION`（:722-723），而先前已写的客户端配置保留。全流程唯一的回滚是 `claude mcp` 注册还原（:334-341）。建议对 `_write` 复用 `store.replace_file` 的退避重试。
- [中] `setup_gui` 依赖 `viewer` 的私有符号 — setup_gui.py:35 `from .viewer import LOOPBACK, STATIC, UI_TYPES, _host_name, _server`；`_host_name`/`_server` 是下划线私有函数，viewer 重构即断（两处 Host 校验语义也会分叉）。建议抽成 `http_shared` 之类的公共模块，或让 viewer 显式导出。
- [低] `find_autocad_path` 在 `setup` 主流程没有兜底 — install.py:687-689 在 `try` 之外调用 `glob`/`winreg`；异常会中断整个 `setup`，且此时 `workspace` 步骤已执行。建议与 `shortcut` 一样单独捕获后记 `{"status":"error"}`。
- [低] accoreconsole 日志固定按 `utf-16-le` 解码 — autocad.py:106。若某版本/区域设置输出别的编码，`_audit_lines`（:114-118）与 `audit_dxf` 的 `messages` 会被误解码污染；`result.txt`/`done=1`（:150、107）不受影响，所以失败是"审计结论变哑"而不是崩。建议按 BOM 或 `locale.getpreferredencoding()` 先试。
- [低] 图标依赖 `powershell.exe` 存在 — install.py:551 `shutil.which("powershell") or "powershell.exe"`；只有 PowerShell 7（`pwsh`）的机器上 `FileNotFoundError` 被 `install.setup` 捕获成 `SHORTCUT_FAILED`（:742-743），`SetupResult.ok` 仍为真，所以 `setup` 命令退出码是 0、安装器界面只显示"打开失败"。建议探测 `pwsh` 再退化。
- [低] `_fc_view.render` 的代价只受像素上限约束 — _fc_view.py:291-293 只校验 `size`，而 :247-254 对每个三角形做一次 Python 循环；大网格 + `ss=2` 时容易顶到 120 s 超时（freecad.py:28、214-217），表现为 `FREECAD_TIMEOUT` 而不是"图太大"。建议加三角形数上限。
- [低] FreeCAD 的 `-I` 分支依赖解释器自身的 `sys.path` — `_from_dir` 返回 `[python, "-I"]` 且 `extra` 为空（freecad.py:36-37），而 `_fc_view` 只在 `AGENTCAD_FREECAD_LIB` 存在时才补 `sys.path`（_fc_view.py:19-20）；隔离模式不把脚本目录加入 `sys.path`，`import FreeCAD` 是否成功取决于该 Python 的安装布局（推测：本机未装 FreeCAD，未验证）。建议对该分支同时注入 `AGENTCAD_FREECAD_LIB=<dir>`。
- [低] 文档与代码不一致 — docs/architecture.md:121 称 `subprocess` 只出现在 `scripting.py` 与 `backends/autocad.py`，实际 `install.py`、`desktop.py`、`backends/freecad.py` 也在用；tests/test_security.py:123 的白名单已包含它们，说明是文档滞后而非代码违约。
- [低] Windows Script Host 关闭检测有两份实现 — install.py:436-448（winreg）与 installer/AgentCAD-Setup.cmd:12-13（`reg query` 正则）。两边规则一旦分叉，安装器与图标会走不同启动路径。
- [低] 插 `--refresh-package` 的位置靠 `args.index("agentcad")` 找 — install.py:413-414 与 :474 都假设参数里恰好有一个等于 `agentcad` 的元素；`--from agentcad`（包名直接作为源）会让 `args[:index+1]` 包含源参数，插出 `uvx --refresh-package agentcad-runtime --from agentcad --help` 这种能跑但语义变了、且与 `write_launcher` 其余分支不一致的命令。建议按 launcher 结构显式拼装，不靠字符串搜索。
- [低] macOS 的快/慢路径与 24 h 节流是第二份实现 — install.py:473-486（shell 脚本里 `nohup`、`grep '"viewer"'`、`find -mtime`）与 viewer_static/launch.ps1:18-22、78-97（PowerShell 里 `last-update-check` 时间戳）各写一遍。改 Windows 的启动顺序不会影响 macOS，反之亦然；两处的"成功判据"也不同（日志里出现 `"viewer"` vs 进程没退出）。
- [低] `autocad_live action='command'` 的 journal 一律记 `ok: True` — service.py:1192-1193 在 `s.command` 返回 `finished=False`（AutoCAD 仍在忙，acad_live.py:246-260）时也照记，只在结果里带 `finished` 与提示。时间线读起来像"命令成功执行"，把"可能还在等输入"的信息留在了 `hint` 里。
- [中] `desktop.open_folder` 在 Windows 绕过 `subprocess` 白名单 — desktop.py:16 用 `os.startfile`，而 test_security.py:152-154 只约束 `subprocess.Popen([...])` 的三个固定程序。无交互桌面的会话（服务、计划任务）上它抛 `OSError`；`/api/reveal` 会把它转成 JSON 错误（viewer.py:983-985），而向导的 `/api/open-folder` 只捕获 `OSError`（setup_gui.py:124）。
- [低] 向导页是独立构建产物 — `src/agentcad/viewer_static/app/setup.html`（609 字节）+ `/app/assets/*` 从 `web/viewer` 构建（setup_gui.py:6），Python 侧与页面只能靠人工保持一致。缓解是 test_q_setup_gui.py:83-100 会真的加载页面与其 JS 并断言其中出现 `/api/start`，但 `/api/generic`、`/api/doctor`、`/api/open-folder` 只测了端点本身，没有"页面确实调用它"的断言。

**测试对应**
- tests/test_q_install.py — 默认工作区为 `~/AgentCAD`（:64）、`--root`/`AGENTCAD_ROOT` 优先级（:73）、四客户端幂等（:93）、dry-run 不写盘（:122）、**写出的配置能真起 MCP server 并建图**（:130）、坏 JSON 不动（:139）、有 `claude` CLI 时走 CLI（:161）、`.cmd` 元字符不进参数（:193）、无 CLI 时写用户配置（:211）、只配了 project 级的诊断（:240）、Codex 别名与坏 TOML（:255-283）、BOM（:285）、uvx/pipx/`--offline`/ephemeral 判定（:294-344）、`ask_clients`（:345）、`doctor`（:352）、CLI 端到端（:379）。
- tests/test_q_setup_gui.py — 页面与 `/api/info`（:83）、`/api/generic`（:103、:113）、token 与 Host 拒绝（:121）、逐步骤进度（:129、:185）、坏配置上报而非覆盖（:150）、dry-run（:164）、坏请求（:177）、archive 安装保留来源（:192）、doctor 页（:208）、三平台图标（:217、:240）、图标失败不影响 `ok`（:263）、复用运行中的 viewer（:271）、引号与特殊字符（:285、:297、:460）、两个图标（:352）、launcher 先试本地副本（:364）、图标无窗口（:389）、旧 `.command` 清理（:476）、无 Script Host 回退 conhost（:486）、mac app 形态（:494）。
- tests/test_security.py — 启动器白名单与 `shell=False` 断言（:117-160），`from .install import` 只允许 `ensure_workspace`（:158-160）。
- tests/test_autocad_live.py — 本机没有 `accoreconsole.exe` 则整个文件跳过（:10）：生成的图通过 AUDIT、R12 导出、DWG 往返保留 spec、DWG 审计、缺陷检出。
- tests/test_autocad_golden.py / test_autocad_findings.py — 不需要 AutoCAD：用 `tests/golden/autocad_ops.json` 与"AutoCAD 2027 观测"回放，覆盖 `_run` 之外的 AutoCAD 兼容性知识。
- tests/test_l_live_session.py — 用假 COM app 覆盖 `acad_live` 契约：只读打开允许原子提交、脏文档不被静默关闭、`pull` 失败保留 AutoCAD 副本（:370、:422）。
- tests/test_o_viewer.py — FreeCAD（`needs_freecad`，:768）：真实 job、直接执行 `_fc_view`、缓存输出不暴露半成品（:847）、安装位置记忆（:875）；`desktop.reveal/open_folder` 的路径沙箱（:516-571）；tests/conftest.py:24-28 把 `freecad.memo_path` 指到临时目录，保证测试永不写真实用户目录。
- 覆盖缺口（都核实过）：`acad_live.command`/`zoom` 没有测试（test_l_live_session.py 只覆盖 open/pull/reload 与失败保留）；`autocad._registry_locations`（autocad.py:46）无单测，且 test_autocad_live.py 在无 `accoreconsole.exe` 时整文件跳过（本机即如此）；`desktop.reveal` 的 Windows `explorer /select` 分支只被静态断言约束（test_security.py:152-154），无行为测试；`mac_app` 的 24 h 节流与"快路径失败再退回"分支无测试（test_q_setup_gui.py:494 只断言生成的脚本形态）。

## 4. 跨切面机制（锁、修订、错误、单位、标签、元数据、指标、策略、分层）

**规模**：12 个文件 / 3418 行（service.py 1590、store.py 579、model.py 378、progress.py 212、sessions.py 177、policy.py 94、errors.py 87、units.py 77、viewer_link.py 66、guards.py 62、locking.py 51、metadata.py 45）

**职责与边界**
这一层不实现任何 CAD 语义，只回答「一次调用能不能写、写的时候谁在抢、写完凭什么说没写坏、失败怎么说清楚、长度怎么换算、实体怎么被再次找到、元数据存在哪、过程和成本怎么被看见、谁能挡在入口」。它被 `Runtime`（service.py）调用，也渗进 `ops/*`（tags/selectors/coerce）和 `validate/*`（单位、指纹）——因此它是「横穿所有层」的机制集合，而不是一个可替换的模块。它不做：不做几何、不做验证规则、不决定信封里放哪些业务字段（那是 `@tool` 与各工具自己的事）。关键切分是 `errors.py` 是唯一无内部依赖的公共叶子（被 49 个模块导入），`policy.py` 只决定「允许/拒绝」不碰文件内容，`guards.py` 只比较「读到的」与「记住的」不写任何状态，`store.py` 是唯一接触 `write.lock`/`head.json`/`journal.jsonl` 的地方。

**文件清单**
| 文件 | 行数 | 角色 | 职责 |
|---|---|---|---|
| service.py | 1590 | 核心 | `@tool` 信封（计时/计字节/指标/错误捕获/进度/旁路），`_lock` 重入锁，`PROGRESS_TOOLS`/`LIVE_SYNC_TOOLS`/`VIEWER_TOOLS` 三张表，`get_metrics` |
| store.py | 579 | 核心 | 状态目录解析与 legacy 迁移、`replace_file` 重试、`_replace_tracked` 原子替换、pending 结算、检查点、journal 尾部倒读、`status` 完整性 |
| model.py | 378 | 核心 | 实体序列化 `entity_to_dict`、XDATA 标签读写、`ACTIVE_LAYOUT` 上下文、选择器 `select`、布局归属与 OCS→WCS 归一化 |
| progress.py | 212 | 支撑 | `activity.jsonl` 事件（working/checking/done/error）、`op_brief`/`group_ops` 摘要、路径脱敏 `scrub`、ContextVar 承载当前 Activity |
| sessions.py | 177 | 支撑 | 每个 MCP 进程一份 `<pid>.json`，进程存活/pid 复用判定，`tag()` 给进度与 journal 盖「谁做的」 |
| policy.py | 94 | 支撑 | 沙箱根、`.agentcad` 保留、后缀白名单、只读、op 白名单、各类预算与环境变量默认值 |
| errors.py | 87 | 数据 | 8 个封闭错误类别、`AgentCADError` 字段与 `to_dict` 稳定形状、5 个类别工厂 |
| units.py | 77 | 工具 | 5 个长度单位与 $INSUNITS code、别名（含中文）、`"8mm"` 解析、换算 |
| viewer_link.py | 66 | 适配 | 仅用标准库探测「本 workspace 的 viewer 是否在跑」，避免为一次判断导入 ezdxf |
| guards.py | 62 | 支撑 | 实体指纹、`expected_revision`/`preconditions` 的 STALE_REVISION 判定、dry_run 的全量指纹快照 |
| locking.py | 51 | 支撑 | `write.lock` 上的非阻塞 OS 字节锁（msvcrt/flock），旧版 `lock` 标记一律不抢占 |
| metadata.py | 45 | 数据 | rootdict → AGENTCAD 字典 → XRECORD（JSON 按 250 字符分块存 group 1）的读写 |

**关键入口**
- `tool(fn)`（service.py:67-107）— 唯一信封装饰器；`TOOLS[name] = fn` 存的是**未包装**的原函数（105），这正是 `_create_if_missing`（service.py:509）与 `revise_from_document`（809）用 `TOOLS[...](self, ...)` 直接调用、避免再套一层信封的原因。
- `Runtime._lock(store)`（service.py:201-213）— 同路径在本调用链内复用外层 store（`ContextVar _held_store`，189/203-207），否则 `store.lock(migrate=not policy.read_only)`（208）；`drawing_lock` 本身不可重入，重入语义在这一层伪造。
- `DrawingStore._replace_tracked`（store.py:408-468）— 唯一的替换协议；`commit_bytes`（396-406）与 `restore`（536-561）都是它的包装。
- `DrawingStore.settle_pending`（496-511）/ `finish_pending`（513-534）— 每次替换**之前**先结算上一次中断的提交（415-418），结算结果写成一条 `recover` 记录并回填中断提交的 `operations`/`changes`（506-510）。
- `guards.check`（22-62）/ `guards.fingerprint`（14-19）— 修订与指纹的唯一判定点。
- `model.select`（320-374）— 全仓库唯一的实体寻址入口；`ACTIVE_LAYOUT`（66）决定过滤对象的默认布局。
- `model.set_tag`/`get_tag`（54-61 / 43-51）— 标签即 XDATA。
- `metadata.set_meta`/`get_meta`（15-25 / 27-38）— `strict=True` 才把坏 JSON 变成 `INVALID_METADATA`。
- `units.parse_length`（56-77）/ `normalize_unit`（35-43）/ `unit_from_insunits`（52-53）。
- `Policy.resolve`（38-63）/ `check_write`（65-67）/ `check_op_allowed`（81-84）/ `state_dir`（86-87）。
- `progress.Activity`（114-187）与模块级 `step`/`checking`（202-211）— engine 与 `_validate_bytes` 通过这两个函数上报，未在跟踪时是空操作。
- `sessions.Session.note`/`tag`（69-97 / 45-55）、`sessions.alive`（128-153）— pid + 进程创建时间双重判活。
- `AgentCADError.to_dict`（errors.py:50-67）— CLI/MCP/HTTP 共同消费的错误形状。

**数据结构与不变量**
- 锁粒度是「一张图的状态目录」，不是文件本身；`write.lock` 的文件句柄在整个临界区内保持打开，解锁后**不删文件**（locking.py:21-22、36-40），保证 POSIX 上不会因为 inode 更换而出现两把锁。
- 只有协作进程受锁保护：锁是 OS 字节锁，AutoCAD / 任意写盘工具不会参与；因此「文件字节 sha」是第二道关（guards.py:37、service.py:480、store.py:522）。
- 修订号是**提交计数**：只在替换成功后 +1（store.py:421、458）与结算成功时补齐（526），外部直写不改变它；`expected_revision` 单独使用无法发现外部修改，必须搭配 `preconditions.sha256`（guards.py:44-45）。
- 指纹跨保存/重载稳定：`TagCollector.dxftags(entity)` + 其 `attribs`，按 `(code, str(value))` 列表做 sha256（guards.py:14-19）；实测改标签（XDATA）与改图层都会改变指纹，属性文字的变化同样被覆盖（tests/test_t_workflow_guards.py:163）。
- 「已改文件」与「已记账」必须可区分：替换前任何 PermissionError → 什么都没变、`retryable=true`；替换后 → `partial_changes=true`、`retryable=false`、要求 `recover`（store.py:56-64、438-466）。
- journal 记录三种形态（成功/失败/`recover`）共享 `seq`/`ts`/`drawing`/`versions`/`client`/`conversation`（store.py:329-330）；`seq = max(head.seq, 文件末尾 seq)+1`，先 append 后写 head，head 写失败不丢记录（327-337）。
- 错误类别是 8 元封闭集合（errors.py:19），构造时 `assert`（38）；`retryable` 为真的编码全仓库有 9 个：`DRAWING_LOCKED`、替换前的 `FILE_BUSY`、`FILE_READ_ERROR`、`AUTOCAD_NOT_RUNNING`、`VERSION_CHANGED`，以及三个后端的忙/超时分支 `AUTOCAD_BUSY`、`AUTOCAD_COM_ERROR`、`AUTOCAD_TIMEOUT`、`FREECAD_TIMEOUT`。
- 单位：数字一律按图纸单位解释，只有带后缀的字符串才换算；图纸无单位时带后缀的输入报 `UNITS_UNDEFINED`（units.py:71-76），不带后缀的数字**照旧通过**——这是「无单位图仍可编辑」的隐含约定。
- 元数据没有 schema 版本键：唯一的版本戳是 journal 里的 `VERSIONS`（store.py:38、330）与交付清单的 `"version": 2`（quality.py:109）。
- 预算与开关在 `Policy` 上集中（policy.py:13-32），但执行点分散在 engine/coerce/ops/store，见 4.8。
- 分层约定只有一条被测试强制：适配层不得导入 `ezdxf`/`ops`/`engine`（tests/test_g_interfaces.py:208-221）。

**主要调用链**
1. 写调用：`@tool` 包装器（service.py:74-103）→ `_activity` 建进度（228-249）→ 工具方法 → `_lock`（201-213）→ `DrawingStore.lock`（213-252）→ `drawing_lock`（locking.py:16-40）。
2. 提交前复检：`sha256_file`（service.py:452）→ `guards.check`（454）→ 比对读盘期间的 sha（455-456）→ engine 执行 → `guards.check(..., before["revision"], {"sha256": before["sha256"]})`（480）→ `store.commit_bytes`（481）→ `_replace_tracked`（store.py:408）。
3. 崩溃恢复：`Runtime.recover`（service.py:893-938）→ `store.status`（563-579）→ `settle_pending`（496-511）或「committed 副本 → 逆序检查点」逐个校验 sha 与可读性后 `restore`（536-561）。
4. 进度：`tool` → `progress.begin(token)`（service.py:78）→ `engine.execute` 每步 `progress.step`（engine.py:61）→ `Activity.emit`（progress.py:136-146）→ `tool` 末尾 `act.finish(env, ops)`（91-92）→ `progress.end`（90）。
5. 归属：MCP `NotesClient._note`（mcp_server.py:76-86）→ `SESSION.note`（sessions.py:69-85）→ `CURRENT` 字典 → `tag()`（45-55）被 `Activity.emit`（progress.py:140）与 `store.journal`（329-330）读取。

### 4.1 跨进程锁与原子提交
- `drawing_lock`（locking.py:15-40）：目录先 `mkdir`，然后对 `write.lock` 取 1 字节非阻塞锁——Windows `msvcrt.LK_NBLCK`（24-27），POSIX `flock(LOCK_EX|LOCK_NB)`（28-30）；`OSError` 统一转 `DRAWING_LOCKED`（policy/retryable，11-12、31-32）。
- 旧版 `lock` 标记是硬拒：不动、不按年龄或 PID 判死（19-20、43-51），`recover` 也先拒绝（service.py:900-902）。
- `replace_file`（store.py:67-81）：`os.replace` 遇到 `PermissionError` 时**只在 Windows** 退避重试，预算 `REPLACE_TIMEOUT=2.0s`（53），间隔 5 ms 起、翻倍封顶 100 ms；POSIX 立即抛。
- 提交顺序的不可交换点：settle 旧 pending → 算好 `revision`（421）→ 建检查点 → 写 `pending{label,checkpoint,sha256,revision}`（422-428）→ 同目录临时文件 + `fsync`（433-437）→ `replace_file`（439）→ 刷新 committed 副本（447-450）→ 清 pending、写 sha 与 revision（455-458）→ journal。
- `_abandon`（470-494）先改 head 成功才删检查点文件：head 被占用时「head 仍列出该检查点、文件也留着」，是一份未变图纸的无害副本；且只清本次自己的 pending（482）。
- 单进程内的并发不是靠这把锁：HTTP 用一把全局 `execution_lock` 串行化所有请求（http_api.py:38、132），MCP 用单线程 executor（worker.py:8-19），因为 `Runtime` 与 ezdxf 都不是线程安全的。

### 4.2 revision / guard 的完整语义
- 三件套可单独或组合出现：`expected_revision`（非负 int，否则 `BAD_PARAMETER`，guards.py:23-24）、`preconditions.sha256`、`preconditions.entities`（handle → 指纹），后者只接受这三个键且 `entities` 必须是 dict（26-35）。
- `check` 同时读两个来源：`store.head()["revision"]`（磁盘上的 head，不是内存 doc）与 `sha256_file(store.path)`（37）；`preview=True` 时对**所有布局的所有实体**算指纹（39-40），非 preview 只算 `entities` 里点名的 handle。
- 失败一律 `STALE_REVISION`（50-61）：`entities` 列出指纹不符的 handle，`details` 给 `expected_revision`/`actual_revision`/`changed_handles`，`hint` 要求「重新读取或预览后再提交新的 preconditions」。
- 正常路径是预览闭环：`apply_operations(dry_run=True)` 返回 `preview{revision, preconditions{sha256, entities}}`（service.py:466-467），调用方原样回传即可获得「读—改」之间的完整乐观并发检查（tests/test_t_workflow_guards.py:19-43）。
- 两次检查夹住计算段：进入锁后 `guards.check`（454）挡住「计划之后被改」，提交前再 `guards.check`（480）挡住「计算期间被改」；两次之间只留给 `store.replace_file`（store.py:439）。
- 指纹的语义边界：它覆盖 DXF 标签流与属性文字，因此**改颜色/图层/几何/文字/标签都会变**；它不覆盖实体属于哪个布局，也不覆盖块定义内部实体的改动（推测：两者都不在 `dxftags` 导出的属性里，本条只对标签/图层/属性文字做过实测）。
- `preconditions={"entities": {}}` 能通过校验（26-35）却什么都不比（43-49）：既无 `sha256`、循环体为空，`stale` 恒为 False——这是一个静默空操作（见风险）。

### 4.3 错误信封与错误分类
- 一次失败只有一种出口：`AgentCADError`，字段 `code`（自由字符串，按抛出点命名，无枚举）、`category`、`message`、`entities`、`op_index`/`op`、`retryable`、`hint`、`partial_changes`、`details`（errors.py:22-48）；`to_dict` 恒输出前六个字段，其余空值省略（50-67）。
- 类别语义（errors.py:10-19）：`input` 改调用、`spec` 改规格、`geometry` 几何不可行、`file` 读不到/不是 DXF、`policy` 沙箱/只读/预算/锁、`validation` 不满足需求、`dependency` 可选组件缺失、`internal` 缺陷。
- 可重试的完整集合（9 个）：`DRAWING_LOCKED`（locking.py:12）、`FILE_BUSY`（未落地版，store.py:63-64）、`FILE_READ_ERROR`（store.py:97）、`AUTOCAD_NOT_RUNNING`（service.py:1136-1137）、`VERSION_CHANGED`（viewer.py:329-331），以及 `AUTOCAD_BUSY`/`AUTOCAD_COM_ERROR`（backends/acad_live.py:123-132）、`AUTOCAD_TIMEOUT`（backends/autocad.py:104-105）、`FREECAD_TIMEOUT`（backends/freecad.py:216-217）。`STALE_REVISION`、`VALIDATION_FAILED`、`OP_NOT_ALLOWED`、`CONFIRMATION_REQUIRED` 都**不可**原样重试——前两者要先重新读盘/改操作，后两者要改参数。
- `partial_changes=true` 只有一个来源：`busy_error(applied=True)`（store.py:59-62），含义是「图纸文件已经是新内容，但 head/journal 没记上」；契约是禁止重放操作、改调 `recover`。它与 `retryable=false` 是一对，二者不会同时为真。
- 归属信息由 engine 补：任何 op 抛错都写 `op_index`/`op` 并**无条件**置 `partial_changes=false`（engine.py:75-79），库异常转 `INTERNAL_ERROR` 带 6 层 traceback（80-82）；`@tool` 再兜住非 AgentCAD 异常并给 8 层 traceback（service.py:84-87）。
- 适配层的差异必须知道：CLI 按类别映射退出码（validation 1、input/spec/geometry 2、file 3、policy 4、internal 5、dependency 6，cli.py:16-17）；HTTP 把领域错误当 **200 + ok:false** 返回（http_api.py:136），4xx 只留给鉴权/请求体/签名；MCP 把信封当普通工具结果返回，不置 `isError`，调用方只能读 `ok`。
- 传输层错误是手搓的第二个形状：`_refuse` 只写 `{code,category,message}`（http_api.py:54-71），CLI `_die` 也只有三个键（cli.py:35），都不带 `retryable`/`partial_changes`——「稳定契约」只覆盖领域错误路径。

### 4.4 单位系统
- 权威表：`UNITS = {mm:(4,1.0), cm:(5,10.0), m:(6,1000.0), in:(1,25.4), ft:(2,304.8)}`（units.py:16-22），别名含单复数、`"`/`'` 与中文（23-29），反查表 `CODE_TO_UNIT` 只覆盖这 5 个 code（30）。
- 解析：`_LEN_RE`（32）接受前后空格、正负号、指数与可选后缀；`bool` 显式拒绝（58-59），数字直接返回（60-61），无后缀字符串也直接返回（69-70），有后缀则必须能 `normalize_unit` 且图纸必须有单位（71-76）。
- 换算只有一处：`convert(v, src, dst) = v * mm[src] / mm[dst]`（46-49），全部单位经毫米中转。
- 参数入口：op 参数在 `coerce` 里按 `Param.type == "length"` 转换（ops/registry.py:289-296），点坐标在 `_point`（260-261）；公差（tolerances.py:57-58）、柱宽（annotate.py:356）、文字高度（edit.py:387、430；structure.py:149）各自显式调用 `parse_length`。
- 图纸头：`create_drawing` 写 `$INSUNITS=code`、`$MEASUREMENT = 0 if in/ft else 1`、`$LUNITS=2`、`$MIRRTEXT=0`（service.py:158-162），并按 `1/mm_per` 缩放整套标注尺寸（163-169）。`$MEASUREMENT` 之后再无写入者，也无读取者——它只被交付清单原样拷贝（delivery.py:63），是「记录但不校验」的字段。
- 校验侧只对 `$INSUNITS` 发**警告**（validate/checks.py:426-428），所以一张无单位图能通过验证，却在任何 `"8mm"` 输入上失败；`unit_from_insunits` 对 0/3/… 返回 `None`，此时 `_units_of`（service.py:148-149）也为 `None`。

### 4.5 标签 / 选择器 / 批内引用
- 标签就是一条 XDATA：appid `AGENTCAD`、值 `(1000, "tag=<name>")`（model.py:18、43-51）；`set_tag` 用 `set_xdata(APPID, [(1000, ...)])` **整体替换**该 appid 的值列表（54-60），因此同一 appid 下的其他自定义值会被抹掉。
- 索引 `tag_index` 现场扫描全部布局（105-111），`Ctx.tags()` 缓存它（ops/registry.py:186-189）并在删除实体时置 `None`（180）；新建实体时直接往缓存里追加（215）。
- 选择器五种形态（model.py:320-374）：列表递归（331-333）、过滤对象（334-342，空对象拒绝、`{"all":true}` 才是全选、`layout` 缺省取 `ACTIVE_LAYOUT`）、`"$i"`（345-353，取自 `ctx.refs`）、`"tag:x"`（354-355，全布局）、handle（357-361，必须 `is_alive` 且 owner 属于某个布局）；结果按 handle 去重保序（368-374），空结果除非 `allow_empty` 否则 `ENTITY_NOT_FOUND`。
- `ACTIVE_LAYOUT` 是 ContextVar（66），engine 在每个 op 前后 set/reset（engine.py:67、84-85），所以 op 内部的过滤选择器默认落在该 op 的 `layout` 上；handle 与 `tag:` 不受它影响。
- 批内引用 `ctx.refs[i] = created or modified or existing`（engine.py:87）——所以 `$0` 在一次编辑后指向**被改**的实体，在 `if_tag_exists=skip` 后指向已存在的实体；`$i` 用 `doc.entitydb[h]` 直取，不做 owner 校验（model.py:353），而 handle 形态要求 owner 属于某个布局（358）。
- `if_tag_exists`（ops/registry.py:232-250）：`error`（默认，`TAG_EXISTS`）/`skip`（返回既有 handle，op 变成空操作）/`replace`（先删既有实体）。
- 复制/偏移产生的新实体只在 op 显式带 `tag` 参数时才被打标签（registry.py:211-216），不继承源实体标签——与 architecture.md 的说明一致。

### 4.6 XDATA 与 rootdict 元数据
- 存储形态：`doc.rootdict["AGENTCAD"]` 是普通 DICT，每个键挂一条 XRECORD，内容为 `json.dumps(..., ensure_ascii=True, sort_keys=True)` 按 250 字符切块写进 group 1（metadata.py:11-25）。250 对应 DXF group 1 的 255 上限，`ensure_ascii=True` 让中文变 `\uXXXX`（体积 6 倍但编码安全）。
- 写入即重建：`remove(key)` + `add_xrecord(key)`（20-22），XRECORD 的 handle 每次都换——元数据变更会让整份 DXF 的 handle 序列变化，但图纸内容本身不变。
- 运行时只写三个键：`created{by,units,template,ts}`（service.py:180）、`part_spec`（730、764）、`plan_tags`（731、765）。`plan_tags` 是上一次计划的标签全集，`revise_drawing` 用它算「旧计划有、新计划没有」的陈旧标签（759-761）。
- 没有元数据 schema 版本键；读到坏 JSON 时 `get_meta` 默认返回 `default`，只有 `strict=True` 抛 `INVALID_METADATA`（metadata.py:27-38）。两处调用刻意不对称：`read_drawing` 用 `all_meta`（service.py:398，坏值显示 `null`），`_drawing_validation` 用 `strict=True`（662-665，坏值直接失败关闭）。
- 规格的「随文件走」：`part_spec` 只在 `draw_from_spec`/`revise_drawing` 写，`apply_operations` 从不更新它——用 op 改过的规格图仍保留旧规格，而 `validate_drawing` 会用旧规格重算需求（service.py:655-667，`requirements_source="stored_part_spec"`），于是验证比对的是「当初的意图」而非「现在的几何」。这是有意的失败关闭，不是静默放行。

### 4.7 metrics 与 progress
- 计时/字节：`t0 = perf_counter()`；`input_bytes = len(json.dumps([args, kwargs], default=str))`（service.py:75-76）——量的是 Python 参数的 JSON 文本，不是 MCP/HTTP 线上字节；`output_bytes = len(json.dumps(env))`（100-101）在写入 `metrics` 之前计算，因此 `metrics` 自身不计入。
- `metrics{duration_ms, input_bytes, output_bytes}` 追加进 `Runtime.metrics`（100-102）；`get_metrics` 聚合全量、只回最近 200 条，并把 `model_calls`/`model_cost_usd` 硬编码为 0（1387-1394）——这两个字段是占位，不能据此判断模型成本。
- 进度事件写 `<root>/.agentcad/activity.jsonl`，每行一个 JSON（progress.py:136-146），`state` 取 `working`/`checking`/`done`/`error`；每 op 一行但 50 ms 内最多一行，首尾必写（148-154）；单文件超过 512 KB 在新建 Activity 时轮转为 `.1`（31、129-134）。
- 写日志**永不失败**：`emit` 吞掉所有异常（136-146），`_rotate` 只吞 `OSError`（129-134）；只读模式完全不建 Activity（service.py:232-233）。
- 摘要层：`op_brief` 只留 `diameter/radius/count/width/...` 等 13 个数值键与 7 个字符串键（progress.py:33-35、58-87），`group_ops` 把相同 brief 合并成 `n`，最多 40 组（36、90-107）；`scrub` 把 workspace 根变相对路径、其他绝对路径变 `<path>`（46-55）。
- 归属：`sessions.tag()` 在进度事件里全量注入（`client`/`conversation`/`session`，progress.py:140），在 journal 记录里**去掉 `session`（pid）**只留 `client`/`conversation`（store.py:330）——journal 是可迁移的历史，不绑定某个进程号。
- 嵌套：同图嵌套调用不单独上报（service.py:238-247），`apply_operations(create=...)` 因此是一条 run（tests/test_q_viewer_progress.py:84-92）。

### 4.8 policy 边界
- 字段与环境变量（policy.py:13-32）：`root`（`AGENTCAD_ROOT`）、`read_only`（`AGENTCAD_READ_ONLY`）、`allowed_ops`（逗号分隔的 `AGENTCAD_ALLOWED_OPS`，90-94）、`max_entities` 200000、`max_ops_per_batch` 5000、`max_new_entities_per_batch` 50000、`delete_confirm_threshold` 50、`max_file_bytes` 256 MB、`max_document_bytes` 32 MB、`allow_scripts`/`script_timeout`、`live_sync`、`allow_live_commands`。`root` 在 `__post_init__` 一次性 `resolve()`（34-35）——构造后改 `policy.root` 不再归一化。
- 路径沙箱 `resolve`（38-63）：空路径 `MISSING_PATH`；非 Windows 上出现 Windows 盘符/UNC 直接 `PATH_OUTSIDE_WORKSPACE`（46-47）；反斜杠统一按分隔符处理（48）；`resolve()` 之后再 `is_relative_to(root)`（51-55，符号链接因此被展开）；任一路径分量 casefold 为 `.agentcad` 即 `PATH_RESERVED`（56-57）；后缀白名单 `BAD_EXTENSION`（58-59）；`must_exist` 决定 `FILE_NOT_FOUND`（60-62）。
- 写闸门：`check_write` 在 service 的 12 处工具入口 + `store.commit_bytes`/`store.restore`（policy.py:65-67）；`check_scripts_allowed` 只在 `run_script`（69-73、service.py:569）；`check_live_commands_allowed` 只在 `autocad_live action=command`（75-79、service.py:1188）。
- op 白名单只作用于操作名，且只在 `engine.prevalidate` 判定（engine.py:45）；它**不限制工具**：白名单为 `{"add_line"}` 时 `export_drawing`、`rollback`、`create_drawing` 依旧可用（tests/test_security.py:40-48）。
- 预算各自为政：批操作数（engine.py:34-35）、实体总数（96-98，只数 `doc.modelspace()`）、单批新增（ops/edit.py:323-326，被 array 两处调用 258、281）、删除确认（edit.py:334 与 analysis.py:109）、文件字节（store.py:89、400）、文档字节（service.py:782）。
- 只读模式还连带关掉状态目录迁移（service.py:208 `migrate=not read_only`）与进度上报（232），但**不**阻止取锁与状态目录创建：`read_only` 下的 `apply_operations(dry_run=True)` 仍走 `_lock`，`drawing_lock` 的第一步就是 `mkdir`（locking.py:17）。

### 4.9 分层规则与依赖方向
- 依赖图（对 `src/agentcad` 全部 73 个模块做 ast 解析，含相对导入）：扇入最高的是 `errors`(49)、`model`(24)、`policy`(15)、`geom`(15)、`units`(12)、`ops.registry`(10)、`store`(7)。
- 叶子模块（无任何内部导入）：`errors`、`geom`、`appearance`、`desktop`、`drawing_frontend`、`worker`、`engineering`、`backends._fc_view`、`_script_runner`、`_image_runner`。`errors` 是唯一的「公共叶子」，`geom` 是第二层基础。
- 中心：`service` 依赖 35 个内部模块，其中 15 个在模块级导入（service.py:24-35），其余 20 个在函数内延迟导入（viewer 259/268、plot 998、tabular 1031、quality 1377、engineering.* 1282-1308、backends 279）。延迟导入是这套分层能成立的实际手段，也是 `viewer_link.py` 独立存在的原因（viewer_link.py:1-6：导入 viewer 要付 ezdxf 的一秒）。
- 适配层实际依赖：`cli` → service + install/demo/setup_gui/viewer/viewer_link/mcp_server/mcp_config/http_api；`http_api` → service/policy/errors（最干净）；`mcp_server` → service/sessions/worker/policy/backends/install。
- 循环依赖 5 组（engine 参与的那组有两条环路），全部靠延迟导入或调用方向化解：`model ↔ tolerances`（model.py:253 在函数内导入 tolerance_info）、`ops.edit ↔ ops.annotate`、`validate.checks ↔ validate.drafting`、`ops → ops.engineering → engineering.records → engine → ops`、`service ↔ viewer`（viewer.py:281、378 在函数内导入 service）。这些不是可运行错误，但意味着任何「按目录分层」的静态规则都会误报。
- 越层导入（有据可查）：`store → sessions`（store.py:32、330，持久化层给记录盖「谁做的」）；`validate.checks → plot`（checks.py:217，验证器为了查字形导入出图模块）；`ops.analysis → validate.checks`（analysis.py:85，操作用验证器的 `geometry_key`）；`tabular → engineering.pid/records`（tabular.py:25、33）；`quality → delivery`。
- 现有分层约定是被测出来的最小集合：适配层不得导入 `ezdxf`/`ops`/`engine`（tests/test_g_interfaces.py:208-221），实现方式是「导入名按 `.` 切分后与这三个词取交集」——`from .model import select` 这类越层不会被它拦住。

**扩展点**
- 新增一个工具（尤其是不写图、需要延迟导入的读工具）：在 service.py 里加 `@tool` 方法即可，`TOOLS` 自动登记（105），HTTP/CLI 的名字派发也跟着生效；若要进度或 viewer 联动，必须显式加进 `PROGRESS_TOOLS`（48-51）/`VIEWER_TOOLS`（46）/`LIVE_SYNC_TOOLS`（42-43）之一。
- 新增错误语义：错误码是自由字符串，直接在抛出点写；但要跨适配层区分「可重试」，必须显式传 `retryable=True`，并在新类别出现时同步 `errors.CATEGORIES`（19）与 `cli.EXIT`（16-17）。
- 新增单位：`units.UNITS` 加一项（同时给出 $INSUNITS code 与每单位毫米数）、`CODE_TO_UNIT` 自动生成（30）、按需补 `ALIASES`；`setup_document` 的 `$MEASUREMENT` 例外集合（service.py:160）也要跟着改。
- 新增元数据键：`metadata.set_meta` 即可，无需改读路径；但要决定读侧是否 `strict=True`（坏值失败关闭 vs 显示 null），并接受「没有 schema 版本」这一事实。
- 新增策略开关：在 `Policy` 加字段 + 环境变量 + 一个 `check_*` 方法（policy.py:65-79 的模式），然后把闸门放在 service 的工具入口，不要放在 engine（engine 只看 op 名）。
- 新增进度事件类型：`Activity.emit(state, **fields)` 任意扩展字段，viewer 侧按 `state` 分支；`op_brief`/`group_ops` 的白名单（progress.py:33-36）决定新参数是否会出现在摘要里。

**风险与可疑之处**
- [中] 乐观检查与落盘之间仍有窗口 — service.py:480 的复检与 store.py:439 的 `os.replace` 之间没有锁能拦住非协作写者（AutoCAD 保存、另一个工具）；POSIX 上 `os.replace` 永不因读者失败，Windows 只靠共享冲突。影响：外部改动被并进本次提交并记为一次正常提交。建议：在 `_replace_tracked` 内、`replace_file` 之前再比一次当前文件 sha 与 `head["sha256"]`，不等则按 `STALE_REVISION` 拒绝。
- [中] 检查点无上限 — store.py:350-367 每次提交都整份 `copy2` 一份图纸并追加进 `head["checkpoints"]`，全仓库只有 `_abandon`（486）会移除；`_write_head` 每次重写整份 head（260-264）。影响：长寿命图纸的状态目录与 head.json 单调增长，200k 实体图每份副本可达数十 MB。建议：加保留策略（保留最近 N 个 + 每个 label 首次）或按需 GC，并记录在 head 里。
- [中] 实体预算只数模型空间 — engine.py:96-98 用 `len(doc.modelspace())`，而 `run_script` 数全部布局（service.py:590）。影响：纯图纸空间的批量插入绕过 200k 上限，而之后对该图调用 `run_script`（service.py:590 数全部布局）会被判超限，留下一个只有 `run_script` 才拒绝的图。建议：统一为 `sum(len(sp) for sp in layout_spaces(doc, "*"))`。
- [中] `expected_revision` 单独使用时形同虚设 — 修订号只在提交成功时 +1（store.py:421、458、526），外部直写不改变它；`guards.check` 若没同时给 `preconditions.sha256` 就发现不了外部修改（guards.py:44-45）。影响：只按文档传 `expected_revision` 的调用方以为自己做了乐观并发。建议：缺 `preconditions` 时把 `sha256` 作为默认条件一并比对。
- [中] `preconditions={"entities": {}}` 是静默空操作 — guards.py:26-36 只校验「非空 dict 且键在允许集合内」，43-49 随后什么都不比。影响：调用方以为钉住了图纸，实际没有任何检查。建议：要求 `sha256` 与 `entities` 至少一项非空。
- [中] 适配层有第二套错误形状 — http_api.py:54-71 与 cli.py:35 手写 `{code,category,message}`，缺 `retryable`/`partial_changes`。影响：客户端按 `retryable` 分支会崩或误判；domain 路径与传输路径的契约不一致。建议：三条适配层统一走一个 `AgentCADError(...).to_dict()`。
- [低] `AgentCADError` 的类别用 `assert` 校验（errors.py:38）：`python -O` 下消失，非法类别会带着错的值进信封。建议：改 `ValueError`。
- [低] `set_tag` 整体覆盖 AGENTCAD 的 XDATA 值列表（model.py:54-60）：同一 appid 下若还有别的值（未来扩展位），`set_properties(tag=...)` 会静默丢弃。建议：只替换 `tag=` 那一项。
- [低] `replace_file` 的失败语义与平台不符 — store.py:78-79 在非 Windows 上直接抛，而调用方 440-443 一律解释成「被别的进程占用」并置 `retryable=true`。影响：真实的权限/ACL 问题被报成可重试的瞬时冲突（tests/test_p_busy_files.py 覆盖的是 Windows 行为）。建议：按 `os.name` 分流错误码。
- [低] `$MEASUREMENT` 只写不读（service.py:160），校验只对缺 `$INSUNITS` 发警告（validate/checks.py:426-428）：无单位图纸能通过验证，却在任何带单位字符串上失败。建议：把无单位定为 `error` 或至少在 `read_drawing` 里显式标 `units: null` 的原因。
- [低] 指标双记 — `apply_batch` 调的是被装饰的 `self.apply_operations`（service.py:543），而其它嵌套点用 `TOOLS[...]`（509、809）绕过信封；前者每个 job 多出一条 metrics 与一层信封。建议：统一嵌套约定。
- [低] 分层只有黑名单测试 — tests/test_g_interfaces.py:221 只查 `ezdxf`/`ops`/`engine` 三个词，`store → sessions`、`validate.checks → plot` 这类越层无人拦。建议：加一张允许的层间边表。

**测试对应**
- tests/test_z_locking.py — 锁不可按年龄/pid 抢占、崩溃释放、legacy 标记拒绝与 `recover` 不删标记（locking.py、service.py:900-902）。
- tests/test_p_busy_files.py — `FILE_BUSY` 的每个分界：替换前可重试且图纸不变、替换后 `partial_changes` + `recover` 结算、committed 副本忙只发 warning、settle 失败保留上一次 marker（store.py:408-534）。
- tests/test_f_repro_recovery.py — journal 重放、检查点哈希校验、legacy 状态迁移归属、尾部倒读分页（store.py:163-309）。
- tests/test_t_workflow_guards.py — `expected_revision`/`preconditions` 的 STALE 判定、跨进程重开后仍生效、属性指纹拒绝陈旧块编辑（guards.py）。
- tests/test_d_errors.py — 8 个类别的可区分性、失败批次磁盘不变且写 journal、需求字段不被静默忽略（errors.py、service.py:460-463）。
- tests/test_security.py — 路径穿越/绝对路径、覆盖保护、只读、op 白名单、预算、并发写锁（policy.py、engine.py:34/45/96）。
- tests/test_q_sessions.py — session 文件内容与 `tag()`、pid 复用不算会话、viewer 列出会话（sessions.py）。
- tests/test_q_viewer_progress.py — working/checking/done/error 四态、绝对路径脱敏、只读不写进度、嵌套 create 只算一次 run（progress.py、service.py:228-249）。
- tests/test_g_interfaces.py — CLI/MCP/HTTP 共用 `Runtime`，且适配层不导入 ezdxf/ops/engine（分层约定的唯一机器检查）。

## 5. 状态、文件与恢复（.agentcad/、检查点、journal、提交、recover）

**规模**：8 个文件 / 3924 行（store.py 579、locking.py 51、guards.py 62、service.py 1590、sessions.py 177、progress.py 212、viewer.py 1159、policy.py 94）

**职责与边界**
这一层负责"图纸之外的持久状态"：每张图纸的状态目录、写锁、检查点、只追加 journal、带 pending 标记的原子提交、崩溃恢复，以及跨会话/跨进程的旁路文件（sessions、activity、live.json、viewer.json、viewer-cache）。
它不做几何、不做规格编译，也不决定"什么算一次成功改动"——那由 `service.Runtime` 的方法体决定，本层只提供原语并维持其不变量。
调用方向是单向的：`service` → `store` / `locking` / `guards` / `progress` / `sessions`；`store` 只再依赖 `policy`、`sessions`（写 journal 时打标）与 `locking`。
`viewer` 复用 `store` 以只读方式读版本与 journal，但从不写图纸；`policy` 是路径与权限的唯一定义处；`guards` 是与 journal 序列无关的乐观并发前置条件。
边界之外——DXF 语义、验证器、事务内几何执行——都不在本区域。

**状态目录布局**（以代码为准，store.py:143-161，service.py:56、217、576）
```
<root>/.agentcad/
  drawings/<顶层且名字不含 __ 的图名>/          # 旧式扁平布局，仍被沿用
  drawings-v2/<相对父目录>/.agentcad/<文件名>/   # 其余情况：镜像层级 + 保留段
    head.json            seq / sha256 / revision / pending / checkpoints[]
    journal.jsonl        一次调用一行：成功与失败都写
    checkpoints/cp-NNNN.dxf   改动前整文件字节副本
    committed.dxf        最近一次提交的精确副本（崩溃恢复用）
    failed/              strict 校验失败与损坏现场（时间戳命名）
    write.lock           OS 写锁，进程退出即释放
    live.json            AutoCAD 打开时的 base_sha256（仅 live 路径）
    scripts/job-<ms>/    run_script 的临时作业目录，finally 里删除
  sessions/<pid>.json    每个 MCP 进程一个
  activity.jsonl(.1)     进度事件，512 KB 轮转
  viewer.json            运行中的查看器 {url, pid}
  viewer-cache/          按 sha 命名的内容缓存（渲染、版本副本）
  acad/                  AutoCAD 转换作业目录（所有图纸共享）
  live/pull-<ms>.dxf     AutoCAD pull 的中间文件，失败时故意保留
```

**文件清单**
| 文件 | 行数 | 角色 | 职责 |
|---|---|---|---|
| store.py | 579 | 核心 | 状态目录布局、head/journal、检查点、原子替换、pending 结算与恢复原语 |
| locking.py | 51 | 支撑 | 每图纸非阻塞 OS 写锁（msvcrt/flock），拒绝抢占旧版 `lock` 标记 |
| guards.py | 62 | 支撑 | `expected_revision` / `preconditions` 的乐观校验与实体 fingerprint |
| service.py | 1590 | 入口/核心 | 工具注册与信封、加锁调用链、提交与 journal 记录、recover/rollback/history 编排 |
| sessions.py | 177 | 数据 | `<root>/.agentcad/sessions/<pid>.json`，进程存活与 pid 复用判定 |
| progress.py | 212 | 数据 | 追加 `activity.jsonl` 的进度事件、轮转、绝对路径抹除 |
| viewer.py | 1159 | 适配 | 本地只读 HTTP/SSE 查看器；把 3D 零件的保存记为版本（checkpoint+journal） |
| policy.py | 94 | 支撑 | 沙箱路径解析、`.agentcad` 保留、只读检查、操作白名单与预算默认值 |

**关键入口**
- `DrawingStore.lock`（store.py:213）— 上下文管理器；存在旧式扁平目录时先在旧目录加锁、用 `_legacy_head()` 校验归属、暂存到临时目录后 `replace_file` 整体迁移，再对最终目录加锁（store.py:216-249）；`migrate=False`（只读策略）时直接用旧目录。
- `DrawingStore.__init__`（store.py:137）— 决定 `_legacy` 与 `_state`：`flat = relative.replace("/", "__")`，只有 `flat` 含 `__` 时才把扁平目录视为"可能有主的旧目录"（store.py:143-150）。
- `_set_dir`（store.py:156）— 状态目录内四个名字的唯一出处：`checkpoints/`、`journal.jsonl`、`head.json`、`committed<ext>`。
- `commit` / `commit_bytes`（store.py:384、396）— 前者把 doc 序列化成字节，后者做体积与 `overwrite_ok` 检查，然后都交给 `_replace_tracked`。
- `_replace_tracked`（store.py:408）— 提交事务：结算旧 pending → 检查点 → 写 pending（带目标 sha 与 revision）→ tmp+fsync+`os.replace` → 刷新 `committed` → 清 pending 写 head。返回 `{checkpoint_before, sha256, bytes, revision}`。
- `journal`（store.py:324）— 只追加一行 JSON；`seq = max(head["seq"], 文件末条 seq) + 1`（store.py:327）；head 写失败被吞（store.py:333-336）。
- `journal_page` / `journal_newest` / `_journal_tail`（store.py:303、298、266）— 从文件尾部按 64 KB 块反向读，游标是"记录起始字节偏移"，必须落在换行边界上（store.py:274-280）。
- `checkpoint`（store.py:350）— 复制当前文件为 `cp-NNNN.dxf`，`NNNN = len(head["checkpoints"]) + 1`（store.py:356），并把副本自身 sha 记入 head；head 写失败则删副本再抛（store.py:362-366）。
- `checkpoint_path`（store.py:372）— id 必须同时出现在 head 与磁盘，否则 `CHECKPOINT_NOT_FOUND` 并列出可用项。
- `settle_pending` / `finish_pending`（store.py:496、513）— 中断提交的判定：文件 == 目标 sha → `completed`（补写 committed 与 head）；== 上次提交 sha → `discarded`；其余 `None`。结算会写一条 `recover` 记录并补 `completed_commit`、`checkpoint_before`、`completed_seq`（store.py:502-511）。
- `_abandon`（store.py:470）— 替换前失败时撤销本次事务：先改 head（有则弹掉检查点、清掉自己的 pending），head 写成功后才删检查点文件；只清自己的 marker，不碰前一次未结算的。
- `restore`（store.py:536）— `cid="committed"` 或检查点 id；先校验字节 sha 与记录一致（store.py:543-548），再走与提交同一条替换路径，因此回滚本身也可产生 `FILE_BUSY`。
- `status`（store.py:563）— 文件存在性、可解析性、pending、`committed_sha256`/`actual_sha256`、`revision`、`modified_outside_runtime`、检查点数量。
- `replace_file`（store.py:67）— Windows 上 `os.replace` 遇 `PermissionError` 时按 5 ms→100 ms 退避重试到 `REPLACE_TIMEOUT = 2.0 s`（store.py:53），`busy_error` 决定 `retryable` 与 `partial_changes`（store.py:56-64）。
- `drawing_lock`（locking.py:16）— `write.lock` 上 `LK_NBLCK`/`LOCK_EX|LOCK_NB`，失败抛 `DRAWING_LOCKED`（policy 类，retryable）；文件不 unlink，避免 POSIX 上锁被拆到两个 inode。
- `refuse_legacy_marker`（locking.py:43）— 旧 `lock` 标记一律不偷、不按时间猜死，只能离线处理。
- `guards.check`（guards.py:22）— 比较 revision、`sha256_file` 与逐 handle 的 DXF 标签指纹（guards.py:14-19，含 ATTRIB），不符抛 `STALE_REVISION` 并给出 `changed_handles`。
- `Policy.resolve`（policy.py:38）— 拒绝出根、拒绝任何一段等于 `.agentcad`（policy.py:56-57）、扩展名白名单；`state_dir()` 单点定义（policy.py:86）。
- `Runtime._lock`（service.py:201）— 用 `ContextVar` 做同路径重入（delivery 持源锁导出），否则 `store.lock(migrate=not read_only)`。
- `Runtime._save_failure`（service.py:215）— 把被拒字节写进 `failed/<时间戳>-<label>.dxf`，失败返回 `None` 而不影响主流程。
- `Runtime.recover` / `rollback` / `history` / `get_diff`（service.py:893、854、878、828）— 恢复、回退、journal 消费与 diff 的对外入口。
- `sessions.tag`（sessions.py:45）— journal 与进度事件里的 `client`/`conversation`；pid 只在进度事件里出现。
- `progress.Activity`（progress.py:114）、`log_path`（progress.py:110）、`begin/end`（progress.py:194-199）— 一次调用一次 run，事件以 `run`+`n` 定位。
- `Workspace.record_save` / `timeline` / `snapshot` / `versions`（viewer.py:494、279、310、270）— 3D 版本记录、带游标的 journal 分页、按 sha 取历史版本。
- `ChangeFeed`（viewer.py:640）— 0.3 s 轮询每个图纸的 `(mtime,size)` 三元组，`rescan` 1 s 重扫目录；SSE 复用同一套 id。

**数据结构与不变量**
- head 缺省值为 `{"seq":0,"sha256":null,"pending":null,"checkpoints":[]}`（store.py:258）；`revision` 由提交自增并在 head 中落盘（store.py:421、458）。
- `pending = {label, checkpoint, ts, sha256, revision}`；`sha256` 是**目标**内容，供 `recover` 判断替换是否已发生（store.py:420-428）。
- journal 记录固定前缀：`seq`、`ts`（UTC ISO 毫秒）、`drawing`（相对 posix）、`versions{agentcad,ezdxf,python}`（store.py:38、329-330），再并入 `sessions.tag()` 里除 `session` 外的键（即 client/conversation，pid 被刻意丢弃，store.py:330）。
- seq 每图纸单调递增且不回退，`max(head, 文件末条) + 1`（store.py:327）；head 写失败不影响下一条（store.py:333-336）；`_last_journal_seq` 跳过不可解析的记录（store.py:311-322）。
- 提交顺序不可换：结算 → 检查点 → pending → 替换 → committed → head → journal；journal 最后写，因此 head 的 seq 可能落后于文件（store.py:312 的注释即为该假设）。
- 替换之前的一切失败都必须是"文件未变 + retryable"；替换之后的一切失败都必须是 `partial_changes=true`、`retryable=false`（store.py:56-64、459-466）。
- `committed<ext>` 是最近提交的精确副本；它 busy 时只降级为 `warnings`，不算失败（store.py:447-454）。
- 检查点被信任的前提是 head 里记了它的 sha；`restore` 与 `recover` 都重新哈希校验（store.py:546、service.py:923），不匹配即跳过。
- 检查点 id 由 `len(checkpoints)+1` 推出，因此 `_abandon` 弹掉最后一个后，下一个检查点会复用同一个 id——这正是 `_abandon` 在 head 写失败时必须留下文件、且"下次检查点会静默替换它"的由来（store.py:365、481-494）。
- 锁是 OS 级、随进程退出释放，绝不按 PID 或时间推断存活（locking.py:18-22、44-51）。
- 锁粒度是"每张图纸一个状态目录"：不同图纸可并行；`apply_batch` 逐个 job 顺序取锁、从不同时持两把（service.py:539-556），因此没有跨图纸原子性，也没有跨图纸死锁。
- 同一进程内的重入只覆盖同一个 `Runtime` 的同一个 `ContextVar`（service.py:189、201-213）；两个 `Runtime` 即使同进程也会互斥（tests/test_z_locking.py:49）。
- 旧目录只有在 journal 能证明归属、且无未结算 pending 时才迁移（store.py:163-211）；`live.json` 只在 `base_sha256` 与当前文件一致时才跟着搬（store.py:232-239）。
- journal 的游标是字节偏移，`journal_page` 返回的是最后一条记录的起始偏移，用于下一页（store.py:303-309）。
- viewer 永不写图纸：构造时强制 `policy.read_only = True`（viewer.py:1062）；`record=False`（只读 viewer）时连 3D 版本都不记（viewer.py:500）。
- 3D 版本记录只在"内容稳定 1 s、且与上次记录不同"时发生（viewer.py:95、507-514），复制期间文件被改写则放弃本次（viewer.py:518-524）。
- progress 与 sessions 是便利设施：写失败必须静默（progress.py:145-146、sessions.py:96-97），进度文本里的绝对路径被抹成 `<path>` 或相对路径（progress.py:46-55）。
- `sessions.alive` 用"进程创建时间 + pid"双重判定，避免 pid 复用把死会话当活会话（sessions.py:128-153）。

**主要调用链**
1. `apply_operations`（service.py:435-489）：加锁 → 读盘 sha → `guards.check`（454）→ `engine.execute`（459）→ 失败则写 `ok:false` journal 并抛出（460-463）→ `dxf_bytes`（468）→ 可选 strict 校验（469-477）→ 再次 `guards.check` 只比 sha（480）→ `commit_bytes(attempt=…)`（481）→ `journal`（482）→ 解锁后独立校验（483-484）。
2. 提交事务本体（store.py:408-468）：`settle_pending()`（415）→ `checkpoint()`（424）→ 写 pending（426-428）→ tmp 写入并 fsync（432-437）→ `replace_file`（439）→ 写 `committed<ext>`（447-451）→ 清 pending、写 sha/revision（455-460）。
3. 替换前的 busy（store.py:429-443）：`_abandon(cid, marker)` → `busy_error(applied=False)`；替换后的 busy（store.py:459-466）：若调用方给了 `attempt`，先把这次尝试作为失败记录写进 journal，再抛 `applied=True` 的 `FILE_BUSY`。
4. 下一次提交自动结算（store.py:415-418）：任何 `_replace_tracked` 开头先 `settle_pending`，所以 Agent 不调 `recover` 也不会把上次改动无记录地并进去；head 被占时整个提交以 `FILE_BUSY(retryable)` 拒绝。
5. `recover` 判定顺序（service.py:899-938）：旧式 `lock` 标记 → 拒绝（900-902）→ `store.status()`（903）→ 仅"不存在/不可读/有 pending"才继续（904）→ 可读且有 pending 时 `settle_pending(from_status=st)`，有结果即返回（908-917）→ 否则候选序列 `committed` + 最新检查点倒序（918-919），逐个校验哈希与可解析（920-927）→ 坏文件复制进 `failed/`（930-932）→ `restore` + `recover` journal（933-934）→ 全失败抛 `NO_VALID_CHECKPOINT`（937）。
6. `rollback`（service.py:854-870）：不带 `checkpoint` 时取"最后一个 sha 与当前文件不同的检查点"（864-866），因此 `_abandon` 遗留的同内容检查点不会让回滚变成空操作；回滚前的现场同样被检查点（store.py:550-557）。
7. journal 消费：`history` 走 `read_journal(limit)` 尾部读或按 seq 全量过滤（service.py:878-890）；`get_diff("previous")` 取最后一个检查点路径（834-846）；viewer 用 `journal_page` 分页并把每条记录映射到 sha→文件（viewer.py:279-307、270-276）。
8. 进度链路：`@tool` 包装器的 `_activity`（service.py:228-249）→ `progress.begin` → 引擎每步 `progress.step` → `Activity.emit` 追加 `activity.jsonl`（progress.py:136-146）→ `ChangeFeed._read_activity` 增量读取（viewer.py:666-697）→ SSE（viewer.py:997-1016）。
9. 3D 零件版本：`ChangeFeed._poll` 发现内容变化（viewer.py:746-770）→ `record_save`（494-529）加锁 → `checkpoint("saved")` → 写 `save` journal 记录；启动时已存在的文件 `announce=False`（viewer.py:499、767）。
10. 会话：`Session.__init__` 写 `<pid>.json` 并 `atexit` 删除（sessions.py:58-67、99-103），`note` 最多每 2 s 写一次（sessions.py:87-89），`list_sessions` 清理死进程并过滤"连上但没用过"的会话（sessions.py:156-177，viewer.py:862-864）。

**扩展点**
- 新增状态文件名：改 `_set_dir`（store.py:156-161），并确认 `policy.resolve` 的 `.agentcad` 保留规则不被绕过（policy.py:56-57）；跨图纸共享的临时目录按 `state_dir()/"acad"`、`"live"` 的先例放（service.py:984、1212）。
- 新增 journal 记录类型：必须在已持锁的上下文里调用 `store.journal`，并在 `_journal_line`（service.py:1489-1511）里显式白名单新字段，否则 `history` 精简视图看不到它；读取端对未知记录已容错（store.py:311-322）。
- 新增工具：`@tool` 装饰器同时注册进 `TOOLS`（service.py:105）；要接进度/查看器/AutoCAD 需分别登记 `PROGRESS_TOOLS`、`VIEWER_TOOLS`、`LIVE_SYNC_TOOLS`（service.py:42-51）。
- 新增恢复结局：在 `finish_pending` 增加分支（store.py:513-534），再由 `settle_pending` 组装 `recover` 记录，才能带上 `completed_commit`/`completed_seq`（store.py:502-511）。
- 新增查看器端点：加到 `ROUTES`（viewer.py:84），实现成 `Workspace` 方法并套 `@_retry`（viewer.py:114-127），只读路径不得写图纸。
- 新增可识别的助手：往 `sessions.CLIENTS` 加 `(匹配子串, 显示名)`（sessions.py:27）。
- 新增进度状态：只在 `Activity.emit` 的 `state` 里加值（progress.py:136），页面文案由 viewer_static 负责，运行期只写数据（progress.py:10-12）。

**风险与可疑之处**
- [高] 检查点无上限、无清理 — `checkpoint` 每次整文件复制，只向 `head["checkpoints"]` 追加（store.py:350-367）；除 store.py:365 的写 head 失败回滚外，唯一删除点是 `_abandon`（store.py:470-494）。`head.json` 每次重写、`versions()`/`get_diff`/`status` 都随列表线性增长（viewer.py:270-276、service.py:834-839、store.py:576）。5 MB 图纸上千次改动即 GB 级。建议加 `Policy.max_checkpoints`，在 `_replace_tracked` 里保留 `committed` + 最近 N + 带 label 的检查点。
- [高] journal 无轮转、无上限，且一条坏行就能打断读取 — 对比 `activity.jsonl` 有 512 KB 轮转（progress.py:31、129-134），`journal.jsonl` 只追加（store.py:331-332）；`journal_newest`/`read_journal` 直接 `json.loads`（store.py:298-301、343-347），崩溃留下的半行会让 `history`、viewer timeline、以及持 pending 时的 `settle_pending` 全量扫描（store.py:506）全部报 `INTERNAL_ERROR`，而写入端连 fsync 都没有。建议尾部读跳过坏行，并按大小轮转（轮转时保留地板 seq）。
- [中] `head.json` 损坏无法自愈 — `head()` 直接 `json.loads` 无兜底（store.py:255-258），而 `recover` 第一步就调 `store.status()`（service.py:903），最需要恢复的情形在到达检查点候选循环前就抛错；journal 里其实有重建 head 所需信息（每条 ok 记录的 `sha256` 与 `checkpoint_before`）。建议 `head()` 捕获 `ValueError` 并由 journal 重建。
- [中] `recover` 可能覆盖运行期之外的编辑 — pending 目标 sha 与上次提交 sha 都不匹配时 `finish_pending` 返回 `None`（store.py:530-531），流程继续走到 `committed` 候选并整文件替换（service.py:918-936），外部改动只留在 `failed/` 的一份拷贝里。建议把"当前 sha ≠ head sha"单列并要求 `force`。
- [中] 状态目录的旁路持久化只增不减 — `failed/` 由 strict 失败与 recover 各写一份（service.py:215-223、930-932）；`acad/` 是全局共享作业目录且"失败作业故意保留"（service.py:984、1083、1103）；`live/pull-*.dxf` 在失败路径被刻意保留（service.py:1212-1250）；`scripts/` 父目录不删（service.py:576、589）；`viewer-cache` 按内容寻址存整份版本与 mesh/png/thumb 且无清理（viewer.py:310-336、534-557），3D 单版本上限 200 MB（viewer.py:96、507）。建议加配额或启动时清理最旧项。
- [中] `partial_changes` 的 journal 只靠 `attempt` 参数捕获 — head.json 在替换后被占时，失败记录仅在 `attempt is not None` 时写入（store.py:459-466）；`import_drawing`（service.py:1088）与 `apply_constraints`（service.py:707）的提交都不传 `attempt`，这些路径出事后 `recover` 只能写一条没有 `operations`/`changes` 的记录，settle 的 `completed_seq` 也找不到对应尝试（store.py:506-510）。
- [低] 迁移入口的重复条件 — `not self._state.exists()` 在 store.py:152 与 153 出现两次，第二个是不可达的冗余判断；不影响行为，但让"旧目录优先"的真实意图变得含糊。
- [低] 进度轮转存在竞态 — 两个进程同时判断超限并 `os.replace` 到 `.1`（progress.py:129-134），后写的覆盖先写的；读取端以"文件变小即视为轮转"兜底（viewer.py:674-677），代价是一段事件丢失。
- [低] 锁重入是每 `Runtime` 一个 `ContextVar`（service.py:189、201-213），不跨线程；任何把 `_lock` 结果跨线程复用的改动都会退化成二次加锁失败。

**测试对应**
- tests/test_f_repro_recovery.py — journal 尾部读取与 UTF-8 长记录（:67）、同名冲突的旧目录检查点分离（:94）、回滚拒绝哈希不符的检查点（:104）、recover 跳过不可读检查点（:115）、旧目录迁移与混合历史保留（:136-217）、中途失败后回滚与崩溃恢复（:232-247）。
- tests/test_p_busy_files.py — `FILE_BUSY` 在替换前/后的 retryable 与 `partial_changes` 区分（:56、:81）、下一次提交先结算未记录改动（:116）、recover 清掉未发生的标记（:141）、head 繁忙时 journaling 仍保留 seq（:152）、recover 记录带上操作（:342）。
- tests/test_z_locking.py — 旧 `lock` 标记不按时间抢占、崩溃即释放（:12、:59）、同进程两个 runtime 不能重叠（:49）、recover 不读不删旧标记（:69、:88）。
- tests/test_q_sessions.py — 会话文件、`tag()`、pid 复用、viewer 列表（:40-:141）。
- tests/test_q_viewer_progress.py — 批次进度事件序列、失败不含绝对路径、嵌套调用只算一次 run、只读不写进度、日志轮转跟随（:50-:157）。
- tests/test_o_viewer.py — timeline 与版本的映射与分页、SSE、viewer 不写工作区、3D 零件保存成版本、缓存落在工作区（:65-:1033）。
- tests/test_e_tamper.py / tests/test_d_errors.py — 外部修改被 `status()` 标为 `modified_outside_runtime`（:75）、失败批次文件不变且写入 journal（:102）。

## 6. 扩展指南：改什么、按什么顺序

这一章是“从需求反查文件”的索引。每条路径都给出**注册点**、**自动生效的入口**、以及**必须同步改的测试/文档**——AgentCAD 的四种入口（CLI/MCP/HTTP/Python）共享 `service.Runtime`，多数能力只写一次，但测试与文档不会自动跟。

### 6.1 总表

| 想做的事 | 主改文件 | 自动生效 | 必须同步 |
|---|---|---|---|
| 新增绘图/编辑操作 | `ops/<category>.py`（`@op` 声明）+ `ops/__init__.py` | `list_operations`、`apply_operations`、MCP | `scripts/gen_docs.py` → `docs/operations.md`、测试 |
| 新增验证规则 | `validate/checks.py` 或 `validate/drafting.py` | `apply_operations` 返回值里的 `validation`、`validate_drawing` | `docs/architecture.md` 验证器章节、测试 |
| 新增 requirement 类型 | `validate/requirements.py` | `draw_from_spec` / `validate_drawing(requirements=…)` | `spec.py` 产出 requirements 的地方 |
| 新增对外工具 | `service.py`（`@tool`） | CLI/HTTP/Python、`TOOLS`、metrics | MCP（显式或自动注册）、`tests/test_g_interfaces.py` |
| 新增导出格式 | `service.export_drawing`（+`plot.py`） | `export_drawing`、`deliver_drawing` | `docs/architecture.md`、README 能力表、测试 |
| 新增 spec 类型 | `spec.py`（词汇表 + `compile_spec`/`merge`）、`docspec.py` | `draw_from_spec`、`revise_drawing`、`preflight` | `tests/test_a_spec_to_drawing.py`、`test_b_revision.py` |
| 新增标准件 | `stdlib.py`（尺寸表）+ `ops/library.py`（`add_standard_*`） | `standard_library`、library 操作 | `docs/` 标准表、测试 |
| 新增工程领域 | `engineering/<domain>.py` + `ops/engineering.py` + `engineering/records.py` + `service.read_engineering` | `read_engineering`、工程操作 | `quality.py` 的评分、`docs/engineering-workflows.md` |
| viewer 新面板/新事件 | `viewer.py` + `web/viewer/` 源码（构建产物进 `viewer_static/`） | 浏览器页面 | 重新构建静态页、`tests/test_o_viewer.py` |
| 改事务/持久化 | `store.py`/`guards.py`/`service.py` | 全部写入路径 | 见 6.9，高风险 |

### 6.2 新增一个绘图/编辑操作

1. 在 `src/agentcad/ops/<category>.py`（draw / edit / annotate / library / sheet / structure / analysis / engineering）里写执行函数，用 `@op(name, category, description, params, common_draw=?)` 声明（`ops/registry.py:119`）。`params` 是 `{参数名: Param(...)}`，一份声明同时驱动参数校验、`list_operations` 的 JSON Schema 和文档生成。
2. 执行函数接收 `(ctx, **params)`；`ctx` 是 `ops.registry.Ctx`，提供文档、当前布局、`ctx.added/modified/deleted` 记账、tag 索引。返回 `{"created": [handle...]}` 或 `{"modified": [...]}` 或 `{"deleted": [...]}`/`{"unchanged": [...]}`（`engine.execute` 在 `engine.py:87-88` 按这个形状记录结果并供 `$<i>` 引用）。
3. 若新增了模块文件，在 `ops/__init__.py:2` 的 import 里加上（否则 `REGISTRY` 里没有它，`apply_operations` 会报 `UNKNOWN_OP` 并给相近名建议，`engine.py:41-44`）。
4. 参数类型用 `Param` 的 `type`/`gt`/`ge`/`le`/`enum`；单位型参数会经过 `coerce`（`ops/registry.py:272`）把 `"8mm"` 换算成图纸单位。
5. 重新生成文档：`.venv\Scripts\python scripts\gen_docs.py`；CI 的 `gen_docs.py --check` 会拒绝 `docs/operations.md` 与代码不一致。
6. 测试：照 `tests/test_h_operations.py`（操作级）或 `tests/test_i_common_ops.py`（通用行为）写；`python -m pytest tests/test_h_operations.py --basetemp=<短路径>`。
7. 想被 spec 修订复用（`revise_drawing` 原地更新同 tag 实体）时，注意 `engine._reconcile_op`（`engine.py:109`）→ `update_in_place`（`engine.py:165`）只支持 `COPY_ATTRS` 白名单里的实体类型（LINE/CIRCLE/ARC/POINT/TEXT/MTEXT/DIMENSION/INSERT/ELLIPSE，共 9 类）、LWPOLYLINE 和 SPLINE。新 op 产出别的类型，修订时会抛 `UNSUPPORTED_ENTITY`。
8. 只读检查用 `apply_operations(dry_run=True)`：走 `engine.execute` 但不提交，`service.py:435` 起的分支返回 `preview`。

### 6.3 新增一条验证规则

- 通用完整性检查写在 `validate/checks.py:420` 的 `run_generic_checks`（按主题分段：坐标、XDATA、文字、标注、布局、单位、图纸边界……），用 `issue(code, severity, message, entities, **details)`（`validate/checks.py:123`）构造问题；`severity="error"` 会让 `validation.passed=false`。新错误码要同时考虑 `errors.py` 里的分类（`category`、`retryable`）。
- 只读可视性/出图检查（文字过小、字体缺字形）在 `validate/drafting.py`，字体表来自 `plot.font_cmap/plot_fonts`（`validate/checks.py:217`，函数内导入避免加载期环）。
- 与“需求”比较的规则（尺寸/孔径/轮廓）在 `validate/requirements.py`；特征值先由 `validate/features.py::extract` 抽取。
- 改动后同步 `docs/architecture.md` 的验证器章节，并补一个“坏图必须被抓住”的测试（现有范例：`tests/test_d_errors.py`、`tests/test_e_tamper.py`；本地基准 `benchmarks/quality.py` 的 `validation_catches` 也断言越界必报）。

### 6.4 新增一个对外工具（Runtime tool）

1. 在 `service.py` 里加方法并装饰 `@tool`（`service.py:67`）。装饰器统一产生：`{"ok", "tool", ...}` 信封、异常转错误信封、`metrics`（耗时/入出字节）、live/viewer 联动。
2. HTTP 与 Python 无需再改：`http_api.py` 用 `getattr(rt, name)` 分发到 `TOOLS`。
3. MCP 有两条路：在 `mcp_server.py` 显式写 `@mcp.tool`（现有 26 个，可自定义给 agent 看的参数描述与返回值整形），或把方法名加进 `mcp_server.py:432` 的自动注册元组（现有 9 个：preflight、measure_entities、verify_dimensions、refine_drawing、deliver_drawing、search_tools、read_engineering、understand_drawing、plant_takeoff——直接复用 Runtime 的 docstring 作为工具描述）。
4. CLI 需要时在 `cli.py:38` 的 `build_parser()` 加子命令（命令名不必等于工具名，例如 `create` ↔ `create_drawing`；`cli.py:398` 是分发）。
5. 测试 `tests/test_g_interfaces.py` 有一条“四种入口共享一个实现”的检查；新增工具要确认它仍然通过。

### 6.5 新增导出格式

- `service.export_drawing`（`service.py:941`）维护格式→后缀映射与分支；矢量/PDF/PNG 类格式复用 `plot.py` 的布局、视口、比例、字体管线；表格类（CSV/XLSX）走 `tabular.py`。
- 交付包（多格式 + 评分门禁）在 `delivery.py`，它有自己的格式集合，改 `export_drawing` 时要一起看，否则两处能力会漂移。
- 必须同步：README 的“File formats”能力表、`docs/architecture.md`、`tests/test_k_pdf.py`（PDF 路径）或新增测试。

### 6.6 新增 spec 类型或文档抽取规则

- `spec.py`：把新词汇写进校验/合并逻辑，在 `compile_spec` 里产出 `{operations, requirements, plan_tags}` 三件套；`merge` 决定 `revise_drawing` 能改哪些字段。
- `docspec.py`：从 PDF/TXT/MD 抽取事实（带引用校验）→ 组装 spec；`extract_requirements` / `revise_from_document` 在 `service.py:776`、`service.py:789`。
- 测试：`tests/test_a_spec_to_drawing.py`（生成）、`tests/test_b_revision.py`（合并/调和）、`tests/test_c_document.py`（文档）。

### 6.7 新增标准件或工程领域

- **标准件**：`stdlib.py` 的尺寸表 + `ops/library.py` 的 `add_standard_*` 操作 + `standard_library` 查询（`service.py:1257`）。P&ID 符号表在 `engineering/pid.py` 与 `ops/library.py`。
- **工程领域读取**：`engineering/<domain>.py` 负责解析/记录，`engineering/records.py` 负责把领域记录写进 XDATA/rootdict，`ops/engineering.py` 暴露成操作，`service.read_engineering`（`service.py:1300`）按 `kind` 分派——四处都要动。评分/门禁在 `quality.py`。

### 6.8 给 viewer 加功能

- 后端在 `viewer.py`（HTTP 服务、事件/轮询数据、页面装配）；页面源码在 `web/viewer/`，构建产物提交到 `src/agentcad/viewer_static/app/`（`.gitattributes` 标了 `-text` 保持字节一致）。
- 进度/状态数据来自 `progress.py` 写的 `.agentcad/activity.jsonl`；加新事件类型要同时改写入端（`progress.Activity.emit`）和页面端。
- 安全相关（Host 校验、`--allow-remote`）在 `viewer.py`，改监听/暴露范围时要连带检查，`tests/test_o_viewer.py` 覆盖。

### 6.9 改事务与持久化：高风险清单

动 `store.py`（commit/restore/journal/pending）、`guards.py`（revision/sha/preconditions）、`service.apply_operations` 的顺序时，必须同时确认：

1. **原子性**：失败路径要么文件未变、要么错误里带 `partial_changes=true` 且 `pending` 标记可被 `recover` 收敛（`store.py`、`locking.py`）。
2. **修订号**：`expected_revision` 与 `preconditions` 在提交前二次校验（`guards.check`，`service.py` 中两处调用）。
3. **journal 兼容**：`history`/`get_diff`/`rollback` 读的是既有格式；加字段可以，改语义会让旧工作区读不懂。
4. **检查点**：提交即产生检查点，`rollback` 依赖“最新一个与当前文件不同的检查点”的选取规则（`service.py:854` 起）。
5. **测试**：`tests/test_f_repro_recovery.py`（崩溃恢复）、`tests/test_p_busy_files.py`（文件占用）、`tests/test_e_tamper.py`（外部篡改）、`tests/test_g_interfaces.py`（入口一致）。
6. **性能**：事务层固定成本已经接近单次调用的主导项（见第 7 章实测：1 个 op ≈ 50 ms，500 个 op ≈ 380 ms），加一次全文件写/读/复检都会直接放大它；改完跑 `python -m benchmarks`。

## 7. 热点、代价与风险清单

这一章只列**有证据**的项：代码体量、本机实测数字、可直接 grep 到的实现事实。每条给出“为什么值得管”和“改之前先看什么”，不重复各模块小节里的普通说明。

### 7.1 代码热点（体量 × 影响面）

| 文件 | 行数 | 为什么是热点 | 改动风险 |
|---|---:|---|---|
| `service.py` | 1,590 | 唯一业务门面，35 条内部出边；所有工具信封、锁、journal、复检都在这里编排 | 改一个工具的语义会波及四种入口；容易继续堆能力变成上帝模块 |
| `store.py` | 579 | 原子提交、检查点、journal、pending/recover、原子替换全在这里 | 出错会丢用户数据；必须连带跑崩溃恢复/文件占用测试 |
| `ops/edit.py` | 1,076 | 71 个操作里 23 个是 edit 类（trim/fillet/offset/array/break/join/purge…），几何分支最多 | 几何边界情况多，靠测试兜；reconcile 修订走 in-place 更新白名单 |
| `viewer.py` | 1,159 | 自带 HTTP 服务、页面数据、事件流与安全边界 | Host/Origin 校验与 `--allow-remote` 是安全相关代码 |
| `install.py` | 919 | 安装器：探测、uvx 调用、平台差异、自更新 | 影响用户机器；外部程序路径/下载来源假设多 |
| `validate/checks.py` | 574 | 全部通用验证规则的落点 | 新规则会影响所有既有图纸的“是否通过” |
| `ops/library.py` | 673 | P&ID/GD&T/ISO 标准件符号与几何 | 领域数据大、易与 `stdlib.py` 表漂移 |
| `mcp_server.py` | 496 | 35 个 MCP 工具声明（26 个显式包装 + 9 个自动注册） | 显式包装与 Runtime 签名双写，改签名不会自动报警 |
| `engineering/mechanical.py` / `architecture.py` / `plant.py` | 485 / 462 / 443 | 工程领域读取与记录 | 领域语义重，测试覆盖相对薄 |
| `plot.py` | 466 | 纯 Python SVG/PDF/PNG 出图 | 中文字体、视口、比例的组合多 |

前 12 个文件共 8,980 行，占全仓 **46%**；其中 `service.py + store.py + ops/edit.py` 是“改错就出事”的三件套。

### 7.2 性能锚点（本机实测，结构性事实）

本机：24 核 Windows 11、Python 3.13.15、ezdxf 1.4.5；数据来自 `benchmarks/`（`python -m benchmarks`，提交 `59b8ba6≈fe2cdaa`）。**绝对值属于这台机器，看相对关系。**

| 操作 | 中位耗时 | 结构成因 |
|---|---:|---|
| `apply_operations`，1 个 op | ~50 ms | 固定成本主导：加锁 → 读整个 DXF → 执行 → 写整个 DXF → 存检查点副本 → 写 journal → **独立重读文件再验证一遍** |
| `apply_operations`，50 个 op | ~64 ms | 边际约 0.3 ms/op，固定成本几乎不变 |
| `apply_operations`，500 个 op | ~378 ms | 边际约 0.66 ms/op（实体变多后校验与序列化变贵） |
| `draw_from_spec`（27 个 op 的板件） | ~41 ms | 一次事务 + 带 requirements 的验证 |
| `read_drawing` / `validate_drawing` | ~30 / ~32 ms | 都要完整读 DXF；validate 再做全量几何检查 |
| `rollback` / `recover` | ~20 / ~13 ms | 检查点恢复 = 整文件替换 + 新提交 + 新检查点 |
| `history` / `list_checkpoints` | ~0.35 / ~0.27 ms | 只读 head/journal 尾部 |
| `export svg` / `pdf` | ~23 / ~40 ms | 纯 Python 渲染 |
| 2,000 实体：`read` / `validate` | ~139 / ~139 ms | 每实体约 70 µs 的遍历/校验成本 |
| MCP 进程启动 → `initialize` | ~660 ms | 导入 ezdxf + mcp，构建 35 个工具 |
| MCP `tools/call` 往返（read_drawing） | ~56 ms（进程内 ~32 ms） | 协议 + 单 worker 调度约 24 ms |
| 一次 8 步绘图会话 | ~290 ms | 8 次完整事务 |
| `python -m agentcad --help` | ~32 ms | CLI 延迟导入做得好，冷启动很轻 |

**结构含义**：
1. 单次调用的瓶颈不是“画几何”，而是**整文件读写 + 检查点副本 + 独立重读校验**。优化几何代码对总时长影响很小；要提速只能动提交/校验路径（比如合并写、增量校验），而那是高风险区。
2. **批量是 agent 端唯一有效的杠杆**：500 个 op 一批的成本约等于 7 个单 op 调用。
3. **上下文成本与延迟同等重要**：`list_operations` 15 KB（约 3.8k tokens）、`inspect_entities` 单页 8.3 KB、2,000 实体图一页 37 KB、MCP `tools/list` 32 KB。`AGENTCAD_TOOL_PROFILE=discovery`（`search_tools`/`tool_schema`/`call_tool` 三件套）是现有的缓解手段（`mcp_server.py:435` 起）。

### 7.3 事务与状态风险

- **检查点没有清理机制**：`store.py:356-361` 每次提交都向 `head["checkpoints"]` 追加一个整文件副本，全仓没有 prune/上限/保留策略（`rg prune|cleanup|MAX_CHECKPOINT src/agentcad/store.py` 无结果）。一张 100 KB 的图纸改 1,000 次 ≈ 100 MB 状态目录；这是按设计换“随时可撤销”的代价，但工作区会无界增长，且 `list_checkpoints`/`head()` 的解析成本随之上升。
- **`--allow-remote` 关闭整组浏览器防护**：默认只监听回环，校验 Host、Origin 与自定义头 `X-AgentCAD-Viewer`（`viewer.py:40-41`）；`--allow-remote` 打开后这些检查全关，而页面**没有认证**（`viewer.py:1066-1071` 的提示原文）。暴露到非回环地址等于把工作区图纸与时间线公开。
- **乐观并发不是 CAS**：`expected_revision`/`preconditions` 是“提交前二次校验”（`guards.check` 在 `apply_operations` 里调用两次），不提供操作系统级 compare-and-swap；外部编辑器无视锁时仍可能覆盖。文档已写明，但改这块时要保持这个诚实边界。
- **崩溃恢复的复杂度集中在 `store.py`**：pending 标记、`FILE_BUSY`、半提交状态（`store.py:454` 附近的回退说明）三者交互，`recover` 的判定顺序是唯一收敛点。任何写入路径改动都要跑 `tests/test_f_repro_recovery.py`、`tests/test_p_busy_files.py`、`tests/test_e_tamper.py`。

### 7.4 文档与发布一致性（会让新开发者误判）

| 事实 | 证据 | 影响 |
|---|---|---|
| 三份文档仍写“43 个操作”，实际 **71** 个 | `docs/coverage.md:4`、`docs/tutorial.md:151`、`docs/acceptance-report.md:122` 写 43；`docs/architecture.md` 原写 41，本 PR 已改为 71；`REGISTRY` 实测 71（edit 23 / draw 14 / annotate 13 / library 7 / layout 4 / block 4 / layer 3 / engineering 3） | 新读者低估操作面 |
| `docs/test-results.txt` 是旧环境产物 | 文件头 `rootdir: D:\Projects\AgentCAD`、`2026-10-28` | 被当成当前测试证据会误导 |
| `docs/benchmarks.md:55` 写上游 PR “(pending review)” | 上游 `U-C4N/Autocad-MCP#15` 实际已关闭且未合并（第一轮审计核实） | 对外陈述失实 |
| README 与 `pyproject.toml` 声称 MIT，但**仓库没有 LICENSE 文件** | `git ls-files | grep -i license` 为空 | 法律上默认保留所有权利；再分发/商用前必须补 |
| 安装器从 `archive/refs/heads/main.zip` 拉取，未固定版本；macOS 侧 `uvx --refresh` 每次刷新 | `installer/AgentCAD-Setup.cmd:7`、`installer/AgentCAD-Setup.command:19` | 安装结果随 main 漂移，不可复现 |
| 本机开发用 Python 3.13，CI 矩阵是 3.10/3.12 | `.github/workflows/verify.yml` | 本地新语法可能过不了 CI |

### 7.5 结构性风险（改代码时容易踩）

1. **MCP 工具注册双写**：34 个 `Runtime` 工具中，25 个在 `mcp_server.py` 有显式包装（可定制 agent 可见的参数描述/返回整形），9 个走 `mcp_server.py:432` 的自动注册。改 `Runtime` 方法签名时，显式包装不会自动报错——要靠测试或人工核对。
2. **ezdxf 没有被隔离**：30 个模块直接 `import ezdxf`。升级 ezdxf 时没有单一适配层可改，只能全仓核对；反过来也说明 DXF 实体就是内部数据模型，任何“加一层 DTO”的提案都要先证明值得。
3. **5 组循环依赖靠函数内导入维持**（见 1.4）。新增**顶层**回边会制造加载期循环；新增函数内 import 时建议注释原因。
4. **in-place 修订受白名单限制**：`engine.update_in_place`（`engine.py:165`）只覆盖 `COPY_ATTRS` 的 9 类实体（LINE/CIRCLE/ARC/POINT/TEXT/MTEXT/DIMENSION/INSERT/ELLIPSE）+ LWPOLYLINE/SPLINE。新操作若产出别的实体类型，`revise_drawing` 会抛 `UNSUPPORTED_ENTITY`——这是 spec 驱动修订的隐性边界。
5. **`service.py` 的熵增趋势**：1,590 行、34 个工具、35 条出边。新能力最省事的写法是继续往里加；超过一定规模后，拆分的成本会高于收益，但现在已经是全仓最该警惕的“上帝模块”。
6. **两套工具描述需要同步**：`Runtime` 的 docstring 会通过自动注册直接成为 agent 看到的 MCP 工具描述；显式包装则有自己的一份。同一能力两处文案，容易一边改了另一边没改。
7. **`agentcad` 包名与 PyPI 上的无关项目重名**：`pyproject.toml` 的发行名是 `agentcad-runtime`，安装器与文档都提醒过；命令行入口仍是 `agentcad`，与 PyPI 上另一个包同名，容易误装。

### 7.6 已核实的高影响缺陷候选（按影响排序）

这一节是从各章“风险”条目里筛出的跨模块项，去重后按影响排序。第 1 条在本机复现过，其余是代码证据（子代理核查 + 人工抽查），修复前请按 file:line 复核。

| # | 影响 | 事实 | 证据 |
|---|---|---|---|
| 1 | **高** | **选择器过滤对象拼错键名不报错，等于选中整个布局**：`inspect_entities(filter={"layr":"HOLES"})` 返回 3/3 个实体（正确写法应为 2/2），`delete` 的 `target` 用同样拼写会把图删空 | `coerce` 只校验是 dict（`ops/registry.py:324-326`）；`select` 只拒绝空 dict（`model.py:335-337`）；`_match` 只读 `type/layer/tag/tag_prefix`（`model.py:302-317`）。**本机已复现：3 个实体全删** |
| 2 | **高** | **每次 `revise_drawing` 都把旧 `part_spec` 作为孤儿 XRECORD 留在文件里**：连续修订 8 次，XRECORD 从 3 涨到 19，文件 51.9 KB → 56.9 KB，旧宽度值仍能在文件字节里搜到；模型/交付门禁都看不到（它们只读被引用的元数据） | `metadata.py:19` 的 `rootdict.add_new_dict(DICT)` 默认 `hard_owned=False`，于是 `metadata.py:21` 的 `d.remove(key)` 只删字典项、不删 OBJECTS 里的实体（ezdxf 1.4.5 `Dictionary.remove` 只在 `is_hard_owner` 时销毁实体）。建议改 `hard_owned=True` 或删除前显式 `doc.objects.delete_entity(old)`。**本机已复现** |
| 3 | 中 | 整体视口保护只覆盖 `delete` 与 `change_space`；`move/rotate/scale/mirror` 走的 `transform` 未保护，与 architecture.md“拒绝删除或移走整体视口”不一致 | `edit.py:125`（transform）对比 `edit.py:333`、`ops/sheet.py:62`；测试只覆盖后两者（`tests/test_layouts_and_scripts.py:372`） |
| 4 | 中 | 尺寸测量有两份实现且已出现算法分歧（角度分支不同），改一处修 bug 另一处保持旧行为 | `model.py:260-297`（atan2 + 360 回绕）vs `validate/features.py:74-104`（`G.angle_of` + `G.ccw_span`） |
| 5 | 中 | 验证器没有兜底异常处理：畸形外来图（缺样式表/异常 XDATA）会让 `validate_drawing` 抛非结构化错误，**已收集的 issues 全部丢失** | `validate/__init__.py:69` 无 try；`checks.py:484-490` 只转 `AgentCADError` |
| 6 | 中 | 实体预算只数模型空间，图纸空间可把单图实体数推到远超 `max_entities`，唯一兜底是 256 MB 文件上限 | `engine.py:96-98` 用 `len(doc.modelspace())`；`run_script` 路径却数全部布局（`service.py:590-593`） |
| 7 | 中 | `dry_run` 与实体级 `preconditions` 会对全图每个实体序列化求指纹（O(全图)），大图上一次预览就是一次全量遍历 | `guards.py:38-40`；`tests/test_t_workflow_guards.py:66` 专门为 strict 路径规避全量指纹 |
| 8 | 中 | 策略环境变量解析在错误信封之外：`AGENTCAD_MAX_ENTITIES=abc` 会让 `Policy()` 构造直接 Traceback，拿不到结构化错误 | `policy.py:18-19、27` 的 `int()/float()`；`Runtime` 在 `cli.py:307-308` 构造 |
| 9 | 低 | 检查点 id 用 `len(checkpoints)+1`，`_abandon` 会 pop 最后一项，之后同一 id 可能指向另一份字节 | `store.py:356-357`、`store.py:486`；journal 以 id 记录 `checkpoint_before` |
| 10 | 低 | 同进程两个句柄冲突时，错误文案说“另一个进程”，且跨图嵌套编辑会被自己的锁拒绝 | `locking.py:31-32` + `service.py:203-207`；`tests/test_z_locking.py:49-54` |
| 11 | 低 | `Runtime.metrics` 无上限增长（长驻 MCP 进程内存随调用数线性增长），`get_metrics` 只回放最近 200 条 | `service.py:102`、`service.py:1394` |

### 7.7 测试体系的薄弱处

（见第 8 章的测试地图；此处只列结构性结论）

- 45 个 `test_*.py`、约 10,400 行测试，覆盖以行为/场景为主（happy path、错误路径、崩溃恢复、接口一致性），值得信任；但**没有 CI 性能门禁**（`benchmarks/` 是本地工具，非 CI gate），性能回归只能靠人跑。
- AutoCAD / FreeCAD 相关用例在无后端机器上是 skip，本地“全绿”不等于后端路径已验证。
- 领域模块（engineering/*）与安装器（install.py 919 行）的测试密度明显低于核心事务层，是风险与改动成本的双高区。

## 8. 测试地图

**规模**：46 个文件 / 10,500 行（45 个 `test_*.py` + `conftest.py`；早前统计写 48，实测 45），其中本区逐行核对的 21 个共 4,892 行；543 个 `def test_`，本机 `--collect-only` 收集 914 个用例

**职责与边界**

`tests/` 是 AgentCAD 唯一的行为契约：它只通过 `service.Runtime` 的公共方法（以及少量 `store.DrawingStore`、`policy.Policy`）断言统一信封、磁盘文件、journal/checkpoint 与验证器结论，从不断言内部实现结构。例外是三处刻意的静态扫描：`test_g_interfaces.py:208-221` 检查适配层不导入 `ezdxf`/`ops`/`engine`，`test_security.py:72-164` 检查全包没有 `os.system`/`eval(`/多余 `subprocess`，`test_security.py:88-96` 检查 `install` 的导入面。

它不做性能门禁：延迟与载荷由 `benchmarks/` 承担（`test_t_quick_benchmark.py:1-6` 明确只测 harness 的统计与质量判定逻辑），以及 CI 的独立 benchmark 步骤（`.github/workflows/verify.yml:80-88`）。

它调用的对象分三层：`conftest.py` 提供的 `rt`/`plate`/`blank`/`ok`/`reread`/`by_tag`（`tests/conftest.py:36-69`）；被测代码本身（`Runtime` → `spec`/`engine`/`ops`/`store`/`validate`/`plot`/`viewer`）；以及仓库里**不属于 src 包**的评测脚本——`examples/autocad_benchmark/run.py`、`examples/trials/pro_comparison/run.py`、`benchmarks/`，它们由 `importlib.util.spec_from_file_location` 按路径加载（`test_s_upstream_benchmark.py:15-18`、`test_n_pro_comparison.py:8-12`）或靠 `sys.path.insert` 找到（`test_t_quick_benchmark.py:12`）。

因此测试套件只能在仓库 checkout 里跑，不能对着 pip 安装的包跑（`test_a_spec_to_drawing.py:10` 直接 `from conftest import ...`）。

谁调用它：CI 的 `merge gate`（`verify.yml:77-79`，Linux 3.10/3.12 + Windows 3.11 + 需要时的 macOS，`verify.yml:37-53`），以及开发者按改动选的子集（`AGENTS.md:33-34`，全量本地约 13 分钟）。

**文件清单**

| 文件 | 行数 | 角色 | 职责 |
|---|---:|---|---|
| `conftest.py` | 101 | 支撑 | 五个 fixture（`ws`/`rt`/`plate`/`blank` + autouse 的 `_freecad_memo`）、`ok()` 信封断言、`reread()` 独立读回、`by_tag()` 单标签断言、手写 PDF 生成器 `write_pdf()` |
| `test_a_spec_to_drawing.py` | 81 | 核心 | 规格 → 图纸的主干：100×60 板 + 4×Ø8 角孔，全部从重读文件的原始几何反推（`:18-42`）、验证器报告（`:45-53`）、R2000/R2018/R12/SVG 导出与句柄级一致性（`:56-71`）、无单位被拒（`:74-81`） |
| `test_b_revision.py` | 87 | 核心 | 修订语义：宽度 100→120 后**同句柄**、只有 10 个受影响对象变化（`:26-35`）、`get_diff` 字段级前后值（`:43-46`）、幂等与 `rollback cp-0001`（`:57-65`）、矛盾修订不落盘（`:68-72`）、外来图纸走 `stretch`+`requirements` 路线（`:75-87`） |
| `test_c_document.py` | 117 | 核心 | 文档 → 规格：PDF 抽取的 `traceability`（页/节/原文，`:26-35`）、逐项 `verification` 带 `document_source`（`:38-51`）、中文 Markdown（`:54-66`）、冲突/缺失/引用幻觉三种拒绝（`:68-108`）、文档修订复用已有图纸（`:111-117`） |
| `test_d_errors.py` | 112 | 核心 | 错误契约：`category` 五分类互不混淆（`:90-99`）、`path` 定位到特征（`:17-18`）、`op_index`/`partial_changes`/`file_unchanged`（`:87`、`:102-112`）、未识别需求字段必须变成 `warnings` 而非静默忽略（`:55-64`） |
| `test_e_tamper.py` | 111 | 核心 | 验证器判磁盘而非任务：用**裸 ezdxf** 在运行时背后改图（`:12-15`），移动孔/缩放轮廓/删尺寸/改文字/改直径五种篡改各自命中（`:27-72`），`modified_outside_runtime`（`:75-78`），两个纯几何回归（轮廓连接顺序 `:81-92`、500 条断链的 `dist` 调用上限 `:95-111`） |
| `test_f_repro_recovery.py` | 268 | 核心 | 复现与恢复：同 plan 两次几何与**句柄**完全相同（`:23-32`）、`if_tag_exists` 幂等与 `TAG_EXISTS`/`FILE_EXISTS`（`:35-48`）、journal 可追溯 + `cp-0002` diff（`:51-64`）、受限尾部读只解析 2 条记录（`:67-91`）、legacy 名碰撞分家与迁移四种（`:94-229`）、批中失败 + 回滚（`:232-244`）、崩溃恢复保留 `committed` 与 `failed/*-corrupt.dxf`（`:247-268`） |
| `test_g_interfaces.py` | 243 | 适配 | 协议层：真实 `subprocess` 跑 CLI（`:22-57`，含退出码 0/1/2/4）、官方 MCP SDK stdio 客户端拉起真实 server（`:60-111`，`tool_calls == 9`）、生成的 MCP 配置从任意 cwd 起服务（`:126-179`）、HTTP `/v1/<tool>`（`:182-205`）、三适配层不导入 CAD 逻辑（`:208-221`）、目录默认压缩到 20K 字符内（`:224-243`） |
| `test_h_operations.py` | 435 | 核心 | 一 op 一测：绘制（`:27-138`）、注释（`:113-156`）、编辑（`:161-342`）、块与属性（`:392-410`）、引用 `$N`（`:420-426`）、单位换算（`:429-435`）；每测都做「成功 → 从磁盘重读几何 → 一个可诊断失败码」 |
| `test_i_common_ops.py` | 251 | 核心 | 第二批 AutoCAD 常用 op：HATCH（含岛与绘制顺序 `:31-63`）、POLYGON/DONUT/XLINE/RAY（`:66-107`）、BREAK/JOIN（`:112-156`）、DRAWORDER/PURGE（`:159-191`）、坐标标注/中心线/文字样式（`:196-241`）、新 op 在块内可用（`:244-251`） |
| `test_j_compact_results.py` | 77 | 支撑 | 结果体积契约：`validate_drawing` 默认只给失败项 + `requirements_passed` + `recognized` 摘要，且 `< full/3`（`:17-31`）、失败项始终完整（`:34-40`）、`get_diff` 与 `history` 的简/详两级（`:54-77`） |
| `test_k_pdf.py` | 190 | 核心 | 出图：标准比例与幅面选择（`:18-34`）、图纸空间 1:1 一页一布局（`:37-50`）、单色与过小字提示（`:53-60`）、CJK 字体替换不改源文件（`:63-73`）、沙箱/覆盖/扩展名（`:76-80`）、PNG 像素级校验（`:83-106`）、字形缺失 `TEXT_PLOTS_AS_BOXES` 与 `^` 转义（`:121-190`） |
| `test_l_fewer_turns.py` | 112 | 支撑 | 减少轮次：`apply_operations(create=...)` 建图并返回同一份验证（`:17-44`）、`layout` 校验（`:47-54`）、`apply_batch` 一调用画一族（`:57-66`，整体 `< 6000` 字符）、单 job 隔离与 `stop_on_error` 跳过（`:69-79`）、执行前整表校验（`:82-91`）、只读/strict（`:94-102`）、MCP 注册与提示词（`:105-112`） |
| `test_m_library.py` | 305 | 核心 | 行业库：P&ID 在线符号切管（`:24-103`）、GD&T 框格规则与注释比例（`:107-150`）、ISO 孔/紧固件/轴承的真实几何与 BOM（`:154-203`）、`standard_library` 查询与目录体积（`:214-230`）、多实体 op 的重放/替换限制（`:234-258`）、管件在端点与拐角处切割（`:261-305`） |
| `test_n_pro_comparison.py` | 217 | 工具 | 对照实验评测器：事件统计与 usage（`:15-33`）、两臂任务文本一致（`:36-40`）、不覆盖历史结果（`:43-50`）、按图纸而非自我声明打分（`:52-68`）、中断分类的证据门槛（`:92-103`）、配对重试去重（`:106-217`） |
| `test_o_viewer.py` | 1043 | 核心 | 实时查看器：时间线/版本 = sha256（`:65-98`）、旧版本渲染与 diff（`:101-119`）、外部改动标记（`:132-141`）、增量 feed 与 SSE（`:146-226`）、HTTP 全部路由与越权码（`:173-205`）、**从不写工作区**（`:229-237`）、`viewer_url` 只信任本 root 的 loopback（`:411-433`）、Host 头防 DNS rebinding（`:391-398`）、远端绑定需显式允许（`:401-408`）、读并发与 Windows 重命名竞态（`:284-374`）、FreeCAD 缩略图与安装位置记忆（`:768-1043`） |
| `test_p_busy_files.py` | 350 | 核心 | 文件被占用时的提交状态机：`FILE_BUSY` 且可重试/文件未变（`:56-78`）、已落盘未记录 → `applied_not_recorded` + `recover` 补记（`:81-113`）、checkpoint 与 head 永远同步（`:266-296`）、B1/B2/N1/N2 四个悬挂标记场景（`:272-349`） |
| `test_q_install.py` | 399 | 适配 | 安装：默认工作区 `~/AgentCAD`（`:63-70`）、`AGENTCAD_ROOT` > 参数的优先级（`:73-78`）、四客户端幂等写入（`:93-110`）、Claude CLI 与 `cmd.exe` 元字符回退（`:161-208`）、Codex 四种 TOML 写法与 BOM/坏 TOML（`:249-289`）、`doctor`（`:226-246`） |
| `test_s_upstream_benchmark.py` | 128 | 工具 | 评测器的对抗检查：不完整报告被 `scripts/check_benchmark.py` 拒（`:21-39`）、roundtrip 必须覆盖每条测量且用新读（`:42-72`）、服务端声明不能替代磁盘证据（`:75-111`）、参考产物不被后续任务覆盖（`:114-128`） |
| `test_security.py` | 164 | 支撑 | 执行安全：路径穿越/绝对路径/保留目录/扩展名（`:11-17`）、覆盖保护会先建检查点（`:20-26`）、只读与 `dry_run` 例外（`:29-37`）、`allowed_ops` 白名单（`:40-47`）、预算（`:50-59`）、写锁 `DRAWING_LOCKED` 可重试（`:62-69`）、`install` 导入面与全包执行面扫描（`:72-164`） |
| `test_t_quick_benchmark.py` | 101 | 工具 | 本地基准的**逻辑**而非数字：分位数插值（`:25-29`）、计时不含序列化且失败不成样本（`:32-40`）、噪声地板与回归判定（`:43-50`）、机器指纹门控（`:60-68`）、workload 声明与 quick 子集（`:71-81`）、6 项质量检查在空工作区全过（`:84-91`）、quick 端到端（`:94-101`） |

其余 25 个文件同属本区，按族归并（未逐行细读，只核对 docstring 与用例名；括号内为 `def`/收集数）：

- **尺寸与公差**：`test_dimension_display.py`(18/68)、`test_dimension_appearance.py`、`test_y_tolerances.py`(20/58)、`test_mechanical_annotations.py`(16/73)、`test_w_image_dimensions.py`。
- **工程域**：`test_u_engineering.py`、`test_layouts_and_scripts.py`(21/54)、`test_constraints.py`、`test_drafting_checks.py`。
- **原生后端**：`test_autocad_live.py`(6，需 `accoreconsole`)、`test_native_delivery.py`(7/12，需 `accoreconsole`)、`test_autocad_findings.py`、`test_autocad_golden.py`（对 `tests/golden/autocad_ops.json`）、`test_l_live_session.py`(21/34，COM 替身)。
- **查看器与会话**：`test_q_viewer_progress.py`(21)、`test_q_sessions.py`(10)、`test_q_setup_gui.py`(28)。
- **图像与交付**：`test_t_image_inspection.py`(16/70)、`test_w_delivery.py`(24)、`test_source_board.py`、`test_standard_sources.py`。
- **协议与守卫**：`test_x_http_security.py`(24)、`test_z_locking.py`、`test_t_workflow_guards.py`、`test_trial_harness.py`。

**命名分层的实际情况**：字母**只覆盖一部分文件，且已漂移**。`a`–`k` 仍对应架构文档的 A–K 场景（出图、修订、文档、错误、篡改、复现、接口、操作、常用 op、紧凑结果、出图 PDF），可以按字母选阶段；但 `l` 用了两次且各自在 docstring 里自称 "Test L"（`test_l_fewer_turns.py:1` 是"更少轮次"，`test_l_live_session.py:1` 是"与运行中的 AutoCAD 协作"），`q` 用了四次（`test_q_install.py:1` 安装、`test_q_sessions.py:1` 连接者、`test_q_setup_gui.py:1` 向导、`test_q_viewer_progress.py:1` 进度，后者连 "Test Q" 前缀都没有），`t` 三次、`w` 两次，`r` 与 `v` 缺失。所以字母是历史编号而不是稳定分类，**不能**假设某一字母唯一。

**fixtures 与 golden**：`tests/fixtures/` 只有三份文档输入——`plate_spec_zh.md`（中文规格）、`plate_spec_conflict.txt`（第 5、7 行冲突，`test_c_document.py:73` 断言这两个行号）、`plate_spec_missing.txt`（缺单位与孔径）；PDF 不入库，由 `conftest.write_pdf`（`conftest.py:72-101`）现场手工拼一个合法 PDF。`tests/golden/autocad_ops.json` 是 AutoCAD 2027 `accoreconsole` 录下的逐 op 实体串（AutoCAD 侧基准，`test_autocad_golden.py` 是唯一的差分消费者），不含 Autodesk 样例图纸。

**关键入口**（本层的"API"是 fixture 与跳过开关）

- `rt`（`conftest.py:36-38`）— `Runtime(Policy(root=tmp_path))`；所有业务断言都从这里进，`read_only`/`allowed_ops`/预算等变体在各文件里自行构造（`test_security.py:41`、`:51`）。
- `_freecad_memo`（`conftest.py:23-28`）— autouse，把 `backends.freecad.memo_path` 指进临时目录，保证测试不碰用户真实的"记住的 FreeCAD 路径"。
- `plate` / `blank`（`conftest.py:41-52`）— 规格出图与空图两个基线；`PLATE_SPEC` 是共享常量（`:17-20`）。
- `ok()`（`conftest.py:55-57`）— 断言 `r["ok"]` 并在失败时打印 `error`；它让每个失败用例都打印完整 `error`，而不是只报一个断言位置。
- `reread()`（`conftest.py:60-62`）— 全新 `ezdxf.readfile`，是"独立读回"这一主张的唯一实现点：几乎所有几何断言都走它而不是 `rt.read_drawing`。
- `by_tag()`（`conftest.py:65-69`）— 顺带断言标签唯一，标签重复会在这里先炸。
- `cli()`（`test_g_interfaces.py:22-25`）— `python -m agentcad.cli --root <ws> --compact`，`timeout=120`，把 stdout 当 JSON 解析；退出码语义（0/1/2/4）在这一层被钉住。
- `live_rt()`（`test_l_live_session.py:108-112`）— 用 `rt.live_connect = lambda ...: app` 换掉 `service.py:279-280` 的 COM 连接点，`acad_live` 本身仍被真实调用两次（`service.py:283-291`）。
- `applied_not_recorded()`（`test_p_busy_files.py:245-263`）— 先让 `replace_file` 真把图纸换掉，再抓住 `head.json`；这是"已落盘未记录"唯一可复现的构造。
- **跳过开关**：`pytestmark = pytest.mark.skipif(os.name != "nt", ...)`（`test_p_busy_files.py:16`）、`skipif(find_accoreconsole() is None)`（`test_autocad_live.py:10`、`test_native_delivery.py:8`）、`skipif(importlib.util.find_spec("pythoncom") is None)`（`test_l_live_session.py:20`）、FreeCAD 用 `available()`（`test_o_viewer.py:768`）、字体用 `pytest.skip`（`test_k_pdf.py:66,123,153,167,183`）、`importorskip("tomllib")`（`test_g_interfaces.py:143`）。没有自定义 marker，全部靠文件级 `skipif`（`pyproject.toml:44-46` 只配了 `testpaths` 与 `-q`）。
- **如何跑子集**：`--basetemp` 是硬要求——本机默认临时目录被策略挡住（`AGENTS.md:27-29`；`docs/audit/existing-projects.md:58` 记录过 564 个 error 的先例），且路径要短（260 字符上限）。单阶段：`.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=D:\tmp\acad tests/test_a_spec_to_drawing.py tests/test_b_revision.py tests/test_c_document.py`；全量 45 个文件约 13 分钟（`AGENTS.md:34`）；CI 用 `uv run --locked python -m pytest -p no:cacheprovider --basetemp=<runner.temp> --junitxml=...`（`verify.yml:79`）。测试是**互不共享状态**的（每个用例一个 `tmp_path`），所以可以按文件自由并行；但 `test_security.py:163` 读 `Path.cwd()`、`test_q_install.py:36` 会 `chdir`，同进程内不要与并行插件混跑。

**数据结构与不变量**

- 统一信封：`ok` 为真时结果可被 json 序列化并用 `size()` 量体积（`test_j_compact_results.py:13-14`）；为假时必须有 `error.code`/`category`，批次类错误还要 `op_index` 与 `partial_changes`（`test_d_errors.py:87`、`test_h_operations.py:415-417`）。
- 失败批次的文件必须字节不变，且 journal 记一条 `ok=false` 带 `file_unchanged`（`test_d_errors.py:102-112`）；`test_f_repro_recovery.py:236-244` 对同一条不变量做二次确认。
- 句柄稳定：修订只改受影响实体、其余（含用户手加的注记）字节级不变（`test_b_revision.py:26-35`）；同一 plan 两次产生**完全相同句柄**（`test_f_repro_recovery.py:31-32`）——这是"确定性"最强的断言。
- 检查点身份：`cp-NNNN` 单调、`checkpoint_before` 指向批次前状态、`committed` 是最后一次成功提交（`test_f_repro_recovery.py:61`、`:263`）；检查点内容被换过必须 `CHECKPOINT_CHANGED` 而不是照回滚（`:104-112`）。
- journal 的 `seq` 单调且 `read_journal(n)` 只解析 n 条（用 `store.json.loads` 计数验证，`:88-91`），超长 UTF-8 记录从尾部读也要正确（`:78`）。
- 版本即内容：`version == sha256_file(workspace/drawing)`（`test_o_viewer.py:77`），viewer 只读、绝不写工作区（`:229-237`）。
- 安全不变量：`PATH_OUTSIDE_WORKSPACE`/`PATH_RESERVED`/`BAD_EXTENSION`/`FILE_EXISTS`/`READ_ONLY`/`DRAWING_LOCKED`(retryable)/`BUDGET_EXCEEDED`（`test_security.py:11-69`），以及"没有 shell、没有 `eval`、`exec` 只出现在两个 runner 的 `main`"（`test_security.py:116-164`）。
- 每个 op 至少一个可诊断失败码，且错别字要给出建议（`test_h_operations.py:413-417`）。

**主要调用链**

1. 规格 → 图纸：`rt.draw_from_spec(PLATE_SPEC, "plate.dxf")`（`conftest.py:43`）→ `service` 生成 → `reread()` 用独立 ezdxf 读回 → 用原始几何反算尺寸与孔距（`test_a_spec_to_drawing.py:23-36`）→ `validate_drawing(detail=True)` 逐条需求核对（`:45-53`）。
2. 篡改 → 判定：`ezdxf.readfile` → 改实体 → `saveas` 覆盖（`test_e_tamper.py:12-15`）→ `rt.validate_drawing` 必须失败且给 `deviation`/`DIMENSION_NOT_ATTACHED`（`:30-49`）。这条链路证明验证器不信任内存中的 document。
3. 接口：`subprocess` → `agentcad.cli`（`test_g_interfaces.py:22-25`）；`stdio_client` → `agentcad.mcp_server`（`:60-93`）；`ThreadingHTTPServer` + `urllib` → `/v1/<tool>`（`:182-205`）。三条链都对同一个 `Runtime` 断言同样的信封。
4. 崩溃：`DrawingStore._write_head({"pending": ...})` → 截断图纸到一半（`test_f_repro_recovery.py:253-259`）→ `rt.recover` 恢复 `committed` 并把损坏件留在 `failed/`（`:261-268`）。
5. 占用：monkeypatch `store_mod.replace_file`，在替换图纸之后立刻 `open(head_path)`（`test_p_busy_files.py:88-93`）→ 断言 `applied_not_recorded` → `recover` 补记 → 下一次提交不再重复（`:102-113`）。
6. 查看器：`Viewer(Policy(root=ws), port=0, interval=0.05, cache_dir=...)`（`test_o_viewer.py:39`）→ 真 HTTP 请求 `/api/timeline`、`/api/svg`、`/api/events` → 结束后比对工作区文件哈希（`:229-237`）。

**扩展点**

- 新增 op：在 `test_h_operations.py`（基础目录）或 `test_i_common_ops.py`（第二批 AutoCAD 语义）加一节，形状固定为"成功 → `reread` 几何 → 一个错误码"；若 op 产出多实体，另在 `test_m_library.py:234-248` 补 `if_tag_exists` 重放。
- 新增错误码：`test_d_errors.py`（分类与 `path`）与 `test_security.py`（策略面）两处同时补，否则"五分类互相可区分"（`test_d_errors.py:90-99`）会被悄悄放宽。
- 新增接口或字段：改 `test_g_interfaces.py`，并让 `test_interfaces_share_one_implementation`（`:208-221`）继续通过——它把"适配层不导入 `ezdxf`/`ops`/`engine`"变成可执行约束。
- 新增平台相关行为：用文件级 `pytestmark`（`test_p_busy_files.py:16`）或单测装饰器（`test_o_viewer.py:280`、`test_l_live_session.py:139`）；新增可选后端用 `available()`/`find_accoreconsole()` 决定跳过（`test_o_viewer.py:768`、`test_autocad_live.py:10`）。
- 新增 CLI/安装路径：`test_q_install.py` 的 `home` fixture（`:24-38`）是唯一被认可的写法——伪造 `HOME/APPDATA/LOCALAPPDATA/XDG` 并把 `claude/codex/cursor` 从 PATH 上摘掉；`test_q_setup_gui.py` 复用同一套。
- 新增评测逻辑：`test_t_quick_benchmark.py`（`benchmarks` 的统计与质量）、`test_s_upstream_benchmark.py` / `test_n_pro_comparison.py`（`examples/` 下的脚本，按路径加载）。
- 新增共享 fixture/输入：`tests/conftest.py` 或 `tests/fixtures/`（现有三份）。

**风险与可疑之处**

- [高] 整个 `test_p_busy_files.py`（350 行 / 14 个 `def` / 15 个收集用例）在非 Windows 上被文件级跳过（`:16`），而它守的是 `store.py` 最难的"已落盘未记录"状态机；Linux CI（`verify.yml:79`）对此零覆盖。影响：`settle_pending`/`recover` 的回归只能由 Windows job 或人工发现；建议把状态判定抽成不依赖 OS 的用例（让 `replace_file` 抛 `PermissionError`），真文件句柄只留 2 个 Windows 用例。
- [高] 原生后端的交叉验证在 CI 里**从不执行**：`test_autocad_live.py:10` 与 `test_native_delivery.py:8` 在没有 `accoreconsole.exe` 的机器上整体 skip（本机实测为 `None`，共 18 个用例），CI 步骤名就写着 "optional native backends report skips"（`verify.yml:78`）。影响：DWG 往返与 AutoCAD AUDIT 的结论只来自有 AutoCAD 的开发机；建议把 golden 差分（`test_autocad_golden.py`，不需要 AutoCAD）扩到 DWG 读回的最小集。
- [中] 私有 API 直连：`rt._drawing(...)`（`test_f_repro_recovery.py:69,108,121,166`；`test_p_busy_files.py:26`；`test_o_viewer.py:86`）、`store._write_head`（`test_f_repro_recovery.py:257`）、`state.journal()`/`checkpoint_path`（`:72`、`:110`）。影响：重命名这些内部符号会同时弄红三个文件，且失败信息与行为无关；建议在 `DrawingStore` 上补一个 `@internal` 的测试用访问器，或让测试改用 `Policy` 暴露的路径。
- [中] 体积阈值断言散落六处：`< full/3`（`test_j_compact_results.py:22`）、`< full/2`（`:77`）、`< 6000`（`test_l_fewer_turns.py:66`）、`< 20000`（`test_g_interfaces.py:233`）、`< 16000`/`< 4000`（`test_m_library.py:229`、`:218`）。影响：任何新增一个字段都可能变红，而错误只说大小不说是谁涨的；建议断言"包含/不包含哪些键"而非字节数，或把上限集中成常量并在失败时打印差额最大的字段。
- [中] 以源码文本为断言对象：`test_security.py:128-164` 断言 `"os.system" not in text`、`text.count("exec(") == ...`、`"subprocess" not in text`，`test_g_interfaces.py:214-221` 解析 AST 取 import 名。影响：注释里出现 `exec(`、或把 `subprocess.run` 换成 `subprocess.Popen` 别名，都会误报/漏报；这是实现级而不是行为级门禁，改动这类守卫时要同步改测试的匹配面。
- [中] 进程级全局状态与并行不安全：`conftest.py:15` 在 **import 时**写 `os.environ["AGENTCAD_VIEWER"]="0"`（`test_q_sessions.py:92` 又手动置一次，说明契约并不牢），`test_q_install.py:36` 用 `monkeypatch.chdir`，`test_security.py:163` 读 `Path.cwd()`。影响：`-n auto` 或 `-p xdist` 下这些用例会互相干扰；建议把 cwd 依赖改成参数注入，把 viewer 开关改成 fixture 级。
- [中] 评测脚本与测试同仓耦合：`test_s_upstream_benchmark.py:15-18`、`test_n_pro_comparison.py:8-12` 按路径加载 `examples/**/run.py`，`test_s` 还 `subprocess` 调 `scripts/check_benchmark.py`（`:37`），`test_t_quick_benchmark.py:12` 往 `sys.path` 插仓库根。影响：这些不是 src 包的一部分，`tests/` 因此只能在 checkout 内运行；把 `examples/` 整体移走会让 4 个文件的收集直接报错。
- [低] 命名漂移：`l`、`q`、`t`、`w` 重复（`test_l_fewer_turns.py:1` 与 `test_l_live_session.py:1` 都自称 "Test L"），`r`/`v` 缺失，25 个文件没有字母前缀。影响：按字母选阶段时会漏（例如"Test L"实际是两个不相干的领域）；建议在新文件里用领域名而非下一个字母。

**测试对应**

| 测试 | 覆盖的 src 模块 / 行为 |
|---|---|
| `test_a` / `test_b` / `test_c` | `service.py`（`draw_from_spec`/`revise_drawing`/`revise_from_document`/`export_drawing`）、`spec.py`、`docspec.py`（间接）、`validate/*`、`model.py` |
| `test_d` / `test_e` | `errors.py` 分类表、`service.validate_drawing`、`validate/checks.py` + `validate/features.py`（`measure_dimension`/`extract`/contours） |
| `test_f` / `test_p` | `store.py`（journal、checkpoint、head、`replace_file`/`REPLACE_TIMEOUT`、legacy 迁移）、`service.recover`/`rollback`/`history`/`get_diff` |
| `test_g` / `test_x_http_security` | `cli.py`、`mcp_server.py`、`http_api.py`、`mcp_config.py`、`ops/registry.py` 的目录与 schema；信封与退出码在三个入口的一致性 |
| `test_h` / `test_i` / `test_m` | `ops/draw.py`、`ops/edit.py`、`ops/annotate.py`、`ops/structure.py`、`ops/analysis.py`、`ops/library.py`、`ops/registry.py`（参数校验与 `REGISTRY`）、`units.py`、`geom.py`（间接） |
| `test_j` / `test_l_fewer_turns` | `service` 的紧凑报告（`requirements_passed`/`recognized`/`detail_hint`）、`apply_batch`、MCP `INSTRUCTIONS` |
| `test_k` | `plot.py`（`plot_pdf`/`plot_png`/`font_cmap`/`CJK_FONTS`）、`drawing_frontend.py`（间接）、`validate/checks.py:217` 的字形判定 |
| `test_n` / `test_s` / `test_t_quick` | **非 src**：`examples/trials/pro_comparison/run.py`、`examples/autocad_benchmark/run.py`、`scripts/check_benchmark.py`、`benchmarks/{harness,quality,workloads,run}.py` |
| `test_o` / `test_q_viewer_progress` / `test_q_sessions` | `viewer.py`、`viewer_link.py`（经 `viewer.py:67` 再导出）、`progress.py`、`sessions.py`、`viewer_static/`、`backends/_fc_view.py`（间接） |
| `test_q_install` / `test_q_setup_gui` | `install.py`（`CLIENTS`/`config_path`/`setup`/`doctor`/`cmd_unsafe`）、`setup_gui.py`、`desktop.py`、`demo.py`、`mcp_config.py` |
| `test_security` / `test_z_locking` | `policy.py`（root 沙箱、`read_only`、`allowed_ops`、预算、`allow_scripts`）、`locking.py`、全包静态执行面 |
| `test_l_live_session` / `test_autocad_live` / `test_autocad_findings` / `test_autocad_golden` | `backends/acad_live.py`（经 `service.py:279-291`）、`backends/autocad.py`、`tests/golden/autocad_ops.json` |
| `test_y_tolerances` / `test_dimension_display` / `test_dimension_appearance` / `test_mechanical_annotations` | `tolerances.py`、`fits.py`、`dimension_display.py`、`appearance.py`、`validate/drafting.py`、`engineering/mechanical_annotations.py` |
| `test_u_engineering` / `test_layouts_and_scripts` / `test_constraints` / `test_drafting_checks` | `engineering/{pid,mechanical,architecture,plant}.py`、`constraints.py`、`scripting.py` + `_script_runner.py` |
| `test_t_image_inspection` / `test_w_image_dimensions` / `test_w_delivery` | `images.py` + `_image_runner.py`、`image_dimensions.py`、`delivery.py`、`quality.py`、`metadata.py` |
| `test_native_delivery` / `test_autocad_live`（DWG 部分） | `backends/autocad.py` 的 DWG 转换与 AUDIT，`delivery.py` 的原生复核 |
| `test_t_workflow_guards` / `test_trial_harness` / `test_standard_sources` / `test_source_board` | `guards.py`、`discovery.py`、`stdlib.py`、`tabular.py`、`examples/trials` 的 harness、`examples/source_capability` 的 JSON/SVG |

覆盖率缺口（用 `rg` 统计"模块名是否在 `tests/` 里出现过一次"，再逐个看是否被间接覆盖）：

- **9 个 src 文件从未被点名**：`docspec.py`、`drawing_frontend.py`、`geom.py`、`image_dimensions.py`、`viewer_link.py`、`backends/acad_live.py`、`engineering/mechanical_annotations.py`、`ops/analysis.py`、`ops/structure.py`。
- 逐个查证后这 9 个都是**间接覆盖**：`docspec` 走 `rt.extract_requirements`（`test_c_document.py:27`）、`drawing_frontend` 走 `plot._render`（`test_k_pdf.py`）、`geom` 是几何断言的地基、`image_dimensions` 走 `images.inspect_image`（`test_t_image_inspection.py`）、`viewer_link.viewer_url` 经 `viewer.py:67` 再导出后被 `test_o_viewer.py:412` 直接调用、`acad_live` 经 `service.live_connect`（`test_l_live_session.py:108-112`）、`mechanical_annotations` 走 ops、`ops/analysis.py` 与 `ops/structure.py` 由 `ops/__init__.py:2` 注册后经 `apply_operations` 进入。所以"0 次点名"不等于未覆盖，但这些模块**没有模块级用例**，改内部结构只能靠端到端兜底。
- **只有 1 次提及的一批更值得关注**：`fits.py`、`tolerances.py`、`stdlib.py`、`tabular.py`、`validate/drafting.py`、`ops/registry.py`、`ops/annotate.py`、`engineering/plant.py`、`backends/_fc_view.py`、两个 `_runner.py`。它们构成"数值表 + 派发 + 子进程入口"这一层，只有端到端用例顺带经过。
- 其中 **`ops/registry.py` 是性价比最高的一处待补**：参数类型、默认值、`if_exists` 语义是整个 `REGISTRY`（本机 71 个 op）的公共面，而现有断言只覆盖 `UNKNOWN_OP`/`UNKNOWN_PARAM` 两个错误码（`test_h_operations.py:413-417`）。
- 两处只有骨架级覆盖、没有逐项用例：MCP 的工具 schema（`test_g_interfaces.py:101-102` 只断言 `apply_operations` 的 `operations`/`path`，`test_l_fewer_turns.py:105-112` 只断言 `apply_batch` 的 `jobs`）与 HTTP 的路由表（`test_g_interfaces.py:198-203` 只走 4 个工具）。新增工具时最容易漏的正是这两处注册。

## 9. 仓库、构建、CI 与文档管线

**规模**：表内 32 个文件 / 6,594 行（其中 uv.lock 1,901、生成物 docs/operations.md 1,111、benchmarks 四模块 1,063、verify.yml 224、两个安装器 154、四个 scripts 450）；关联体量另计：docs/ 递归 32 篇 Markdown 共 6,094 行（其中顶层 29 篇 5,306 行；表内已列 1,802 行）、examples/ 228 个 .py 24,109 行 + 308 MB 数据、research/ 6.5 MB、launch-video/ 11 个文件 101 KB（均不被 pyproject 或 CI 引用）。

**职责与边界**
这一层不执行业务逻辑，只回答三个问题：哪些文件构成一次可安装的交付物（`pyproject.toml:30-31`、两个安装器）、哪条命令算通过（`.github/workflows/verify.yml:22-224`、`scripts/check_benchmark.py:12-22`）、哪些文本必须与代码逐字节一致（`scripts/gen_docs.py:61-73`）。输入是 `src/agentcad`、`tests/` 与 `examples/autocad_benchmark/run.py`，输出是 wheel 内容、四个 CI 结论（plan / tests / merge-gate / main-health）、两类生成文档和两个双击安装器。它调用 `agentcad.ops.REGISTRY`、`agentcad.constraints`（gen_docs.py:12-13）与 `agentcad.install`/`setup_gui`（安装链），不被 Runtime 的事务路径导入，也不修改图纸。除 CI 之外还存在一条纯人工门禁（`AGENTS.md:31-46`：热路径改动要跑 `python -m benchmarks`），没有任何工具强制执行。

**文件清单**
| 文件 | 行数 | 角色 | 职责 |
|---|---|---|---|
| pyproject.toml | 46 | 核心 | 包元数据、2 个 console script、hatch wheel/sdist 目标、pytest 配置 |
| .github/workflows/verify.yml | 224 | 核心 | plan 矩阵、tests 四腿、merge gate、main health 状态机 |
| scripts/gen_docs.py | 77 | 工具 | 从 `REGISTRY` 生成 `docs/operations.md`（可选 `--matrix`），`--check` 做字节比对 |
| scripts/check_benchmark.py | 39 | 工具 | v8 发布门禁：31 条任务全 100 分且 summary 与 reference 相等 |
| scripts/new-worktree.ps1 | 118 | 工具 | 每 agent 一个 worktree：分支、独立 `.venv`、独立 `.mcp.json`；`-Remove` 前校验已合并 |
| scripts/code_map_stats.py | 216 | 工具 | 生成 `docs/code-map.md` 的结构统计：73 文件/19,508 行、7 个分组合计、262 条内部依赖边（179 模块级 + 83 函数内）、循环依赖、ezdxf 直接导入者；口径写在 docstring |
| AGENTS.md | 108 | 数据 | 协作规程：一任务一 worktree、合并四条件、macOS 例外、需先问用户的情形 |
| README.md | 156 | 数据 | 对外首页：安装入口、71 操作/35 工具、基准摘要、文档索引、测试快照声明 |
| benchmarks/README.md | 123 | 数据 | 开发基准用法、profile/分组表、机器指纹门控、6 项质量检查、加 workload 的写法 |
| benchmarks/harness.py | 336 | 核心 | 统计（median/p90）、机器指纹、结果 schema、baseline 比较与回归筛选 |
| benchmarks/run.py | 136 | 核心 | CLI（profile/only/repeat/save/baseline/gate/keep）与退出码约定 |
| benchmarks/workloads.py | 385 | 核心 | 10 个 workload 组（startup/drawing/ops/read/revision/discovery/export/scale/mcp/session） |
| benchmarks/quality.py | 206 | 核心 | 6 项确定性绘图检查，重读磁盘而非采信运行时自报 |
| benchmarks/baseline.json | 321 | 数据 | 维护者 Windows 机器的已记录基线（含 git commit/dirty 与 calibration_ms） |
| installer/AgentCAD-Setup.cmd | 127 | 入口 | Windows 双击：批处理引导 + `#PS-START#` 之后的 WinForms 进度窗 |
| installer/AgentCAD-Setup.command | 27 | 入口 | macOS/Linux 双击：sh，无窗口，只把 URL 打到终端 |
| web/viewer/package.json | 37 | 适配 | 查看器与安装页源码依赖（React 19、Vite 8、three、radix、tailwind 4） |
| web/viewer/vite.config.ts | 27 | 适配 | 双入口（index/setup）构建，`outDir` 直接写进 `src/agentcad/viewer_static/app` |
| web/viewer/README.md | 37 | 数据 | 声明「构建产物随包提交、安装不需要 Node」及重建时机 |
| .gitignore | 41 | 数据 | 排除 venv/状态目录/Autodesk 派生样本/`.mcp.json`/`node_modules` |
| .gitattributes | 19 | 数据 | 二进制标记、7 条 `export-ignore`、`.cmd` CRLF、`.command` LF、查看器产物 `-text` |
| .claude/launch.json | 50 | 适配 | 三个本地调试入口（viewer 8781、setup demo 8790、vite 5180） |
| benchmarks/__main__.py | 6 | 入口 | `python -m benchmarks` 的薄入口，转 `run.main()` |
| benchmarks/__init__.py | 4 | 支撑 | 包标记与 `python -m` 入口可见性，无逻辑 |
| uv.lock | 1,901 | 数据 | 54 包解析结果，带 hash 与 8 条 python/platform marker |
| docs/test-results.txt | 26 | 数据 | 2026-10-28 的一次 136 passed 运行记录，仍被 `docs/acceptance-report.md:4` 引用 |
| docs/install.md | 210 | 数据 | 安装路径的对外叙述：双击流程、`uvx --from` 语义、离线启动、排错 |
| docs/release-verification.md | 63 | 数据 | 可复现校验命令、后端契约、HTTP/锁边界 |
| docs/benchmarks.md | 216 | 数据 | agent 级试验与上游能力矩阵（开发基准的历史对照） |
| docs/architecture.md | 124 | 数据 | 分层/数据模型/信封/验证器的高层说明，本片段的下层读物 |
| docs/operations.md | 1,111 | 数据 | 由 `REGISTRY` 生成的 71 个 op 的参数表（CLI 的 `agentcad ops` 与 MCP 的 `list_operations` 的人类可读版本） |
| docs/op_test_matrix.md | 80 | 数据 | 由 `--matrix` 生成的 op × 测试文件表，**不受** `--check` 门禁 |

**关键入口**
- `agentcad.cli:main`（pyproject.toml:23，cli.py:398）— `agentcad` 唯一入口；`mcp-config`（cli.py:181）、`setup`（cli.py:153）、`serve`（cli.py:188）、`doctor`（cli.py:177）都挂在这里。
- `agentcad.mcp_server:main`（pyproject.toml:24，mcp_server.py:490）— `agentcad-mcp` 入口。`build_server`（mcp_server.py:114）装 26 个装饰器工具 + 9 个 `rt` 方法（mcp_server.py:432-434）＝ 35；`discovery` 档位另起一个只含 3 个工具的服务器（mcp_server.py:440-461），与 README.md:17 的「35 / 3」相符（实测 35）。
- `scripts/gen_docs.py --check`（gen_docs.py:61-73）— 契约是「重算的 Markdown 必须与磁盘逐字节相等」，不等即 `SystemExit` 非零；比较的名字集合来自 `files` 字典（gen_docs.py:64-66）。
- `scripts/check_benchmark.py <report.json>`（check_benchmark.py:25-35）— 契约是条数=31、`task_id` 集合相等、每项 `status=="pass" and score==100`、`summary` 与 `examples/autocad_benchmark/results/2026-10-30-v8/reference.json`（check_benchmark.py:9）逐键相等；异常收敛为 `BENCHMARK_GATE_FAILED` + 退出 1。
- `installer/AgentCAD-Setup.cmd:21` 的 `#PS-START#` 分隔 — 同一文件既是批处理又是 PowerShell 源；批处理按标记切开后 `iex`（第 15、19 行）。
- `install.setup(...)`（install.py:668-750）— 安装链唯一落点；`progress(step,state,info)` 的步骤名必须与 `setup_gui.Wizard.start` 的步骤数组（setup_gui.py:89）一致。
- `harness.compare_samples` / `env_matches`（harness.py:197-235）— 基准判定的唯一实现：先比机器指纹，再按 `DEFAULT_TOLERANCE=1.5` 与 `FLOOR_MS=2.0` 判回归（harness.py:27-29）。
- `python -m benchmarks`（benchmarks/__main__.py:1-6）— 从仓库根启动，`run.main` 解析 profile/only/repeat/save/baseline/gate/keep；wheel 只含 `src/agentcad`（pyproject.toml:30-31），所以装完包的用户没有这条命令。
- `merge gate` 这个 check 名（verify.yml:101）是外部契约：GitHub ruleset「main: merge gate」按名字要求它，改名等于拆掉服务端强制（AGENTS.md:89-91）。
- `install.doctor`（install.py:857-910）— 安装链的自检端：按 `_check_entry`（install.py:846）把每个客户端配置里的 command/args/env 与 `server_entry()` 重新算出的结果比对，`setup_gui.doctor` 只是它的网页包装（setup_gui.py:73-76）。

**数据结构与不变量**
- 生成文档与注册表同源：`ops_md()` 只读 `REGISTRY`（gen_docs.py:24-38），任何 op 的名称/类别/描述/参数约束变化都要重跑脚本，否则四条 CI 腿在 pytest 之前就红（verify.yml:77）。本次实测 `--check` 通过，`len(REGISTRY)==71` 且 `docs/operations.md` 恰好 71 个 `### \`op\`` 标题。
- `docs/op_test_matrix.md` 是「不受门禁的生成物」：`--check` 只比对 `files` 里出现的名字，而该字典仅在 `--matrix` 时加入矩阵（gen_docs.py:64-66），CI 从不带 `--matrix`（verify.yml:77）。当前它与 `matrix_md()` 输出仍相等，但新增测试里的 `"op": "<name>"` 会让它静默漂移。
- v8 报告是冻结快照：条数 31 硬编码在脚本里（check_benchmark.py:16），reference 目录名带日期，`docs/release-verification.md:24-25` 规定「历史报告不改、新测量进新目录」——换基准必须同时改常量或换路径。
- 安装链的隐式契约：Windows 进度窗靠正则 `"setup":\s*"([^"]+)"` 从子进程 stdout 抓 URL（AgentCAD-Setup.cmd:99），该 JSON 行由 `Wizard.serve` 打印（setup_gui.py:134）。改输出格式即静默失去「打开安装页」能力。
- 安装页安全不变量：只绑 127.0.0.1、校验 Host，`/api/*` 必须带 `X-AgentCAD-Token`，与 URL 中 `?t=` 的 `secrets.token_urlsafe(18)` 常量时间比较（setup_gui.py:49,172-184）；`/app/assets/` 文件名限 `[A-Za-z0-9._-]+` 且后缀在 `UI_TYPES` 白名单（setup_gui.py:192-196）。
- 行尾是构建输入：`*.cmd` 必须 CRLF、`*.command` 必须 LF 且可执行（.gitattributes:14-15），因为 `.cmd` 同时被 cmd.exe 解析和被 PowerShell 按标记切分；`web/viewer/**` 强制 LF 是为了「Windows 上构建也能复现提交的 app/index.html」（.gitattributes:18-19）。
- 两份「不发布」清单必须同步：`.gitattributes:7-13` 的 7 条 `export-ignore`（决定 GitHub `main.zip` 内容）与 `pyproject.toml:33-42` 的 7 条 sdist `exclude` 是同一组路径，靠人工对齐。
- 版本号三处同值：`pyproject.toml:3` 与 `src/agentcad/__init__.py:2` 均为 `0.1.0`，`store.py:38` 又把 `__version__` 写进每次提交的 `VERSIONS` 记录；hatch 没有 `[tool.hatch.version]` 动态版本，改一处不会带动另一处。
- 安装来源是分支而不是版本：两个安装器都指向 `archive/refs/heads/main.zip`（AgentCAD-Setup.cmd:7、AgentCAD-Setup.command:19），`install_info()` 从 wheel 的 `direct_url.json` 取 `source`（install.py:74-75），静态 `0.1.0` 意味着任何时点的 main 装出来都自称 0.1.0。
- 基准比较的前提是同一台机器：`env_matches` 要求 OS/machine/python/ezdxf/cpu_count 相等且 calibration 差异 <25%（harness.py:27-29,197-210），不匹配就「不比较」；`baseline.json` 里 `git.dirty=true`（由 harness.py:169-171 记录）说明该基线录于脏树。
- worktree 回收是破坏性的但有前置条件：`-Remove` 先用 `merge-base --is-ancestor` 校验本地与远端分支都在 `origin/main` 里，不满足就整体拒绝（new-worktree.ps1:82-87）。
- 测试临时目录受限：默认 tmp 在本机被阻塞，要求 `-p no:cacheprovider --basetemp=<短路径>`（AGENTS.md:27-29），CI 用 `${{ runner.temp }}/agentcad-tests`（verify.yml:79）。
- 查看器不参与业务但参与环境：`conftest.py:15` 与 CI 都设 `AGENTCAD_VIEWER=0`（verify.yml:66），两处必须一致，否则测试会拉起 viewer 进程。
- 依赖图必须由 lock 决定：CI 用 `uv sync --locked`（verify.yml:76），`docs/release-verification.md:3-6` 把「lock 含 marker 与 hash」当作可复现性前提；`uv.lock:1-11` 的 `requires-python = ">=3.10"` 与 `pyproject.toml:6` 必须一致。
- 基准结果格式自校验：`benchmarks/baseline.json` 的 `schema` 必须等于 `harness.SCHEMA`（harness.py:21,264），跨版本读旧基线不做迁移而是直接不比较。
- 每次提交都要有结论：PR 的新推送取消旧运行，`main` 的每次运行都保留（verify.yml:16-19）——这是 `main-health` 敢按 commit 写 status 的前提。
- 提交/推送边界写在文档而非 hook 里：只按路径 `git add`、不禁 `git add .`（AGENTS.md:38-40），仓库没有 pre-commit 配置，唯一的行尾检查是 `git diff --cached --check` 的人工约定。
- `docs/operations.md` 自称「与 `list_operations` 返回一致」（gen_docs.py:20），但两者是两条独立实现：文档走 `ops_md()` 的排序与分组，工具走 `Runtime.list_operations`；门禁只保证前者与注册表一致，不保证与 MCP/CLI 的输出字段一致。
- `examples/` 同时是证据与测试输入：`examples/autocad_benchmark/run.py` 被 CI 执行（verify.yml:86），`examples/trials/*.py`、`examples/benchcad/*.py` 被测试 import，所以「把 examples 全外置」会同时打断 CI 和测试。

**主要调用链**
1. Windows 双击 → `cmd.exe` 解析 `AgentCAD-Setup.cmd:1-20`（先探测 WSH 是否被禁用：12-13）→ `wscript.exe //E:JScript` 以隐藏窗口启动 PowerShell 并 `iex` 第 21 行之后的脚本 → `Run()` 用 `cmd.exe /d /s /c` 起子进程（69-71）→ 无 uvx 时先 `irm https://astral.sh/uv/install.ps1 | iex`（84）→ `uvx --refresh-package agentcad-runtime --from <main.zip> agentcad setup --gui`（77）→ 定时器从 stdout 抓 URL（99-105）→ 浏览器打开 `http://127.0.0.1:<port>/?t=<token>`。
2. macOS/Linux 双击 → `AgentCAD-Setup.command:14-19`（必要时 `curl -LsSf https://astral.sh/uv/install.sh | sh`）→ 同一条 uvx 命令前台运行，URL 直接打到终端，失败则提示后等回车退出（21-27）。
3. 安装页 → `POST /api/start`（setup_gui.py:219-221）→ 线程内 `install.setup(clients, root, progress=self._progress, shortcut=True)`（setup_gui.py:101-115）→ `install_info()`/`choose_launcher()`（install.py:54,103）→ `server_entry()`（install.py:138-148）→ 写 Claude CLI 或 `~/.codex/config.toml` 或 Claude Desktop / Cursor 配置（install.py:691-736）→ `create_shortcut()`（install.py:519+）写桌面图标，图标命令带 `--refresh-package` 日更（install.py:404-413）。
4. 开发机：`uv venv` + `uv pip install -e ".[dev]"`（README.md:44-45）或 `uv sync --locked --extra dev`（new-worktree.ps1:46、verify.yml:76）→ `.venv\Scripts\agentcad.exe --root playground mcp-config --write`（new-worktree.ps1:55）→ `mcp_config.mcp_config` 写 `.mcp.json`（mcp_config.py:66-79），该文件被忽略（.gitignore:37）。
5. PR：`plan` 用 `git diff --name-only HEAD^1 HEAD | grep -E "$MACOS_PATHS"` 选矩阵（verify.yml:37,48）→ 每条 `tests` 腿 `uv sync --locked --extra dev` → `gen_docs --check` → `pytest --basetemp=<runner.temp>/agentcad-tests`（77-79）→ ubuntu+3.12 额外 clone `U-C4N/Autocad-MCP@58a05dd`，用工作区解释器跑 `examples/autocad_benchmark/run.py`，再以 `scripts/check_benchmark.py` 判定（80-87）→ `upload-artifact` 收 JUnit 与 v8 目录（88-97）→ `merge-gate` 只读 `needs.{plan,tests}.result`（99-115）。
6. main：`main-health` 用 GH API 逐 job 判定，先写 `agentcad/macos`、`agentcad/main-health` 两个 commit status（190-191），再把无 runner 的合并打 `macos-unverified` 标签并评论（203-212），macOS 通过时按 `compare` 关系清标签（213-223）。
7. 本地性能：`python -m benchmarks`（benchmarks/README.md:13）→ `run.run()` 选 workload → `harness.measure()` 计时 → `compare_samples()` 与 `baseline.json` 比 → `--gate` 时非零退出（run.py:122-129）。
8. 改 op 后：`python scripts/gen_docs.py`（gen_docs.py:74-77）覆盖 `docs/operations.md`；要矩阵再加 `--matrix`。
9. 用户自检：`agentcad doctor` → `install.doctor`（install.py:857）→ 重新算 `server_entry()` 并与四个客户端的落盘配置比对 → `render_doctor`（install.py:911）给逐项结论；安装页的 `/api/doctor` 走同一条链（setup_gui.py:73-76）。
10. 记录基线：`python -m benchmarks --save` → `harness.fingerprint()` 采集 OS/CPU/版本/calibration 与 git commit+dirty（harness.py:169-192）→ `save_json` 写 `benchmarks/baseline.json`（harness.py:278）；提交它意味着把维护者机器的指纹带进仓库。
11. 证据留存：`upload-artifact` 每条腿都跑（`if: always()`），保留 30 天（verify.yml:88-97）；`if-no-files-found: error` 让「pytest 早失败到没产出 junitxml」这一步本身也变红。

**扩展点**
- 新增 op：只改 `src/agentcad/ops/`（注册表），然后重跑 `scripts/gen_docs.py`；`docs/operations.md` 是它唯一的自动投影，不要在文档里手写参数表。
- 新增 benchmark workload：在 `benchmarks/workloads.py` 用 `@workload(...)` 注册；`benchmarks/README.md:96-112` 要求自清理工作区、`quick` 档位保持 ~5 秒；质量检查加在 `benchmarks/quality.py` 并在 `tests/test_t_quick_benchmark.py:84-91` 断言条数为 6。
- 新增 CI 腿：矩阵字面量硬编码在 `verify.yml:39-42`，`EVENT != pull_request` 时给全部腿（43-44）；新增 macOS 敏感路径要同时改 `MACOS_PATHS`（verify.yml:37）与 `AGENTS.md:53-58` 的散文说明。
- 新增安装目标客户端：`install.CLIENTS` 与 `LABELS`（install.py:28-29）＋ `config_path()`（install.py:186）＋ `setup()` 里的分支（install.py:691-736）；`setup_gui` 的步骤列表自动跟随（setup_gui.py:89），页面文案在 `web/viewer/src/setup/`。
- 新增受门禁的生成文档：往 `gen_docs.py:64-66` 的 `files` 字典加一项即被 `--check` 覆盖；`op_test_matrix.md` 正因为没进这个字典才不受门禁。
- 换安装渠道：环境变量 `AGENTCAD_SOURCE` 可覆盖 zip 地址（AgentCAD-Setup.cmd:6-7），`AGENTCAD_SETUP_ARGS` 可追加 `setup` 参数（27）。
- 换构建后端或包内容：只改 `pyproject.toml:26-42` 的 hatch 段；没有 MANIFEST.in，也没有别的构建文件。
- 新增受门禁的图表资产：`docs/assets/source-capability/make_board.py` 提供 `--check` 模式，`tests/test_source_board.py:53-57` 以子进程调用它——把「生成器 + `--check` + 一条测试」作为新增生成资产的模板。
- 新增一门校验（例如 wheel 内容、前端产物）：只能加在 `verify.yml` 的 `tests` 步骤序列里（77-87）或新增 job 并接进 `merge-gate` 的 `needs`（103），因为 ruleset 只认 `merge gate` 这一个 check 名。

**风险与可疑之处**
- [高] 安装器拉整仓 `main.zip`，而 `examples/` 在工作树里实测 308 MB，其中三份 `evidence.zip` 占 129 MB（`examples/source_capability/results/*/evidence.zip`），`.gitignore:9-32` 只排除少量 Autodesk 派生目录，`export-ignore` 只排 7 个小文件（.gitattributes:7-13）。影响：首次安装要下载数百 MB（推测：zip 体积未实测，按工作树体积与 git archive 规则推断），而 `docs/install.md:40` 承诺「about a minute」。建议：证据移出主仓或改发布资产，安装器改指向只含 `src/` 与 `pyproject.toml` 的产物。
- [高] 安装来源是浮动分支且无版本固定（AgentCAD-Setup.cmd:7、AgentCAD-Setup.command:19），`pyproject.toml:3` 恒为 `0.1.0`，`--refresh-package` 只出现在桌面图标的日更里（install.py:404-413）。影响：两台机器同日装出不同代码却报同一版本，`doctor` 与 journal 的 `VERSIONS`（store.py:38）都无法区分；也没有任何 tag/release 自动化。建议：改用 Release tag 的 zip，或先发 PyPI（README.md:39 已承诺 `agentcad-runtime`）。
- [中] 发布资产不在 CI 里构建：README.md:35 与 docs/install.md:14-16 让用户下载 `releases/latest/download/AgentCAD-Setup.cmd` 与 `-mac.zip`，但 `verify.yml` 只有 plan/tests/merge-gate/main-health 四个 job，没有任何打包或上传步骤。影响：仓库里的 `installer/` 改动不会自动进入用户下载的资产，`latest` 又是浮动指针，实际发出的安装器可能与当前 `main` 不一致。建议：加 release job，或把安装器改成随仓库分发的文件。
- [中] 文档与代码的数字已漂移且无门禁：`REGISTRY` 现有 71 个 op，`docs/coverage.md:4`、`docs/tutorial.md:151`、`docs/acceptance-report.md:122` 仍写 43 个；`docs/acceptance-report.md:4` 引用 `docs/test-results.txt`（136 passed），而 README.md:131 声称 885 passed。影响：两份互相矛盾的验收数字，评审无从判断哪份是当前状态。建议：把「操作总数」「测试总数」也做成 `gen_docs.py` 的生成字段，或在 CI 里加一条 grep 断言。
- [中] `merge gate` 依赖 v8 基准步骤，而该步骤要联网 clone 上游并按 `--extra pdf --extra office` 建环境（verify.yml:84-87），与全部测试共享 25 分钟（verify.yml:64）。影响：GitHub/PyPI 抖动会让所有 PR 无法合并，且 `merge-gate` 只看 `tests` 的聚合结果，分不清「真失败」与「下载失败」。建议：v8 拆成独立 job，`main` 健康与 PR 门禁解耦，失败信息里显式区分网络原因。
- [中] macOS 相关性只靠一条正则，且失败时静默降级：`MACOS_PATHS`（verify.yml:37）不含 `src/agentcad/service.py`、`cli.py`，而 `AGENTS.md:53-58` 描述的是「管理进程、文件系统或 GUI 的模块」；`elif git diff … | grep -E` 若 diff 失败（浅克隆缺父提交，fetch-depth 只有 2，verify.yml:30），条件为假直接落到 else，矩阵只剩 Linux+Windows 且无任何 warning（48-53）。建议：diff 失败即失败退出，模块清单改为单一来源生成。
- [中] 构建产物没有任何验证步骤：CI 从不 `uv build`，也不在干净环境安装 wheel/sdist（verify.yml 全文无 build 步骤）。影响：`src/agentcad/viewer_static/app`（1.3 MB 提交产物）是否进 wheel、sdist 是否误带 308 MB `examples/`，都无证据；`pyproject.toml:33-42` 的 exclude 只对 sdist 生效且从未被执行过。建议：加 `uv build` + 干净环境 `pip install dist/*.whl` + `agentcad --help` 与 `setup.html` 存在的冒烟 job。
- [中] 提交的前端产物与源码无一致性检查：`web/viewer/README.md:22-24` 要求改完 `web/viewer/src` 后 `npm run build` 并提交 `src/agentcad/viewer_static/app/`，而 CI 不装 Node、不重建。影响：查看器与安装页的提交产物可落后于源码而无人发现。建议：加「重建后 `git diff` 为空」的 job，或写入构建哈希断言。
- [中] README.md:131 的门面证据无法在 CI 复现：那条「885 passed, 8 skipped」写明「with AutoCAD 2027 and FreeCAD 1.1.4 enabled」，而 CI 只有 Linux/Windows/macOS 通用 runner，可选后端一律 skip（verify.yml:78 的 step 名即承认这点），AutoCAD 又必须串行共享（AGENTS.md:23-26）。影响：最强的一条测试证据只存在于维护者机器上，评审无法复核。建议：把该数字标注为某一 commit + 特定机器的历史快照，并补一条 CI 侧的可复核替代（例如按后端分类的 skip 统计）。
- [低] 安装器把环境变量未加引号拼进 cmd.exe 命令行：`AGENTCAD_SOURCE` 与 `AGENTCAD_SETUP_ARGS`（AgentCAD-Setup.cmd:27,69-71,77）。影响：同一用户会话下预先设置这两个变量的脚本可在双击时执行任意命令（威胁模型限于本机用户）。建议：两个变量做白名单校验或改走参数数组。
- [低] 本地与 CI 的解释器/依赖不同源：README.md:44-45 用 `uv venv --python 3.12` + `uv pip install -e ".[dev]"`（绕过 lock），CI 用 `uv sync --locked`（verify.yml:76）；本机 `.venv` 实为 Python 3.13.15（`.venv/pyvenv.cfg:3`），矩阵最高 3.12；`uv.lock:4-11` 已含 3.14 的 marker；`hatchling` 未进 `uv.lock`（锁里无该条目），构建后端版本不受约束。
- [低] 依赖声明冗余且缺上界：`pillow` 同时是核心依赖与 `preview` extra（pyproject.toml:12,18,20），`preview` 实为空操作；`numpy` 无版本约束（pyproject.toml:13），锁里因此并存 2.2.6/2.4.6/2.5.4 三个 numpy 与 12.3.0/13.0.0 两个 pillow；`dev` 与 `live` 又各自重复 pin 了 `pywin32`。
- [低] 仓根没有 LICENSE 文件（`ls` 只有 README/AGENTS/pyproject 等），而 `pyproject.toml:7` 声明 `license = { text = "MIT" }`、README.md:155 写 MIT；同时缺 `[project.urls]` 与 classifiers。影响：wheel/sdist 不带许可文本，发布元数据不完整。
- [低] 两份文档对基准的定位互相矛盾：benchmarks/README.md:116 说「Not a CI target」，benchmarks/run.py:7 的注释却写 `--gate` 用于「CI or a pre-push check」；实际 CI 只跑 `tests/test_t_quick_benchmark.py`，`AGENTS.md:36-37` 要求的 `python -m benchmarks` 全靠自觉。
- [低] `docs/` 没有链接检查也没有字数预算：33 个条目里手写文档、生成物（`operations.md`、`op_test_matrix.md`）与运行记录（`test-results.txt`）混在同一层，只有前者受 `--check` 保护。影响：相对链接失效与过期记录都不会被发现。
- [低] 本地调试配置只有 Windows 形态：`.claude/launch.json:4-6` 写死 `.venv/Scripts/python.exe`，第三个入口直接调 `npm`（:37-45）。影响：macOS/Linux 贡献者无法直接用这些 launch 配置，与「四平台协同」的其余基建不对齐。
- [低] 安装器每次运行都在 `%TEMP%` 留下 `agentcad-setup-<guid>.log` 与 `.err` 且从不清理（AgentCAD-Setup.cmd:65,77）。影响：临时目录长期积累含本机绝对路径的日志，排障之外无用途。

**测试对应**
- tests/test_s_upstream_benchmark.py — 以子进程调用 `scripts/check_benchmark.py`（:37），对 9 类缺陷（fail/partial/environment_blocked/not_run/score/missing/duplicate/summary，:21）断言退出码；这是本区域唯一被测试直接覆盖的脚本。
- tests/test_t_quick_benchmark.py — 覆盖 `benchmarks/harness.py` 的百分位、序列化不计入计时、失败调用不得被记录为快样本、<2 ms 噪声地板、机器指纹拒绝、workload 的 profile/description 契约，以及 `quality.run_quality` 的 6 项必须全过（:84-91）。
- tests/test_source_board.py — 覆盖 `docs/assets/source-capability/` 的生成关系：字段 JSON 自洽、field README 表格与 JSON 一致、`make_board.py --check` 保证 SVG 与 JSON 同步（:54-57，缺 matplotlib 时跳过）。
- tests/test_q_install.py（399 行）与 tests/test_q_setup_gui.py（510 行）— 安装链行为：假 home 下写 Claude/Codex/Cursor 配置、`main.zip` 源（test_q_install.py:307、test_q_setup_gui.py:195）、wizard 的 token 与路由。
- tests/test_n_pro_comparison.py、tests/test_trial_harness.py — 证明 `examples/` 里的 harness 也是被测代码（`examples/trials/pro_comparison/run.py`、`examples/trials/agents.py` 被 import 后断言），因此 `examples/` 不能整体外置。
- 无测试覆盖：`scripts/gen_docs.py` 只由 CI 的 `--check` 覆盖（本地 pytest 不跑它）；`scripts/new-worktree.ps1` 完全没有测试；`verify.yml` 的 `plan` 与 `main-health` 逻辑只在真实 PR 与合并上执行，没有离线用例。
- 收集范围由 `pyproject.toml:44-46` 决定（`testpaths=["tests"]`、`addopts="-q"`）：`scripts/`、`benchmarks/`、`examples/` 都不被 pytest 直接收集，只能经 `tests/` 里的用例间接跑到——这也解释了为什么本区域的三个脚本里只有一个有测试。

## 10. 附录：文件清单

由脚本从 `src/agentcad` 生成；行数与 docstring 取生成时的 `fe2cdaa`。角色缩写：入口/核心/支撑/适配/数据/工具。

### 10.1 源码（73 个文件 / 19,508 行）

| 文件 | 行数 | 模块 docstring 首句 |
|---|---:|---|
| `src/agentcad/__init__.py` | 2 | AgentCAD Runtime — a headless, agent-native 2D CAD runtime over DXF. |
| `src/agentcad/_image_runner.py` | 151 | Child process for ``inspect_image(..., code=...)`` (see scripting.py, images.py): measures pixels of a workspa |
| `src/agentcad/_script_runner.py` | 70 | Child process for ``run_script`` (see scripting.py): loads job/in.dxf, runs job/script.py with the drawing as |
| `src/agentcad/appearance.py` | 39 | Resolve saved entity visibility, including DIMENSION block inheritance. |
| `src/agentcad/backends/__init__.py` | 0 | — |
| `src/agentcad/backends/_fc_view.py` | 320 | FreeCAD side of the live viewer's 3D view. Runs inside FreeCAD's own Python (``python.exe -I _fc_view.py <job> |
| `src/agentcad/backends/acad_live.py` | 260 | Optional live AutoCAD session (the user's running AutoCAD GUI, over COM; Windows + pywin32). |
| `src/agentcad/backends/autocad.py` | 183 | Optional AutoCAD backend (headless accoreconsole.exe): DWG import/export and |
| `src/agentcad/backends/freecad.py` | 244 | Optional FreeCAD backend for the live viewer's 3D view. AgentCAD does not build 3D parts: agents write them wi |
| `src/agentcad/cli.py` | 411 | ``agentcad`` command-line interface. Every command prints one JSON envelope |
| `src/agentcad/constraints.py` | 292 | Geometric constraints. |
| `src/agentcad/delivery.py` | 199 | Read delivery artifacts independently; reject corruption and lost drawing data. |
| `src/agentcad/demo.py` | 105 | A live demo to show people: ``agentcad demo`` opens the live viewer and draws a mounting plate step by step, t |
| `src/agentcad/desktop.py` | 28 | Show a folder, or a file inside its folder, in the computer's file manager: the viewer's "Show in folder" and  |
| `src/agentcad/dimension_display.py` | 191 | Read the visible primary value from a saved DIMENSION graphics block. |
| `src/agentcad/discovery.py` | 60 | Searchable native command catalogue; aliases describe implemented workflows. |
| `src/agentcad/docspec.py` | 295 | Technical-document → structured plate spec, with source traceability. |
| `src/agentcad/drawing_frontend.py` | 16 | Use DXF block-reference properties for DIMENSION graphics in every preview. |
| `src/agentcad/engine.py` | 204 | Transactional operation engine. |
| `src/agentcad/engineering/__init__.py` | 1 | Deterministic drawing workflows and readers. Readers receive only saved geometry. |
| `src/agentcad/engineering/architecture.py` | 462 | Orthogonal wall plans, hosted openings, stairs, room detection and schedules. |
| `src/agentcad/engineering/mechanical.py` | 485 | Revolved-part drawing views, ISO-size sheets, drawing-derived BOM and linked balloons. |
| `src/agentcad/engineering/mechanical_annotations.py` | 418 | Saved manufacturing annotations for the declared revolved-profile geometry. |
| `src/agentcad/engineering/pid.py` | 279 | Port-connected P&ID creation and independent graph reconstruction from DXF. |
| `src/agentcad/engineering/plant.py` | 443 | Evidence-based foreign plant drawing description and preliminary quantities. |
| `src/agentcad/engineering/records.py` | 97 | Entity-local domain records that travel with DXF geometry. |
| `src/agentcad/errors.py` | 87 | Structured, machine-readable errors. |
| `src/agentcad/fits.py` | 63 | Verified ISO limit deviations for a bounded set of common fit classes. |
| `src/agentcad/geom.py` | 232 | Small, dependency-free 2D geometry kernel used by edit operations and the |
| `src/agentcad/guards.py` | 62 | Optimistic edit preconditions, independent of the journal's event sequence. |
| `src/agentcad/http_api.py` | 165 | Minimal HTTP JSON API (stdlib only), sharing ``Runtime`` with CLI and MCP. |
| `src/agentcad/image_dimensions.py` | 288 | Check caller-supplied drawing anchors and solve their linear dimension graph. |
| `src/agentcad/images.py` | 123 | Read-only image inspection for small callouts and section views in drawings, and pixel measurement by script. |
| `src/agentcad/install.py` | 919 | Install helpers for people, not agents: the default workspace folder, ``agentcad setup`` (writes the MCP |
| `src/agentcad/locking.py` | 51 | Nonblocking OS write locks, released on process exit rather than by age. |
| `src/agentcad/mcp_config.py` | 80 | Generate MCP client configs for this machine. Paths in an MCP config are |
| `src/agentcad/mcp_server.py` | 496 | MCP server (stdio). Each tool is a thin adapter over ``Runtime``; no CAD |
| `src/agentcad/measure.py` | 196 | Analytic planar measurements and conservative spatial selection. |
| `src/agentcad/metadata.py` | 45 | Runtime metadata stored *inside* the DXF (rootdict -> AGENTCAD dictionary -> |
| `src/agentcad/model.py` | 378 | Drawing data model: entity serialization, tags and entity selection. |
| `src/agentcad/ops/__init__.py` | 5 | Operation catalog. Importing this package registers all operations. |
| `src/agentcad/ops/analysis.py` | 114 | Drawing operations that derive geometry or clean existing entities. |
| `src/agentcad/ops/annotate.py` | 501 | Dimensions, leaders, tables and title blocks. |
| `src/agentcad/ops/draw.py` | 413 | Primitive creation operations. |
| `src/agentcad/ops/edit.py` | 1076 | Geometry editing operations (AutoCAD MOVE/COPY/ROTATE/MIRROR/SCALE/STRETCH/ |
| `src/agentcad/ops/engineering.py` | 63 | Domain operations participate in the same transaction and permission policy as primitives. |
| `src/agentcad/ops/library.py` | 673 | Industry libraries as operations: P&ID symbols and lines, GD&T annotations, ISO standard holes and parts. |
| `src/agentcad/ops/registry.py` | 352 | Declarative operation registry. |
| `src/agentcad/ops/sheet.py` | 143 | Printed sheets: paper-space layouts and viewports onto model space. |
| `src/agentcad/ops/structure.py` | 191 | Layers and blocks. |
| `src/agentcad/plot.py` | 466 | Plot to PDF without external software. |
| `src/agentcad/policy.py` | 94 | Execution policy: sandboxed workspace, read-only mode, op allow-list, budgets. |
| `src/agentcad/progress.py` | 212 | Progress events: what the runtime is doing right now, in a form the live viewer can show in plain words. |
| `src/agentcad/quality.py` | 120 | Observable drawing quality and delivery evidence. |
| `src/agentcad/scripting.py` | 178 | Opt-in escape hatch: run an agent-written ezdxf script on a drawing, inside the runtime's transaction. |
| `src/agentcad/service.py` | 1590 | Unified business API. CLI, MCP server and HTTP server are thin adapters over |
| `src/agentcad/sessions.py` | 177 | Which assistants are connected: one small file per MCP server process, so the live viewer can say "Claude Code |
| `src/agentcad/setup_gui.py` | 233 | Setup wizard: ``agentcad setup --gui`` does what ``agentcad setup`` does, in a local web page instead of a ter |
| `src/agentcad/spec.py` | 283 | Structured part specifications. |
| `src/agentcad/stdlib.py` | 330 | Industry libraries: data behind the library ops (ops/library.py). |
| `src/agentcad/store.py` | 579 | Drawing persistence: safe load/save, checkpoints, append-only journal and |
| `src/agentcad/tabular.py` | 125 | Drawing text/attributes exported as data, never executable spreadsheet formulas. |
| `src/agentcad/tolerances.py` | 161 | Explicit signed dimension deviations; no unverified ISO fit-table lookup. |
| `src/agentcad/units.py` | 77 | Explicit length units. |
| `src/agentcad/validate/__init__.py` | 107 | Independent validator. |
| `src/agentcad/validate/checks.py` | 574 | Generic drawing-integrity checks (no spec needed). |
| `src/agentcad/validate/drafting.py` | 384 | Drafting-practice checks: what a checker marks on a drawing whose geometry and values are right. |
| `src/agentcad/validate/features.py` | 317 | Feature recognition from *actual file geometry*. |
| `src/agentcad/validate/requirements.py` | 158 | Check explicit requirements against geometry recognised from the file. |
| `src/agentcad/verification.py` | 128 | Bind physical requirements to dimension anchors, independently of displayed text. |
| `src/agentcad/viewer.py` | 1159 | Live viewer: a local web page that shows a workspace's drawings and parts as agents change them (stdlib only). |
| `src/agentcad/viewer_link.py` | 66 | Finding a running live viewer of a workspace, without loading the viewer itself. |
| `src/agentcad/worker.py` | 19 | A cancelled waiter cannot release a running CAD worker's serialization slot. |
| **合计** | **19508** | |

### 10.2 测试

`tests/`：45 个 `test_*.py`，10399 行，另有 `conftest.py`、`fixtures/`、`golden/`。测试组织与覆盖见第 8 章。

### 10.3 源码之外的代码面

| 路径 | 作用 |
|---|---|
| `scripts/gen_docs.py` | 从 `ops.REGISTRY` 生成 `docs/operations.md`；`--check` 是 CI 检查 |
| `scripts/check_benchmark.py` | 校验 U-C4N v8 31 题报告完整通过（CI 步骤） |
| `scripts/new-worktree.ps1` | 每个任务一个 worktree + 独立 `.venv`/`.mcp.json` |
| `benchmarks/` | 本地开发基准（`python -m benchmarks`）：延迟/载荷/绘图检查，见 `benchmarks/README.md` |
| `.github/workflows/verify.yml` | CI：测试矩阵、merge gate、main-health、v8 benchmark |
| `installer/` | Windows `.cmd` 与 macOS/Linux zip 安装器 |
| `web/viewer/` | viewer 页面源码（构建产物在 `src/agentcad/viewer_static/app/`）；`docs/` 文档；`examples/` 试验证据 |

## 11. 校验记录与维护

**基线**：`fe2cdaa`；环境 Windows 11 / Python 3.13.15 / ezdxf 1.4.5。

**方法**：12 个分析子代理分批读码（每批 ≤4 并发）产出第 2–10 章，再由 2 个**独立**校验子代理对照代码核查：一个做断言抽查（45 条断言 + 2 个缺陷独立复现），一个做覆盖与一致性（49 个核对项，含脚本化的文件覆盖、依赖图、目录/编号、跨章矛盾）。校验者不读分析者的推理，只读文档与代码。

**结果**：断言抽查 37/45 通过、5 条 FAIL、3 条 PARTIAL；两个缺陷复现均成立；一致性 36/49 通过。已按校验结果修正全文，主要更正如下（这些数字现在由 `scripts/code_map_stats.py` 统一生成，避免再漂移）：

| 项 | 原文 | 更正 |
|---|---|---|
| 直接 `import ezdxf` 的模块 | 31 | **30**（`scripting.py` 里那句是注释，不是导入） |
| 内部依赖边 | 262（179 加载期 + 83 函数内） | **262 / 179 / 83**（见脚本口径；此前三种统计口径都复现不出同一组数） |
| `validate/checks.py` 的 `issue()` 调用点 | 31 | **32** |
| `retryable=True` 的错误码 | 5 | **9**（另含 AutoCAD/FreeCAD 的忙与超时码） |
| `engine.COPY_ATTRS` | 8 类 | **9 类**（漏了 POINT） |
| MCP 显式包装 | 25（与同行“35 个”自相矛盾） | **26**（26 + 9 = 35） |
| 前 12 个文件占比 | 44% | **46%**（8,980 行） |
| `docs/` 体量 | 32 篇 / 5,304 行（两个基数混算） | **递归 32 篇 / 6,094 行**（顶层 29 篇 / 5,306 行） |
| `docs/architecture.md` 的操作数 | 写 41，与代码不符 | 本 PR 已改为 **71**（coverage/tutorial/acceptance-report 的“43”仍待改，见 7.4） |
| 循环依赖表两行 | 把函数内导入写成“顶层” | 按 AST 重新标注（`ops` 三角全为函数内；`engine`+`engineering` 大环顶层/函数内混用） |

**两个已复现、登记在第 7.6 节的缺陷**（校验者独立复现一致）：选择器过滤对象拼错键名会命中整个布局；连续 `revise_drawing` 会在 DXF 中累积旧 `part_spec` 的孤儿 XRECORD。

**仍保留、未在本 PR 修复的偏差**（属于需要单独决策的产品/法务改动，已列在 7.4）：`docs/coverage.md:4`、`docs/tutorial.md:151`、`docs/acceptance-report.md:122` 的“43 个操作”；`docs/benchmarks.md:55` 的上游 PR “pending review” 陈述；仓库缺少 LICENSE 文件。

**维护清单**：
1. 移动/增删 `src/agentcad` 下的模块后，跑 `python scripts/code_map_stats.py`，按输出更新 1.2、1.5、各章“规模”行与第 10 章附录。
2. 新增操作/工具/验证规则时，按第 6 章的路径改，并补对应测试（第 8 章列了各测试文件覆盖什么）。
3. 第 7 章的热点是会过期的：改动事务层或输出管线后，用 `python -m benchmarks`（见 `benchmarks/README.md`）重测第 7.2 节锚点。
