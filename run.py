"""Локальный запуск Valdi CRM.

    python run.py           — запустить на http://127.0.0.1:5000
    python run.py --demo    — перед запуском заполнить базу демо-данными (если она пустая)

При первом запуске, если в базе нет ни одного пользователя, создаётся
администратор admin со случайным паролем — пароль выводится в консоль.
"""
import os
import secrets
import sys

from werkzeug.security import generate_password_hash

from valdi import create_app
from valdi.db import connect
from valdi.utils import now_str

app = create_app()


def bootstrap():
    conn = connect(app.config["DATABASE"])
    try:
        if "--demo" in sys.argv:
            from valdi.demo import seed
            print(seed(conn))
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            password = secrets.token_urlsafe(9)
            conn.execute(
                "INSERT INTO users (full_name, login, password_hash, role, permissions, is_active, created_at) "
                "VALUES ('Администратор системы', 'admin', ?, 'admin', '', 1, ?)",
                (generate_password_hash(password), now_str()),
            )
            conn.commit()
            print("\n" + "=" * 60)
            print("  Создан администратор.  Логин: admin   Пароль:", password)
            print("  Сохраните пароль — он показывается один раз.")
            print("=" * 60 + "\n")
    finally:
        conn.close()


if __name__ == "__main__":
    bootstrap()
    host = os.environ.get("VALDI_HOST", "127.0.0.1")
    port = int(os.environ.get("VALDI_PORT", "5000"))
    print(f"Valdi CRM: http://{host}:{port}")
    app.run(host=host, port=port, debug=os.environ.get("VALDI_DEBUG") == "1")
