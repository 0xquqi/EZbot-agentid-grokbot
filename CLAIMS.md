# 待领权益与领取流水

Bot 记住参与项目后还有哪些权益可领，并持续记录每一次领取。范围是已跟踪的 Agent ID / Agentic Wallet 生态项目，可登记 NFT 挖矿、分红、质押奖励、线性释放、空投及其他领取权益。不会主动扩大为扫描用户所有钱包资产。

## 用户看到什么

- 待领清单：项目、链、钱包、权益类型、NFT/仓位标识、领取合约、领取条件、截止时间、下次检查时间、最近独立核验结果。
- 状态区分：已核实可领、等待下一期、资格不符、项目已结束、资料未知/需刷新，以及有交易待核对。资料过期不会继续当作可领；过截止时间不主动提供领取建议。
- 领取流水：每次请求/交易的状态、交易哈希、观察时间、实际收到的资产与原始数量、核验证据。确认次数和最近领取观察时间随清单展示。失败和未知也记录，未知不算成功。
- 待核查项目：铸造独立确认后，暂时没识别出后续权益的项目也留一条记录；宿主尚未接入或查询失败时不静默丢弃。

周期权益领取后继续跟踪，下一期不是“已完成”而消失。一次性权益独立确认领取后标记 completed，保留全部历史。重新发现同一权益不会清空历史。不同钱包、NFT/仓位、权益类型分开记账；同交易重复回查不会重复计次数。合约/身份变更需要独立核实登记，不猜测合并。

## 运行入口

BotRuntime 提供：

- `track_entitlement(value)`：记录历史参与或主动添加的权益，调用可信宿主 review_entitlement。
- `pending_claims()`：本地汇总清单、待核查项目、未终局领取和历史，不调用模型。
- `refresh_claims()`：补查历史已终局铸造项目，只对到期权益读取最新状态；回传 check_errors。标准查询直接工具执行；NeedsAgent 才通过现有 followup.plan 委派并独立验收。
- `record_claim(entitlement_id, attempt_id, evidence)`：宿主 verify_claim_event 独立核验后记账，也可用于用户在其他入口完成的领取。
- `claim_history(entitlement_id=None)`：读取领取流水，不把待确认交易计为成功。

execute/reconcile 中的已终局成功铸造会调用 entitlements_for_order；发现成功只做一次，失败保留待核查记录并至少等待300秒再试。现有后续任务返回经核实的 claim_event 时自动写领取账本，然后才标任务完成。refresh_claims 也可随独立链查询补录外部领取。

## 宿主数据契约

权益字段：project_id、chain_id、wallet、contract、position_id（例如 NFT token ID 或仓位稳定标识）、benefit_id（同仓位的奖励种类稳定ID）、kind（nft_mining/dividend/staking_reward/vesting/airdrop/other）、recurring 布尔、conditions。可选 deadline/next_check_at 为时间戳；check_interval_seconds 默认3600，最少60。未知截止时间写 null，不编造日期。

review_entitlement 返回 verified/reviewer/evidence_ref。entitlements_for_order 返回这些字段及 order_id、entitlements 数组；空数组只能表示已经核实没有需要跟踪的权益，不能表示接口没实现。没有接口则保留待核查项目。

inspect_entitlement 返回 verified/evidence_ref、entitlement_id/chain_id/wallet/contract、observed_at/valid_until，以及 state=claimable/waiting/ended/ineligible/unknown，可附 amount_raw、next_check_at、deadline、period_id、claim_events。经核验的新 deadline 更新当前期截止时间，周期切换不会一直沿用上一期已过期日期。每个 claim_event 需要稳定 attempt_id 和完整领取事实。NeedsAgent 的结果通过 verify_entitlement_evidence 验收，远端自报 verified 不是独立证据。

领取事实包含同一权益身份、verified/evidence_ref、observed_at、status=submitted/pending/unknown/confirmed/reverted、可选 tx_hash。confirmed/reverted 必须有真实格式交易哈希与 finalized=True；confirmed 还需 received_assets，每项含 asset（native 或合约地址）、recipient、amount_raw，接收人须为该权益钱包。确认时间如需区块时间由宿主额外提供，observed_at 仅代表独立核验观察时间。最终事实冲突须显式复核，不覆盖既有终局。

现有 verify_followup 的 done 结果可附 claim_event（另含 entitlement_id/attempt_id）；对应权益必须先登记，项目/钱包/链须与订单一致。流水表记录最新领取状态，事件表保留追加的状态证据。

## 运行边界

这是只读跟踪与记账功能，不提供跳过原钱包审核/授权的领取发送接口。领取交易应另行准备、审计目标、确认并执行，保留原钱包提示与清理流程；记账不授予交易权限，未知状态不重发。

当前核心没有后台定时服务。宿主应在用户查看待领项目、检查持仓收益或已启用的 routine 中调用 refresh_claims，再展示可领、临近截止与待核查项目；是否推送、频率及免打扰由安装者配置。未接入调度器前不能声称已经订阅自动提醒。

权益/流水/项目扫描记录保存在安装者私有 SQLite，随私有状态备份恢复；公开发行只带空表结构和代码，不带任何人的参与或收益记录。

## 接入修订：刷新、复核及能力检查

- `claims_readiness()` 列出权益发现、登记、直接刷新、Agent证据验收、外部领取记账、终局纠正需要的回调及缺项。它只检查回调存在，不认证实现，verified_live 始终为 false；不会启动调度器。
- 刷新时 observation.valid_until 过期即视为到期，即便 next_check_at 更晚。查询失败仍明确返回 check_errors，不把错误当成可领。
- 同笔领取先归一化 wallet/contract/recipient/代币地址及资产列表顺序，再做幂等比较；旧记录同样在比较时归一，旧证据原文保留。
- `claim_events(attempt_id=None)` 返回追加事件轨迹。`claim_history` 是每次尝试的最新状态，并提供 record_digest。
- 纠正终局使用 `review_claim(entitlement_id, attempt_id, evidence)`。宿主 verify_claim_event 重新核实事实，review_claim_correction(entry,current,fact) 返回 approved/reviewer/evidence_ref/reason/expected_digest；expected_digest 必须匹配 current.record_digest。只允许同笔交易终局的显式纠正，新事件包含旧事实和审核理由；原事件不删除。并发改动导致摘要不符则重新复核。普通回查不能借此改写终局。
- conditions、position_id、benefit_id、project_id 都是非空字符串，NFT数字ID须由宿主转换为稳定字符串。deadline 省略表示沿用，显式 null 表示清除；新周期应明确给时间或 null。
- period_id 是展示用标签，不能按字典序推断新旧。宿主可提供经独立核验的非负整数 period_sequence；使用后不得缺失或倒退，进入新序号必须显式提供 deadline/null。不提供序号时只使用观察时间排序，跨期顺序由宿主保证。
- 每个 claim_events/claim_event 各自需要 verified/evidence_ref、身份字段、observed_at、稳定attempt_id；最外层 verified 不替代逐条核验。畸形后续事件保留 pending/last_error，可再次检查，不让整个对账崩溃。
