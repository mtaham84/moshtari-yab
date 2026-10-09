import re
from datetime import date, datetime

def gregorian_to_jalali(gy: int, gm: int, gd: int):
    """Converts a Gregorian date (year, month, day) to Jalali (jy, jm, jd)."""
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy if gm > 2 else gy - 1
    days = 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 - 80 + gd + g_d_m[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30
    return jy, jm, jd


def jalali_to_gregorian(jy: int, jm: int, jd: int):
    """Converts a Jalali date (jy, jm, jd) to Gregorian (gy, gm, gd)."""
    if jy > 979:
        gy = 1600
        jy -= 979
    else:
        gy = 621
    days = 365 * jy + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + 78 + jd + ((jm - 1) * 31 if jm < 7 else ((jm - 7) * 30) + 186)
    gy += 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    sal_a = [0, 31, (29 if (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0) else 28), 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 13 and gd > sal_a[gm]:
        gd -= sal_a[gm]
        gm += 1
    return gy, gm, gd


def to_persian_digits(val) -> str:
    """Converts English digits in string or number to Persian digits."""
    trans = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
    return str(val).translate(trans)


def to_english_digits(val) -> str:
    """Converts Persian/Arabic digits to English digits."""
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return str(val).translate(trans)


def format_jalali_date(d, persian_digits=True) -> str:
    """
    Formats a datetime.date or datetime.datetime to Persian string YYYY/MM/DD.
    """
    if not d:
        return ""
    if isinstance(d, datetime):
        d = d.date()
    jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
    raw = f"{jy:04d}/{jm:02d}/{jd:02d}"
    if persian_digits:
        return to_persian_digits(raw)
    return raw


def format_jalali_datetime(dt, persian_digits=True) -> str:
    """
    Formats a datetime.datetime to Persian string YYYY/MM/DD - HH:MM.
    """
    if not dt:
        return ""
    date_part = format_jalali_date(dt, persian_digits=persian_digits)
    time_part = f"{dt.hour:02d}:{dt.minute:02d}"
    if persian_digits:
        time_part = to_persian_digits(time_part)
    return f"{date_part} - {time_part}"


def parse_jalali_date(s: str) -> date | None:
    """
    Parses a Persian date string like '1403/07/15' or '۱۴۰۳/۰۷/۱۵' or '1403-07-15'
    into a Python datetime.date (Gregorian).
    Returns None if parsing fails.
    """
    if not s:
        return None
    cleaned = to_english_digits(str(s).strip())
    parts = re.split(r"[/\\-]", cleaned)
    if len(parts) != 3:
        return None
    try:
        jy = int(parts[0])
        jm = int(parts[1])
        jd = int(parts[2])
        gy, gm, gd = jalali_to_gregorian(jy, jm, jd)
        return date(gy, gm, gd)
    except Exception:
        return None
