# 基金投资决策助手（个人自用）

> ⚠️ **免责声明**：本项目仅为信息聚合与辅助分析工具，不构成任何形式的投资建议。所有投资决策与风险由使用者自行承担。

一个面向单用户的中国公募基金投资助手。项目的设计原则是：

- **确定性规则负责算数**：仓位、集中度、估值温度、风险压力测试、资金效率等都由 Python 规则引擎计算。
- **LLM 负责综合表达**：模型只做定性归纳、风险提示和行动建议生成，不负责捏造基础数据。
- **数据结构贴近投资者习惯**：把“当前持仓”和“当前定投计划”拆开建模，而不是把历史、计划、快照揉成一个表单。

---

## 当前能力

- 持久化的 `portfolio.yaml` 读写，带备份和原子替换。
- 新版投资组合数据模型：
  - `capital`：当前可用现金、应急储备、月支出、目标投入规模
  - `holdings`：当前持仓快照
  - `dca_plans`：当前定投计划
  - `transactions`：预留的交易流水入口（当前为可选高级模式）
- 持仓管理 UI：
  - 单独维护“当前持仓”
  - 单独维护“当前定投计划”
  - 基金名称 / 类型自动联网补全
  - 基金类型统一中文显示
- 今日建议链路：
  - 联网补全基金基础信息与最新净值
  - 规则诊断
  - 可选 DeepSeek LLM 综合
  - 失败时自动降级为纯规则兜底
- 规则诊断模块：
  - 集中度诊断
  - 资金效率诊断
  - 大类仓位诊断
  - 成本 / C 类赎回费阶梯提醒
  - 指数估值诊断
  - 历史风险 / 压力测试
- 基金明细页：
  - 近 1-5 年净值曲线
  - 对应指数估值分位
  - 顶部“最新净值”优先使用本页曲线最后一个点
- 用量与成本：
  - SQLite 记录 LLM token / 费用
  - 月度警戒线 / 阻断线
- 定时任务：
  - 每个交易日定时生成诊断 JSON 报告
  - UI 可回看历史报告

---

## 快速开始

### 1. 环境准备

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

cd fund_advisor
uv sync
```

### 2. 配置

```bash
cp .env.example .env
```

- 如果只使用规则引擎，`.env` 可以先不填。
- 如果要启用 LLM 综合建议或候选基金分析，需要配置：

```env
DEEPSEEK_API_KEY=your_key_here
```

### 3. 编辑投资组合

当前默认使用 [config/portfolio.yaml](/Users/huangshawn/Downloads/代码/Stock/fund_advisor/config/portfolio.yaml)。

示例结构：

```yaml
capital:
  available_cash: 5000.0
  emergency_reserve: 5000.0
  monthly_expense: null
  target_portfolio_budget: 10000.0

holdings:
  - code: "017512"
    name: 广发北证50成份指数A
    fund_type: equity_fund
    shares: 15073.94
    average_cost: 1.6703
    notes: null

dca_plans: []
transactions: []
```

### 4. 启动 UI

```bash
uv run streamlit run src/fund_advisor/ui/app.py
```

默认地址：`http://localhost:8501`

### 5. 跑测试

```bash
uv run pytest
```

当前测试结果：`80 passed`

---

## 数据模型

### `capital`

描述组合层的资金状态：

- `available_cash`
  当前还能用于申购基金的现金。
- `emergency_reserve`
  明确保留、不参与投资的钱。
- `monthly_expense`
  可选，用于应急金充足度判断；不填时回退到 `settings.yaml` 默认值。
- `target_portfolio_budget`
  可选，表示这套组合最终准备投入到多大规模。

### `holdings`

只描述**当前真实持仓快照**：

- `code`
- `name`
- `fund_type`
- `shares`
- `average_cost`
- `notes`

当前模型故意**不再要求**用户在主界面维护：

- 建仓日
- 持仓策略
- 单只基金目标占比

旧版 `purchase_date / strategy / target_allocation` 仍支持兼容读取，但不再是新模型的核心输入。

### `dca_plans`

单独描述未来持续买入安排：

- `code`
- `name`
- `fund_type`
- `amount_rmb`
- `frequency`
  可选 `daily / weekly / monthly`
- `start_date`
- `enabled`
- `day_of_week`
- `day_of_month`
- `notes`

这让“当前持仓”和“未来计划”在结构上彻底分开。

### `transactions`

预留给未来的高级模式。

目标是支持：

- 精确成本计算
- 真实持有期
- 更准确的收益率 / 赎回费 / IRR

当前版本并不要求你维护交易流水。

---

## UI 页面说明

当前页面包含 6 个 tab：

### 1. 今日建议

- 可选使用 LLM 综合
- 联网补全基金名称 / 类型 / 最新净值
- 输出行动建议、风险提示、反面观点
- 展示规则原始信号与完整 JSON

### 2. 组合总览

- 总资产、持仓市值、现金、应急储备
- 持仓明细
- 当前定投计划概览
- 资产构成饼图

### 3. 基金明细

- 选择持仓基金
- 查看近 `1-5` 年净值曲线
- 查看对应指数的近 3 年估值分位
- 页面顶部“最新净值”来自：
  - 优先：本页刚拉到的净值曲线最后一个点
  - 回退：运行时已补齐的 `latest_nav`

### 4. 持仓管理

- 组合资金状态维护
- 当前持仓维护
- 当前定投计划维护
- 可选“保存并联网补全基金名称/类型”

