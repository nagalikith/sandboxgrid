from sqlmodel import SQLModel
from datetime import datetime, timezone

from sandbox_api.charts_renderer import (
    _chart_theme,
    _safe_filename,
    _write_chart_html,
    build_echarts_option,
    render_dashboard_charts,
)
from sandbox_api.dashboard_models import DashboardChart, DashboardPayload, DashboardPoint, DashboardSeries
from sandbox_api.database import engine
from sandbox_api.models import SandboxRecord, SandboxStatus
from sandbox_api.artifacts import ArtifactRepository


def test_build_echarts_option_line_chart():
    chart = DashboardChart(
        id="requests",
        title="Requests",
        type="line",
        series=[
            DashboardSeries(
                label="req/min",
                points=[DashboardPoint(t="t1", v=1), DashboardPoint(t="t2", v=3)],
            )
        ],
    )
    option, background = build_echarts_option(chart, variant="v1")
    assert option["xAxis"]["data"] == ["t1", "t2"]
    assert option["series"][0]["type"] == "line"
    assert option["series"][0]["data"] == [1, 3]
    assert background


def test_build_echarts_option_bar_chart_with_gaps():
    chart = DashboardChart(
        id="sales",
        title="Sales",
        type="bar",
        series=[
            DashboardSeries(
                label="Series A",
                points=[DashboardPoint(t="t1", v=10), DashboardPoint(t="t3", v=30)],
            ),
            DashboardSeries(
                label="Series B",
                points=[DashboardPoint(t="t2", v=20)],
            ),
        ],
    )
    option, _ = build_echarts_option(chart, variant="v2")
    assert option["xAxis"]["data"] == ["t1", "t3", "t2"]
    assert option["series"][0]["data"] == [10, 30, None]
    assert option["series"][1]["data"] == [None, None, 20]


def test_safe_filename_and_theme():
    assert _safe_filename("Chart 1") == "Chart_1"
    assert _safe_filename("!!!") == "chart"
    theme = _chart_theme("unknown")
    assert "background" in theme


def test_write_chart_html(tmp_path):
    output_path = tmp_path / "chart.html"
    _write_chart_html(
        output_path=output_path,
        option={"title": "demo"},
        width=100,
        height=120,
        background="#fff",
        chart_id="chart",
    )
    content = output_path.read_text(encoding="utf-8")
    assert "echarts" in content


def test_render_dashboard_charts_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTS_RENDERER", "off")
    payload = DashboardPayload(charts=[DashboardChart(id="c1", title="t1")])
    now = datetime.now(timezone.utc)
    record = SandboxRecord(
        sandbox_id="sbx_1",
        status=SandboxStatus.ready,
        created_at=now,
        expires_at=now,
        owner_id="user_a",
        cpu_limit="2",
        memory_limit_mb=1024,
        capabilities=[],
        allow_network=None,
        artifacts_path=str(tmp_path),
    )
    repo = ArtifactRepository(engine)
    assert render_dashboard_charts(payload, record=record, artifact_repo=repo, emit_event=lambda _e: None, log=lambda _m: None) == []


def test_render_dashboard_charts_html(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTS_RENDERER", "html")
    SQLModel.metadata.create_all(engine)
    payload = DashboardPayload(
        charts=[
            DashboardChart(
                id="chart-one",
                title="Chart",
                type="line",
                series=[DashboardSeries(label="s1", points=[DashboardPoint(t="a", v=1)])],
            )
        ]
    )
    now = datetime.now(timezone.utc)
    record = SandboxRecord(
        sandbox_id="sbx_1",
        status=SandboxStatus.ready,
        created_at=now,
        expires_at=now,
        owner_id="user_a",
        cpu_limit="2",
        memory_limit_mb=1024,
        capabilities=[],
        allow_network=None,
        artifacts_path=str(tmp_path),
    )
    repo = ArtifactRepository(engine)
    events = []
    logs = []
    artifact_ids = render_dashboard_charts(
        payload,
        record=record,
        artifact_repo=repo,
        emit_event=events.append,
        log=logs.append,
    )
    assert artifact_ids
    assert events


def test_render_dashboard_charts_unknown_renderer(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTS_RENDERER", "unknown")
    payload = DashboardPayload(charts=[DashboardChart(id="c1", title="t1")])
    now = datetime.now(timezone.utc)
    record = SandboxRecord(
        sandbox_id="sbx_1",
        status=SandboxStatus.ready,
        created_at=now,
        expires_at=now,
        owner_id="user_a",
        cpu_limit="2",
        memory_limit_mb=1024,
        capabilities=[],
        allow_network=None,
        artifacts_path=str(tmp_path),
    )
    repo = ArtifactRepository(engine)
    logs = []
    artifact_ids = render_dashboard_charts(
        payload, record=record, artifact_repo=repo, emit_event=lambda _e: None, log=logs.append
    )
    assert artifact_ids == []
    assert logs
