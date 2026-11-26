"""Arabic versions of the reports: the shift email, its plain text and the
Excel file. The reports are built in English; these helpers translate their
labels (never the data in them) and turn the layout right to left."""

import re
from collections.abc import Callable
from io import BytesIO

LANGUAGES = ("en", "ar")

AR = {
    # Email and Excel labels
    "Confirmed money": "الفلوس المؤكدة",
    "Confirmed receipts": "إيصالات مؤكدة",
    "Auto-confirmed": "اتأكدت تلقائي",
    "Auto-confirmed (bank SMS)": "اتأكدت تلقائي (رسالة البنك)",
    "Confirmed by a reviewer": "أكدها مراجع",
    "Waiting for review": "مستنية مراجعة",
    "Duplicate groups": "مجموعات مكررة",
    "Receipts matched to a bank SMS": "إيصالات اتطابقت مع رسالة بنك",
    "Receipts waiting for an SMS": "إيصالات مستنية رسالة",
    "Receipts with no matching SMS": "إيصالات من غير رسالة مطابقة",
    "Receipts with several possible SMS": "إيصالات ليها أكتر من رسالة محتملة",
    "Bank SMS used for a receipt": "رسايل بنك اتستخدمت لإيصال",
    "Bank SMS not used yet": "رسايل بنك لسه ما اتستخدمتش",
    "Unused SMS money": "فلوس رسايل ما اتستخدمتش",
    "SMS without an amount": "رسايل من غير مبلغ",
    "Receipt images received": "صور إيصالات وصلت",
    "Bank SMS received": "رسايل بنك وصلت",
    "Images still processing": "صور لسه بتتقري",
    "Images that failed": "صور فشلت",
    "Summary": "الملخص",
    "Bank SMS matching": "مطابقة رسايل البنك",
    "Activity": "النشاط",
    "Per phone (received / confirmed / SMS matched / confirmed money)": (
        "حسب الموبايل (وصل / اتأكد / اتطابق مع رسالة / الفلوس المؤكدة)"
    ),
    "No receipts today.": "مفيش إيصالات النهارده.",
    "The full list is in the attached Excel file.": (
        "القايمة كاملة في ملف Excel المرفق."
    ),
    "The full list is attached as Excel.": "القايمة كاملة مرفقة كملف Excel.",
    "Sent by Relay": "اتبعت من Relay",
    "Confirmed": "مؤكد",
    "Confirmed (auto)": "مؤكد (تلقائي)",
    "Needs review": "محتاج مراجعة",
    "Rejected": "مرفوض",
    "SMS not used": "رسايل ما اتستخدمتش",
    "Per phone": "حسب الموبايل",
    "Phone": "الموبايل",
    "Received": "وصل",
    "SMS matched": "اتطابق مع رسالة",
    "Money (EGP)": "الفلوس (ج.م)",
    "Receipts": "الإيصالات",
    "Status": "الحالة",
    "Count": "العدد",
    "Item": "البند",
    "Value": "القيمة",
    "Shift report": "تقرير الوردية",
    "Daily report": "التقرير اليومي",
    "DAILY SUMMARY": "ملخص اليوم",
    "Nothing to show.": "مفيش حاجة.",
    # Excel only
    "Transactions": "المعاملات",
    "Duplicates": "المكرر",
    "Relay · Receipts report": "Relay · تقرير الإيصالات",
    "All dates": "كل التواريخ",
    "By status": "حسب الحالة",
    "By phone": "حسب الموبايل",
    "Amount": "المبلغ",
    "Currency": "العملة",
    "No receipts in this period.": "مفيش إيصالات في الفترة دي.",
    "No phones sent receipts.": "مفيش موبايل بعت إيصالات.",
    "Confirmed money counts each payment once (duplicates left out).": (
        "الفلوس المؤكدة بتحسب كل دفعة مرة واحدة (من غير المكرر)."
    ),
    "Times are Cairo time. Full list on the Transactions sheet.": (
        "الأوقات بتوقيت القاهرة. القايمة كاملة في شيت المعاملات."
    ),
    "Received (Cairo)": "وصل (القاهرة)",
    "Receipt date": "تاريخ الإيصال",
    "Receipt time": "وقت الإيصال",
    "Why": "السبب",
    "Bank SMS": "رسالة البنك",
    "Reference": "المرجع",
    "Sender": "المرسل",
    "Recipient": "المستلم",
    "Recipient phone / ID": "رقم / معرف المستلم",
    "Duplicate": "مكرر",
    "Receipt type": "نوع الإيصال",
    "Receipt ID": "رقم الإيصال في Relay",
    "Group": "المجموعة",
    "Role": "الدور",
    "Reason": "السبب",
    "OCR confidence": "دقة القراءة",
    "Original": "الأصلي",
    "Same reference": "نفس المرجع",
    "Payment request (no money moved)": "طلب دفع (مفيش فلوس اتحولت)",
    "Rejected by a reviewer": "رفضه مراجع",
    "Transfer failed": "التحويل فشل",
    "Not recognised as a receipt": "مش متعرف عليه كإيصال",
    "Possible duplicate": "تكرار محتمل",
    "Possible duplicate: same reference (surely a copy)": (
        "تكرار محتمل: نفس المرجع (نسخة أكيدة)"
    ),
    "Possible duplicate: same amount and sender, no reference": (
        "تكرار محتمل: نفس المبلغ والمرسل، مفيش مرجع"
    ),
    "Possible duplicate: same amount, sender unreadable": (
        "تكرار محتمل: نفس المبلغ، والمرسل مش مقروء"
    ),
    "Recipient not clear": "المستلم مش واضح",
    "Sent to someone else": "اتحول لحد تاني",
    "Same amount twice that day": "نفس المبلغ مرتين في نفس اليوم",
    "Still being read": "لسه بيتقري",
    "No bank SMS yet": "لسه مفيش رسالة بنك",
    "Fields to confirm": "بيانات محتاجة تأكيد",
    "Matched": "اتطابق",
    "Waiting": "مستني",
    "No SMS": "مفيش رسالة",
    "Several SMS": "أكتر من رسالة",
    "Bank transfer": "تحويل بنكي",
    "Unknown": "مش معروف",
}

