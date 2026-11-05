# 统一接入契约

## 1. 产品入口与职责

Python 3.11+、macOS/Linux、标准库。宿主使用BotRuntime(state_directory, owner, config, host)，支持上下文管理器/close。私有目录必须在模板源码之外；owner来自宿主身份，不来自聊天文本。每安装者独立数据库，启动无外发或钱包动作。

产品顺序：discover → brief（describe/explain为同一结果视图）→ select → prepare → confirm → execute → reconcile/status。select_many只接受逐钱包明确intent，不自动执行。cancel仅适用于未提交订单。

只有MintCoordinator实现交易执行；Workflow只保留研究/审计/学习。AgentRouter是指定执行者的底层传输，PriorityRouter实现优先级与持久选择，不直接作为公共钱包HTTP接口暴露。

CLI只支持本地案例工具；delegate明确要求带真实宿主的BotRuntime，不能从CLI绕过费用披露或确认。

## 2. 原方法与角色

研究方法为原版EZbot-grok-project-skill中的ezbot-grok-scout。verify_installation检查完整资源，可信部署端生成installed_methods；远端自报摘要不能替代核验。研究角色自动绑定required_method，完成结果须回传method_digest。

项目与审计资料均为不可信数据；原方法保持只读，外层工具构造交易。已有宿主核实的来源/源码/目标角色可复用，不要求再次搜一遍网站。

project.brief一次返回data中的summary、participation、risks、sources、observed_at及method_digest。介绍和教程缓存同一个结果，经host.review_research_result核验后复用。project_view仅取字段，不调用Agent。底层旧project.describe/participation.explain兼容角色不作为产品连续两步；新组合能力需单独验收。

完整角色、直接工具优先规则见根目录AGENT_ROUTING.md。

## 3. 传输与异步

providers/routes初始为空。HTTPS配置url、可选auth_env、timeout_seconds，不跟随重定向。stdio配置绝对argv，不用shell，只继承PATH/LANG及显式pass_env。其他协议通过安装者适配器接入，不假称可直接调用任意聊天API。研究接口不接收钱包登录态/私钥。

请求protocol=agentid-task/v1，包含task_id、role、payload、request_digest。响应回传task_id/request_digest，status为completed/pending/needs_confirmation/failed/cancelled，data为对象。写完成响应不是链上完成。

任务ID和请求/执行者配置持久绑定；写调用先落盘再发送，未知不重试。第三方按独立能力验收、方法版本和健康状态优先，无配置则用host.run_original_agent。该回调可直接调用工具，不要求每个角色都启动模型。

disclose回调由host.accept_external_cost_and_data实现，展示实际外发范围及费用/未知计费。原钱包authorize另行核对交易授权，可使用同一次仍有效且绑定计划的真实确认，不让用户机械重复确认。

异步控制protocol=agentid-task-control/v1，operation=status/cancel，响应仍绑定原请求。声明task_control才可使用。task_status查询原执行者；取消仅限只读任务，明确cancelled才算完成。写的链上取消/替换另走原钱包授权。analysis_status查询NeedsAgent产生的稳定分析任务；shared_task_status处理共享简报任务。配置变更不盲目查询新执行者。

## 4. 审计、方法和模板

snapshot包含chain_id、address、完整runtime_sha256、implementation_sha256、proxy_kind、selectors、context。context含permissions/payment_path/dependencies完整身份；代理记录实现地址、代码及相关路径。未知不伪造为空。resolution由可信宿主提供host_verified、chain_id、address、role=mint、sources、review_ref，并与snapshot一致。

审计返回verdict=pass/fail/unknown、evidence、risks、static_findings。host.review_audit核对具体证据，不是机械要求另一个模型重做全文。只复核事实、关键结论和缺口；无法证明就补证或保留未知。

同链同目标已审快照匹配才复用其静态结论。相似源码只得到PatternStore的检查方法建议；不同地址不继承pass。目标匹配优先于其他部署，避免别的合约记录挤掉本目标缓存。

