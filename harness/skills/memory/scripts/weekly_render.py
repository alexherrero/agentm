"""weekly_render.py — draw a `weekly_email.Week` as an HTML email, its
plain-text copy, and the Markdown report.

The HTML is the approved design ("Quiet Triage", agentm task 200): table
layout and inline styles for Gmail, a `<style>` block only for dark mode and
small screens, no images and no remote resources. A card whose pill is not
green is drawn in full; a green card is a chip in the "All good" block; a
single-line card (the homelab line) is a capsule. Every status carries a shape
as well as a color: ● OK, ▲ Look, ■ Act.
"""
from __future__ import annotations

from html import escape

from weekly_email import ACT, LOOK, OK, VERDICT, Card, Week

SHAPE = {OK: "&#9679;", LOOK: "&#9650;", ACT: "&#9632;"}
WORD = {OK: "OK", LOOK: "Look", ACT: "Act"}
PILL = {OK: ("st-ok", "#D1FAE5", "#065F46", "#10B981"),
        LOOK: ("st-look", "#FEF3C7", "#92400E", "#F59E0B"),
        ACT: ("st-act", "#FEE2E2", "#991B1B", "#DC2626")}
FG = {OK: ("fg-green", "#047857"), LOOK: ("fg-amber", "#B45309"), ACT: ("fg-red", "#DC2626")}
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
TABLE = 'role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'
LINK = 'class="fg-link" style="color:#0369A1;text-decoration:none;font-weight:600;"'

STYLE = """<style type="text/css">
:root { color-scheme: light dark; supported-color-schemes: light dark; }
body, table, td, a { -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }
table, td { mso-table-lspace: 0pt; mso-table-rspace: 0pt; border-collapse: collapse; }
@media only screen and (max-width: 580px) {
.wrap-pad { padding: 12px 8px !important; }
.card-inner { padding: 14px 12px !important; }
.chip-ok { padding: 7px 11px !important; margin: 0 5px 7px 0 !important; }
.num-big { font-size: 20px !important; line-height: 24px !important; }
}
@media (prefers-color-scheme: dark) {
body, .bg-shell { background-color: #111311 !important; color: #E4E7E4 !important; }
.surface-main { background-color: #1A1D1A !important; border-color: #2C312C !important; }
.surface-action { background-color: #211D15 !important; border-color: #5C4516 !important; }
.surface-act { background-color: #241414 !important; border-color: #DC2626 !important; }
.surface-digest { background-color: #171B18 !important; border-color: #28332B !important; }
.surface-capsule { background-color: #181A18 !important; border-color: #2B2F2B !important; }
.surface-tile { background-color: #222622 !important; border-color: #323832 !important; }
.fg-strong { color: #F4F6F4 !important; }
.fg-body { color: #C8CEC8 !important; }
.fg-quiet { color: #8E968E !important; }
.fg-link { color: #7DD3FC !important; }
.fg-amber { color: #FBBF24 !important; }
.fg-green { color: #34D399 !important; }
.fg-red { color: #F87171 !important; }
.line-sep { border-color: #282C28 !important; }
.st-look { background-color: #422806 !important; color: #FDE68A !important; border-color: #B45309 !important; }
.st-ok { background-color: #083322 !important; color: #6EE7B7 !important; border-color: #059669 !important; }
.st-act { background-color: #450A0A !important; color: #FCA5A5 !important; border-color: #DC2626 !important; }
.chip-ok { background-color: #132A20 !important; border-color: #059669 !important; color: #A7F3D0 !important; }
.chip-dot { color: #34D399 !important; }
.meter-bg { background-color: #2C312C !important; }
.badge-urgent { background-color: #272B27 !important; color: #C8CEC8 !important; border-color: #3A3F3A !important; }
}
</style>"""


def _e(s) -> str:
    return escape(str(s), quote=True)


def _a(href, inner: str, attrs: str = LINK) -> str:
    return f'<a href="{_e(href)}" {attrs}>{inner}</a>' if href else f'<span class="fg-strong" style="color:#111827;font-weight:600;">{inner}</span>'


def pill(status: str, label: str = "", size: int = 11) -> str:
    cls, bg, fg, border = PILL[status]
    return (f'<span class="{cls}" style="display:inline-block;font-size:{size}px;font-weight:700;padding:2px 9px;'
            f'border-radius:999px;background:{bg};color:{fg};border:1px solid {border};">'
            f'{SHAPE[status]} {_e(label or WORD[status])}</span>')


