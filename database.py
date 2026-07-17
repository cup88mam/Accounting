import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

def get_supabase_client() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        print("警告: 尚未設定 SUPABASE_URL 或 SUPABASE_KEY")
    return create_client(url, key)

def init_db():
    print("已成功連接 Supabase 雲端資料庫！")

if __name__ == '__main__':
    init_db()