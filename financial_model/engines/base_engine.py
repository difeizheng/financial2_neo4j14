"""
计算引擎基类 — 统一接口和通用工具

所有引擎继承 BaseEngine，实现 calculate() 方法。
每个引擎接收参数模型和时间轴，返回 pandas DataFrame。

设计原则:
  - 引擎间通过 DataFrame 传递数据 (无共享状态)
  - 引擎依赖通过构造器注入 (非服务定位器)
  - 结果不可变 — calculate() 每次返回新 DataFrame
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import pandas as pd

from financial_model.params.construction import ConstructionParams
from financial_model.params.investment import InvestmentParams
from financial_model.params.financing import FinancingParams
from financial_model.timeline.generator import ProjectTimeline


class BaseEngine(ABC):
    """所有计算引擎的基类

    子类必须实现:
      - name: 引擎名称 (用于日志和调试)
      - calculate(): 核心计算逻辑, 返回 DataFrame

    子类可选实现:
      - validate_inputs(): 输入验证 (在 calculate 前调用)
    """

    def __init__(
        self,
        params_construction: ConstructionParams,
        params_investment: InvestmentParams,
        params_financing: FinancingParams,
        timeline: ProjectTimeline,
    ) -> None:
        self._construction = params_construction
        self._investment = params_investment
        self._financing = params_financing
        self._timeline = timeline

    @property
    @abstractmethod
    def name(self) -> str:
        """引擎名称 (如 'investment', 'financing', 'depreciation')"""
        ...

    @abstractmethod
    def calculate(self) -> pd.DataFrame:
        """执行计算, 返回结果 DataFrame

        Returns:
            DataFrame, index 为年度或时间区间, columns 为各计算项。
            必须包含 'year' 列 (int) 用于年度对齐。
        """
        ...

    def validate_inputs(self) -> list[str]:
        """验证输入参数, 返回警告列表 (空列表 = 无警告)

        默认实现为空, 子类可覆盖以添加特定验证。
        """
        return []

    # ── 通用工具方法 ────────────────────────────────────────

    def _aligned_yearly_df(
        self,
        data: dict[str, list[Any]],
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> pd.DataFrame:
        """创建与时间轴对齐的年度 DataFrame

        Args:
            data: {列名: [值列表]} 字典
            start_year: 起始年 (默认=建设起始年)
            end_year: 结束年 (默认=运营结束年)

        Returns:
            DataFrame, index=year (int)
        """
        if start_year is None:
            start_year = self._timeline.year_range.start
        if end_year is None:
            end_year = self._timeline.year_range.stop - 1  # range.stop is exclusive

        years = list(range(start_year, end_year + 1))
        df = pd.DataFrame(data, index=years)
        df.index.name = "year"
        return df

    @property
    def construction(self) -> ConstructionParams:
        return self._construction

    @property
    def investment(self) -> InvestmentParams:
        return self._investment

    @property
    def financing(self) -> FinancingParams:
        return self._financing

    @property
    def timeline(self) -> ProjectTimeline:
        return self._timeline


@dataclass(frozen=True)
class EngineResult:
    """引擎计算结果容器

    Attributes:
        engine_name: 引擎名称
        data: 计算 DataFrame
        warnings: 输入验证警告
    """

    engine_name: str
    data: pd.DataFrame
    warnings: tuple[str, ...] = ()
