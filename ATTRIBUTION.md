# 作者、来源与致谢

## 本项目

- 作者与维护者：**0xquqi**
- GitHub 主页：https://github.com/0xquqi
- 原创部分：本项目运行库、流程编排、审计方法复用、多钱包任务管理、权益记录及配套文档。
- 原创部分版权署名：Copyright (c) 2026 0xquqi。

本文件说明作者和组件来源，不代替许可证，不对第三方作品重新授予许可。项目仓库：https://github.com/0xquqi/EZbot-agentid-grokbot 。原创部分适用根目录 MIT 许可证；不覆盖第三方组件。

## 项目研究与审计方法

- 作者：0xquqi。
- 上游仓库：https://github.com/0xquqi/EZbot-grok-project-skill
- Skill：`ezbot-grok-scout`。
- 固定版本：`fe21572daaf6c31cc932c63762233a9d2af8e406`。
- 上游路径：`skills/ezbot-grok-scout`。
- 交付方式：外部依赖；本项目未将其改写成自己的审计 Skill。
- 版本与文件摘要：`config/research-method.lock.json`。

使用该依赖时遵循上游对应版本的许可及署名要求。

## 币安钱包 Skill

- 来源：Binance 官方 `binance-skills-hub`。
- 上游仓库：https://github.com/binance/binance-skills-hub
- 固定版本：`96563daf34fa5a87af5d76c2f1107d697ae7ce07`。
- 上游路径：`skills/binance-web3/binance-agentic-wallet`。
- 安装后路径：`skills/binance-agentic-wallet`；公开仓库不打包此目录，由使用者从官方固定版本安装。
- Skill 版本：`1.15.0`；对应 CLI 要求见原版 Skill 与锁文件。
- 安装时须完整保留上游 20 个文件，包括安全提示、确认要求及原有署名。
- 文件摘要：`config/wallet-upstream.lock.json`。

该目录属于第三方组件，不归入本项目原创部分。公开可读不等于已确认再分发许可；发布前须核实对应版本适用的许可或授权，不把本项目的版权声明套用于该目录。

## GitHub About 简介文案

仓库 About 简介：

> 由 0xquqi 开发维护的 Agent ID 生态工具：项目发现、审计方法复用、钱包流程编排与权益追踪；研究源自 EZbot-grok-project-skill，钱包采用 Binance 原版 Skill。

## 名称与关联说明

Grok、Binance、Agent ID 及其他产品名称归其相应权利人所有，用于描述兼容对象和来源。本项目由 0xquqi 独立维护，不表示上述产品方的官方发行、审核或背书。
