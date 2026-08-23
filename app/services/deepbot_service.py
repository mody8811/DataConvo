"""DeepBot — interactive data-science agent for the AI Chat terminal.

Given a pandas DataFrame (from the last query result), DeepBot:
  1. Asks an LLM to generate a clean Python snippet (uses `df`, pandas/numpy/
     sklearn/matplotlib).
  2. Executes the snippet inside a restricted sandbox with pre-loaded
     `pd`, `np`, `sklearn`, and `plt`.
  3. Captures any Matplotlib figure as a base64 PNG, clears the plot buffer,
     and returns a structured JSON payload (explanation + code + image).
  4. Never crashes the server: exceptions are caught and returned cleanly.
"""
import base64
import io
import json
import logging
import re
import traceback

from app.agents.llm_router import build_chat_openai, workspace_byok_enabled

logger = logging.getLogger(__name__)

# Modules pre-loaded into the execution sandbox. Each import is wrapped in its
# own try/except so a missing optional module (matplotlib/sklearn) never blocks
# pandas/numpy analysis. Pandas display overrides are injected automatically so
# wide tables render cleanly (no "..." truncation from terminal-width defaults).
_PRELOAD = (
    "try:\n    import pandas as pd\nexcept Exception:\n    pd = None\n"
    "try:\n    pd.set_option('display.max_columns', None)\n"
    "    pd.set_option('display.width', 1000)\n"
    "    pd.set_option('display.max_rows', 100)\n"
    "    pd.set_option('display.expand_frame_repr', False)\n"
    "except Exception:\n    pass\n"
    "try:\n    import numpy as np\nexcept Exception:\n    np = None\n"
    "try:\n    import matplotlib\n    matplotlib.use('Agg')\n    import matplotlib.pyplot as plt\nexcept Exception:\n    plt = None\n"
    "try:\n    import scipy\nexcept Exception:\n    scipy = None\n"
    "try:\n    import seaborn as sns\nexcept Exception:\n    sns = None\n"
    "try:\n    import statsmodels\nexcept Exception:\n    statsmodels = None\n"
)
# Styled HTML table wrapper injected into the sandbox so any printed DataFrame
# returns a clean, CSS-styled table (subtle borders, padding, alternating row
# tones, proper font-family) instead of default unstyled text.
# NOTE: border-collapse:separate + spacing:0 keeps the grid borders tight
# WITHOUT the collapsed-mode quirk that breaks position:sticky on <th>.
# position:sticky + top:0 locks the table header while scrolling.
_HTML_TABLE_HELPERS = (
    "\n_STYLE_HTML_TABLE = (\n"
    "    '<style>'\n"
    "    'table.dc-df {border-collapse:separate;border-spacing:0;width:100%;margin:8px 0;font-family:Inter,-apple-system,sans-serif;font-size:0.78rem;color:#e2e8f0;}'"
    "    'table.dc-df th {background:#121829;color:#94a3b8;font-weight:600;text-align:left;padding:8px 12px;border-bottom:1px solid #1e293b;position:sticky;top:0;z-index:5;}'"
    "    'table.dc-df td {padding:8px 12px;border-bottom:1px solid rgba(255,255,255,0.04);border-left:1px solid rgba(255,255,255,0.04);}'"
    "    'table.dc-df tbody tr:nth-child(odd) {background:rgba(255,255,255,0.02);}'"
    "    'table.dc-df tbody tr:hover {background:rgba(99,102,241,0.06);}'"
    "    '</style>'\n"
    ")\n"
    "def _df_to_html(o):\n"
    "    try:\n"
    "        from IPython.core.display import HTML  # noqa\n"
    "    except Exception:\n"
    "        pass\n"
    "    try:\n"
    "        html = o.to_html(index=True, classes='dc-df dc-table', border=0)\n"
    "        return _STYLE_HTML_TABLE + html\n"
    "    except Exception:\n"
    "        return str(o)\n"
    "\n"
    "import pandas as _pd\n"
    "if _pd is not None:\n"
    "    _pd.set_option('display.html.table_schema', False)\n"
    "    _pd.set_option('display.html.use_mathjax', False)\n"
)
# Optional scikit-learn (set imports to fail silently if unavailable)
_EXTRA = "try:\n    import sklearn\n    from sklearn import linear_model, cluster, preprocessing, metrics\nexcept Exception:\n    sklearn = None\n"

