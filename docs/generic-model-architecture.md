# 通用抽水蓄能财务模型 — 架构蓝图

> 版本: v1.3 | 日期: 2026-06-03
> 目标: 构建参数驱动的通用抽水蓄能电站财务模型引擎，Excel从"输入源"变为"输出格式"
> 当前状态: Phase 1-11 + Phase 10 完成，619测试全绿，3套预设模板，6个分析Tab

---

## 1. 背景与动机

### 1.1 当前模型的时间依赖问题

当前 Excel 模型（`数字化系统财务模型边界【抽水蓄能】v17`）中：

- **建设期起始** (`参数输入表_5_I` = 2023-02-01) 和 **建设期结束** (`参数输入表_7_I` = 2030-07-31) 是硬编码值
- **180+ 下游指标** 直接依赖这两个日期
- **时间序列表** 有 54 列固定时间轴 + 16 个 SUMIF 块
- **投资概算明细** 的 10 个列标题（F-O）对应固定里程碑日期
- **表1-资金筹措** ~20 个子表的列位置与日期语义硬绑定

**核心矛盾**: Excel 中列位置与日期语义硬绑定。F 列 = "2023-03"，M 列 = "2029-08"。改变建设期 → 列数变化 → 所有 SUMIF 范围断裂。

### 1.2 通用性需求

一个通用的抽水蓄能财务模型应支持：

- 任意建设期（5~12 年）
- 任意运营期（20~50 年）
- 任意开工日期
- 灵活的里程碑节点（截流、蓄水、投产、验收）
- 不同的融资结构和还款方式
- 不同的电价机制
- 不同的折旧政策

---

## 2. 技术路线选择

### 2.1 三条路线对比

| 维度 | 路线A: Python原生引擎 | 路线B: Excel模板引擎 | 路线C: 增量改造现有系统 |
|------|---------------------|---------------------|---------------------|
| **核心思路** | 纯Python计算，Excel仅作输出 | Python根据参数生成Excel模板 | 在现有知识图谱系统上增加结构变更能力 |
| **灵活性** | ⭐⭐⭐ 完全灵活 | ⭐⭐ 中等 | ⭐ 低 |
| **复用度** | ⭐ 需重写业务逻辑 | ⭐⭐ 复用部分解析体系 | ⭐⭐⭐ 最大化复用 |
| **复杂度** | 中等 (~2000行新代码) | 高 (Excel公式动态调整) | 极高 (结构+数值双重变更) |
| **维护性** | ⭐⭐⭐ 好 | ⭐⭐ 中 | ⭐ 差 |
| **推荐度** | ✅ **推荐** | 备选 | 不推荐 |

### 2.2 选择: 路线A — Python原生引擎

**决策依据**:
1. 时间轴的动态性是本质需求，Excel的列位置绑定是根本性障碍
2. pandas DataFrame + DatetimeIndex 天然支持任意长度的时间序列
3. `df.groupby(df.index.year).sum()` 替代 SUMIF，无需硬编码范围
4. 参数变更时无需重建图谱，直接重算 DataFrame

---

## 3. 系统架构

### 3.1 分层架构

```
┌─────────────────────────────────────────────────────────┐
│                    第六层: 导出层 (Export)                │
│   ExcelExporter │ Neo4jBridge │ ReportExporter          │
├─────────────────────────────────────────────────────────┤
│                 第五层: 分析工具 (Analysis)               │
│   Sensitivity │ Scenario │ MonteCarlo │ DerivedMetrics  │
├─────────────────────────────────────────────────────────┤
│                 第四层: 模型编排器 (Orchestrator)          │
│   依赖管理 │ 执行顺序 │ 一键生成全表                      │
├─────────────────────────────────────────────────────────┤
│                 第三层: 计算引擎 (Engines)                 │
│   Investment │ Financing │ Depreciation │ Cost │ Revenue │
│   PnL × 2 │ CashFlow × 3 │ BalanceSheet              │
├─────────────────────────────────────────────────────────┤
│                 第二层: 时间轴引擎 (Timeline) ← 核心创新   │
│   TimelineGenerator │ PhaseManager │ MilestoneTracker  │
├─────────────────────────────────────────────────────────┤
│                 第一层: 参数模型 (Params)                  │
│   Construction │ Financing │ Operating │ Cost │ Tax     │
│   Depreciation │ Presets (YAML)                        │
└─────────────────────────────────────────────────────────┘
```

