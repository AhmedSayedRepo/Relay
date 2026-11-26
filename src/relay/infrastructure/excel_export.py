"""Readable, macro-free workbook export: a Summary first, then one row per
receipt in plain words and local (Cairo) time, then duplicate groups."""

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any, cast
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from relay.domain.agent import JsonValue

DANGEROUS_PREFIXES = ("=", "+", "-", "@")

# Relay's portal colours.
BRAND = "143F45"
BRAND_SOFT = "E3EFEE"
INK = "1B1A22"
INK2 = "5F6670"
LINE = "D9DEE2"
BAND = "F6F8F9"
TONES = {
    "ok": ("1F7A4D", "E2F3EA"),
    "warn": ("9A5B00", "FDF0D8"),
    "bad": ("A23B32", "F9E1DE"),
    "muted": ("5F6670", "EEF0F2"),
}
UI = "Segoe UI"

TRANSACTION_HEADERS = (
    "Received (Cairo)",
    "Receipt date",
    "Receipt time",
    "Amount",
    "Currency",
    "Status",
    "Why",
    "Bank SMS",
    "Reference",
    "Sender",
    "Recipient",
    "Recipient phone / ID",
    "Phone",
    "Duplicate",
    "Receipt type",
    "Receipt ID",
)
DUPLICATE_HEADERS = (
    "Group",
    "Role",
    "Received (Cairo)",
    "Amount",
    "Currency",
    "Reference",
    "Status",
    "Phone",
    "Reason",
    "OCR confidence",
    "Receipt ID",
)
REASONS = {
    "payment_request": "Payment request (no money moved)",
    "rejected_by_reviewer": "Rejected by a reviewer",
    "transfer_failed": "Transfer failed",
    "not_recognized": "Not recognised as a receipt",
    "duplicate": "Possible duplicate: same reference (surely a copy)",
    "recipient_unclear": "Recipient not clear",
    "other_recipient": "Sent to someone else",
    "repeated_amount": "Possible duplicate: same amount and sender, no reference",
    "repeated_unread_sender": "Possible duplicate: same amount, sender unreadable",
    "reading": "Still being read",
    "no_sms": "No bank SMS yet",
    "missing_fields": "Fields to confirm",
}
SMS = {
    "VERIFIED": ("Matched", "ok"),
    "WAITING": ("Waiting", "warn"),
    "UNMATCHED": ("No SMS", "bad"),
    "AMBIGUOUS": ("Several SMS", "warn"),
}
TYPES = {
    "INSTAPAY_TRANSFER_RECEIPT": "InstaPay",
    "BANK_TRANSFER_RECEIPT": "Bank transfer",
    "UNKNOWN_RECEIPT": "Unknown",
}


def spreadsheet_text(value: object) -> str:
    text = "".join(
        character
        for character in str(value)
        if character in "\t\n\r" or ord(character) >= 32
    )
    if text.lstrip().startswith(DANGEROUS_PREFIXES):
        return "'" + text
    return text


def _text(row: dict[str, JsonValue], key: str) -> str:
    value = row.get(key)
    return spreadsheet_text(value) if value not in (None, "") else ""


def _reference(row: dict[str, JsonValue]) -> str:
    value = row.get("reference_original") or row.get("reference_normalized")
    return spreadsheet_text(value) if value is not None else ""


def _decimal(row: dict[str, JsonValue], key: str) -> Decimal | None:
    value = row.get(key)
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _printed(row: dict[str, JsonValue]) -> tuple[date | None, time | None]:
    """The date and time printed on the receipt (already local)."""
    value = row.get("transaction_occurred_at")
    if not isinstance(value, str):
        return None, None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None, None
    return parsed.date(), parsed.time().replace(tzinfo=None, microsecond=0)


def _status(row: dict[str, JsonValue]) -> tuple[str, str]:
    """(label, tone) in plain words."""
    review = row.get("review_status")
    if review == "AUTO_ACCEPTED":
        resolution = row.get("resolution_result")
        auto = isinstance(resolution, dict) and "auto_confirmed" in resolution
        return ("Confirmed (auto)" if auto else "Confirmed"), "ok"
    if review == "NEEDS_REVIEW":
        return "Needs review", "warn"
    if review == "REJECTED":
        return "Rejected", "muted"
    return spreadsheet_text(review or ""), "muted"


