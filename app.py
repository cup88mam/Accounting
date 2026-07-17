import os
import time
import base64
import requests
import uvicorn
from fastapi import FastAPI, Request, HTTPException, Header, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, FlexSendMessage, JoinEvent, FollowEvent, TextSendMessage
from main_mistral import process_receipt
from database import get_supabase_client, init_db

load_dotenv()
app = FastAPI()
init_db()
supabase = get_supabase_client()
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# 為了確保在沒有上傳圖片時不報錯，保留 static 路徑設定
os.makedirs("static/uploads", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(content=b"", media_type="image/x-icon")

class SplitDetail(BaseModel):
    user_id: str
    owed_amount: float

class ExpenseData(BaseModel):
    group_id: int
    expense_date: str
    category: str
    description: str
    notes: str = ""
    amount: float
    currency: str
    exchange_rate: float
    payer_id: str
    image_path: Optional[str] = None
    splits: List[SplitDetail]
    current_user_name: str = "某人"

class MemberData(BaseModel):
    user_id: str
    user_name: str

class GroupCreate(BaseModel):
    name: str
    user_id: str
    user_name: str

class LineGroupBind(BaseModel):
    line_group_id: str
    user_id: str
    user_name: str

@app.get("/")
async def read_index():
    return FileResponse("static/index.html")

def log_activity(group_id, user_name, action_type, target_name, details=""):
    supabase.table("activities").insert({
        "group_id": group_id, "user_id": user_name,
        "action_type": action_type, "target_name": target_name, "details": details
    }).execute()

def calculate_debts(group_id):
    # 1. 計算每個人付了多少錢
    exp_res = supabase.table('expenses').select('id, payer_id, amount, exchange_rate').eq('group_id', group_id).execute()
    paid = {}
    expense_ids = []
    for e in exp_res.data:
        paid[e['payer_id']] = paid.get(e['payer_id'], 0) + (e['amount'] * e['exchange_rate'])
        expense_ids.append(e['id'])

    # 2. 計算每個人應該分攤多少錢
    owed = {}
    if expense_ids:
        splits_res = supabase.table('expense_splits').select('user_id, owed_amount, expense_id').in_('expense_id', expense_ids).execute()
        rates = {e['id']: e['exchange_rate'] for e in exp_res.data}
        for s in splits_res.data:
            rate = rates.get(s['expense_id'], 1.0)
            owed[s['user_id']] = owed.get(s['user_id'], 0) + (s['owed_amount'] * rate)

    # 3. 計算淨值 (正數為債權人，負數為債務人)
    balances = {}
    all_users = set(paid.keys()).union(set(owed.keys()))
    for u in all_users:
        balances[u] = paid.get(u, 0) - owed.get(u, 0)

    debtors, creditors = [], []
    for u, bal in balances.items():
        if bal < -0.01: debtors.append([u, -bal])
        elif bal > 0.01: creditors.append([u, bal])
        
    debtors.sort(key=lambda x: x[1], reverse=True)
    creditors.sort(key=lambda x: x[1], reverse=True)

    # 4. 結算演算法
    transactions = []
    i, j = 0, 0
    while i < len(debtors) and j < len(creditors):
        debtor, debt_amt = debtors[i]
        creditor, cred_amt = creditors[j]
        
        settle_amt = min(debt_amt, cred_amt)
        transactions.append({"from": debtor, "to": creditor, "amount": round(settle_amt, 0)})
        
        debtors[i][1] -= settle_amt
        creditors[j][1] -= settle_amt
        
        if debtors[i][1] < 0.01: i += 1
        if creditors[j][1] < 0.01: j += 1
        
    return balances, transactions

@app.get("/api/groups/{group_id}/dashboard")
async def get_dashboard(group_id: int):
    # 總花費
    exp_res = supabase.table('expenses').select('amount, exchange_rate').eq('group_id', group_id).execute()
    total_expense = sum([(e['amount'] * e['exchange_rate']) for e in exp_res.data])
    
    # 取得成員名單
    mem_res = supabase.table('group_members').select('user_id, user_name').eq('group_id', group_id).execute()
    member_names = {m['user_id']: m['user_name'] for m in mem_res.data}
    
    balances, debts = calculate_debts(group_id)
    
    # 近期活動
    act_res = supabase.table('activities').select('*').eq('group_id', group_id).order('created_at', desc=True).limit(10).execute()
    
    return {
        "total_expense": round(total_expense, 0),
        "balances": {member_names.get(k, k): round(v, 0) for k, v in balances.items()},
        "debts": [{"from": member_names.get(d['from'], d['from']), "to": member_names.get(d['to'], d['to']), "amount": d['amount']} for d in debts],
        "activities": act_res.data,
        "members_map": member_names
    }

@app.get("/api/analytics")
async def get_analytics(user_id: str, mode: str = 'personal', group_id: int = 1):
    if mode == 'group':
        res = supabase.table('expenses').select('expense_date, category, amount, exchange_rate').eq('group_id', group_id).neq('category', '轉帳').execute()
        return [{"date": r['expense_date'], "category": r['category'], "cost": r['amount'] * r['exchange_rate']} for r in res.data]
    else:
        splits_res = supabase.table('expense_splits').select('owed_amount, expense_id').eq('user_id', user_id).execute()
        if not splits_res.data:
            return []
            
        exp_ids = [s['expense_id'] for s in splits_res.data]
        exp_res = supabase.table('expenses').select('id, expense_date, category, exchange_rate').in_('id', exp_ids).neq('category', '轉帳').execute()
        exp_dict = {e['id']: e for e in exp_res.data}

        data = []
        for s in splits_res.data:
            e = exp_dict.get(s['expense_id'])
            if e:
                data.append({
                    "date": e['expense_date'],
                    "category": e['category'],
                    "cost": s['owed_amount'] * e['exchange_rate']
                })
        return data

@app.post("/callback")
async def callback(request: Request, x_line_signature: str = Header(None)):
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return {"status": "ok"}

# === 功能一：剛加入聊天室/群組時，自動提供使用提示（升級為按鈕卡片版） ===
@handler.add(JoinEvent)
def handle_join(event):
    flex_welcome = FlexSendMessage(
        alt_text="👊🏿 討債工讀生來囉！🤜🏿",
        contents={
            "type": "bubble",
            "size": "kilo",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "歡迎使用討債工讀生！🤜🏿",
                        "weight": "bold",
                        "size": "lg",
                        "color": "#fbc02d"
                    },
                    {
                        "type": "text",
                        "text": "大家好！我是討債工讀生🤜🏿，很高興能加入這個群組幫大家輕鬆分帳 ✨\n\n💡 快速使用提示：\n請直接點擊下方按鈕，我會立刻為大家送出功能目錄選單卡片喔！",
                        "size": "sm",
                        "color": "#ffffff",
                        "margin": "md",
                        "wrap": True  # 允許文字自動換行
                    }
                ],
                "backgroundColor": "#1e1e1e"
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "height": "sm",
                        "color": "#fbc02d",
                        "action": {
                            "type": "message",        # 類型設定為 message
                            "label": "✨ 點我呼叫功能選單", # 按鈕文字
                            "text": "選單"             # 點擊後自動發送的字串
                        }
                    }
                ],
                "backgroundColor": "#1e1e1e"
            }
        }
    )
    line_bot_api.reply_message(event.reply_token, flex_welcome)