模板未命中时一次audit.prepare返回audit和recipe。已有source_material/target_resolution随任务提供，补缺失事实即可。recipe基本字段为function_signature、argument_sources、output_check；参数来源只允许quantity/recipient/wallet/fresh_proof/fresh_signature/fresh_nonce/fresh_price/user_referrer/activity_id，产出检查为asset_balance_delta/nft_ownership/verified_position。

可附followup_checks列表，每项只有id、kind（claim/reveal/unlock）、schedule_source（protocol_schedule/receipt_deadline）、check（claimable/revealed/unlocked）。不缓存实际签名、proof、nonce、接收人、推荐人值或可发送calldata。host.review_template核验ABI、参数顺序与后续检查结构，复用同次审计证据。

若审计含method_checks且已有宿主核实的source_material，Workflow.learn直接复用该源码提取候选，不再调用pattern_material取源码。缺少材料时可以由宿主补充；不得伪造部署绑定。方法启用、合约撤回、进化规则见根目录LEARNING_AND_RECOVERY.md。

## 5. 直接准备与语义缺口

host.finalize_plan(order,snapshot,template,learned_checks,proposal=None)为默认准备入口：直接取本钱包当前参数、可信ABI编码、模拟及钱包预览，没有链上写副作用。proposal=None表示模板本身已提供结构，不需要额外Agent方案。当前激活的附加检查仍需验证。

只有缺少机制解释时抛NeedsAgent(reason,evidence_version,missing)。DelegatingHost调用trade.prepare后把结果交给同一finalize_plan验收。其他RPC/账户/权限错误直接报告，不启动模型猜测。缺少宿主实现也不通过Agent冒充工具。

标准查询分别实现host.verify_order_result、host.has_authority、host.verify_followup，直接读取独立来源。明确NeedsAgent时才调用receipt.inspect/approval.inspect/followup.plan，再交给verify_order_evidence/verify_authority_evidence/verify_followup_evidence独立验收。接口返回verified不能直接成为宿主结果。

相同问题/证据版本稳定绑定任务。AgentPending包含task_id；先查结果，完成后再进同一入口，不随机换ID重新付费。版本应表达相关证据内容，不用每轮时间制造变化。

## 6. 订单、完整计划核验与增量快照

intent六字段：project_id、activity_id、chain_id、wallet、recipient、quantity（正整数字符串）。订单按owner/request_key与意图去重；新钱包/份数/接收人独立建单，旧确认不可复用。

plan必须包含：

- intent_digest、transaction（chain_id/wallet/to/value_wei/data）、audit_ref、实际preview_request_id、expires_at（最早报价/证明/预览时效）；
- static_binding（代码/实现/权限/支付路径/依赖）、dynamic_binding（价格/资格/剩余额度/开放状态/证明身份等）；
- expected_assets、risks、approvals、followups（唯一id，可含not_before和poll_interval_seconds，默认60秒）；
- costs：kind=payment/mint_gas/cleanup_gas/followup_gas/service，asset=native或地址，amount_raw为整数串；四项支付/gas类型显式存在，确实无费用写0；balances按资产保存整数余额。

host.verify_plan只在新计划prepare时做完整核验：合约结论、ABI/calldata、接收人/数量、支付与授权路径、实际预览、当前配置和原钱包政策。OrderStore保存plan_review的plan_digest、cost_totals与核验时间。confirm绑定同一计划摘要并履行原钱包提示/确认。

执行前不再调用完整verify_plan。host.current_checks(order)返回一次当前快照：verified/evidence_ref、同一plan_digest、policy_valid、observed_at、valid_until、static_binding、dynamic_binding、preview_valid、balances。当前配置/账户/授权策略由policy_valid明确核对；原钱包最终authorize仍验证真实授权。

