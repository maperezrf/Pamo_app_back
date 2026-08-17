import hashlib
import hmac

from config.constants import GITHUB_WEBHOOK_SECRET


class GitHubClient:
    def verify_webhook_signature(self, raw_body, header_signature):
        if not header_signature or not header_signature.startswith("sha256="):
            return False

        expected = hmac.new(
            GITHUB_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256
        ).hexdigest()
        received = header_signature.removeprefix("sha256=")
        return hmac.compare_digest(expected, received)