def _duplicate(row: dict[str, JsonValue]) -> str:
    if row.get("is_canonical") is True:
        return "Original"
    if row.get("duplicate_of_candidate_id"):
        return "Duplicate"
    return ""


def _confirmed_once(row: dict[str, JsonValue]) -> bool:
    """Counts in the confirmed money: confirmed, and not the same payment again."""
    return row.get("review_status") == "AUTO_ACCEPTED" and not row.get(
        "duplicate_of_candidate_id"
    )


class _Sheet:
    """Small helpers so every sheet looks the same."""

    thin = Side(style="thin", color=LINE)

    def __init__(self, sheet: Worksheet) -> None:
        self.sheet = sheet
        sheet.sheet_view.showGridLines = False
        # Printed or saved as PDF: landscape, all columns on one page width.
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.print_options.horizontalCentered = True

    def header(
        self,
        row: int,
        names: tuple[str, ...],
        right: tuple[int, ...] = (),
        center: tuple[int, ...] = (),
    ) -> None:
        """Header row; numeric columns line up with their values."""
        fill = PatternFill("solid", fgColor=BRAND)
        for column, name in enumerate(names, start=1):
            cell = self.sheet.cell(row, column, name)
            cell.font = Font(name=UI, bold=True, color="FFFFFF", size=10)
            cell.fill = fill
            side = (
                "right" if column in right else "center" if column in center else "left"
            )
            cell.alignment = Alignment(
                vertical="center",
                horizontal=side,
                wrap_text=True,
                indent=1 if side == "left" else 0,
            )
        self.sheet.row_dimensions[row].height = 28

    def body(self, cell: Any, band: bool, align: str = "left") -> None:
        cell.font = Font(name=UI, size=10, color=INK)
        cell.alignment = Alignment(
            vertical="center", horizontal=align, indent=1 if align == "left" else 0
        )
        cell.border = Border(bottom=self.thin)
        if band:
            cell.fill = PatternFill("solid", fgColor=BAND)

    def pill(self, cell: Any, tone: str) -> None:
        ink, soft = TONES[tone]
        cell.font = Font(name=UI, size=10, bold=True, color=ink)
        cell.fill = PatternFill("solid", fgColor=soft)

    def fit(self, widths: dict[int, tuple[int, int]]) -> None:
        """Width per column from its longest value, within (min, max)."""
        for column, (low, high) in widths.items():
            longest = max(
                (
                    len(str(cell.value)) if cell.value is not None else 0
                    for (cell,) in self.sheet.iter_rows(
                        min_col=column, max_col=column, min_row=1
                    )
                ),
                default=0,
            )
            letter = get_column_letter(column)
            self.sheet.column_dimensions[letter].width = max(
                low, min(high, longest + 3)
            )


