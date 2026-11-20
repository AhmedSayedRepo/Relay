"""One look for every Relay email: masthead, hero pill and title, a strip
of key numbers, sections with a dot, count and table, and a footer. Plain
tables and inline styles only, so Outlook, Gmail and phone mail apps show
it the same. Every value is escaped here; callers pass plain text."""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

PAPER = "#E9ECED"
CARD = "#FFFFFF"
TINT = "#F7F9F9"
INK = "#14232A"
INK2 = "#5F6B70"
INK3 = "#97A2A6"
LINE = "#E1E6E8"
LINE2 = "#EEF2F3"
BRAND = "#103E46"
BRAND_SOFT = "#DCEBEC"
ACCENT = "#A95028"
TONES = {
    "brand": (BRAND, BRAND_SOFT),
    "ok": ("#1F7A4D", "#E2F3EA"),
    "warn": ("#9A5B00", "#FDF0D8"),
    "bad": ("#A23B32", "#F9E1DE"),
    "muted": (INK2, LINE2),
}
# Double quotes inside: the style attributes are single-quoted.
UI = '"Segoe UI",Roboto,Helvetica,Arial,sans-serif'
MONO = '"SFMono-Regular",Consolas,Menlo,monospace'


def _e(value: object) -> str:
    return escape(str(value))


def pill(text: str, tone: str = "brand") -> str:
    ink, soft = TONES[tone]
    return (
        f"<span style='display:inline-block;background:{soft};color:{ink};"
        f"font-size:11px;font-weight:700;letter-spacing:.4px;padding:4px 10px;"
        f"border-radius:20px;white-space:nowrap'>{_e(text)}</span>"
    )


def kpis(items: Sequence[tuple[str, str, str]]) -> str:
    """(label, value, tone) cells in one bordered strip."""
    cells = ""
    for index, (label, value, tone) in enumerate(items):
        border = "" if index == 0 else f"border-left:1px solid {LINE2};"
        cells += (
            f"<td style='{border}padding:13px 6px 14px;text-align:center;"
            f"vertical-align:top'>"
            f"<div style='font-size:9.5px;font-weight:700;letter-spacing:1px;"
            f"color:{INK3};text-transform:uppercase'>{_e(label)}</div>"
            f"<div style='font-family:{MONO};font-size:18px;font-weight:700;"
            f"white-space:nowrap;"
            f"color:{TONES[tone][0]};margin-top:6px;line-height:1.1'>{_e(value)}</div>"
            f"</td>"
        )
    return (
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        f"style='border:1px solid {LINE};border-radius:12px;table-layout:fixed'>"
        f"<tr>{cells}</tr></table>"
    )


def section_head(title: str, count: object | None = None, dot: str = BRAND) -> str:
    badge = (
        f"<td valign='middle' style='padding-left:9px'><span style='font-family:{MONO};"
        f"font-size:11px;font-weight:700;color:{INK2};background:{LINE2};"
        f"border-radius:20px;padding:3px 9px'>{_e(count)}</span></td>"
        if count is not None
        else ""
    )
    return (
        f"<table role='presentation' cellpadding='0' cellspacing='0'><tr>"
        f"<td valign='middle' style='padding-right:10px'><span style='display:"
        f"inline-block;width:9px;height:9px;border-radius:50%;background:{dot}'>"
        f"</span></td><td valign='middle' style='font-size:14.5px;font-weight:700;"
        f"color:{INK};letter-spacing:-.2px'>{_e(title)}</td>{badge}</tr></table>"
    )


Cell = str | tuple[str, str]  # plain text, or (text, tone) shown as a pill


def table(
    headers: Sequence[str],
    rows: Sequence[Sequence[Cell]],
    right: Sequence[int] = (),
    empty: str = "Nothing to show.",
) -> str:
    """A bordered table; columns in ``right`` (numbers) align right."""

    def side(index: int) -> str:
        return "text-align:right;" if index in right else ""

    def pad(index: int, count: int) -> str:
        first, last = index == 0, index == count - 1
        end, start = ("14px" if last else "8px"), ("14px" if first else "8px")
        return f"padding:11px {end} 11px {start}"

    count = len(headers)
    head = "".join(
        f"<td style='{pad(i, count)};font-size:10.5px;letter-spacing:.5px;"
        f"text-transform:uppercase;color:{INK3};font-weight:700;{side(i)}'>{_e(h)}</td>"
        for i, h in enumerate(headers)
    )
    body = ""
    for row in rows:
        cells = ""
        for i, cell in enumerate(row):
            if isinstance(cell, tuple):
                content = pill(cell[0], cell[1]) if cell[0] else ""
            else:
                content = _e(cell)
            weight = "700" if i in right else ("600" if i == 0 else "400")
            font = f"font-family:{MONO};" if i in right else ""
            cells += (
                f"<td style='{pad(i, count)};border-top:1px solid {LINE2};"
                f"font-size:13px;font-weight:{weight};color:{INK};{font}{side(i)}'>"
                f"{content}</td>"
            )
        body += f"<tr>{cells}</tr>"
    if not rows:
        body = (
            f"<tr><td colspan='{count}' style='padding:12px 14px;font-size:13px;"
            f"color:{INK2}'>{_e(empty)}</td></tr>"
        )
    return (
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        f"style='border:1px solid {LINE};border-radius:12px;border-collapse:separate'>"
        f"<tr style='background:{TINT}'>{head}</tr>{body}</table>"
    )