### 3.2 目录结构

```
financial_model/                    # 新的通用模型包
├── __init__.py
├── params/                         # 第一层：参数模型
│   ├── __init__.py
│   ├── base.py                     # 基础参数验证 + 序列化
│   ├── construction.py             # 建设期参数 (起止日期、里程碑)
│   ├── financing.py                # 融资参数 (股债比、利率、还款方式)
│   ├── operating.py                # 运营参数 (装机容量、电价、利用小时)
│   ├── cost.py                     # 成本参数 (材料、维护、人工、保险)
│   ├── tax.py                      # 税务参数 (增值税、所得税、附加税)
│   ├── depreciation.py             # 折旧参数 (5类资产、残值率、年限)
│   └── presets/                    # 预设模板
│       ├── pshp_standard.yaml      # 标准抽蓄模板
│       └── pshp_guangdong.yaml     # 广东某项目模板
│
├── timeline/                       # 第二层：时间轴引擎
│   ├── __init__.py
│   ├── generator.py                # 时间轴生成器 (任意起止日期)
│   ├── phases.py                   # 阶段划分 (建设/试运行/运营/退役)
│   └── milestones.py               # 里程碑管理 (截流/蓄水/投产/验收)
│
├── engines/                        # 第三层：计算引擎
│   ├── __init__.py
│   ├── base_engine.py              # 引擎基类 (统一接口)
│   ├── investment.py               # 投资概算引擎
│   ├── financing.py                # 资金筹措 + 还本付息引擎
│   ├── depreciation.py             # 折旧摊销引擎 (最复杂)
│   ├── cost.py                     # 成本费用引擎
│   ├── revenue.py                  # 收入税金引擎
│   ├── pnl_equity.py               # 利润表 - 资本金
│   ├── pnl_total.py                # 利润表 - 全投资
│   ├── cashflow_equity.py          # 现金流量表 - 资本金
│   ├── cashflow_total.py           # 现金流量表 - 全投资
│   ├── cashflow_plan.py            # 现金流量表 - 财务计划
│   └── balance_sheet.py            # 资产负债表
│
├── orchestrator.py                 # 第四层：模型编排器
│
├── analysis/                       # 第五层：分析工具
│   ├── __init__.py
│   ├── sensitivity.py              # 敏感性分析
│   ├── scenario.py                 # 情景分析
│   ├── monte_carlo.py              # 蒙特卡罗模拟
│   └── derived.py                  # 派生指标 (IRR/NPV/DSCR/回收期)
│
└── export/                         # 第六层：导出层
    ├── __init__.py
    ├── excel_exporter.py           # → Excel (格式化输出)
    ├── neo4j_bridge.py             # → 知识图谱 (复用现有体系)
    └── report_exporter.py          # → Word/PDF 报告
```

---

## 4. 核心模块详细设计

### 4.1 参数模型层 (Params)

#### 设计原则
- 每个 dataclass 对应一个参数类别
- 所有字段有类型标注和默认值
- `__post_init__` 做跨字段验证
- 支持 JSON/YAML 序列化
- 支持预设模板加载

#### 参数分类

| 类别 | 文件 | 关键参数 | 数量估计 |
|------|------|---------|---------|
| **工程计划** | `construction.py` | 建设期起止日期、里程碑、施工进度 | ~15 |
| **生产技术** | `operating.py` | 装机容量、利用小时、达产比例(48年序列)、电价(48年序列) | ~100+ |
| **工程概算** | `construction.py` | 30+项工程费用、分年度投资比例 | ~35 |
| **融资计划** | `financing.py` | 股债比、资本金到位计划、贷款条款(利率/期限/还款方式)、短期贷款(48年) | ~160+ |
| **成本费用** | `cost.py` | 材料、维护(48年)、保险(48年)、人工、抽水电费(48年)、其他 | ~200+ |
| **折旧摊销** | `depreciation.py` | 5类资产的原始值、残值率、折旧年限、摊销年限 | ~25 |
| **税务** | `tax.py` | 增值税率、所得税率、附加税率、亏损弥补年限 | ~10 |

