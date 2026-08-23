"""BYOK-aware LLM client router.

Supports a universal, Cline-style multi-provider configuration:
  - openai      : standard ChatOpenAI (default)
  - anthropic   : ChatAnthropic with the user's model ID + API key
  - openrouter  : ChatOpenAI pointed at https://openrouter.ai/api/v1
  - gemini      : ChatGoogleGenerativeAI with the user's model ID + API key
  - custom      : ChatOpenAI pointed at any OpenAI-compatible base URL

When a workspace admin saves BYOK credentials via the UI (Account →
Security → BYOK), the key is stored Fernet-encrypted in the DATABASE — NOT
in process environment variables. build_llm_client() therefore ALWAYS
decrypts the stored key and passes it explicitly as api_key=... to the SDK
so the OpenAI client sends the Authorization header (fixes
401 Missing Authentication Header when env vars are absent).
"""
import os
import logging

from app.utils.encryption import decrypt_secret

logger = logging.getLogger(__name__)

DEFAULT_MODEL = 'gpt-4o-mini'

# Default base URLs per provider (used when the user doesn't supply one)
DEFAULT_BASE_URLS = {
    'openrouter': 'https://openrouter.ai/api/v1',
    'custom': '',
}

# Provider alias -> canonical provider name
PROVIDER_ALIASES = {
    'openai': 'openai',
    'anthropic': 'anthropic',
    'openrouter': 'openrouter',
    'gemini': 'gemini',
    'google': 'gemini',
    'custom': 'custom',
}

# Default model per provider when the user leaves model ID blank
DEFAULT_MODELS = {
    'openai': 'gpt-4o-mini',
    'anthropic': 'claude-3-5-sonnet-20241022',
    'openrouter': 'deepseek/deepseek-chat',
    'gemini': 'gemini-2.0-flash',
    'custom': 'gpt-4o-mini',
}


def get_workspace_admin(user):
    """Return the workspace admin whose BYOK config a member inherits.

    Admins return themselves. Members resolve through `Workspace.owner_id`
    so a single admin-configured key + provider serves the whole workspace.
    """
    if not user:
        return None
    if getattr(user, 'role', 'member') == 'admin':
        return user
    workspace_id = getattr(user, 'workspace_id', None)
    if not workspace_id:
        return user
    try:
        from app.models import User, Workspace
        workspace = Workspace.query.get(workspace_id)
        if workspace and workspace.owner_id:
            admin = User.query.get(workspace.owner_id)
            if admin is not None:
                return admin
    except Exception as e:
        logger.warning('Workspace-admin resolution failed for user %s: %s', getattr(user, 'id', '?'), e)
    return user


def resolve_effective_user(user):
    """Return the user whose BYOK/provider config should be used.

    Admins use their own config. Members inherit the workspace admin's
    config whenever the admin has a STORED provider key (the authoritative
    BYOK signal — NOT the `byok_enabled` boolean, which can be out of sync
    on legacy rows / partial saves). Without this, a member would be sent
    to the SDK keyless → OpenAI 401 Missing Authentication Header.
    """
    if not user:
        return None
    resolved = get_workspace_admin(user)
    if resolved is not user:
        has_key = bool(
            getattr(resolved, 'openai_api_key_encrypted', None)
            or getattr(resolved, 'anthropic_api_key_encrypted', None)
        )
        if has_key:
            return resolved
    return user


def workspace_byok_enabled(user):
    """True when this user (admin or member) effectively has BYOK active."""
    if not user:
        return False
    if getattr(user, 'byok_enabled', False):
        return True
    resolved = get_workspace_admin(user)
    if resolved is not user:
        return bool(getattr(resolved, 'byok_enabled', False))
    return False


def get_byok_state(user):
    """Return (enabled, active_provider, has_openai_key, has_anthropic_key).

    Members reflect their workspace admin's inherited BYOK state so the
    status endpoint shows exactly what they leverage at query time.
    """
    if not user:
        return False, 'openai', False, False
    resolved = resolve_effective_user(user)
    if resolved is None:
        return False, 'openai', False, False
    return (
        bool(getattr(resolved, 'byok_enabled', False)),
        getattr(resolved, 'active_llm_provider', 'openai') or 'openai',
        bool(getattr(resolved, 'openai_api_key_encrypted', None)),
        bool(getattr(resolved, 'anthropic_api_key_encrypted', None)),
    )