def facts(rows: Sequence[tuple[str, str]]) -> str:
    """Two-column label / value list."""
    return table(("Item", "Value"), [(label, value) for label, value in rows], (1,))


def note(text: str) -> str:
    return f"<div style='margin-top:10px;font-size:12px;color:{INK2}'>{_e(text)}</div>"


def page(
    *,
    kind: str,
    stamp: str,
    badge: tuple[str, str],
    title: str,
    subtitle: str,
    strip: str = "",
    sections: Sequence[str] = (),
    footer: str = "",
) -> str:
    """The whole email. ``sections`` are HTML built with the helpers above."""
    mark = (
        f"<table role='presentation' cellpadding='0' cellspacing='0'><tr>"
        f"<td width='34' height='34' align='center' valign='middle' style='width:34px;"
        f"height:34px;background:{BRAND};border-radius:9px;color:#FFFFFF;"
        f"font-size:17px;font-weight:800;font-family:{UI}'>R</td></tr></table>"
    )
    masthead = (
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0'><tr>"
        f"<td width='34' valign='middle' style='padding-right:13px'>{mark}</td>"
        f"<td valign='middle'><div style='font-size:15px;font-weight:700;color:{INK};"
        f"letter-spacing:-.2px'>Relay</div><div style='font-size:12px;font-weight:700;"
        f"color:{ACCENT};margin-top:2px'>{_e(kind)}</div></td>"
        f"<td valign='middle' align='right' style='font-family:{MONO};font-size:11px;"
        f"color:{INK3};font-weight:700'>{_e(stamp)}</td></tr></table>"
    )
    hero = (
        pill(*badge)
        + f"<div style='font-size:23px;font-weight:700;letter-spacing:-.5px;"
        f"color:{INK};line-height:1.2;margin:14px 0 0'>{_e(title)}</div>"
        f"<div style='font-size:13px;color:{INK2};font-weight:600;margin-top:8px'>"
        f"{_e(subtitle)}</div>"
    )
    blocks = "".join(
        f"<tr><td style='padding:24px 32px 6px;border-top:1px solid {LINE}'>"
        f"{block}</td></tr>"
        for block in sections
    )
    strip_row = (
        f"<tr><td style='padding:18px 32px 6px'>{strip}</td></tr>" if strip else ""
    )
    end = (
        f"<div style='font-size:11.5px;font-weight:600;color:{INK3};line-height:1.6'>"
        f"Sent by Relay{(' &middot; ' + _e(footer)) if footer else ''}</div>"
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' "
        "content='width=device-width,initial-scale=1'></head>"
        f"<body style='margin:0;padding:0;background:{PAPER};"
        "-webkit-text-size-adjust:100%'>"
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        f"style='background:{PAPER}'><tr>"
        "<td align='center' style='padding:26px 12px 48px'>"
        f"<table role='presentation' width='680' cellpadding='0' cellspacing='0' "
        f"style='width:100%;max-width:680px;background:{CARD};border:1px solid {LINE};"
        f"border-radius:16px;overflow:hidden;font-family:{UI};color:{INK}'>"
        f"<tr><td style='height:3px;line-height:3px;font-size:0;background:{BRAND}'>"
        "&nbsp;</td></tr>"
        f"<tr><td style='padding:24px 32px 0'>{masthead}</td></tr>"
        f"<tr><td style='padding:18px 32px 4px'>{hero}</td></tr>"
        f"{strip_row}{blocks}"
        f"<tr><td style='padding:18px 32px 24px;border-top:1px solid {LINE};"
        f"background:{TINT}'>{end}</td></tr>"
        "</table></td></tr></table></body></html>"
    )
