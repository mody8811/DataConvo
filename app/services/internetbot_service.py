"""InternetBot - Multi-Agent Orchestration engine
InternetBot does NOT query the database directly. It delegates to QueryBot/DeepBot and does web searches.
"""
import json
import logging
import re
import urllib.parse
import urllib.request

from app.agents.llm_router import build_chat_openai, workspace_byok_enabled

logger = logging.getLogger(__name__)

_TOOL_ASK_QUERYBOT = 'ask_querybot'
_TOOL_ASK_DEEPBOT = 'ask_deepbot'
_TOOL_WEB_SEARCH = 'web_search'

_SYSTEM_PROMPT = """You are the external research agent (InternetBot).

Your job is to synthesize internal company analytics with external real-world
events (market news, macro-economic indicators, industry trends).

Current year is 2026. Always search for up-to-date current trends using
2026 unless specified otherwise.

When the user asks a question that requires internal data, follow this flow:
1. First use `ask_querybot` to get relevant internal metrics, OR `ask_deepbot`
   if statistical analysis / trends / distributions are needed.
2. Then use `web_search` to find external real-world events that explain or
   contextualize those numbers.
3. Finally synthesize BOTH into a single clear answer.

CRITICAL EXECUTION RULES (MANDATORY):
- NO DEATH LOOPS: If a tool returns an error (e.g. a Python Traceback or SQL
  error), you are STRICTLY FORBIDDEN from calling the exact same tool with the
  exact same query again. You must either rewrite the query/code to fix the
  error, or proceed to the final synthesis using the data you already have.
- SEQUENTIAL DEPENDENCY: Do not call `ask_querybot` and `ask_deepbot`
  simultaneously if the DeepBot analysis depends on the specific output of the
  QueryBot SQL fetch. Execute them sequentially (QueryBot FIRST, then DeepBot
  once its rows are available).
- TOOL EFFICIENCY (MANDATORY): You have a LIMITED iteration budget. Combine
  related data questions into a SINGLE ask_querybot call when possible.
  Do NOT spawn parallel variations of the same query (e.g. total revenue,
  average revenue, and max revenue as three separate calls). One well-crafted
  query using GROUP BY can return all metrics. Keep at most 1-2 QueryBot calls
  and 1-2 DeepBot calls per turn. Reserve the final iteration for SYNTHESIS.
- WEBSITE SEARCH BUDGET: Limit web_search calls to at most 2 per request.
  Prefer ONE broad search over many narrow ones.
- FINAL SYNTHESIS: Once you have gathered enough data, STOP calling tools and
  produce the final JSON payload with explanation, tool_calls, and sources.
  If you are near the iteration limit, synthesize with what you have rather than
  requesting more data - a complete briefing beats infinite data gathering.

Return ONLY a JSON payload with keys:
- "explanation": markdown text with the synthesized answer
- "tool_calls": array of {"tool", "query"} describing each tool call
- "sources": array of {"title", "url", "snippet"} from web_search
"""


