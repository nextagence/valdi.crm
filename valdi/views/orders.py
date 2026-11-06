"""Заказы: список, карточка, создание, редактирование, статусы."""
from __future__ import annotations

from itertools import zip_longest

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template, request,
                   url_for)

from ..auth import permission_required
from ..db import get_db, query, scalar
from ..utils import (FURNITURE_TYPES, STATUSES, fmt_phone, normalize_phone, now, now_str,
                     parse_date, parse_int, parse_money, phone_digits, today)

bp = Blueprint("orders", __name__, url_prefix="/orders")

PER_PAGE = 20
SORTS = {
    "created": "o.created_at",
    "total": "o.total",
    "id": "o.id",
    "delivery": "o.delivery_date",
    "client": "c.full_name",
}
SORT_LABELS = [("created", "Дата создания"), ("delivery", "Дата доставки"),
               ("total", "Стоимость"), ("id", "Номер заказа"), ("client", "Клиент")]

TYPES_SUBQ = ("(SELECT GROUP_CONCAT(t, ', ') FROM (SELECT DISTINCT furniture_type AS t "
              "FROM order_items WHERE order_id = o.id ORDER BY position)) AS types")


# ---------------------------------------------------------------- список

@bp.route("/")
@permission_required("orders_view")
def index():
    a = request.args
    f = {
        "number": (a.get("number") or "").strip().lstrip("#№"),
        "client": (a.get("client") or "").strip(),
        "phone": (a.get("phone") or "").strip(),
        "type": a.get("type") or "",
        "status": a.get("status") or "",
        "date_from": a.get("date_from") or "",
        "date_to": a.get("date_to") or "",
        "sort": a.get("sort") if a.get("sort") in SORTS else "created",
        "dir": "asc" if a.get("dir") == "asc" else "desc",
    }
    where, args = [], []
    if f["number"]:
        n = parse_int(f["number"])
        where.append("o.id = ?")
        args.append(n if n is not None else -1)
    if f["client"]:
        where.append("py_lower(c.full_name) LIKE ?")
        args.append(f"%{f['client'].lower()}%")
    if f["phone"]:
        d = phone_digits(f["phone"])
        if d:
            where.append("digits(c.phone) LIKE ?")
            args.append(f"%{d}%")
    if f["type"] in FURNITURE_TYPES:
        where.append("EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = o.id AND i.furniture_type = ?)")
        args.append(f["type"])
    if f["status"] in STATUSES:
        where.append("o.status = ?")
        args.append(f["status"])
    elif f["status"] == "active":
        where.append("o.status IN ('accepted', 'production', 'ready')")
    elif f["status"] == "overdue":
        where.append("o.status IN ('accepted', 'production', 'ready') AND o.delivery_date IS NOT NULL AND o.delivery_date < ?")
        args.append(today().isoformat())
    df, dt = parse_date(f["date_from"]), parse_date(f["date_to"])
    if df:
        where.append("o.created_at >= ?")
        args.append(df.isoformat() + " 00:00:00")
    if dt:
        where.append("o.created_at <= ?")
        args.append(dt.isoformat() + " 23:59:59")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    total_count = scalar(f"SELECT COUNT(*) FROM orders o JOIN clients c ON c.id = o.client_id {where_sql}", args)
    sum_total = scalar(
        f"SELECT COALESCE(SUM(o.total), 0) FROM orders o JOIN clients c ON c.id = o.client_id "
        f"{where_sql} {'AND' if where_sql else 'WHERE'} o.status != 'cancelled'", args)
    pages = max(1, -(-total_count // PER_PAGE))
    page = parse_int(a.get("page"), 1, 1) or 1
    page = min(page, pages)

    order_sql = f"{SORTS[f['sort']]} {f['dir'].upper()}"
    if f["sort"] == "delivery":
        order_sql = f"o.delivery_date IS NULL, {order_sql}"
    rows = query(
        f"SELECT o.*, c.full_name AS client, c.phone AS client_phone, {TYPES_SUBQ} "
        f"FROM orders o JOIN clients c ON c.id = o.client_id {where_sql} "
        f"ORDER BY {order_sql}, o.id DESC LIMIT ? OFFSET ?",
        (*args, PER_PAGE, (page - 1) * PER_PAGE),
    )
    has_filters = any(f[k] for k in ("number", "client", "phone", "type", "status", "date_from", "date_to"))
    return render_template("orders/list.html", rows=rows, f=f, total_count=total_count, sum_total=sum_total,
                           page=page, pages=pages, sort_labels=SORT_LABELS, has_filters=has_filters)


# ---------------------------------------------------------------- карточка

def _get_order(order_id: int):
    order = query(
        "SELECT o.*, c.full_name AS client, c.phone AS client_phone, c.comment AS client_comment, "
        "u.full_name AS author FROM orders o JOIN clients c ON c.id = o.client_id "
        "LEFT JOIN users u ON u.id = o.created_by WHERE o.id = ?",
        (order_id,), one=True,
    )
    if order is None:
        abort(404)
    return order


def _get_items(order_id: int):
    return query("SELECT * FROM order_items WHERE order_id = ? ORDER BY position", (order_id,))


@bp.route("/<int:order_id>")
@permission_required("orders_view")
def detail(order_id):
    order = _get_order(order_id)
    items = _get_items(order_id)
    history = query(
        "SELECT h.*, u.full_name AS user FROM order_history h LEFT JOIN users u ON u.id = h.user_id "
        "WHERE h.order_id = ? ORDER BY h.created_at DESC, h.id DESC",
        (order_id,),
    )
    qty_total = sum(i["qty"] for i in items)
    return render_template("orders/detail.html", order=order, items=items, history=history, qty_total=qty_total)


# ---------------------------------------------------------------- форма

def _blank_item():
    return {"furniture_type": FURNITURE_TYPES[0], "name": "", "width": "", "height": "", "depth": "",
            "qty": "1", "unit_price": ""}


def _parse_form(form, is_new: bool):
    """Разбор и проверка формы заказа. Возвращает (data, items, errors, raw)."""
    errors: dict[str, str] = {}
    item_errors: list[str] = []
    data: dict = {}

    mode = form.get("client_mode", "existing")
    data["client_mode"] = mode if mode in ("existing", "new") else "existing"
    if data["client_mode"] == "existing":
        cid = parse_int(form.get("client_id"))
        client = query("SELECT id, full_name, phone FROM clients WHERE id = ?", (cid,), one=True) if cid else None
        if client is None:
            errors["client_id"] = "Выберите клиента из базы или переключитесь на «Новый клиент»."
        data["client_id"] = cid
        data["client"] = client
    else:
        name = (form.get("new_client_name") or "").strip()
        phone_raw = (form.get("new_client_phone") or "").strip()
        phone = normalize_phone(phone_raw)
        if len(name) < 2:
            errors["new_client_name"] = "Укажите ФИО клиента."
        elif len(name) > 120:
            errors["new_client_name"] = "ФИО — не длиннее 120 символов."
        if not phone:
            errors["new_client_phone"] = "Телефон: от 10 до 15 цифр. Допустимы +, пробелы, скобки и дефисы."
        data["new_client"] = {"name": name, "phone": phone, "phone_raw": phone_raw,
                              "comment": (form.get("new_client_comment") or "").strip()[:1000]}

    # --- позиции
    raw_items, items = [], []
    cols = [form.getlist(k) for k in ("item_type", "item_name", "item_w", "item_h", "item_d", "item_qty", "item_price")]
    for idx, (ftype, name, w, h, d, qty, price) in enumerate(zip_longest(*cols, fillvalue=""), start=1):
        raw = {"furniture_type": ftype, "name": (name or "").strip(), "width": (w or "").strip(),
               "height": (h or "").strip(), "depth": (d or "").strip(), "qty": (qty or "").strip(),
               "unit_price": (price or "").strip()}
        if not any([raw["name"], raw["width"], raw["height"], raw["depth"], raw["unit_price"]]):
            continue  # пустая строка — пропускаем
        raw_items.append(raw)
        n = len(raw_items)
        row_errors = []
        if ftype not in FURNITURE_TYPES:
            row_errors.append("выберите тип мебели")
        if len(raw["name"]) > 200:
            row_errors.append("описание длиннее 200 символов")
        dims = {}
        for key, label in (("width", "ширина"), ("height", "высота"), ("depth", "глубина")):
            if raw[key] == "":
                dims[key] = None
                continue
            v = parse_int(raw[key], min_value=1, max_value=100000)
            if v is None:
                row_errors.append(f"{label} — целое число мм от 1 до 100 000")
            dims[key] = v
        q = parse_int(raw["qty"] or "1", min_value=1, max_value=9999)
        if q is None:
            row_errors.append("количество — целое число от 1")
        p = parse_money(raw["unit_price"])
        if p is None:
            row_errors.append("укажите цену за единицу")
        if row_errors:
            item_errors.append(f"Позиция {n}: " + ", ".join(row_errors) + ".")
            continue
        items.append({"furniture_type": ftype, "name": raw["name"] or None, **dims,
                      "qty": q, "unit_price": p, "line_total": p * q})
    if not raw_items:
        item_errors.append("Добавьте хотя бы одну позицию с ценой.")
    if item_errors:
        errors["items"] = " ".join(item_errors)

    # --- доп. информация
    data["comment"] = (form.get("comment") or "").strip()[:2000]
    dd_raw = form.get("delivery_date") or ""
    data["delivery_date"] = parse_date(dd_raw)
    if dd_raw and data["delivery_date"] is None:
        errors["delivery_date"] = "Неверная дата."
    cd_raw = form.get("created_date") or ""
    data["created_date"] = parse_date(cd_raw)
    if cd_raw and data["created_date"] is None:
        errors["created_date"] = "Неверная дата."

    status = form.get("status") or "accepted"
    if status not in STATUSES:
        errors["status"] = "Выберите статус."
        status = "accepted"
    data["status"] = status
    data["status_comment"] = (form.get("status_comment") or "").strip()[:500]

    raw = {
        "client_mode": data["client_mode"],
        "client_id": form.get("client_id") or "",
        "new_client_name": form.get("new_client_name") or "",
        "new_client_phone": form.get("new_client_phone") or "",
        "new_client_comment": form.get("new_client_comment") or "",
        "comment": data["comment"],
        "delivery_date": dd_raw,
        "created_date": cd_raw,
        "status": status,
        "status_comment": data["status_comment"],
    }
    return data, items, errors, (raw_items or [_blank_item()]), raw


def _client_for_form(raw):
    cid = parse_int(raw.get("client_id"))
    if not cid:
        return None
    c = query("SELECT id, full_name, phone FROM clients WHERE id = ?", (cid,), one=True)
    return {"id": c["id"], "name": c["full_name"], "phone": fmt_phone(c["phone"])} if c else None


def _recent_clients():
    rows = query("SELECT id, full_name, phone FROM clients ORDER BY created_at DESC, id DESC LIMIT 8")
    return [{"id": r["id"], "name": r["full_name"], "phone": fmt_phone(r["phone"])} for r in rows]


def _save_items(db, order_id, items):
    db.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
    for pos, it in enumerate(items, start=1):
        db.execute(
            "INSERT INTO order_items (order_id, position, furniture_type, name, width, height, depth, qty, unit_price, line_total) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (order_id, pos, it["furniture_type"], it["name"], it["width"], it["height"], it["depth"],
             it["qty"], it["unit_price"], it["line_total"]),
        )


def _resolve_client(db, data):
    if data["client_mode"] == "existing":
        return data["client_id"]
    nc = data["new_client"]
    cur = db.execute("INSERT INTO clients (full_name, phone, comment, created_at) VALUES (?, ?, ?, ?)",
                     (nc["name"], nc["phone"], nc["comment"] or None, now_str()))
    return cur.lastrowid


@bp.route("/new", methods=["GET", "POST"])
@permission_required("orders_manage")
def create():
    if request.method == "POST":
        data, items, errors, raw_items, raw = _parse_form(request.form, is_new=True)
        if not errors:
            db = get_db()
            try:
                client_id = _resolve_client(db, data)
                ts = now()
                created_at = (f"{data['created_date'].isoformat()} {ts.strftime('%H:%M:%S')}"
                              if data["created_date"] else ts.strftime("%Y-%m-%d %H:%M:%S"))
                total = sum(i["line_total"] for i in items)
                cur = db.execute(
                    "INSERT INTO orders (client_id, status, comment, delivery_date, total, created_at, updated_at, created_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (client_id, data["status"], data["comment"] or None,
                     data["delivery_date"].isoformat() if data["delivery_date"] else None,
                     total, created_at, now_str(), g.user.id),
                )
                order_id = cur.lastrowid
                _save_items(db, order_id, items)
                db.execute("INSERT INTO order_history (order_id, kind, status, comment, user_id, created_at) "
                           "VALUES (?, 'created', ?, ?, ?, ?)",
                           (order_id, data["status"], data["status_comment"] or None, g.user.id, now_str()))
                db.commit()
            except Exception:
                db.rollback()
                raise
            flash(f"Заказ №{order_id} создан.", "success")
            return redirect(url_for("orders.detail", order_id=order_id))
        flash("Заказ не сохранён — исправьте отмеченные поля.", "error")
        return render_template("orders/form.html", is_new=True, raw=raw, items=raw_items, errors=errors,
                               selected_client=_client_for_form(raw), recent_clients=_recent_clients()), 422

    # GET: можно открыть сразу с клиентом — /orders/new?client_id=3
    raw = {"client_mode": "existing", "client_id": request.args.get("client_id", ""), "status": "accepted",
           "new_client_name": "", "new_client_phone": "", "new_client_comment": "", "comment": "",
           "delivery_date": "", "created_date": "", "status_comment": ""}
    return render_template("orders/form.html", is_new=True, raw=raw, items=[_blank_item()], errors={},
                           selected_client=_client_for_form(raw), recent_clients=_recent_clients())


