import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

IST = timezone(timedelta(hours=5, minutes=30))
MEETING_SET_STATUS = "Meeting Set"
# A remark counts as "not connected" if it mentions DNP (Did Not Pick).
DNP_RE = re.compile(r"\bdnp\b", re.I)
# CRM accounts belong to AEs, but SDRs operate them. Map to the real SDR name.
SDR_NAME_MAP = {
    "Jai Rathi": "Pranathi",
    "Eshan Aggarwal": "Indrani",
}


@dataclass
class Lead:
    """A single Zoho CRM lead record with typed access to the fields we use."""

    raw: dict

    @staticmethod
    def _time(value):
        return datetime.fromisoformat(value) if value else None

    def _user(self, field):
        user = self.raw.get(field) or {}
        name = user.get("name") or user.get("email") or "(unknown)"
        return SDR_NAME_MAP.get(name, name)

    @property
    def created_at(self):
        return self._time(self.raw.get("Created_Time"))

    @property
    def modified_at(self):
        return self._time(self.raw.get("Modified_Time"))

    @property
    def created_by(self):
        return self._user("Created_By")

    @property
    def modified_by(self):
        return self._user("Modified_By")

    @property
    def remarks(self):
        text = re.sub(r"<[^>]+>", " ", self.raw.get("Remarks") or "")
        return re.sub(r"\s+", " ", text).strip()

    @property
    def has_remark(self):
        return bool(self.remarks)

    @property
    def is_connected(self):
        """A logged call is 'connected' unless the remark mentions DNP."""
        return not DNP_RE.search(self.remarks)

    @property
    def is_meeting_set(self):
        return self.raw.get("Lead_Status") == MEETING_SET_STATUS


class ZohoClient:
    """Authenticates against Zoho CRM (India DC) and fetches lead records."""

    ACCOUNTS_URL = "https://accounts.zoho.in"
    API_BASE = "https://www.zohoapis.in"
    LEAD_FIELDS = (
        "Full_Name,Owner,Lead_Status,Remarks,"
        "Created_Time,Created_By,Modified_Time,Modified_By"
    )

    def __init__(self, client_id, client_secret, refresh_token):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token

    @classmethod
    def from_env(cls):
        return cls(
            client_id=os.environ["ZOHO_CLIENT_ID"],
            client_secret=os.environ["ZOHO_CLIENT_SECRET"],
            refresh_token=os.environ["ZOHO_REFRESH_TOKEN"],
        )

    def _access_token(self):
        r = requests.post(
            f"{self.ACCOUNTS_URL}/oauth/v2/token",
            params={
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if "access_token" not in data:
            raise SystemExit(f"Zoho token error: {data}")
        return data["access_token"]

    def fetch_leads(self):
        headers = {"Authorization": f"Zoho-oauthtoken {self._access_token()}"}
        # page_token is required for deep pagination; page numbers cap at 2000.
        params = {"fields": self.LEAD_FIELDS, "per_page": 200}
        records = []
        while True:
            r = requests.get(
                f"{self.API_BASE}/crm/v7/Leads",
                headers=headers,
                params=params,
                timeout=30,
            )
            if r.status_code == 204:
                break
            r.raise_for_status()
            payload = r.json()
            records.extend(payload.get("data", []))
            info = payload.get("info", {})
            if not info.get("more_records") or not info.get("next_page_token"):
                break
            params = {
                "fields": self.LEAD_FIELDS,
                "per_page": 200,
                "page_token": info["next_page_token"],
            }
        return [Lead(rec) for rec in records]


class WeeklyReport:
    """Aggregates lead activity for a work week and renders the Slack message."""

    def __init__(self, leads, start, end):
        self.leads = leads
        self.start = start
        self.end = end

    @staticmethod
    def previous_work_week(now=None):
        """Previous work week: Mon 00:00 -> Fri 23:59:59.999 IST (no weekend)."""
        now = now or datetime.now(IST)
        this_monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start = this_monday - timedelta(days=7)
        end = start + timedelta(days=5) - timedelta(microseconds=1)
        return start, end

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
            "*:bar_chart: Weekly SDR Report*",
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


class SlackNotifier:
    """Posts a message to a Slack Incoming Webhook."""

    def __init__(self, webhook_url):
        self.webhook_url = webhook_url

    def post(self, message):
        if not self.webhook_url:
            raise SystemExit("SLACK_WEBHOOK is not set in the environment/.env")
        r = requests.post(self.webhook_url, json={"text": message}, timeout=30)
        r.raise_for_status()


def main():
    load_dotenv()
    leads = ZohoClient.from_env().fetch_leads()
    start, end = WeeklyReport.previous_work_week()
    message = WeeklyReport(leads, start, end).build_message()
    print(message)

    if "--dry-run" in sys.argv:
        print("\n(dry-run: not posting to Slack)")
        return

    SlackNotifier(os.environ.get("SLACK_WEBHOOK", "").strip()).post(message)
    print("\nPosted to Slack.")


if __name__ == "__main__":
    main()