class OpenpyxlWorkbookExporter:
    def __init__(self, timezone: str = "Africa/Cairo") -> None:
        self.zone = ZoneInfo(timezone)

    def _local(self, row: dict[str, JsonValue], key: str) -> datetime | None:
        value = row.get(key)
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(self.zone)
        return parsed.replace(tzinfo=None, microsecond=0)

    def build(
        self,
        transactions: list[dict[str, JsonValue]],
        summary: dict[str, JsonValue],
        filters: dict[str, JsonValue],
        exported_at: datetime,
    ) -> bytes:
        workbook = Workbook()
        workbook.properties.title = "Relay receipts report"
        workbook.properties.creator = "Relay"
        workbook.properties.created = exported_at.replace(tzinfo=None)
        workbook.properties.modified = exported_at.replace(tzinfo=None)
        summary_sheet = workbook.active
        summary_sheet.title = "Summary"
        self._summary(summary_sheet, transactions, summary, filters, exported_at)
        self._transactions(workbook.create_sheet("Transactions"), transactions)
        self._duplicates(workbook.create_sheet("Duplicates"), transactions)
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()

    def _transactions(
        self, sheet: Worksheet, transactions: list[dict[str, JsonValue]]
    ) -> None:
        view = _Sheet(sheet)
        view.header(1, TRANSACTION_HEADERS, right=(4,), center=(2, 3))
        for index, item in enumerate(transactions, start=2):
            printed_date, printed_time = _printed(item)
            status, tone = _status(item)
            sms_label, sms_tone = SMS.get(str(item.get("sms_verification")), ("", ""))
            values: tuple[Any, ...] = (
                self._local(item, "folder_at") or self._local(item, "received_at"),
                printed_date,
                printed_time,
                _decimal(item, "amount"),
                _text(item, "currency"),
                status,
                REASONS.get(str(item.get("status_reason")), ""),
                sms_label,
                _reference(item),
                _text(item, "sender_name"),
                _text(item, "recipient_name"),
                _text(item, "recipient_identifier"),
                _text(item, "device_name"),
                _duplicate(item),
                TYPES.get(str(item.get("receipt_type")), _text(item, "receipt_type")),
                _text(item, "id"),
            )
            band = index % 2 == 1
            for column, value in enumerate(values, start=1):
                cell = sheet.cell(index, column, value)
                align = (
                    "right" if column == 4 else "center" if column in (2, 3) else "left"
                )
                view.body(cell, band, align)
            sheet.cell(index, 1).number_format = "yyyy-mm-dd  hh:mm"
            sheet.cell(index, 2).number_format = "yyyy-mm-dd"
            sheet.cell(index, 3).number_format = "hh:mm"
            amount = sheet.cell(index, 4)
            amount.number_format = "#,##0.00"
            amount.font = Font(name=UI, size=10, bold=True, color=INK)
            view.pill(sheet.cell(index, 6), tone)
            if sms_tone:
                view.pill(sheet.cell(index, 8), sms_tone)
            sheet.cell(index, 16).font = Font(name="Consolas", size=9, color=INK2)
            sheet.row_dimensions[index].height = 20
        last = len(transactions) + 1
        sheet.freeze_panes = "B2"
        sheet.auto_filter.ref = (
            f"A1:{get_column_letter(len(TRANSACTION_HEADERS))}{last}"
        )
        # Confirmed money under the list (each payment once).
        totals = self._totals(transactions)
        row = last + 2
        for currency, amount in sorted(totals.items()):
            label = sheet.cell(row, 3, "Confirmed money")
            label.font = Font(name=UI, size=10, bold=True, color=BRAND)
            label.alignment = Alignment(horizontal="right")
            cell = sheet.cell(row, 4, amount)
            cell.number_format = "#,##0.00"
            cell.font = Font(name=UI, size=11, bold=True, color=BRAND)
            cell.fill = PatternFill("solid", fgColor=BRAND_SOFT)
            sheet.cell(row, 5, spreadsheet_text(currency)).font = Font(
                name=UI, size=10, bold=True, color=BRAND
            )
            row += 1
        view.fit(
            {
                1: (18, 18),
                2: (13, 13),
                3: (12, 12),
                4: (13, 16),
                5: (10, 10),
                6: (18, 20),
                7: (14, 34),
                8: (12, 14),
                9: (16, 22),
                10: (14, 30),
                11: (14, 30),
                12: (16, 26),
                13: (12, 22),
                14: (11, 12),
                15: (12, 16),
                16: (14, 38),
            }
        )

    def _duplicates(
        self, sheet: Worksheet, transactions: list[dict[str, JsonValue]]
    ) -> None:
        view = _Sheet(sheet)
        view.header(1, DUPLICATE_HEADERS, right=(4, 10))
        rows = [item for item in transactions if item.get("duplicate_group_id")]
        rows.sort(
            key=lambda item: (
                str(item.get("duplicate_group_id")),
                item.get("is_canonical") is not True,
            )
        )
        groups: dict[str, int] = {}
        for index, item in enumerate(rows, start=2):
            group = groups.setdefault(
                str(item.get("duplicate_group_id")), len(groups) + 1
            )
            status, tone = _status(item)
            values: tuple[Any, ...] = (
                f"Group {group}",
                "Original" if item.get("is_canonical") is True else "Duplicate",
                self._local(item, "folder_at") or self._local(item, "received_at"),
                _decimal(item, "amount"),
                _text(item, "currency"),
                _reference(item),
                status,
                _text(item, "device_name"),
                {"REFERENCE_NUMBER": "Same reference"}.get(
                    str(item.get("duplicate_reason")),
                    _text(item, "duplicate_reason")
                    or ("Same reference" if item.get("is_canonical") else ""),
                ),
                item.get("ocr_mean_confidence"),
                _text(item, "id"),
            )
            band = group % 2 == 0
            for column, value in enumerate(values, start=1):
                view.body(
                    sheet.cell(index, column, value),
                    band,
                    "right" if column in (4, 10) else "left",
                )
            sheet.cell(index, 3).number_format = "yyyy-mm-dd  hh:mm"
            sheet.cell(index, 4).number_format = "#,##0.00"
            sheet.cell(index, 10).number_format = "0%"
            view.pill(sheet.cell(index, 7), tone)
            if item.get("is_canonical") is True:
                sheet.cell(index, 2).font = Font(
                    name=UI, size=10, bold=True, color=BRAND
                )
            sheet.cell(index, 11).font = Font(name="Consolas", size=9, color=INK2)
            sheet.row_dimensions[index].height = 20
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = (
            f"A1:{get_column_letter(len(DUPLICATE_HEADERS))}{len(rows) + 1}"
        )
        view.fit(
            {
                1: (10, 10),
                2: (11, 11),
                3: (18, 18),
                4: (13, 16),
                5: (10, 10),
                6: (16, 22),
                7: (18, 20),
                8: (12, 22),
                9: (16, 24),
                10: (15, 15),
                11: (14, 38),
            }
        )

    @staticmethod
    def _totals(transactions: list[dict[str, JsonValue]]) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for item in transactions:
            currency = item.get("currency")
            amount = _decimal(item, "amount")
            if _confirmed_once(item) and isinstance(currency, str) and amount:
                totals[currency] = totals.get(currency, Decimal("0")) + amount
        return totals

    def _summary(
        self,
        sheet: Worksheet,
        transactions: list[dict[str, JsonValue]],
        summary: dict[str, JsonValue],
        filters: dict[str, JsonValue],
        exported_at: datetime,
    ) -> None:
        view = _Sheet(sheet)
        for letter, width in zip(
            "ABCDEFGH", (22, 14, 22, 14, 22, 14, 22, 14), strict=True
        ):
            sheet.column_dimensions[letter].width = width
        start = str(filters.get("date_from") or summary.get("date_from") or "")
        end = str(filters.get("date_to") or summary.get("date_to") or "")
        period = start if start == end else f"{start} to {end}".strip()
        shift = filters.get("shift")
        if shift:
            # A shift's report: its number and the hours it covered.
            hours = (
                f"{filters.get('time_from') or ''} to {filters.get('time_to') or ''}"
            )
            period = f"{period} · Shift {shift} ({hours})"
        sheet.merge_cells("A1:H1")
        sheet["A1"] = "Relay · Receipts report"
        sheet["A1"].font = Font(name=UI, size=18, bold=True, color=BRAND)
        sheet.row_dimensions[1].height = 32
        sheet.merge_cells("A2:H2")
        sheet["A2"] = spreadsheet_text(period or "All dates")
        sheet["A2"].font = Font(name=UI, size=12, bold=True, color=INK)
        sheet.merge_cells("A3:H3")
        local = exported_at.astimezone(self.zone) if exported_at.tzinfo else exported_at
        sheet["A3"] = f"Exported {local:%Y-%m-%d %H:%M} (Cairo time)"
        sheet["A3"].font = Font(name=UI, size=10, color=INK2)
        for column in range(1, 9):
            sheet.cell(4, column).border = Border(
                bottom=Side(style="medium", color=BRAND)
            )

        statuses = [_status(item)[0] for item in transactions]
        totals = self._totals(transactions)
        money = (
            ", ".join(
                f"{amount:,.2f} {code}" for code, amount in sorted(totals.items())
            )
            or "0.00"
        )
        cards = (
            ("Confirmed money", money, "ok"),
            (
                "Confirmed receipts",
                sum(1 for item in transactions if _confirmed_once(item)),
                "ok",
            ),
            ("Needs review", statuses.count("Needs review"), "warn"),
            ("Rejected", statuses.count("Rejected"), "muted"),
        )
        for offset, (label, value, tone) in enumerate(cards):
            first = 1 + offset * 2
            ink, soft = TONES[tone]
            for row, text, font in (
                (6, label.upper(), Font(name=UI, size=9, bold=True, color=INK2)),
                (7, value, Font(name=UI, size=16, bold=True, color=ink)),
            ):
                sheet.merge_cells(
                    start_row=row, start_column=first, end_row=row, end_column=first + 1
                )
                cell = sheet.cell(row, first, text)
                cell.font = font
                cell.alignment = Alignment(horizontal="center", vertical="center")
                for column in (first, first + 1):
                    sheet.cell(row, column).fill = PatternFill("solid", fgColor=soft)
        sheet.row_dimensions[6].height = 22
        sheet.row_dimensions[7].height = 34

        def section(row: int, title: str) -> int:
            cell = sheet.cell(row, 1, title)
            cell.font = Font(name=UI, size=12, bold=True, color=BRAND)
            return row + 1

        row = section(9, "By status")
        view.header(row, ("Status", "Receipts", "Amount", "Currency"), right=(2, 3))
        groups: dict[tuple[str, str], list[Decimal]] = {}
        for item in transactions:
            key = (_status(item)[0], str(item.get("currency") or ""))
            groups.setdefault(key, []).append(_decimal(item, "amount") or Decimal(0))
        order = ["Confirmed (auto)", "Confirmed", "Needs review", "Rejected"]
        for band, ((status, currency), amounts) in enumerate(
            sorted(
                groups.items(),
                key=lambda kv: (
                    order.index(kv[0][0]) if kv[0][0] in order else 9,
                    kv[0][1],
                ),
            )
        ):
            row += 1
            tone = _tone(status)
            for column, value in enumerate(
                (
                    status,
                    len(amounts),
                    sum(amounts, Decimal(0)),
                    spreadsheet_text(currency),
                ),
                start=1,
            ):
                cell = sheet.cell(row, column, value)
                view.body(cell, band % 2 == 1, "right" if column in (2, 3) else "left")
            sheet.cell(row, 3).number_format = "#,##0.00"
            view.pill(sheet.cell(row, 1), tone)
        if not groups:
            row += 1
            sheet.cell(row, 1, "No receipts in this period.").font = Font(
                name=UI, size=10, color=INK2
            )

        phones = [
            cast(dict[str, Any], item)
            for item in cast(list[Any], summary.get("transactions_by_device") or [])
            if isinstance(item, dict)
        ]
        row = section(row + 2, "By phone")
        view.header(
            row,
            ("Phone", "Receipts", "Confirmed", "SMS matched", "Confirmed money"),
            right=(2, 3, 4, 5),
        )
        for band, phone in enumerate(phones):
            row += 1
            values = (
                spreadsheet_text(phone.get("device_name") or ""),
                int(phone.get("count") or 0),
                int(phone.get("confirmed_count") or 0),
                int(phone.get("sms_verified_count") or 0),
                _decimal(phone, "confirmed_amount") or Decimal(0),
            )
            for column, value in enumerate(values, start=1):
                view.body(
                    sheet.cell(row, column, value),
                    band % 2 == 1,
                    "left" if column == 1 else "right",
                )
            sheet.cell(row, 5).number_format = "#,##0.00"
        if not phones:
            row += 1
            sheet.cell(row, 1, "No phones sent receipts.").font = Font(
                name=UI, size=10, color=INK2
            )

        sms = summary.get("sms_by_status")
        sms = sms if isinstance(sms, dict) else {}
        row = section(row + 2, "Bank SMS matching")
        view.header(row, ("Receipts", "Count"), right=(2,))
        for band, (code, (label, _)) in enumerate(SMS.items()):
            row += 1
            view.body(sheet.cell(row, 1, label), band % 2 == 1)
            view.body(
                sheet.cell(row, 2, int(cast(Any, sms.get(code)) or 0)),
                band % 2 == 1,
                "right",
            )

        row += 2
        failures = summary.get("processing_failure_count") or 0
        notes = [
            f"Images that failed to process: {failures}",
            "Confirmed money counts each payment once (duplicates left out).",
            "Times are Cairo time. Full list on the Transactions sheet.",
        ]
        shown = {
            key: value
            for key, value in filters.items()
            if key not in ("date_from", "date_to", "shift", "time_from", "time_to")
            and value not in (None, "", [])
        }
        if shown:
            notes.insert(
                0,
                "Filters: "
                + ", ".join(
                    f"{key.replace('_', ' ')} = {value}"
                    for key, value in sorted(shown.items())
                ),
            )
        for note in notes:
            cell = sheet.cell(row, 1, spreadsheet_text(note))
            cell.font = Font(name=UI, size=9, color=INK2)
            row += 1
        sheet.freeze_panes = "A5"


def _tone(status: str) -> str:
    if status.startswith("Confirmed"):
        return "ok"
    if status == "Needs review":
        return "warn"
    return "muted"