DAYS = {
    "Monday": "الاتنين",
    "Tuesday": "التلات",
    "Wednesday": "الأربع",
    "Thursday": "الخميس",
    "Friday": "الجمعة",
    "Saturday": "السبت",
    "Sunday": "الحد",
}
MONTHS = {
    "January": "يناير",
    "February": "فبراير",
    "March": "مارس",
    "April": "أبريل",
    "May": "مايو",
    "June": "يونيو",
    "July": "يوليو",
    "August": "أغسطس",
    "September": "سبتمبر",
    "October": "أكتوبر",
    "November": "نوفمبر",
    "December": "ديسمبر",
}


def _clock(value: str) -> str:
    return value.replace("AM", "ص").replace("PM", "م")


PATTERNS: list[tuple[re.Pattern[str], Callable[[re.Match[str]], str]]] = [
    (
        re.compile(r"^Relay shift report: (\S+) shift (\d+)$"),
        lambda m: f"تقرير وردية Relay: {m[1]} وردية {m[2]}",
    ),
    (
        re.compile(r"^Relay daily report: (\S+)$"),
        lambda m: f"تقرير Relay اليومي: {m[1]}",
    ),
    (re.compile(r"^SHIFT (\d+)$"), lambda m: f"وردية {m[1]}"),
    (
        re.compile(r"^(.+) · Shift (\d+) \((.*) to (.*)\)$"),
        lambda m: f"{m[1]} · وردية {m[2]} (من {m[3]} لحد {m[4]})",
    ),
    (re.compile(r"^Group (\d+)$"), lambda m: f"مجموعة {m[1]}"),
    (
        re.compile(r"^(\w+day) (\d{2}) (\w+) (\d{4})$"),
        lambda m: f"{DAYS.get(m[1], m[1])} {int(m[2])} {MONTHS.get(m[3], m[3])} {m[4]}",
    ),
    (
        re.compile(r"^(.+) to (.+), by when receipts reached each phone$"),
        lambda m: (
            f"من {_clock(m[1])} لحد {_clock(m[2])}، حسب وقت وصول الإيصال للموبايل"
        ),
    ),
    (
        re.compile(r"^Exported (.+) \(Cairo time\)$"),
        lambda m: f"اتصدّر {m[1]} (بتوقيت القاهرة)",
    ),
    (
        re.compile(r"^Images that failed to process: (\d+)$"),
        lambda m: f"صور فشلت قراءتها: {m[1]}",
    ),
    (re.compile(r"^Filters: (.*)$"), lambda m: f"الفلاتر: {m[1]}"),
]


def translate(text: str) -> str:
    """A label in Arabic; anything that is not a known label stays as it is."""
    stripped = text.strip()
    if not stripped:
        return text
    found = AR.get(stripped)
    if found is None:
        for pattern, build in PATTERNS:
            match = pattern.match(stripped)
            if match:
                found = build(match)
                break
    if found is None:
        return text
    start = text[: len(text) - len(text.lstrip())]
    end = text[len(text.rstrip()) :]
    return start + found + end


def arabic_text(text: str) -> str:
    """The plain-text email: each line, or the label before ": ", in Arabic."""
    lines = []
    for line in text.split("\n"):
        whole = translate(line)
        if whole != line or ": " not in line:
            lines.append(whole)
            continue
        label, value = line.split(": ", 1)
        lines.append(f"{translate(label)}: {value}")
    return "\n".join(lines)


_TEXT_NODE = re.compile(r">([^<>]+)<")
_SENT_BY = "Sent by Relay &middot; "


def arabic_html(html: str) -> str:
    """The HTML email in Arabic, laid out right to left."""
    html = html.replace(_SENT_BY, translate("Sent by Relay") + " &middot; ")
    html = _TEXT_NODE.sub(lambda m: ">" + translate(m[1]) + "<", html)
    # Mirror the layout: what sat on the right moves to the left, and back.
    html = (
        html.replace("text-align:right", "text-align:__end")
        .replace("text-align:left", "text-align:right")
        .replace("text-align:__end", "text-align:left")
        .replace("align='right'", "align='__end'")
        .replace("align='left'", "align='right'")
        .replace("align='__end'", "align='left'")
        .replace("padding-right:", "padding-__end:")
        .replace("padding-left:", "padding-right:")
        .replace("padding-__end:", "padding-left:")
    )
    return html.replace("<html>", "<html dir='rtl' lang='ar'>", 1).replace(
        "<body style='", "<body dir='rtl' style='direction:rtl;", 1
    )


def arabic_workbook(data: bytes) -> bytes:
    """The Excel file in Arabic: labels, sheet names, right-to-left sheets."""
    from openpyxl import load_workbook

    book = load_workbook(BytesIO(data))
    for sheet in book.worksheets:
        sheet.title = translate(sheet.title)[:31]
        sheet.sheet_view.rightToLeft = True
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and not cell.value.startswith("="):
                    cell.value = translate(cell.value)
    output = BytesIO()
    book.save(output)
    return output.getvalue()
