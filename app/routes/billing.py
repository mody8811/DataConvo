from flask import Blueprint, render_template, request, jsonify, current_app, redirect, url_for, flash
from flask_login import login_required, current_user
from app import db
from app.models import User, TeamInvite, Workspace
from app.services.licensing import (
    activate_license,
    get_plan,
    get_seat_limit,
    normalize_tier,
    workspace_seat_usage,
    PLAN_DEFS,
)
import os
import json

try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    stripe = None
    STRIPE_AVAILABLE = False

billing = Blueprint('billing', __name__)

# Stripe is optional. If not configured, the pricing page still works but
# checkout buttons are disabled with a helpful message.
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')
PRO_PRICE_ID = os.getenv('STRIPE_PRO_PRICE_ID', 'price_placeholder')

# Stripe Checkout / Payment Link for the Team/Pro tier ($399/month).
# Set STRIPE_TEAM_PAYMENT_LINK to the payment-link URL generated in Stripe
# (e.g. https://buy.stripe.com/dRmfZh1ti2lF8oCgnK0x200).
STRIPE_TEAM_PAYMENT_LINK = os.getenv('STRIPE_TEAM_PAYMENT_LINK', '').strip()

USES_STRIPE = bool(STRIPE_SECRET_KEY and not STRIPE_SECRET_KEY.startswith('sk_test_placeholder') and STRIPE_AVAILABLE)


def is_pro(user):
    return user.subscription_tier == 'pro'


@billing.route('/pricing')
def pricing():
    return render_template(
        'pricing.html',
        uses_stripe=USES_STRIPE,
        current_tier=current_user.subscription_tier if current_user.is_authenticated else 'free',
        plans=PLAN_DEFS,
        team_payment_link=STRIPE_TEAM_PAYMENT_LINK,
    )


# ===== License & Subscription (self-hosted activation portal) =====

@billing.route('/account/license/status')
@login_required
def license_status():
    """Return the current license tier, seat usage, and BYOK requirement."""
    from app.agents.llm_router import get_byok_state
    tier = normalize_tier(getattr(current_user, 'subscription_tier', 'community') or 'community')
    member_count, pending, seat_limit = workspace_seat_usage(current_user)
    enabled, active_provider, has_openai, has_anthropic = get_byok_state(current_user)
    return jsonify({
        "tier": tier,
        "plan": get_plan(tier),
        "seat_limit": seat_limit,
        "member_count": member_count,
        "pending_invites": pending,
        "seats_used": member_count + pending,
        "license_key": getattr(current_user, 'license_key', None),
        "byok": {
            "required": True,
            "configured": bool(enabled and (has_openai or has_anthropic)),
            "provider": active_provider,
        },
    })


@billing.route('/account/license/activate', methods=['POST'])
@login_required
def license_activate():
    """Validate + save a license key. Admin only (workspace-scoped)."""
    if current_user.role != 'admin':
        return jsonify({"error": "Admin access required."}), 403
    data = request.get_json(silent=True) or {}
    license_key = data.get('license_key') or ''
    ok, payload = activate_license(current_user, license_key)
    if not ok:
        return jsonify(payload), 400
    return jsonify(payload)


@billing.route('/api/version/check')
@login_required
def version_check():
    """Lightweight remote version check for Super Admins (admins only).

    Pulls from a remote version JSON file (VERSION_CHECK_URL). When the remote
    version is newer than the deployed one, the dashboard shows the update
    banner: 'New version available. Run docker compose pull && docker compose up -d to update.'
    """
    if current_user.role != 'admin':
        return jsonify({"error": "Admin access required."}), 403
    import urllib.request

    current_version = os.getenv('APP_VERSION') or '1.0.0'
    remote_url = os.getenv('VERSION_CHECK_URL') or ''
    result = {
        "current_version": current_version,
        "update_available": False,
        "latest_version": current_version,
        "message": None,
    }
    if not remote_url:
        result["error"] = "VERSION_CHECK_URL not configured."
        return jsonify(result)

    try:
        req = urllib.request.Request(remote_url, headers={'User-Agent': 'DataConvo/SelfHosted'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        latest = str(data.get('version') or data.get('latest_version') or current_version)
        result["latest_version"] = latest

        def _parts(v):
            return [int(x) for x in str(v).strip().lstrip('v').split('.')]
        try:
            if _parts(latest) > _parts(current_version):
                result["update_available"] = True
                result["message"] = (
                    "🚀 New version available. Run "
                    "`docker compose pull && docker compose up -d` to update."
                )
        except Exception:
            if latest != current_version:
                result["update_available"] = True
                result["message"] = (
                    "🚀 New version available. Run "
                    "`docker compose pull && docker compose up -d` to update."
                )
    except Exception as e:
        result["error"] = f"Version check failed: {type(e).__name__}"
    return jsonify(result)


@billing.route('/checkout/pro', methods=['POST'])
@login_required
def checkout_pro():
    if not USES_STRIPE:
        flash('Stripe is not configured yet. Please set STRIPE_SECRET_KEY.', 'error')
        return redirect(url_for('billing.pricing'))

    stripe.api_key = STRIPE_SECRET_KEY
    try:
        customer_id = current_user.stripe_customer_id
        if not customer_id:
            customer = stripe.Customer.create(
                email=current_user.email,
                name=current_user.username or current_user.email,
                metadata={'user_id': str(current_user.id)}
            )
            customer_id = customer.id
            current_user.stripe_customer_id = customer_id
            db.session.commit()

        success_url = request.host_url.rstrip('/') + url_for('billing.checkout_success')
        cancel_url = request.host_url.rstrip('/') + url_for('billing.checkout_cancel')

        checkout_session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=['card'],
            line_items=[{
                'price': PRO_PRICE_ID,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={'user_id': str(current_user.id)}
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        current_app.logger.error(f"Stripe checkout failed: {e}")
        flash('Could not start checkout. Please try again.', 'error')
        return redirect(url_for('billing.pricing'))


@billing.route('/checkout/success')
@login_required
def checkout_success():
    flash('Payment successful! Your Pro account is now active.', 'success')
    return redirect(url_for('billing.pricing'))


@billing.route('/checkout/cancel')
@login_required
def checkout_cancel():
    flash('Checkout was cancelled. You can upgrade anytime.', 'info')
    return redirect(url_for('billing.pricing'))


@billing.route('/webhook/stripe', methods=['POST'])
def stripe_webhook():
    """Handle Stripe checkout.session.completed events to upgrade the user."""
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    if not STRIPE_WEBHOOK_SECRET:
        return jsonify({'error': 'Webhook secret not configured'}), 400

    try:
        stripe.api_key = STRIPE_SECRET_KEY
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({'error': 'Invalid signature'}), 400

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        customer_id = session.get('customer')
        user_id = session.get('metadata', {}).get('user_id')

        user = None
        if user_id:
            user = User.query.get(int(user_id))
        if not user and customer_id:
            user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if not user:
            return jsonify({'error': 'User not found'}), 404

        user.subscription_tier = 'pro'
        if customer_id:
            user.stripe_customer_id = customer_id
        db.session.commit()
        current_app.logger.info(f"User {user.id} upgraded to pro via webhook")

    return jsonify({'status': 'ok'}), 200