from sqlalchemy import create_engine, text

from ingestion.config import DATABASE_URL

_engine = create_engine(DATABASE_URL)


def get_engine():
    return _engine


def upsert(conn, table, pk_columns, values):
    """Idempotent INSERT ... ON CONFLICT (pk_columns) DO UPDATE for a single row."""
    columns = list(values.keys())
    col_list = ", ".join(columns)
    placeholders = ", ".join(f":{c}" for c in columns)
    update_cols = [c for c in columns if c not in pk_columns]

    if update_cols:
        update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        conflict_action = f"DO UPDATE SET {update_clause}"
    else:
        conflict_action = "DO NOTHING"

    pk_list = ", ".join(pk_columns)
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT ({pk_list}) {conflict_action}"
    )
    conn.execute(text(sql), values)


def game_ids_with_stats(conn):
    """game_ids that already have player_game_stats rows, so backfill can skip them."""
    rows = conn.execute(text("SELECT DISTINCT game_id FROM player_game_stats")).fetchall()
    return {row[0] for row in rows}