def get_user_provider(user):
    """Return the canonical provider name from the user's saved config."""
    if not user:
        return 'openai'
    raw = getattr(user, 'llm_provider', None) or getattr(user, 'active_llm_provider', None) or 'openai'
    return PROVIDER_ALIASES.get(str(raw).strip().lower(), 'openai')


def get_user_model(user, fallback=None):
    """Return the user's desired model ID (or a provider default)."""
    model = getattr(user, 'llm_model_id', None)
    if model and str(model).strip():
        return str(model).strip()
    if fallback:
        return fallback
    provider = get_user_provider(user)
    return DEFAULT_MODELS.get(provider, DEFAULT_MODEL)


def get_user_base_url(user):
    """Return the user's configured base URL (may be empty)."""
    url = getattr(user, 'llm_base_url', None)
    if url and str(url).strip():
        return str(url).strip()
    provider = get_user_provider(user)
    return DEFAULT_BASE_URLS.get(provider, '')


def get_stored_key_attr(user, provider):
    """Return the attribute name holding the encrypted key for a provider."""
    if provider == 'anthropic':
        return 'anthropic_api_key_encrypted'
    return 'openai_api_key_encrypted'


def get_stored_key_ciphertext(user, provider=None):
    """Return the RAW stored encrypted key cell (or None) for a user/provider.

    Members automatically inherit the workspace admin's stored cell.
    Used to distinguish 'no key configured' from 'key present but failed to
    decrypt' — the latter must never produce a keyless client a 401.
    """
    effective = resolve_effective_user(user)
    if effective is None:
        return None
    provider = provider or get_user_provider(effective)
    return getattr(effective, get_stored_key_attr(effective, provider), None)


def resolve_api_key(user, provider=None):
    """Return the decrypted BYOK provider key, or None if no key is stored.

    Members automatically inherit the workspace admin's decrypted key
    (admin's provider config is used at query time). Callers must NEVER
    log or persist the returned plaintext key.

    IMPORTANT: we return the stored key whenever an encrypted row exists,
    WITHOUT gating on the `byok_enabled` boolean. The flag can be out of
    sync (legacy rows / partial save), and a 401 Missing Authentication
    Header occurs when the SDK is constructed keyless — so a stored key is
    the authoritative signal that BYOK is configured.
    """
    effective = resolve_effective_user(user)
    if effective is None:
        return None
    provider = provider or get_user_provider(effective)
    enc = getattr(effective, get_stored_key_attr(effective, provider), None)
    if not enc:
        return None
    try:
        return decrypt_secret(enc)
    except Exception as e:  # never leak decryption failures to the caller
        logger.warning('BYOK key decryption failed for user %s: %s', getattr(effective, 'id', '?'), type(e).__name__)
        return None


class BYOKRequiredError(RuntimeError):
    """Raised when a query is attempted without a valid BYOK configuration.

    The self-hosted Community/Team/Enterprise model has ZERO platform API key
    fallback: every agent call must be powered by the workspace admin's own
    LLM provider key (OpenAI / Anthropic / OpenRouter / Gemini / custom).
    """


def byok_required(user):
    """Return True when the platform enforces BYOK-only (no sponsored fallback).

    Enforcement is always ON for self-hosted deployments (default). Setting
    DISABLE_BYOK_ENFORCEMENT=1 re-enables the legacy sponsored fallback for
    demo/dev environments only.
    """
    if os.getenv('DISABLE_BYOK_ENFORCEMENT', '').strip().lower() in ('1', 'true', 'yes', 'on'):
        return False
    return True


# Canonical API-key env var per provider. LangChain/SDK wrappers
# (ChatOpenAI, ChatAnthropic, ChatGoogleGenerativeAI) read these during
# construction/auth, so injecting them on-demand from the DB makes every
# provider authenticate cleanly inside Docker.
PROVIDER_ENV_KEY = {
    'openai': 'OPENAI_API_KEY',
    'anthropic': 'ANTHROPIC_API_KEY',
    'openrouter': 'OPENROUTER_API_KEY',
    'gemini': 'GOOGLE_API_KEY',
    'custom': 'OPENAI_API_KEY',
}


