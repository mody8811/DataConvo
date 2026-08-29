from flask import Blueprint, render_template, redirect, url_for, request, flash, session, current_app
from flask_login import login_user, logout_user, login_required, current_user
from app.models import User
from app import db
import os

try:
    from authlib.integrations.flask_client import OAuth
    AUTHLIB_AVAILABLE = True
except ImportError:
    OAuth = None
    AUTHLIB_AVAILABLE = False

# Supabase Auth (optional — only active if SUPABASE_URL and SUPABASE_ANON_KEY are set)
try:
    from supabase import create_client, Client
    SUPABASE_AVAILABLE = True
except ImportError:
    from typing import Any
    Client = Any
    SUPABASE_AVAILABLE = False

auth = Blueprint('auth', __name__)

SUPABASE_URL = os.getenv('SUPABASE_URL', '')
SUPABASE_ANON_KEY = os.getenv('SUPABASE_ANON_KEY', '')
USES_SUPABASE = bool(SUPABASE_URL and SUPABASE_ANON_KEY and SUPABASE_AVAILABLE)

_supabase_client = None


def get_supabase_client():
    """Lazily initialize the Supabase client."""
    global _supabase_client
    if _supabase_client is None and USES_SUPABASE:
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    return _supabase_client


@auth.route('/signup', methods=['GET', 'POST'])
def signup():
    # Logged-in users should not see the signup page -> go straight to Account.
    if current_user.is_authenticated:
        return redirect(url_for('auth.account'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        username = request.form.get('username', '').strip() or email.split('@')[0]

        # Invite-aware signup: an invite_token query param is accepted; if present,
        # verify the pending invite and LOCK the email to the invite.
        invite_token = request.args.get('invite_token', '').strip()
        invite_workspace = None
        if invite_token:
            from app.models import TeamInvite
            inv = TeamInvite.query.filter_by(token=invite_token, status='pending').first()
            if not inv:
                flash('This invite is invalid or has already been used.', 'error')
                return redirect(url_for('auth.signup'))
            if email != inv.email:
                flash('This invitation is linked to a specific email and cannot be changed.', 'error')
                return redirect(url_for('main.invite_accept', token=invite_token))
            invite_workspace = inv.workspace_id

        if not email or not password:
            flash('Email and password are required.', 'error')
            return redirect(url_for('auth.signup'))

        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'error')
            return redirect(url_for('auth.signup'))

        # Check if user already exists locally
        if User.query.filter_by(email=email).first():
            flash('An account with that email already exists. Please log in.', 'error')
            return redirect(url_for('auth.login'))

        # Create user via Supabase Auth (if configured), then also record locally
        supabase_user_id = None
        if USES_SUPABASE:
            try:
                supabase = get_supabase_client()
                supabase_resp = supabase.auth.sign_up({
                    'email': email,
                    'password': password,
                    'options': {'data': {'username': username}}
                })
                supabase_user = supabase_resp.user if hasattr(supabase_resp, 'user') else None
                if supabase_user:
                    supabase_user_id = supabase_user.id
                    # Store the Supabase session token in the Flask session for subsequent API calls
                    session['supabase_access_token'] = supabase_resp.session.access_token if hasattr(supabase_resp, 'session') and supabase_resp.session else None
                    session['supabase_refresh_token'] = supabase_resp.session.refresh_token if hasattr(supabase_resp, 'session') and supabase_resp.session else None
            except Exception as e:
                current_app.logger.warning(f"Supabase signup failed (falling back to local auth): {e}")
                # If the user already exists in Supabase, fall through to local auth
                user_exists = User.query.filter_by(email=email).first()
                if user_exists:
                    flash('An account with that email already exists. Please log in.', 'error')
                    return redirect(url_for('auth.login'))

        # Create local user record (always keep in sync with Supabase)
        from app.models import Workspace
        user = User(email=email, username=username)
        user.set_password(password)
        if supabase_user_id:
            user.supabase_id = supabase_user_id

        if invite_workspace:
            # Invited user: join the inviter's workspace as a MEMBER (not admin).
            user.role = 'member'
            user.workspace_id = invite_workspace
            db.session.add(user)
            db.session.flush()
            # Mark the invite as accepted
            inv.status = 'accepted'
        else:
            # New workspace flow: the first user in a new workspace is the admin
            user.role = 'admin'
            db.session.add(user)
            db.session.flush()  # get user.id
            # Create their personal/team workspace
            workspace = Workspace(
                name=f"{username or email.split('@')[0]}'s Workspace",
                owner_id=user.id
            )
            db.session.add(workspace)
            db.session.flush()  # get workspace.id
            user.workspace_id = workspace.id

        db.session.commit()
        login_user(user)

        # Automated welcome email — fired AFTER the user record is committed.
        try:
            from app.services.email_service import send_welcome_email
            login_url = url_for('auth.login', _external=True)
            send_welcome_email(email, login_url)
        except Exception as e:
            current_app.logger.warning(f"Welcome email failed for {email}: {e}")

        flash('Account created successfully! Welcome to Data Convo.', 'success')
        flash(
            "Next, you'll be prompted to scan a QR code with your authenticator app "
            "(like Google Authenticator or Authy) to secure your workspace.",
            'info',
        )
        return redirect(url_for('auth.setup_mfa_page'))

    return render_template('signup.html', uses_supabase=USES_SUPABASE)


@auth.route('/login', methods=['GET', 'POST'])
def login():
    # Logged-in users should not see the login page -> go straight to Account.
    if current_user.is_authenticated:
        return redirect(url_for('auth.account'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'

        if not email or not password:
            flash('Email and password are required.', 'error')
            return redirect(url_for('auth.login'))

        # Try Supabase Auth first (if configured)
        if USES_SUPABASE:
            try:
                supabase = get_supabase_client()
                supabase_resp = supabase.auth.sign_in_with_password({
                    'email': email,
                    'password': password
                })
                if supabase_resp.user:
                    # Store the Supabase session for subsequent API calls
                    session['supabase_access_token'] = supabase_resp.session.access_token if hasattr(supabase_resp, 'session') and supabase_resp.session else None
                    session['supabase_refresh_token'] = supabase_resp.session.refresh_token if hasattr(supabase_resp, 'session') and supabase_resp.session else None

                    # Ensure local user record exists
                    user = User.query.filter_by(email=email).first()
                    if not user:
                        user = User(
                            email=email,
                            username=email.split('@')[0],
                        )
                        user.set_password(password)
                        user.supabase_id = supabase_resp.user.id
                        db.session.add(user)
                        db.session.commit()

                    # TOTP MFA handshake for Supabase-authenticated users too.
                    if user.totp_secret and not session.get('mfa_verified'):
                        session['pending_mfa_user'] = user.id
                        session['pending_mfa_remember'] = remember
                        nxt = request.args.get('next')
                        if nxt:
                            session['pending_mfa_next'] = nxt
                        return redirect(url_for('auth.mfa_challenge'))
                    login_user(user, remember=remember)
                    return redirect(url_for('auth.account'))
            except Exception as e:
                # Supabase login failed — fall through to local auth
                current_app.logger.warning(f"Supabase login failed (trying local auth): {e}")

        # Local auth fallback
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            # TOTP MFA ENFORCEMENT: if mfa_verified=True, stage for verify-mfa.
            if getattr(user, 'mfa_verified', False) and not session.get('mfa_verified'):
                session['pending_mfa_user'] = user.id
                session['pending_mfa_remember'] = remember
                nxt = request.args.get('next')
                if nxt:
                    session['pending_mfa_next'] = nxt
                return redirect(url_for('auth.verify_mfa_page'))
            # Legacy: users with totp_secret but no mfa_verified yet
            if user.totp_secret and not session.get('mfa_verified'):
                session['pending_mfa_user'] = user.id
                session['pending_mfa_remember'] = remember
                nxt = request.args.get('next')
                if nxt:
                    session['pending_mfa_next'] = nxt
                return redirect(url_for('auth.mfa_challenge'))
            login_user(user, remember=remember)
            return redirect(url_for('auth.account'))

        flash('Invalid email or password.', 'error')
        return redirect(url_for('auth.login'))

    return render_template('login.html', uses_supabase=USES_SUPABASE)


@auth.route('/logout')
@login_required
def logout():
    # Also sign out of Supabase if configured
    if USES_SUPABASE:
        try:
            supabase = get_supabase_client()
            supabase.auth.sign_out()
        except Exception as e:
            current_app.logger.warning(f"Supabase signout failed: {e}")
    session.pop('supabase_access_token', None)
    session.pop('supabase_refresh_token', None)
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


# ===== Forgot Password (ItsDangerous, 60-min expiry) =====
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

RESET_TOKEN_MAX_AGE = 60 * 60  # 60 minutes


def _reset_serializer():
    return URLSafeTimedSerializer(
        current_app.config['SECRET_KEY'],
        salt='dataconvo-password-reset',
    )


@auth.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Request a password reset link for an email."""
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        if not email:
            flash('Please enter your email address.', 'error')
            return redirect(url_for('auth.forgot_password'))

        user = User.query.filter_by(email=email).first()
        # Always show the same message to avoid user enumeration.
        if user:
            token = _reset_serializer().dumps(user.email)
            reset_url = url_for('auth.reset_password', token=token, _external=True)
            try:
                from app.services.email_service import send_password_reset_email
                send_password_reset_email(user.email, reset_url)
                current_app.logger.info('Password reset email sent to %s', user.email)
            except Exception as e:
                current_app.logger.warning(f"Password reset email failed for {email}: {e}")
        flash(
            'If an account exists for that email, a password reset link has been sent. '
            'The link expires in 60 minutes.',
            'info',
        )
        return redirect(url_for('auth.login'))

    return render_template('forgot_password.html')


@auth.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    """Validate the reset token and update the user's password."""
    token = request.args.get('token', '') or (request.form.get('token', '') if request.method == 'POST' else '')
    if not token:
        flash('Missing reset token.', 'error')
        return redirect(url_for('auth.forgot_password'))

    try:
        email = _reset_serializer().loads(token, max_age=RESET_TOKEN_MAX_AGE)
    except SignatureExpired:
        flash('This password reset link has expired. Please request a new one.', 'error')
        return redirect(url_for('auth.forgot_password'))
    except BadSignature:
        flash('This password reset link is invalid. Please request a new one.', 'error')
        return redirect(url_for('auth.forgot_password'))

    user = User.query.filter_by(email=email).first()
    if not user:
        flash('This account no longer exists.', 'error')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'error')
            return render_template('reset_password.html', token=token)
        if password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html', token=token)

        user.set_password(password)
        db.session.commit()
        session.pop('mfa_verified', None)
        flash('Your password has been updated. Please log in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('reset_password.html', token=token)


# ===== Account Dashboard =====
@auth.route('/account')
@login_required
def account():
    """User profile/account page showing plan status and upgrade options."""
    from app.models import Workspace, TeamInvite
    from app.routes import main as main_routes

    workspace = None
    members = []
    invites = []
    seat_limit = 1
    member_count = 0

    if current_user.workspace_id:
        workspace = Workspace.query.get(current_user.workspace_id)
        if workspace:
            members = [m for m in workspace.members if m.role == 'member']
            member_count = len(members)
            invites = TeamInvite.query.filter_by(workspace_id=workspace.id, status='pending').all()
    elif current_user.role == 'admin':
        # Try to find/create their workspace
        workspace = Workspace.query.filter_by(owner_id=current_user.id).first()
        if not workspace:
            workspace = Workspace(
                name=f"{current_user.username or current_user.email.split('@')[0]}'s Workspace",
                owner_id=current_user.id
            )
            from app import db as app_db
            app_db.session.add(workspace)
            app_db.session.flush()
            current_user.workspace_id = workspace.id
            app_db.session.commit()
            members = []
            invites = []
        else:
            if not current_user.workspace_id:
                current_user.workspace_id = workspace.id
                from app import db as app_db
                app_db.session.commit()
            members = [m for m in workspace.members if m.role == 'member']
            member_count = len(members)
            invites = TeamInvite.query.filter_by(workspace_id=workspace.id, status='pending').all()

    # Seat limit based on plan
    if current_user.subscription_tier == 'pro':
        seat_limit = 3
    else:
        seat_limit = 1

    import os as _os
    team_payment_link = (_os.getenv('STRIPE_TEAM_PAYMENT_LINK') or '').strip()
    return render_template(
        'account.html',
        user=current_user,
        workspace=workspace,
        members=members,
        invites=invites,
        member_count=member_count,
        seat_limit=seat_limit,
        pending_invites_count=len(invites),
        uses_stripe=__import__('app.routes.billing', fromlist=['USES_STRIPE']).USES_STRIPE,
        current_tier=current_user.subscription_tier or 'free',
        team_payment_link=team_payment_link,
    )


# ===== TOTP MFA (ISO 27001) =====
import io
import base64
import pyotp
import qrcode


def _get_mfa_secret(user):
    if user.totp_secret:
        return user.totp_secret
    user.totp_secret = pyotp.random_base32()
    db.session.commit()
    return user.totp_secret


def _mfa_provisioning(user):
    secret = _get_mfa_secret(user)
    otp = pyotp.TOTP(secret)
    uri = otp.provisioning_uri(name=user.email, issuer_name="Data Convo")
    qr = qrcode.make(uri)
    buf = io.BytesIO()
    qr.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return {"secret": secret, "qr_b64": qr_b64, "provisioning_uri": uri,
            "issuer": "Data Convo", "email": user.email}


@auth.route('/api/auth/mfa/setup', methods=['GET', 'POST'])
@login_required
def mfa_setup():
    prov = _mfa_provisioning(current_user)
    return {"secret": prov["secret"], "qr_b64": prov["qr_b64"],
            "provisioning_uri": prov["provisioning_uri"],
            "issuer": prov["issuer"], "email": prov["email"],
            "enrolled": bool(current_user.totp_secret and current_user.mfa_verified)}


@auth.route('/api/auth/mfa/verify', methods=['POST'])
@login_required
def mfa_verify():
    data = request.get_json(silent=True) or {}
    code = (data.get('code') or '').strip()
    if not code:
        return {"error": "A 6-digit code is required."}, 400
    if not current_user.totp_secret:
        return {"error": "MFA is not enabled for this account."}, 400
    if not pyotp.TOTP(current_user.totp_secret).verify(code, valid_window=1):
        return {"error": "Invalid or expired code."}, 400
    session['mfa_verified'] = True
    current_user.mfa_verified = True
    db.session.commit()
    return {"success": True, "mfa_enabled": True}


@auth.route('/api/auth/mfa/status')
@login_required
def mfa_status():
    return {"enrolled": bool(current_user.totp_secret),
            "verified": bool(current_user.mfa_verified)}


@auth.route('/api/auth/mfa/disable', methods=['POST'])
@login_required
def mfa_disable():
    """Disable TOTP 2FA for the current user.

    Clears the totp_secret and mfa_verified flags. Called from the
    Account Settings security tab ("Disable 2FA" button).
    """
    current_user.totp_secret = None
    current_user.mfa_verified = False
    db.session.commit()
    return {"success": True, "mfa_enabled": False}


@auth.route('/mfa/setup')
@login_required
def mfa_setup_page():
    """Render the MFA enrollment page (QR + manual key + code verify)."""
    return render_template('mfa_enroll.html')


@auth.route('/auth/setup-mfa')
@login_required
def setup_mfa_page():
    """Post-signup Security Setup page — shows QR + code entry immediately."""
    return render_template('setup_mfa.html', email=current_user.email)


@auth.route('/auth/verify-mfa', methods=['GET', 'POST'])
def verify_mfa_page():
    """MFA verification page for users with mfa_verified=True.

    Displays an OTP input. Verifies the code against the user's TOTP secret.
    Only upon success completes the session login and redirects.
    """
    pending_id = session.get('pending_mfa_user')
    if not pending_id:
        flash('Please sign in first.', 'error')
        return redirect(url_for('auth.login'))

    user = User.query.get(pending_id)
    if not user or not user.totp_secret:
        session.pop('pending_mfa_user', None)
        session.pop('pending_mfa_remember', None)
        session.pop('pending_mfa_next', None)
        flash('MFA is not enabled for this account.', 'error')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        code = (request.form.get('code') or '').strip()
        if not code:
            flash('Enter your 6-digit code.', 'error')
        elif pyotp.TOTP(user.totp_secret).verify(code, valid_window=1):
            session['mfa_verified'] = True
            user.mfa_verified = True
            db.session.commit()
            login_user(user, remember=bool(session.get('pending_mfa_remember')))
            session.pop('pending_mfa_next', None)
            session.pop('pending_mfa_user', None)
            session.pop('pending_mfa_remember', None)
            return redirect(url_for('auth.account'))
        else:
            flash('Invalid or expired code.', 'error')

    return render_template('mfa_code.html', email=user.email)


@auth.route('/mfa/challenge', methods=['GET', 'POST'])
def mfa_challenge():
    """MFA code-entry screen (used after staged login)."""
    pending_id = session.get('pending_mfa_user')
    if not pending_id:
        flash('Please sign in first.', 'error')
        return redirect(url_for('auth.login'))

    user = User.query.get(pending_id)
    if not user or not user.totp_secret:
        session.pop('pending_mfa_user', None)
        session.pop('pending_mfa_remember', None)
        session.pop('pending_mfa_next', None)
        flash('MFA is not enabled for this account.', 'error')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        code = (request.form.get('code') or '').strip()
        if not code:
            flash('Enter your 6-digit code.', 'error')
        elif pyotp.TOTP(user.totp_secret).verify(code, valid_window=1):
            session['mfa_verified'] = True
            user.mfa_verified = True
            db.session.commit()
            login_user(user, remember=bool(session.get('pending_mfa_remember')))
            session.pop('pending_mfa_next', None)
            session.pop('pending_mfa_user', None)
            session.pop('pending_mfa_remember', None)
            return redirect(url_for('auth.account'))
        else:
            flash('Invalid or expired code.', 'error')

    return render_template('mfa_code.html', email=user.email)
