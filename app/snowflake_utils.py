import snowflake.connector as sf
import pandas as pd

def _conn(user, password, account, warehouse, database, schema, role=None):
    return sf.connect(
        user=user, password=password, account=account,
        warehouse=warehouse, database=database, schema=schema, role=role
    )

def get_snowflake_columns(user, password, account, warehouse, database, schema, table, role=None):
    db, sch, tbl = table.split(".")
    with _conn(user, password, account, warehouse, database, schema, role) as con:
        cur = con.cursor()
        cur.execute(f"SHOW COLUMNS IN {db}.{sch}.{tbl}")
        rows = cur.fetchall()
        # rows: name, type, kind, ...
        cols = [r[2] for r in rows]  # column_name index
        return cols

def run_sql_sample(user, password, account, warehouse, database, schema, sql, role=None, limit=1000):
    with _conn(user, password, account, warehouse, database, schema, role) as con:
        df = pd.read_sql(f"{sql} LIMIT {limit}", con)
    return df
