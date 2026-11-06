"""Главная панель."""
from __future__ import annotations

import math
from datetime import timedelta

from flask import Blueprint, render_template, request

from ..auth import permission_required
from ..db import query, scalar
from ..utils import (ACTIVE_STATUSES, DONE_STATUSES, STATUSES, month_keys, period_start,
                     today)

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")

PERIODS = [("7", "7 дней"), ("30", "30 дней"), ("90", "90 дней"), ("year", "Год"), ("all", "Всё время")]
STATUS_ORDER = ["accepted", "production", "ready", "delivered", "cancelled"]


def _ph(n: int) -> str:
    return ",".join("?" * n)


@bp.route("/")
@permission_required("dashboard")
def index():
    period = request.args.get("period", "all")
    if period not in dict(PERIODS):
        period = "all"
    start = period_start(period)
    where, args = ("WHERE o.created_at >= ?", [start]) if start else ("WHERE 1=1", [])

    # --- KPI
    new_clients = scalar(
        "SELECT COUNT(*) FROM clients" + (" WHERE created_at >= ?" if start else ""),
        [start] if start else [],
    )
    orders_count = scalar(f"SELECT COUNT(*) FROM orders o {where}", args)
    rev = query(
        f"SELECT COALESCE(SUM(total), 0) AS s, COUNT(*) AS c FROM orders o {where} AND o.status != 'cancelled'",
        args, one=True,
    )
    revenue, paid_count = rev["s"], rev["c"]
    avg_check = revenue // paid_count if paid_count else 0

    today_s = today().isoformat()
    in_work = scalar(f"SELECT COUNT(*) FROM orders WHERE status IN ({_ph(3)})", ACTIVE_STATUSES)
    overdue = scalar(
        f"SELECT COUNT(*) FROM orders WHERE status IN ({_ph(3)}) AND delivery_date IS NOT NULL AND delivery_date < ?",
        (*ACTIVE_STATUSES, today_s),
    )

    # --- выручка по месяцам (последние 6)
    months = month_keys(6)
    rows = query(
        "SELECT substr(created_at, 1, 7) AS ym, SUM(total) AS s FROM orders "
        "WHERE status != 'cancelled' AND created_at >= ? GROUP BY ym",
        (months[0][0] + "-01 00:00:00",),
    )
    by_month = {r["ym"]: r["s"] or 0 for r in rows}
    chart_max = max([by_month.get(k, 0) for k, _ in months] + [1])
    chart = [
        {"key": k, "label": lbl, "value": by_month.get(k, 0),
         "pct": round(by_month.get(k, 0) / chart_max * 100, 1), "current": i == len(months) - 1}
        for i, (k, lbl) in enumerate(months)
    ]
    chart_total = sum(c["value"] for c in chart)

    # --- статусы за период
    counts = {r["status"]: r["c"] for r in query(
        f"SELECT o.status, COUNT(*) AS c FROM orders o {where} GROUP BY o.status", args)}
    status_total = sum(counts.values())
    done = sum(counts.get(s, 0) for s in DONE_STATUSES)
    done_pct = round(done / status_total * 100) if status_total else 0
    # Сегменты кольцевой диаграммы (r=52 → длина окружности)
    circ = 2 * math.pi * 52
    segments, offset = [], 0.0
    for key in STATUS_ORDER:
        c = counts.get(key, 0)
        length = c / status_total * circ if status_total else 0
        segments.append({"key": key, "label": STATUSES[key], "count": c,
                         "pct": round(c / status_total * 100) if status_total else 0,
                         "dash": f"{max(length - 2, 0):.2f} {circ:.2f}", "offset": f"{-offset:.2f}"})
        offset += length

    # --- категории
    categories = query(
        f"SELECT i.furniture_type AS name, SUM(i.line_total) AS s, COUNT(DISTINCT i.order_id) AS orders "
        f"FROM order_items i JOIN orders o ON o.id = i.order_id {where} AND o.status != 'cancelled' "
        f"GROUP BY i.furniture_type ORDER BY s DESC LIMIT 6",
        args,
    )
    cat_max = max([c["s"] for c in categories] + [1])

    # --- требуют внимания: просроченные и ближайшие 3 дня
    horizon = (today() + timedelta(days=3)).isoformat()
    attention = query(
        f"SELECT o.id, o.delivery_date, o.status, o.total, c.full_name AS client "
        f"FROM orders o JOIN clients c ON c.id = o.client_id "
        f"WHERE o.status IN ({_ph(3)}) AND o.delivery_date IS NOT NULL AND o.delivery_date <= ? "
        f"ORDER BY o.delivery_date ASC, o.id ASC LIMIT 8",
        (*ACTIVE_STATUSES, horizon),
    )

    # --- последние заказы и лента
    recent = query(
        "SELECT o.id, o.total, o.status, o.created_at, c.full_name AS client, "
        "(SELECT GROUP_CONCAT(t, ', ') FROM (SELECT DISTINCT furniture_type AS t FROM order_items WHERE order_id = o.id)) AS types "
        "FROM orders o JOIN clients c ON c.id = o.client_id ORDER BY o.created_at DESC, o.id DESC LIMIT 6"
    )
    activity = query(
        "SELECT h.order_id, h.kind, h.status, h.comment, h.created_at, u.full_name AS user "
        "FROM order_history h LEFT JOIN users u ON u.id = h.user_id "
        "WHERE h.kind IN ('created', 'status') ORDER BY h.created_at DESC, h.id DESC LIMIT 8"
    )

    return render_template(
        "dashboard.html",
        periods=PERIODS, period=period,
        kpi={"new_clients": new_clients, "orders": orders_count, "revenue": revenue,
             "avg_check": avg_check, "in_work": in_work, "overdue": overdue},
        chart=chart, chart_total=chart_total,
        segments=segments, status_total=status_total, done_pct=done_pct,
        categories=categories, cat_max=cat_max,
        attention=attention, recent=recent, activity=activity,
    )
