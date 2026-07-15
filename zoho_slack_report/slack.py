import requests


class SlackNotifier:
    """Posts a message to a Slack Incoming Webhook."""

    def __init__(self, webhook_url):
        self.webhook_url = webhook_url

    def post(self, message):
        if not self.webhook_url:
            raise SystemExit("SLACK_WEBHOOK is not set in the environment/.env")
        r = requests.post(self.webhook_url, json={"text": message}, timeout=30)
        r.raise_for_status()
