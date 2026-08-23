"""Email service for Data Convo workspace invites."""
import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)

FROM_ADDRESS = os.getenv('SMTP_FROM_ADDRESS', 'support@dataconvo.app')
SMTP_HOST = os.getenv('SMTP_HOST', '')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER', '')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')


def _render_invite_email(workspace_name, invite_url, inviter_name='Data Convo Admin'):
    """Render branded HTML invite email."""
    accent = '#6366f1'
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background-color:#f1f5f9;font-family:'Inter',-apple-system,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f1f5f9;padding:32px 12px;">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:12px;border:1px solid #e2e8f0;">
        <tr><td style="background:linear-gradient(135deg,{accent},#8b5cf6);padding:28px 32px;text-align:center;">
          <span style="font-size:26px;font-weight:800;color:#ffffff;">Data Convo</span>
        </td></tr>
        <tr><td style="padding:32px;">
          <h1 style="font-size:20px;color:#0f172a;margin:0 0 16px;">You've been invited 🤝</h1>
          <p style="font-size:15px;line-height:1.6;color:#475569;margin:0 0 12px;">
            <strong>{inviter_name}</strong> has invited you to join the
            <strong style="color:{accent};">{workspace_name}</strong>
            workspace on Data Convo.
          </p>
          <p style="font-size:15px;line-height:1.6;color:#475569;margin:0 0 24px;">
            Accept the invitation to start querying your team's data and building dashboards together.
          </p>
          <a href="{invite_url}" style="background:linear-gradient(135deg,{accent},#8b5cf6);color:#ffffff;text-decoration:none;padding:13px 28px;border-radius:8px;font-size:15px;font-weight:700;display:inline-block;">Accept Invitation →</a>
          <p style="font-size:13px;color:#94a3b8;margin:24px 0 0;">
            If the button doesn't work, open: <a href="{invite_url}" style="color:{accent};">{invite_url}</a>
          </p>
        </td></tr>
        <tr><td style="background:#f8fafc;padding:20px 32px;text-align:center;border-top:1px solid #e2e8f0;">
          <span style="font-size:12px;color:#94a3b8;">Sent from {FROM_ADDRESS} · © Data Convo {os.getenv('APP_YEAR', '2026')}</span>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def send_invite_email(recipient_email, invite_url, workspace_name, inviter_name='Data Convo Admin'):
    """Send branded invite email from support@dataconvo.app."""
    subject = f"You've been invited to {workspace_name} on Data Convo"
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = FROM_ADDRESS
    msg['To'] = recipient_email
    msg.attach(MIMEText(f"Join {workspace_name}: {invite_url}", 'plain'))
    msg.attach(MIMEText(_render_invite_email(workspace_name, invite_url, inviter_name), 'html'))

    return _dispatch(msg, recipient_email, subject)


def _dispatch(msg, recipient_email, subject):
    """Shared SMTP dispatch — logs for dev when SMTP isn't configured."""
    if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD):
        logger.info("[dev-email] To=%s Subject=%s", recipient_email, subject)
        return True, "SMTP not configured — email logged (dev)."
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        return True, "Email sent."
    except Exception as e:
        logger.warning('Email failed to %s: %s', recipient_email, e)
        return False, f"Failed to send email: {e}"


def _render_welcome_email(login_url):
    """Branded welcome email."""
    accent = '#6366f1'
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background-color:#f1f5f9;font-family:'Inter',-apple-system,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f1f5f9;padding:32px 12px;">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:12px;border:1px solid #e2e8f0;">
        <tr><td style="background:linear-gradient(135deg,{accent},#8b5cf6);padding:28px 32px;text-align:center;">
          <span style="font-size:26px;font-weight:800;color:#ffffff;">Data Convo</span>
        </td></tr>
        <tr><td style="padding:32px;">
          <h1 style="font-size:20px;color:#0f172a;margin:0 0 16px;">Welcome to Data Convo 🎉</h1>
          <p style="font-size:15px;line-height:1.6;color:#475569;margin:0 0 12px;">Your workspace is ready. Thanks for signing up!</p>
          <p style="font-size:15px;line-height:1.6;color:#475569;margin:0 0 24px;">Log in and connect your first database to start querying your data with natural language.</p>
          <a href="{login_url}" style="background:linear-gradient(135deg,{accent},#8b5cf6);color:#ffffff;text-decoration:none;padding:13px 28px;border-radius:8px;font-size:15px;font-weight:700;display:inline-block;">Log In & Connect a Database →</a>
        </td></tr>
        <tr><td style="background:#f8fafc;padding:20px 32px;text-align:center;border-top:1px solid #e2e8f0;">
          <span style="font-size:12px;color:#94a3b8;">Sent from {FROM_ADDRESS} · © Data Convo {os.getenv('APP_YEAR', '2026')}</span>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def send_welcome_email(recipient_email, login_url):
    """Send branded welcome email after registration."""
    subject = "Welcome to Data Convo! 🎉"
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = FROM_ADDRESS
    msg['To'] = recipient_email
    msg.attach(MIMEText(f"Welcome to Data Convo! Log in at {login_url} and connect your first database.", 'plain'))
    msg.attach(MIMEText(_render_welcome_email(login_url), 'html'))
    return _dispatch(msg, recipient_email, subject)


def _render_reset_email(reset_url):
    """Branded password-reset email."""
    accent = '#6366f1'
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background-color:#f1f5f9;font-family:'Inter',-apple-system,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f1f5f9;padding:32px 12px;">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:12px;border:1px solid #e2e8f0;">
        <tr><td style="background:linear-gradient(135deg,{accent},#8b5cf6);padding:28px 32px;text-align:center;">
          <span style="font-size:26px;font-weight:800;color:#ffffff;">Data Convo</span>
        </td></tr>
        <tr><td style="padding:32px;">
          <h1 style="font-size:20px;color:#0f172a;margin:0 0 16px;">Reset your password 🔐</h1>
          <p style="font-size:15px;line-height:1.6;color:#475569;margin:0 0 24px;">We received a request to reset your Data Convo password. This link expires in <strong>60 minutes</strong>.</p>
          <a href="{reset_url}" style="background:linear-gradient(135deg,{accent},#8b5cf6);color:#ffffff;text-decoration:none;padding:13px 28px;border-radius:8px;font-size:15px;font-weight:700;display:inline-block;">Reset Password →</a>
          <p style="font-size:13px;color:#94a3b8;margin:24px 0 0;">If the button doesn't work, open: <a href="{reset_url}" style="color:{accent};">{reset_url}</a></p>
          <p style="font-size:12px;color:#94a3b8;margin:16px 0 0;">If you didn't request this, you can safely ignore this email.</p>
        </td></tr>
        <tr><td style="background:#f8fafc;padding:20px 32px;text-align:center;border-top:1px solid #e2e8f0;">
          <span style="font-size:12px;color:#94a3b8;">Sent from {FROM_ADDRESS} · © Data Convo {os.getenv('APP_YEAR', '2026')}</span>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def send_password_reset_email(recipient_email, reset_url):
    """Send branded password-reset email."""
    subject = "Reset your Data Convo password"
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = FROM_ADDRESS
    msg['To'] = recipient_email
    msg.attach(MIMEText(f"Reset your password: {reset_url} (expires in 60 minutes)", 'plain'))
    msg.attach(MIMEText(_render_reset_email(reset_url), 'html'))
    return _dispatch(msg, recipient_email, subject)