def _mark(status) -> str:
    if status is None:
        return ""
    cls, color = FG[status]
    glyph = "&#10003;" if status == OK else SHAPE[status]
    return f'<span class="{cls}" style="color:{color};">{glyph}</span> '


def _emailed(text) -> str:
    if not text:
        return ""
    return ('<div class="badge-urgent" style="display:inline-block;font-size:10px;background:#F3F4F6;color:#4B5563;'
            f'border:1px solid #E5E7EB;border-radius:999px;padding:1px 6px;margin-top:4px;">{_e(text)}</div>')


def _items(card: Card) -> str:
    rows = []
    for i, it in enumerate(card.items):
        last = i == len(card.items) - 1
        sep = "" if last else "border-bottom:1px solid #ECEFEA;"
        rows.append(f'<!-- row:item --><tr><td class="line-sep" style="padding:6px 0;{sep}font-size:14px;">'
                    f'{_mark(it.mark)}{_a(it.link, _e(it.text))}{_emailed(it.emailed)}</td></tr><!-- /row:item -->')
    return f'<table {TABLE}>{"".join(rows)}</table>' if rows else ""


def _tiles(card: Card) -> str:
    if not card.tiles:
        return ""
    cells = []
    for t in card.tiles:
        cls, color = FG[t.status]
        border = {OK: "#E4E8E1", LOOK: "#F59E0B", ACT: "#DC2626"}[t.status]
        inner = (f'<div style="font-size:12px;font-weight:700;color:#111827;"><span class="fg-strong">{_e(t.name)}</span> '
                 f'<span class="{cls}" style="color:{color};">{SHAPE[t.status]}</span></div>{_emailed(t.emailed)}')
        box = (f'class="surface-tile" style="display:block;text-decoration:none;background:#F8FAF7;border:1px solid {border};'
               f'border-radius:8px;padding:6px 4px;text-align:center;"')
        body = f'<a href="{_e(t.link)}" {box}>{inner}</a>' if t.link else f'<div {box}>{inner}</div>'
        cells.append(f'<td width="{100 // len(card.tiles)}%" valign="top" style="padding:0 2px;">{body}</td>')
    return f'<table {TABLE} style="margin-bottom:8px;"><tr>{"".join(cells)}</tr></table>'


def _strip(card: Card) -> str:
    if not card.strip:
        return ""
    dots = "".join(
        f'<span class="{FG[s][0]}" style="color:{FG[s][1]};">{SHAPE[s]}</span>' if s in FG
        else '<span class="fg-quiet" style="color:#9CA3AF;">&#9675;</span>' for s in card.strip)
    return (f'<div class="surface-tile" style="background:#F8FAF7;border:1px solid #E4E8E1;border-radius:8px;'
            f'padding:6px 10px;margin-bottom:10px;"><table {TABLE}><tr>'
            '<td class="fg-quiet" style="font-size:11px;font-weight:600;color:#5F665E;width:32px;">Mon</td>'
            f'<td align="center" style="font-size:14px;letter-spacing:6px;">{dots}</td>'
            '<td align="right" class="fg-quiet" style="font-size:11px;font-weight:600;color:#5F665E;width:32px;">Sun</td>'
            '</tr></table></div>')


def _meters(card: Card) -> str:
    out = []
    for m in card.meters:
        total = m.total or 1
        fill = max(0, min(100, round(100 * m.value / total)))
        color = {OK: "#10B981", LOOK: "#F59E0B", ACT: "#DC2626"}[m.status]
        if m.mark is not None and m.mark < m.value:
            # Over a line (a budget): green up to it, a tick, the status color past it.
            upto = max(1, round(100 * m.mark / total))
            cells = [f'<td width="{upto}%" style="background:#10B981;border-right:2px solid #111827;height:8px;'
                     'font-size:1px;line-height:1px;">&nbsp;</td>',
                     f'<td width="{max(1, fill - upto)}%" style="background:{color};height:8px;font-size:1px;'
                     'line-height:1px;">&nbsp;</td>']
        else:
            cells = [f'<td width="{fill}%" style="background:{color};height:8px;font-size:1px;line-height:1px;">&nbsp;</td>']
        if m.mark is not None and m.mark > m.value:
            gap = max(1, min(100 - fill, round(100 * (m.mark - m.value) / total)))
            cells.append(f'<td width="{gap}%" style="background:#FEF3C7;border-right:2px solid #92400E;height:8px;'
                         f'font-size:1px;line-height:1px;">&nbsp;</td>')
            fill += gap
        if fill < 100:
            cells.append(f'<td width="{100 - fill}%" style="height:8px;font-size:1px;line-height:1px;">&nbsp;</td>')
        label = (f'<table {TABLE} style="margin-bottom:4px;"><tr><td class="fg-link" style="font-size:13px;font-weight:700;'
                 f'color:#0369A1;">{_e(m.label)}</td><td align="right" class="fg-quiet" style="font-size:12px;color:#5F665E;">'
                 f'{_e(m.mark_label)}</td></tr></table>')
        bar = (f'<table {TABLE} class="meter-bg" style="background:#ECEFEA;border-radius:999px;height:8px;">'
               f'<tr>{"".join(cells)}</tr></table>')
        inner = label + bar
        out.append(f'<a href="{_e(m.link)}" style="display:block;text-decoration:none;margin-bottom:8px;">{inner}</a>'
                   if m.link else f'<div style="margin-bottom:8px;">{inner}</div>')
    return "".join(out)


