"""Deterministic, macro-free operational workbook export."""

import json
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import cast

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from relay.domain.agent import JsonValue

TRANSACTION_HEADERS = (
    "Date",
    "Time",
    "Amount",
    "Currency",
    "Reference / Serial Number",
    "Sender",
    "Recipient",
    "Recipient Identifier",
    "Review Status",
    "Duplicate Status",
    "Duplicate Group / Canonical Candidate",
    "Duplicate Reason",
    "Source",
    "Device",
    "Receipt Type",
    "Received Timestamp (UTC)",
    "Processing Status",
    "Candidate ID",
    "Sent to Folder (UTC)",
)
DUPLICATE_HEADERS = (
    "Duplicate Group",
    "Canonical",
    "Candidate ID",
    "Reference / Serial Number",
    "Amount",
    "Currency",
    "Review Status",
    "Processing Status",
    "Source",
    "Device",
    "Received Timestamp (UTC)",
    "OCR Confidence",
    "Duplicate Reason",
    "Protected Image Path",
)
DANGEROUS_PREFIXES = ("=", "+", "-", "@")


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
    return spreadsheet_text(value) if value is not None else ""


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


def _local_date_time(
    row: dict[str, JsonValue],
) -> tuple[date | None, time | None]:
    value = row.get("transaction_occurred_at")
    if not isinstance(value, str):
        return None, None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None, None
    return parsed.date(), parsed.time().replace(tzinfo=None)


def _utc_datetime(row: dict[str, JsonValue], key: str) -> datetime | None:
    value = row.get(key)
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError:
        return None


def _duplicate_status(row: dict[str, JsonValue]) -> str:
    if row.get("is_canonical") is True:
        return "Canonical"
    if row.get("duplicate_of_candidate_id"):
        return "Duplicate"
    return "Unique"


def _style(sheet: Worksheet, widths: tuple[int, ...]) -> None:
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    fill = PatternFill("solid", fgColor="1F4B5B")
    for cell in sheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 30
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=False)


