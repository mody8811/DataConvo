"""Multi-agent orchestration engine for Data Convo.

Each persona wraps the existing text-to-SQL execution pipeline AFTER results
are returned. None hardcode any database schema or provider — they operate
purely on the SQL execution output, and they use the request_clarification
path when the incoming question is too vague to proceed.
"""
from collections import Counter
from datetime import date, datetime

PERSONAS = {
    "querybot": {"name": "QueryBot", "icon": "/static/brand/querybot.png", "description": "Standard text-to-SQL engine."},
    "vizbot": {"name": "VizBot", "icon": "/static/brand/vizbot.png", "description": "Structures results for charts."},
    "anomalybot": {"name": "AnomalyBot", "icon": "/static/brand/anomalybot.png", "description": "Data hygiene sentinel."},
    "deepbot": {"name": "DeepBot", "icon": "/static/brand/deepbot.png", "description": "Statistical analyst."},
    "internetbot": {"name": "InternetBot", "icon": "/static/brand/internetbot.png", "description": "External research bridge."},
}

# Vague patterns that trigger request_clarification instead of guessing
_AMBIGUOUS_MARKERS = [
    "this table", "that table", "the table", "some data", "a dataset",
    "this one", "that one", "dataset", "the data set", "which table",
]


def _rows_to_list(raw_results):
    """Normalize query results into a list of dicts."""
    if isinstance(raw_results, str):
        return [{"result": raw_results}]
    if not isinstance(raw_results, list):
        return []

    formatted = []
    for row in raw_results:
        if isinstance(row, (tuple, list)):
            formatted.append({f"col_{i}": v for i, v in enumerate(row)})
        elif isinstance(row, dict):
            formatted.append(dict(row))
        else:
            formatted.append({"result": row})
    return formatted


def _is_ambiguous(question, rows):
    """Heuristic: request clarification when question is vague OR when we have no data."""
    q = (question or "").lower()
    if any(m in q for m in _AMBIGUOUS_MARKERS):
        return True
    return len(rows) == 0


def run_persona(persona, raw_results, sql, question, table_names=None):
    """Route through the selected persona's post-processing pipeline.

    Returns a dict the router merges into the chat response. If the query
    is ambiguous or yielded no data, it returns a clarification object
    instead of fabricating an answer.
    """
    persona = (persona or "querybot").lower()
    rows = _rows_to_list(raw_results)
    tables = table_names or []

    # ----- Request clarification instead of guessing -----
    if _is_ambiguous(question, rows):
        return {
            "persona": persona,
            "persona_name": PERSONAS.get(persona, {}).get("name", persona.title()),
            "type": "clarification",
            "response": _build_clarification(persona, question, tables),
            "summary": "I need a bit more context before I can run the right analysis.",
        }

    # ----- QUERYBOT: standard passthrough -----
    if persona == "querybot":
        return {
            "persona": "querybot",
            "persona_name": "QueryBot",
            "type": "data",
            "summary": f"The query returned {len(rows)} row{'s' if len(rows) != 1 else ''}.",
        }

    # ----- VIZBOT: chart-ready structural analysis -----
    if persona == "vizbot":
        viz = _build_viz_schema(rows)
        return {
            "persona": "vizbot",
            "persona_name": "VizBot",
            "type": "viz",
            "viz_schema": viz,
            "summary": (
                f"VizBot structured {len(rows)} rows for charting. "
                f"Detected {len(viz.get('numeric_columns', []))} numeric, "
                f"{len(viz.get('label_columns', []))} label column(s). "
                f"Suggested chart: {', '.join(viz.get('suggested_chart_types', [])) or 'n/a'}."
            ),
        }

    # ----- ANOMALYBOT: hygiene + outlier audit -----
    if persona == "anomalybot":
        hygiene = _run_hygiene_check(rows)
        anomalies = _find_outliers(rows)
        return {
            "persona": "anomalybot",
            "persona_name": "AnomalyBot",
            "type": "anomaly",
            "hygiene": hygiene,
            "anomalies": anomalies,
            "summary": (
                f"AnomalyBot audited {len(rows)} rows: "
                f"{hygiene.get('null_count', 0)} null(s), "
                f"{hygiene.get('missing_fields', 0)} missing field(s), "
                f"{len(hygiene.get('type_issues', []))} type issue(s), "
                f"{len(anomalies)} potential outlier(s)."
            ),
        }

    # ----- DEEPBOT: statistical breakdown -----
    if persona == "deepbot":
        analysis = _run_deep_analysis(rows)
        return {
            "persona": "deepbot",
            "persona_name": "DeepBot",
            "type": "deep",
            "analysis": analysis,
            "summary": (
                f"DeepBot ran a statistical breakdown across "
                f"{analysis.get('column_count', 0)} columns from {len(rows)} rows."
            ),
        }

    # ----- INTERNETBOT: external context enrichment -----
    if persona == "internetbot":
        enrichment = {
            "external_sources": [],
            "benchmarks": [],
            "note": "InternetBot is ready to enrich results with external web context. "
                    "Configure a search provider to enable live enrichment.",
        }
        return {
            "persona": "internetbot",
            "persona_name": "InternetBot",
            "type": "internet",
            "enrichment": enrichment,
            "summary": f"InternetBot is ready to enrich {len(rows)} rows with external research.",
        }

    # ----- Fallback -----
    return {
        "persona": "querybot",
        "persona_name": "QueryBot",
        "type": "data",
        "summary": f"The query returned {len(rows)} row{'s' if len(rows) != 1 else ''}.",
    }