### 5. 候选基金分析

- 输入一个**尚未持有**的基金代码
- 指定意向金额与方式
- 调用 LLM 生成“要不要买、怎么买”的建议

### 6. 用量与成本

- 展示最近 LLM 调用记录
- 展示月度累计成本
- 展示预算警告 / 阻断状态

---

## 规则诊断说明

### 集中度诊断

文件：`src/fund_advisor/diagnostics/concentration.py`

每只基金按名称关键词和基金类型归入风险类别，然后比较其在总资产中的占比上限。

默认上限：

- 高波动品种：15%
- 宽基指数：30%
- 债基：40%
- 货基：100%
- 未识别：15%

### 资金效率诊断

文件：`src/fund_advisor/diagnostics/capital.py`

如果配置了 `target_portfolio_budget`，则按：

```text
investable = target_portfolio_budget - emergency_reserve
```

计算本金利用率和建议定投预算。

如果未配置 `target_portfolio_budget`，则退化成按“当前持仓 + 当前现金”估算可投资规模。

### 大类仓位诊断

文件：`src/fund_advisor/diagnostics/position.py`

仍使用系统内置的默认大类目标：

- 股基 50%
- 债基 30%
- 货基 20%

当前版本这部分配置不再暴露给用户表单，但诊断仍然启用。

### 成本 / 赎回费诊断

文件：`src/fund_advisor/diagnostics/cost.py`

当前只要有：

- 当前份额
- 平均成本
- 最新净值

就能计算：

- 浮盈亏
- 收益率

而下列功能依赖旧版 `purchase_date` 或未来 `transactions`：

- 持有天数
- 近似年化收益
- C 类赎回费降档提醒

如果没有历史字段，这些结果会自动降级为 `None` / `—`，不会崩溃。

### 估值诊断

文件：`src/fund_advisor/diagnostics/valuation.py`

通过基金名称映射到指数，再用 akshare 拉取对应指数近 3 年估值分位。

适合宽基；
主动基金、主题基金或无法映射指数的产品会降级为 `unavailable`。

### 风险 / 压力测试

文件：`src/fund_advisor/diagnostics/risk.py`

支持：

- 1 年最大回撤
- 年化波动率
- 历史压力场景组合损失

默认场景来自 [config/settings.yaml](/Users/huangshawn/Downloads/代码/Stock/fund_advisor/config/settings.yaml)。

---

## LLM 设计

### 当前接入

- 提供商：DeepSeek
- 模式：
  - `light` -> `deepseek-chat`
  - `deep` -> `deepseek-reasoner`

### 原则

- LLM 不负责基础数值计算
- LLM 必须输出结构化 JSON
- 出错时自动降级为规则兜底

### 月度预算控制

费用通过 SQLite 记录在：

- `data/usage.db`

预算阈值定义在 [config/settings.yaml](/Users/huangshawn/Downloads/代码/Stock/fund_advisor/config/settings.yaml)：

- `monthly_budget_warn`
- `monthly_budget_block`

当月累计成本达到阻断线后，`deep` 模式会被禁用。

---

## 定时任务

启动方式：

```bash
uv run fund-advisor-scheduler
uv run fund-advisor-scheduler --run-now
uv run fund-advisor-scheduler --run-once
```

说明：

- 调度配置来自 `config/settings.yaml -> scheduler`
- 日志写入 `logs/scheduler.log`
- 每日结果落盘到 `reports/YYYY-MM-DD.json`
- UI 侧边栏支持加载最近 30 份历史报告

---

## 架构概览

```text
config/portfolio.yaml
        ↓
Pydantic models
        ↓
data/
  - portfolio_loader
  - akshare_client
  - usage_db
        ↓
diagnostics/
  - concentration
  - capital
  - position
  - cost
  - valuation
  - risk
        ↓
advisor/run_diagnosis
        ↓
llm/synthesizer (optional)
        ↓
ui/app.py  /  scheduler/
```

目录说明：

```text
fund_advisor/
├── config/
│   ├── portfolio.yaml
│   └── settings.yaml
├── src/fund_advisor/
│   ├── advisor/
│   ├── data/
│   ├── diagnostics/
│   ├── llm/
│   ├── models/
│   ├── scheduler/
│   └── ui/
├── tests/
├── reports/
└── data/
```

---

## 兼容性说明

当前 `Portfolio` 模型支持从旧版 schema 自动迁移，例如：

- 顶层 `cash / principal_total / emergency_reserve`
- `holdings[].cost_price`
- `holdings[].purchase_date`
- `holdings[].strategy`

这些字段仍然可以被读取，但保存后会逐步落到新的 `capital / holdings / dca_plans` 结构上。

---

## 已知限制

- 当前不执行真实定投或交易，只做分析与建议。
- `transactions` 还未接入完整收益核算流程。
- 没有交易流水时，部分成本与持有期相关指标只能降级显示。
- 指数估值对宽基更友好，对主动基金支持有限。
- 货币基金最新净值仍采用简化处理（按 `1.0` 处理）。

---

## 技术栈

- Python 3.11+
- Pydantic v2
- Streamlit
- Plotly
- Pandas
- AkShare
- OpenAI-compatible SDK（DeepSeek）
- APScheduler
- Loguru
- Pytest

---

## 再次声明

本项目是个人自用的基金分析助手，**不是交易机器人，也不是投顾产品**。  
请把它当成“辅助你做判断的仪表盘”，而不是“替你做决定的黑盒系统”。
