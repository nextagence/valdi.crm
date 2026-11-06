"""Форматирование, парсинг и справочники."""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(os.environ.get("VALDI_TZ", "Asia/Almaty"))
except Exception:  # нет tzdata — берём локальное время сервера
    TZ = None

NBSP = " "

# ---------------------------------------------------------------- справочники

STATUSES = {
    "accepted":   "Принят",
    "production": "В производстве",
    "ready":      "Готов",
    "delivered":  "Доставлен",
    "cancelled":  "Отменён",
}
ACTIVE_STATUSES = ("accepted", "production", "ready")
DONE_STATUSES = ("ready", "delivered")

FURNITURE_TYPES = ["Кухня", "Шкаф", "Стол", "Кровать", "Комод", "Диван", "Стеллаж", "Другое"]

MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

# ---------------------------------------------------------------- время


def now() -> datetime:
    dt = datetime.now(TZ) if TZ else datetime.now()
    return dt.replace(tzinfo=None, microsecond=0)


def now_str() -> str:
    return now().strftime("%Y-%m-%d %H:%M:%S")


def today() -> date:
    return now().date()


def parse_date(value: str | None) -> date | None:
    """<input type=date> → date. Пусто/мусор → None."""
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _to_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    value = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def fmt_date(value) -> str:
    dt = _to_dt(value)
    return dt.strftime("%d.%m.%Y") if dt else "—"


def fmt_datetime(value) -> str:
    dt = _to_dt(value)
    return dt.strftime("%d.%m.%Y %H:%M") if dt else "—"


def iso_date(value) -> str:
    dt = _to_dt(value)
    return dt.strftime("%Y-%m-%d") if dt else ""


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def time_ago(value) -> str:
    dt = _to_dt(value)
    if not dt:
        return ""
    sec = int((now() - dt).total_seconds())
    if sec < 60:
        return "только что"
    if sec < 3600:
        m = sec // 60
        return f"{m} {plural(m, 'минуту', 'минуты', 'минут')} назад"
    if sec < 86400:
        h = sec // 3600
        return f"{h} {plural(h, 'час', 'часа', 'часов')} назад"
    d = sec // 86400
    if d < 7:
        return f"{d} {plural(d, 'день', 'дня', 'дней')} назад"
    return fmt_datetime(dt)


def deadline_info(delivery_date) -> dict | None:
    """Относительный срок доставки для активного заказа."""
    d = parse_date(delivery_date) if isinstance(delivery_date, str) else delivery_date
    if not d:
        return None
    diff = (d - today()).days
    if diff < 0:
        n = -diff
        return {"tone": "danger", "label": f"Просрочен на {n} {plural(n, 'день', 'дня', 'дней')}", "days": diff}
    if diff == 0:
        return {"tone": "warn", "label": "Сегодня", "days": 0}
    if diff == 1:
        return {"tone": "muted", "label": "Завтра", "days": 1}
    return {"tone": "muted", "label": f"Через {diff} {plural(diff, 'день', 'дня', 'дней')}", "days": diff}


# ---------------------------------------------------------------- деньги


def parse_money(value) -> int | None:
    """'190 000,50' → 19000050 (тиыны). Отрицательное/мусор → None."""
    if value is None:
        return None
    s = str(value).strip().replace(" ", "").replace(NBSP, "").replace("₸", "").replace(",", ".")
    if s == "":
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    if d < 0 or d > Decimal("100000000000"):
        return None
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _group(n: int) -> str:
    return f"{n:,}".replace(",", NBSP)


def fmt_money(cents, decimals: bool = True, sign: bool = True) -> str:
    cents = int(cents or 0)
    neg = cents < 0
    cents = abs(cents)
    whole, frac = divmod(cents, 100)
    s = _group(whole)
    if decimals:
        s += f",{frac:02d}"
    if neg:
        s = "−" + s
    return f"{s}{NBSP}₸" if sign else s


def fmt_money_short(cents) -> str:
    v = Decimal(int(cents or 0)) / 100
    if v >= 1_000_000:
        x = (v / 1_000_000).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        return f"{str(x).replace('.', ',').rstrip('0').rstrip(',')}{NBSP}млн"
    if v >= 1_000:
        x = (v / 1_000).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return f"{x}{NBSP}тыс"
    return f"{v.quantize(Decimal('1'))}"


def money_input(cents) -> str:
    """Значение для <input>: 19000050 → '190000.50'."""
    if cents is None or cents == "":
        return ""
    whole, frac = divmod(int(cents), 100)
    return f"{whole}.{frac:02d}" if frac else str(whole)


# ---------------------------------------------------------------- телефоны

PHONE_ALLOWED = re.compile(r"^[\d\s()+\-]+$")


def normalize_phone(value: str | None) -> str | None:
    """Допускаются цифры, +, пробелы, скобки, дефисы; 10–15 цифр.
    Казахстанские 8XXXXXXXXXX и 10-значные номера приводятся к +7."""
    if not value:
        return None
    value = value.strip()
    if not PHONE_ALLOWED.match(value):
        return None
    digits = re.sub(r"\D", "", value)
    if not 10 <= len(digits) <= 15:
        return None
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    return "+" + digits


def fmt_phone(value: str | None) -> str:
    if not value:
        return "—"
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("7"):
        return f"+7 {digits[1:4]} {digits[4:7]} {digits[7:9]} {digits[9:11]}"
    return value


def phone_digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


# ---------------------------------------------------------------- прочее


def parse_int(value, default=None, min_value=None, max_value=None):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if min_value is not None and n < min_value:
        return default
    if max_value is not None and n > max_value:
        return default
    return n


def month_keys(count: int, end: date | None = None) -> list[tuple[str, str]]:
    """Последние N месяцев: [('2026-06', 'июн'), ...]."""
    end = end or today()
    y, m = end.year, end.month
    out = []
    for _ in range(count):
        out.append((f"{y:04d}-{m:02d}", MONTHS_SHORT[m - 1]))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(out))


def period_start(period: str) -> str | None:
    days = {"7": 7, "30": 30, "90": 90, "year": 365}.get(period)
    if not days:
        return None
    start = today() - timedelta(days=days - 1)
    return start.strftime("%Y-%m-%d 00:00:00")
