"""
Компактный сводный PDF-отчёт по декларации УСН 6%.

Вместо визуального воспроизведения формы КНД 1152017 (которое требует
точного бланка ФНС и часто даёт «странный» результат), формируем
читаемую справочную сводку: кто, за что, сколько и по каким строкам.

Основной официальный документ для подачи — XLSX-файл декларации.
PDF нужен только как быстрая визуальная сверка значений.
"""
from __future__ import annotations

import io
import os
from typing import Any, Dict

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas as pdf_canvas
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


_FONT = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"
_FONT_REGISTERED = False


def _register_font() -> None:
    global _FONT, _FONT_BOLD, _FONT_REGISTERED
    if _FONT_REGISTERED:
        return
    paths_regular = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/ARIAL.TTF",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    paths_bold = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/ARIALBD.TTF",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]

    for p in paths_regular:
        if os.path.exists(p):
            try:
                pdfmetrics.registerFont(TTFont("AppFont", p))
                _FONT = "AppFont"
                break
            except Exception:
                pass
    for p in paths_bold:
        if os.path.exists(p):
            try:
                pdfmetrics.registerFont(TTFont("AppFont-Bold", p))
                _FONT_BOLD = "AppFont-Bold"
                break
            except Exception:
                pass
    _FONT_REGISTERED = True


def _fmt_rub(value) -> str:
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return "0"
    return f"{v:,}".replace(",", " ")


_PERIOD_LABELS = {
    "21": "1 квартал",
    "31": "Полугодие",
    "33": "9 месяцев",
    "34": "Год",
    "50": "Последний налоговый период",
    "95": "Последний налоговый период при переходе на иной режим",
    "96": "Последний налоговый период при прекращении деятельности",
}


