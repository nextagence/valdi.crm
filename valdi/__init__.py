"""Valdi CRM — учёт клиентов и заказов мебельного производства."""
from __future__ import annotations

import os
import secrets
from datetime import timedelta
from pathlib import Path

import click
from flask import Flask, g, redirect, render_template, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash

from . import auth, db, utils


def _secret_key(instance: Path) -> str:
    env = os.environ.get("VALDI_SECRET_KEY")
    if env:
        return env
    path = instance / "secret_key"
    if path.exists():
        return path.read_text().strip()
    key = secrets.token_hex(32)
    path.write_text(key)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    instance = Path(app.instance_path)
    instance.mkdir(parents=True, exist_ok=True)

    app.config.update(
        DATABASE=os.environ.get("VALDI_DB", str(instance / "valdi.db")),
        SECRET_KEY=_secret_key(instance),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("VALDI_SECURE_COOKIES") == "1",
        SESSION_COOKIE_NAME="valdi_session",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=int(os.environ.get("VALDI_SESSION_HOURS", "12"))),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        COMPANY_NAME=os.environ.get("VALDI_COMPANY", "Valdi"),
    )
    if test_config:
        app.config.update(test_config)

    if os.environ.get("VALDI_BEHIND_PROXY") == "1":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # Схема создаётся автоматически при первом запуске
    with app.app_context():
        conn = db.connect(app.config["DATABASE"])
        db.init_schema(conn)
        conn.close()

    app.teardown_appcontext(db.close_db)

    @app.before_request
    def _before():
        auth.load_user()
        auth.check_csrf()

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.endpoint != "static":
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    # ------------------------------------------------------------ шаблоны
    app.jinja_env.globals.update(
        csrf_token=auth.csrf_token,
        STATUSES=utils.STATUSES,
        ROLES=auth.ROLES,
        PERMISSIONS=auth.PERMISSIONS,
        FURNITURE_TYPES=utils.FURNITURE_TYPES,
        deadline_info=utils.deadline_info,
        plural=utils.plural,
    )
    app.jinja_env.filters.update(
        money=utils.fmt_money,
        money0=lambda v: utils.fmt_money(v, decimals=False),
        money_short=utils.fmt_money_short,
        money_input=utils.money_input,
        phone=utils.fmt_phone,
        date=utils.fmt_date,
        datetime=utils.fmt_datetime,
        isodate=utils.iso_date,
        ago=utils.time_ago,
    )

    @app.context_processor
    def _ctx():
        return {"current_user": g.get("user"), "company": app.config["COMPANY_NAME"]}

    # ------------------------------------------------------------ маршруты
    from .views import clients, dashboard, employees, orders

    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(orders.bp)
    app.register_blueprint(clients.bp)
    app.register_blueprint(employees.bp)

    @app.route("/")
    def index():
        if g.user is None:
            return redirect(url_for("auth.login"))
        endpoint = g.user.home_endpoint()
        if endpoint is None:
            return render_template("error.html", code=403,
                                   title="Нет доступных разделов",
                                   message="Администратор ещё не выдал вам права. Обратитесь к нему."), 403
        return redirect(url_for(endpoint))

    @app.errorhandler(400)
    def _400(e):
        return render_template("error.html", code=400, title="Запрос не выполнен",
                               message=getattr(e, "description", "") or "Проверьте данные и повторите."), 400

    @app.errorhandler(403)
    def _403(e):
        return render_template("error.html", code=403, title="Нет доступа",
                               message="У вашей учётной записи нет прав на этот раздел. Обратитесь к администратору."), 403

    @app.errorhandler(404)
    def _404(e):
        return render_template("error.html", code=404, title="Страница не найдена",
                               message="Возможно, запись удалили или ссылка устарела."), 404

    # ------------------------------------------------------------ CLI
    @app.cli.command("create-admin")
    @click.option("--login", "login_", default="admin", show_default=True)
    @click.option("--password", default=None, help="Если не задан — будет сгенерирован")
    @click.option("--name", default="Администратор системы", show_default=True)
    def create_admin(login_, password, name):
        """Создать администратора (или сбросить ему пароль)."""
        password = password or secrets.token_urlsafe(9)
        if len(password) < 8:
            raise click.ClickException("Пароль — минимум 8 символов.")
        conn = db.connect(app.config["DATABASE"])
        row = conn.execute("SELECT id FROM users WHERE login = ?", (login_,)).fetchone()
        h = generate_password_hash(password)
        if row:
            conn.execute("UPDATE users SET password_hash = ?, role = 'admin', is_active = 1 WHERE id = ?", (h, row["id"]))
            action = "Пароль обновлён"
        else:
            conn.execute(
                "INSERT INTO users (full_name, login, password_hash, role, permissions, is_active, created_at) "
                "VALUES (?, ?, ?, 'admin', '', 1, ?)",
                (name, login_, h, utils.now_str()),
            )
            action = "Администратор создан"
        conn.commit()
        conn.close()
        click.echo(f"{action}. Логин: {login_}  Пароль: {password}")

    @app.cli.command("seed-demo")
    @click.option("--force", is_flag=True, help="Очистить базу перед заполнением")
    def seed_demo(force):
        """Заполнить базу демо-данными."""
        from .demo import seed
        conn = db.connect(app.config["DATABASE"])
        try:
            msg = seed(conn, force=force)
        finally:
            conn.close()
        click.echo(msg)

    return app
