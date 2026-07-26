from dataclasses import dataclass, field
from datetime import datetime

from .config import IST
from .time_windows import trailing_24_hours

INTERNAL_DOMAINS = {"breatheesg.com"}
FREE_MAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.co.in",
    "outlook.com",
    "hotmail.com",
    "icloud.com",
    "me.com",
    "live.com",
    "proton.me",
    "protonmail.com",
    "aol.com",
    "mail.com",
    "ymail.com",
    "rediffmail.com",
}
NOTE_BODY_MAX = 4000
NOTE_TITLE_MAX = 100
MIN_DEAL_NAME_LEN = 4


def _domain(email_addr):
    if not email_addr or "@" not in email_addr:
        return ""
    return email_addr.rsplit("@", 1)[-1].lower()


def _is_external(email_addr, mailbox):
    addr = (email_addr or "").lower()
    if not addr or addr == mailbox:
        return False
    return _domain(addr) not in INTERNAL_DOMAINS


def _gmail_marker(message_id):
    return f"[gmail-id:{message_id}]"


def _note_title(subject):
    title = f"Email: {subject or '(no subject)'}"
    if len(title) <= NOTE_TITLE_MAX:
        return title
    return title[: NOTE_TITLE_MAX - 1] + "…"


def _note_content(message, match_method, confidence):
    body = (message.body_text or "").strip()
    if len(body) > NOTE_BODY_MAX:
        body = body[: NOTE_BODY_MAX - 1] + "…"
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
class MatchResult:
    deal_id: str
    deal_name: str
    method: str  # contact | domain | subject
    confidence: str  # ok | ambiguous
    matched_email: str = ""


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


class DealEmailIndex:
    """Maps contact emails / domains / deal names to deals for matching."""

    def __init__(self, deals_by_id, email_to_deals, domain_to_deals):
        self.deals_by_id = deals_by_id
        self.email_to_deals = email_to_deals
        self.domain_to_deals = domain_to_deals

    @classmethod
    def build(cls, zoho, deals):
        deals_by_id = {d.id: d for d in deals if d.id}
        email_to_deals = {}
        domain_to_deals = {}

        for deal in deals:
            if not deal.id:
                continue
            roles = zoho.fetch_deal_contact_roles(deal.id)
            for role in roles:
                email_addr = (role.get("Email") or "").strip().lower()
                if not email_addr or "@" not in email_addr:
                    continue
                email_to_deals.setdefault(email_addr, set()).add(deal.id)
                dom = _domain(email_addr)
                if dom and dom not in FREE_MAIL_DOMAINS and dom not in INTERNAL_DOMAINS:
                    domain_to_deals.setdefault(dom, set()).add(deal.id)

        return cls(deals_by_id, email_to_deals, domain_to_deals)

    def _pick_deal(self, deal_ids):
        """Prefer open deals; among those, latest Modified_Time. Returns (deal, ambiguous)."""
        deals = [self.deals_by_id[i] for i in deal_ids if i in self.deals_by_id]
        if not deals:
            return None, False
        open_deals = [d for d in deals if d.is_open]
        pool = open_deals or deals
        pool.sort(
            key=lambda d: d.modified_at or datetime.min.replace(tzinfo=IST),
            reverse=True,
        )
        return pool[0], len(pool) > 1

    def match(self, message, external_emails):
        # 1) Contact email exact match
        contact_hits = set()
        matched_email = ""
        for addr in external_emails:
            if addr in self.email_to_deals:
                contact_hits |= self.email_to_deals[addr]
                if not matched_email:
                    matched_email = addr
        if contact_hits:
            deal, ambiguous = self._pick_deal(contact_hits)
            if deal:
                return MatchResult(
                    deal_id=deal.id,
                    deal_name=deal.name,
                    method="contact",
                    confidence="ambiguous" if ambiguous else "ok",
                    matched_email=matched_email,
                )

        # 2) Domain unique open-deal match
        domain_hits = set()
        for addr in external_emails:
            dom = _domain(addr)
            if not dom or dom in FREE_MAIL_DOMAINS or dom in INTERNAL_DOMAINS:
                continue
            domain_hits |= self.domain_to_deals.get(dom, set())
            if not matched_email:
                matched_email = addr
        if domain_hits:
            open_ids = {
                i
                for i in domain_hits
                if i in self.deals_by_id and self.deals_by_id[i].is_open
            }
            if len(open_ids) == 1:
                deal_id = next(iter(open_ids))
                deal = self.deals_by_id[deal_id]
                return MatchResult(
                    deal_id=deal.id,
                    deal_name=deal.name,
                    method="domain",
                    confidence="ok",
                    matched_email=matched_email,
                )

        # 3) Deal_Name substring in subject — unique open deal only
        subject = (message.subject or "").lower()
        if subject:
            name_hits = []
            for deal in self.deals_by_id.values():
                if not deal.is_open:
                    continue
                name = (deal.name or "").strip()
                if len(name) < MIN_DEAL_NAME_LEN:
                    continue
                if name.lower() in subject:
                    name_hits.append(deal)
            if len(name_hits) == 1:
                deal = name_hits[0]
                return MatchResult(
                    deal_id=deal.id,
                    deal_name=deal.name,
                    method="subject",
                    confidence="ok",
                )

        return None


def external_participants(message, mailbox):
    return [e for e in message.all_emails if _is_external(e, mailbox)]


def note_already_exists(notes, message_id):
    marker = _gmail_marker(message_id)
    for note in notes:
        content = note.get("Note_Content") or ""
        if marker in content:
            return True
    return False


def sync_email_notes(zoho, gmail, dry_run=False):
    """Fetch last-24h mail, match to deals, create notes. Returns SyncStats."""
    start, end = trailing_24_hours()
    stats = SyncStats(start=start, end=end)
    mailbox = gmail.user_email

    messages = gmail.fetch_messages_since(start)
    deals = zoho.fetch_deals()
    index = DealEmailIndex.build(zoho, deals)

    # Cache notes per deal to avoid re-fetching within a run
    notes_cache = {}

    for message in messages:
        externals = external_participants(message, mailbox)
        if not externals:
            continue

        match = index.match(message, externals)
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
    """Build Indrani's review summary for Slack."""
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
        "Please confirm logged notes look right; fix Contact Roles on unmatched deals for next run."
    )
    return "\n".join(lines)
