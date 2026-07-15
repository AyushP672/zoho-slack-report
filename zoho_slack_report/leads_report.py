from collections import defaultdict
from datetime import timedelta


class LeadsReport:
    """Aggregates lead activity for a time window and renders the Slack message."""

    def __init__(self, leads, start, end, title, show_daily_breakdown=True):
        self.leads = leads
        self.start = start
        self.end = end
        self.title = title
        self.show_daily_breakdown = show_daily_breakdown

    def _in_window(self, when):
        return when is not None and self.start <= when <= self.end

    @property
    def _days(self):
        return [(self.start + timedelta(days=i)).date() for i in range(5)]

    def build_message(self):
        created = [ld for ld in self.leads if self._in_window(ld.created_at)]
        modified = [
            ld
            for ld in self.leads
            if self._in_window(ld.modified_at) and not self._in_window(ld.created_at)
        ]
        calls = [
            ld
            for ld in self.leads
            if self._in_window(ld.modified_at) and ld.has_remark
        ]
        meetings = [
            ld
            for ld in self.leads
            if ld.is_meeting_set and self._in_window(ld.modified_at)
        ]

        totals = defaultdict(
            lambda: {"created": 0, "modified": 0, "done": 0, "connected": 0, "meetings": 0}
        )
        per_day = defaultdict(
            lambda: defaultdict(lambda: {"done": 0, "connected": 0, "meetings": 0})
        )
        for ld in created:
            totals[ld.created_by]["created"] += 1
        for ld in modified:
            totals[ld.modified_by]["modified"] += 1
        for ld in calls:
            who, day = ld.modified_by, ld.modified_at.date()
            totals[who]["done"] += 1
            per_day[who][day]["done"] += 1
            if ld.is_connected:
                totals[who]["connected"] += 1
                per_day[who][day]["connected"] += 1
        for ld in meetings:
            who, day = ld.modified_by, ld.modified_at.date()
            totals[who]["meetings"] += 1
            per_day[who][day]["meetings"] += 1

        connected_total = sum(1 for ld in calls if ld.is_connected)

        lines = [
            f"*:bar_chart: {self.title}*",
            f"_{self.start:%d %b} \u2013 {self.end:%d %b %Y}_",
            "",
            "*Team Summary*",
            f"\u2022 Leads Created: *{len(created)}*",
            f"\u2022 Leads Modified: *{len(modified)}*",
            f"\u2022 Calls Done: *{len(calls)}*  "
            f"(Connected: {connected_total}, DNP: {len(calls) - connected_total})",
            f"\u2022 Meetings Set: *{len(meetings)}*",
            "",
        ]

        for sdr in sorted(totals, key=lambda s: totals[s]["done"], reverse=True):
            m = totals[sdr]
            lines.append(f"*{sdr}*")
            lines.append(f"\u2022 Leads: {m['created']} created, {m['modified']} modified")
            lines.append(
                f"\u2022 Calls: {m['done']} done, {m['connected']} connected, "
                f"{m['done'] - m['connected']} DNP"
            )
            lines.append(f"\u2022 Meetings Set: {m['meetings']}")
            if self.show_daily_breakdown:
                for day in self._days:
                    d = per_day[sdr].get(day)
                    if not d:
                        continue
                    parts = [f"Calls {d['done']}/{d['connected']} conn"]
                    if d["meetings"]:
                        parts.append(f"Meetings {d['meetings']}")
                    lines.append(f"      _{day:%a %d %b}_: " + ", ".join(parts))
            lines.append("")

        return "\n".join(lines).rstrip()
