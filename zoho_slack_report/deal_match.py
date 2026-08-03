"""Shared matching: Zoho Leads (email → domain → name) then resolve to a Deal."""

from dataclasses import dataclass
from datetime import datetime

from .config import IST
from .models import Deal

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
MIN_NAME_LEN = 4
NOTE_BODY_MAX = 4000
NOTE_TITLE_MAX = 100


def domain_of(email_addr):
    if not email_addr or "@" not in email_addr:
        return ""
    return email_addr.rsplit("@", 1)[-1].lower()


def is_external_email(email_addr, owner_email=None):
    addr = (email_addr or "").lower()
    if not addr:
        return False
    if owner_email and addr == owner_email.lower():
        return False
    return domain_of(addr) not in INTERNAL_DOMAINS


def external_emails_from(addresses, owner_email=None):
    """Dedupe and keep only external addresses."""
    seen = []
    for addr in addresses or []:
        a = (addr or "").strip().lower()
        if a and is_external_email(a, owner_email) and a not in seen:
            seen.append(a)
    return seen


def truncate_note_title(prefix, text):
    title = f"{prefix}{text or '(no title)'}"
    if len(title) <= NOTE_TITLE_MAX:
        return title
    return title[: NOTE_TITLE_MAX - 1] + "…"


def truncate_body(text):
    body = (text or "").strip()
    if len(body) > NOTE_BODY_MAX:
        return body[: NOTE_BODY_MAX - 1] + "…"
    return body


def note_has_marker(notes, marker):
    for note in notes:
        content = note.get("Note_Content") or ""
        if marker in content:
            return True
    return False


@dataclass
class MatchResult:
    deal_id: str
    deal_name: str
    method: str  # deal_title | lead_email | lead_domain | lead_name
    confidence: str  # ok | ambiguous
    matched_email: str = ""
    lead_id: str = ""
    lead_name: str = ""


