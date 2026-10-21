
from notify import _send_email

recipient = input("Enter an email address you can access: ").strip()

try:
    result = _send_email(
        recipient,
        "Global One - Brevo connection test",
        "<h2>Brevo connection successful</h2>"
        "<p>Your Global One School System email configuration is working.</p>",
        "Brevo connection successful. Your Global One email configuration is working.",
    )
    print("Brevo accepted the email request.")
    print("Message ID:", result.get("messageId", "Not provided"))
except Exception as exc:
    print("Email test failed:", str(exc))
