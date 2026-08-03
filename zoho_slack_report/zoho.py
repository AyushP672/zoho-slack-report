import os

import requests

from .models import Deal, Lead


class ZohoClient:
    """Authenticates against Zoho CRM (India DC) and reads/writes CRM records."""

    ACCOUNTS_URL = "https://accounts.zoho.in"
    API_BASE = "https://www.zohoapis.in"
    LEAD_FIELDS = (
        "Full_Name,Email,Company,Converted,Owner,Lead_Status,Remarks,"
        "Created_Time,Created_By,Modified_Time,Modified_By"
    )
    DEAL_FIELDS = (
        "Deal_Name,Partner,Stage,SQL,Amount,Closing_Date,"
        "Modified_Time,Created_Time,Owner,Account_Name"
    )

    def __init__(self, client_id, client_secret, refresh_token):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self._token = None

    @classmethod
    def from_env(cls):
        return cls(
            client_id=os.environ["ZOHO_CLIENT_ID"],
            client_secret=os.environ["ZOHO_CLIENT_SECRET"],
            refresh_token=os.environ["ZOHO_REFRESH_TOKEN"],
        )

    def _access_token(self):
        if self._token:
            return self._token
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
        self._token = data["access_token"]
        return self._token

    def _headers(self):
        return {"Authorization": f"Zoho-oauthtoken {self._access_token()}"}

    def _fetch_records(self, module, fields):
        headers = self._headers()
        # page_token is required for deep pagination; page numbers cap at 2000.
        params = {"fields": fields, "per_page": 200}
        records = []
        while True:
            r = requests.get(
                f"{self.API_BASE}/crm/v7/{module}",
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
                "fields": fields,
                "per_page": 200,
                "page_token": info["next_page_token"],
            }
        return records

    def fetch_leads(self):
        return [Lead(rec) for rec in self._fetch_records("Leads", self.LEAD_FIELDS)]

    def fetch_lead(self, lead_id):
        """Fetch a single Lead (used to read converted Deal id when needed)."""
        r = requests.get(
            f"{self.API_BASE}/crm/v7/Leads/{lead_id}",
            headers=self._headers(),
            params={"fields": self.LEAD_FIELDS},
            timeout=30,
        )
        if r.status_code == 204:
            return None
        r.raise_for_status()
        data = (r.json().get("data") or [None])[0]
        return Lead(data) if data else None

    def fetch_deals(self):
        return [Deal(rec) for rec in self._fetch_records("Deals", self.DEAL_FIELDS)]

    def fetch_deal_contact_roles(self, deal_id):
        """Return Contact Role records (with Email) linked to a Deal."""
        r = requests.get(
            f"{self.API_BASE}/crm/v7/Deals/{deal_id}/Contact_Roles",
            headers=self._headers(),
            params={"fields": "Email,Full_Name"},
            timeout=30,
        )
        if r.status_code == 204:
            return []
        r.raise_for_status()
        return r.json().get("data") or []

    def fetch_deal_notes(self, deal_id):
        """Return Notes related to a Deal (Note_Title, Note_Content)."""
        notes = []
        page = 1
        while True:
            r = requests.get(
                f"{self.API_BASE}/crm/v7/Deals/{deal_id}/Notes",
                headers=self._headers(),
                params={
                    "fields": "Note_Title,Note_Content",
                    "per_page": 200,
                    "page": page,
                },
                timeout=30,
            )
            if r.status_code == 204:
                break
            r.raise_for_status()
            payload = r.json()
            notes.extend(payload.get("data") or [])
            info = payload.get("info") or {}
            if not info.get("more_records"):
                break
            page += 1
        return notes

    def create_deal_note(self, deal_id, title, content):
        """Create a Note on a Deal. Returns the created note id."""
        body = {
            "data": [
                {
                    "Note_Title": title,
                    "Note_Content": content,
                }
            ]
        }
        r = requests.post(
            f"{self.API_BASE}/crm/v7/Deals/{deal_id}/Notes",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
        r.raise_for_status()
        payload = r.json()
        data = (payload.get("data") or [{}])[0]
        if data.get("status") != "success" and data.get("code") != "SUCCESS":
            raise RuntimeError(f"Zoho create note failed: {payload}")
        return (data.get("details") or {}).get("id")
