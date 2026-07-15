import os

import requests

from .models import Deal, Lead


class ZohoClient:
    """Authenticates against Zoho CRM (India DC) and fetches lead records."""

    ACCOUNTS_URL = "https://accounts.zoho.in"
    API_BASE = "https://www.zohoapis.in"
    LEAD_FIELDS = (
        "Full_Name,Owner,Lead_Status,Remarks,"
        "Created_Time,Created_By,Modified_Time,Modified_By"
    )
    DEAL_FIELDS = (
        "Deal_Name,Partner,Stage,SQL,Amount,Closing_Date,"
        "Modified_Time,Created_Time,Owner"
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

    def _fetch_records(self, module, fields):
        headers = {"Authorization": f"Zoho-oauthtoken {self._access_token()}"}
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

    def fetch_deals(self):
        return [Deal(rec) for rec in self._fetch_records("Deals", self.DEAL_FIELDS)]
