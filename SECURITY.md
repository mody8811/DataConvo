# Security: dependency policy and known outstanding advisories

Last audited: **10 November 2026**

This document records how dependency vulnerabilities are tracked, what was fixed,
and what remains open with a deliberate rationale. It is generated from the OSV
database (which aggregates GitHub Security Advisories, PyPA advisories and the
NVD) — the same upstream feed Dependabot reads.

## How to re-run the audit

There is no `gh` CLI or `pip-audit` in the container, so the audit queries OSV
directly and needs no credentials:

```bash
python _osv_audit.py        # full report, writes _osv_report.json
python _resolve_check.py    # confirms requirements.txt still resolves
```

`_osv_audit.py` and `_resolve_check.py` are root-level scratch scripts, ignored
by `/_*` in `.gitignore`. Re-create them from the audit notes if they are gone.

## Result of the 2026-11-10 remediation

| | Before | After |
|---|---|---|
| CRITICAL | 3 | **0** |
| HIGH | 23 | **1** |
| MEDIUM / LOW | 78 | **11** |
| **Total advisories** | **104** | **12** |

The single remaining HIGH and all 11 MEDIUM/LOW entries are LangChain, and every
one of them is only fixed in the 1.x major line. No non-LangChain advisory
remains. `requirements.txt` resolves cleanly: `pip install --dry-run` reports
`RESOLVED OK` with 0 compatibility problems.

## What changed

Nine packages were raised to versions that close **3 critical** and **22 high**
advisories. Every one is an in-line upgrade with no API change on any code path
this application uses, except `supabase`, which needed a major-line bump (see the
conflict note below).

| Package | From | To | Advisories closed |
|---|---|---|---|
| `PyJWT` | 2.13.0 | **2.15.1** | GHSA-ffc3-869f-jxw9 (**CRITICAL**) + 12 others |
| `PyMySQL` | 1.1.0 | **>=1.1.1** | GHSA-v9hf-5j83-6xpp (**CRITICAL**, SQL injection) |
| `authlib` | 1.7.2 | **1.9.5** | GHSA-6v38-4h7w-rj9h (**CRITICAL**, empty JWS signatures) + 7 others |
| `cryptography` | 46.0.5 | **50.0.2** | GHSA-537c-gmf6-5ccf, GHSA-jwv3-5hgf-82ww, GHSA-g6cj-pr64-35w5 |
| `sqlparse` | 0.4.4 | **>=0.6.1** | GHSA-f2ff-p2ww-7p4p, GHSA-prg7-hcfm-mfcr, GHSA-pwgv-4x5q-6m9f |
| `snowflake-sqlalchemy` | 1.5.0 | **>=1.11.0** | GHSA-8g6f-qw9x-4q6q (SQL injection) |
| `requests` | 2.32.4 | **2.34.2** | PYSEC-2026-2275 |
| `Werkzeug` | 3.1.5 | **3.1.9** | PYSEC-2026-2320 |
| `langchain`, `langchain-core`, `langchain-text-splitters`, `langsmith` | 0.3.0 / 0.3.81 / 0.3.0 / 0.1.147 | **0.3.30 / 0.3.85 / 0.3.9 / 0.8.18** | GHSA-3644-q5cj-c5c7, GHSA-pjwx-r37v-7724, GHSA-m42m-m8cr-8m58, GHSA-f4xh-w4cj-qxq8 |

### The supabase bump was forced, not optional

Raising `langsmith` to 0.8.18 pulls `httpx2>=2`, but `supabase==2.5.3` pinned
`httpx<1`. The two could not coexist and pip refused to resolve. `supabase` was
raised to **2.34.1**, verified against the patched langsmith:

```
langsmith 0.8.18 + supabase 2.5.3   -> CONFLICT (httpx vs httpx2)
langsmith 0.8.18 + supabase 2.34.1  -> RESOLVES
```

The supabase 2.x client API this app uses (`create_client`,
`client.auth.sign_up`, `client.auth.sign_in_with_password`) is unchanged across
2.x, and `supabase` is import-guarded in `app/routes/auth.py`, so a failure there
degrades to local auth rather than crashing. **Still test signup and login on
staging** — this was the only bump that crossed a major version.

Two notes on the constraints that shaped these choices:

- **`sqlparse` 0.6.x requires Python >= 3.10.** The container is
  `python:3.11-slim`, so this is fine; it would break a 3.9 deployment.
- **`cryptography` 50.x is imported by `app/utils/encryption.py`** (Fernet, used
  for encrypting BYOK provider keys). The Fernet API is unchanged in 50.x, but
  this is the highest-risk bump in the batch — verify BYOK key save/decrypt on
  a staging deploy before promoting.

## Open: LangChain 1.x migration required

Several LangChain advisories are **only fixed in the 1.x major line**. They
cannot be closed by raising a floor inside 0.3.x:

| Advisory | Package | Fixed only in | Issue |
|---|---|---|---|
| GHSA-2g6r-c272-w58r | `langchain-core` | 1.2.11 | SSRF via image_url token counting |
| PYSEC-2026-2193 | `langchain-core` | 1.2.22 | (see OSV) |
| PYSEC-2026-2192 | `langchain` | 1.3.9 | (see OSV) |
| PYSEC-2026-2556 | `langchain-anthropic` | 1.4.6 | path traversal / sandbox escape |
| GHSA-r7w7-9xr2-qq2r | `langchain-openai` | 1.1.14 | image token counting SSRF |

**Why this was not done today:** LangChain 1.x is a breaking release. This
codebase imports `langchain_core.prompts`, `langchain_core.output_parsers`,
`langchain_core.messages`, `langchain_openai.ChatOpenAI`,
`langchain_anthropic.ChatAnthropic`, `langchain_google_genai.ChatGoogleGenerativeAI`
and `langchain_community.utilities.SQLDatabase` across `app/text_to_sql.py`,
`app/agents/llm_router.py`, `app/core/database.py`, `app/services/sql_chain_service.py`,
`app/services/deepbot_service.py` and `app/services/internetbot_service.py`.
Upgrading without a test pass would risk the core chat, SQL-generation and
agent features. It needs its own branch and its own regression run.

**Exposure assessment:** the SSRF advisories require an attacker-controlled
`image_url` to reach the model call. This application passes user *questions*,
not user-supplied image URLs, so the practical exposure is low. That is a
mitigation, not a fix — the upgrade is still owed.

## Standing rules

1. **Every dependency in `requirements.txt` carries a comment naming the fixed
   advisory when the pin exists for a security reason.** Do not lower a floor
   without checking OSV first.
2. **Never commit `.env`.** It is gitignored; `.env.example` is the template.
   The audit script also scans staged diffs for credential patterns.
3. **`SITE_URL` must be the production domain.** `app/services/seo.py` refuses
   localhost values at runtime, because a dev `.env` reaching production would
   write `localhost` into every canonical URL and sitemap entry.
4. **Re-run the audit before each release**, and update the table above rather
   than adding a second one.