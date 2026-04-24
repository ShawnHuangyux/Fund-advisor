# 基金投资决策助手（个人自用）

> ⚠️ **免责声明**：本系统仅为信息聚合与辅助分析工具，不构成投资建议；所有投资决策与风险由使用者自行承担。

一个面向单用户的中国公募基金投资决策助手。数值计算由确定性 Python 规则引擎完成，LLM 只做定性综合与语言输出；每条建议都可追溯到具体规则或数据依据。

---

## 当前状态：阶段 1（MVP）

已实现：
- ✅ Pydantic 数据模型（Portfolio / Holding / Settings / DiagnosisReport 骨架）
- ✅ `portfolio.yaml` 读写（写前备份 + 原子替换）
- ✅ **集中度诊断** — 基于 `fund_type` + 关键词识别风险等级，对比 15/30/40% 上限
- ✅ **资金效率诊断** — 本金利用率、应急金充足度、定投预算建议
- ✅ Streamlit UI（组合总览 / 持仓管理 / 诊断报告 / 成本与用量 4 个 tab）
- ✅ `pytest` 覆盖两个诊断模块 + 读写层

尚未启用（留给后续阶段）：
- akshare 实时净值 / 指数估值分位（阶段 2）
- 仓位诊断、成本诊断（含 C 类赎回费阶梯提醒）、估值诊断（阶段 2）
- 风险诊断与 2022/2024 历史压力测试（阶段 3）
- DeepSeek / 智谱 LLM 综合意见 + action_items 生成（阶段 4）

### 阶段 5：每日定时诊断（已接入）

```bash
# 常驻进程：每个交易日 16:30 (Asia/Shanghai) 自动跑一次，落盘到 reports/YYYY-MM-DD.json
uv run fund-advisor-scheduler

# 启动时立刻跑一次再进调度（用于开发调试）
uv run fund-advisor-scheduler --run-now

# 只跑一次就退出（用于 launchd / 系统 cron）
uv run fund-advisor-scheduler --run-once
```

- 调度参数在 `config/settings.yaml` 的 `scheduler` 小节可调（hour / minute / timezone / reports_dir）。
- 日志写入 `logs/scheduler.log`（每日轮转、保留 14 天）。
- Streamlit UI 侧边栏的"历史报告"下拉可回看最近 30 份落盘诊断。
- 任务默认走 `llm.mode`（通常 deep）+ 阶段 4 的月度预算门槛——超 ¥100 自动降级为规则兜底。

---

## 5 分钟快速启动

### 1. 环境准备

```bash
# 安装 uv（如未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# 同步依赖
cd fund_advisor
uv sync
```

### 2. 配置

```bash
cp .env.example .env                  # 阶段 4 之前可以不填 API Key
# 编辑 config/portfolio.yaml 录入你的持仓 / 现金 / 本金 / 应急金
```

### 3. 启动 UI

```bash
uv run streamlit run src/fund_advisor/ui/app.py
```

默认地址 `http://localhost:8501`。

### 4. 跑测试

```bash
uv run pytest
```

---

## 目录结构

```
fund_advisor/
├── pyproject.toml
├── .env.example
├── config/
│   ├── portfolio.yaml         # 持仓主配置（阶段 1 唯一数据源）
│   └── settings.yaml          # 阈值 / 关键词 / 压力测试窗口 / LLM 参数
├── src/fund_advisor/
│   ├── models/                # Pydantic 数据模型
│   ├── data/                  # portfolio_loader（阶段 1 用）
│   ├── diagnostics/           # concentration / capital（阶段 1）
│   ├── advisor/               # run_stage1_diagnosis orchestrator
│   ├── ui/app.py              # Streamlit 入口
│   ├── llm/ advisor/          # 留给阶段 4
│   └── scheduler/             # 留给阶段 5
└── tests/
    ├── test_portfolio_loader.py
    └── diagnostics/
        ├── test_concentration.py
        └── test_capital.py
```

---

## 阶段 1 诊断规则说明

### 集中度诊断 (`diagnostics/concentration.py`)

每只基金占总资产比例 vs 其风险类别上限：

| 风险类别 | 命中条件 | 上限 |
|---|---|---|
| `high_volatility` | 基金名命中关键词（北证、北交所、科创、行业、主题、半导体、医药、军工、新能源、芯片、AI 等） | **15%** |
| `broad_index` | 基金名命中关键词（沪深300、中证500/800、上证50、创业板、MSCI、全指 等） | 30% |
| `bond` | `fund_type == bond_fund` | 40% |
| `money` | `fund_type == money_fund` | 不限（100%） |
| `unknown` | 未匹配任何关键词的股基/混合基 | **15%（保守）** |

> 关键词优先级：**`high_volatility` 先判，再判 `broad_index`**。"北证50" 同时命中"北证"和"50"，按高波动处理。
>
> 所有关键词与上限都在 `config/settings.yaml`，不硬编码。

超标即触发 `OVER_CONCENTRATED` 信号（WARN）。

### 资金效率诊断 (`diagnostics/capital.py`)

| 指标 | 公式 |
|---|---|
| 本金利用率 | `invested_value / (principal_total - emergency_reserve)` |
| 应急金月数 | `emergency_reserve / monthly_expense_default` |
| 建议月度定投 | `max(0, (investable - invested) / default_dca_months)` |

触发信号：

- `EMERGENCY_RESERVE_LOW`（**HIGH**，应急金 < 3 个月）：**后续阶段会据此阻断所有加仓类 ActionItem**
- `CAPITAL_UNDERUTILIZED`（INFO，利用率 < 50%）：仅提示，阶段 4 会结合估值信号升级

---

## 阶段 1 验收样例

按默认 `config/portfolio.yaml`（017513 + 沪深300 + 纯债 + 货基，现金 5 万、总本金 20 万、应急金 3 万），启动 UI 后：

- 组合总览：总资产 ≈ ¥64,150
- 集中度诊断：所有持仓均在上限内；若把 017513 份额改到 15000（市值 ~18750 → 占比 ~29%），立即触发 `OVER_CONCENTRATED`
- 资金效率：应急金 3.75 个月（充足），利用率 ≈ 8%（触发 `CAPITAL_UNDERUTILIZED` info），建议月度定投 ≈ ¥12,320

---

## 阶段 4 前瞻：DeepSeek API Key 获取

1. 访问 https://platform.deepseek.com
2. 注册账号 → 充值 → 在 API Keys 页面创建 Key
3. 填入 `.env` 的 `DEEPSEEK_API_KEY`

阶段 4 才会真正调用；阶段 1-3 可留空。

---

## 技术栈

- Python 3.11+（项目本地用 3.12 测试）
- 依赖管理：`uv`
- 数据校验：`pydantic` v2
- UI：`streamlit` + `plotly`
- 日志：`loguru`
- 测试：`pytest`

后续阶段将引入：`akshare`（阶段 2）、`pyxirr`（阶段 2 IRR）、`tenacity` + `openai` SDK（阶段 4 LLM）、`apscheduler`（阶段 5）。

---

## 再次声明

本项目为个人自用的辅助分析工具，**不是交易机器人，也不构成任何形式的投资建议**。
