from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from textwrap import shorten
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ...dashboards.models import DashboardChart, DashboardMetric, DashboardPayload, DashboardPoint, DashboardSeries, DashboardTable


_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "your",
    "you",
    "are",
    "was",
    "were",
    "their",
    "there",
    "they",
    "them",
    "then",
    "than",
    "but",
    "not",
    "did",
    "does",
    "done",
    "into",
    "over",
    "under",
    "also",
    "have",
    "has",
    "had",
    "missing",
    "incorrect",
    "wrong",
    "error",
    "step",
    "work",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> List[str]:
    tokens = re.findall(r"[a-z0-9']+", text.lower())
    return [token for token in tokens if token not in _STOPWORDS and len(token) > 2]


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _load_grade_results(output_dir: Path) -> List[Dict[str, Any]]:
    if not output_dir.exists():
        return []
    results: List[Dict[str, Any]] = []
    for path in output_dir.glob("**/grade_result.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        results.append(data)
    return results


def _short_label(text: str, limit: int = 48) -> str:
    label = shorten(text, width=limit, placeholder="…")
    return label or "(no label)"


def _histogram(values: List[float], bins: int) -> List[Tuple[str, int]]:
    if not values:
        return []
    low = min(values)
    high = max(values)
    if bins < 1:
        bins = 1
    if low == high:
        label = f"{low:.1f}"
        return [(label, len(values))]
    step = (high - low) / bins
    if step <= 0:
        step = 1.0
    counts = [0 for _ in range(bins)]
    for value in values:
        idx = int((value - low) / step)
        if idx >= bins:
            idx = bins - 1
        counts[idx] += 1
    labels: List[Tuple[str, int]] = []
    for idx, count in enumerate(counts):
        start = low + idx * step
        end = start + step
        labels.append((f"{start:.1f}-{end:.1f}", count))
    return labels


def _cluster_items(items: List[Dict[str, Any]], threshold: float) -> List[Dict[str, Any]]:
    clusters: List[Dict[str, Any]] = []
    for item in items:
        tokens = item.get("tokens") or []
        best_idx = None
        best_score = 0.0
        for idx, cluster in enumerate(clusters):
            score = _jaccard(tokens, cluster["tokens"])
            if score > best_score:
                best_score = score
                best_idx = idx
        if best_idx is not None and best_score >= threshold:
            cluster = clusters[best_idx]
            cluster["items"].append(item)
            cluster["tokens"].update(tokens)
        else:
            clusters.append({"tokens": set(tokens), "items": [item]})
    return clusters


def _cluster_label(cluster: Dict[str, Any]) -> str:
    counter: Counter[str] = Counter()
    for item in cluster["items"]:
        counter.update(item.get("tokens") or [])
    tokens = [token for token, _count in counter.most_common(4)]
    if tokens:
        return " ".join(tokens)
    sample = cluster["items"][0].get("comment") or cluster["items"][0].get("text") or ""
    return _short_label(sample)


def build_assessment_dashboard(
    *,
    output_dir: Path,
    assignment_id: str,
    course_id: Optional[str],
    top_clusters: int,
    similarity_threshold: float,
    min_cluster_size: int,
    score_bins: int = 10,
) -> DashboardPayload:
    results = _load_grade_results(output_dir)
    filtered: List[Dict[str, Any]] = []
    for item in results:
        if item.get("assignment_id") != assignment_id:
            continue
        if course_id and item.get("course_id") != course_id:
            continue
        filtered.append(item)

    if not filtered:
        return DashboardPayload(
            title=f"Assessment Dashboard: {assignment_id}",
            subtitle="No grading results found",
            updated_at=_now_iso(),
            notes=["No grade_result.json files matched the assignment filter."],
        )

    assignment_title = filtered[0].get("assignment_title") or assignment_id

    points_by_criterion: Dict[str, List[float]] = {}
    total_scores: List[float] = []
    evidence_mode_counts: Counter[str] = Counter()
    graded_at_counts: Counter[str] = Counter()
    items_by_criterion: Dict[str, List[Dict[str, Any]]] = {}

    for result in filtered:
        total_points = (result.get("grade") or {}).get("total_points")
        if isinstance(total_points, (int, float)):
            total_scores.append(float(total_points))
        evidence_mode = result.get("evidence_mode") or "unknown"
        evidence_mode_counts[str(evidence_mode)] += 1
        graded_at = result.get("graded_at")
        if isinstance(graded_at, str):
            try:
                dt = datetime.fromisoformat(graded_at.replace("Z", "+00:00"))
                graded_at_counts[dt.date().isoformat()] += 1
            except ValueError:
                pass
        grade = result.get("grade") or {}
        criteria = grade.get("criteria") or []
        for criterion in criteria:
            criterion_id = criterion.get("id") or "unknown"
            points = criterion.get("points")
            if isinstance(points, (int, float)):
                points_by_criterion.setdefault(criterion_id, []).append(float(points))
            comment = (criterion.get("comment") or "").strip()
            evidence = criterion.get("evidence") or []
            quote = ""
            if evidence:
                quote = (evidence[0].get("quote") or "").strip()
            text = " ".join(part for part in [comment, quote] if part).strip()
            if not text:
                continue
            tokens = _tokenize(text)
            item = {
                "criterion_id": criterion_id,
                "comment": comment,
                "quote": quote,
                "text": text,
                "tokens": tokens,
            }
            items_by_criterion.setdefault(criterion_id, []).append(item)

    clusters: List[Dict[str, Any]] = []
    for criterion_id, items in items_by_criterion.items():
        grouped = _cluster_items(items, similarity_threshold)
        for cluster in grouped:
            if len(cluster["items"]) < min_cluster_size:
                continue
            label = _cluster_label(cluster)
            clusters.append(
                {
                    "criterion_id": criterion_id,
                    "label": label,
                    "count": len(cluster["items"]),
                    "sample": cluster["items"][0],
                }
            )

    clusters.sort(key=lambda entry: entry["count"], reverse=True)
    top = clusters[:top_clusters]

    metrics = [
        DashboardMetric(label="Submissions", value=len(filtered)),
        DashboardMetric(label="Clusters", value=len(clusters)),
    ]

    charts: List[DashboardChart] = []
    if total_scores:
        hist = _histogram(total_scores, score_bins)
        charts.append(
            DashboardChart(
                id="total_score_histogram",
                title="Total Score Distribution",
                type="bar",
                series=[
                    DashboardSeries(
                        label="Submissions",
                        points=[DashboardPoint(t=label, v=count) for label, count in hist],
                    )
                ],
            )
        )
    if evidence_mode_counts:
        charts.append(
            DashboardChart(
                id="evidence_mode",
                title="Evidence Mode Split",
                type="bar",
                series=[
                    DashboardSeries(
                        label="Count",
                        points=[
                            DashboardPoint(t=mode, v=count)
                            for mode, count in sorted(evidence_mode_counts.items())
                        ],
                    )
                ],
            )
        )
    if graded_at_counts:
        charts.append(
            DashboardChart(
                id="submissions_over_time",
                title="Submissions Over Time",
                type="line",
                series=[
                    DashboardSeries(
                        label="Submissions",
                        points=[
                            DashboardPoint(t=day, v=count)
                            for day, count in sorted(graded_at_counts.items())
                        ],
                    )
                ],
            )
        )
    if top:
        charts.append(
            DashboardChart(
                id="common_mistakes",
                title="Most Common Similar Wrong Answers",
                type="bar",
                series=[
                    DashboardSeries(
                        label="Count",
                        points=[
                            DashboardPoint(t=f"{entry['criterion_id']}: {entry['label']}", v=entry["count"])
                            for entry in top
                        ],
                    )
                ],
            )
        )

    if points_by_criterion:
        charts.append(
            DashboardChart(
                id="avg_points",
                title="Average Points By Criterion",
                type="bar",
                series=[
                    DashboardSeries(
                        label="Avg points",
                        points=[
                            DashboardPoint(
                                t=criterion_id,
                                v=sum(values) / max(len(values), 1),
                            )
                            for criterion_id, values in sorted(points_by_criterion.items())
                        ],
                    )
                ],
            )
        )

    tables: List[DashboardTable] = []
    if top:
        rows: List[List[Any]] = []
        for entry in top:
            sample = entry.get("sample") or {}
            rows.append(
                [
                    entry["criterion_id"],
                    entry["label"],
                    entry["count"],
                    _short_label(sample.get("comment") or "", 80),
                    _short_label(sample.get("quote") or "", 80),
                ]
            )
        tables.append(
            DashboardTable(
                title="Common Wrong Answer Clusters",
                columns=["Criterion", "Cluster", "Count", "Example Comment", "Example Quote"],
                rows=rows,
            )
        )

    notes = [
        "Clusters are grouped by comment + evidence similarity (token Jaccard).",
        "Use GRADING_WORKER_CONCURRENCY to speed up batch grading; dashboard updates are separate jobs.",
    ]

    return DashboardPayload(
        title=f"Assessment Dashboard: {assignment_title}",
        subtitle=f"Assignment {assignment_id} • {len(filtered)} submissions",
        updated_at=_now_iso(),
        metrics=metrics,
        charts=charts,
        tables=tables,
        notes=notes,
    )
