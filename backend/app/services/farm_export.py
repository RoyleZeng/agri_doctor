"""Standalone SVG export of selected plans. All user strings are XML-escaped."""
from datetime import date
from xml.sax.saxutils import escape

KINDS = {"stage": ("生育時期", "#e3eddb", "#426136"), "care": ("農事管理", "#e4f0ec", "#245c45"), "nutrition": ("肥培管理", "#eaf0f8", "#3a628c"), "pest": ("病蟲害巡查", "url(#patrol)", "#865e20")}


def xml(value):
    # XML 1.0 excludes low control characters even when text is otherwise escaped.
    return escape("".join(c for c in str(value) if c in "\t\n\r" or (32 <= ord(c) <= 0xD7FF) or (0xE000 <= ord(c) <= 0xFFFD) or (0x10000 <= ord(c) <= 0x10FFFF)), {'"': "&quot;", "'": "&apos;"})


def render_svg(farm_name, plans, visible_kinds):
    if not plans:
        raise ValueError("請先選擇作物")
    def as_date(value):
        return value if isinstance(value, date) else date.fromisoformat(value)
    start = min(as_date(p["planted_on"]) for p in plans)
    end = max(as_date(p["end_on"]) for p in plans)
    span = (end - start).days + 1
    width, label, track = 1500, 170, 1280
    step = max(7, round(span / 12))
    elements = []
    def text(x, y, value, size=13, color="#44514a", weight="400"):
        return f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}">{xml(value)}</text>'
    elements += [text(30, 42, f"{farm_name} · 作物管理複合圖", 25, "#245c45", "700"), text(30, 70, f"{start} — {end}　｜　{len(plans)} 期作物"), text(30, 96, "日期為可調整的規劃估計；病蟲害色帶為巡查窗口，不代表已發病或發生機率。", 13), text(30, 118, "未依品種、期作、所在地或天氣校正；請依田間實際生育與植物醫生判讀調整。", 13)]
    y = 155
    for p in plans:
        elements.append(f'<rect x="25" y="{y}" width="1450" height="38" rx="6" fill="#f0f5eb"/>')
        elements.append(text(38, y + 25, f"{p['crop_name']} · {p['field_name']} · {p.get('variety') or '品種未填'}", 16, "#245c45", "600"))
        elements.append(text(1110, y + 25, f"{p['planted_on']} — {p['end_on']}", 12))
        y += 64
        from datetime import timedelta
        for offset in range(0, span, step):
            d = start + timedelta(days=offset)
            elements.append(text(round(label + offset / span * track), y, f"{d.month}/{d.day}", 11))
        y += 12
        for kind, (name, fill, color) in KINDS.items():
            if kind not in visible_kinds:
                continue
            lanes = []
            events = sorted((e for e in p["events"] if e["kind"] == kind), key=lambda e: str(e["start_on"]))
            for e in events:
                lane = next((row for row in lanes if as_date(row[-1]["end_on"]) < as_date(e["start_on"])), None)
                if lane is None:
                    lane = []
                    lanes.append(lane)
                lane.append(e)
            height = max(1, len(lanes)) * 45 + 10
            elements.append(text(38, y + 30, name, 13))
            elements.append(f'<path d="M25 {y+height}H1475" stroke="#e2e7e1"/>')
            for offset in range(0, span, step):
                x = label + offset / span * track
                elements.append(f'<path d="M{x:.1f} {y}v{height}" stroke="#e7ece6" stroke-dasharray="3 3"/>')
            for index, lane in enumerate(lanes):
                for e in lane:
                    x = label + (as_date(e["start_on"]) - start).days / span * track
                    bar_width = max(3, ((as_date(e["end_on"]) - as_date(e["start_on"])).days + 1) / span * track)
                    top = y + 8 + index * 45
                    identifier = f"clip-{len(elements)}"
                    caption = ("✓ " if e["completed"] else "") + e["title"]
                    info = f"{e['title']} / {e['start_on']} — {e['end_on']} / {e['detail']} / 紀錄：{e['note']} / 參考：{','.join(e['source_ids'])}"
                    elements.append(f'<g><title>{xml(info)}</title><rect x="{x:.1f}" y="{top}" width="{bar_width:.1f}" height="33" rx="4" fill="{fill}" opacity="{0.6 if e["completed"] else 1}"/><clipPath id="{identifier}"><rect x="{x:.1f}" y="{top}" width="{bar_width:.1f}" height="33"/></clipPath><g clip-path="url(#{identifier})">{text(round(x+6), top+22, caption, 12, color)}</g></g>')
            if not events:
                elements.append(text(label + 8, y + 30, "尚無事項", 12, "#7b877f"))
            y += height
        y += 24
    elements.append(text(30, y + 12, "參考資料（抓取日不等於官方更新日；人工修改內容需另行確認）", 14, "#245c45", "600"))
    y += 36
    sources = {(s["id"], s["sha256"]): s for p in plans for s in p["sources"]}
    for s in sources.values():
        elements.append(text(30, y, f"{s['title']} · {s['publisher']} · 抓取 {s['fetched_at'][:10]}", 12))
        elements.append(text(30, y + 18, s["url"], 10))
        y += 48
    description = "".join(elements)
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{y+24}" viewBox="0 0 {width} {y+24}" role="img" aria-labelledby="title desc"><title id="title">{xml(farm_name)}作物管理複合圖</title><desc id="desc">已選作物的生育階段、農事、肥培與病蟲害巡查。日期為規劃估計。</desc><defs><pattern id="patrol" patternUnits="userSpaceOnUse" width="8" height="8"><rect width="8" height="8" fill="#fbf0dc"/><path d="M0 8L8 0" stroke="#eddfbe"/></pattern></defs><rect width="100%" height="100%" fill="white"/><g font-family="Noto Sans TC, PingFang TC, Microsoft JhengHei, sans-serif">{description}</g></svg>'