def _numbers(card: Card) -> str:
    if not card.numbers:
        return ""
    cells = []
    for n in card.numbers:
        cls, color = FG[n.status] if n.status else ("fg-strong", "#111827")
        inner = (f'<div class="num-big {cls}" style="font-size:22px;line-height:26px;font-weight:800;color:{color};">'
                 f'{_e(n.value)}</div><div class="fg-body" style="font-size:11px;line-height:14px;font-weight:600;'
                 f'color:#4B5563;margin-top:2px;">{_e(n.label)}</div>')
        box = ('class="surface-tile" style="display:block;text-decoration:none;background:#F8FAF7;border:1px solid #E4E8E1;'
               'border-radius:10px;padding:10px 4px;text-align:center;"')
        body = f'<a href="{_e(n.link)}" {box}>{inner}</a>' if n.link else f'<div {box}>{inner}</div>'
        cells.append(f'<!-- row:number --><td width="{100 // len(card.numbers)}%" valign="top" style="padding:0 2px;">'
                     f'{body}</td><!-- /row:number -->')
    return f'<table {TABLE} style="margin-bottom:8px;"><tr>{"".join(cells)}</tr></table>'


def _footer(card: Card) -> str:
    if not card.footer or not card.footer[0]:
        return ""
    text, link = card.footer
    inner = (f'<a href="{_e(link)}" class="fg-link" style="color:#0369A1;text-decoration:underline;font-weight:600;">{_e(text)}</a>'
             if link else f'<span class="fg-quiet" style="color:#5F665E;">{_e(text)}</span>')
    return f'<div class="line-sep" style="border-top:1px solid #ECEFEA;margin-top:6px;padding-top:8px;font-size:13px;">{inner}</div>'


def full_card(card: Card) -> str:
    if card.tray:
        act = card.status == ACT
        surface = ('class="surface-act" style="background:#FFF8F8;border:2px solid #DC2626;border-radius:14px;"' if act
                   else 'class="surface-action" style="background:#FFFDF7;border:2px solid #F59E0B;border-radius:14px;"')
        title_style = "font-size:17px;font-weight:800;"
    else:
        surface = 'class="surface-main" style="background:#FFFFFF;border:1px solid #DCE0D9;border-radius:14px;"'
        title_style = "font-size:16px;font-weight:700;"
    head = (f'<table {TABLE} style="margin-bottom:10px;"><tr><td align="left"><span class="fg-strong" '
            f'style="{title_style}color:#111827;">{card.icon} {_e(card.title)}</span></td>'
            f'<td align="right">{pill(card.status)}</td></tr></table>')
    body = _tiles(card) + _strip(card) + _numbers(card) + _meters(card) + _items(card) + _footer(card)
    return (f'<!-- card:{card.slug} --><tr><td id="{card.slug}" style="padding:0 0 12px 0;">'
            f'<table {TABLE} {surface}><tr><td class="card-inner" style="padding:16px 18px;">{head}{body}'
            f'</td></tr></table></td></tr><!-- /card:{card.slug} -->')


def line_card(card: Card) -> str:
    it = card.items[0] if card.items else None
    words = _a(it.link, _e(it.text)) if it else ""
    return (f'<!-- card:{card.slug} --><tr><td id="{card.slug}" style="padding:0 0 12px 0;">'
            f'<table {TABLE} class="surface-capsule" style="background:#FFFFFF;border:1px solid #DCE0D9;border-radius:999px;">'
            f'<tr><td style="padding:10px 16px;font-size:14px;line-height:20px;">{pill(card.status)} '
            f'<strong class="fg-strong" style="color:#111827;">{card.icon} {_e(card.title)}</strong> &middot; {words}'
            f'</td></tr></table></td></tr><!-- /card:{card.slug} -->')


