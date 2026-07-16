# 生产级 AI 工程开发 (Production AI Engineering)

### 学习如何设计、构建、部署和维护生产级 AI 系统。

---

## 🌟 项目愿景

本项目**不是**另一个 AI 开发框架、玩具聊天机器人或基础的 RAG 模板。

**它的核心目标是教授生产级 AI 系统工程学。**

市面上有无数的教程展示如何在 100 行代码内构建一个 AI Agent。但是极少有教程能够深入解释如何将这样一个 Agent 演进为包含重试机制、状态检查点、任务队列、后台工作协程、状态持久化、生产级部署、健康监控、质量评测和长周期工作流的生产级系统。

**本项目旨在弥合这一工程鸿沟。**

每一章节都以一个简易的教学示例开始，并逐步演进为可部署的生产级实现。最后的综合系统章节将结合前期学到的所有模式，展示受实际工业界经验启发的完整 AI 平台设计。

---

## 🎯 目标受众
* **后端工程师**：希望为 AI 系统搭建强壮、高可扩展的基础设施。
* **AI 与 LLM 工程师**：希望从玩具原型和简单的 prompt 拼接，进阶到构建高弹性的生产级 Agent 系统。
* **计算机专业学生**：希望理解在大模型时代，现代软件工程是如何落地和演化。

---

## 🧠 核心理念：重工程，轻框架

我们把核心精力放在**底层工程原理**上，而非绑定到特定的第三方框架（如 LangChain 或 LangGraph）。无论你选择哪种编排框架或 LLM API，这里讲授的工程设计和架构思想都同样适用。

每一章节都聚焦于回答一个工程关键问题：
> *“在实际生产环境中，这套机制究竟应该如何构建，并如何进行高并发、高可用运行？”*

* **可读性高于简短性**：我们绝不为了追求代码行数的缩减而牺牲可读性。每一行代码都以生产级标准编写（包含强类型约束、清晰的代码架构和关注点分离原则）。
* **原创与自治**：无复制的提示词、无闭源商业逻辑、不绑定任何特定开发框架。

---

## 🗺️ 仓库目录结构与渐进式路径

```
production-ai-engineering/
├── part-1-foundations/             # 第一部分：核心基础与原语构建块 (f01–f15)
│   ├── 01-state/                   # 线程安全、写时复制、版本化状态存储
│   ├── 02-agent-loop/              # 稳定、模型无关的自律执行控制循环
│   └── ...                         # 结构化输出、持久化、事件总线、定时调度
│
├── part-2-production-patterns/     # 第二部分：生产级工程挑战与设计模式 (p16–p30)
│   ├── 16-retry-patterns/          # 接口抖动退避重试与抖动策略
│   ├── 17-background-workers/      # 异步 offloaded 工作协程与池化管理
│   └── ...                         # Redis 状态快照、Postgres 事务账本、消息队列
│
├── part-3-complete-systems/        # 第三部分：综合集成系统参考实现 (c01–c04)
│   ├── healthcare-platform/        # 旗舰医疗综合调度平台（遵从 HIPAA 规范设计）
│   └── ...                         # 智能工单支持系统、文献检索研究员、沙箱编程 Agent
│
├── part-4-infrastructure/          # 第四部分：本地容器基础设施编排 (i01)
│   └── local-compose-deployment/   # Docker Compose 完整多容器集成配置规范
│
├── common/                         # 共享通用工具函数库
└── web/                            # 基于 Next.js 开发的渐进式交互式大盘与可视化平台
```

第一部分与第二部分的每一课都严格包含：
1. **README.md**：概念详解、 naive 实现对比、生产级演进思路及设计权衡。
2. **architecture.md**：基于 ASCII 绘制的流程控制拓扑和组件架构边界图。
3. **example.py**：完全强类型、符合本番运行标准的 Python 源码实现。
4. **tests.py**：自动化单元/集成测试，校验高并发和临界边界下的正确性。

---

## 📚 课程大纲

> [!NOTE]
> **路线图进度**：目前仅 **Lesson 01 (状态与上下文表示)** 已提供了完整的生产级代码。其他课时目前包含概念大纲、目录结构和代码框架占位符，我们将持续推进各个章节的内容填充。
>
> **多语言声明**：本仓库非英文版本的文档（中文和日文）均为大语言模型/机器翻译生成，如有翻译不准之处以英文原版为准。

### 第一部分 — 核心基础 (Foundations)
目标：掌握 AI Agent 系统的底层思考逻辑和原语。章节代码均保持在 200 行以内，极具教学启发性。

