# 安装者个人配置

钱包账号、默认账号、推荐地址、服务接口和凭证均由安装者自行配置。初始accounts/providers/routes为空，default_account/referrer_address为null；不从作者环境或历史案例继承。不自动选择第一个钱包，不填作者或零地址作为推荐人。

## 额度和授权

limits.mode=unlimited，金额/每日金额/单笔份数上限均为null，仅表示模板不加额度上限。钱包、平台、合约及余额限制仍生效。未指定数量且未配置自己的数量策略时，不继承作者个人“默认打满”规则。

execution.authorization_mode=unconfigured，standing_authorizations和scheduled_transactions为空。无上限不等于交易授权；只读浏览可先用，执行需要真实宿主、原钱包提示/确认及独立核验。定时发现/交易不随安装自动开启。

## Agent 路由

routes具体角色覆盖`*`，null指定原版。新项目介绍与参与教程统一配置project.brief能力；两者来自同一数据包。providers可是一家或多家任意服务，不绑定DS。能力需独立验收且有效，不能只凭连通性声明优先。

第三方优先用于需要Agent分析的任务。已审模板填参、标准链上查询和状态比较直接用工具；明确语义缺口才委派。原版兜底由真实宿主实现，不是内置免费服务。凭证只放安装者安全存储，研究任务不接收钱包会话/私钥。

agents.fallback_policy默认automatic，可选wait/notify；fallback_wait_seconds默认60。等待只记录截止时间，不启动后台循环。已提交写任务不回退重发。

## 研究预算与缓存

research_budget.max_calls_per_day / max_cost_units_per_day / currency默认null，与铸造额度分开。设置费用上限后，需要可信报价上限；未知费用不调用。超时和实际回退分别记账，不能承诺远端退款。

research_cache.ttl_seconds默认300，只复用经宿主核验的project.brief；旧只读兼容接口缓存不作为产品默认路径。RPC价格、资格、余额、回执、授权和交易计划不放描述缓存。没有真实reviewer就不能假称简报已经复核。

## 授权清理与恢复

本次流程新增或用过的全部授权终局后清理，包括旧授权；无USDT等通用豁免，不扫任务外历史授权。撤权保留原钱包提示/确认。未知类型、签名失效不明、等待确认或失败都明确未完成。

同钱包串行至独立终局与清理完成，多钱包独立。clean流程封存，不触碰后来新任务的授权。后续领取需要新授权；not_before/next_check_at只决定下次reconcile何时可以检查，不自动创建定时任务。

状态及案例保存到源码之外，备份同样私有。升级到当前接口后，旧待发送计划缺少plan_review或新鲜快照字段时必须重新准备；已经派出的旧任务不改ID/执行者，不因升级而重交。真实宿主接线与边界见接入协议。

## 配置执行责任（独立审计修订）

`limits` 和 `referrer_address` 是宿主策略输入，不是运行库自带的日额度账本。填写非空金额/日累计/份数限制、非 unlimited 模式或推荐地址时，启动必须通过 `host.validate_personal_config(config)` 验收，否则拒绝启动。宿主只有实际实现并测试对应限额、日累计及 ABI 参数绑定后才能返回 True；发送前仍须通过 `current_checks.policy_valid` 与 `authorize` 校验。默认 unlimited/null 不变，不导入作者的交易偏好。

`wallet.accounts` 非空时为 EVM 地址字符串列表，select 会检查成员；空列表表示由宿主核实账户所有权，不代表任意钱包都已获授权。默认账号选择仍由宿主负责。

清理是固定策略，不提供 enabled/exemptions 开关；旧配置中 enabled=false 或非空 exemptions 会被拒绝，不静默忽略。默认配置已删除这两个可误解为可编辑策略的键。