程序验证快照未过期且绑定计划；静态变化进入needs_reaudit，动态变化/过期进入needs_refresh，旧确认失效。费用总额复用已审plan_review，余额重新读取。没有plan_review的旧待执行订单必须重新prepare；不能靠兼容模式跳过。

## 7. 执行、终局与清理

钱包FIFO和进程间锁按chain/wallet隔离；确认后排队，执行前刷新余额。第一次授权/交易之前Cleanup.track记录本流程全部使用权限，包括旧授权。未知提交持久保留占用，不因超时释放。host.track_submission只保存凭据并跟踪，不必须再次启动一份全量到账核验。

host.verify_order_result返回verified/evidence_ref和pending/unknown/mint_verified/reverted/aborted_no_pending，核对交易身份、实际资产/数量/接收人。只有finalized=True表示达到宿主链确认策略。未终局继续读取，不清理或释放占用；已终局且绑定本订单/计划的事实可复用。

sequence_terminal=True还表示整个授权/交易序列已结束。此时Cleanup.finish可复用同一可信终局证据，不再调用workflow_status；否则workflow_status仍须独立核对全序列。两种事实不能混为一谈。

清理回调：has_authority（True仍有权限、False已独立核实归零/失效、None未知）、supports_revoke、in_use_elsewhere、confirm_revoke、revoke、revoke_status。原钱包提示/确认完整保留。Permit2两层及未消费签名的失效机制需分别处理；未知类型留待处理，不能冒充clean。

只有实际归零才clean。整个清理完成后写入cleanup_completion，旧流程封存，不重新撤销后来新任务的授权。需要处理新授予权限时建立新的有明确范围的工作流，不能复活旧清理。

后续任务到not_before/next_check_at才调用直接读取，未完成按poll_interval_seconds再查；已核实的更晚检查时间可以延后。分析pending只保留任务ID，不反复新开Agent。领取待办不占用已终局且清理完成的钱包；后续交易仍需新授权。

## 8. 运行和发行

能力验收、研究预算、缓存、任务恢复、方法复现与私有备份见根目录LEARNING_AND_RECOVERY.md；配置由安装者填写。额度默认无上限不提供交易授权；自动发现/订阅需要宿主真正支持且用户明确启用。

当前仅本地运行库及测试。所有真实宿主回调都必须实际实现，不能用固定True冒充链上验证。原钱包20文件、原研究方法外部锁依赖不改。公开发行按文件哈希白名单构建，不含私有案例、学习报告、数据库或测试夹具，公开发布另需用户对应授权。

## 9. 独立审计后的执行边界

- 运行库在准备和发送入口绑定审计案例的 chain/address 与 transaction.to，并逐项绑定代码/实现哈希及权限/支付路径/依赖快照。当前仅支持直接调用被审目标的计划；额外路由合约不能借用另一个目标的审计。expected_assets 必须包含与意图 recipient/quantity 相符的条目；宿主仍负责 ABI、实际产出及单位语义验证。
- `DelegatingHost.revoke` 优先调用已实现的 `host.revoke(approval,idempotency_key=...)`。仅未提供直接实现时使用原有 approval.revoke 路由。直接写回调抛任何异常都不切换服务重试；撤权仍通过 Cleanup 持久去重、确认、查询归零。
- `PreDispatchRejected` 只可由可信的本地发送前门禁抛出，表示肯定未外发。普通 PermissionError/TimeoutError 在发送回调内发生仍属未知，绝不能冒充该类型。可证明未发送的交易退回 awaiting_confirmation、清除确认、释放队列并移除未使用的清理跟踪；同任务 ID 的本地预算拒绝允许重新验收后重试。未知任务不重试。
- reconcile 与 execute 使用同钱包 guard；有发送者持锁时不接受终局。崩溃遗留 dispatching 仅在取得锁后转为 submission_unknown，再由宿主独立对账。直接 OrderStore.reconcile 不终结 dispatching。needs_refresh/needs_reaudit 等发送前状态的遗留队列可在同锁下恢复。
- `reverted` 结果暂停同目标/代码的静态复用，进入 outcome_review；不是直接判定合约有漏洞。可信宿主核对失败原因后调用 CaseStore.review_outcomes(case_id, review)，review 必须含 approved/reviewer/evidence_ref/reason；记录绑定当时全部失败事件，新增失败再次暂停。未广播的 aborted 不自动污染合约结论。
- 描述缓存的本地拒绝立即结束 pending。未知且过期的共享任务返回 reconciliation_required 和原 task ID，须查询原任务，不自动重派或承诺远端费用为零。终局结果查回后才恢复正常 TTL 流程；崩溃前尚未派发的孤立任务需要宿主确认原工作者已停止并人工恢复，不能只凭 TTL 推定未发送。
- review_research_result 推荐返回 verified/reviewer/evidence_ref 字典，记录在 research_reviews；旧布尔 True 兼容但只标 legacy_boolean，不伪造审核来源。project.brief 限 100 KB 和每列表 100 项；内容仍是外部不可信资料，不能作为指令。
- CLI case-record 强制 verified=False，不能从 stdin 自证通过。审计晋升只能由可信宿主完成。共享 shell/数据库写权限并不构成安全隔离。
- 非默认限额/推荐人配置需要 validate_personal_config 实现验收，详见 PERSONAL_CONFIG.md；运行库不冒充已实现完整支付额度策略。