def chip(card: Card, week: Week) -> str:
    href = None
    if week.report_link:
        href = f"{week.report_link}#{card.slug}"
    inner = (f'<span class="chip-dot" style="color:#047857;font-weight:700;">&#9679;</span> {card.icon} '
             f'<strong class="fg-strong" style="color:#111827;font-weight:600;">{_e(_chip_name(card))}</strong>'
             + (f" &middot; {_e(card.chip)}" if card.chip else ""))
    style = ('class="chip-ok" style="display:inline-block;text-decoration:none;background:#F0FDF4;border:1px solid #6EE7B7;'
             'border-radius:999px;padding:7px 13px;margin:0 6px 8px 0;font-size:13px;line-height:18px;color:#065F46;"')
    body = f'<a href="{_e(href)}" {style}>{inner}</a>' if href else f'<span {style}>{inner}</span>'
    return f'<!-- chip:ok -->{body}<!-- /chip:ok -->'


def _chip_name(card: Card) -> str:
    short = {"agent-health": "Health", "what-the-agent-learned": "Learned",
             "agent-and-memory-research": "Research",
             "projects-in-motion": "Projects"}  # root-casing: a chip label, not a path
    return short.get(card.slug, card.title.split(",")[0].split(" and ")[0])


def _glance(week: Week) -> str:
    out = []
    for c in week.cards:
        if c.hidden:
            continue
        cls, bg, fg, border = PILL[c.status]
        target = "#all-good" if c.status == OK else f"#{c.slug}"
        out.append(f'<a href="{target}" class="{cls}" style="display:inline-block;text-decoration:none;padding:3px 6px;'
                   f'margin:0 4px 4px 0;border-radius:6px;background:{bg};color:{fg};border:1px solid {border}">'
                   f'{c.icon}{SHAPE[c.status]}</a>')
    return '<div class="line-sep" style="border-top:1px solid #ECEFEA;padding-top:10px">' + "".join(out) + "</div>"


def html(week: Week) -> str:
    status = week.status
    cls, bg, fg, border = PILL[status]
    verdict = VERDICT[status]
    shown = [c for c in week.cards if not c.hidden]
    full = [c for c in shown if c.status != OK]
    greens = [c for c in shown if c.status == OK]
    header = (
        f'<tr><td style="padding:0 0 12px 0;"><table {TABLE} class="surface-main" style="background:#FFFFFF;'
        f'border:1px solid #DCE0D9;border-radius:14px;"><tr><td class="card-inner" style="padding:18px 20px;">'
        f'<table {TABLE}><tr><td align="left" style="vertical-align:middle;">'
        f'<div class="fg-quiet" style="font-size:12px;font-weight:600;color:#5F665E;letter-spacing:0.03em;">'
        f'{week.end:%a} {week.end.day} {week.end:%b %Y}</div>'
        '<div class="fg-strong" style="font-size:22px;font-weight:800;color:#111827;letter-spacing:-0.02em;'
        'line-height:28px;margin-top:2px;">AgentM Weekly</div></td>'
        f'<td align="right" style="vertical-align:top;"><span class="{cls}" style="display:inline-block;font-size:12px;'
        f'font-weight:700;padding:5px 11px;border-radius:999px;background:{bg};color:{fg};border:1px solid {border};">'
        f'{SHAPE[status]} {_e(verdict)}</span></td></tr></table>'
        f'<p class="fg-body" style="margin:8px 0 12px 0;font-size:15px;line-height:21px;color:#374151;font-weight:500;">'
        f'{_e(week.headline)}</p>{_glance(week)}</td></tr></table></td></tr>')
    body = []
    for c in full:
        body.append(line_card(c) if c.line else full_card(c))
    if greens:
        body.append(
            f'<!-- card:all-good --><tr><td id="all-good" style="padding:0 0 16px 0;"><table {TABLE} class="surface-digest" '
            'style="background:#F7FAF7;border:1px solid #CFE0D2;border-radius:14px;"><tr><td class="card-inner" '
            'style="padding:14px 16px 8px 16px;"><div class="fg-quiet" style="font-size:12px;font-weight:700;color:#5F665E;'
            'letter-spacing:0.03em;margin-bottom:8px;">ALL GOOD</div><div>'
            + "".join(chip(c, week) for c in greens) + '</div></td></tr></table></td></tr><!-- /card:all-good -->')
    report = (f'<div style="font-size:14px;margin-bottom:4px;"><a href="{_e(week.report_link)}" class="fg-link" '
              'style="color:#0369A1;font-weight:700;text-decoration:underline;">Full report with every reading &rarr;</a></div>'
              if week.report_link else "")
    footer = (f'<tr><td style="padding:4px 8px 20px 8px;text-align:center;">{report}<div class="fg-quiet" '
              'style="font-size:12px;color:#5F665E;">Sent by AgentM every Sunday morning.</div></td></tr>')
    pre = f"{VERDICT[status]}: {week.headline}"
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        '<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">'
        f'<title>AgentM weekly · {_e(verdict)}</title>{STYLE}</head>'
        f'<body class="bg-shell" style="margin:0;padding:0;background:#F1F3EF;color:#18181B;font-family:{FONT};'
        'font-size:15px;line-height:1.5;">'
        '<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:#F1F3EF;">'
        f'{_e(pre)}</div>'
        f'<table {TABLE} class="bg-shell" style="background:#F1F3EF;width:100%;"><tr><td align="center" class="wrap-pad" '
        f'style="padding:20px 12px;"><table {TABLE} style="max-width:600px;width:100%;margin:0 auto;">'
        + header + "".join(body) + footer +
        '</table></td></tr></table></body></html>\n')


