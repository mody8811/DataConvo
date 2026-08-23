"""RBAC helper: admin vs member table-level access control."""
import json
import sqlparse
from flask_login import current_user
from app.models import TablePermission, db


DEFAULT_MEMBER_TABLES = []  # Empty by default; admins toggle tables on


def get_member_allowed_tables(workspace_id=None, user_id=None):
    """Return tables a 'member' role may query in ONE workspace.

    STRICT tenant isolation — resolution order:
      1. Workspace row: TablePermission(workspace_id=<ws>, role='member')
      2. Legacy: per-user row (user_id, deprecated) ONLY if no workspace_id
         is supplied (callers should always pass workspace_id for members)
      3. Default empty list

    NOTE: This function intentionally does NOT fall back to legacy global rows
    (workspace_id IS NULL, user_id IS NULL). Members must have a row tied to
    their workspace; a missing row means NO table access (empty list), never
    cross-tenant leakage.
    """
    if workspace_id is not None:
        # PER-USER OVERRIDE takes precedence when a specific member is targeted.
        # Row key: (workspace_id=<ws>, user_id=<uid>, role='member').
        if user_id is not None:
            per_user = TablePermission.query.filter(
                TablePermission.workspace_id == workspace_id,
                TablePermission.role == 'member',
                TablePermission.user_id == user_id,
            ).first()
            if per_user:
                try:
                    return json.loads(per_user.allowed_tables or '[]')
                except Exception:
                    return []
        # WORKSPACE-WIDE default — row that ALL members inherit when no
        # per-member override exists: (workspace_id=<ws>, user_id IS NULL).
        ws_row = TablePermission.query.filter(
            TablePermission.workspace_id == workspace_id,
            TablePermission.role == 'member',
            TablePermission.user_id.is_(None),
        ).first()
        if ws_row:
            try:
                return json.loads(ws_row.allowed_tables or '[]')
            except Exception:
                return []
        # Neither a per-user override nor a workspace default exists ->
        # strict isolation: member in this workspace has NO access.
        return []

    if user_id is not None:
        row = TablePermission.query.filter_by(user_id=user_id, role='member').first()
        if row:
            try:
                return json.loads(row.allowed_tables or '[]')
            except Exception:
                return []

    return list(DEFAULT_MEMBER_TABLES)


def save_member_allowed_tables(allowed_tables, user_id=None, workspace_id=None, column_permissions=None):
    """Save (upsert) the allowed table list for the 'member' role.

    Resolution order (mirrors get_member_allowed_tables):
      1. Workspace-scoped row: TablePermission(workspace_id=<ws>, role='member')
      2. Legacy per-user row (user_id, deprecated)
      3. Legacy global NULL row (user_id IS NULL, workspace_id IS NULL)
    """
    allowed = [t for t in (allowed_tables or []) if t]
    col_map = column_permissions or {}

    # PER-MEMBER OVERRIDE: target a specific member (per-user row scoped to
    # this workspace) so workspace defaults remain untouched for other members.
    if workspace_id is not None and user_id is not None:
        per_row = TablePermission.query.filter(
            TablePermission.workspace_id == workspace_id,
            TablePermission.role == 'member',
            TablePermission.user_id == user_id,
        ).first()
        if not per_row:
            per_row = TablePermission(
                workspace_id=workspace_id, user_id=user_id,
                role='member', allowed_tables='[]',
            )
            db.session.add(per_row)

        per_row.allowed_tables = json.dumps(allowed)
        if col_map:
            per_row.set_column_map(col_map)
        elif column_permissions is not None:
            per_row.set_column_map({})
        db.session.commit()
        return allowed

    if workspace_id is not None:
        # Workspace-scoped: save against (workspace_id, role='member') so ALL
        # members of this workspace inherit the permission (strict tenant
        # isolation).
        #
        # IMPORTANT: The row MUST be user_id IS NULL (a true workspace-wide
        # role row). If a legacy per-user row exists for this workspace
        # (user_id set by an older save), we convert it by clearing user_id so
        # the strict get_member_allowed_tables lookup (user_id IS NULL) always
        # finds it.
        row = TablePermission.query.filter(
            TablePermission.workspace_id == workspace_id,
            TablePermission.role == 'member',
            TablePermission.user_id.is_(None),
        ).first()
        if not row:
            # Convert any legacy per-user row for this workspace, or create new.
            row = TablePermission.query.filter(
                TablePermission.workspace_id == workspace_id,
                TablePermission.role == 'member',
            ).first()
            if not row:
                row = TablePermission(
                    workspace_id=workspace_id,
                    user_id=None,
                    role='member',
                    allowed_tables='[]',
                )
                db.session.add(row)
            else:
                row.user_id = None  # promote legacy per-user row -> workspace-wide

        row.allowed_tables = json.dumps(allowed)
        if col_map:
            row.set_column_map(col_map)
        elif column_permissions is not None:
            row.set_column_map({})
        db.session.commit()
        return allowed

    if user_id is not None:
        row = TablePermission.query.filter_by(user_id=user_id, role='member').first()
        if not row:
            row = TablePermission(user_id=user_id, role='member', allowed_tables='[]')
            db.session.add(row)

        row.allowed_tables = json.dumps(allowed)
        if col_map:
            row.set_column_map(col_map)
        elif column_permissions is not None:
            row.set_column_map({})
        db.session.commit()
        return allowed

    # Legacy global default (user_id=NULL, workspace_id=NULL)
    row = TablePermission.query.filter_by(
        user_id=None, workspace_id=None, role='member').first()
    if not row:
        row = TablePermission(
            user_id=None,
            workspace_id=None,
            role='member',
            allowed_tables='[]',
        )
        db.session.add(row)

    row.allowed_tables = json.dumps(allowed)
    if col_map:
        row.set_column_map(col_map)
    elif column_permissions is not None:
        row.set_column_map({})
    db.session.commit()
    return allowed


