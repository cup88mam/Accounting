import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

class TursoResponse:
    def __init__(self, data):
        self.data = data

class TursoQueryBuilder:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.action = None
        self.columns = "*"
        self.payload = None
        self.conditions = []
        self.order_col = None
        self.order_desc = False
        self.limit_val = None

    def select(self, columns="*"):
        self.action = "select"
        self.columns = columns
        return self

    def insert(self, payload):
        self.action = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.action = "update"
        self.payload = payload
        return self

    def delete(self):
        self.action = "delete"
        return self

    def eq(self, col, val):
        self.conditions.append((f"{col} = ?", [val]))
        return self

    def neq(self, col, val):
        self.conditions.append((f"{col} != ?", [val]))
        return self

    def like(self, col, val):
        self.conditions.append((f"{col} LIKE ?", [val]))
        return self

    def ilike(self, col, val):
        self.conditions.append((f"LOWER({col}) LIKE LOWER(?)", [val]))
        return self

    def in_(self, col, vals):
        if not vals:
            self.conditions.append(("1 = 0", []))
        else:
            placeholders = ", ".join(["?"] * len(vals))
            self.conditions.append((f"{col} IN ({placeholders})", list(vals)))
        return self

    def order(self, col, desc=False):
        self.order_col = col
        self.order_desc = desc
        return self

    def limit(self, val):
        self.limit_val = val
        return self

    def _build_where(self):
        if not self.conditions:
            return "", []
        clauses = []
        params = []
        for c, p in self.conditions:
            clauses.append(c)
            params.extend(p)
        return " WHERE " + " AND ".join(clauses), params

    def execute(self):
        if self.action == "select":
            where_sql, params = self._build_where()
            sql = f"SELECT {self.columns} FROM {self.table_name}{where_sql}"
            if self.order_col:
                direction = "DESC" if self.order_desc else "ASC"
                sql += f" ORDER BY {self.order_col} {direction}"
            if self.limit_val is not None:
                sql += f" LIMIT {int(self.limit_val)}"
            rows = self.client.execute_sql(sql, params)
            return TursoResponse(rows)

        elif self.action == "insert":
            if isinstance(self.payload, list):
                if not self.payload:
                    return TursoResponse([])
                results = []
                for item in self.payload:
                    cols = list(item.keys())
                    placeholders = ", ".join(["?"] * len(cols))
                    col_names = ", ".join(cols)
                    vals = [json.dumps(item[c], ensure_ascii=False) if isinstance(item[c], (list, dict)) else item[c] for c in cols]
                    sql = f"INSERT INTO {self.table_name} ({col_names}) VALUES ({placeholders}) RETURNING *"
                    res = self.client.execute_sql(sql, vals)
                    results.extend(res)
                return TursoResponse(results)
            else:
                cols = list(self.payload.keys())
                placeholders = ", ".join(["?"] * len(cols))
                col_names = ", ".join(cols)
                vals = [json.dumps(self.payload[c], ensure_ascii=False) if isinstance(self.payload[c], (list, dict)) else self.payload[c] for c in cols]
                sql = f"INSERT INTO {self.table_name} ({col_names}) VALUES ({placeholders}) RETURNING *"
                res = self.client.execute_sql(sql, vals)
                return TursoResponse(res)

        elif self.action == "update":
            cols = list(self.payload.keys())
            set_clause = ", ".join([f"{c} = ?" for c in cols])
            vals = [json.dumps(self.payload[c], ensure_ascii=False) if isinstance(self.payload[c], (list, dict)) else self.payload[c] for c in cols]
            where_sql, where_params = self._build_where()
            sql = f"UPDATE {self.table_name} SET {set_clause}{where_sql} RETURNING *"
            res = self.client.execute_sql(sql, vals + where_params)
            return TursoResponse(res)

        elif self.action == "delete":
            where_sql, where_params = self._build_where()
            sql = f"DELETE FROM {self.table_name}{where_sql}"
            self.client.execute_sql(sql, where_params)
            return TursoResponse([])