#### 示例：建设期参数

```python
@dataclass(frozen=True)
class ConstructionParams:
    """建设期参数 — 改变这些参数，整个模型自动适配"""

    # 核心日期 (模型种子)
    construction_start: date    # 建设期起始日期 (原 参数输入表!I5)
    construction_end: date      # 建设期结束日期 (原 参数输入表!I7)

    # 派生日期 (自动计算，不可手动设置)
    # first_year_end: date     # = date(construction_start.year, 12, 31)
    # end_year_start: date     # = date(construction_end.year, 1, 1)
    # operation_start: date    # = construction_end + timedelta(days=1)

    # 运营期
    operation_years: int = 40   # 运营期年限 (原 参数输入表!I26)

    # 里程碑 (可选，用于投资分配)
    milestones: tuple[Milestone, ...] = ()

    # 工程概算明细
    budget_items: tuple[BudgetItem, ...] = ()

    @property
    def construction_months(self) -> int:
        """建设期总月数"""
        ...

    @property
    def construction_years(self) -> int:
        """建设期年数 (向上取整)"""
        ...
```

### 4.2 时间轴引擎 (Timeline) ← 核心创新

#### 为什么这是核心

当前模型所有时间依赖问题的根源是 Excel 列位置与日期的硬绑定。时间轴引擎用 DataFrame + DatetimeIndex 彻底解耦：

| 维度 | Excel 模型 | Python 引擎 |
|------|-----------|------------|
| 时间轴 | 54个固定列位置 | DatetimeIndex，任意长度 |
| 建设期列数 | 固定10列(F-O) | 动态生成，5年=7列，10年=12列 |
| SUMIF按年聚合 | `$C$6:$BC$6` 硬编码范围 | `df.groupby(df.index.year).sum()` |
| 日期变更影响 | 180+公式引用断裂 | DataFrame自动适配 |
| 新增里程碑 | 手动插入列 | 添加到 milestones 列表 |

#### 时间轴数据结构

```python
@dataclass(frozen=True)
class ProjectTimeline:
    """项目时间轴 — 由参数驱动，完全动态"""

    # 核心日期
    construction_start: date
    construction_end: date
    operation_start: date       # = construction_end + 1 day
    operation_end: date

    # 建设期时间节点 (不规则)
    # 列: period_start, period_end, months, year, phase
    construction_periods: pd.DataFrame

    # 运营期时间节点 (标准年度)
    # 列: period_start, period_end, months, year, phase
    operation_periods: pd.DataFrame

    # 完整时间轴 (建设+运营)
    full_periods: pd.DataFrame

    # 派生属性
    construction_months: int
    construction_years: int
    operation_years: int
    total_years: int
```

#### 时间轴生成规则

**建设期** (不规则时间节点):
1. **首年**: `construction_start` → 当年 12月31日
2. **中间完整年**: 1月1日 → 12月31日
3. **末年**: 1月1日 → `construction_end`

**运营期** (标准年度):
1. `operation_start` → 当年12月31日 (首年)
2. 中间年: 1月1日 → 12月31日
3. 末年: 1月1日 → `operation_end`

**每年月份计算**: `round((end - start).days / 30)`

**年度聚合** (替代SUMIF):
```python
# 替代 =SUMIF($C$6:$BC$6, C7, $C$5:$BC$5)
monthly_by_year = df.groupby('year')['months'].sum()
```

### 4.3 计算引擎层 (Engines)

#### 基类设计

```python
class BaseEngine(ABC):
    """所有计算引擎的基类"""

    def __init__(self, params: ModelParams, timeline: ProjectTimeline):
        self.params = params
        self.timeline = timeline

    @abstractmethod
    def calculate(self) -> pd.DataFrame:
        """执行计算，返回结果 DataFrame"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """引擎名称"""
        ...
```

#### 引擎依赖关系

