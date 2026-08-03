from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from uuid import uuid4

from ..artifacts import ArtifactRecord, ArtifactRepository, register_artifact_file
from .models import DashboardChart, DashboardPayload
from ..sandboxes.models import SandboxRecord
from datetime import datetime, timezone


def _safe_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
    return safe.strip("_") or "chart"


def _chart_theme(variant: str) -> dict[str, Any]:
    themes = {
        "v1": {
            "background": "#0f172a",
            "text": "#e2e8f0",
            "grid": "#334155",
            "axis": "#475569",
            "palette": ["#38bdf8", "#f472b6", "#22c55e", "#f59e0b"],
        },
        "v2": {
            "background": "#ffffff",
            "text": "#0f172a",
            "grid": "#e2e8f0",
            "axis": "#94a3b8",
            "palette": ["#2563eb", "#16a34a", "#f97316", "#db2777"],
        },
        "v3": {
            "background": "#111827",
            "text": "#f9fafb",
            "grid": "#1f2937",
            "axis": "#6b7280",
            "palette": ["#a78bfa", "#34d399", "#fbbf24", "#f472b6"],
        },
    }
    return themes.get(variant, themes["v1"])


def _collect_categories(series: Iterable[Any]) -> list[str]:
    categories: list[str] = []
    for entry in series:
        for point in entry.points or []:
            if point.t not in categories:
                categories.append(point.t)
    return categories


def _series_values(series, categories: list[str]) -> list[Optional[float]]:
    lookup = {point.t: point.v for point in series.points or []}
    return [lookup.get(category) for category in categories]


def build_echarts_option(chart: DashboardChart, *, variant: str) -> tuple[dict[str, Any], str]:
    theme = _chart_theme(variant)
    categories = _collect_categories(chart.series)
    option = {
        "backgroundColor": theme["background"],
        "textStyle": {"color": theme["text"]},
        "title": {"text": chart.title, "left": "center", "textStyle": {"color": theme["text"]}},
        "tooltip": {"trigger": "axis"},
        "legend": {
            "data": [series.label for series in chart.series],
            "textStyle": {"color": theme["text"]},
        },
        "grid": {"left": 48, "right": 24, "top": 60, "bottom": 40},
        "xAxis": {
            "type": "category",
            "data": categories,
            "axisLine": {"lineStyle": {"color": theme["axis"]}},
            "axisLabel": {"color": theme["text"]},
        },
        "yAxis": {
            "type": "value",
            "axisLine": {"lineStyle": {"color": theme["axis"]}},
            "splitLine": {"lineStyle": {"color": theme["grid"]}},
            "axisLabel": {"color": theme["text"]},
            "name": chart.unit or "",
        },
        "color": theme["palette"],
        "animation": False,
        "series": [],
    }

    for series in chart.series:
        entry = {
            "name": series.label,
            "type": chart.type,
            "data": _series_values(series, categories),
        }
        if chart.type == "line":
            entry["smooth"] = True
        option["series"].append(entry)

    return option, theme["background"]


def _register_chart_artifact(
    repository,
    *,
    owner_id: str,
    sandbox_id: str,
    run_id: str,
    file_path: Path,
    filename: str,
    tags: list[str],
    artifact_type: str,
    artifact_format: str,
    mime_type: str,
    session_id: str | None = None,
    emit_event=None,
) -> ArtifactRecord:
    """Register a rendered chart via the shared helper (canonical events)."""
    return register_artifact_file(
        repository,
        owner_id=owner_id,
        sandbox_id=sandbox_id,
        session_id=session_id,
        run_id=run_id,
        command_id=run_id,
        file_path=file_path,
        filename=filename,
        artifact_format=artifact_format,
        mime_type=mime_type,
        artifact_type=artifact_type,
        tags=tags,
        source="dashboard",
        sensitivity="internal",
        volatility="stable",
        emit_event=emit_event,
    )