class TursoClient:
    def __init__(self, url: str, token: str):
        if url and url.startswith("libsql://"):
            url = url.replace("libsql://", "https://")
        self.url = url.rstrip("/") if url else ""
        self.token = token
        self.session = requests.Session()
        if token:
            self.session.headers.update({
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            })

    def table(self, table_name: str):
        return TursoQueryBuilder(self, table_name)

    def _encode_arg(self, val):
        if val is None:
            return {"type": "null"}
        elif isinstance(val, bool):
            return {"type": "integer", "value": "1" if val else "0"}
        elif isinstance(val, int):
            return {"type": "integer", "value": str(val)}
        elif isinstance(val, float):
            return {"type": "float", "value": val}
        else:
            return {"type": "text", "value": str(val)}

    def _decode_val(self, cell, col_name=""):
        t = cell.get("type")
        v = cell.get("value")
        if t == "null" or v is None:
            return None
        if t == "integer":
            return int(v)
        if t == "float":
            return float(v)
        if col_name == "items" and isinstance(v, str):
            try:
                return json.loads(v)
            except:
                return []
        return v

    def execute_sql(self, sql: str, params=None):
        if not self.url:
            raise RuntimeError("尚未設定 TURSO_DATABASE_URL")
        stmt = {"sql": sql}
        if params:
            stmt["args"] = [self._encode_arg(p) for p in params]

        payload = {
            "requests": [
                {"type": "execute", "stmt": stmt},
                {"type": "close"}
            ]
        }
        resp = self.session.post(f"{self.url}/v2/pipeline", json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        first_res = data.get("results", [])[0]
        if first_res.get("type") == "error":
            err_msg = first_res.get("error", {}).get("message", "Unknown Turso SQL Error")
            raise RuntimeError(f"Turso SQL Error: {err_msg} | SQL: {sql}")

        exec_res = first_res.get("response", {}).get("result", {})
        cols = [c.get("name") for c in exec_res.get("cols", [])]
        rows = []
        for r in exec_res.get("rows", []):
            row_dict = {}
            for col_name, cell in zip(cols, r):
                row_dict[col_name] = self._decode_val(cell, col_name)
            rows.append(row_dict)
        return rows

def get_supabase_client() -> TursoClient:
    url = os.getenv("TURSO_DATABASE_URL")
    token = os.getenv("TURSO_AUTH_TOKEN")
    if not url or not token:
        print("⚠️ 警告: 尚未設定 TURSO_DATABASE_URL 或 TURSO_AUTH_TOKEN")
    return TursoClient(url, token)

def init_db():
    client = get_supabase_client()
    if not client.url:
        return
    try:
        # 自動建立 SQLite / Turso 相容資料表
        client.execute_sql("""
            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                line_group_id TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        client.execute_sql("""
            CREATE TABLE IF NOT EXISTS group_members (
                group_id INTEGER REFERENCES groups(id),
                user_id TEXT,
                user_name TEXT,
                PRIMARY KEY (group_id, user_id)
            )
        """)
        client.execute_sql("""
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER DEFAULT 0,
                expense_date TEXT NOT NULL,
                category TEXT DEFAULT '一般支出',
                description TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'TWD',
                exchange_rate REAL DEFAULT 1.0,
                payer_id TEXT NOT NULL,
                image_path TEXT,
                notes TEXT,
                items TEXT DEFAULT '[]',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        client.execute_sql("""
            CREATE TABLE IF NOT EXISTS expense_splits (
                expense_id INTEGER REFERENCES expenses(id) ON DELETE CASCADE,
                user_id TEXT,
                owed_amount REAL,
                PRIMARY KEY (expense_id, user_id)
            )
        """)
        client.execute_sql("""
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER,
                user_id TEXT,
                action_type TEXT,
                target_name TEXT,
                details TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # 確保預設群組存在
        client.execute_sql("INSERT OR IGNORE INTO groups (id, name) VALUES (1, '預設個人/群組')")
        client.execute_sql("INSERT OR IGNORE INTO group_members (group_id, user_id, user_name) VALUES (1, 'me', '我')")
        print("✅ 已成功連接並初始化 Turso (libSQL) 雲端資料庫！")
    except Exception as e:
        print(f"❌ Turso 資料庫初始化失敗: {e}")

if __name__ == '__main__':
    init_db()