import argparse
import os

from dotenv import load_dotenv

from .calendar_client import CalendarClient
from .deals_report import build_deals_message
from .email_notes import build_email_notes_slack_message, sync_email_notes
from .gmail_client import GmailClient
from .leads_report import LeadsReport
from .meeting_notes import build_meeting_notes_slack_message, sync_meeting_notes
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


def _indrani_webhook():
    webhook = os.environ.get("SLACK_WEBHOOK_INDRANI", "").strip()
    if not webhook:
        raise SystemExit(
            "SLACK_WEBHOOK_INDRANI is not set in the environment/.env "
            "(required for notes review summary)"
        )
    return webhook


def _deals_webhook():
    webhook = os.environ.get("SLACK_WEBHOOK_DEALS", "").strip()
    if not webhook:
        raise SystemExit(
            "SLACK_WEBHOOK_DEALS is not set in the environment/.env "
            "(required for email notes review summary)"
        )
    return webhook


def run_email_notes(dry_run=False):
    zoho = ZohoClient.from_env()
    gmail = GmailClient.from_env()
    stats = sync_email_notes(zoho, gmail, dry_run=dry_run)
    message = build_email_notes_slack_message(stats)
    print(message)

    if dry_run:
        print("\n(dry-run: not writing Zoho notes or posting to Slack)")
        return

    SlackNotifier(_deals_webhook()).post(message)
    print("\nPosted review summary to Slack (SLACK_WEBHOOK_DEALS).")


def run_meeting_notes(dry_run=False):
    zoho = ZohoClient.from_env()
    calendar = CalendarClient.from_env()
    stats = sync_meeting_notes(zoho, calendar, dry_run=dry_run)
    message = build_meeting_notes_slack_message(stats)
    print(message)

    if dry_run:
        print("\n(dry-run: not writing Zoho notes or posting to Slack)")
        return

    SlackNotifier(_indrani_webhook()).post(message)
    print("\nPosted review summary to Indrani's Slack.")


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
    report_group.add_argument(
        "--email-notes",
        action="store_true",
        help=(
            "Sync last-24h Gmail into Zoho Deal Notes and post "
            "a review summary to Indrani's Slack webhook."
        ),
    )
    report_group.add_argument(
        "--meeting-notes",
        action="store_true",
        help=(
            "Sync last-24h Google Calendar meetings for the sales team into "
            "Zoho Deal Notes and post a name-grouped review summary to Indrani."
        ),
    )
    parser.add_argument(
        "--daily",
        action="store_true",
        help="Use daily window: Leads Daily (default) or Daily Deal Report (with --deals).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print without posting to Slack (and without writing notes for notes syncs).",
    )
    return parser.parse_args(argv)


def main(argv=None):
    load_dotenv()
    args = parse_args(argv)

    if args.email_notes:
        run_email_notes(dry_run=args.dry_run)
        return

    if args.meeting_notes:
        run_meeting_notes(dry_run=args.dry_run)
        return

    message = build_report_message(args)
    print(message)

    if args.dry_run:
        print("\n(dry-run: not posting to Slack)")
        return

    SlackNotifier(os.environ.get("SLACK_WEBHOOK", "").strip()).post(message)
    print("\nPosted to Slack.")