def _render_with_docker(work_dir: Path, *, input_path: Path) -> None:
    docker_bin = os.getenv("DOCKER_BIN", "docker")
    image = os.getenv("CHARTS_RENDERER_IMAGE", "cua-echarts:latest")
    cmd = [
        docker_bin,
        "run",
        "--rm",
        "-v",
        f"{work_dir.resolve()}:/work",
        image,
        f"/work/{input_path.name}",
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _render_with_node(work_dir: Path, *, input_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "charts" / "render.js"
    cmd = ["node", str(script), str(input_path)]
    subprocess.run(cmd, check=True, cwd=str(work_dir), capture_output=True, text=True)


def _write_chart_html(
    *,
    output_path: Path,
    option: dict[str, Any],
    width: int,
    height: int,
    background: str,
    chart_id: str,
) -> None:
    echarts_cdn = os.getenv(
        "ECHARTS_CDN_URL",
        "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js",
    )
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>{chart_id}</title>
    <style>
      html, body {{
        margin: 0;
        padding: 0;
        background: {background};
      }}
      #chart {{
        width: {width}px;
        height: {height}px;
      }}
    </style>
  </head>
  <body>
    <div id="chart"></div>
    <script src="{echarts_cdn}"></script>
    <script>
      const option = {json.dumps(option, ensure_ascii=True)};
      const chart = echarts.init(document.getElementById("chart"));
      chart.setOption(option);
      window.addEventListener("resize", () => chart.resize());
    </script>
  </body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")


def render_dashboard_charts(
    payload: DashboardPayload,
    *,
    record: SandboxRecord,
    artifact_repo: ArtifactRepository,
    emit_event: Callable[[dict[str, Any]], None],
    log: Callable[[str], None],
) -> list[str]:
    renderer = os.getenv("CHARTS_RENDERER", "disabled").lower()
    if renderer in {"disabled", "off", "none"}:
        return []

    run_id = f"dash_{uuid4().hex[:8]}"
    base_dir = Path(record.artifacts_path or "./artifacts") / "dashboard" / run_id
    base_dir.mkdir(parents=True, exist_ok=True)

    artifact_ids: list[str] = []
    for idx, chart in enumerate(payload.charts or []):
        chart_id = chart.id or f"chart_{idx+1}"
        name = _safe_filename(chart_id)
        variant = chart.variant or payload.chart_variant or os.getenv("CHARTS_VARIANT", "v1")
        width = chart.width or payload.chart_width or int(os.getenv("CHARTS_WIDTH", "1200"))
        height = chart.height or payload.chart_height or int(os.getenv("CHARTS_HEIGHT", "630"))
        option, theme_background = build_echarts_option(chart, variant=variant)
        background = chart.background or payload.chart_background or theme_background

        work_dir = base_dir / name
        work_dir.mkdir(parents=True, exist_ok=True)
        input_path = work_dir / "input.json"
        output_name = f"{name}.png"
        input_payload = {
            "option": option,
            "output": output_name,
            "width": width,
            "height": height,
            "background": background,
        }
        input_path.write_text(json.dumps(input_payload, ensure_ascii=True), encoding="utf-8")

        try:
            if renderer == "docker":
                _render_with_docker(work_dir, input_path=input_path)
            elif renderer == "node":
                _render_with_node(work_dir, input_path=input_path)
            elif renderer == "html":
                output_name = f"{name}.html"
                output_path = work_dir / output_name
                _write_chart_html(
                    output_path=output_path,
                    option=option,
                    width=width,
                    height=height,
                    background=background,
                    chart_id=chart_id,
                )
            else:
                log(f"Unknown charts renderer: {renderer}")
                continue
        except subprocess.CalledProcessError as exc:
            log(f"Chart render failed: {exc}")
            continue

        output_path = work_dir / output_name
        if not output_path.exists():
            log("Chart render produced no output.")
            continue

        if renderer == "html":
            artifact_format = "html"
            mime_type = "text/html"
            artifact_type = "chart_html"
        else:
            artifact_format = "png"
            mime_type = "image/png"
            artifact_type = "chart"

        tags = ["dashboard", "chart", variant]
        if idx == 0:
            tags = tags + ["key"]
        artifact = _register_chart_artifact(
            artifact_repo,
            owner_id=record.owner_id,
            sandbox_id=record.sandbox_id,
            run_id=run_id,
            file_path=output_path,
            filename=output_name,
            tags=tags,
            artifact_type=artifact_type,
            artifact_format=artifact_format,
            mime_type=mime_type,
            emit_event=emit_event,
        )
        if artifact:
            artifact_ids.append(artifact.artifact_id)

    return artifact_ids