# Hard cap for any raw micro-preview (format-check only). DeepBot defaults to a
# metadata-first prompt — no raw rows are injected; a ≤2-row preview is only used
# when building the prompt explicitly needs value-format context.
_MAX_MICRO_PREVIEW_ROWS = 2
# Cap the row-scan used to compute the statistical profile (bound runtime/tokens).
_PROFILE_ROWS = 1000

# Recognizers for styled HTML DataFrame blocks emitted by the sandbox.
_DF_HTML_MARKER = '<<<DC_HTML_DF_START>>>'
_DF_HTML_END = '<<<DC_HTML_DF_END>>>'

_SYSTEM_PROMPT = (
    "You are DeepBot, an expert data scientist. Given the pandas DataFrame `df`, "
    "write clean, executable Python code to solve the user's request. "
    "Return ONLY a JSON payload with two keys: "
    "'explanation' (markdown text describing what you did and the findings) and "
    "'code' (a complete executable Python snippet using only columns that exist in df; "
    "you may create plots with matplotlib.pyplot as plt — they will be captured automatically; "
    "you may print() text/statistical tables as normal Python output). "
    "Do not include markdown fences or extra commentary. "
    "Use df.head(20) or df.describe() to inspect the dataset before analysing.\n"
    "IMPORTANT — Metadata-first context (no raw row dumps):\n"
    "Row count: {row_count}\n"
    "Schema (column -> type): {types}\n"
    "Statistical profile (lightweight min/max/mean/null-count over up to 1000 rows):\n{profile}\n"
    "\nSTRICT DEFENSIVE CODING RULES (MANDATORY):\n"
    "1. NEVER call math/aggregations (.mean(), .sum(), .plot()) directly on raw object/string/date columns.\n"
    "2. Always coerce numeric columns first with `pd.to_numeric(df['col'], errors='coerce')` and dropna/fillna as needed.\n"
    "3. Always cast date columns with `pd.to_datetime(df['col'], errors='coerce')` BEFORE any time-series grouping, "
    "resampling, or date-axis plotting.\n"
    "4. Guard against NaN by using .dropna() / .fillna() before plotting or aggregating.\n"
    "5. If a conversion still fails, print the error and a short dtype()/head(3) snapshot rather than crashing.\n"
    "6. PANDAS AGGREGATION SAFETY: When summing/averaging/aggregating a DataFrame, "
    "ALWAYS pass `numeric_only=True` (e.g. `df.sum(numeric_only=True)` or "
    "`df.mean(numeric_only=True)`) so datetime/object/string columns never cause "
    "a fatal TypeError during the aggregation.\n"
    "7. When resampling time-series, ALWAYS use modern pandas offsets: `.resample('ME')` for month-end, "
    "`.resample('QE')` for quarter-end, `.resample('YE')` for year-end. NEVER use legacy offsets "
    "'M', 'Q', 'A', or 'Y' (they are deprecated and raise FutureWarnings/errors in pandas >= 2.2)."
)


def _build_schema_profile(rows):
    """Metadata-first prompt context: schema + lightweight statistical profile.

    Returns (columns, types, profile_str, row_count). No raw rows are dumped
    into the prompt. The profile computes per-column min/max/mean/null-count
    over at most _PROFILE_ROWS rows (bounding runtime + token cost for
    million-row enterprise datasets).
    """
    if not rows:
        return [], {}, {}, 0
    keys = list(rows[0].keys())
    row_count = len(rows)
    scan = rows[:_PROFILE_ROWS]

    types = {}
    profile = {}
    for k in keys:
        # Infer type from first non-null value
        col_type = 'unknown'
        for row in scan:
            v = row.get(k)
            if v is not None:
                col_type = type(v).__name__
                break
        types[k] = col_type

        # Lightweight stats over up to _PROFILE_ROWS rows
        numeric = []
        nulls = 0
        for row in scan:
            v = row.get(k)
            if v is None:
                nulls += 1
                continue
            try:
                numeric.append(float(v))
            except (ValueError, TypeError):
                # Non-numeric — only track nulls and leave numeric stats as null
                pass
        stats = {"nulls": nulls}
        if numeric:
            stats["min"] = round(min(numeric), 4)
            stats["max"] = round(max(numeric), 4)
            stats["mean"] = round(sum(numeric) / len(numeric), 4)
        profile[k] = stats

    return keys, types, profile, row_count



