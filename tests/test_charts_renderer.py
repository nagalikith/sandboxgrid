from sandbox_api.charts_renderer import build_echarts_option
from sandbox_api.dashboard_models import DashboardChart, DashboardPoint, DashboardSeries


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