class OpenpyxlWorkbookExporter:
    def build(
        self,
        transactions: list[dict[str, JsonValue]],
        summary: dict[str, JsonValue],
        filters: dict[str, JsonValue],
        exported_at: datetime,
    ) -> bytes:
        workbook = Workbook()
        workbook.properties.title = "Relay transaction export"
        workbook.properties.creator = "Relay"
        workbook.properties.created = exported_at.replace(tzinfo=None)
        workbook.properties.modified = exported_at.replace(tzinfo=None)
        transaction_sheet = workbook.active
        transaction_sheet.title = "Transactions"
        transaction_sheet.append(TRANSACTION_HEADERS)
        for item in transactions:
            transaction_date, transaction_time = _local_date_time(item)
            transaction_sheet.append(
                (
                    transaction_date,
                    transaction_time,
                    _decimal(item, "amount"),
                    _text(item, "currency"),
                    _reference(item),
                    _text(item, "sender_name"),
                    _text(item, "recipient_name"),
                    _text(item, "recipient_identifier"),
                    _text(item, "review_status"),
                    _duplicate_status(item),
                    _text(item, "duplicate_group_id"),
                    _text(item, "duplicate_reason"),
                    _text(item, "source_type"),
                    _text(item, "device_name"),
                    _text(item, "receipt_type"),
                    _utc_datetime(item, "received_at"),
                    _text(item, "processing_status"),
                    _text(item, "id"),
                    _utc_datetime(item, "folder_at"),
                )
            )
        for cell in transaction_sheet["A"][1:]:
            cell.number_format = "yyyy-mm-dd"
        for cell in transaction_sheet["B"][1:]:
            cell.number_format = "hh:mm:ss"
        for cell in transaction_sheet["C"][1:]:
            cell.number_format = "#,##0.00"
        for column in ("P", "S"):
            for cell in transaction_sheet[column][1:]:
                cell.number_format = "yyyy-mm-dd hh:mm:ss"
        _style(
            transaction_sheet,
            (
                12,
                11,
                15,
                10,
                24,
                22,
                22,
                22,
                18,
                16,
                38,
                20,
                16,
                24,
                30,
                22,
                20,
                38,
                22,
            ),
        )

        duplicate_sheet = workbook.create_sheet("Duplicates")
        duplicate_sheet.append(DUPLICATE_HEADERS)
        for item in transactions:
            if not item.get("duplicate_group_id"):
                continue
            duplicate_sheet.append(
                (
                    _text(item, "duplicate_group_id"),
                    "Yes" if item.get("is_canonical") is True else "No",
                    _text(item, "id"),
                    _reference(item),
                    _decimal(item, "amount"),
                    _text(item, "currency"),
                    _text(item, "review_status"),
                    _text(item, "processing_status"),
                    _text(item, "source_type"),
                    _text(item, "device_name"),
                    _utc_datetime(item, "received_at"),
                    item.get("ocr_mean_confidence"),
                    _text(item, "duplicate_reason")
                    or ("REFERENCE_NUMBER" if item.get("is_canonical") else ""),
                    _text(item, "image_url"),
                )
            )
        for cell in duplicate_sheet["E"][1:]:
            cell.number_format = "#,##0.00"
        for cell in duplicate_sheet["K"][1:]:
            cell.number_format = "yyyy-mm-dd hh:mm:ss"
        for cell in duplicate_sheet["L"][1:]:
            cell.number_format = "0.0%"
        _style(
            duplicate_sheet,
            (38, 12, 38, 24, 15, 10, 18, 20, 16, 24, 22, 15, 20, 52),
        )

        summary_sheet = workbook.create_sheet("Summary")
        summary_sheet.append(("Relay transaction export", ""))
        summary_sheet.append(("Date from", summary.get("date_from")))
        summary_sheet.append(("Date to", summary.get("date_to")))
        summary_sheet.append(("Exported at (UTC)", exported_at.replace(tzinfo=None)))
        summary_sheet.append(
            (
                "Filters",
                spreadsheet_text(
                    json.dumps(filters, sort_keys=True, separators=(",", ":"))
                ),
            )
        )
        summary_sheet.append(("Exported transaction rows", len(transactions)))
        summary_sheet.append(
            (
                "Reviewed/final rows",
                sum(
                    1
                    for item in transactions
                    if item.get("review_status") == "AUTO_ACCEPTED"
                ),
            )
        )
        summary_sheet.append(
            (
                "Needs review rows",
                sum(
                    1
                    for item in transactions
                    if item.get("review_status") == "NEEDS_REVIEW"
                ),
            )
        )
        groups = {
            cast(str, item["duplicate_group_id"])
            for item in transactions
            if isinstance(item.get("duplicate_group_id"), str)
        }
        summary_sheet.append(("Duplicate groups in export", len(groups)))
        summary_sheet.append(
            (
                "Duplicate candidates in export",
                sum(
                    1 for item in transactions if item.get("duplicate_of_candidate_id")
                ),
            )
        )
        summary_sheet.append(
            (
                "Processing failures in selected date range",
                summary.get("processing_failure_count", 0),
            )
        )
        summary_sheet.append(("", ""))
        summary_sheet.append(("Currency", "Final amount"))
        totals: dict[str, Decimal] = {}
        for item in transactions:
            currency = item.get("currency")
            amount = _decimal(item, "amount")
            if (
                item.get("review_status") == "AUTO_ACCEPTED"
                and not item.get("duplicate_of_candidate_id")  # same payment again
                and isinstance(currency, str)
                and amount is not None
            ):
                totals[currency] = totals.get(currency, Decimal("0")) + amount
        for currency, amount in sorted(totals.items()):
            summary_sheet.append((spreadsheet_text(currency), amount))
        summary_sheet["B4"].number_format = "yyyy-mm-dd hh:mm:ss"
        for row in range(14, summary_sheet.max_row + 1):
            summary_sheet.cell(row, 2).number_format = "#,##0.00"
        summary_sheet.column_dimensions["A"].width = 38
        summary_sheet.column_dimensions["B"].width = 70
        summary_sheet["A1"].font = Font(size=16, bold=True, color="1F4B5B")
        summary_sheet["A13"].font = Font(bold=True, color="FFFFFF")
        summary_sheet["B13"].font = Font(bold=True, color="FFFFFF")
        summary_sheet["A13"].fill = PatternFill("solid", fgColor="1F4B5B")
        summary_sheet["B13"].fill = PatternFill("solid", fgColor="1F4B5B")
        summary_sheet.freeze_panes = "A2"

        for sheet in workbook.worksheets:
            sheet.sheet_view.showGridLines = False

        output = BytesIO()
        workbook.save(output)
        return output.getvalue()