| 课时 | 章节名称 | 核心工程关注点 / 原始组件 | 状态 |
|---|---|---|---|
| 01 | [状态与上下文表示](part-1-foundations/01-state) | 线程安全锁机制、写时复制、可追溯的版本回滚审计日志 | ✅ 已完成 |
| 02 | [Agent 执行循环](part-1-foundations/02-agent-loop) | 稳定、模型无关的自主调用与解释循环控制 | ⏳ 占位大纲 |
| 03 | [提示词与结构化输出](part-1-foundations/03-prompting-structured-output) | Schema 约束、Pydantic 数据格式自动重试修正 | ⏳ 占位大纲 |
| 04 | [工具调用与派发](part-1-foundations/04-tool-calling) | 动态工具签名验证、集中式安全工具派发中心 | ⏳ 占位大纲 |
| 05 | [内存与持久上下文](part-1-foundations/05-memory) | 短期情境总结压缩算法、基于向量库的情节检索 | ⏳ 占位大纲 |
| 06 | [规划与任务拆解](part-1-foundations/06-planning-task-decomposition) | DAG 有向无环图任务编排、依赖项执行流追踪 | ⏳ 占位大纲 |
| 07 | [多 Agent 协作机制](part-1-foundations/07-multi-agent-coordination) | 分布式 Agent 协同机制、路由器及消息路由协议 | ⏳ 占位大纲 |
| 08 | [人工审批拦截 (HITL)](part-1-foundations/08-human-approval) | 敏感操作中断栅栏、状态暂停与唤醒交互逻辑 | ⏳ 占位大纲 |
| 09 | [评测与自我反思](part-1-foundations/09-evaluation-self-reflection) | 基于大模型判决器的自动化输出安全性校验评估 | ⏳ 占位大纲 |
| 10 | [任务调度与长周期工作流](part-1-foundations/10-scheduling-long-running-tasks) | 持久化时钟调度器、长周期定时监测与唤醒机制 | ⏳ 占位大纲 |
| 11 | [异步并发执行](part-1-foundations/11-async-execution) | 基于 `asyncio` 的非阻塞并发任务获取和并发调用 | ⏳ 占位大纲 |
| 12 | [事件驱动架构 Agent](part-1-foundations/12-event-driven-agents) | 响应式消息总线设计、Pub/Sub 事件流追踪订阅 | ⏳ 占位大纲 |
| 13 | [确定性状态机守卫](part-1-foundations/13-state-machines) | 有限状态机（FSM）拦截防护，避免 Agent 死循环失控 | ⏳ 占位大纲 |
| 14 | [弹性异常捕获处理](part-1-foundations/14-error-handling) | 运行期异常分类，提供可自愈的容灾应对机制 | ⏳ 占位大纲 |
| 15 | [持久化存储接口](part-1-foundations/15-persistence) | 序列化数据接口设计，满足 Agent 本地冷备要求 | ⏳ 占位大纲 |

---

### 第二部分 — 生产级模式 (Production Patterns)
目标：深入生产一线，攻克 Agent 在网络、存储、资源及高并发环境下的弹性运行痛点。

| 课时 | 章节名称 | 核心工程关注点 / 原始组件 | 状态 |
|---|---|---|---|
| 16 | [异常重试退避模式](part-2-production-patterns/16-retry-patterns) | 网络接口错误分类、指数退避策略、避坑重试抖动 | ⏳ 占位大纲 |
| 17 | [后台工作协程池](part-2-production-patterns/17-background-workers) | 密集计算或慢速网络操作离线处理、线程池设计 | ⏳ 占位大纲 |
| 18 | [Redis 运行状态检查点](part-2-production-patterns/18-redis-checkpointing) | 定期归档 Agent 中断快照至 Redis 确保热恢复能力 | ⏳ 占位大纲 |
| 19 | [PostgreSQL 事务性存储](part-2-production-patterns/19-postgresql-state-store) | ACID 特性的关系型数据库审计，构筑严格的交易屏障 | ⏳ 占位大纲 |
| 20 | [解耦任务消息队列](part-2-production-patterns/20-queues) | 削峰填谷、高吞吐后台消费重入、抗高吞吐负载 | ⏳ 占位大纲 |
| 21 | [Server-Sent Events 串流](part-2-production-patterns/21-streaming) | SSE 单向持续串流机制，提升交互首字响应感知 | ⏳ 占位大纲 |
| 22 | [OpenTelemetry 全链路追踪](part-2-production-patterns/22-observability) | OTel Trace 编排记录，剖析每级调用时长和资费开销 | ⏳ 占位大纲 |
| 23 | [多阶段 Docker 容器化](part-2-production-patterns/23-docker) | 构筑多阶段精简镜像，消除测试开发环境差异 | ⏳ 占位大纲 |
| 24 | [微服务无状态部署](part-2-production-patterns/24-deployment) | 计算无状态化改造，前后端完全解耦与扩容设计 | ⏳ 占位大纲 |
| 25 | [强类型环境变量配置](part-2-production-patterns/25-configuration) | 动态环境变量注入，Pydantic Settings 类型安全加载 | ⏳ 占位大纲 |
| 26 | [度量指标与服务监控](part-2-production-patterns/26-monitoring) | 注册 Prometheus 度量，跟踪报错率、P99 时延指标 | ⏳ 占位大纲 |
| 27 | [自动化评测回归流水线](part-2-production-patterns/27-evaluation-pipelines) | 将提示词回归测试纳入 CI/CD，保障敏捷开发发布 | ⏳ 占位大纲 |
| 28 | [Session 凭证与费用追踪](part-2-production-patterns/28-cost-tracking) | 实时的调用凭证拦截鉴权、会话大模型资费超预算熔断 | ⏳ 占位大纲 |
| 29 | [沙箱隔离与执行安全](part-2-production-patterns/29-security) | 防范恶意提示词注入、利用独立子进程/安全容器沙箱执行 | ⏳ 占位大纲 |
| 30 | [高并发水平扩展配置](part-2-production-patterns/30-scaling) | 面对海量并发会话，后端负载均衡与长连接并发架构 | ⏳ 占位大纲 |