```
投资概算引擎 ──────────────────────────────────┐
                                               │
融资引擎 ←── 投资概算引擎                       │
                                               │
折旧引擎 ←── 投资概算引擎 + 时间轴              │
                                               │
成本引擎 ←── 折旧引擎 + 融资引擎               │
                                               │
收入引擎 ←── 运营参数 + 时间轴                  │
                                               │
利润表 ←── 收入引擎 + 成本引擎 + 融资引擎       │
(资本金/全投资)                                 │
                                               │
现金流量表 ←── 利润表 + 投资概算 + 融资引擎     │
(资本金/全投资/财务计划)                         │
                                               │
资产负债表 ←── 利润表 + 现金流量表 + 折旧引擎   │
```

#### 关键引擎说明

**折旧引擎** (最复杂):
- 5 类资产: 房屋建筑物、机器设备、运输工具、无形资产、长期待摊费用
- 每类资产: 原始值、残值率、折旧年限各不同
- 建设期利息资本化 → 增加固定资产原值
- SUMIF 替代: `groupby(year).sum()` 按年聚合月度折旧
- 需处理: 首末年不完整折旧、残值处理

**融资引擎** (次复杂):
- 多笔长期贷款，每笔可独立设置: 金额、利率、宽限期、还款方式
- 建设期: 按里程碑到账 → 计算建设期利息
- 运营期: 等额本金/等额本息/自定义还款
- 短期贷款: 运营期每年可借还
- 亏损弥补: 所得税亏损结转

### 4.4 模型编排器 (Orchestrator)

```python
class ModelOrchestrator:
    """模型编排器 — 管理引擎依赖，一键生成全表"""

    def __init__(self, params: ModelParams):
        self.params = params
        self.timeline = generate_timeline(
            params.construction.construction_start,
            params.construction.construction_end,
            params.construction.operation_years,
        )

    def run(self) -> ModelResult:
        """执行全部计算"""
        # 1. 按依赖顺序执行各引擎
        investment = InvestmentEngine(self.params, self.timeline).calculate()
        financing = FinancingEngine(self.params, self.timeline, investment).calculate()
        depreciation = DepreciationEngine(self.params, self.timeline, investment).calculate()
        cost = CostEngine(self.params, self.timeline, depreciation, financing).calculate()
        revenue = RevenueEngine(self.params, self.timeline).calculate()

        # 2. 报表引擎
        pnl_equity = PnLEquityEngine(...).calculate()
        pnl_total = PnLTotalEngine(...).calculate()
        cf_equity = CashFlowEquityEngine(...).calculate()
        cf_total = CashFlowTotalEngine(...).calculate()
        cf_plan = CashFlowPlanEngine(...).calculate()
        bs = BalanceSheetEngine(...).calculate()

        # 3. 派生指标
        derived = DerivedMetrics(pnl_equity, cf_equity, ...).calculate()

        return ModelResult(
            timeline=self.timeline,
            investment=investment,
            financing=financing,
            depreciation=depreciation,
            cost=cost,
            revenue=revenue,
            pnl_equity=pnl_equity,
            pnl_total=pnl_total,
            cashflow_equity=cf_equity,
            cashflow_total=cf_total,
            cashflow_plan=cf_plan,
            balance_sheet=bs,
            derived_metrics=derived,
        )
```

### 4.5 分析工具层 (Analysis)

复用现有 `financial_kg/engine/` 中的模块设计思路，但基于 DataFrame 接口：

- **敏感性分析**: 参数扰动 → 重跑编排器 → 对比结果
- **情景分析**: 参数组合 (悲观/基准/乐观) → 并行跑编排器
- **蒙特卡罗**: 随机参数采样 → 大量跑编排器 → 统计分布
- **派生指标**: IRR、NPV、DSCR、回收期、资产负债率

### 4.6 导出层 (Export)

- **Excel 导出**: 使用 openpyxl/xlsxwriter 生成格式化 Excel
- **Neo4j 桥接**: 生成知识图谱节点/边 → 注入 Neo4j → 复用现有 Q&A/可视化体系
- **报告导出**: Word/PDF 报告 (复用现有 report_export.py 逻辑)

---

## 5. 与现有系统的关系

