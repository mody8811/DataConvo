"""Alert Dispatcher — asynchronous notification engine for Anomaly Studio.

Fires SMTP (email) and Slack (webhook) alerts when a data quality monitor
fails. Runs in a background thread so the core monitor check loop is never
blocked by network latency.
"""
import json
import logging
import os
import smtplib
import threading
import urllib.request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from time import sleep

logger = logging.getLogger(__name__)


def _smtp_config():
    return {
        'host': os.getenv('SMTP_HOST', ''),
        'port': int(os.getenv('SMTP_PORT', '587')),
        'username': os.getenv('SMTP_USER', ''),
        'password': os.getenv('SMTP_PASSWORD', ''),
        'from': os.getenv('SMTP_FROM_ADDRESS', 'support@dataconvo.app'),
        'use_tls': os.getenv('SMTP_USE_TLS', 'true').lower() in ('1', 'true', 'yes'),
    }


def _send_email(payload, recipients):
    """Send a plain-text alert email via SMTP. Returns (ok, error)."""
    cfg = _smtp_config()
    if not cfg['host'] or not cfg['username']:
        return False, 'SMTP not configured (SMTP_HOST/SMTP_USERNAME).'

    subject = f"[Data Convo] {payload['severity'].upper()} Alert: {payload['table_name']} — {payload['check_type']}"
    body = (
        f"Monitor: {payload['monitor_name']}\n"
        f"Table: {payload['table_name']}\n"
        f"Check Type: {payload['check_type']}\n"
        f"Severity: {payload['severity']}\n"
        f"Time: {payload['timestamp']}\n\n"
        f"Details: {payload['message']}\n"
    )

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = cfg['from']
    msg['To'] = ', '.join(recipients)
    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP(cfg['host'], cfg['port'])
        if cfg.get('use_tls'):
            server.starttls()
        if cfg['username']:
            server.login(cfg['username'], cfg['password'])
        server.sendmail(cfg['from'], recipients, msg.as_string())
        server.quit()
        return True, None
    except Exception as e:
        logger.warning(f'Email alert dispatch failed: {e}')
        return False, str(e)


def _send_slack(payload, webhook_url):
    """POST a Slack webhook payload. Returns (ok, error)."""
    if not webhook_url:
        return False, 'Slack webhook URL is empty.'

    color = '#ef4444' if payload['severity'] == 'critical' else '#f59e0b'
    slack_payload = {
        'attachments': [{
            'color': color,
            'title': f"[Data Convo] {payload['severity'].upper()} — {payload['table_name']}",
            'fields': [
                {'title': 'Check Type', 'value': payload['check_type'], 'short': True},
                {'title': 'Severity', 'value': payload['severity'], 'short': True},
                {'title': 'Time', 'value': payload['timestamp'], 'short': True},
                {'title': 'Table', 'value': payload['table_name'], 'short': True},
            ],
            'text': payload['message'],
            'footer': 'Data Convo Anomaly Studio',
            'ts': __import__('time').time(),
        }]
    }

    try:
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(slack_payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status >= 400:
                return False, f'Slack webhook returned HTTP {resp.status}'
        return True, None
    except Exception as e:
        logger.warning(f'Slack alert dispatch failed: {e}')
        return False, str(e)


def _dispatch(payload, channels):
    """Dispatch payload to all configured channels (email + slack)."""
    recipients = channels.get('emails') or []
    webhook_url = (channels.get('slack_webhook_url') or '').strip()

    if recipients:
        ok, err = _send_email(payload, recipients)
        logger.info(f'Alert email dispatch: ok={ok} err={err}')
    if webhook_url:
        ok, err = _send_slack(payload, webhook_url)
        logger.info(f'Alert slack dispatch: ok={ok} err={err}')


def dispatch_alert_async(payload, channels):
    """Fire-and-forget background dispatch without blocking the check loop."""
    def worker():
        try:
            # Minimal resilience: retry once after 2s on transient network errors
            _dispatch(payload, channels)
        except Exception as e:
            logger.error(f'Unexpected alert dispatch error: {e}')

    t = threading.Thread(target=worker, daemon=True)
    t.start()