def web_search(query, max_results=5):
    """External web search via DuckDuckGo, Bing fallback."""
    results = []
    if not query or not query.strip():
        return results
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"

    try:
        enc = urllib.parse.quote(query.strip())
        req = urllib.request.Request(
            "https://html.duckduckgo.com/html/?q=" + enc,
            headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        links = re.findall(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            html, re.DOTALL | re.IGNORECASE)
        snips = re.findall(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            html, re.DOTALL | re.IGNORECASE)
        for i, (href, th) in enumerate(links[:max_results]):
            m = re.search(r"uddg=([^&]+)", href)
            url = urllib.parse.unquote(m.group(1)) if m else href
            title = re.sub(r"<[^>]+>", "", th).strip()
            sn = re.sub(r"<[^>]+>", "", snips[i]).strip() if i < len(snips) else ""
            if title:
                results.append({"title": title, "url": url, "snippet": sn})
    except Exception as e:
        logger.info("InternetBot web_search failed (DDG): %s", e)
    if not results:
        try:
            enc = urllib.parse.quote(query.strip())
            req = urllib.request.Request(
                "https://www.bing.com/search?q=" + enc,
                headers={"User-Agent": user_agent})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            blocks = re.findall(
                r'<li class="b_algo".*?</li>', html, re.DOTALL | re.IGNORECASE)
            for block in blocks[:max_results]:
                mu = re.search(r'<h2[^>]*><a[^>]+href="([^"]+)"', block)
                mt = re.search(r'<a[^>]+href="[^"]+"[^>]*>(.*?)</a>', block, re.DOTALL)
                ms = re.search(r"<p[^>]*>(.*?)</p>", block, re.DOTALL)
                if not mu or not mt:
                    continue
                title = re.sub(r"<[^>]+>", "", mt.group(1)).strip()
                sn = re.sub(r"<[^>]+>", "", ms.group(1)).strip() if ms else ""
                if title:
                    results.append({"title": title, "url": mu.group(1), "snippet": sn})
        except Exception as e:
            logger.info("InternetBot web_search fallback failed: %s", e)
    return results[:max_results]



_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": _TOOL_ASK_QUERYBOT,
            "description": (
                "Delegate a natural-language request to QueryBot to fetch "
                "aggregated data metrics from the internal database."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language request for QueryBot."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": _TOOL_ASK_DEEPBOT,
            "description": (
                "Delegate a natural-language request to DeepBot for statistical "
                "analysis, distributions, correlations, or trend detection."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language request for DeepBot."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": _TOOL_WEB_SEARCH,
            "description": (
                "Search the web for external market news, macroeconomic events, "
                "or industry context that may explain internal business numbers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "External web search query."}
                },
                "required": ["query"],
            },
        },
    },
]



def _clean_json(text):
    """Extract the first balanced JSON object from an LLM response."""
    if not text:
        return None
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == chr(92):
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                raw = text[start : i + 1]
                try:
                    return json.loads(raw)
                except Exception:
                    return json.loads(raw.replace(chr(10), chr(92) + "n")) if raw else None
    return None





def _normalize_explanation(text):
    """Strip JSON framing from explanation text if the LLM leaked raw JSON.

    If `text` is a JSON string/object containing an "explanation" key, extract
    just that narrative. Otherwise return the text as-is (cleaned of markdown
    code fences around JSON).
    """
    if not text:
        return ""
    if not isinstance(text, str):
        return str(text)
    stripped = text.strip()
    # Remove markdown code fences if the whole string is wrapped
    if stripped.startswith("```"):
        # find the last ```
        end = stripped.rfind("```")
        if end > 3:
            stripped = stripped[3:end].strip()
            # strip leading language tag e.g. "json\n"
            if "\n" in stripped[:20]:
                stripped = stripped.split("\n", 1)[1].strip()
    # Try to parse as JSON if it looks like JSON
    candidate = None
    if stripped.startswith('{') or stripped.startswith('['):
        try:
            candidate = json.loads(stripped)
        except Exception:
            candidate = None
    if isinstance(candidate, dict):
        if candidate.get("explanation"):
            return str(candidate["explanation"]).strip()
        # If no explanation key, flatten the dict values into text
        parts = []
        for k, v in candidate.items():
            if k in ("tool_calls", "sources"):
                continue
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
        if parts:
            return "\n\n".join(parts)
    elif isinstance(candidate, list):
        # Could be an array of strings or dicts; join
        texts = []
        for item in candidate:
            if isinstance(item, dict):
                if item.get("explanation"):
                    texts.append(str(item["explanation"]))
                elif item.get("content"):
                    texts.append(str(item["content"]))
            elif isinstance(item, str):
                texts.append(item)
        if texts:
            return "\n\n".join(texts)
    # Not JSON - return original
    return stripped