```
现有系统 (保留，用于分析/Q&A):
  financial_kg/  → 知识图谱解析 + LLM问答 + 可视化
  pages/         → Streamlit UI
  main.py        → 入口

新系统 (构建):
  financial_model/ → 通用计算引擎

桥接方式:
  方式1: financial_model → 生成Excel → financial_kg 解析 → 知识图谱
  方式2: financial_model → 直接生成图谱数据 → neo4j_bridge → Neo4j
```

**可复用的现有模块**:
- `financial_kg/viz/` — ECharts/pyvis 可视化
- `financial_kg/llm/` — LLM Q&A 系统
- `financial_kg/storage/` — Neo4j 存储
- `financial_kg/engine/sensitivity.py` — 敏感性分析逻辑
- `financial_kg/engine/monte_carlo*.py` — 蒙特卡罗逻辑
- `pages/` — Streamlit UI 框架

---

## 6. 分阶段实施计划

### Phase 1: 参数抽象 + 时间轴引擎 (2-3天)

**目标**: 建立参数模型和时间轴生成器
**输出**: 给定任意日期 → 生成完整时间轴 DataFrame

| 任务 | 说明 | 预估 |
|------|------|------|
| 1.1 参数提取 | 从现有Excel提取全部参数 → Python dataclass | 0.5天 |
| 1.2 时间轴生成器 | `generate_timeline(start, end, years)` | 1天 |
| 1.3 验证测试 | 用现有模型数据验证时间轴正确性 | 0.5天 |

**验收标准**:
- 输入 (2023-02-01, 2030-07-31, 40) → 输出与 Excel 时间序列表 row 3-8 完全匹配
- 输入 (2025-01-01, 2030-12-31, 30) → 正确生成 6年建设 + 30年运营时间轴
- 所有边界情况 (闰年、部分年、跨年) 处理正确

### Phase 2: 投资概算 + 融资引擎 (3-4天)

**目标**: 最复杂的两个引擎
**输出**: 给定参数+时间轴 → 投资计划表 + 资金筹措表

| 任务 | 说明 | 预估 |
|------|------|------|
| 2.1 投资概算引擎 | 分年度投资分配、价差预备费、建设期利息 | 1天 |
| 2.2 融资引擎 | 多笔贷款、到账计划、还本付息计算 | 2天 |
| 2.3 验证测试 | 与现有 表1 输出对比 | 0.5天 |

### Phase 3: 折旧 + 成本 + 收入引擎 (2-3天)

**目标**: 核心业务计算
**输出**: 折旧表、成本表、收入表

| 任务 | 说明 | 预估 |
|------|------|------|
| 3.1 折旧引擎 | 5类资产折旧，groupby替代SUMIF | 1天 |
| 3.2 成本引擎 | 材料费、维护费、保险费、人工、抽水电费 | 0.5天 |
| 3.3 收入引擎 | 发电量×电价，税金计算 | 0.5天 |
| 3.4 验证测试 | 与现有 表2/3/4 输出对比 | 0.5天 |

### Phase 4: 报表引擎 (2-3天)

**目标**: 利润表/现金流量表/资产负债表
**输出**: 10张标准财务报表

| 任务 | 说明 | 预估 |
|------|------|------|
| 4.1 利润表 | 资本金 + 全投资 | 1天 |
| 4.2 现金流量表 | 资本金 + 全投资 + 财务计划 | 1天 |
| 4.3 资产负债表 | 标准BS格式 | 1天 |
| 4.4 派生指标 | IRR, NPV, DSCR, 回收期 | 0.5天 |

### Phase 5: 分析工具 + 导出 (2-3天)

| 任务 | 说明 | 预估 |
|------|------|------|
| 5.1 敏感性分析 | 复用现有逻辑，DataFrame接口 | 0.5天 |
| 5.2 情景分析 | 参数组合并行计算 | 0.5天 |
| 5.3 蒙特卡罗 | 参数随机采样 + 统计分布 | 0.5天 |
| 5.4 Excel导出 | 格式化输出 | 0.5天 |
| 5.5 Neo4j桥接 | 复用现有知识图谱体系 | 0.5天 |

---

## 7. 技术选型

