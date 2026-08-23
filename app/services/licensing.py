"""Self-hosted license activation + tier enforcement for Data Convo.

Tiers:
    community  ($0)      : 1 Admin + 1 Member, 100% features, BYOK required.
    team       ($399/mo) : up to 15 team members, advanced RBAC/audit/workspace mgmt.
    enterprise (custom)  : unlimited seats, SLA/SSO/VPC deployment support.

The admin activates a tier by entering a License Key in Account →
"License & Subscription". Keys are validated locally with an HMAC signature
(no phone-home required) so the platform works fully offline on the
client's own Docker infrastructure.

License key format (HMAC-SHA256):
    DCCONVO-<BASE32(payload)>-<HEX_SIGNATURE>

    payload = <tier>.<admin_email>.<issued_at_unix>
    signature = HMAC-SHA256(LICENSE_SIGNING_SECRET, payload) hex digest
"""
import base64
import hashlib
import hmac
import os
import time
import uuid

from app import db

PLAN_DEFS = {
    "community": {
        "name": "Community",
        "price_display": "$0",
        "price_period": "Free Forever",
        "seat_limit": 1,          # member seats (1 admin + this many members)
        "seats_text": "1 Admin / 1 Member",
        "features": [
            "100% features unlocked (Full Chat-to-SQL & Anomaly Detection Studio)",
            "Mandatory BYOK (OpenAI / Anthropic / OpenRouter)",
            "No fallback to platform API keys",
        ],
        "cta": "Get Started",
    },
    "team": {
        "name": "Team / Pro",
        "price_display": "$399",
        "price_period": "/ month",
        "seat_limit": 15,         # member seats
        "seats_text": "Up to 15 Team Members",
        "features": [
            "Advanced column-level RBAC",
            "Audit logs",
            "Multi-user workspace management",
        ],
        "cta": "Contact Us",
    },
    "enterprise": {
        "name": "Enterprise",
        "price_display": "Custom",
        "price_period": "Starts at $3,500 / year",
        "seat_limit": None,       # unlimited
        "seats_text": "Unlimited seats",
        "features": [
            "Dedicated SLA support",
            "Custom SSO / SAML",
            "White-glove VPC deployment assistance",
        ],
        "cta": "Talk to Sales",
    },
}


def _secret():
    """License signing secret — from env so the same key works across restarts."""
    return (os.getenv("LICENSE_SIGNING_SECRET") or "dataconvo-selfhosted-default-secret").encode("utf-8")


def normalize_tier(tier):
    """Map legacy tiers -> canonical plan key."""
    if not tier:
        return "community"
    t = str(tier).strip().lower()
    if t in ("pro", "team"):
        return "team"
    if t in ("enterprise", "custom"):
        return "enterprise"
    return "community"


def get_seat_limit(tier):
    """Return the number of MEMBER seats allowed for a tier (None = unlimited)."""
    plan = PLAN_DEFS.get(normalize_tier(tier))
    return plan["seat_limit"] if plan else 1


def get_plan(tier):
    """Return the canonical plan dict for a tier."""
    return PLAN_DEFS.get(normalize_tier(tier), PLAN_DEFS["community"])


def activate_license(admin_user, license_key):
    """Validate a license key, then persist tier + key on the admin user.

    Returns (ok: bool, payload: dict) where payload contains the tier info
    or an error message.
    """
    key = (license_key or "").strip()
    if not key:
        return False, {"error": "A license key is required."}

    tier, email, issued = _verify_key(key)
    if not tier:
        return False, {"error": "Invalid or expired license key."}

    if email and email != (getattr(admin_user, "email", "") or "").strip().lower():
        return False, {"error": "This license key is bound to a different admin email."}

    admin_user.subscription_tier = tier
    admin_user.license_key = key
    db.session.commit()

    plan = get_plan(tier)
    return True, {
        "success": True,
        "tier": tier,
        "plan_name": plan["name"],
        "seat_limit": plan["seat_limit"],
        "seats_text": plan["seats_text"],
    }


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _verify_key(key: str):
    """Verify a DCCONVO-... key.

    Returns (tier, admin_email, issued_at) or (None, None, None).
    Rejects tampered keys (bad HMAC), keys issued for the WRONG
    LICENSE_SIGNING_SECRET (different signing domain), and expired keys
    (expires_at in the past).
    """
    try:
        body = key.strip()
        if not body.startswith("DCCONVO-"):
            # Legacy fallback: bare key format DCCONVO-<tier>-<uuid>
            parts = body.split("-")
            if len(parts) >= 2 and parts[0] == "DCCONVO" and parts[1] in PLAN_DEFS:
                return normalize_tier(parts[1]), None, None
            return None, None, None
        _, payload_b64, signature = body.split("-", 2)
        payload_bytes = base64.b32decode(payload_b64 + "=" * ((8 - len(payload_b64) % 8) % 8))
        payload = payload_bytes.decode("utf-8")
        expected = _sign(payload)
        if not hmac.compare_digest(expected, signature):
            return None, None, None

        parts = payload.split("|")
        tier = parts[0]
        email = parts[1].lower() if len(parts) > 1 else ""
        issued = float(parts[2]) if len(parts) > 2 else None
        expires_at = float(parts[4]) if len(parts) > 4 else None

        # Signing-domain drift: key minted with a DIFFERENT LICENSE_SIGNING_SECRET
        # client-side (e.g. customer's own docker .env) will fail HMAC above; this
        # guard only matters for legacy 3-field keys that have no expiry domain.
        if expires_at is not None and time.time() > expires_at:
            return None, None, None

        # Optional legacy 10-year safety net for 3-field keys (no expires_at field).
        if expires_at is None and issued is not None and issued < time.time() - 10 * 365 * 24 * 3600:
            return None, None, None

        return normalize_tier(tier), email, issued
    except Exception:
        return None, None, None


def generate_trial_key(tier="community", email=None, months=None):
    """Generate a signed license key with an optional expiry.

    months=None  -> non-expiring (default behaviour).
    months>0     -> expires `months` calendar months from issuance.

    Used by the vendor CLI (generate_key.py) and local dev/tests.
    """
    from datetime import datetime as _dt, timedelta as _td

    issued = int(time.time())
    expires_at = None
    if months:
        # Calendar-month expiry: advances the calendar month by `months`.
        base = _dt.utcfromtimestamp(issued)
        y = base.year + ((base.month - 1 + months) // 12)
        m = ((base.month - 1 + months) % 12) + 1
        d = min(base.day, [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28,
                           31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
        expires_at = int(_dt(y, m, d, base.hour, base.minute, base.second).timestamp())

    payload = f"{normalize_tier(tier)}|{((email or 'admin@dataconvo.app').lower())}|{issued}||{expires_at or ''}"
    payload_b64 = base64.b32encode(payload.encode("utf-8")).decode("utf-8").rstrip("=")
    sig = _sign(payload)
    return f"DCCONVO-{payload_b64}-{sig}"


def workspace_seat_usage(admin_user):
    """Return (member_count, pending_count, seat_limit) for the admin's workspace."""
    from app.models import TeamInvite, Workspace, User

    limit = get_seat_limit(getattr(admin_user, "subscription_tier", "community") or "community")
    workspace_id = getattr(admin_user, "workspace_id", None)
    members = []
    if workspace_id:
        workspace = Workspace.query.get(workspace_id)
        if workspace:
            members = [m for m in workspace.members if m.role == "member"]
    member_count = len(members)
    pending = 0
    if workspace_id:
        pending = TeamInvite.query.filter_by(workspace_id=workspace_id, status="pending").count()
    return member_count, pending, limit