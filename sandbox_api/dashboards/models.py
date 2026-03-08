from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


class DashboardMetric(BaseModel):
    label: str
    value: Any
    delta: Optional[str] = None
    tone: Optional[Literal["up", "down", "neutral"]] = None


class DashboardPoint(BaseModel):
    t: str
    v: float


class DashboardSeries(BaseModel):
    label: str
    points: List[DashboardPoint] = Field(default_factory=list)


class DashboardChart(BaseModel):
    id: str
    title: str
    type: Literal["line", "bar"] = "line"
    series: List[DashboardSeries] = Field(default_factory=list)
    unit: Optional[str] = None
    variant: Optional[Literal["v1", "v2", "v3"]] = None
    width: Optional[int] = None
    height: Optional[int] = None
    background: Optional[str] = None


class DashboardTable(BaseModel):
    title: Optional[str] = None
    columns: List[str] = Field(default_factory=list)
    rows: List[List[Any]] = Field(default_factory=list)


class DashboardPayload(BaseModel):
    title: Optional[str] = None
    subtitle: Optional[str] = None
    updated_at: Optional[str] = None
    chart_variant: Optional[Literal["v1", "v2", "v3"]] = None
    chart_width: Optional[int] = None
    chart_height: Optional[int] = None
    chart_background: Optional[str] = None
    metrics: List[DashboardMetric] = Field(default_factory=list)
    charts: List[DashboardChart] = Field(default_factory=list)
    tables: List[DashboardTable] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)

    class Config:
        extra = "allow"
