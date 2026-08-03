from dataclasses import dataclass, field
from datetime import datetime

from .config import CALENDAR_OWNERS, IST
from .deal_match import (
    DealEmailIndex,
    external_emails_from,
    note_has_marker,
    truncate_body,
    truncate_note_title,
)
from .time_windows import trailing_24_hours


def _gcal_marker(event_id):
    return f"[gcal-id:{event_id}]"


def _fmt_dt(dt):
    if not dt:
        return ""
    return dt.astimezone(IST).strftime("%d %b %Y %H:%M IST")


def _note_title(summary):
    return truncate_note_title("Meeting: ", summary or "(no title)")


def _note_content(owner_name, event, match_method, confidence, externals):
    when = f"{_fmt_dt(event.start)} – {_fmt_dt(event.end)}"
    attendees = ", ".join(event.all_emails) or "(none)"
    parts = [
        _gcal_marker(event.event_id),
        f"Owner: {owner_name}",
        f"When: {when}",
        f"Attendees: {attendees}",
        f"External: {', '.join(externals)}" if externals else None,
        f"Match: {match_method} ({confidence})",
        f"Link: {event.html_link}" if event.html_link else None,
        "",
        truncate_body(event.description),
    ]
    return "\n".join(p for p in parts if p is not None)


@dataclass
class LoggedMeeting:
    owner_name: str
    deal_name: str
    summary: str
    method: str
    confidence: str
    matched_email: str = ""


@dataclass
class SkippedMeeting:
    owner_name: str
    summary: str
    event_id: str
    reason: str


@dataclass
class UnmatchedMeeting:
    owner_name: str
    summary: str
    when: str
    attendees: str
    external_emails: list[str] = field(default_factory=list)


@dataclass
class OwnerStats:
    name: str
    email: str
    logged: list[LoggedMeeting] = field(default_factory=list)
    skipped: list[SkippedMeeting] = field(default_factory=list)
    unmatched: list[UnmatchedMeeting] = field(default_factory=list)
    ambiguous_count: int = 0


@dataclass
class MeetingSyncStats:
    start: datetime
    end: datetime
    owners: list[OwnerStats] = field(default_factory=list)

    @property
    def logged_count(self):
        return sum(len(o.logged) for o in self.owners)

    @property
    def skipped_count(self):
        return sum(len(o.skipped) for o in self.owners)

    @property
    def unmatched_count(self):
        return sum(len(o.unmatched) for o in self.owners)

    @property
    def ambiguous_count(self):
        return sum(o.ambiguous_count for o in self.owners)


def sync_meeting_notes(zoho, calendar, dry_run=False, owners=None):
    """Scan each AE calendar for last-24h meetings; write Deal Notes. Returns MeetingSyncStats."""
    start, end = trailing_24_hours()
    stats = MeetingSyncStats(start=start, end=end)
    owner_list = owners if owners is not None else CALENDAR_OWNERS

    deals = zoho.fetch_deals()
    index = DealEmailIndex.build(zoho, deals)
    notes_cache = {}

    for display_name, cal_email in owner_list:
        owner = OwnerStats(name=display_name, email=cal_email)
        events = calendar.list_events(cal_email, start, end)

        for event in events:
            if not event.event_id:
                continue
            externals = external_emails_from(event.all_emails, owner_email=cal_email)
            if not externals:
                # Internal-only / no attendees — skip silently (not sales POC meetings)
                continue

            match = index.match(event.summary, externals)
            if not match:
                owner.unmatched.append(
                    UnmatchedMeeting(
                        owner_name=display_name,
                        summary=event.summary or "(no title)",
                        when=_fmt_dt(event.start),
                        attendees=", ".join(event.all_emails),
                        external_emails=externals,
                    )
                )
                continue

            if match.deal_id not in notes_cache:
                notes_cache[match.deal_id] = zoho.fetch_deal_notes(match.deal_id)
            if note_has_marker(notes_cache[match.deal_id], _gcal_marker(event.event_id)):
                owner.skipped.append(
                    SkippedMeeting(
                        owner_name=display_name,
                        summary=event.summary or "(no title)",
                        event_id=event.event_id,
                        reason="already noted",
                    )
                )
                continue

            title = _note_title(event.summary)
            content = _note_content(
                display_name, event, match.method, match.confidence, externals
            )

            if not dry_run:
                zoho.create_deal_note(match.deal_id, title, content)
                notes_cache[match.deal_id].append({"Note_Content": content})

            if match.confidence == "ambiguous":
                owner.ambiguous_count += 1

            owner.logged.append(
                LoggedMeeting(
                    owner_name=display_name,
                    deal_name=match.deal_name,
                    summary=event.summary or "(no title)",
                    method=match.method,
                    confidence=match.confidence,
                    matched_email=match.matched_email,
                )
            )

        stats.owners.append(owner)

    return stats


def _fmt_window(start, end):
    def fmt(dt):
        return f"{dt.day} {dt.strftime('%b %H:%M')}"

    return f"{fmt(start)} – {fmt(end)} IST"


def build_meeting_notes_slack_message(stats):
    """One Indrani Slack message, sections by sales-team name."""
    lines = [
        "Meetings → Deal Notes (24h)",
        _fmt_window(stats.start, stats.end),
        "",
        (
            f"Logged: {stats.logged_count}  |  Dupes: {stats.skipped_count}"
            f"  |  Unmatched: {stats.unmatched_count}"
            f"  |  Ambiguous: {stats.ambiguous_count}"
        ),
    ]

    for owner in stats.owners:
        lines.append("")
        lines.append(owner.name)

        if not owner.logged and not owner.skipped and not owner.unmatched:
            lines.append("• (none)")
            continue

        for item in owner.logged:
            bit = f"• {item.deal_name} — \"{item.summary}\" — {item.method}"
            if item.matched_email:
                bit += f" — {item.matched_email}"
            if item.confidence == "ambiguous":
                bit += " ⚠ ambiguous"
            lines.append(bit)

        for item in owner.skipped:
            lines.append(f"• (dupe) \"{item.summary}\" — gcal-id:{item.event_id}")

        for item in owner.unmatched:
            lines.append(
                f"• Needs review: \"{item.summary}\" — {item.when} — {item.attendees}"
            )

    lines.append("")
    lines.append(
        "Please confirm logged notes look right. For unmatched items, update "
        "the contact person on the relevant Zoho deal (Deal Contact Roles / Lead Email) "
        "so the next run can match them."
    )
    return "\n".join(lines)
