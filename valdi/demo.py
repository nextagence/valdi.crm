"""Демо-данные: сотрудники, клиенты, заказы за последние 6 месяцев."""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

from .utils import now

DEMO_USERS = [
    # ФИО, телефон, логин, пароль, роль, права
    ("Администратор системы", "+77010000001", "admin", "admin12345", "admin", ""),
    ("Руководитель отдела", "+77017001122", "manager", "manager12345", "manager",
     "dashboard,clients_view,clients_manage,orders_view,orders_manage,employees_manage"),
    ("Менеджер продаж", "+77037899434", "sales", "sales12345", "employee",
     "dashboard,clients_view,clients_manage,orders_view,orders_manage"),
    ("Цех — мастер смены", "+77058793191", "workshop", "workshop12345", "employee", "orders_view"),
]

CLIENTS = [
    ("Ахметов Аслан Серикович", "+77011234567", "Постоянный клиент, кухни на заказ."),
    ("Иванова Мария Петровна", "+77029876543", None),
    ("Сергеев Дмитрий Олегович", "+77775554433", "Интересуется шкафами-купе."),
    ("Ким Елена Викторовна", "+77057778899", None),
    ("Нурланова Айгерим Болатовна", "+77474561230", "Квартира в ЖК «Esentai City», замер 12.10."),
    ("Жумабеков Ерлан Маратович", "+77012223344", None),
    ("Оспанова Динара Кайратовна", "+77085550011", "Просит звонить после 18:00."),
    ("Петров Андрей Николаевич", "+77761239876", None),
    ("Ли Виктория Сергеевна", "+77029994411", "Дизайнер интерьеров, приводит клиентов."),
    ("Бекова Салтанат Ериковна", "+77071112233", None),
    ("ТОО «Арман Строй»", "+77272505050", "Юрлицо, оплата по счёту."),
    ("Садыков Тимур Русланович", "+77753338877", None),
    ("Гончаренко Ольга Ивановна", "+77016667788", None),
    ("Касымов Арман Бауржанович", "+77054440099", "Дача в Талгаре."),
]

ITEMS = {
    "Кухня": [("Кухонный гарнитур угловой, МДФ эмаль", (2800, 900, 600), (1_200_000, 3_200_000)),
              ("Кухня прямая, ЛДСП Egger, столешница кварц", (3200, 2300, 600), (900_000, 2_100_000)),
              ("Остров кухонный с барной стойкой", (1800, 900, 900), (350_000, 700_000))],
    "Шкаф": [("Шкаф-купе 3 двери, зеркало", (2400, 2600, 600), (380_000, 820_000)),
             ("Гардеробная система, открытая", (3000, 2700, 450), (450_000, 950_000)),
             ("Шкаф распашной в прихожую", (1200, 2400, 400), (190_000, 360_000))],
    "Стол": [("Стол обеденный, массив дуба", (1800, 750, 900), (180_000, 420_000)),
             ("Стол письменный с тумбой", (1400, 750, 700), (90_000, 190_000))],
    "Кровать": [("Кровать двуспальная с подъёмным механизмом", (1800, 1100, 2100), (220_000, 520_000)),
                ("Кровать детская, ЛДСП", (900, 900, 2000), (120_000, 240_000))],
    "Комод": [("Комод 5 ящиков, МДФ", (900, 1100, 450), (85_000, 190_000))],
    "Диван": [("Диван угловой, велюр", (2900, 900, 1700), (280_000, 640_000))],
    "Стеллаж": [("Стеллаж книжный во всю стену", (3200, 2600, 350), (160_000, 380_000))],
}
TYPE_WEIGHTS = [("Кухня", 26), ("Шкаф", 24), ("Стол", 12), ("Кровать", 12), ("Комод", 9), ("Диван", 8), ("Стеллаж", 9)]

COMMENTS = [None, None, "Замер выполнен, согласовать цвет фасадов.", "Доставка на 7 этаж, лифт грузовой.",
            "Предоплата 50% получена.", None, "Клиент ждёт эскиз до пятницы.", None]
STATUS_NOTES = {
    "production": ["Материал передан в цех.", "Раскрой начат.", None],
    "ready": ["Собрано на складе, ждёт доставки.", None],
    "delivered": ["Установлено, акт подписан.", None, "Доставлено, клиент доволен."],
    "cancelled": ["Клиент отказался после замера.", "Не согласовали бюджет."],
}


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _pick_type(rng):
    total = sum(w for _, w in TYPE_WEIGHTS)
    x = rng.uniform(0, total)
    for t, w in TYPE_WEIGHTS:
        x -= w
        if x <= 0:
            return t
    return TYPE_WEIGHTS[0][0]


