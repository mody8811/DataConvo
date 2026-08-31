"""Book-a-Demo lead page: nice UX form -> email to support@dataconvo.app."""
from flask import Blueprint, render_template, request, redirect, url_for
from app import db

demo = Blueprint('demo', __name__)

DATA_PLATFORMS = [
    'Snowflake', 'PostgreSQL', 'MySQL', 'Microsoft SQL Server',
    'Databricks', 'BigQuery', 'Amazon Redshift', 'SQLite', 'Other / Not sure',
]


@demo.route('/demo', methods=['GET', 'POST'])
def demo_page():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        email = (request.form.get('email') or '').strip()
        company = (request.form.get('company') or '').strip()
        role = (request.form.get('role') or '').strip()
        platform = (request.form.get('platform') or '').strip()
        message = (request.form.get('message') or '').strip()
        if not name or ('@' not in email) or not company:
            return render_template(
                'demo.html', error='Please fill in your name, work email, and company.',
                data_platforms=DATA_PLATFORMS,
                values=request.form)
        try:
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            from app.services.email_service import _dispatch, FROM_ADDRESS
            rows = ''.join(
                f'<tr><td style="padding:6px 10px;color:#94a3b8;">{k}</td>'
                f'<td style="padding:6px 10px;color:#e2e8f0;">{v}</td></tr>'
                for k, v in [
                    ('Name', name), ('Email', email), ('Company', company),
                    ('Role', role or '—'), ('Data platform', platform or '—'),
                    ('Message', message or '—'),
                ])
            html = (
                '<div style="font-family:Inter,Arial,sans-serif;background:#0b0f19;'
                'padding:24px;border-radius:12px;max-width:560px;">'
                '<h2 style="color:#fff;font-size:18px;margin:0 0 12px;">\U0001F4C5 New Demo Request</h2>'
                f'<table style="border-collapse:collapse;width:100%;">{rows}</table>'
                '<p style="color:#64748b;font-size:12px;margin-top:14px;">'
                'Sent from the Data Convo Book a Demo page.</p></div>')
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f'Demo Request — {name} ({company})'
            msg['From'] = FROM_ADDRESS
            msg['To'] = 'support@dataconvo.app'
            msg.attach(MIMEText(f'Demo request from {name} ({email}, {company}).', 'plain'))
            msg.attach(MIMEText(html, 'html'))
            ok, note = _dispatch(msg, 'support@dataconvo.app', msg['Subject'])
            return render_template('demo.html', success=True,
                                   data_platforms=DATA_PLATFORMS,
                                   email_note=note if not ok else None)
        except Exception as e:
            return render_template('demo.html', error=f'Could not send request: {e}',
                                   data_platforms=DATA_PLATFORMS,
                                   values=request.form)
    return render_template('demo.html', data_platforms=DATA_PLATFORMS)