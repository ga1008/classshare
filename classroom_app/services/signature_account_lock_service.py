"""Account rows precede signature rows in identity/binding transitions.

These are resource locks, not platform authorization locks. Appointment labels
do not grant administrator privileges. A caller which changes platform authority
must already have taken its existing task/actor transition locks before here.
No function commits or performs image/file work.
"""
from __future__ import annotations

import sqlite3


def lock_identity_accounts(conn, holders):
    keys = sorted({(str(role), int(identifier)) for role, identifier in holders
                   if role in {'teacher', 'student'} and identifier and int(identifier) > 0})
    if isinstance(conn, sqlite3.Connection):
        if not conn.in_transaction:
            conn.execute('BEGIN IMMEDIATE')
        for role, identifier in keys:
            table = 'teachers' if role == 'teacher' else 'students'
            conn.execute(f'UPDATE {table} SET id=id WHERE id=?', (identifier,))
    else:
        for role, identifier in keys:
            table = 'teachers' if role == 'teacher' else 'students'
            conn.execute(f'SELECT id FROM {table} WHERE id=? FOR UPDATE', (identifier,)).fetchone()
    return frozenset(keys)


def signature_bindings(conn, signature_ids):
    ids = sorted({int(value) for value in signature_ids if int(value) > 0})
    if not ids:
        return {}
    rows = conn.execute(
        f"SELECT id,subject_role,subject_id FROM electronic_signatures WHERE id IN ({','.join('?' for _ in ids)})",
        tuple(ids),
    ).fetchall()
    return {int(row['id']): (str(row['subject_role'] or ''), int(row['subject_id'] or 0)) for row in rows}


def prepare_signature_accounts(conn, signature_ids, *, additional_holders=()):
    """Snapshot first; a later binding change is a conflict, never a late lock."""
    before = signature_bindings(conn, signature_ids)
    lock_identity_accounts(conn, [*before.values(), *additional_holders])
    return before


def lock_signature_rows(conn, signature_ids):
    ids = sorted({int(value) for value in signature_ids if int(value) > 0})
    if isinstance(conn, sqlite3.Connection):
        if not conn.in_transaction:
            conn.execute('BEGIN IMMEDIATE')
        for identifier in ids:
            conn.execute('UPDATE electronic_signatures SET id=id WHERE id=?', (identifier,))
    else:
        for identifier in ids:
            # Explicit UPDATE-strength locks also order foreign-key inserts;
            # do not assume an unchanged key UPDATE takes the same row mode.
            conn.execute('SELECT id FROM electronic_signatures WHERE id=? FOR UPDATE', (identifier,)).fetchone()


def assert_signature_bindings(conn, before):
    if signature_bindings(conn, before) != before:
        from .signature_service import SignatureServiceError
        raise SignatureServiceError(409, '签名绑定已变化，请重新读取后操作。')


def lock_signature_accounts(conn, signature_ids, *, additional_holders=()):
    before = prepare_signature_accounts(conn, signature_ids, additional_holders=additional_holders)
    lock_signature_rows(conn, signature_ids)
    assert_signature_bindings(conn, before)
    return before