def get_effective_tables(published_model, user=None):
    """Return a copy of the published semantic layer filtered by the user's role.

    - Admins: full access to ALL tables in the model (bypass).
    - Members: only tables allowed in THEIR workspace row. Strict tenant
      isolation — a member with no workspace-scoped permission row gets an
      empty table set (NO global fallback).
    """
    if user is None:
        from flask_login import current_user as cu
        user = cu if cu.is_authenticated else None

    if user is not None and getattr(user, 'role', 'member') == 'admin':
        return published_model

    allowed = get_member_allowed_tables(
        workspace_id=getattr(user, 'workspace_id', None) if user else None,
        user_id=user.id if user else None,
    )

    col_map = get_member_column_permissions(
        workspace_id=getattr(user, 'workspace_id', None) if user else None,
        user_id=user.id if user else None,
    )
    filtered = dict(published_model)
    filtered_tables = {}
    for name, data in (published_model.get('tables') or {}).items():
        if name not in allowed:
            continue
        if col_map and name in col_map and isinstance(col_map[name], list) and col_map[name]:
            allowed_cols = set(col_map[name])
            t_copy = dict(data) if isinstance(data, dict) else data
            if isinstance(t_copy, dict) and isinstance(t_copy.get('columns'), dict):
                t_copy['columns'] = {c: cd for c, cd in t_copy['columns'].items() if c in allowed_cols}
            filtered_tables[name] = t_copy
        else:
            filtered_tables[name] = data
    filtered['tables'] = filtered_tables
    return filtered


def get_disallowed_tables(published_model, user=None):
    """Return the table names that exist in the model but are NOT allowed for this user."""
    if user is None:
        from flask_login import current_user as cu
        user = cu if cu.is_authenticated else None

    if user is not None and getattr(user, 'role', 'member') == 'admin':
        return []

    allowed = set(get_member_allowed_tables(
        workspace_id=getattr(user, 'workspace_id', None) if user else None,
        user_id=user.id if user else None,
    ))
    all_tables = set((published_model.get('tables') or {}).keys())
    return sorted(all_tables - allowed)


def is_admin(user=None):
    """True if the user has the admin role."""
    if user is None:
        from flask_login import current_user as cu
        user = cu if cu.is_authenticated else None
    return user is not None and getattr(user, 'role', 'member') == 'admin'

def get_member_column_permissions(workspace_id=None, user_id=None):
    # Return {table: [allowed columns]} for the member role in ONE workspace.
    def _read(row):
        try:
            data = json.loads(row.column_permissions or '{}')
            if isinstance(data, dict):
                return {str(k): list(v) for k, v in data.items() if isinstance(v, list)}
        except Exception:
            pass
        return {}
    if workspace_id is not None:
        # PER-USER OVERRIDE takes precedence (mirrors get_member_allowed_tables).
        if user_id is not None:
            per_user = TablePermission.query.filter(TablePermission.workspace_id == workspace_id, TablePermission.role == 'member', TablePermission.user_id == user_id).first()
            if per_user:
                return _read(per_user)
        ws_row = TablePermission.query.filter(TablePermission.workspace_id == workspace_id, TablePermission.role == 'member', TablePermission.user_id.is_(None)).first()
        if ws_row:
            return _read(ws_row)
        return {}
    if user_id is not None:
        row = TablePermission.query.filter_by(user_id=user_id, role='member').first()
        return _read(row) if row else {}
    return {}


def get_disallowed_columns(published_model, user=None):
    if user is None:
        from flask_login import current_user as cu
        user = cu if cu.is_authenticated else None
    if user is not None and getattr(user, 'role', 'member') == 'admin':
        return {}
    ws_id = getattr(user, 'workspace_id', None) if user else None
    uid = user.id if user else None
    allowed = set(get_member_allowed_tables(workspace_id=ws_id, user_id=uid))
    col_map = get_member_column_permissions(workspace_id=ws_id, user_id=uid)
    if not col_map:
        return {}
    restricted = {}
    tables_src = (published_model or {}).get('tables') or {}
    for table_name, t_data in tables_src.items():
        if table_name not in allowed:
            continue
        whitelist = col_map.get(table_name)
        if not isinstance(whitelist, list) or not whitelist:
            continue
        all_cols = set((t_data or {}).get('columns', {}).keys())
        blocked = sorted(all_cols - set(whitelist))
        if blocked:
            restricted[table_name] = blocked
    return restricted
