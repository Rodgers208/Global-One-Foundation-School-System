
import html
import logging

import requests

from config import cfg

log = logging.getLogger("sms.notify")

BREVO_EMAIL_URL = "https://api.brevo.com/v3/smtp/email"


def _send_email(to_email, subject, html_content, text_content):
    """Send a transactional email using the Brevo API."""
    api_key = cfg("BREVO_API_KEY", "").strip()
    sender_email = cfg("MAIL_FROM_EMAIL", "").strip()
    sender_name = cfg("MAIL_FROM_NAME", "Global One School System").strip()

    if not api_key or not sender_email:
        raise RuntimeError("Brevo API key or sender email is not configured.")

    if not to_email:
        raise ValueError("Recipient email address is missing.")

    payload = {
        "sender": {
            "name": sender_name,
            "email": sender_email,
        },
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_content,
        "textContent": text_content,
    }

    response = requests.post(
        BREVO_EMAIL_URL,
        headers={
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json",
        },
        json=payload,
        timeout=15,
    )

    if not response.ok:
        # Do not log API keys, message bodies, or reset tokens.
        log.error("Brevo email delivery request failed (HTTP %s).", response.status_code)
        raise RuntimeError("Brevo could not accept the email request.")

    log.info("Brevo accepted a transactional email (HTTP %s).", response.status_code)
    return response.json()


def send_code(user, code, kind):
    """Email an existing six-digit verification code."""
    recipient = (user.get("email") or "").strip()

    if not recipient:
        raise RuntimeError("This account has no email address for verification.")

    safe_code = html.escape(str(code))
    safe_name = html.escape(str(user.get("name") or "there"))
    subject = (
        "Your Global One sign-in verification code"
        if kind == "2fa"
        else "Your Global One password reset code"
    )

    html_content = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:auto">
      <h2>Global One School System</h2>
      <p>Hello {safe_name},</p>
      <p>Your verification code is:</p>
      <p style="font-size:28px;font-weight:bold;letter-spacing:5px">
        {safe_code}
      </p>
      <p>If you did not request this code, you can ignore this email.</p>
    </div>
    """

    text_content = (
        f"Hello {user.get('name') or 'there'},\n\n"
        f"Your Global One verification code is: {code}\n\n"
        "If you did not request this code, ignore this email."
    )

    _send_email(recipient, subject, html_content, text_content)


def send_reset_email(user, raw_token):
    """Send a password-reset link. Store only a hash of raw_token in the database."""
    recipient = (user.get("email") or "").strip()
    frontend_url = cfg("FRONTEND_URL", "").strip().rstrip("/")

    if not recipient:
        raise RuntimeError("This account has no email address.")

    if not frontend_url.startswith("https://"):
        # Permit localhost only for local development.
        if not frontend_url.startswith("http://localhost:") and not frontend_url.startswith(
            "http://127.0.0.1:"
        ):
            raise RuntimeError("FRONTEND_URL must use HTTPS outside local development.")

    from urllib.parse import urlencode

    reset_url = f"{frontend_url}/reset?{urlencode({'token': raw_token})}"
    safe_url = html.escape(reset_url, quote=True)
    safe_name = html.escape(str(user.get("name") or "there"))

    html_content = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:auto">
      <h2>Global One School System</h2>
      <p>Hello {safe_name},</p>
      <p>We received a request to reset your password.</p>
      <p>
        <a href="{safe_url}"
           style="display:inline-block;padding:12px 20px;background:#c9932e;
                  color:#111;text-decoration:none;border-radius:6px">
          Reset your password
        </a>
      </p>
      <p>This link expires in 30 minutes and can be used only once.</p>
      <p>If you did not request a reset, you can ignore this email.</p>
    </div>
    """

    text_content = (
        f"Hello {user.get('name') or 'there'},\n\n"
        "We received a request to reset your password.\n"
        f"Open this link to continue: {reset_url}\n\n"
        "The link expires in 30 minutes and should only be used once. "
        "If you did not request this, ignore this email."
    )

    _send_email(
        recipient,
        "Reset your Global One School System password",
        html_content,
        text_content,
    )