def _extract_python_blocks(text):
    """Extract all ```python ... ``` code blocks from markdown."""
    blocks = []
    if not text:
        return blocks
    pattern = re.compile(r"```(?:python|py)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)
    for m in pattern.finditer(text):
        code = m.group(1).strip()
        if code:
            blocks.append(code)
    return blocks


def _strip_code_fences(code):
    """Remove ```python ... ``` wrapper if present."""
    if not code or not isinstance(code, str):
        return code
    stripped = code.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def _clean_json(text):
    """Extract the first balanced JSON object from an LLM response.

    Handles markdown code fences around the JSON (```json ... ```),
    leading text, and trailing commentary.
    """
    if not text:
        return None
    # Strip markdown ```json ... ``` fences if present
    candidate = text
    fenced = re.search(r"```(?:json)?\s*\n?(.*?)```", candidate, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    start = candidate.find('{')
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(candidate)):
        ch = candidate[i]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                raw = candidate[start:i + 1]
                try:
                    return json.loads(raw)
                except Exception:
                    try:
                        return json.loads(raw.replace('\n', '\\n')) if raw else None
                    except Exception:
                        return None
    return None


    return None


def _sanitize_legacy_offsets(code):
    """Rewrite legacy pandas resample offsets to modern ones.

    pandas >= 2.2 deprecated 'M' (month-end) -> 'ME', 'Q' -> 'QE',
    'A'/'Y' (year-end) -> 'YE'. Applied as a hard guardrail on generated
    code so DeepBot never trips the FutureWarning/error at execution time.
    """
    if not code:
        return code
    # .resample('M') -> .resample('ME'), .resample("Q") -> .resample("QE"),
    # .resample('A')/.resample('Y') -> .resample('YE') (both quote styles).
    code = re.sub(r"\.resample\(\s*['\"]M['\"]\s*\)", ".resample('ME')", code)
    code = re.sub(r"\.resample\(\s*['\"]Q['\"]\s*\)", ".resample('QE')", code)
    code = re.sub(r"\.resample\(\s*['\"][AY]['\"]\s*\)", ".resample('YE')", code)
    # Also cover common alias arguments like freq='M' / freq="Q" / freq='A'.
    # Capturing group avoids variable-width lookbehind (Python re limitation).
    code = re.sub(r"(freq\s*=\s*)['\"]M['\"]", r"\1'ME'", code)
    code = re.sub(r"(freq\s*=\s*)['\"]Q['\"]", r"\1'QE'", code)
    code = re.sub(r"(freq\s*=\s*)['\"][AY]['\"]", r"\1'YE'", code)
    return code


def _capture_plots(code_with_plt):
    """Inject a plot-capture footer that encodes any open figure to base64."""
    footer = (
        "\nimport base64, io, json as _json\n"
        "_images = []\n"
        "_plot_error = None\n"
        "try:\n"
        "    import matplotlib.pyplot as _plt\n"
        "    for _n in _plt.get_fignums():\n"
        "        _fig = _plt.figure(_n)\n"
        "        _buf = io.BytesIO()\n"
        "        _fig.savefig(_buf, format='png', bbox_inches='tight', dpi=110)\n"
        "        _images.append(base64.b64encode(_buf.getvalue()).decode('ascii'))\n"
        "        _plt.close(_fig)\n"
        "except Exception as _e:\n"
        "    _plot_error = str(_e)\n"
    )
    return code_with_plt + "\n" + footer


def run_deepbot(prompt, rows, llm=None, user=None):
    """Main entry point.

    Args:
        prompt: natural-language instruction from the user.
        rows: list-of-dicts from the active query result.
        llm: optional callable(text) -> str (LLM completion). Defaults to
             ChatOpenAI (gpt-4o-mini).
        user: optional User object. When BYOK is enabled, the LLM client is
             built from the user's decrypted provider key.
    Returns:
        dict with keys {explanation, code, output, html, image, error, data}.
    """
    keys, types, profile, row_count = _build_schema_profile(rows)
    if row_count == 0:
        return {
            "explanation": "I need some data to analyse. Run a SQL query first, then ask me a follow-up like 'what does this look like statistically?' or 'can you plot…?'",
            "code": "",
            "output": "",
            "image": None,
            "error": None,
        }

    user_prompt = (
        f"User request: {prompt}\n\n"
        "Write the Python snippet as instructed. Remember: return ONLY the JSON payload."
    )

    # Generate code via LLM
    if llm is None:
        try:
            if user is not None and (
        getattr(user, 'byok_enabled', False) or workspace_byok_enabled(user)):
                chat = build_chat_openai(user=user, model='gpt-4o-mini', temperature=0.3)
            else:
                from langchain_openai import ChatOpenAI
                chat = ChatOpenAI(model='gpt-4o-mini', temperature=0.3)
            response = chat.invoke([
                {"role": "system", "content": _SYSTEM_PROMPT.format(
                    types=json.dumps(types),
                    row_count=row_count,
                    profile=json.dumps(profile, default=str)
                )},
                {"role": "user", "content": user_prompt}
            ])
            raw = getattr(response, 'content', '') or str(response)
        except Exception as e:
            return {
                "explanation": "",
                "code": "",
                "output": "",
                "image": None,
                "error": f"LLM generation failed: {e}",
            }
    else:
        raw = llm(_SYSTEM_PROMPT.format(
            types=json.dumps(types),
            row_count=row_count,
            profile=json.dumps(profile, default=str)
        ) + "\n" + user_prompt)

    parsed = _clean_json(raw)

    # ---- Robust extraction: prefer JSON, fall back to markdown code blocks ----
    explanation = ''
    code = ''

    if parsed and isinstance(parsed, dict):
        explanation = parsed.get('explanation', '') or ''
        code = parsed.get('code') or ''
        if not isinstance(code, str):
            code = str(code)
        code = _strip_code_fences(code).strip()

    # If JSON failed OR no code key, extract ```python ... ``` blocks directly.
    if not code and isinstance(raw, str):
        blocks = _extract_python_blocks(raw)
        if blocks:
            code = "\n\n".join(blocks)
            if not explanation:
                prose = re.sub(r"```(?:python|py)?\s*\n?.*?```", "", raw,
                               flags=re.DOTALL | re.IGNORECASE).strip()
                if prose and not prose.startswith('{'):
                    explanation = prose[:2000]
                else:
                    explanation = "Here is the analysis from DeepBot."

    if not code:
        return {
            "explanation": explanation or "DeepBot returned no code.",
            "code": "",
            "output": "",
            "image": None,
            "error": "No code in LLM output.",
        }

    # Hard guardrail: rewrite legacy pandas resample offsets to modern ones
    # before execution ('.resample('M')' -> 'ME', 'Q' -> 'QE', 'A'/'Y' -> 'YE').
    code = _sanitize_legacy_offsets(code)

    # Build the full executable script: preloads + HTML styler helpers + user code.
    script = _PRELOAD + _HTML_TABLE_HELPERS + _EXTRA + "\n" + code + "\n"
    # Intercept DataFrame prints so they render as styled HTML tables.
    # Uses the sandbox's own `print` (captured as _orig_print) to avoid a
    # NameError on `builtins` (the sandbox exposes builtins as a dict).
    if 'dataframe' in code.lower() or 'df.' in code or 'print(' in code:
        script = (
            "import pandas as _pd_capture\n"
            "def _safe_print(*args, **kw):\n"
            "    if len(args) == 1 and isinstance(args[0], _pd_capture.DataFrame):\n"
            "        _orig_print('" + _DF_HTML_MARKER + "' + _df_to_html(args[0]) + '" + _DF_HTML_END + "', **kw)\n"
            "    else:\n"
            "        _orig_print(*args, **kw)\n"
            "_orig_print = print\n"
            "print = _safe_print\n"
        ) + script
    # Only append plot-capture if the code uses matplotlib AND it is installed
    if ('plt.' in code or 'pyplot' in code):
        try:
            __import__('matplotlib')
            script = _capture_plots(script)
        except Exception:
            pass  # matplotlib unavailable — skip plot capture gracefully

    # ---- Sandboxed execution ----
    image = None
    output = ""
    error = None
    styled_html = None
    try:
        # Restricted namespace with only the pre-loaded modules (+ df)
        globs = {"df": __import__('pandas').DataFrame(rows)}
        exec_globals = {}
        # Build a minimal builtins whitelist to keep the sandbox tight
        import builtins
        _safe_builtins = {
            'print': builtins.print, 'len': len, 'range': range, 'str': str,
            'int': int, 'float': float, 'bool': bool, 'list': list, 'dict': dict,
            'tuple': tuple, 'set': set, 'abs': abs, 'min': min, 'max': max,
            'sum': sum, 'round': round, 'sorted': sorted, 'enumerate': enumerate,
            'zip': zip, 'any': any, 'all': all, 'isinstance': isinstance,
            'type': type, 'repr': repr, 'Exception': Exception, 'ValueError': ValueError,
            'TypeError': TypeError, 'MemoryError': MemoryError,
            # Required so `import pandas as pd` / `import numpy as np` etc.
            # work inside the sandbox. The restrictive sandbox is enforced via
            # the module whitelist (only pd/np/sklearn/matplotlib pre-loaded) and
            # the LLM system prompt boundary, not by removing __import__ entirely.
            '__import__': builtins.__import__,
        }
        exec_globals['__builtins__'] = _safe_builtins
        exec_globals['df'] = globs['df']

        # Capture stdout
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(compile(script, '<deepbot>', 'exec'), exec_globals)
        raw_output = buf.getvalue()

        # Extract styled HTML table blocks produced by the _safe_print interceptor;
        # the remaining plain-text output is kept for the code-output panel.
        html_parts = []
        cleaned_out = raw_output
        while True:
            s = cleaned_out.find(_DF_HTML_MARKER)
            if s == -1:
                break
            e = cleaned_out.find(_DF_HTML_END, s + len(_DF_HTML_MARKER))
            if e == -1:
                break
            html_parts.append(cleaned_out[s + len(_DF_HTML_MARKER):e])
            cleaned_out = cleaned_out[:s] + cleaned_out[e + len(_DF_HTML_END):]
        styled_html = '\n'.join(html_parts) if html_parts else None
        output = cleaned_out

        # Retrieve captured images (injected via footer)
        image = exec_globals.get('_images') or []
        if image and isinstance(image, list):
            image = image[0]  # show the first (most relevant) plot
        else:
            image = None

        # Graceful fallback: if the generated code attempted a plot but the
        # capture failed (e.g. matplotlib unavailable in this environment),
        # surface a clear message while preserving the text/stat output.
        if image is None and ('plt.' in code or 'pyplot' in code):
            plot_err = exec_globals.get('_plot_error')
            if plot_err:
                logger.info('DeepBot plot capture unavailable: %s', plot_err)
                explanation = (explanation or '').rstrip() + (
                    "\n\n_⚠️ Plot capture was skipped — matplotlib is not available in this "
                    "backend environment. Install it with `pip install matplotlib`. "
                    "Your text and statistical output above are unaffected._"
                )
    except Exception as e:
        error = f"Execution error: {e}"
        logger.warning('DeepBot sandbox error: %s\n%s', e, traceback.format_exc())

    # ---- Tabular output: capture any DataFrame produced by the script ----
    # The script may reassign `df`, or produce `result`/`results` frames. Emit
    # them as paginated chat data rows alongside the plot, not just HTML.
    data_rows = None
    try:
        import pandas as _pd_cap
        cands = []
        # NOTE: intentionally EXCLUDE the raw input `df` from the candidate list
        # so DeepBot only surfaces explicitly generated analysis/summary frames
        # (result, results, summary, trend, aggregated) instead of dumping the
        # original source table alongside the requested analysis every run.
        for name in ('result', 'results', 'summary', 'trend', 'aggregated'):
            obj = exec_globals.get(name)
            if obj is not None:
                cands.append(obj)
        frame = None
        for obj in cands:
            try:
                if isinstance(obj, _pd_cap.DataFrame):
                    frame = obj
                    break
            except Exception:
                pass
        if frame is not None:
            from datetime import date as _date, datetime as _datetime, time as _time
            data_rows = []
            for _, row in frame.head(5000).iterrows():
                rec = {}
                for col in frame.columns:
                    v = row[col]
                    try:
                        if hasattr(v, 'isoformat'):
                            v = v.isoformat()
                        elif isinstance(v, _pd_cap.Period):
                            # pandas.Period (e.g. from .resample('ME') or
                            # .dt.to_period('M')) is NOT JSON serializable —
                            # render it as its display string.
                            v = str(v)
                        elif hasattr(v, 'item'):
                            v = v.item()
                        # numpy .item() can yield datetime.date / datetime
                        # / datetime.time which are also not JSON-serializable
                        # by default — normalize them via isoformat too.
                        if isinstance(v, (_date, _datetime, _time)):
                            v = v.isoformat()
                        if v != v:  # NaN
                            v = None
                    except Exception:
                        pass
                    rec[str(col)] = v
                data_rows.append(rec)
    except Exception as e:
        logger.warning('DeepBot DataFrame capture failed: %s', e)
        data_rows = None

    return {
        "explanation": explanation,
        "code": code,
        "output": output[:6000],
        "html": styled_html,
        "image": image,
        "error": error,
        "data": data_rows,
    }