def _env_api_key_for(provider):
    """Return the current value of the provider's canonical *_API_KEY env var."""
    env_name = PROVIDER_ENV_KEY.get(provider or 'openai', 'OPENAI_API_KEY')
    return os.getenv(env_name)


def _inject_env_api_key(provider, key):
    """Set the provider's canonical *_API_KEY env var to `key` (if provided).

    This is the universal BYOK fallback: the decrypted DB key is pushed into
    the process environment right before the LangChain wrapper is built, so
    all SDKs (OpenAI / Anthropic / Gemini / OpenRouter) pick it up natively,
    even when the Docker container has no system-level .env key.
    """
    if not key:
        return
    env_name = PROVIDER_ENV_KEY.get(provider or 'openai', 'OPENAI_API_KEY')
    # Also alias Gemini to GEMINI_API_KEY for backends that check either var.
    os.environ[env_name] = key
    if provider == 'gemini':
        os.environ['GEMINI_API_KEY'] = key


def build_llm_client(user=None, model=None, temperature=0.3):
    """Build the correct LangChain client based on the user's saved LLM config.

    Supports: openai, anthropic, openrouter, gemini, custom (OpenAI-compatible).
    Members automatically inherit the workspace admin's provider/model/key.

    401-FIX: when a decrypted DB-stored key exists it is ALWAYS passed to the
    SDK as api_key=... — never omitted in favour of a global env var — so the
    generated client carries the Authorization header. UI-saved keys live in
    the DB metadata, not in process env vars.

    BYOK-ONLY ENFORCEMENT: no platform API key fallback. If BYOK is not
    configured (no stored key for the effective provider), this raises
    BYOKRequiredError so queries never execute on a managed/sponsored key.
    """
    # Workspace BYOK inheritance: members use the admin's provider config
    effective = resolve_effective_user(user)
    provider = get_user_provider(effective)
    model = get_user_model(effective, fallback=model)
    base_url = get_user_base_url(effective)
    key = resolve_api_key(effective, provider)

    if not key and byok_required(user):
        raise BYOKRequiredError(
            "BYOK required: connect your own OpenAI / Anthropic / OpenRouter API key "
            "in Account → Security before running queries."
        )

    # UNIVERSAL BYOK ENV FALLBACK: the encrypted key lives in the DB, not in
    # process env (Docker). ChatAnthropic, ChatGoogleGenerativeAI and
    # ChatOpenAI all read their canonical `*_API_KEY` env var during
    # construction OR authentication. Inject the decrypted key on-demand into
    # the matching env var right before building the wrapper, so every provider
    # authenticates cleanly without a system-level .env edit.
    key = key or _env_api_key_for(provider)  # explicit opt-out path may still use env
    _inject_env_api_key(provider, key)

    if provider == 'anthropic':
        from langchain_anthropic import ChatAnthropic
        kwargs = {'model': model, 'temperature': temperature}
        if key:
            kwargs['api_key'] = key
        elif not byok_required(user):
            env_key = os.getenv('ANTHROPIC_API_KEY')
            if env_key:
                kwargs['api_key'] = env_key
        return ChatAnthropic(**kwargs)

    if provider == 'gemini':
        from langchain_google_genai import ChatGoogleGenerativeAI
        kwargs = {'model': model, 'temperature': temperature}
        if key:
            kwargs['api_key'] = key
        elif not byok_required(user):
            env_key = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
            if env_key:
                kwargs['api_key'] = env_key
        return ChatGoogleGenerativeAI(**kwargs)

    # openai / openrouter / custom -> ChatOpenAI family
    # NATIVE-OPENAI URL FIX: an OpenAI API key MUST talk directly to OpenAI's
    # native endpoint. If the user previously used OpenRouter (llm_base_url
    # still holds 'https://openrouter.ai/api/v1' in the DB) and switched to
    # provider="openai", we must NEVER send that stale gateway URL — OpenRouter
    # rejects OpenAI keys. Force the native OpenAI URL unless a custom
    # non-router OpenAI-compatible endpoint was explicitly configured.
    from langchain_openai import ChatOpenAI

    native_openai_url = 'https://api.openai.com/v1'

    if provider == 'openai':
        if not base_url or 'openrouter' in (base_url or '').lower():
            base_url = native_openai_url

    kwargs = {'model': model, 'temperature': temperature}
    if key:
        kwargs['api_key'] = key
        if base_url:
            kwargs['base_url'] = base_url
        # ⚠️ force the Authorization header regardless of SDK/env resolution
        kwargs['default_headers'] = {'Authorization': 'Bearer ' + key}
    elif not byok_required(user):
        # Explicit opt-out (DISABLE_BYOK_ENFORCEMENT=1) — env fallback allowed.
        if provider == 'openrouter' or (base_url and 'openrouter' in base_url):
            env_key = os.getenv('OPENROUTER_API_KEY')
            if env_key:
                kwargs['api_key'] = env_key
                kwargs['default_headers'] = {'Authorization': 'Bearer ' + env_key}
            kwargs['base_url'] = base_url or 'https://openrouter.ai/api/v1'
        else:
            env_key = os.getenv('OPENAI_API_KEY')
            if env_key:
                kwargs['api_key'] = env_key
                kwargs['default_headers'] = {'Authorization': 'Bearer ' + env_key}
            if base_url:
                kwargs['base_url'] = base_url
    return ChatOpenAI(**kwargs)