@bp.route("/<int:order_id>/edit", methods=["GET", "POST"])
@permission_required("orders_manage")
def edit(order_id):
    order = _get_order(order_id)
    if request.method == "POST":
        data, items, errors, raw_items, raw = _parse_form(request.form, is_new=False)
        if not errors:
            db = get_db()
            try:
                client_id = _resolve_client(db, data)
                created_at = order["created_at"]
                if data["created_date"] and data["created_date"].isoformat() != created_at[:10]:
                    created_at = data["created_date"].isoformat() + created_at[10:]
                total = sum(i["line_total"] for i in items)
                db.execute(
                    "UPDATE orders SET client_id = ?, status = ?, comment = ?, delivery_date = ?, total = ?, "
                    "created_at = ?, updated_at = ? WHERE id = ?",
                    (client_id, data["status"], data["comment"] or None,
                     data["delivery_date"].isoformat() if data["delivery_date"] else None,
                     total, created_at, now_str(), order_id),
                )
                _save_items(db, order_id, items)
                db.execute("INSERT INTO order_history (order_id, kind, status, comment, user_id, created_at) "
                           "VALUES (?, 'edited', ?, NULL, ?, ?)", (order_id, data["status"], g.user.id, now_str()))
                if data["status"] != order["status"]:
                    db.execute("INSERT INTO order_history (order_id, kind, status, comment, user_id, created_at) "
                               "VALUES (?, 'status', ?, ?, ?, ?)",
                               (order_id, data["status"], data["status_comment"] or None, g.user.id, now_str()))
                db.commit()
            except Exception:
                db.rollback()
                raise
            flash("Изменения сохранены.", "success")
            return redirect(url_for("orders.detail", order_id=order_id))
        flash("Изменения не сохранены — исправьте отмеченные поля.", "error")
        return render_template("orders/form.html", is_new=False, order=order, raw=raw, items=raw_items,
                               errors=errors, selected_client=_client_for_form(raw),
                               recent_clients=_recent_clients()), 422

    items = [{"furniture_type": i["furniture_type"], "name": i["name"] or "",
              "width": i["width"] or "", "height": i["height"] or "", "depth": i["depth"] or "",
              "qty": str(i["qty"]), "unit_price": _money_raw(i["unit_price"])} for i in _get_items(order_id)]
    raw = {"client_mode": "existing", "client_id": str(order["client_id"]), "status": order["status"],
           "new_client_name": "", "new_client_phone": "", "new_client_comment": "",
           "comment": order["comment"] or "", "delivery_date": order["delivery_date"] or "",
           "created_date": order["created_at"][:10], "status_comment": ""}
    return render_template("orders/form.html", is_new=False, order=order, raw=raw, items=items or [_blank_item()],
                           errors={}, selected_client=_client_for_form(raw), recent_clients=_recent_clients())


