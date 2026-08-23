"""Column-level RBAC SQL guard."""
import sqlparse

_SKIP = {'select', 'from', 'where', 'and', 'or', 'as', 'by', 'on', 'join',
         'group', 'order', 'having', 'limit', 'offset', 'distinct', 'not',
         'null', 'is', 'in', 'between', 'like', 'case', 'when', 'then',
         'else', 'end', 'desc', 'asc'}


def validate_query_columns(sql_query, restricted_map):
    """Raise ValueError if SQL references a column restricted for this member."""
    if not restricted_map:
        return
    blocked = {c.lower() for cols in restricted_map.values()
               if isinstance(cols, list) for c in cols}
    if not blocked:
        return
    found = set()
    for stmt in sqlparse.parse(sql_query or ''):
        for tok in stmt.flatten():
            if tok.ttype in (sqlparse.tokens.Keyword, sqlparse.tokens.Operator,
                             sqlparse.tokens.String, sqlparse.tokens.Number,
                             sqlparse.tokens.Punctuation,
                             sqlparse.tokens.Whitespace):
                continue
            raw = str(tok.value or '').strip()
            name = raw.strip('"').strip('`').strip('[').strip(']')
            if not name or '.' in name or ' ' in name or name.lower() in _SKIP:
                continue
            if name.lower() in blocked:
                found.add(name.lower())
    if found:
        raise ValueError(
            "Data policy restriction: column(s) " + ", ".join(sorted(found)) +
            " are restricted for your role. Contact your administrator if you need access."
        )