"""Клиенты."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..auth import permission_required
from ..db import get_db, query, scalar
from ..utils import fmt_phone, normalize_phone, now_str, parse_int, phone_digits, plural

bp = Blueprint("clients", __name__, url_prefix="/clients")

PER_PAGE = 25


@bp.route("/")
@permission_required("clients_view")
def index():
    q = (request.args.get("q") or "").strip()
    where, args = "", []
    if q:
        parts = ["py_lower(c.full_name) LIKE ?"]
        args.append(f"%{q.lower()}%")
        d = phone_digits(q)
        if len(d) >= 3:
            parts.append("digits(c.phone) LIKE ?")
            args.append(f"%{d}%")
        where = "WHERE " + " OR ".join(parts)
    total = scalar(f"SELECT COUNT(*) FROM clients c {where}", args)
    pages = max(1, -(-total // PER_PAGE))
    page = min(parse_int(request.args.get("page"), 1, 1) or 1, pages)
    rows = query(
        f"SELECT c.*, COUNT(o.id) AS orders_count, "
        f"COALESCE(SUM(CASE WHEN o.status != 'cancelled' THEN o.total END), 0) AS orders_sum, "
        f"MAX(o.created_at) AS last_order_at "
        f"FROM clients c LEFT JOIN orders o ON o.client_id = c.id {where} "
        f"GROUP BY c.id ORDER BY c.id DESC LIMIT ? OFFSET ?",
        (*args, PER_PAGE, (page - 1) * PER_PAGE),
    )
    return render_template("clients/list.html", rows=rows, q=q, total=total, page=page, pages=pages)


def _get_client(client_id: int):
    c = query("SELECT * FROM clients WHERE id = ?", (client_id,), one=True)
    if c is None:
        abort(404)
    return c


@bp.route("/<int:client_id>")
@permission_required("clients_view")
def detail(client_id):
    client = _get_client(client_id)
    orders = query(
        "SELECT o.*, (SELECT GROUP_CONCAT(t, ', ') FROM (SELECT DISTINCT furniture_type AS t FROM order_items "
        "WHERE order_id = o.id)) AS types FROM orders o WHERE o.client_id = ? ORDER BY o.created_at DESC",
        (client_id,),
    )
    stats = {
        "count": len(orders),
        "sum": sum(o["total"] for o in orders if o["status"] != "cancelled"),
        "active": sum(1 for o in orders if o["status"] in ("accepted", "production", "ready")),
    }
    return render_template("clients/detail.html", client=client, orders=orders, stats=stats)


def _validate(form):
    name = (form.get("full_name") or "").strip()
    phone_raw = (form.get("phone") or "").strip()
    comment = (form.get("comment") or "").strip()
    errors = {}
    if len(name) < 2:
        errors["full_name"] = "Укажите ФИО клиента."
    elif len(name) > 120:
        errors["full_name"] = "ФИО — не длиннее 120 символов."
    phone = normalize_phone(phone_raw)
    if not phone:
        errors["phone"] = "От 10 до 15 цифр. Допустимы +, пробелы, скобки и дефисы."
    if len(comment) > 1000:
        errors["comment"] = "Комментарий — не длиннее 1000 символов."
    return {"full_name": name, "phone": phone, "phone_raw": phone_raw, "comment": comment}, errors


@bp.route("/new", methods=["GET", "POST"])
@permission_required("clients_manage")
def create():
    data, errors = {"full_name": "", "phone_raw": "", "comment": ""}, {}
    if request.method == "POST":
        data, errors = _validate(request.form)
        if not errors:
            db = get_db()
            cur = db.execute("INSERT INTO clients (full_name, phone, comment, created_at) VALUES (?, ?, ?, ?)",
                             (data["full_name"], data["phone"], data["comment"] or None, now_str()))
            db.commit()
            flash(f"Клиент «{data['full_name']}» добавлен.", "success")
            if request.form.get("then") == "order":
                return redirect(url_for("orders.create", client_id=cur.lastrowid))
            return redirect(url_for("clients.detail", client_id=cur.lastrowid))
    return render_template("clients/form.html", data=data, errors=errors, client=None), (422 if errors else 200)


@bp.route("/<int:client_id>/edit", methods=["GET", "POST"])
@permission_required("clients_manage")
def edit(client_id):
    client = _get_client(client_id)
    data = {"full_name": client["full_name"], "phone_raw": fmt_phone(client["phone"]), "comment": client["comment"] or ""}
    errors = {}
    if request.method == "POST":
        data, errors = _validate(request.form)
        if not errors:
            db = get_db()
            db.execute("UPDATE clients SET full_name = ?, phone = ?, comment = ? WHERE id = ?",
                       (data["full_name"], data["phone"], data["comment"] or None, client_id))
            db.commit()
            flash("Изменения сохранены.", "success")
            return redirect(url_for("clients.detail", client_id=client_id))
    return render_template("clients/form.html", data=data, errors=errors, client=client), (422 if errors else 200)


@bp.route("/<int:client_id>/delete", methods=["POST"])
@permission_required("clients_manage")
def delete(client_id):
    client = _get_client(client_id)
    n = scalar("SELECT COUNT(*) FROM orders WHERE client_id = ?", (client_id,))
    if n:
        flash(f"Клиента «{client['full_name']}» нельзя удалить: у него {n} {plural(n, 'заказ', 'заказа', 'заказов')}. "
              f"Сначала удалите или перенесите заказы.", "error")
        if request.form.get("back") == "detail":
            return redirect(url_for("clients.detail", client_id=client_id))
        return redirect(url_for("clients.index"))
    db = get_db()
    db.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    db.commit()
    flash(f"Клиент «{client['full_name']}» удалён.", "success")
    return redirect(url_for("clients.index"))