def _money_raw(cents):
    whole, frac = divmod(int(cents or 0), 100)
    return f"{whole}.{frac:02d}" if frac else str(whole)


@bp.route("/<int:order_id>/status", methods=["POST"])
@permission_required("orders_manage")
def change_status(order_id):
    order = _get_order(order_id)
    status = request.form.get("status")
    comment = (request.form.get("comment") or "").strip()[:500]
    if status not in STATUSES:
        flash("Выберите статус из списка.", "error")
    elif status == order["status"] and not comment:
        flash("Статус не изменился.", "info")
    else:
        db = get_db()
        db.execute("UPDATE orders SET status = ?, updated_at = ? WHERE id = ?", (status, now_str(), order_id))
        db.execute("INSERT INTO order_history (order_id, kind, status, comment, user_id, created_at) "
                   "VALUES (?, 'status', ?, ?, ?, ?)", (order_id, status, comment or None, g.user.id, now_str()))
        db.commit()
        flash(f"Статус изменён: «{STATUSES[status]}».", "success")
    return redirect(url_for("orders.detail", order_id=order_id))


@bp.route("/<int:order_id>/delete", methods=["POST"])
@permission_required("orders_manage")
def delete(order_id):
    _get_order(order_id)
    db = get_db()
    db.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    db.commit()
    flash(f"Заказ №{order_id} удалён.", "success")
    return redirect(url_for("orders.index"))


# ---------------------------------------------------------------- поиск клиентов для формы

@bp.route("/client-search.json")
@permission_required("orders_manage")
def client_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify(_recent_clients())
    d = phone_digits(q)
    sql = "SELECT id, full_name, phone FROM clients WHERE py_lower(full_name) LIKE ?"
    args = [f"%{q.lower()}%"]
    if len(d) >= 3:
        sql += " OR digits(phone) LIKE ?"
        args.append(f"%{d}%")
    rows = query(sql + " ORDER BY full_name LIMIT 20", args)
    return jsonify([{"id": r["id"], "name": r["full_name"], "phone": fmt_phone(r["phone"])} for r in rows])
