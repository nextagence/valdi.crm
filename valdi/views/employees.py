"""Сотрудники и права доступа."""
from __future__ import annotations

import re

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash

from ..auth import PERMISSION_KEYS, PERMISSION_LABELS, ROLE_DEFAULTS, ROLES, permission_required
from ..db import get_db, query, scalar
from ..utils import fmt_phone, normalize_phone, now_str

bp = Blueprint("employees", __name__, url_prefix="/employees")

LOGIN_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _get_user(user_id: int):
    u = query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    if u is None:
        abort(404)
    return u


def _guard_admin_target(target):
    """Учётные записи администраторов может менять только администратор."""
    if target["role"] == "admin" and not g.user.is_admin:
        abort(403)


def _active_admins(exclude_id=None) -> int:
    sql = "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1"
    args = []
    if exclude_id is not None:
        sql += " AND id != ?"
        args.append(exclude_id)
    return scalar(sql, args)


@bp.route("/")
@permission_required("employees_manage")
def index():
    rows = query("SELECT * FROM users ORDER BY CASE role WHEN 'admin' THEN 0 WHEN 'manager' THEN 1 ELSE 2 END, full_name")
    users = []
    for r in rows:
        perms = [p for p in (r["permissions"] or "").split(",") if p in PERMISSION_LABELS]
        # «управление» включает «просмотр» — показываем только старшее право
        shown = [p for p in PERMISSION_KEYS if p in perms
                 and not (p.endswith("_view") and p.replace("_view", "_manage") in perms)]
        users.append({**dict(r), "perm_labels": [PERMISSION_LABELS[p] for p in shown]})
    return render_template("employees/list.html", users=users)


def _validate(form, user=None):
    is_new = user is None
    errors = {}
    d = {
        "full_name": (form.get("full_name") or "").strip(),
        "phone_raw": (form.get("phone") or "").strip(),
        "email": (form.get("email") or "").strip(),
        "login": (form.get("login") or "").strip(),
        "role": form.get("role") or "employee",
        "permissions": [p for p in form.getlist("permissions") if p in PERMISSION_KEYS],
        "is_active": form.get("is_active") == "1",
        "password": form.get("password") or "",
        "password2": form.get("password2") or "",
    }
    if len(d["full_name"]) < 2:
        errors["full_name"] = "Укажите ФИО или должность."
    elif len(d["full_name"]) > 120:
        errors["full_name"] = "Не длиннее 120 символов."
    d["phone"] = None
    if d["phone_raw"]:
        d["phone"] = normalize_phone(d["phone_raw"])
        if not d["phone"]:
            errors["phone"] = "От 10 до 15 цифр. Допустимы +, пробелы, скобки и дефисы."
    if d["email"] and (len(d["email"]) > 120 or not EMAIL_RE.match(d["email"])):
        errors["email"] = "Проверьте адрес: должен быть вида name@company.kz."
    if not LOGIN_RE.match(d["login"]):
        errors["login"] = "3–32 символа: латинские буквы, цифры, точка, дефис, подчёркивание."
    else:
        exists = query("SELECT id FROM users WHERE login = ? AND id != ?", (d["login"], user["id"] if user else 0), one=True)
        if exists:
            errors["login"] = "Этот логин уже занят."
    if d["role"] not in ROLES:
        errors["role"] = "Выберите роль."
    elif d["role"] == "admin" and not g.user.is_admin:
        errors["role"] = "Назначить администратора может только администратор."

    if is_new or d["password"] or d["password2"]:
        if len(d["password"]) < 8:
            errors["password"] = "Минимум 8 символов."
        elif d["password"] != d["password2"]:
            errors["password2"] = "Пароли не совпадают."

    if user is not None:
        if user["id"] == g.user.id and not d["is_active"]:
            errors["is_active"] = "Нельзя отключить собственную учётную запись."
            d["is_active"] = True
        if user["role"] == "admin" and (d["role"] != "admin" or not d["is_active"]) and _active_admins(exclude_id=user["id"]) == 0:
            errors["role"] = "Это последний активный администратор — сначала назначьте другого."
    return d, errors


