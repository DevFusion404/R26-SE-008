"""
Minimal row store over either backend
=====================================
R26-SE-008 | Bandara S M Y M | IT22277886

The GitHub review tables (transformations, write-backs) need only insert,
equality select and update. This gives them one implementation that works on
SQLite and on Supabase/PostgREST, decided per call like the rest of db/.

JSON columns are stored as TEXT on both backends and decoded by the callers'
repositories, so a value means the same thing whichever backend wrote it.
"""

import config


class UniqueViolation(Exception):
    """An insert hit a UNIQUE constraint."""


class _Sqlite:
    @staticmethod
    def _db():
        from db.database import get_db
        return get_db()

    def insert(self, table, row):
        import sqlite3
        db = self._db()
        try:
            db.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES "
                       f"({', '.join('?' for _ in row)})", list(row.values()))
        except sqlite3.IntegrityError as exc:
            db.rollback()
            raise UniqueViolation(str(exc)) from exc
        db.commit()

    def select(self, table, where, order=None, desc=False, limit=None):
        clause = " AND ".join(f"{k}=?" for k in where) or "1=1"
        sql = f"SELECT * FROM {table} WHERE {clause}"
        if order:
            sql += f" ORDER BY {order} {'DESC' if desc else 'ASC'}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self._db().execute(sql, list(where.values())).fetchall()]

    def update(self, table, where, fields):
        db = self._db()
        cursor = db.execute(
            f"UPDATE {table} SET {', '.join(f'{k}=?' for k in fields)} WHERE "
            + " AND ".join(f"{k}=?" for k in where),
            list(fields.values()) + list(where.values()))
        db.commit()
        return cursor.rowcount


class _Supabase:
    @staticmethod
    def _table(table):
        from db.supabase_client import get_supabase
        return get_supabase().table(table)

    def insert(self, table, row):
        try:
            self._table(table).insert(row).execute()
        except Exception as exc:  # postgrest APIError, imported lazily
            if getattr(exc, "code", None) == "23505":
                raise UniqueViolation(str(exc)) from exc
            raise

    def select(self, table, where, order=None, desc=False, limit=None):
        query = self._table(table).select("*")
        for key, value in where.items():
            query = query.eq(key, value)
        if order:
            query = query.order(order, desc=desc)
        if limit:
            query = query.limit(limit)
        return query.execute().data or []

    def update(self, table, where, fields):
        query = self._table(table).update(fields)
        for key, value in where.items():
            query = query.eq(key, value)
        return len(query.execute().data or [])


def store():
    return _Supabase() if config.uses_supabase() else _Sqlite()
