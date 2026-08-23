#!/usr/bin/env python3
"""Data Convo — License Key Generator (vendor / internal CLI).

Generates a signed HMAC-SHA256 license key bound to a customer's admin email,
optionally with a calendar-month expiry. Use this to instantly issue keys for
paying customers and paste them into the "License & Subscription" activation
tab (Account → 🔑 License & Subscription).

Usage:
    python generate_key.py --email client@company.com --tier team --months 12
    python generate_key.py --email admin@acme.io --tier enterprise            # no expiry
    python generate_key.py --tier community --email dev@localhost --months 6

The LICENSE_SIGNING_SECRET is read from .env (falling back to the built-in
default) so the generated key verifies against the production deployment.
"""
import argparse
import base64
import os
import sys
from datetime import datetime, timezone

# Force UTF-8 output so the emoji + box-drawing UI never crashes on legacy
# terminal code pages (cp1252 etc.). Python 3.7+ supports stdout.reconfigure.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover - non-UTF8 fallback handled in _box
    pass

# Ensure `.env` is loaded so LICENSE_SIGNING_SECRET matches the deployment.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional at import time
    pass

from app.services.licensing import (
    generate_trial_key,
    get_plan,
    normalize_tier,
    _verify_key,
)


TIERS = ("community", "team", "enterprise")


def _supports_unicode():
    """True when stdout can safely print box-drawing characters."""
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    return enc in ("utf-8", "utf8", "utf-16", "utf-16-le", "utf-16-be", "cp65001")


def _box(lines):
    """Render a fixed-width copy-paste friendly box.

    Uses Unicode box-drawing when the terminal supports it, otherwise falls
    back to plain ASCII so the key can always be piped/copied anywhere.
    """
    if _supports_unicode():
        tl, tr, bl, br, hz, vt = "┌", "┐", "└", "┘", "─", "│"
    else:
        tl, tr, bl, br, hz, vt = "+", "+", "+", "+", "-", "|"
    width = max(len(l) for l in lines) + 4
    top = tl + hz * (width - 2) + tr
    bottom = bl + hz * (width - 2) + br
    out = [top]
    for l in lines:
        out.append(vt + " " + l.ljust(width - 4) + " " + vt)
    out.append(bottom)
    return "\n".join(out)


def _key_expiry_text(key, months):
    """Decode exact expiry embedded in the key for an accurate display."""
    expiry_text = "Never"
    if not months:
        return expiry_text
    try:
        payload_b64 = key.split("-", 2)[1]
        payload_bytes = base64.b32decode(payload_b64 + "=" * ((8 - len(payload_b64) % 8) % 8))
        payload = payload_bytes.decode("utf-8")
        parts = payload.split("|")
        if len(parts) > 4 and parts[4]:
            expiry_text = datetime.fromtimestamp(float(parts[4]), tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            expiry_text = "in %d month(s)" % months
    except Exception:
        expiry_text = "in %d month(s)" % months
    return expiry_text


def main():
    parser = argparse.ArgumentParser(
        description="Generate a Data Convo HMAC-SHA256 signed license key.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Customer's admin email the license is bound to (must match their login).",
    )
    parser.add_argument(
        "--tier",
        choices=TIERS,
        default="team",
        help="License tier: community (1 member), team (15 members), enterprise (unlimited).",
    )
    parser.add_argument(
        "--months",
        type=int,
        default=None,
        help="Optional licence duration in calendar months. Omit for a non-expiring key.",
    )
    args = parser.parse_args()

    email = (args.email or "").strip().lower()
    if not email or "@" not in email:
        sys.exit("ERROR: --email must be a valid email address.")

    months = args.months
    if months is not None and months <= 0:
        sys.exit("ERROR: --months must be a positive integer (or omit it for no expiry).")

    tier = normalize_tier(args.tier)
    plan = get_plan(tier)

    key = generate_trial_key(tier=tier, email=email, months=months)

    # Self-test: verify the generated key round-trips under the same secret.
    verified_tier, verified_email, issued_at = _verify_key(key)
    if verified_tier != tier:
        sys.exit("ERROR: generated key failed local verification — inconsistent LICENSE_SIGNING_SECRET.")

    issued = datetime.fromtimestamp(int(issued_at or 0), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    expiry_text = _key_expiry_text(key, months)

    summary = _box([
        "Summary",
        "  Tier    : " + plan["name"],
        "  Seats   : " + plan["seats_text"],
        "  Email   : " + email + "  (bound to this admin)",
        "  Issued  : " + issued,
        "  Expires : " + expiry_text,
        "  Signing : HMAC-SHA256 · LICENSE_SIGNING_SECRET",
    ])

    print("\n✅ DATA CONVO · LICENSE KEY GENERATED\n")
    print(_box([
        "LICENSE KEY",
        "",
        key,
    ]))
    print()
    print(summary)
    print()
    print("Activation: the customer pastes this key into")
    print("  Account → 🔑 License & Subscription → 'Activate License'.")
    print()


if __name__ == "__main__":
    main()