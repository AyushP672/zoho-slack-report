import json
import os
from dataclasses import dataclass, field
from datetime import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build

from .config import IST

CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"


def _parse_event_time(value):
    """Parse Calendar API dateTime or date into an aware IST datetime."""
    if not value:
        return None
    if "dateTime" in value:
        raw = value["dateTime"]
        # Google may return +05:30 or Z
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(IST)
    if "date" in value:
        # All-day: treat as midnight IST on that date
        d = datetime.fromisoformat(value["date"]).date()
        return datetime(d.year, d.month, d.day, tzinfo=IST)
    return None


@dataclass
class CalendarEvent:
    event_id: str
    summary: str
    description: str
    start: datetime | None
    end: datetime | None
    status: str
    html_link: str
    attendee_emails: list[str] = field(default_factory=list)
    organizer_email: str = ""

    @property
    def all_emails(self):
        emails = list(self.attendee_emails)
        if self.organizer_email and self.organizer_email not in emails:
            emails.append(self.organizer_email)
        return emails


class CalendarClient:
    """Reads Google Calendar events via service account + domain-wide delegation."""

    def __init__(self, service_account_info):
        if isinstance(service_account_info, str):
            self._info = json.loads(service_account_info)
        else:
            self._info = service_account_info

    @classmethod
    def from_env(cls):
        raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        if not raw:
            raise SystemExit(
                "GOOGLE_SERVICE_ACCOUNT_JSON is not set in the environment/.env "
                "(required for calendar meeting notes)"
            )
        # Allow file path for local dry-runs
        if raw.startswith("{"):
            return cls(raw)
        path = os.path.expanduser(raw)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return cls(json.load(f))
        raise SystemExit(
            "GOOGLE_SERVICE_ACCOUNT_JSON must be a JSON string or a path to a JSON key file"
        )

    def _service_for(self, user_email):
        creds = service_account.Credentials.from_service_account_info(
            self._info,
            scopes=[CALENDAR_READONLY_SCOPE],
        )
        delegated = creds.with_subject(user_email)
        return build("calendar", "v3", credentials=delegated, cache_discovery=False)

    def list_events(self, user_email, start, end):
        """Return non-cancelled events on the user's primary calendar in [start, end]."""
        service = self._service_for(user_email)
        time_min = start.astimezone(IST).isoformat()
        time_max = end.astimezone(IST).isoformat()
        events = []
        page_token = None
        while True:
            result = (
                service.events()
                .list(
                    calendarId="primary",
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                    pageToken=page_token,
                )
                .execute()
            )
            for raw in result.get("items") or []:
                status = (raw.get("status") or "").lower()
                if status == "cancelled":
                    continue
                attendees = []
                for att in raw.get("attendees") or []:
                    if att.get("resource"):
                        continue
                    email_addr = (att.get("email") or "").strip().lower()
                    if email_addr:
                        attendees.append(email_addr)
                organizer = ((raw.get("organizer") or {}).get("email") or "").strip().lower()
                events.append(
                    CalendarEvent(
                        event_id=raw.get("id") or "",
                        summary=(raw.get("summary") or "").strip(),
                        description=(raw.get("description") or "").strip(),
                        start=_parse_event_time(raw.get("start") or {}),
                        end=_parse_event_time(raw.get("end") or {}),
                        status=status or "confirmed",
                        html_link=raw.get("htmlLink") or "",
                        attendee_emails=attendees,
                        organizer_email=organizer,
                    )
                )
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return events
