import re
from datetime import timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
MEETING_SET_STATUS = "Meeting Set"
# A remark counts as "not connected" if it mentions DNP (Did Not Pick).
DNP_RE = re.compile(r"\bdnp\b", re.I)
# CRM accounts belong to AEs, but SDRs operate them. Map to the real SDR name.
SDR_NAME_MAP = {
    "Jai Rathi": "Pranathi",
    "Eshan Aggarwal": "Indrani",
}

# Partner report configuration (Deals module). These partners are ALWAYS shown
# (rendering zeros when they have no deals in the CRM); any additional partners
# found in the data are included automatically.
ALWAYS_INCLUDE_PARTNERS = [
    "AVA",
    "ByteeIT",
    "CNK",
    "Core Bridge",
    "InCorp",
    "Qdesq",
    "Rubix",
]

# AE/AD deal reports: (display_name, crm_owner_name) from Deal.Owner.
AE_OWNERS = [
    ("Jai", "Jai Rathi"),
    ("Eshan", "Eshan Aggarwal"),
    ("Karan", "Karantaj Singh"),
]
MEETING_STAGES = {"Meeting Done - SQL", "Meeting Done - Not SQL Yet"}
CLOSED_STAGES = {
    "Closed Won",
    "Closed Lost",
    "Closed Lost to Competition",
    "Payment Recived",
}
