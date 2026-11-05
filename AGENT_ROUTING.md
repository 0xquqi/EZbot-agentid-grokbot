# Agent 与直接工具的分工

**需要推理时，已配置、已验收的第三方优先；确定性工作直接用工具。**第三方优先不等于每个步骤都必须再调用一个模型。

## 角色表

| 任务 | 正常路径 | Agent接口及触发条件 |
|---|---|---|
| 项目发现 | 原方法搜索、去重、初筛 | discovery |
| 项目介绍＋参与教程＋风险 | 一次生成结构化简报，复核后缓存；展示按字段取值 | project.brief |
| 深入研究/定位目标 | 复用已有有效来源，只补缺失或过期证据 | research / contract.resolve |
| 新目标审计＋参数/后续模板 | 本地先匹配方法，外部按原方法审计 | audit.prepare；audit保留只读专项用途 |
| 已审模板的当前交易 | 直接填当前钱包参数、编码、模拟和钱包预览 | trade.prepare仅在工具明确提出NeedsAgent时调用 |
| 交易提交 | 单一订单入口＋已授权执行通道 | trade.execute；通道可以直接执行工具，不要求再推理 |
| 回执与到账 | 独立RPC/钱包读取 | receipt.inspect仅解释异常/未知结构 |
| 当前授权 | 直接链上权限读数 | approval.inspect仅解释未知权限结构 |
| 撤权 | 按原钱包确认与精确对象提交 | approval.revoke；无保留白名单 |
| 后续领取/解锁 | 按已审任务清单，到检查时间直接读取状态 | followup.plan仅补新的机制信息 |
| 方法/案例总结 | 已复核源码和审计材料自动提取候选 | case.summarize可补充分析，不能自行启用规则 |

BotRuntime.describe/explain 都调用同一project.brief，返回同一份内容；project_view仅做字段投影。BotRuntime.read会把旧project.describe/participation.explain角色归并到project.brief。底层旧只读角色仅为兼容保留，不作为产品默认的连续两步。

组合简报的data须有summary字符串、participation列表、risks列表、非空sources列表、observed_at时间及method_digest。新接入方需声明并验收project.brief能力，旧两个接口的声明不会被伪装成组合能力。

## 优先级与回退

- 一个provider可承担多个角色；routes中的具体角色覆盖`*`。显式null指定原版流程。
- BotRuntime只优先选择能力验收有效、原方法版本匹配且宿主未判为不可用的接口。无配置/不支持/未验收则走真实原版执行器。provider名称错误明确报错。
- 只读连接失败按automatic/wait/notify处理。wait只保存截止时间，不阻塞；notify等待宿主明确允许回退。两次实际调用分别计预算，超时的远端仍可能计费。
- pending、拒绝外发、错误证据/回复不通过换Agent绕过。已经派出的写任务固定执行者，未知结果只查询，不切换重发。
- native_send负责原版Skill和工具适配，不是随包提供的免费模型。确定性钱包操作应直接调用原工具。

## 语义缺口升级

工具适配器只有在确实需要解释时抛出NeedsAgent(reason,evidence_version,missing)。例如新的事件格式或无法解释的参与机制。不要用它处理RPC超时、余额不足、未授权、普通pending或缺少工具适配器。

DelegatingHost先尝试直接工具；出现NeedsAgent后才委派，随后仍由独立宿主验收。相同问题、输入和证据版本映射到稳定analysis任务ID。证据版本应标识决定该问题的内容，如代码/日志身份，不能每轮拿当前时间冒充新证据。轮询时间和上一次pending任务号不参与问题摘要。

AgentPending暴露task_id；宿主用BotRuntime.analysis_status继续查询，完成后重走同一入口即可复用结果，不另起任务。异常分析不会给交易权限，也不会让外部的verified字段直接成为链上事实。

## 连接宿主

推荐只创建BotRuntime，自动连接路由、直接工具优先适配器和订单流。不要手工把每个标准查询接成Agent任务。宿主必须实现原版执行器、工具填参/预览、原钱包确认、独立链核验以及相应复核回调，详见skills/agentid-orchestrator/references/integration.md。

服务使用HTTPS或stdio适配，其他协议可封装；不把私钥、会话、全库或个人签名发给研究接口。每次外发由宿主检查数据范围与费用，可复用有效且覆盖本次范围的安装者授权。

能力验收、任务控制、研究预算及缓存细节见LEARNING_AND_RECOVERY.md。当前真实服务和Grok宿主尚未联调，没有公开安装链接。
