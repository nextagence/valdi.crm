"""Авторизация, роли, права доступа, CSRF."""
from __future__ import annotations

import hmac
import secrets
import time
from collections import defaultdict, deque
from functools import wraps
from threading import Lock

from flask import (Blueprint, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash

from .db import get_db, query
from .utils import now_str

bp = Blueprint("auth", __name__)

# ---------------------------------------------------------------- роли и права

ROLES = {
    "admin": "Администратор",
    "manager": "Руководитель",
    "employee": "Сотрудник",
}

PERMISSIONS = [
    ("dashboard", "Главная панель"),
    ("clients_view", "Клиенты: просмотр"),
    ("clients_manage", "Клиенты: управление"),
    ("orders_view", "Заказы: просмотр"),
    ("orders_manage", "Заказы: управление"),
    ("employees_manage", "Сотрудники: управление"),
]
PERMISSION_KEYS = [k for k, _ in PERMISSIONS]
PERMISSION_LABELS = dict(PERMISSIONS)

# Предзаполнение галочек при выборе роли в форме
ROLE_DEFAULTS = {
    "manager": PERMISSION_KEYS[:],
    "employee": ["dashboard", "clients_view", "orders_view"],
}


class User:
    __slots__ = ("id", "full_name", "login", "role", "perms", "pw_marker")

    def __init__(self, row):
        self.id = row["id"]
        self.full_name = row["full_name"]
        self.login = row["login"]
        self.role = row["role"]
        self.perms = set(filter(None, (row["permissions"] or "").split(",")))
        self.pw_marker = row["password_hash"][-16:]

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def role_label(self) -> str:
        return ROLES.get(self.role, self.role)

    def can(self, perm: str) -> bool:
        if self.is_admin:
            return True
        if perm in self.perms:
            return True
        # управление включает просмотр
        if perm.endswith("_view") and perm.replace("_view", "_manage") in self.perms:
            return True
        return False

    def home_endpoint(self) -> str | None:
        for perm, endpoint in (
            ("dashboard", "dashboard.index"),
            ("orders_view", "orders.index"),
            ("clients_view", "clients.index"),
            ("employees_manage", "employees.index"),
        ):
            if self.can(perm):
                return endpoint
        return None


def load_user():
    g.user = None
    uid = session.get("uid")
    if not uid:
        return
    row = query("SELECT * FROM users WHERE id = ?", (uid,), one=True)
    # Сессия сбрасывается, если сотрудника отключили, удалили или сменили пароль
    if not row or not row["is_active"] or row["password_hash"][-16:] != session.get("pwm"):
        session.clear()
        return
    g.user = User(row)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path if request.method == "GET" else None))
        return view(*args, **kwargs)
    return wrapped


def permission_required(*perms):
    """Нужно хотя бы одно из прав."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not any(g.user.can(p) for p in perms):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ---------------------------------------------------------------- CSRF


def csrf_token() -> str:
    tok = session.get("_csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["_csrf"] = tok
    return tok


def check_csrf():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token", "")
        if not sent or not hmac.compare_digest(sent, session.get("_csrf", "")):
            abort(400, description="Сессия устарела. Обновите страницу и повторите действие.")


# ---------------------------------------------------------------- защита от перебора

_FAILS: dict[str, deque] = defaultdict(deque)
_FAILS_LOCK = Lock()
MAX_FAILS = 5
WINDOW = 10 * 60


def _throttle_key(login: str) -> str:
    return f"{request.remote_addr}|{login.lower()}"


def _blocked_for(key: str) -> int:
    with _FAILS_LOCK:
        q = _FAILS[key]
        cutoff = time.time() - WINDOW
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= MAX_FAILS:
            return int(q[0] + WINDOW - time.time()) + 1
    return 0


def _register_fail(key: str):
    with _FAILS_LOCK:
        _FAILS[key].append(time.time())


def _reset_fails(key: str):
    with _FAILS_LOCK:
        _FAILS.pop(key, None)


def _safe_next(target: str | None) -> str | None:
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return None
    return target


# ---------------------------------------------------------------- маршруты


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(url_for("index"))

    error = None
    login_value = ""
    if request.method == "POST":
        login_value = (request.form.get("login") or "").strip()
        password = request.form.get("password") or ""
        key = _throttle_key(login_value)
        wait = _blocked_for(key)
        if wait:
            minutes = max(1, wait // 60)
            error = f"Слишком много неудачных попыток. Повторите через {minutes} мин."
        elif not login_value or not password:
            error = "Введите логин и пароль."
        else:
            row = query("SELECT * FROM users WHERE login = ?", (login_value,), one=True)
            if row is None or not check_password_hash(row["password_hash"], password):
                _register_fail(key)
                error = "Неверный логин или пароль. Проверьте раскладку и Caps Lock."
            elif not row["is_active"]:
                error = "Учётная запись отключена. Обратитесь к администратору."
            else:
                _reset_fails(key)
                session.clear()
                session.permanent = True
                session["uid"] = row["id"]
                session["pwm"] = row["password_hash"][-16:]
                db = get_db()
                db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_str(), row["id"]))
                db.commit()
                user = User(row)
                nxt = _safe_next(request.args.get("next"))
                if nxt:
                    return redirect(nxt)
                endpoint = user.home_endpoint()
                if endpoint is None:
                    session.clear()
                    error = "У учётной записи нет доступа ни к одному разделу. Обратитесь к администратору."
                else:
                    return redirect(url_for(endpoint))

    no_users = query("SELECT COUNT(*) AS n FROM users", one=True)["n"] == 0
    return render_template("login.html", error=error, login_value=login_value, no_users=no_users), (401 if error else 200)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Вы вышли из системы.", "info")
    return redirect(url_for("auth.login"))