def seed(conn, force: bool = False) -> str:
    cur = conn.cursor()
    if force:
        for t in ("order_history", "order_items", "orders", "clients", "users"):
            cur.execute(f"DELETE FROM {t}")
        cur.execute("DELETE FROM sqlite_sequence")
    elif cur.execute("SELECT COUNT(*) FROM clients").fetchone()[0]:
        return "В базе уже есть клиенты. Чтобы перезаписать демо-данными, запустите с --force."

    rng = random.Random(2026)
    ref = now()
    start = ref - timedelta(days=182)

    # --- сотрудники (существующие логины не трогаем)
    user_ids = {}
    created = []
    for name, phone, login, pw, role, perms in DEMO_USERS:
        row = cur.execute("SELECT id FROM users WHERE login = ?", (login,)).fetchone()
        if row:
            user_ids[login] = row[0]
            continue
        cur.execute(
            "INSERT INTO users (full_name, phone, login, password_hash, role, permissions, is_active, created_at, last_login_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (name, phone, login, generate_password_hash(pw), role, perms, _ts(start),
             _ts(ref - timedelta(hours=rng.randint(1, 60))) if login in ("admin", "sales") else None),
        )
        user_ids[login] = cur.lastrowid
        created.append(f"{login} / {pw}")
    actors = [user_ids["admin"], user_ids["manager"], user_ids["sales"]]

    # --- клиенты
    client_ids = []
    for i, (name, phone, comment) in enumerate(CLIENTS):
        created_at = start + timedelta(days=i * 12 + rng.randint(0, 6), hours=rng.randint(9, 18), minutes=rng.randint(0, 59))
        cur.execute("INSERT INTO clients (full_name, phone, comment, created_at) VALUES (?, ?, ?, ?)",
                    (name, phone, comment, _ts(min(created_at, ref - timedelta(days=1)))))
        client_ids.append((cur.lastrowid, min(created_at, ref - timedelta(days=1))))

    # --- заказы
    n_orders = 0
    day = start + timedelta(days=4)
    while day < ref - timedelta(hours=6):
        eligible = [(cid, c_at) for cid, c_at in client_ids if c_at <= day]
        if eligible:
            cid, _ = rng.choice(eligible)
            created_at = day.replace(hour=rng.randint(9, 19), minute=rng.randint(0, 59), second=0)
            if created_at > ref:
                created_at = ref - timedelta(hours=2)
            age = (ref - created_at).days
            lead = rng.choice([14, 21, 28, 30, 35, 45])
            delivery = (created_at + timedelta(days=lead)).date()

            # статус по возрасту заказа: старые закрыты, свежие в работе, каждый 3-й из недавних — с просрочкой
            if n_orders % 12 == 5:
                status = "cancelled"
            elif age > lead + 20:
                status = "delivered"
            elif age > lead + 2:
                status = ("ready", "production")[n_orders % 2] if n_orders % 3 == 0 else "delivered"
            elif age > lead - 7:
                status = "ready" if n_orders % 3 else "production"
            elif age > 4:
                status = "production" if n_orders % 3 else "accepted"
            else:
                status = "accepted"

            items = []
            for pos in range(1, rng.choice([1, 1, 1, 2, 2, 3]) + 1):
                t = _pick_type(rng)
                name, (w, h, d), (lo, hi) = rng.choice(ITEMS[t])
                qty = 1 if t in ("Кухня", "Шкаф", "Диван") else rng.choice([1, 1, 2])
                price = rng.randrange(lo, hi, 5000) * 100
                jitter = lambda v: v + rng.choice([-100, -50, 0, 0, 50, 100])  # noqa: E731
                items.append((pos, t, name, jitter(w), jitter(h), d, qty, price, price * qty))
            total = sum(i[-1] for i in items)

            author = rng.choice(actors)
            cur.execute(
                "INSERT INTO orders (client_id, status, comment, delivery_date, total, created_at, updated_at, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, status, rng.choice(COMMENTS), delivery.isoformat(), total, _ts(created_at), _ts(created_at), author),
            )
            oid = cur.lastrowid
            n_orders += 1
            for it in items:
                cur.execute(
                    "INSERT INTO order_items (order_id, position, furniture_type, name, width, height, depth, qty, unit_price, line_total) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (oid, *it))

            # история статусов
            cur.execute("INSERT INTO order_history (order_id, kind, status, comment, user_id, created_at) "
                        "VALUES (?, 'created', 'accepted', ?, ?, ?)", (oid, None, author, _ts(created_at)))
            path = {"accepted": [], "production": ["production"], "ready": ["production", "ready"],
                    "delivered": ["production", "ready", "delivered"], "cancelled": ["cancelled"]}[status]
            t_cursor = created_at
            span = max(1, min(age, lead + 5))
            for step, st in enumerate(path, start=1):
                t_cursor = created_at + timedelta(days=span * step / (len(path) + 0.5), hours=rng.randint(0, 5))
                t_cursor = min(t_cursor, ref - timedelta(minutes=rng.randint(30, 600)))
                cur.execute("INSERT INTO order_history (order_id, kind, status, comment, user_id, created_at) "
                            "VALUES (?, 'status', ?, ?, ?, ?)",
                            (oid, st, rng.choice(STATUS_NOTES.get(st, [None])), rng.choice(actors), _ts(t_cursor)))
            cur.execute("UPDATE orders SET updated_at = ? WHERE id = ?", (_ts(max(t_cursor, created_at)), oid))
        day += timedelta(days=rng.choice([2, 3, 4, 5, 6]))

    conn.commit()
    users_msg = ("Новые учётные записи: " + ", ".join(created)) if created else "Сотрудники уже существовали — пароли не менялись."
    return f"Готово: {len(client_ids)} клиентов, {n_orders} заказов. {users_msg}"
