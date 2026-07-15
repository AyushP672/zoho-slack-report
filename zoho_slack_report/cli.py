import argparse
import os

from dotenv import load_dotenv

from .deals_report import build_deals_message
from .leads_report import LeadsReport
from .partners_report import build_partner_message
from .slack import SlackNotifier
from .time_windows import current_work_week, trailing_24_hours
from .zoho import ZohoClient


def build_report_message(args):
    client = ZohoClient.from_env()
    if args.deals:
        if args.daily:
            start, end = trailing_24_hours()
            return build_deals_message(
                client.fetch_deals(),
                title="Daily Deal Report",
                start=start,
                end=end,
                movement_label="last 24 hours",
            )
        start, end = current_work_week()
        return build_deals_message(
            client.fetch_deals(),
            title="Weekly Deal Report",
            start=start,
            end=end,
            movement_label="this week",
        )
    if args.partners or args.rubix:
        return build_partner_message(client.fetch_deals())
    if args.daily:
        start, end = trailing_24_hours()
        return LeadsReport(
            client.fetch_leads(),
            start,
            end,
            title="Leads Daily Report",
            show_daily_breakdown=False,
        ).build_message()
    start, end = current_work_week()
    return LeadsReport(
        client.fetch_leads(),
        start,
        end,
        title="Leads Weekly Report",
        show_daily_breakdown=True,
    ).build_message()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Generate Zoho CRM reports for Slack.")
    report_group = parser.add_mutually_exclusive_group()
    report_group.add_argument(
        "--deals",
        action="store_true",
        help="Generate the Weekly or Daily Deal Report (AE/AD owners).",
    )
    report_group.add_argument(
        "--partners",
        action="store_true",
        help="Generate the Partners Weekly Report.",
    )
    report_group.add_argument(
        "--rubix",
        action="store_true",
        help="Alias for --partners (legacy flag).",
    )
    parser.add_argument(
        "--daily",
        action="store_true",
        help="Use daily window: Leads Daily (default) or Daily Deal Report (with --deals).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the report without posting to Slack.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    load_dotenv()
    args = parse_args(argv)
    message = build_report_message(args)
    print(message)

    if args.dry_run:
        print("\n(dry-run: not posting to Slack)")
        return

    SlackNotifier(os.environ.get("SLACK_WEBHOOK", "").strip()).post(message)
    print("\nPosted to Slack.")