def _build_clarification(persona, question, tables):
    """Build a request_clarification-style message for the given persona."""
    name = PERSONAS.get(persona, {}).get("name", persona.title())
    if tables:
        return (
            f"{name}: I see {len(tables)} available dataset(s) — "
            f"{', '.join(tables[:3])}{'…' if len(tables) > 3 else ''}. "
            f"Could you tell me which table you'd like me to look into?"
        )
    return (
        f"{name}: I need a bit more context before I query. "
        f"What table or dataset should I analyze, and what metric matters most to you?"
    )


def _build_viz_schema(rows):
    """Detect numeric + label columns and suggest chart types."""
    if not rows:
        return {"label_columns": [], "numeric_columns": [], "suggested_chart_types": [], "sample_keys": []}

    keys = list(rows[0].keys())
    numeric_columns, label_columns = [], []

    for key in keys:
        is_numeric = False
        for row in rows:
            val = row.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                is_numeric = True
                break
            if isinstance(val, str):
                try:
                    float(val)
                    is_numeric = True
                    break
                except (ValueError, TypeError):
                    pass
        (numeric_columns if is_numeric else label_columns).append(key)

    chart_types = ["bar", "line"]
    if len(numeric_columns) > 1:
        chart_types.append("scatter")
    if len(label_columns) and len(numeric_columns):
        chart_types.append("pie")

    return {
        "label_columns": label_columns,
        "numeric_columns": numeric_columns,
        "suggested_chart_types": chart_types,
        "sample_keys": keys,
    }


def _run_hygiene_check(rows):
    """Audit results for nulls, missing fields, and type discrepancies."""
    if not rows:
        return {
            "status": "empty",
            "null_count": 0,
            "missing_fields": 0,
            "type_issues": [],
            "total_cells": 0,
            "null_ratio": 0,
        }

    null_count = missing_fields = total_cells = 0
    type_issues = []
    column_types = {}

    for row in rows:
        for key, val in row.items():
            total_cells += 1
            if val is None:
                null_count += 1
            else:
                vtype = type(val).__name__
                if key in column_types and column_types[key] != vtype:
                    type_issues.append({"column": key, "expected": column_types[key], "found": vtype})
                column_types.setdefault(key, vtype)

    first_keys = set(rows[0].keys())
    for row in rows[1:]:
        missing_fields += len(first_keys - set(row.keys()))

    null_ratio = round(null_count / total_cells, 4) if total_cells else 0
    status = "clean"
    if null_ratio > 0.05:
        status = "high_null_ratio"
    if type_issues:
        status = "type_mismatch" if status == "clean" else status + "_and_types"

    return {
        "status": status,
        "null_count": null_count,
        "null_ratio": null_ratio,
        "missing_fields": missing_fields,
        "type_issues": type_issues[:10],
        "total_cells": total_cells,
    }


def _find_outliers(rows):
    """Detect statistical outliers on numeric columns using simple IQR / z-style heuristic."""
    if not rows:
        return []

    outliers = []
    keys = list(rows[0].keys())

    for key in keys:
        vals = []
        for row in rows:
            v = row.get(key)
            try:
                vals.append(float(v))
            except (ValueError, TypeError):
                continue
        if len(vals) < 4:
            continue

        mean = sum(vals) / len(vals)
        variance = sum((x - mean) ** 2 for x in vals) / len(vals)
        std = variance ** 0.5
        if std == 0:
            continue

        threshold = 2.5  # z-score threshold
        for row in rows:
            v = row.get(key)
            try:
                fv = float(v)
            except (ValueError, TypeError):
                continue
            z = (fv - mean) / std
            if abs(z) > threshold:
                outliers.append({
                    "column": key,
                    "value": fv,
                    "z_score": round(z, 2),
                    "row_index": rows.index(row) + 1,
                })

    # De-duplicate by column+value
    seen = set()
    uniq = []
    for o in outliers:
        key = (o["column"], o["value"])
        if key not in seen:
            seen.add(key)
            uniq.append(o)
    return uniq[:10]


def _run_deep_analysis(rows):
    """Statistical breakdown: distributions, std, growth over numeric columns."""
    if not rows:
        return {"column_count": 0, "numeric_summary": {}, "text_summary": {}, "insights": []}

    keys = list(rows[0].keys())
    numeric_summary, text_summary = {}, {}
    insights = []

    for key in keys:
        values = [row.get(key) for row in rows if row.get(key) is not None]
        if not values:
            continue

        numeric_vals = []
        for v in values:
            try:
                numeric_vals.append(float(v))
            except (ValueError, TypeError):
                pass

        if numeric_vals and len(numeric_vals) == len(values):
            mean = sum(numeric_vals) / len(numeric_vals)
            variance = sum((x - mean) ** 2 for x in numeric_vals) / len(numeric_vals)
            std = variance ** 0.5
            numeric_summary[key] = {
                "count": len(numeric_vals),
                "min": min(numeric_vals),
                "max": max(numeric_vals),
                "mean": round(mean, 4),
                "std": round(std, 4),
                "median": round(sorted(numeric_vals)[len(numeric_vals) // 2], 4),
            }
            # Detect growth trend (first vs last)
            if len(numeric_vals) >= 2:
                first, last = numeric_vals[0], numeric_vals[-1]
                if first != 0:
                    growth = round((last - first) / abs(first) * 100, 2)
                    insights.append(f"{key}: {growth}% change from first to last.")
        else:
            count = Counter(str(v) for v in values)
            text_summary[key] = {"unique_values": len(count), "top_values": count.most_common(5)}

    return {
        "column_count": len(keys),
        "numeric_summary": numeric_summary,
        "text_summary": text_summary,
        "insights": insights[:10],
    }