---

### 第三部分 — 完整系统 (Complete Systems)
目标：参考工业界真实的架构细节，解构大型、复杂的集成应用系统。

* **[医疗综合 Agent 平台](part-3-complete-systems/healthcare-platform) (旗舰级 ⭐)**：遵循 HIPAA 安全隐私标准，融合患者登记队列、Redis 周期检查点、多级人工审批通道与 PostgreSQL 全事务日志审计。
* **[智能客服支持系统](part-3-complete-systems/customer-support)**：支持多渠道集成、基于历史对话的状态自愈唤醒、智能客服-人工客服平滑升级流转。
* **[学术与文献自主研究员](part-3-complete-systems/research-assistant)**: 自主规划检索策略，并发抓取网页与 PDF，执行事实一致性交叉验证与论文综述合成。
* **[沙箱式自主编程 Agent](part-3-complete-systems/coding-agent)**：运行于隔离的 Git 虚拟工作树沙箱，自主分析代码报错、运行测试用例并执行自修复。

---

### 第四部分 — 基础设施 (Infrastructure)
* **[多容器 Docker Compose 部署](part-4-infrastructure/local-compose-deployment)**：一键启动包含 PostgreSQL、Redis、Celery/AMQP 队列、FastAPI 接口层和 Prometheus 大盘度量的本地集成开发环境。

---

## ⚡ 交互式可视化面板 UI

我们配套提供了一个精心设计、动画丰富的 Next.js 开发人员面板，用于动态可视化 Agent 的执行流图、一键查看代码变动差异、追溯每轮操作轨迹。

### 本地启动可视化面板

1. **安装 Python 基础依赖库**：
   ```bash
   pip install -r requirements.txt
   ```

2. **初始化并运行 Next.js 前端应用**：
   ```bash
   cd web
   npm install
   npm run dev
   ```
   启动后，在浏览器访问 [http://localhost:3000](http://localhost:3000) 即可开始沉浸式交互学习。

3. **重新提取与构建课时数据**：
   若你本地修改了任何章节代码或文档 README，可运行以下命令重新生成静态索引数据：
   ```bash
   npm run extract
   ```

---

## 🚀 未来路线与交互展望

本项目设计了两种灵活的运行姿态：

### 1. 静态阅读模式 (Learning Mode)
* 完全托管运行在 **GitHub Pages** 上，零门槛阅读。
* 不需要搭建任何复杂的本地后端和 Docker 容器服务。
* 在浏览器中直接阅读核心架构解析、查看完整的 Python 源码实现、通过预渲染的轨迹掌握概念和控制流。

### 2. 本地交互模式 (Interactive Mode)
在本地进入具体系统目录（如 `part-3-complete-systems/healthcare-platform`），通过一行指令一键拉起多容器基础设施：
```bash
docker compose up
```
前端可视化面板能自动定时探测本地服务健康状态（通过 `/health` 端点），识别到后端成功唤醒后，会将原本静态的教程页面瞬间升级为**动态交互式沙箱**：

* **自律执行可视化**：输入自定义的提示词，看着 Agent 在浏览器上实时流转、验证并调用本地工具。
* **队列与并发监控**：实时观察后台 Worker 状态，追溯 AMQP 消息队列堆积和消费者分发情况。
* **重试与抖动跟踪**：直观目睹网络请求失败时系统如何捕获异常、分类判定、并在可视化倒计时中退避重试。
* **OpenTelemetry 全链路追踪**：剖析每级 prompt 损耗的 token 费用、数据读写和网络消耗延时。

#### 开启本地交互
在阅读各个课时时，页面会提供显眼的 **🚀 Launch Live Demo** 按钮。若检测到本地后端离线，它会指引：
> *“请在 `part-3-complete-systems/healthcare-platform/` 目录运行 `docker compose up` 并重新载入本页。”*

一旦完成握手，静态课时页将自动转变为一个可实时操作、监控和调试的生产级试验场。

---

## 🤝 致谢

本项目的交互式文档网站与渐进式教学思路受到了优秀的 [Learn Claude Code](https://github.com/shareAI-lab/learn-claude-code) 开源项目的启发。他们出色的渐进式文档风格极大地影响了本仓库目录结构的构建。在此，我们对 Learn Claude Code 项目团队表示由衷的感谢。本项目中所有的课程大纲、Python 代码实现、架构拓扑和生产模式解读均由本项目独立设计并完全原创，专注于通用生产级 AI 工程学。
