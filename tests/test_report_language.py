"""Shift reports in the closer's language: Arabic labels, right to left."""

from datetime import UTC, date, datetime
from io import BytesIO

from openpyxl import load_workbook

from relay.infrastructure.daily_report import render
from relay.infrastructure.excel_export import OpenpyxlWorkbookExporter
from relay.infrastructure.report_language import (
    arabic_html,
    arabic_text,
    arabic_workbook,
    translate,
)


def test_labels_and_patterns_are_translated_data_is_not() -> None:
    assert translate("Confirmed money") == "الفلوس المؤكدة"
    assert translate("SHIFT 2") == "وردية 2"
    assert translate("Saturday 21 November 2026") == "السبت 21 نوفمبر 2026"
    assert translate("MOHAMED ALAA") == "MOHAMED ALAA"
    assert translate("  Needs review ") == "  محتاج مراجعة "


def test_the_email_reads_right_to_left_in_arabic() -> None:
    summary = {"totals_by_currency": [{"amount": "815.00", "currency": "EGP"}]}
    shift = {"number": 1, "span": "12:00 AM to 08:57 AM"}
    text, html = render(date(2026, 11, 21), summary, {}, shift)
    arabic = arabic_html(html)
    assert "dir='rtl'" in arabic and "الفلوس المؤكدة" in arabic
    assert "تقرير الوردية" in arabic and "Shift report" not in arabic
    assert "الفلوس المؤكدة: 815.00 EGP" in arabic_text(text)


def test_the_excel_file_in_arabic() -> None:
    exporter = OpenpyxlWorkbookExporter()
    data = exporter.build([], {}, {}, datetime(2026, 11, 21, 12, tzinfo=UTC))
    book = load_workbook(BytesIO(arabic_workbook(data)))
    assert book.sheetnames[:2] == ["الملخص", "المعاملات"]
    assert book.worksheets[0].sheet_view.rightToLeft is True
    first = book["المعاملات"]
    assert first.cell(1, 1).value == "وصل (القاهرة)"
