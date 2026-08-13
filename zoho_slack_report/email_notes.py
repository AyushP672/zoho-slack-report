import os
from dataclasses import dataclass, field
from datetime import datetime

from .deal_match import (
    DealEmailIndex,
    external_emails_from,
    note_has_marker,
    truncate_body,
    truncate_note_title,
)
from .time_windows import trailing_24_hours

DEFAULT_SID_EMAIL = "sid@breatheesg.com"


def sid_email_from_env():
    return (os.environ.get("GMAIL_GROUP") or DEFAULT_SID_EMAIL).strip().lower()


def involves_sid(message, sid_email):
    """True if Sid is on To or Cc (sales mail; skip ayush-only personal)."""
    sid = (sid_email or "").strip().lower()
    if not sid:
        return False
    return sid in (message.to_emails or []) or sid in (message.cc_emails or [])


def _gmail_marker(message_id):
    return f"[gmail-id:{message_id}]"


def _note_title(subject):
    return truncate_note_title("Email: ", subject or "(no subject)")


def _note_content(message, match_method, confidence):
    body = truncate_body(message.body_text)
    parts = [
        _gmail_marker(message.message_id),
        f"From: {message.from_header}",
        f"To: {message.to_header}",
        f"Cc: {message.cc_header}" if message.cc_header else None,
        f"Date: {message.date_header or (message.internal_date.isoformat() if message.internal_date else '')}",
        f"Match: {match_method} ({confidence})",
        "",
        body,
    ]
    return "\n".join(p for p in parts if p is not None)


@dataclass
class LoggedItem:
    deal_name: str
    subject: str
    method: str
    confidence: str
    matched_email: str = ""


@dataclass
class SkippedItem:
    subject: str
    message_id: str
    reason: str


@dataclass
class UnmatchedItem:
    subject: str
    from_header: str
    to_header: str
    external_emails: list[str] = field(default_factory=list)


@dataclass
class SyncStats:
    start: datetime
    end: datetime
    logged: list[LoggedItem] = field(default_factory=list)
    skipped: list[SkippedItem] = field(default_factory=list)
    unmatched: list[UnmatchedItem] = field(default_factory=list)
    ambiguous_count: int = 0

    @property
    def logged_count(self):
        return len(self.logged)

    @property
    def skipped_count(self):
        return len(self.skipped)

    @property
    def unmatched_count(self):
        return len(self.unmatched)


def external_participants(message, mailbox):
    return external_emails_from(message.all_emails, owner_email=mailbox)


def note_already_exists(notes, message_id):
    return note_has_marker(notes, _gmail_marker(message_id))


def sync_email_notes(zoho, gmail, dry_run=False):
    """Fetch last-24h mail, match to deals, create notes. Returns SyncStats."""
    start, end = trailing_24_hours()
    stats = SyncStats(start=start, end=end)
    sid_email = sid_email_from_env()

    messages = gmail.fetch_messages_since(start, sales_email=sid_email)
    index = DealEmailIndex.build(zoho)

    notes_cache = {}

    for message in messages:
        if not involves_sid(message, sid_email):
            continue

        externals = external_participants(message, sid_email)
        if not externals:
            continue

        match = index.match(message.subject, externals)
        if not match:
            stats.unmatched.append(
                UnmatchedItem(
                    subject=message.subject or "(no subject)",
                    from_header=message.from_header,
                    to_header=message.to_header,
                    external_emails=externals,
                )
            )
            continue

        if match.deal_id not in notes_cache:
            notes_cache[match.deal_id] = zoho.fetch_deal_notes(match.deal_id)
        if note_already_exists(notes_cache[match.deal_id], message.message_id):
            stats.skipped.append(
                SkippedItem(
                    subject=message.subject or "(no subject)",
                    message_id=message.message_id,
                    reason="already noted",
                )
            )
            continue

        title = _note_title(message.subject)
        content = _note_content(message, match.method, match.confidence)

        if not dry_run:
            zoho.create_deal_note(match.deal_id, title, content)
            notes_cache[match.deal_id].append({"Note_Content": content})

        if match.confidence == "ambiguous":
            stats.ambiguous_count += 1

        stats.logged.append(
            LoggedItem(
                deal_name=match.deal_name,
                subject=message.subject or "(no subject)",
                method=match.method,
                confidence=match.confidence,
                matched_email=match.matched_email,
            )
        )

    return stats


def _fmt_window(start, end):
    def fmt(dt):
        return f"{dt.day} {dt.strftime('%b %H:%M')}"

    return f"{fmt(start)} – {fmt(end)} IST"


def build_email_notes_slack_message(stats):
    """Build the review summary for Slack."""
    lines = [
        "Email → Deal Notes (24h)",
        _fmt_window(stats.start, stats.end),
        "",
        (
            f"Logged: {stats.logged_count}  |  Dupes skipped: {stats.skipped_count}"
            f"  |  Unmatched: {stats.unmatched_count}"
            f"  |  Ambiguous: {stats.ambiguous_count}"
        ),
    ]

    if stats.logged:
        lines.append("")
        lines.append("Logged")
        for item in stats.logged:
            bit = f"• {item.deal_name} — \"{item.subject}\" — {item.method}"
            if item.matched_email:
                bit += f" — {item.matched_email}"
            if item.confidence == "ambiguous":
                bit += " ⚠ ambiguous"
            lines.append(bit)

    if stats.skipped:
        lines.append("")
        lines.append("Skipped (already noted)")
        for item in stats.skipped:
            lines.append(f"• \"{item.subject}\" — gmail-id:{item.message_id}")

    if stats.unmatched:
        lines.append("")
        lines.append("Needs review (unmatched)")
        for item in stats.unmatched:
            lines.append(
                f"• From: {item.from_header} | Subject: \"{item.subject}\" | To: {item.to_header}"
            )

    lines.append("")
    lines.append(
        "Please confirm logged notes look right. For unmatched items, update "
        "the contact person on the relevant Zoho deal (Deal Contact Roles / Lead Email) "
        "so the next run can match them."
    )
    return "\n".join(lines)