def generate_summary_pdf(
    decl_data: Dict[str, Any],
    project_data: Dict[str, Any],
) -> bytes:
    """
    Сформировать компактный сводный PDF-отчёт по декларации.

    Структура:
      1. Шапка — кто/когда/за какой период
      2. Раздел 1.1 — что к уплате (авансы и годовой налог)
      3. Раздел 2.1.1 — доходы, налог, вычет по периодам
      4. Подпись — что XLSX это основной документ
    """
    _register_font()

    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    c.setTitle("Сводка по декларации УСН 6%")

    width, height = A4
    x_left = 20 * mm
    x_right = width - 20 * mm
    y = height - 20 * mm

    # ---------- Заголовок ----------
    c.setFont(_FONT_BOLD, 14)
    c.drawString(x_left, y, "Налоговая декларация ИП на УСН 6%")
    y -= 6 * mm
    c.setFont(_FONT, 10)
    c.drawString(x_left, y, "(форма по КНД 1152017) — сводка данных")
    y -= 4 * mm
    c.setStrokeColorRGB(0.7, 0.7, 0.7)
    c.line(x_left, y, x_right, y)
    y -= 7 * mm

    # ---------- Реквизиты ----------
    inn = str(project_data.get("inn", "") or "—")
    fio = str(project_data.get("fio", "") or "—")
    year = str(project_data.get("tax_period_year", "") or "—")
    oktmo = str(project_data.get("oktmo", "") or "—")
    ifns = str(project_data.get("ifns_code", "") or "—")
    period_code = str(decl_data.get("period_code", "") or "34")
    period_label = _PERIOD_LABELS.get(period_code, period_code)
    date_pres = str(decl_data.get("date_presented", "") or "—")

    rows = [
        ("ИНН", inn),
        ("ФИО", fio),
        ("Налоговый период (год)", year),
        ("Отчётный период", f"{period_label} (код {period_code})"),
        ("Код ИФНС", ifns),
        ("ОКТМО", oktmo),
        ("Дата представления", date_pres),
    ]

    c.setFont(_FONT_BOLD, 10)
    c.drawString(x_left, y, "Реквизиты")
    y -= 5 * mm
    c.setFont(_FONT, 10)
    for label, value in rows:
        c.drawString(x_left, y, label + ":")
        c.drawString(x_left + 55 * mm, y, value)
        y -= 5 * mm

    y -= 3 * mm

    # ---------- Раздел 1.1 ----------
    s11 = decl_data.get("section_1_1", {}) or {}
    c.setFont(_FONT_BOLD, 11)
    c.drawString(x_left, y, "Раздел 1.1 — Суммы к уплате (объект «Доходы»)")
    y -= 6 * mm
    c.setFont(_FONT, 10)
    pay_rows = [
        ("020", "Авансовый платёж к уплате за 1 квартал",   s11.get("line_020", 0)),
        ("040", "Авансовый платёж к уплате за полугодие",   s11.get("line_040", 0)),
        ("070", "Авансовый платёж к уплате за 9 месяцев",   s11.get("line_070", 0)),
        ("100", "Налог к доплате за налоговый период",      s11.get("line_100", 0)),
    ]
    _draw_table(c, x_left, y, pay_rows, x_right)
    y -= (len(pay_rows) + 1) * 5 * mm + 4 * mm

    # ---------- Раздел 2.1.1 ----------
    s211 = decl_data.get("section_2_1_1", {}) or {}
    c.setFont(_FONT_BOLD, 11)
    c.drawString(x_left, y, "Раздел 2.1.1 — Расчёт налога")
    y -= 6 * mm
    c.setFont(_FONT, 10)

    has_emp_code = str(s211.get("line_101", "1"))
    has_emp_text = "С наёмными работниками" if has_emp_code == "1" else "Без работников"
    c.drawString(x_left, y, f"Признак налогоплательщика: {has_emp_code} ({has_emp_text})")
    y -= 6 * mm

    # Формат ряда: (label, lines_prefix, v_q1, v_half, v_9m, v_year)
    calc_rows = [
        ("Доходы, ₽",         "стр. 110–113",
         s211.get("line_110", 0), s211.get("line_111", 0),
         s211.get("line_112", 0), s211.get("line_113", 0)),
        ("Налог, ₽",          "стр. 130–133",
         s211.get("line_130", 0), s211.get("line_131", 0),
         s211.get("line_132", 0), s211.get("line_133", 0)),
        ("Вычет (взносы), ₽", "стр. 140–143",
         s211.get("line_140", 0), s211.get("line_141", 0),
         s211.get("line_142", 0), s211.get("line_143", 0)),
    ]

    # Рендер таблицы: | Показатель (широкий) | 1 кв | полугодие | 9 мес | год |
    label_col_w = 55 * mm
    col_w = (x_right - x_left - label_col_w) / 4
    c.setFont(_FONT_BOLD, 9)
    c.drawString(x_left, y, "Показатель")
    for i, label in enumerate(["1 кв", "полугодие", "9 мес", "год"]):
        c.drawRightString(x_left + label_col_w + col_w * (i + 1) - 2, y, label)
    y -= 5 * mm
    c.line(x_left, y + 2, x_right, y + 2)
    y -= 1 * mm

    for label, lines_prefix, *values in calc_rows:
        c.setFont(_FONT_BOLD, 9)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(x_left, y, label)
        c.setFont(_FONT, 8)
        c.setFillColorRGB(0.55, 0.55, 0.55)
        c.drawString(x_left, y - 3.5 * mm, lines_prefix)
        c.setFillColorRGB(0, 0, 0)
        c.setFont(_FONT, 10)
        for i, v in enumerate(values):
            c.drawRightString(x_left + label_col_w + col_w * (i + 1) - 2, y, _fmt_rub(v))
        y -= 8 * mm

    y -= 4 * mm
    c.setStrokeColorRGB(0.7, 0.7, 0.7)
    c.line(x_left, y, x_right, y)
    y -= 5 * mm

    # ---------- Подвал ----------
    c.setFont(_FONT_BOLD, 9)
    c.drawString(x_left, y, "⚠ Для сдачи в налоговую используйте XLSX-файл.")
    y -= 4 * mm
    c.setFont(_FONT, 8)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    notes = [
        "Этот PDF — сводка ключевых показателей декларации для визуальной сверки.",
        "Официальный документ для подачи — XLSX, заполненный по форме КНД 1152017.",
        "Откройте XLSX в Excel → «Файл» → «Сохранить как PDF» для печати по форме ФНС.",
    ]
    for note in notes:
        c.drawString(x_left, y, note)
        y -= 3.5 * mm

    c.save()
    return buf.getvalue()


def _draw_table(c, x_left, y_top, rows, x_right):
    """Нарисовать простую 3-колоночную таблицу: код строки | описание | сумма."""
    col_code_w  = 12 * mm
    col_sum_w   = 35 * mm
    row_h       = 5 * mm

    c.setStrokeColorRGB(0.85, 0.85, 0.85)
    c.line(x_left, y_top + 2, x_right, y_top + 2)
    y = y_top
    for code, desc, amount in rows:
        c.setFillColorRGB(0.5, 0.5, 0.5)
        c.drawString(x_left, y, f"стр. {code}")
        c.setFillColorRGB(0, 0, 0)
        c.drawString(x_left + col_code_w + 4 * mm, y, desc)
        c.drawRightString(x_right, y, _fmt_rub(amount) + " ₽")
        y -= row_h
    c.line(x_left, y + 3, x_right, y + 3)
