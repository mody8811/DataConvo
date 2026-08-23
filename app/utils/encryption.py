"""Secure Fernet encryption vault for BYOK provider credentials.

Uses cryptography.fernet with a persistent app-level key derived from
SECRET_KEY so stored provider keys can be decrypted on future requests.
Never logs / returns plain-text keys.
"""
import os
import base64
import hashlib

from cryptography.fernet import Fernet

_fernet = None


def _get_fernet():
    """Lazy-build a Fernet instance keyed off the Flask application secret.

    The key is a stable base64-urlsafe SHA256 of the app secret so it
    survives restarts without a separate key-management file.

    ⚠️ MUST match the SAME secret source the Flask app uses. The app honors
    `FLASK_SECRET_KEY` first and falls back to legacy `SECRET_KEY`; if this
    helper derived a different key, UI-saved BYOK keys could not be decrypted
    at query time (decrypt_secret returns None) and the OpenAI SDK would send
    requests with NO Authorization header — a 401 Missing Authentication Header.
    """
    global _fernet
    if _fernet is None:
        secret = os.getenv('FLASK_SECRET_KEY') or os.getenv('SECRET_KEY') or 'data-convo-default-insecure-secret-change-me'
        digest = hashlib.sha256(secret.encode('utf-8')).digest()
        key = base64.urlsafe_b64encode(digest)
        _fernet = Fernet(key)
    return _fernet


def encrypt_secret(plaintext):
    """Encrypt a plain-text API key. Returns encrypted string or None."""
    if not plaintext:
        return None
    try:
        return _get_fernet().encrypt(plaintext.encode('utf-8')).decode('ascii')
    except Exception:
        return None


def decrypt_secret(ciphertext):
    """Decrypt a stored encrypted API key. Returns plain-text or None.

    Callers must never log / expose the returned value.
    """
    if not ciphertext:
        return None
    try:
        return _get_fernet().decrypt(ciphertext.encode('ascii')).decode('utf-8')
    except Exception:
        return None


def mask_key(key):
    """Return a safe preview of a key (sk-XXXX...XXXX) for display."""
    if not key:
        return ''
    if len(key) <= 8:
        return '****'
    return key[:4] + '...' + key[-4:]