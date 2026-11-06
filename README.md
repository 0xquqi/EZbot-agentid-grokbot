# Agent ID Grok Bot 模板

**作者与维护者：[0xquqi](https://github.com/0xquqi)**  
由 0xquqi 设计与维护的 Agent ID 项目发现、审计复用、钱包流程编排与权益追踪工具。第三方组件的来源及署名见[来源与致谢](ATTRIBUTION.md)。

面向 Binance Agent ID / Agentic Wallet 生态铸造的本地运行库与 Skill 模板。发现和研究使用原版 ezbot-grok-scout，钱包使用未删改的官方币安 Skill。

原则：**一份证据采集一次，一类问题分析一次；复用结构化结果，变化部分才重新处理。**

## 当前流程

发现项目 → 一份项目简报（介绍、参与方式、风险、来源）→ 用户选择活动 → 查同目标已审模板 → 未命中才审计并建立模板 → 工具填参、模拟/预览 → 一次完整计划核验与确认 → 发送前增量检查 → 单一订单执行入口 → 独立核验到账、终局和授权清理 → 按时检查后续任务。

相似代码复用的是审计检查方法，新地址不继承旧合约的通过结论。具体合约撤回与方法停用独立。多钱包独立建单，同钱包串行；结果未知不重发。

第三方优先用于需要 Agent 分析的任务。已审模板填参、标准 RPC/钱包查询与状态比较直接用工具；遇到明确的解释缺口才委派。未配置或不可用时由宿主原版执行器兜底，不自带免费模型服务。

## 文档入口

- [Bot 对话内使用说明](BOT_USER_GUIDE.md)
- [作者、组件来源与致谢](ATTRIBUTION.md)
- [完整用户流程与职责划分](USER_FLOW.md)
- [Agent 角色与优先级](AGENT_ROUTING.md)
- [个人配置](PERSONAL_CONFIG.md)
- [待领权益与每次领取流水](CLAIMS.md)
- [外层工作规则](PROMPTS.md)与[编排 Skill](skills/agentid-orchestrator/SKILL.md)
- [宿主接口、订单与恢复契约](skills/agentid-orchestrator/references/integration.md)
- [方法学习、多钱包和恢复边界](LEARNING_AND_RECOVERY.md)
- [原版钱包 Skill（官方固定版本）](https://github.com/binance/binance-skills-hub/tree/96563daf34fa5a87af5d76c2f1107d697ae7ce07/skills/binance-web3/binance-agentic-wallet)
- [发行隔离规则](RELEASE_POLICY.md)

## 运行入口

宿主通过 `from runtime import BotRuntime` 创建实例，提供源码之外的私有状态目录、可信安装者身份、个人配置和真实工具适配器。BotRuntime 统一连接路由、案例/方法库、订单、钱包队列与清理模块。启动不会登录、研究、交易或开启定时任务。

交易只走 BotRuntime.execute → MintCoordinator；旧 Workflow.execute 已移除。Workflow 仅负责研究/审计和学习，不再提供绕过订单的执行路径。

Python 3.11+，macOS/Linux，运行库仅用标准库。保留 tests 的本地开发目录可运行：

```sh
python3 -W error::ResourceWarning -m unittest discover -s tests -q
PYTHONPATH=. python3 tests/replay_learning.py
PYTHONPATH=. python3 tests/replay_efficiency.py
```

无案例发行预览不包含测试夹具；这些命令只适用于本地开发目录。

## 交付状态

当前是本地核心与接入契约，尚未完成真实 Grok/Agent/钱包端到端联调，没有公共安装链接。真实来源采集、ABI 编码/模拟、钱包预览、原钱包确认及独立链上核验仍由宿主提供。不能用空回调或模型自称 verified 代替。

个人钱包、接口、密钥由安装者配置；默认模板金额额度无上限，不代表自动交易授权。未指定铸造份数时，默认在钱包剩余额度与单笔上限内打满，仍须确认后才执行；执行器保持可插拔。正式发行空案例库，排除私有历史、派生学习数据、数据库和测试夹具。公开源代码不代表已经部署到 Grok Bot。

研究依赖固定于 config/research-method.lock.json，外部安装原版 [EZbot-grok-project-skill](https://github.com/0xquqi/EZbot-grok-project-skill)。原 Skill 保持只读边界；外层工具构建交易，原版钱包负责预览与执行。官方钱包 20 个文件以独立锁文件校验；本公开仓库不打包该第三方目录，请按下方说明从官方固定版本安装，保留全部原文。

## 来源与署名

本项目的原创运行库、流程编排、审计方法复用、权益记录与配套文档由 **0xquqi** 设计与维护。Copyright (c) 2026 0xquqi。本署名不覆盖第三方组件，也不替代开源许可证。

- 项目研究与审计方法：来自 0xquqi 的 [EZbot-grok-project-skill](https://github.com/0xquqi/EZbot-grok-project-skill)，作为外部依赖安装。
- 钱包 Skill：来自 Binance 的 [binance-skills-hub](https://github.com/binance/binance-skills-hub)，保留上游原文、安全提示与原有署名。
- Grok、Binance 等名称用于说明兼容对象及组件来源，不表示本项目由其官方开发或获得背书。

各依赖的固定版本、路径和许可边界详见 [ATTRIBUTION.md](ATTRIBUTION.md)。

## 获取与安装依赖

项目仓库：https://github.com/0xquqi/EZbot-agentid-grokbot

1. 克隆本仓库，使用 Python 3.11 或更高版本。
2. 从 [Binance 官方固定版本](https://github.com/binance/binance-skills-hub/tree/96563daf34fa5a87af5d76c2f1107d697ae7ce07/skills/binance-web3/binance-agentic-wallet) 获取完整目录，将其放到 `skills/binance-agentic-wallet/`。不要删减安全提示或修改文件；遵循上游适用条款。固定版本与 20 个文件摘要见 `config/wallet-upstream.lock.json`。
3. 从 [研究 Skill 固定版本](https://github.com/0xquqi/EZbot-grok-project-skill/tree/fe21572daaf6c31cc932c63762233a9d2af8e406/skills/ezbot-grok-scout) 安装原版研究 Skill，版本见 `config/research-method.lock.json`。
4. 按宿主接入协议实现真实研究、钱包、链上核验及安全配置接口；个人配置与状态保存到源码目录外。单纯克隆源码不会自动连接钱包或开启交易。
5. 将 `PROMPTS.md` 和 `BOT_USER_GUIDE.md` 交给宿主 Agent 读取；需要主动提醒时另行配置调度和通知渠道。

本项目原创部分使用 [MIT 许可证](LICENSE)。第三方组件遵循其各自条款，本项目的 MIT 许可不覆盖第三方组件。