| 组件 | 选择 | 理由 |
|------|------|------|
| 语言 | Python 3.11+ | 与现有系统一致 |
| 数据结构 | pandas DataFrame | 时间序列计算的标准工具 |
| 参数模型 | dataclass (frozen) | 不可变，符合项目规范 |
| 参数存储 | YAML/JSON | 人类可读，支持预设模板 |
| Excel导出 | openpyxl | 现有系统已使用 |
| 图数据库 | Neo4j | 现有系统已使用 |
| 测试 | pytest | 现有系统已使用 |
| 格式化 | ruff + black | 现有系统已使用 |

---

## 8. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 参数遗漏 | 中 | 高 | 从现有indicators数据批量提取，不全靠手工 |
| 计算精度差异 | 中 | 中 | 设置容差(1e-6)，逐表对比验证 |
| 边界情况未覆盖 | 中 | 中 | 边写测试边开发，覆盖多种建设期长度 |
| 折旧引擎复杂度 | 高 | 高 | 优先实现，预留充足时间 |
| 融资引擎多笔贷款 | 中 | 高 | 先实现单笔贷款，再扩展到多笔 |

---

## 9. 当前模型时间依赖分析（参考）

### 9.1 完整日期依赖链路

```
参数输入表!I5 (建设期起始 2023-02-01)
参数输入表!I7 (建设期结束 2030-07-31)
    │
    ├─→ 参数输入表!I9 = ROUND(DATEDIF(I5,I7,"D")/365*12,0) = 90个月
    │     └─→ I10 = I9/12 = 7.5年
    │           └─→ I4 = ROUNDUP(I10,0) = 8年
    │
    ├─→ 参数输入表!I27 = I7+1 = 2030-08-01 (运营期起始)
    │     └─→ I28 = I7+40*365+INT(40/4) = 2070-07-31 (运营期结束)
    │
    ├─→ 时间序列!C4 = 参数输入表!I5 (时间轴起点)
    ├─→ 时间序列!N4 = 参数输入表!I250 = EDATE(I5,91)-1 (运营起点)
    ├─→ 时间序列!BC4 = 参数输入表!I28 (运营终点)
    │
    ├─→ 时间序列 Row 4: 54列日期端点 (C..BC)
    │     ├─→ Row 5: 月份数 (DATEDIF/30)
    │     ├─→ Row 6: 年度 (YEAR)
    │     └─→ Row 8: SUMIF按年聚合月份
    │           └─→ 16个折旧/摊销SUMIF块 (rows 22-124)
    │                 └─→ 表2-折旧摊销表
    │                 └─→ 表3-成本费用表
    │                 └─→ 表4-收入税金表
    │
    └─→ 投产&达产比例: IF(date=参数输入表!$I$7, 100%, ...)
          └─→ 收入计算
```

### 9.2 受影响Sheet清单

| Sheet | 受影响程度 | 原因 |
|-------|-----------|------|
| 参数输入表 | 直接 | 日期种子所在 |
| 时间序列 | 直接 | 54列时间轴需重建 |
| 投产&达产比例 | 直接 | 生产比例与日期绑定 |
| 投资概算明细 | 直接 | 列标题为日期 |
| 表1-资金筹措 | 高 | ~20子表，列位置与日期绑定 |
| 表2-折旧摊销 | 高 | 16个SUMIF块依赖时间序列 |
| 表3-成本费用 | 中 | 间接通过表2 |
| 表4-收入税金 | 中 | 建设期税务依赖时间轴 |
| 表5-10 | 中低 | 间接依赖，列结构固定 |

---

## 10. 参考资料

- [ANL Pumped Storage Hydropower Valuation Guidebook](https://publications.anl.gov/anlpubs/2021/03/166807.pdf)
- [NREL PSH Component-Level Cost Model](https://docs.nlr.gov/docs/fy23osti/84875.pdf)
- [Gridlines - Infrastructure Project Finance Structure](https://www.gridlines.com/blog/infrastructure-project-finance/)
- [Wall Street Prep - Project Finance Model Structure](https://www.wallstreetprep.com/knowledge/project-finance-model-structure/)
- [Financial Modelling Handbook - Project Finance](https://www.financialmodellinghandbook.org/project-finance-modelling-handbook-contents/)
- [pbpython - Financial Modeling with Pandas](https://pbpython.com/amortization-model.html)
