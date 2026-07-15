from .config import AE_OWNERS
from .deal_movement import build_section


def build_deals_message(deals, *, title, start, end, movement_label):
    today = end.date()
    lines = [
        f"*:briefcase: {title}*",
        f"_{start:%d %b} \u2013 {end:%d %b %Y}_",
        "",
    ]
    for display_name, crm_name in AE_OWNERS:
        filtered = [d for d in deals if d.owner == crm_name]
        lines.extend(
            build_section(display_name, filtered, start, end, today, movement_label)
        )
        lines.append("")
    return "\n".join(lines).rstrip()
