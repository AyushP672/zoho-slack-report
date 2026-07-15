from datetime import timedelta


def format_inr(value):
    """Format an amount in Indian lakh/crore notation (e.g. 7675000 -> '₹76.75L')."""
    value = value or 0
    if value >= 1e7:
        return f"\u20b9{value / 1e7:.2f}Cr"
    if value >= 1e5:
        return f"\u20b9{value / 1e5:.2f}L"
    return f"\u20b9{value:,.0f}"


def deals_count(n):
    """Pluralize a deal count (e.g. 1 -> '1 deal', 2 -> '2 deals')."""
    return f"{n} deal" if n == 1 else f"{n} deals"


def _modified_in_window(deal, start, end):
    when = deal.modified_at
    return when is not None and start <= when <= end


def _pipeline(deals, today, days):
    hi = today + timedelta(days=days)
    selected = [
        d
        for d in deals
        if d.is_open
        and d.closing_date is not None
        and today <= d.closing_date <= hi
    ]
    return len(selected), sum(d.amount for d in selected)


def build_section(label, deals, start, end, today, movement_label):
    """Render deal movement bullets for one group (partner, AE owner, etc.)."""
    meetings = [
        d for d in deals if d.is_meeting_done and _modified_in_window(d, start, end)
    ]
    sql_moves = [d for d in deals if d.is_sql and _modified_in_window(d, start, end)]
    n30, v30 = _pipeline(deals, today, 30)
    n90, v90 = _pipeline(deals, today, 90)

    meetings_value = sum(d.amount for d in meetings)
    sql_value = sum(d.amount for d in sql_moves)

    return [
        f"*{label}*",
        f"\u2022 Meetings ({movement_label}): *{format_inr(meetings_value)}*  "
        f"({deals_count(len(meetings))})",
        f"\u2022 SQL Movement ({movement_label}): *{format_inr(sql_value)}*  "
        f"({deals_count(len(sql_moves))})",
        f"\u2022 Pipeline \u226430 days: *{format_inr(v30)}*  ({deals_count(n30)})",
        f"\u2022 Pipeline \u226490 days: *{format_inr(v90)}*  ({deals_count(n90)})",
    ]