def _sources_from_payload(payload):
    """Extract a clean sources list from any response payload shape."""
    sources = payload.get("sources") if isinstance(payload, dict) else None
    if not sources:
        return []
    clean = []
    for s in sources:
        if isinstance(s, dict):
            clean.append({
                "title": s.get("title") or s.get("name") or "Untitled",
                "url": s.get("url") or s.get("link") or "#",
                "snippet": s.get("snippet") or s.get("description") or "",
            })
        elif isinstance(s, str):
            clean.append({"title": s, "url": "#", "snippet": ""})
    return clean


def run_internetbot(prompt, rows, llm=None):
    """Main entry point for InternetBot orchestration.

    Args:
        prompt: natural-language instruction from the user.
        rows: list-of-dicts from the active query result (schema preview).
        llm: optional callable(system_prompt, user_prompt) -> str.
    Returns:
        dict with keys {explanation, tool_calls, sources, data, error}.
    """
    data_preview = ""
    if rows:
        keys = list(rows[0].keys())
        row_count = len(rows)
        data_preview = (
            "\nAvailable internal dataset: {} rows.\n"
            "Columns: {}\n"
            "Use ask_querybot for metrics/aggregations, "
            "ask_deepbot for statistical/trend analysis."
        ).format(row_count, ", ".join(keys))
    else:
        data_preview = (
            "\nNo internal dataset is currently loaded. "
            "Use ask_querybot to request metrics or ask_deepbot for analysis; "
            "you may still call web_search for external context."
        )

    user_prompt = (
        "User request: " + prompt + "\n" + data_preview +
        "\n\nDecide which tools to call. Return ONLY the JSON payload."
    )

    raw = None
    tool_calls_meta = []
    try:
        if llm is None:
            from langchain_openai import ChatOpenAI

            chat = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
            response = chat.bind_tools(_TOOL_DEFS, tool_choice="auto").invoke(messages)
            if hasattr(response, "content"):
                raw = response.content or ""
                raw = raw if isinstance(raw, str) else str(raw)
            else:
                raw = str(response)
            if hasattr(response, "tool_calls") and response.tool_calls:
                for tc in response.tool_calls:
                    fn = getattr(tc, "function", None)
                    if fn:
                        tool_calls_meta.append(
                            {
                                "tool": getattr(fn, "name", ""),
                                "query": (getattr(fn, "arguments", "") or ""),
                            }
                        )
        else:
            raw = llm(_SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        return {
            "explanation": "",
            "tool_calls": [],
            "sources": [],
            "data": None,
            "error": "LLM orchestration failed: " + str(e),
        }


    parsed = _clean_json(raw) if isinstance(raw, str) else None
    if not parsed:
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else None
        except Exception:
            parsed = None
    if not parsed:
        parsed = {
            "explanation": raw if isinstance(raw, str) and raw.strip() else (
                "I received your request. Let me research the internal data "
                "and external context for you."
            ),
            "tool_calls": tool_calls_meta,
            "sources": [],
        }

    raw_calls = parsed.get("tool_calls", []) or tool_calls_meta or []
    tool_calls = []
    for tc in raw_calls:
        if isinstance(tc, dict):
            tool_calls.append({"tool": tc.get("tool", ""), "query": tc.get("query", "")})
        elif isinstance(tc, str):
            tool_calls.append({"tool": "", "query": tc})

    sources = []
    for tc in tool_calls:
        if tc.get("tool") == _TOOL_WEB_SEARCH or "search" in str(tc.get("tool", "")):
            q = tc.get("query") or prompt
            sources.extend(web_search(q))

    return {
        "explanation": _normalize_explanation(parsed.get("explanation", "")),
        "tool_calls": tool_calls,
        "sources": _sources_from_payload(parsed) if not sources else sources,
        "data": rows,
        "error": None,
    }



def run_internetbot_agentic(prompt, rows, tool_executor, max_iterations=8, user=None, step_callback=None):
    """Agentic loop: invoke LLM with tools, execute tool calls, feed results
    back to the model, repeat until the model produces a final JSON synthesis
    or we hit max_iterations.

    Args:
        prompt: natural-language instruction from the user.
        rows: list-of-dicts from the active query result (schema preview).
        tool_executor: callable(tool_name, tool_query, rows) -> dict result.
        max_iterations: cap on LLM+tool round-trips (default 8 - increased
                        so multi-domain queries still have headroom for the
                        final synthesis pass).
        user: optional User object. When BYOK is enabled, the orchestrator LLM
              is built from the user's decrypted provider key.
        step_callback: optional callable(event_type, payload) invoked at each
              phase: 'thought', 'tool_start', 'tool_end', 'synthesis'.
              Used for SSE streaming / live "Agent Thought Stream".
    Returns:
        dict with keys {explanation, tool_calls, sources, data, error}.

    NOTE: Each agentic iteration is a FULL LLM round-trip (invoke + tool exec).
    Inside a single iteration we may run MULTIPLE tools. Complex multi-domain
    queries that call QueryBot + DeepBot + web_search across several turns
    typically consume 4-7 iterations. The default budget is therefore 8 to
    guarantee a final synthesis pass even when many tools are requested.
    """
    data_preview = ""
    if rows:
        keys = list(rows[0].keys())
        row_count = len(rows)
        data_preview = (
            "\nAvailable internal dataset: {} rows.\n"
            "Columns: {}\n"
            "Use ask_querybot for metrics/aggregations, "
            "ask_deepbot for statistical/trend analysis."
        ).format(row_count, ", ".join(keys))
    else:
        data_preview = (
            "\nNo internal dataset is currently loaded. "
            "Use ask_querybot to request metrics or ask_deepbot for analysis; "
            "you may still call web_search for external context."
        )

    user_prompt = (
        "User request: " + prompt + "\n" + data_preview +
        "\n\nDecide which tools to call. Return ONLY the JSON payload."
    )

    tool_calls_record = []   # [{tool, query}] in call order
    sources = []
    collected_data = rows or []

    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import (
            HumanMessage, SystemMessage, AIMessage, ToolMessage,
        )

        if user is not None and (
        getattr(user, 'byok_enabled', False) or workspace_byok_enabled(user)):
            chat = build_chat_openai(user=user, model='gpt-4o-mini', temperature=0.3)
        else:
            chat = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
        model_with_tools = chat.bind_tools(_TOOL_DEFS, tool_choice="auto")

        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]

        explanation = ""
        # Hard anti-death-loop guard: key = (tool_name, normalized query).
        # If a tool+query fails once, we refuse to execute the SAME pair again.
        failed_calls = set()
        # Deduplicate SUCCESSFUL tool calls: key = (tool_name, normalized query).
        # Prevents the orchestrator from spawning redundant sub-agent calls
        # (e.g. asking QueryBot the same metric 4 times in a row).
        executed_calls = set()

        def _emit(event_type, payload_dict=None):
            """Fire a step event through step_callback (if provided)."""
            if step_callback is None:
                return
            try:
                step_callback(event_type, payload_dict or {})
            except Exception:
                pass

        for iteration in range(max_iterations):
            # 1) Ask the LLM
            _emit('thought', {'iteration': iteration + 1,
                              'message': f"Step {iteration + 1}: thinking about which tools to use..."})
            response = model_with_tools.invoke(messages)
            messages.append(response)

            tool_calls = getattr(response, "tool_calls", None)
            if not tool_calls:
                # No tools wanted -> final synthesis in response.content
                _emit('synthesis', {'message': 'All data gathered — synthesizing final intelligence briefing...'})
                final_text = getattr(response, "content", "") or ""
                parsed = _clean_json(final_text) if isinstance(final_text, str) else None
                if not parsed:
                    try:
                        parsed = json.loads(final_text) if isinstance(final_text, str) else None
                    except Exception:
                        parsed = None
                if not parsed:
                    parsed = {
                        "explanation": final_text if isinstance(final_text, str) and final_text.strip()
                                       else "InternetBot completed its research.",
                        "tool_calls": tool_calls_record,
                        "sources": sources,
                    }
                return {
                    "explanation": _normalize_explanation(parsed.get("explanation", "")),
                    "tool_calls": tool_calls_record or parsed.get("tool_calls", []),
                    "sources": _sources_from_payload(parsed) if not sources else sources,
                    "data": collected_data,
                    "error": None,
                }

            # 2) Sequential-dependency resolution: if the model asked for BOTH
            #    ask_querybot AND ask_deepbot in the same turn, run ask_querybot
            #    FIRST. Defer any ask_deepbot that depends on that fetch until the
            #    next iteration (after the QueryBot SQL rows are fed back).
            order = []
            for tc in tool_calls:
                tname = tc.get("name") or tc.get("function", {}).get("name", "")
                order.append((tname, tc))
            has_query = any(n == _TOOL_ASK_QUERYBOT or n == "ask_querybot" for n, _ in order)
            has_deep = any(n == _TOOL_ASK_DEEPBOT or n == "ask_deepbot" for n, _ in order)
            deferred = []
            if has_query and has_deep:
                # Keep ask_querybot + web_search now; defer ask_deepbot.
                keep, deferred = [], []
                for n, tc in order:
                    if n == _TOOL_ASK_DEEPBOT or n == "ask_deepbot":
                        deferred.append(tc)
                    else:
                        keep.append(tc)
                order = [(n, tc) for n, tc in keep]
                # If everything was deferred (unlikely), keep the original set.
                if not order:
                    order = [(n, tc) for n, tc in order] if order else [(n, tc) for n, tc in zip(
                        [tc.get("name") or tc.get("function", {}).get("name", "") for tc in tool_calls],
                        tool_calls,
                    )]

            # 3) Execute each requested tool call (in dependency-safe order)
            for tc_name_holder in order:
                tname, tc = tc_name_holder
                # LangChain tool call shape: {name, args, id}
                tool_name = tname
                tool_args = tc.get("args") or {}
                if isinstance(tool_args, str):
                    try:
                        tool_args = json.loads(tool_args)
                    except Exception:
                        tool_args = {}
                tool_query = (tool_args.get("query") or prompt or "").strip()
                call_id = tc.get("id") or tc.get("tool_call_id") or "call_" + str(iteration)

                # ANTI-DEATH-LOOP: if we already executed this exact tool+query
                # and it errored, DO NOT run it again. Force the model to either
                # rewrite the query or move on to synthesis.
                sig = (tool_name, tool_query)
                if sig in failed_calls:
                    messages.append(ToolMessage(
                        content=(
                            "This exact tool call (tool={!r}, query={!r}) previously failed "
                            "and is BLOCKED. Do NOT retry it verbatim. Rewrite the query/code "
                            "to fix the error, use a different approach, or proceed directly "
                            "to the final synthesis with data you already have."
                        ).format(tool_name, tool_query),
                        tool_call_id=call_id,
                    ))
                    tool_calls_record.append({
                        "tool": tool_name, "query": tool_query, "blocked": True, "error": "prior failure"
                    })
                    continue

                # DEDUPLICATION: if this exact tool+query already ran successfully,
                # do NOT re-run it. Tell the model to use the already-collected
                # data instead of spawning a redundant pass.
                if sig in executed_calls:
                    messages.append(ToolMessage(
                        content=(
                            "This exact tool call (tool={!r}, query={!r}) was already "
                            "executed successfully earlier. Use the previous result; DO NOT "
                            "re-run it. Either continue to the final synthesis or request a "
                            "DIFFERENT analysis."
                        ).format(tool_name, tool_query),
                        tool_call_id=call_id,
                    ))
                    tool_calls_record.append({
                        "tool": tool_name, "query": tool_query, "duplicate": True
                    })
                    _emit('tool_start', {'tool': tool_name, 'query': tool_query,
                                         'skipped': True})
                    continue

                tool_calls_record.append({"tool": tool_name, "query": tool_query})
                _emit('tool_start', {'tool': tool_name, 'query': tool_query,
                                     'message': f"Calling {tool_name}..."})

                # Execute the delegation via the route injectable executor
                result = {}
                try:
                    result = tool_executor(tool_name, tool_query, rows)
                except Exception as exc:
                    result = {"error": str(exc)}

                # Mark success so we never duplicate this call
                if not result.get("error"):
                    executed_calls.add(sig)
                _emit('tool_end', {'tool': tool_name, 'query': tool_query,
                                   'error': result.get('error')})

                # Mark failures so the same tool+query is never retried verbatim.
                if result.get("error"):
                    failed_calls.add(sig)

                # web_search results also collected for the source list
                if tool_name == _TOOL_WEB_SEARCH or "search" in str(tool_name):
                    got = result.get("sources") or []
                    sources.extend(got if isinstance(got, list) else [])

                # NOTE: Do NOT overwrite collected_data with tool results.
                # InternetBot must continue to display the LAST dataframe from
                # QueryBot (rows) — not whatever a delegated tool happened to
                # query (which may target a different table).

                # 4) Feed the tool output back to the LLM
                messages.append(ToolMessage(content=str(result), tool_call_id=call_id))

            # 5) If we deferred ask_deepbot (sequential dependency), append a
            #    human nudge so the model re-requests it based on QueryBot's output.
            #    NOTE: we do NOT inject AIMessage(tool_calls=[deferred]) here —
            #    that would create a dangling tool call with no ToolMessage and
            #    break the next OpenAI turn. The QueryBot rows are already in the
            #    conversation; the model will re-request ask_deepbot naturally.
            if deferred:
                for d in deferred:
                    dname = d.get("name") or d.get("function", {}).get("name", "")
                    dargs = d.get("args") or {}
                    if isinstance(dargs, str):
                        try:
                            dargs = json.loads(dargs)
                        except Exception:
                            dargs = {}
                    tool_calls_record.append({
                        "tool": dname,
                        "query": (dargs.get("query") or prompt or ""),
                        "deferred": True,
                    })
                messages.append(HumanMessage(content=(
                    "The ask_deepbot tool call(s) were deferred until after "
                    "ask_querybot completed, because the DeepBot analysis depends "
                    "on QueryBot's fresh SQL output. Re-request ask_deepbot now "
                    "with the precise request, using the QueryBot rows just returned."
                )))
                # Let the loop continue so the model can re-issue ask_deepbot.
                continue

        # max_iterations reached without a final synthesis.
        # Instead of returning raw gathered artifacts, run one FINAL LLM pass
        # to synthesize a coherent human-readable answer from all collected data.
        _emit('synthesis', {'message': 'Reached tool budget — running mandatory final synthesis...'})
        try:
            synth_prompt = (
                "You have completed your research phase. Below is a summary of "
                "everything you gathered via tool calls. Write a FINAL, coherent, "
                "human-readable answer to the user's original request. "
                "Do NOT use tools. Do NOT return JSON. Just write the narrative.\"\n\n"
                "Original user request: " + prompt + "\n\n"
                "Tool calls executed:\n" + json.dumps(tool_calls_record, default=str) + "\n\n"
                "External sources:\n" + json.dumps(sources, default=str) + "\n\n"
                "Data rows available: " + str(len(collected_data)) + "\n"
                "Write the final briefing now."
            )
            final_resp = chat.invoke([
                {"role": "system", "content":
                    "You are InternetBot's synthesis layer. Produce a polished, "
                    "narrative answer. Markdown formatting is allowed."},
                {"role": "user", "content": synth_prompt},
            ])
            final_text = getattr(final_resp, "content", "") or str(final_resp)
            explanation = final_text if isinstance(final_text, str) and final_text.strip() else (
                "I gathered research data but could not synthesize a final answer."
            )
        except Exception as e:
            explanation = (
                "I gathered internal and external data but the final synthesis "
                "step failed. Here is the research collected: " + str(e)
            )

        return {
            "explanation": explanation,
            "tool_calls": tool_calls_record,
            "sources": sources,
            "data": collected_data,
            "error": None,
        }
    except Exception as exc:
        return {
            "explanation": "",
            "tool_calls": tool_calls_record,
            "sources": sources,
            "data": collected_data,
            "error": "InternetBot agentic loop failed: " + str(exc),
        }