def _form_defaults():
    return {"full_name": "", "phone_raw": "", "email": "", "login": "", "role": "employee",
            "permissions": ROLE_DEFAULTS["employee"][:], "is_active": True}


@bp.route("/new", methods=["GET", "POST"])
@permission_required("employees_manage")
def create():
    data, errors = _form_defaults(), {}
    if request.method == "POST":
        data, errors = _validate(request.form)
        if not errors:
            db = get_db()
            db.execute(
                "INSERT INTO users (full_name, phone, email, login, password_hash, role, permissions, is_active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (data["full_name"], data["phone"], data["email"] or None, data["login"],
                 generate_password_hash(data["password"]), data["role"],
                 ",".join(data["permissions"]) if data["role"] != "admin" else "",
                 1 if data["is_active"] else 0, now_str()),
            )
            db.commit()
            flash(f"Сотрудник «{data['full_name']}» добавлен. Логин: {data['login']}.", "success")
            return redirect(url_for("employees.index"))
    return render_template("employees/form.html", data=data, errors=errors, user=None,
                           role_defaults=ROLE_DEFAULTS), (422 if errors else 200)


@bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@permission_required("employees_manage")
def edit(user_id):
    user = _get_user(user_id)
    _guard_admin_target(user)
    data = {"full_name": user["full_name"], "phone_raw": fmt_phone(user["phone"]) if user["phone"] else "", "email": user["email"] or "",
            "login": user["login"], "role": user["role"],
            "permissions": [p for p in (user["permissions"] or "").split(",") if p],
            "is_active": bool(user["is_active"])}
    errors = {}
    if request.method == "POST":
        data, errors = _validate(request.form, user)
        if not errors:
            db = get_db()
            db.execute(
                "UPDATE users SET full_name = ?, phone = ?, email = ?, login = ?, role = ?, permissions = ?, is_active = ? "
                "WHERE id = ?",
                (data["full_name"], data["phone"], data["email"] or None, data["login"], data["role"],
                 ",".join(data["permissions"]) if data["role"] != "admin" else "",
                 1 if data["is_active"] else 0, user_id),
            )
            if data["password"]:
                new_hash = generate_password_hash(data["password"])
                db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
                if user_id == g.user.id:  # не выкидываем себя после смены пароля
                    session["pwm"] = new_hash[-16:]
            db.commit()
            flash("Изменения сохранены.", "success")
            return redirect(url_for("employees.index"))
    return render_template("employees/form.html", data=data, errors=errors, user=user,
                           role_defaults=ROLE_DEFAULTS), (422 if errors else 200)


@bp.route("/<int:user_id>/toggle", methods=["POST"])
@permission_required("employees_manage")
def toggle(user_id):
    user = _get_user(user_id)
    _guard_admin_target(user)
    if user["id"] == g.user.id:
        flash("Нельзя отключить собственную учётную запись.", "error")
    elif user["is_active"] and user["role"] == "admin" and _active_admins(exclude_id=user_id) == 0:
        flash("Это последний активный администратор.", "error")
    else:
        new = 0 if user["is_active"] else 1
        db = get_db()
        db.execute("UPDATE users SET is_active = ? WHERE id = ?", (new, user_id))
        db.commit()
        flash(f"«{user['full_name']}»: доступ {'включён' if new else 'отключён'}.", "success")
    return redirect(url_for("employees.index"))


@bp.route("/<int:user_id>/delete", methods=["POST"])
@permission_required("employees_manage")
def delete(user_id):
    user = _get_user(user_id)
    _guard_admin_target(user)
    if user["id"] == g.user.id:
        flash("Нельзя удалить собственную учётную запись.", "error")
    elif user["role"] == "admin" and _active_admins(exclude_id=user_id) == 0:
        flash("Это последний активный администратор.", "error")
    else:
        db = get_db()
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        db.commit()
        flash(f"Сотрудник «{user['full_name']}» удалён. История его действий сохранена.", "success")
    return redirect(url_for("employees.index"))
