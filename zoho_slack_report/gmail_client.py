import base64
import email.utils
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.header import decode_header, make_header

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from .config import IST

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
ADDR_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


def _decode_header_value(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _addresses_from_header(value):
    """Return lowercased emails from a From/To/Cc header."""
    found = []
    for _, addr in email.utils.getaddresses([value or ""]):
        addr = (addr or "").strip().lower()
        if addr and "@" in addr:
            found.append(addr)
    if not found and value:
        found.extend(a.lower() for a in ADDR_RE.findall(value))
    return found


def _decode_body_data(data):
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8", errors="replace")


def _strip_html(html):
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<br\s*/?>", "\n", text)
    text = re.sub(r"(?s)</p>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"[ \t]{2,}", " ", text)).strip()


def _extract_bodies(payload):
    """Return (plain, html) from a Gmail message payload tree."""
    plain_parts = []
    html_parts = []

    def walk(part):
        mime = (part.get("mimeType") or "").lower()
        body = part.get("body") or {}
        data = body.get("data")
        if mime == "text/plain" and data:
            plain_parts.append(_decode_body_data(data))
        elif mime == "text/html" and data:
            html_parts.append(_decode_body_data(data))
        for child in part.get("parts") or []:
            walk(child)

    walk(payload or {})
    return "\n".join(plain_parts).strip(), "\n".join(html_parts).strip()


@dataclass
class GmailMessage:
    gmail_id: str
    thread_id: str
    message_id: str
    subject: str
    from_header: str
    to_header: str
    cc_header: str
    date_header: str
    internal_date: datetime | None
    from_emails: list[str] = field(default_factory=list)
    to_emails: list[str] = field(default_factory=list)
    cc_emails: list[str] = field(default_factory=list)
    body_text: str = ""

    @property
    def all_emails(self):
        return list(dict.fromkeys(self.from_emails + self.to_emails + self.cc_emails))


class GmailClient:
    """Reads mail from a Google Workspace user mailbox via OAuth refresh token."""

    def __init__(self, client_id, client_secret, refresh_token, user_email):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.user_email = user_email.strip().lower()
        self._service = None

    @classmethod
    def from_env(cls):
        return cls(
            client_id=os.environ["GOOGLE_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
            refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
            user_email=os.environ.get("GMAIL_USER") or "sid@breatheesg.com",
        )

    def _service_client(self):
        if self._service is not None:
            return self._service
        creds = Credentials(
            token=None,
            refresh_token=self.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=[GMAIL_READONLY_SCOPE],
        )
        creds.refresh(Request())
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def fetch_messages_since(self, start, sales_email=None):
        """List and fully fetch messages with internal date >= start (aware datetime).

        If sales_email is set, narrow the Gmail query to To/Cc that address (Sid).
        Callers should still post-filter for correctness.
        """
        service = self._service_client()
        # Gmail after: is epoch seconds; inclusive of that day in practice — we
        # also filter by internalDate below for a precise trailing window.
        after_epoch = int(start.timestamp())
        query = f"after:{after_epoch}"
        sales = (sales_email or "").strip().lower()
        if sales:
            query = f"{query} (to:{sales} OR cc:{sales})"
        message_refs = []
        page_token = None
        while True:
            kwargs = {
                "userId": "me",
                "q": query,
                "maxResults": 100,
            }
            if page_token:
                kwargs["pageToken"] = page_token
            result = service.users().messages().list(**kwargs).execute()
            message_refs.extend(result.get("messages") or [])
            page_token = result.get("nextPageToken")
            if not page_token:
                break

        messages = []
        start_ms = int(start.timestamp() * 1000)
        for ref in message_refs:
            raw = (
                service.users()
                .messages()
                .get(userId="me", id=ref["id"], format="full")
                .execute()
            )
            internal_ms = int(raw.get("internalDate") or 0)
            if internal_ms < start_ms:
                continue
            messages.append(self._parse_message(raw))
        messages.sort(key=lambda m: m.internal_date or datetime.min.replace(tzinfo=IST))
        return messages

    def _parse_message(self, raw):
        headers = {
            (h.get("name") or "").lower(): h.get("value") or ""
            for h in (raw.get("payload") or {}).get("headers") or []
        }
        subject = _decode_header_value(headers.get("subject", ""))
        from_header = _decode_header_value(headers.get("from", ""))
        to_header = _decode_header_value(headers.get("to", ""))
        cc_header = _decode_header_value(headers.get("cc", ""))
        date_header = headers.get("date", "")
        message_id = (headers.get("message-id") or "").strip() or raw.get("id", "")

        plain, html = _extract_bodies(raw.get("payload") or {})
        body = plain or _strip_html(html)

        internal_ms = int(raw.get("internalDate") or 0)
        internal_date = (
            datetime.fromtimestamp(internal_ms / 1000, tz=IST) if internal_ms else None
        )

        return GmailMessage(
            gmail_id=raw.get("id", ""),
            thread_id=raw.get("threadId", ""),
            message_id=message_id,
            subject=subject,
            from_header=from_header,
            to_header=to_header,
            cc_header=cc_header,
            date_header=date_header,
            internal_date=internal_date,
            from_emails=_addresses_from_header(from_header),
            to_emails=_addresses_from_header(to_header),
            cc_emails=_addresses_from_header(cc_header),
            body_text=body,
        )
