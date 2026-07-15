import re
from dataclasses import dataclass
from datetime import datetime

from .config import (
    CLOSED_STAGES,
    DNP_RE,
    MEETING_SET_STATUS,
    MEETING_STAGES,
    SDR_NAME_MAP,
)


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


@dataclass
class Deal:
    """A single Zoho CRM deal record with typed access to the fields we use."""

    raw: dict

    @staticmethod
    def _time(value):
        return datetime.fromisoformat(value) if value else None

    @staticmethod
    def _date(value):
        return datetime.fromisoformat(value).date() if value else None

    @property
    def owner(self):
        user = self.raw.get("Owner") or {}
        return user.get("name") or user.get("email") or "(unknown)"

    @property
    def partner(self):
        return self.raw.get("Partner")

    @property
    def stage(self):
        return self.raw.get("Stage")

    @property
    def sql(self):
        return self.raw.get("SQL")

    @property
    def amount(self):
        return self.raw.get("Amount") or 0

    @property
    def closing_date(self):
        return self._date(self.raw.get("Closing_Date"))

    @property
    def modified_at(self):
        return self._time(self.raw.get("Modified_Time"))

    @property
    def created_at(self):
        return self._time(self.raw.get("Created_Time"))

    @property
    def is_open(self):
        return self.stage not in CLOSED_STAGES

    @property
    def is_meeting_done(self):
        return self.stage in MEETING_STAGES

    @property
    def is_sql(self):
        return self.sql == "Yes"