_TEXT_MARK = {OK: "✓ ", LOOK: "▲ ", ACT: "■ ", None: "- "}


def text(week: Week) -> str:
    lines = [f"AgentM weekly · week ending {week.end:%a} {week.end.day} {week.end:%b %Y}",
             f"{VERDICT[week.status].upper()}: {week.headline}", ""]
    shown = [c for c in week.cards if not c.hidden]
    for c in [c for c in shown if c.status != OK]:
        lines.append(f"[{WORD[c.status].upper()}] {c.title}")
        for t in c.tiles:
            lines.append(f"  {t.name}: {WORD[t.status]}" + (f" ({t.emailed})" if t.emailed else ""))
        for n in c.numbers:
            lines.append(f"  {n.value} {n.label}")
        for m in c.meters:
            lines.append(f"  {m.label}" + (f" ({m.mark_label})" if m.mark_label else ""))
        for it in c.items:
            lines.append(f"  {_TEXT_MARK.get(it.mark, '- ')}{it.text}" + (f"  {it.link}" if it.link else ""))
        if c.footer and c.footer[0]:
            lines.append(f"  {c.footer[0]}")
        lines.append("")
    greens = [c for c in shown if c.status == OK]
    if greens:
        lines.append("ALL GOOD: " + " · ".join(f"{_chip_name(c)} ({c.chip})" if c.chip else _chip_name(c) for c in greens))
        lines.append("")
    if week.report_link:
        lines.append(f"Full report: {week.report_link}")
    return "\n".join(lines) + "\n"


def markdown(week: Week) -> str:
    """The full report: every card, green ones included, under a heading whose
    anchor the email's chips link to."""
    out = ["---", f"title: AgentM weekly — {week.end:%Y-%m-%d}", "kind: report", f"date: {week.end:%Y-%m-%d}",
           f"verdict: {VERDICT[week.status]}", "generated_by: weekly_email.py", "---", "",
           f"# AgentM weekly — week ending {week.end:%a} {week.end.day} {week.end:%b %Y}", "",
           f"**{VERDICT[week.status]}.** {week.headline}", "",
           "| Status | Card | In short |", "|---|---|---|"]
    for c in week.cards:
        if c.hidden:
            continue
        short = c.chip if c.status == OK else "; ".join(it.text for it in c.items[:2]) or c.chip
        out.append(f"| {WORD[c.status].upper()} | [{c.title}](#{c.slug}) | {short.replace('|', '/')} |")
    out.append("")
    for c in week.cards:
        if c.hidden:
            continue
        out += [f'<a id="{c.slug}"></a>', "", f"## {c.title}", "", f"*{WORD[c.status]}*", ""]
        for it in c.items:
            label = f"[{it.text}]({it.link})" if it.link else it.text
            out.append(f"- {_TEXT_MARK.get(it.mark, '').strip()} {label}".replace("-  ", "- ")
                       + (f" *({it.emailed})*" if it.emailed else ""))
        for n in c.numbers:
            out.append(f"- {n.value} {n.label}")
        out += c.detail
        if c.footer and c.footer[0]:
            out.append(f"- {c.footer[0]}" + (f" ([open]({c.footer[1]}))" if c.footer[1] else ""))
        out.append("")
    out.append(f"Written {week.now:%Y-%m-%d %H:%M %Z} by weekly_email.py.")
    return "\n".join(out) + "\n"
