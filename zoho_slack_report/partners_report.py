from datetime import datetime

from .config import ALWAYS_INCLUDE_PARTNERS, IST
from .deal_movement import build_section
from .time_windows import trailing_7_days


def discover_partners(deals):
    """All partners to report: the always-include roster unioned with any partner
    present in the data, sorted alphabetically."""
    return sorted(set(ALWAYS_INCLUDE_PARTNERS) | {d.partner for d in deals if d.partner})


def build_partner_message(deals, now=None):
    now = now or datetime.now(IST)
    start, end = trailing_7_days(now)
    today = now.date()
    lines = [
        "*:handshake: Partners Weekly Report*",
        f"_{start:%d %b} \u2013 {end:%d %b %Y}_",
        "",
    ]
    partners = discover_partners(deals)
    if not partners:
        lines.append("_No partner deals found._")
    for partner in partners:
        filtered = [d for d in deals if d.partner == partner]
        lines.extend(
            build_section(partner, filtered, start, end, today, "last 7 days")
        )
        lines.append("")
    return "\n".join(lines).rstrip()