class DealEmailIndex:
    """Match title → Lead email/domain/name, then resolve to a Deal for notes."""

    def __init__(
        self,
        zoho,
        leads_by_id,
        email_to_leads,
        domain_to_leads,
        deals_by_id,
        contact_email_to_deals,
    ):
        self.zoho = zoho
        self.leads_by_id = leads_by_id
        self.email_to_leads = email_to_leads
        self.domain_to_leads = domain_to_leads
        self.deals_by_id = deals_by_id
        self.contact_email_to_deals = contact_email_to_deals

    @classmethod
    def build(cls, zoho, deals=None):
        """Build Lead match indexes. Optional deals avoids a second fetch."""
        leads = zoho.fetch_leads()
        deals = deals if deals is not None else zoho.fetch_deals()
        leads_by_id = {lead.id: lead for lead in leads if lead.id}
        deals_by_id = {deal.id: deal for deal in deals if deal.id}

        email_to_leads = {}
        domain_to_leads = {}
        for lead in leads:
            if not lead.id:
                continue
            if lead.email and "@" in lead.email:
                email_to_leads.setdefault(lead.email, set()).add(lead.id)
                dom = domain_of(lead.email)
                if dom and dom not in FREE_MAIL_DOMAINS and dom not in INTERNAL_DOMAINS:
                    domain_to_leads.setdefault(dom, set()).add(lead.id)

        # Exact Contact Role email → deal (resolve only; never used for domain match)
        contact_email_to_deals = {}
        for deal in deals:
            if not deal.id:
                continue
            for role in zoho.fetch_deal_contact_roles(deal.id):
                email_addr = (role.get("Email") or "").strip().lower()
                if email_addr and "@" in email_addr:
                    contact_email_to_deals.setdefault(email_addr, set()).add(deal.id)

        return cls(
            zoho,
            leads_by_id,
            email_to_leads,
            domain_to_leads,
            deals_by_id,
            contact_email_to_deals,
        )

    def _pick_lead(self, lead_ids):
        leads = [self.leads_by_id[i] for i in lead_ids if i in self.leads_by_id]
        if not leads:
            return None, False
        leads.sort(
            key=lambda lead: lead.modified_at or datetime.min.replace(tzinfo=IST),
            reverse=True,
        )
        return leads[0], len(leads) > 1

    def _pick_deal(self, deal_ids):
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

    def _converted_deal_id(self, lead):
        deal_id = lead.converted_deal_id
        if deal_id:
            return deal_id
        if not lead.is_converted or not lead.id:
            return None
        # List payloads often omit converted detail — fetch the record once.
        full = self.zoho.fetch_lead(lead.id)
        if full and full.converted_deal_id:
            self.leads_by_id[lead.id] = full
            return full.converted_deal_id
        return None

    def _resolve_deal(self, lead):
        """Map a matched Lead to a Deal. Returns (deal, ambiguous) or (None, False)."""
        converted_id = self._converted_deal_id(lead)
        if converted_id and converted_id in self.deals_by_id:
            return self.deals_by_id[converted_id], False
        if converted_id:
            # Deal may be missing from the full fetch — still usable as id.
            return Deal({"id": converted_id, "Deal_Name": "(converted deal)"}), False

        if lead.email and lead.email in self.contact_email_to_deals:
            return self._pick_deal(self.contact_email_to_deals[lead.email])

        return None, False

    def _result(self, lead, method, lead_ambiguous, matched_email=""):
        deal, deal_ambiguous = self._resolve_deal(lead)
        if not deal or not deal.id:
            return None
        confidence = "ambiguous" if (lead_ambiguous or deal_ambiguous) else "ok"
        return MatchResult(
            deal_id=deal.id,
            deal_name=deal.name,
            method=method,
            confidence=confidence,
            matched_email=matched_email or lead.email,
            lead_id=lead.id or "",
            lead_name=lead.full_name or lead.company or "",
        )

    def _match_deal_title(self, subject):
        """Match subject against Deal Account_Name / Deal_Name. Returns MatchResult or None."""
        subject_l = (subject or "").lower()
        if not subject_l:
            return None
        hit_ids = set()
        for deal in self.deals_by_id.values():
            if not deal.id:
                continue
            labels = [
                deal.account_name,
                (deal.raw.get("Deal_Name") or "").strip(),
            ]
            for label in labels:
                name = (label or "").strip()
                if len(name) < MIN_NAME_LEN:
                    continue
                if name.lower() in subject_l:
                    hit_ids.add(deal.id)
                    break
        if not hit_ids:
            return None
        deal, ambiguous = self._pick_deal(hit_ids)
        if not deal or not deal.id:
            return None
        return MatchResult(
            deal_id=deal.id,
            deal_name=deal.name,
            method="deal_title",
            confidence="ambiguous" if ambiguous else "ok",
        )

    def match(self, subject, external_emails):
        """Match subject/emails to a Deal: title → Lead email → domain → name."""
        # 1) Deal Account_Name / Deal_Name in subject (primary)
        title_hit = self._match_deal_title(subject)
        if title_hit:
            return title_hit

        # 2) Exact Lead.Email
        email_hits = set()
        matched_email = ""
        for addr in external_emails:
            if addr in self.email_to_leads:
                email_hits |= self.email_to_leads[addr]
                if not matched_email:
                    matched_email = addr
        if email_hits:
            lead, ambiguous = self._pick_lead(email_hits)
            if lead:
                result = self._result(lead, "lead_email", ambiguous, matched_email)
                if result:
                    return result

        # 3) Unique Lead email-domain
        domain_hits = set()
        for addr in external_emails:
            dom = domain_of(addr)
            if not dom or dom in FREE_MAIL_DOMAINS or dom in INTERNAL_DOMAINS:
                continue
            domain_hits |= self.domain_to_leads.get(dom, set())
            if not matched_email:
                matched_email = addr
        if len(domain_hits) == 1:
            lead_id = next(iter(domain_hits))
            lead = self.leads_by_id.get(lead_id)
            if lead:
                result = self._result(lead, "lead_domain", False, matched_email)
                if result:
                    return result

        # 4) Unique Lead Company or Full_Name in subject
        subject_l = (subject or "").lower()
        if subject_l:
            name_hits = []
            for lead in self.leads_by_id.values():
                for label in (lead.company, lead.full_name):
                    name = (label or "").strip()
                    if len(name) < MIN_NAME_LEN:
                        continue
                    if name.lower() in subject_l:
                        name_hits.append(lead)
                        break
            # unique by lead id
            unique = {lead.id: lead for lead in name_hits if lead.id}
            if len(unique) == 1:
                lead = next(iter(unique.values()))
                result = self._result(lead, "lead_name", False, matched_email)
                if result:
                    return result

        return None