@handler.add(FollowEvent)
def handle_follow(event):
    flex_welcome = FlexSendMessage(
        alt_text="👊🏿 討債工讀生來囉！🤜🏿",
        contents={
            "type": "bubble",
            "size": "kilo",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "👊🏿 嗨！我是討債工讀生！🤜🏿",
                        "weight": "bold",
                        "size": "lg",
                        "color": "#fbc02d"
                    },
                    {
                        "type": "text",
                        "text": "感謝你將我加入好友 ✨\n\n💡 快速使用提示：\n把你跟朋友常用的 LINE 群組拉我進去，大家就能一起記帳！現在可以點擊下方按鈕測試呼叫選單功能：",
                        "size": "sm",
                        "color": "#ffffff",
                        "margin": "md",
                        "wrap": True
                    }
                ],
                "backgroundColor": "#1e1e1e"
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "height": "sm",
                        "color": "#fbc02d",
                        "action": {
                            "type": "message",
                            "label": "✨ 點我呼叫功能選單",
                            "text": "選單"
                        }
                    }
                ],
                "backgroundColor": "#1e1e1e"
            }
        }
    )
    line_bot_api.reply_message(event.reply_token, flex_welcome)


# === 功能二：回傳含有 [首頁/紀錄/新增支出/分析] 4 個按鈕的選單 ===
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text
    
    if msg == "記帳" or msg == "選單":
        # 定義你的基礎 LIFF 網址
        base_liff_url = "https://liff.line.me/2010733190-GepcYGbG"
        
        flex_message = FlexSendMessage(
            alt_text="功能選單目錄來囉！",
            contents={
                "type": "bubble",
                "size": "kilo",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {
                            "type": "text",
                            "text": "👊🏿討債工讀生選單👊🏿",
                            "weight": "bold",
                            "size": "lg",
                            "color": "#fbc02d"
                        },
                        {
                            "type": "text",
                            "text": "請選擇欲前往的記帳頁面：",
                            "size": "xs",
                            "color": "#aaaaaa",
                            "margin": "xs"
                        }
                    ],
                    "backgroundColor": "#1e1e1e"
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "contents": [
                        {
                            "type": "button",
                            "style": "primary",
                            "height": "sm",
                            "color": "#fbc02d",
                            "action": {
                                "type": "uri",
                                "label": "🏠 前往首頁 (群組總覽)",
                                "uri": f"{base_liff_url}?view=dashboard"
                            }
                        },
                        {
                            "type": "button",
                            "style": "primary",
                            "height": "sm",
                            "color": "#444444",
                            "action": {
                                "type": "uri",
                                "label": "📃 查看所有紀錄",
                                "uri": f"{base_liff_url}?view=records"
                            }
                        },
                        {
                            "type": "button",
                            "style": "primary",
                            "height": "sm",
                            "color": "#fbc02d",
                            "action": {
                                "type": "uri",
                                "label": "➕ 新增支出 (AI 辨識)",
                                "uri": f"{base_liff_url}?view=form"
                            }
                        },
                        {
                            "type": "button",
                            "style": "primary",
                            "height": "sm",
                            "color": "#444444",
                            "action": {
                                "type": "uri",
                                "label": "📊 消費數據分析",
                                "uri": f"{base_liff_url}?view=analytics"
                            }
                        }
                    ],
                    "backgroundColor": "#1e1e1e"
                }
            }
        )
        line_bot_api.reply_message(event.reply_token, flex_message)