## 10. 待领权益和流水

运行库新增 ClaimLedger，BotRuntime 提供 track_entitlement/pending_claims/refresh_claims/record_claim/claim_history。成功铸造终局会记项目权益核查项；宿主 entitlements_for_order 独立核实后登记具体权益。周期权益不因领取一次而关闭，每次实际领取按稳定attempt ID、交易哈希、终局和到账证据记账。verify_followup 可附 claim_event 自动记账；刷新可补记其他入口的领取。标准查询直接工具，明确NeedsAgent时复用followup.plan路由并验收。完整字段、去重/隔离与调度边界见根目录CLAIMS.md。账本不提供新的链上写接口，不将已登记权益当作执行授权。

## 11. Claims 独立审计接入修订

审计 audit 新增 mint_selectors：无0x前缀的8位小写十六进制字符串列表，须属于已核实 snapshot.selectors，并由 host.review_audit 核实仅为本次允许的铸造入口。准备/发送/最终授权均检查 calldata 的前4字节，不能仅凭“合约含此函数”授权任意函数；参数语义仍由宿主核验。旧案例缺少该字段不能直接快速执行，须补做可信审计。expected_assets 每一项都验证地址与数量格式，不因第一项匹配而跳过后面的条目。

confirm 只在短暂恢复遗留队列时尝试拿钱包锁，用户确认UI不持锁，可在同钱包其他订单执行时确认排队。实际发送与对账仍共用钱包锁。

新订单 pre_send 写入 transport_started=False；进入路由前持久化 True。在持有钱包锁的恢复中，仅明确 False 可证明本流程未进入发送：移除未使用的清理跟踪、退回 awaiting_confirmation 并释放队列。True 或旧记录无字段均按未知处理；submission=None、aborted_no_pending 和没有 pending 都不能单独证明从未产生授权副作用。

未知发送缺清理日志时返回 cleanup_pending/permission_journal_missing，不自动认定 clean、不盲目把全部计划权限作为已用权限。宿主可通过 BotRuntime.recover_cleanup(order_id) 调用 verify_used_approvals(order)，返回 verified/evidence_ref、order_id/chain_id/wallet、sequence_terminal=True 和实际使用权限完整列表 approvals（可为空）。仅独立终局订单允许恢复；重建后走正常撤权核验。不能将计划列表直接充当独立实际使用证据。

权益能力检查、事件轨迹、终局复核和周期序号详见CLAIMS.md。BotRuntime.review_case_outcomes(case_id,evidence) 为具体合约失败原因复核提供宿主门禁封装。旧的确认/恢复说明以本节为准。