def build_chat_openai(user=None, model=DEFAULT_MODEL, temperature=0.3):
    """Backward-compatible wrapper — delegates to build_llm_client.

    Existing callers (SQLChainService, InternetBot, DeepBot) keep working even
    when the user selects a non-OpenAI provider, because this returns the
    correct provider-specific LangChain client.

    401-FIX safety net: when BYOK enforcement is active, we never allow a
    keyless ChatOpenAI to be returned — that is exactly what sends the SDK
    request without an Authorization header.
    """
    client = build_llm_client(user=user, model=model, temperature=temperature)
    if (client is not None
            and hasattr(client, 'openai_api_key')
            and not getattr(client, 'openai_api_key', None)
            and not os.getenv('DISABLE_BYOK_ENFORCEMENT', '')):
        raise BYOKRequiredError(
            "BYOK key could not be resolved for the OpenAI client. "
            "Re-save your API key in Account → Security → BYOK."
        )
    return client


def build_anthropic(user=None, model='claude-3-5-sonnet-20241022', temperature=0.3):
    """Backward-compatible wrapper — delegates to the universal router."""
    return build_llm_client(user=user, model=model, temperature=temperature)


def test_openai_key(api_key, base_url=None):
    """Lightweight validation: list models with the provided key.

    Returns (ok: bool, message: str). Does not expose the key.
    Supports optional base_url for OpenRouter/custom-compatible endpoints.
    """
    try:
        from openai import OpenAI
        kwargs = {'api_key': api_key, 'timeout': 10}
        if base_url:
            kwargs['base_url'] = base_url
        client = OpenAI(**kwargs)
        client.models.list()
        return True, 'Key is valid — models accessible.'
    except Exception as e:
        return False, f'Key validation failed: {type(e).__name__}'


def test_anthropic_key(api_key):
    """Lightweight validation: minimal completion with the Anthropic key."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key, timeout=10)
        client.messages.create(
            model='claude-3-5-haiku-20241022',
            max_tokens=1,
            messages=[{'role': 'user', 'content': 'hi'}],
        )
        return True, 'Key is valid — model responded.'
    except Exception as e:
        return False, f'Key validation failed: {type(e).__name__}'


def test_gemini_key(api_key):
    """Lightweight validation: minimal generation call with the Gemini key."""
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            model='gemini-2.0-flash',
            api_key=api_key,
            max_tokens=1,
        )
        llm.invoke('hi')
        return True, 'Key is valid - model responded.'
    except Exception as e:
        return False, f'Key validation failed: {type(e).__name__}'


def test_provider_key(provider, api_key, base_url=None):
    """Dispatch to the correct provider-specific key validator."""
    provider = PROVIDER_ALIASES.get(str(provider or '').strip().lower(), 'openai')
    if provider == 'anthropic':
        return test_anthropic_key(api_key)
    if provider == 'gemini':
        return test_gemini_key(api_key)
    return test_openai_key(api_key, base_url=base_url)