@app.post("/api/groups")
async def bind_line_group(data: LineGroupBind):
    # 使用 LINE 的群組 ID 產生一個獨一無二的內部名稱
    group_name = f"LINE_{data.line_group_id}"
    try:
        # 尋找是否已經有這個聊天室專屬的記帳群組
        res = supabase.table('groups').select('id').eq('name', group_name).execute()
        if res.data:
            group_id = res.data[0]['id']
        else:
            # 如果沒有，就自動建立一個
            ins = supabase.table('groups').insert({'name': group_name}).execute()
            group_id = ins.data[0]['id']
            
        # 檢查該點擊的用戶是否已經在群組內
        mem_res = supabase.table('group_members').select('*').eq('group_id', group_id).eq('user_id', data.user_id).execute()
        if not mem_res.data:
            # 不在裡面就自動加進去
            supabase.table('group_members').insert({
                'group_id': group_id, 'user_id': data.user_id, 'user_name': data.user_name
            }).execute()
            log_activity(group_id, data.user_name, 'join', '透過 LINE 聊天室自動加入')
            
        return {"status": "success", "group_id": group_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
async def create_group(data: GroupCreate):
    try:
        res = supabase.table('groups').insert({'name': data.name}).execute()
        group_id = res.data[0]['id']
        supabase.table('group_members').insert({'group_id': group_id, 'user_id': data.user_id, 'user_name': data.user_name}).execute()
        log_activity(group_id, data.user_name, 'join', '建立並加入了群組')
        return {"status": "success", "group_id": group_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/groups")
async def get_groups(user_id: str = None):
    if user_id:
        mem_res = supabase.table('group_members').select('group_id').eq('user_id', user_id).execute()
        g_ids = [m['group_id'] for m in mem_res.data]
        if g_ids:
            return supabase.table('groups').select('id, name').in_('id', g_ids).execute().data
        return []
    else:
        return supabase.table('groups').select('id, name').execute().data

@app.post("/api/groups/{group_id}/members")
async def add_member(group_id: int, member: MemberData):
    try:
        exists = supabase.table("group_members").select("*").eq("group_id", group_id).eq("user_id", member.user_id).execute()
        if not exists.data:
            supabase.table("group_members").insert({
                "group_id": group_id, "user_id": member.user_id, "user_name": member.user_name
            }).execute()
            log_activity(group_id, member.user_name, 'join', '加入了群組')
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/groups/{group_id}/members")
async def get_members(group_id: int):
    return supabase.table("group_members").select("user_id, user_name").eq("group_id", group_id).execute().data

@app.post("/api/scan-receipt")
async def scan_receipt(file: UploadFile = File(...)):
    temp_file_path = f"temp_scan_{file.filename}"
    with open(temp_file_path, "wb") as buffer:
        buffer.write(await file.read())
    try:
        result = process_receipt(temp_file_path)
        if isinstance(result, dict) and "error" in result: raise HTTPException(status_code=500, detail=result["error"])
        return result
    finally:
        if os.path.exists(temp_file_path): os.remove(temp_file_path)

@app.post("/api/upload-image")
async def upload_image(file: UploadFile = File(...)):
    api_key = os.getenv("IMGBB_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="Missing ImgBB API Key")

    image_content = await file.read()
    b64_image = base64.b64encode(image_content).decode('utf-8')
    
    try:
        response = requests.post("https://api.imgbb.com/1/upload", data={"key": api_key, "image": b64_image})
        result = response.json()
        if result.get("success"):
            return {"image_path": result["data"]["display_url"]}
        else:
            raise HTTPException(status_code=400, detail="ImgBB 上傳失敗")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/add_expense")
async def add_expense(data: ExpenseData):
    try:
        expense_data = {
            "group_id": data.group_id, "expense_date": data.expense_date, "category": data.category,
            "description": data.description, "notes": data.notes, "amount": data.amount,
            "currency": data.currency, "exchange_rate": data.exchange_rate, "payer_id": data.payer_id,
            "image_path": data.image_path
        }
        exp_res = supabase.table("expenses").insert(expense_data).execute()
        expense_id = exp_res.data[0]["id"]
        
        splits_data = [{"expense_id": expense_id, "user_id": s.user_id, "owed_amount": s.owed_amount} for s in data.splits]
        supabase.table("expense_splits").insert(splits_data).execute()
        
        log_activity(data.group_id, data.current_user_name, 'add', data.description, f"{data.currency} {data.amount}")
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/expenses")
async def get_expenses(group_id: int = 1):
    exp_res = supabase.table("expenses").select("*").eq("group_id", group_id).order("expense_date", desc=True).execute()
    mem_res = supabase.table("group_members").select("user_id, user_name").eq("group_id", group_id).execute()
    members_map = {m['user_id']: m['user_name'] for m in mem_res.data}
    
    results = []
    for e in exp_res.data:
        e['payer_name'] = members_map.get(e['payer_id'], e['payer_id'])
        results.append(e)
    return results

@app.get("/api/expenses/{expense_id}")
async def get_single_expense(expense_id: int):
    exp_res = supabase.table("expenses").select("*").eq("id", expense_id).execute()
    if not exp_res.data:
        raise HTTPException(status_code=404, detail="Expense not found")
    row = exp_res.data[0]
    
    splits_res = supabase.table("expense_splits").select("user_id, owed_amount").eq("expense_id", expense_id).execute()
    row['splits'] = splits_res.data
    return row

@app.put("/api/expenses/{expense_id}")
async def update_expense(expense_id: int, data: ExpenseData):
    try:
        expense_data = {
            "expense_date": data.expense_date, "category": data.category, "description": data.description,
            "notes": data.notes, "amount": data.amount, "currency": data.currency,
            "exchange_rate": data.exchange_rate, "payer_id": data.payer_id, "image_path": data.image_path
        }
        supabase.table("expenses").update(expense_data).eq("id", expense_id).execute()
        
        supabase.table("expense_splits").delete().eq("expense_id", expense_id).execute()
        splits_data = [{"expense_id": expense_id, "user_id": s.user_id, "owed_amount": s.owed_amount} for s in data.splits]
        supabase.table("expense_splits").insert(splits_data).execute()
            
        log_activity(data.group_id, data.current_user_name, 'update', data.description)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/expenses/{expense_id}")
async def delete_expense(expense_id: int, group_id: int, user_name: str, description: str):
    try:
        supabase.table("expense_splits").delete().eq("expense_id", expense_id).execute()
        supabase.table("expenses").delete().eq("id", expense_id).execute()
        log_activity(group_id, user_name, 'delete', description)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)