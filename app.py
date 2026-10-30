import re
from datetime import datetime
import os
import time
import base64
import asyncio
import requests
import uvicorn
from urllib.parse import parse_qsl
from fastapi import FastAPI, Request, HTTPException, Header, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, ImageMessage, FlexSendMessage, 
    JoinEvent, FollowEvent, TextSendMessage, PostbackEvent
)
from main_mistral import process_receipt
from database import get_supabase_client, init_db

load_dotenv()
app = FastAPI()
init_db()
supabase = get_supabase_client()
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))
pending_receipts = {}

os.makedirs("static/uploads", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
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
    items: Optional[list] = []
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

class GroupUpdate(BaseModel):
    name: str

@app.get("/")
def read_index():
    return FileResponse("static/index.html")

def log_activity(group_id, user_name, action_type, target_name, details=""):
    supabase.table("activities").insert({
        "group_id": group_id, "user_id": user_name,
        "action_type": action_type, "target_name": target_name, "details": details
    }).execute()

def merge_virtual_member_if_exists(group_id: int, real_user_id: str, user_name: str):
    if real_user_id.startswith("virtual_"):
        return False

    res = supabase.table("group_members").select("user_id").eq("group_id", group_id).ilike("user_name", user_name.strip()).like("user_id", "virtual_%").execute()
    
    if res.data:
        virtual_id = res.data[0]["user_id"]
        supabase.table("expenses").update({"payer_id": real_user_id}).eq("group_id", group_id).eq("payer_id", virtual_id).execute()
        supabase.table("expense_splits").update({"user_id": real_user_id}).eq("user_id", virtual_id).execute()
        supabase.table("activities").update({"user_id": real_user_id}).eq("group_id", group_id).eq("user_id", virtual_id).execute()
        supabase.table("group_members").delete().eq("group_id", group_id).eq("user_id", virtual_id).execute()
        return True
        
    return False

def calculate_debts(group_id):
    exp_res = supabase.table('expenses').select('id, payer_id, amount, exchange_rate').eq('group_id', group_id).execute()
    paid = {}
    expense_ids = []
    for e in exp_res.data:
        paid[e['payer_id']] = paid.get(e['payer_id'], 0) + (e['amount'] * e['exchange_rate'])
        expense_ids.append(e['id'])

    owed = {}
    if expense_ids:
        splits_res = supabase.table('expense_splits').select('user_id, owed_amount, expense_id').in_('expense_id', expense_ids).execute()
        rates = {e['id']: e['exchange_rate'] for e in exp_res.data}
        for s in splits_res.data:
            rate = rates.get(s['expense_id'], 1.0)
            owed[s['user_id']] = owed.get(s['user_id'], 0) + (s['owed_amount'] * rate)

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
def get_dashboard(group_id: int):
    # 群組總計排除「轉帳」還款金額
    exp_res = supabase.table('expenses').select('amount, exchange_rate, category').eq('group_id', group_id).neq('category', '轉帳').execute()
    total_expense = sum([(e['amount'] * e['exchange_rate']) for e in exp_res.data])
    
    mem_res = supabase.table('group_members').select('user_id, user_name').eq('group_id', group_id).execute()
    member_names = {m['user_id']: m['user_name'] for m in mem_res.data}
    
    balances, debts = calculate_debts(group_id)
    act_res = supabase.table('activities').select('*').eq('group_id', group_id).order('created_at', desc=True).limit(10).execute()
    
    return {
        "total_expense": round(total_expense, 0),
        "balances": {member_names.get(k, k): round(v, 0) for k, v in balances.items()},
        "debts": [{"from": member_names.get(d['from'], d['from']), "to": member_names.get(d['to'], d['to']), "amount": d['amount']} for d in debts],
        "activities": act_res.data,
        "members_map": member_names
    }

@app.get("/api/analytics")
def get_analytics(user_id: str, mode: str = 'personal', group_id: int = 1):
    if mode == 'group':
        res = supabase.table('expenses').select('expense_date, category, amount, exchange_rate').eq('group_id', group_id).neq('category', '轉帳').execute()
        return [{"date": r['expense_date'], "category": r['category'], "cost": r['amount'] * r['exchange_rate']} for r in res.data]
    else:
        group_exp_res = supabase.table('expenses').select('id, expense_date, category, exchange_rate').eq('group_id', group_id).neq('category', '轉帳').execute()
        if not group_exp_res.data:
            return []
            
        group_exp_dict = {e['id']: e for e in group_exp_res.data}
        group_exp_ids = list(group_exp_dict.keys())
        
        splits_res = supabase.table('expense_splits').select('owed_amount, expense_id').eq('user_id', user_id).in_('expense_id', group_exp_ids).execute()
        
        data = []
        for s in splits_res.data:
            e = group_exp_dict.get(s['expense_id'])
            if e:
                data.append({
                    "date": e['expense_date'],
                    "category": e['category'],
                    "cost": s['owed_amount'] * e['exchange_rate']
                })
        return data

# ================= 成員管理 Pydantic 結構 =================
class GroupMemberCreate(BaseModel):
    group_id: int = 1
    user_id: str
    user_name: str

class GroupMemberUpdate(BaseModel):
    user_name: str
# ================= 成員管理 API =================
@app.get("/api/groups/{group_id}/members")
def get_group_members(group_id: int):
    res = supabase.table("group_members").select("*").eq("group_id", group_id).execute()
    return res.data

@app.post("/api/group_members")
def add_group_member(member: GroupMemberCreate):
    existing = supabase.table("group_members").select("*").eq("group_id", member.group_id).eq("user_id", member.user_id).execute()
    if existing.data:
        return {"status": "error", "message": "此成員 ID 已存在"}
    
    res = supabase.table("group_members").insert({
        "group_id": member.group_id,
        "user_id": member.user_id,
        "user_name": member.user_name
    }).execute()
    return {"status": "success", "data": res.data}

@app.put("/api/group_members/{group_id}/{user_id}")
def update_group_member(group_id: int, user_id: str, data: GroupMemberUpdate):
    res = supabase.table("group_members").update({
        "user_name": data.user_name
    }).eq("group_id", group_id).eq("user_id", user_id).execute()
    return {"status": "success", "data": res.data}

@app.delete("/api/group_members/{group_id}/{user_id}")
def delete_group_member(group_id: int, user_id: str):
    has_payer = supabase.table("expenses").select("id").eq("group_id", group_id).eq("payer_id", user_id).execute()
    has_splits = supabase.table("expense_splits").select("expense_id").eq("user_id", user_id).execute()
    
    if has_payer.data or has_splits.data:
        return {
            "status": "error", 
            "message": "無法刪除：該成員已有相關記帳或分攤帳目紀錄。若刪除會導致分帳報表失真。"
        }

    supabase.table("group_members").delete().eq("group_id", group_id).eq("user_id", user_id).execute()
    return {"status": "success"}

# ================= LINE Webhook 回調 =================
@app.post("/callback")
def callback(request: Request, x_line_signature: str = Header(None)):
    body = asyncio.run(request.body())
    try:
        handler.handle(body.decode("utf-8"), x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return {"status": "ok"}

# 🌟 新增：處理點擊對話選單中的 Postback 加入事件
@handler.add(PostbackEvent)
def handle_postback(event):
    data = event.postback.data
    params = dict(parse_qsl(data))
    
    if params.get("action") == "join_group":
        line_id = event.source.group_id if event.source.type == "group" else getattr(event.source, 'room_id', None)
        user_id = event.source.user_id
        target_group_id = int(params.get("group_id", 0))
        
        group_name = "本群組"
        if target_group_id:
            g_res = supabase.table('groups').select('id, name').eq('id', target_group_id).execute()
            if g_res.data:
                group_name = g_res.data[0]['name']
        
        user_name = "新朋友"
        try:
            if line_id:
                p = line_bot_api.get_group_member_profile(line_id, user_id)
            else:
                p = line_bot_api.get_profile(user_id)
            user_name = p.display_name
        except Exception as e:
            print(f"無法取得使用者資料: {e}")
            
        if not target_group_id:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 無法辨識群組 ID，請重新發送「選單」嘗試！"))
            return

        # 檢查是否已在名單中
        mem_res = supabase.table('group_members').select('*').eq('group_id', target_group_id).eq('user_id', user_id).execute()
        if mem_res.data:
            line_bot_api.reply_message(
                event.reply_token, 
                TextSendMessage(text=f"💡 @{user_name} 你已經在「{group_name}」記帳名單中囉！分帳時大家可以直接選你。")
            )
            return

        # 寫入群組成員
        supabase.table('group_members').insert({
            'group_id': target_group_id,
            'user_id': user_id,
            'user_name': user_name
        }).execute()

        # 自動融合可能存在的同名虛擬帳號
        merged = merge_virtual_member_if_exists(target_group_id, user_id, user_name)

        action_detail = '點擊選單加入了群組 (並自動融合同名虛擬替身)' if merged else '點擊選單加入了群組'
        log_activity(target_group_id, user_name, 'join', action_detail)

        reply_msg = f"🎉 成功加入！\n【{user_name}】已加入「{group_name}」記帳名單！"
        if merged:
            reply_msg += "\n⚡ 偵測到同名虛擬帳號，已自動將歷史帳目合併到你的帳號！"
        reply_msg += "\n之後大家記帳、分攤就能直接指名你囉 💰"

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

@handler.add(JoinEvent)
def handle_join(event):
    line_id = event.source.group_id if event.source.type == "group" else getattr(event.source, 'room_id', None)
    db_group_id = None
    if line_id:
        res = supabase.table('groups').select('id').eq('line_group_id', line_id).execute()
        if res.data:
            db_group_id = res.data[0]['id']
        else:
            ins = supabase.table('groups').insert({'name': '💬 聊天室專屬群組', 'line_group_id': line_id}).execute()
            db_group_id = ins.data[0]['id']

    # 新人歡迎卡片：加入綠色的「點我加入記帳」按鍵
    buttons = []
    if db_group_id:
        buttons.append({
            "type": "button",
            "style": "primary",
            "height": "sm",
            "color": "#06C755",
            "action": {
                "type": "postback",
                "label": "🙋‍♂️ 點我加入記帳成員",
                "data": f"action=join_group&group_id={db_group_id}",
                "displayText": "🙋‍♂️ 我要加入記帳名單！"
            }
        })
    buttons.extend([
        {
            "type": "button",
            "style": "primary",
            "height": "sm",
            "color": "#fbc02d",
            "action": { "type": "message", "label": "🔫 點我呼叫討債選單", "text": "選單" }
        },
        {
            "type": "button",
            "style": "secondary",
            "height": "sm",
            "color": "#444444",
            "action": { "type": "message", "label": "📖 快速指令教學", "text": "教學" }
        }
    ])

    flex_welcome = FlexSendMessage(
        alt_text="👊🏿 討債工讀生來囉！🤜🏿",
        contents={
            "type": "bubble",
            "size": "kilo",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    { "type": "text", "text": "歡迎使用討債工讀生！🤜🏿", "weight": "bold", "size": "lg", "color": "#fbc02d" },
                    {
                        "type": "text",
                        "text": "大家好！我是討債工讀生🤜🏿，很高興能加入這個群組幫大家輕鬆討債 💰\n\n🔫 快速使用提示：\n請群組內成員直接點擊下方【綠色按鈕】一鍵登記加入分帳名單，即可輕鬆開始分帳！",
                        "size": "sm", "color": "#ffffff", "margin": "md", "wrap": True
                    }
                ],
                "backgroundColor": "#1e1e1e"
            },
            "footer": {
                "type": "box", "layout": "vertical", "spacing": "sm", "contents": buttons, "backgroundColor": "#1e1e1e"
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
                    { "type": "text", "text": "👊🏿 嗨！我是討債工讀生！🤜🏿", "weight": "bold", "size": "lg", "color": "#fbc02d" },
                    {
                        "type": "text",
                        "text": "感謝你將我加入好友 ✨\n\n💡 快速使用提示：\n把你跟朋友常用的 LINE 群組拉我進去，大家就能一起記帳！現在可以點擊下方按鈕測試功能：",
                        "size": "sm", "color": "#ffffff", "margin": "md", "wrap": True
                    }
                ],
                "backgroundColor": "#1e1e1e"
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    { "type": "button", "style": "primary", "height": "sm", "color": "#fbc02d", "action": { "type": "message", "label": "🔫 點我呼叫討債選單", "text": "選單" } },
                    { "type": "button", "style": "secondary", "height": "sm", "color": "#444444", "action": { "type": "message", "label": "📖 快速指令教學", "text": "教學" } }
                ],
                "backgroundColor": "#1e1e1e"
            }
        }
    )
    line_bot_api.reply_message(event.reply_token, flex_welcome)

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    line_id = event.source.group_id if event.source.type == "group" else event.source.room_id if event.source.type == "room" else None
    if not line_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 討債工讀生：請在群組內上傳圖片喔！"))
        return

    message_content = line_bot_api.get_message_content(event.message.id)
    temp_path = f"temp_{event.message.id}.jpg"
    with open(temp_path, 'wb') as fd:
        for chunk in message_content.iter_content():
            fd.write(chunk)
    
    try:
        ai_data = process_receipt(temp_path)
        if "error" in ai_data:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 辨識失敗：{ai_data['error']}"))
            return
        
        with open(temp_path, "rb") as file:
            b64_image = base64.b64encode(file.read()).decode('utf-8')
        try:
            imgbb_res = requests.post(
                "https://api.imgbb.com/1/upload",
                data={"key": os.getenv("IMGBB_API_KEY"), "image": b64_image},
                timeout=8
            )
            img_url = imgbb_res.json().get("data", {}).get("display_url", "") if imgbb_res.status_code == 200 else ""
        except Exception as img_err:
            print(f"ImgBB 上傳失敗或連線逾時：{img_err}")
            img_url = ""

        pending_receipts[line_id] = {
            "ai_data": ai_data,
            "img_url": img_url
        }

        reply = (
            f"📸 討債工讀生看懂了！\n"
            f"✅ 項目：{ai_data.get('description', '未命名')}\n"
            f"💰 總額：{ai_data.get('amount', 0):g} {ai_data.get('currency', 'TWD')}\n"
            f"📅 日期：{ai_data.get('date', '')}\n\n"
            f"👉🏿 這筆錢要怎麼算？請直接回覆我：\n"
            f"1️⃣ 回覆【平分】(全群幫你平分)\n"
            f"2️⃣ 或用原指令 (例: @小龍 欠我 {ai_data.get('amount', 0):g})\n"
            f"3️⃣ 回覆【取消】不記這筆"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 發生錯誤：{str(e)}"))
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text.strip()
    
    if msg in ["教學", "指令", "快速指令"]:
        tutorial_flex = FlexSendMessage(
            alt_text="快速記帳指令教學",
            contents={
                "type": "bubble",
                "size": "mega",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        { "type": "text", "text": "🔫 討債快速指令教學", "weight": "bold", "size": "xl", "color": "#fbc02d" },
                        { "type": "separator", "margin": "md" },
                        { "type": "text", "text": "【新增單人欠款 / 還款】", "weight": "bold", "color": "#ffffff", "margin": "md" },
                        { "type": "text", "text": "@小龍 欠我 車費 200\n我欠 @小龍 車費 200\n@小龍 @小恩 各欠我 車費 200\n@小龍 欠 @小恩 早餐 300\n\n@小龍 還我 200\n我還 @小龍 200", "size": "sm", "color": "#aaaaaa", "wrap": True },
                        { "type": "separator", "margin": "md" },
                        { "type": "text", "text": "【新增平分帳款】(全群平分)", "weight": "bold", "color": "#ffffff", "margin": "md" },
                        { "type": "text", "text": "自己付 👉 帳款名 金額\n(例：午餐 300)\n\n他人付 👉 @名字 帳款名 金額\n(例：@小明 午餐 300)", "size": "sm", "color": "#aaaaaa", "wrap": True },
                        { "type": "separator", "margin": "md" },
                        { "type": "text", "text": "【支援多國幣別 (選填)】", "weight": "bold", "color": "#fbc02d", "margin": "md" },
                        { "type": "text", "text": "在金額後方加上幣別 (預設為台幣)\n支援：USD, 美金, JPY, 日幣, ¥\n\n範例：@小龍 午餐 10 USD\n範例：@小恩 欠我 門票 1500 日圓", "size": "sm", "color": "#aaaaaa", "wrap": True }
                    ],
                    "backgroundColor": "#1e1e1e"
                }
            }
        )
        line_bot_api.reply_message(event.reply_token, tutorial_flex)
        return

    line_id = None
    if event.source.type == "group": line_id = event.source.group_id
    elif event.source.type == "room": line_id = event.source.room_id
        
    db_group_id = None
    if line_id:
        res = supabase.table('groups').select('id').eq('line_group_id', line_id).execute()
        if res.data: db_group_id = res.data[0]['id']
        else:
            ins = supabase.table('groups').insert({'name': '💬 聊天室專屬群組', 'line_group_id': line_id}).execute()
            db_group_id = ins.data[0]['id']

    # --- 呼叫主選單：加入綠色「點我加入記帳」按鍵 ---
    if msg in ["記帳", "選單"]:
        base_liff_url = "https://liff.line.me/2010733190-GepcYGbG"
        invite_param = f"&invite_group={db_group_id}" if db_group_id else ""
        
        url_dash = f"{base_liff_url}?view=dashboard{invite_param}"
        url_rec = f"{base_liff_url}?view=records{invite_param}"
        url_form = f"{base_liff_url}?view=form{invite_param}"
        url_ana = f"{base_liff_url}?view=analytics{invite_param}"

        menu_buttons = []
        if db_group_id:
            menu_buttons.append({
                "type": "button",
                "style": "primary",
                "height": "sm",
                "color": "#06C755",
                "action": {
                    "type": "postback",
                    "label": "🙋‍♂️ 點我加入記帳成員",
                    "data": f"action=join_group&group_id={db_group_id}",
                    "displayText": "🙋‍♂️ 我要加入記帳名單！"
                }
            })

        menu_buttons.extend([
            { "type": "button", "style": "primary", "height": "sm", "color": "#fbc02d", "action": { "type": "uri", "label": "🏠 前往首頁 (群組總覽)", "uri": url_dash } },
            { "type": "button", "style": "primary", "height": "sm", "color": "#444444", "action": { "type": "uri", "label": "📃 查看所有紀錄", "uri": url_rec } },
            { "type": "button", "style": "primary", "height": "sm", "color": "#fbc02d", "action": { "type": "uri", "label": "➕ 新增支出 (AI 辨識)", "uri": url_form } },
            { "type": "button", "style": "primary", "height": "sm", "color": "#444444", "action": { "type": "uri", "label": "📊 消費數據分析", "uri": url_ana } }
        ])

        flex_menu = FlexSendMessage(
            alt_text="功能選單目錄來囉！",
            contents={
                "type": "bubble",
                "size": "kilo",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        { "type": "text", "text": "👊🏿 討債工讀生選單 🤜🏿", "weight": "bold", "size": "lg", "color": "#fbc02d" },
                        { "type": "text", "text": "請選擇欲前往的討債頁面或加入群組：", "size": "xs", "color": "#aaaaaa", "margin": "xs" }
                    ],
                    "backgroundColor": "#1e1e1e"
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "contents": menu_buttons,
                    "backgroundColor": "#1e1e1e"
                }
            }
        )
        line_bot_api.reply_message(event.reply_token, flex_menu)
        return

    if not db_group_id: return

    sender_id = event.source.user_id
    sender_name = "某人"
    try:
        if event.source.type == "group": sender_name = line_bot_api.get_group_member_profile(line_id, sender_id).display_name
        elif event.source.type == "room": sender_name = line_bot_api.get_room_member_profile(line_id, sender_id).display_name
        else: sender_name = line_bot_api.get_profile(sender_id).display_name
    except: pass

    mem_res = supabase.table('group_members').select('*').eq('group_id', db_group_id).eq('user_id', sender_id).execute()
    if not mem_res.data:
        supabase.table('group_members').insert({'group_id': db_group_id, 'user_id': sender_id, 'user_name': sender_name}).execute()
        
    merge_virtual_member_if_exists(db_group_id, sender_id, sender_name)

    event_mentions = getattr(event.message, 'mention', None)
    
    def resolve_member(raw_name_or_id: str, mention_idx: int = 0):
        if event_mentions and getattr(event_mentions, 'mentionees', None):
            mentionees = event_mentions.mentionees
            if mention_idx < len(mentionees):
                target_uid = getattr(mentionees[mention_idx], 'user_id', None)
                if target_uid:
                    mem = supabase.table('group_members').select('user_id').eq('group_id', db_group_id).eq('user_id', target_uid).execute()
                    if not mem.data:
                        try:
                            p = line_bot_api.get_group_member_profile(line_id, target_uid)
                            d_name = p.display_name
                        except:
                            d_name = raw_name_or_id.replace("@", "").strip() or "群組成員"
                        supabase.table('group_members').insert({'group_id': db_group_id, 'user_id': target_uid, 'user_name': d_name}).execute()
                    return target_uid

        clean_name = raw_name_or_id.replace("@", "").strip()
        res = supabase.table('group_members').select('user_id').eq('group_id', db_group_id).ilike('user_name', clean_name).execute()
        if res.data:
            return res.data[0]['user_id']

        vid = f"virtual_{int(time.time()*1000)}_{clean_name}"
        supabase.table('group_members').insert({'group_id': db_group_id, 'user_id': vid, 'user_name': clean_name}).execute()
        return vid

    def normalize_currency(raw_text):
        if not raw_text: return "TWD", 1.0
        text = raw_text.upper()
        if any(k in text for k in ['JPY', '日圓', '日幣', '￥', '¥']): return "JPY", 0.20
        if any(k in text for k in ['USD', '美金', '美元', 'US']): return "USD", 32.5
        return "TWD", 1.0

    pending_data = pending_receipts.get(line_id)
    if pending_data:
        if msg == "取消":
            del pending_receipts[line_id]
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🗑️ 討債工讀生：已將這張收據撕毀作廢！"))
            return
        elif msg == "平分":
            ai = pending_data["ai_data"]
            msg = f"{ai.get('description', '一般支出')} {ai.get('amount', 0)} {ai.get('currency', 'TWD')}"

    m_repay_1 = re.match(r'^@?(.+?)\s+還我\s+([0-9.]+)\s*([A-Za-z$¥￥\u4e00-\u9fa5]*)?$', msg)
    m_repay_2 = re.match(r'^我還\s+@?(.+?)\s+([0-9.]+)\s*([A-Za-z$¥￥\u4e00-\u9fa5]*)?$', msg)
    m_debt_1  = re.match(r'^@?(.+?)\s+欠我\s+(.+?)\s+([0-9.]+)\s*([A-Za-z$¥￥\u4e00-\u9fa5]*)?$', msg)
    m_debt_2  = re.match(r'^我欠\s+@?(.+?)\s+(.+?)\s+([0-9.]+)\s*([A-Za-z$¥￥\u4e00-\u9fa5]*)?$', msg)
    m_debt_3  = re.match(r'^@?(.+?)\s+@?(.+?)\s+各欠我\s+(.+?)\s+([0-9.]+)\s*([A-Za-z$¥￥\u4e00-\u9fa5]*)?$', msg)
    m_debt_4  = re.match(r'^@?(.+?)\s+欠\s+@?(.+?)\s+(.+?)\s+([0-9.]+)\s*([A-Za-z$¥￥\u4e00-\u9fa5]*)?$', msg)
    m_split_1 = re.match(r'^@(.+?)\s+(\S+)\s+([0-9.]+)\s*([A-Za-z$¥￥\u4e00-\u9fa5]*)?$', msg)
    m_split_2 = re.match(r'^([^@\s]\S*)\s+([0-9.]+)\s*([A-Za-z$¥￥\u4e00-\u9fa5]*)?$', msg)

    payer_id, category, desc, amount, splits = None, "一般支出", "", 0.0, []
    raw_currency = ""

    if m_repay_1:
        payer_id, amount, desc, category = resolve_member(m_repay_1.group(1), 0), float(m_repay_1.group(2)), "轉帳還款", "轉帳"
        raw_currency = m_repay_1.group(3)
        splits = [{"user_id": sender_id, "owed_amount": amount}]
    elif m_repay_2:
        payer_id, receiver_id, amount, desc, category = sender_id, resolve_member(m_repay_2.group(1), 0), float(m_repay_2.group(2)), "轉帳還款", "轉帳"
        raw_currency = m_repay_2.group(3)
        splits = [{"user_id": receiver_id, "owed_amount": amount}]
    elif m_debt_1:
        debtor_id, desc, amount, payer_id = resolve_member(m_debt_1.group(1), 0), m_debt_1.group(2), float(m_debt_1.group(3)), sender_id
        raw_currency = m_debt_1.group(4)
        splits = [{"user_id": debtor_id, "owed_amount": amount}]
    elif m_debt_2:
        payer_id, desc, amount, debtor_id = resolve_member(m_debt_2.group(1), 0), m_debt_2.group(2), float(m_debt_2.group(3)), sender_id
        raw_currency = m_debt_2.group(4)
        splits = [{"user_id": debtor_id, "owed_amount": amount}]
    elif m_debt_3:
        d1_id, d2_id, desc, each_amount = resolve_member(m_debt_3.group(1), 0), resolve_member(m_debt_3.group(2), 1), m_debt_3.group(3), float(m_debt_3.group(4))
        payer_id, amount = sender_id, each_amount * 2
        raw_currency = m_debt_3.group(5)
        splits = [{"user_id": d1_id, "owed_amount": each_amount}, {"user_id": d2_id, "owed_amount": each_amount}]
    elif m_debt_4:
        debtor_id, payer_id, desc, amount = resolve_member(m_debt_4.group(1), 0), resolve_member(m_debt_4.group(2), 1), m_debt_4.group(3), float(m_debt_4.group(4))
        raw_currency = m_debt_4.group(5)
        splits = [{"user_id": debtor_id, "owed_amount": amount}]
    elif m_split_1:
        payer_id, desc, amount = resolve_member(m_split_1.group(1), 0), m_split_1.group(2), float(m_split_1.group(3))
        raw_currency = m_split_1.group(4)
        all_m = [m['user_id'] for m in supabase.table('group_members').select('user_id').eq('group_id', db_group_id).execute().data]
        if not all_m: all_m = [payer_id]
        splits = [{"user_id": m, "owed_amount": amount / len(all_m)} for m in all_m]
    elif m_split_2:
        desc, amount, payer_id = m_split_2.group(1), float(m_split_2.group(2)), sender_id
        raw_currency = m_split_2.group(3)
        all_m = [m['user_id'] for m in supabase.table('group_members').select('user_id').eq('group_id', db_group_id).execute().data]
        if not all_m: all_m = [payer_id]
        splits = [{"user_id": m, "owed_amount": amount / len(all_m)} for m in all_m]

    if payer_id and splits:
        currency, exchange_rate = normalize_currency(raw_currency)
        
        final_cat = category
        final_date = datetime.now().strftime("%Y-%m-%d")
        final_img = None
        final_items = []

        if pending_data:
            ai = pending_data["ai_data"]
            final_cat = ai.get("category", category)
            final_date = ai.get("date", final_date)
            final_img = pending_data.get("img_url")
            final_items = ai.get("items", [])
            
            if final_items:
                all_m = [m['user_id'] for m in supabase.table('group_members').select('user_id').eq('group_id', db_group_id).execute().data]
                for item in final_items:
                    item['splitters'] = all_m

            del pending_receipts[line_id]
        
        exp_res = supabase.table('expenses').insert({
            'group_id': db_group_id, 'expense_date': final_date, 'category': final_cat,
            'description': desc, 'amount': amount, 'currency': currency, 'exchange_rate': exchange_rate, 'payer_id': payer_id,
            'image_path': final_img,
            'items': final_items
        }).execute()
        
        splits_data = [{"expense_id": exp_res.data[0]['id'], "user_id": s['user_id'], "owed_amount": s['owed_amount']} for s in splits]
        supabase.table('expense_splits').insert(splits_data).execute()
        log_activity(db_group_id, sender_name, 'add', desc, f"快速記帳 ({currency})")

        reply = f"🔫 討債工讀生已火速記錄！\n✅ 項目：{desc}\n💰 總額：{amount:g} {currency}\n"
        if final_img: reply += "🧾 (已為您自動附上收據憑證)\n"
        reply += "趕快點擊選單去追債吧！🤜🏿"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

# ================= 支出與群組操作 API =================
@app.put("/api/groups/{group_id}")
def update_group(group_id: int, data: GroupUpdate):
    try:
        supabase.table('groups').update({'name': data.name}).eq('id', group_id).execute()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/groups/{group_id}")
def delete_group(group_id: int):
    try:
        supabase.table('activities').delete().eq('group_id', group_id).execute()
        exp_res = supabase.table('expenses').select('id').eq('group_id', group_id).execute()
        exp_ids = [e['id'] for e in exp_res.data]
        if exp_ids:
            supabase.table('expense_splits').delete().in_('expense_id', exp_ids).execute()
        supabase.table('expenses').delete().eq('group_id', group_id).execute()
        supabase.table('group_members').delete().eq('group_id', group_id).execute()
        supabase.table('groups').delete().eq('id', group_id).execute()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/groups")
def create_group(data: GroupCreate):
    try:
        res = supabase.table('groups').insert({'name': data.name}).execute()
        group_id = res.data[0]['id']
        supabase.table('group_members').insert({'group_id': group_id, 'user_id': data.user_id, 'user_name': data.user_name}).execute()
        log_activity(group_id, data.user_name, 'join', '建立並加入了群組')
        return {"status": "success", "group_id": group_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/api/groups")
def get_groups(user_id: str = None):
    if user_id:
        mem_res = supabase.table('group_members').select('group_id').eq('user_id', user_id).execute()
        g_ids = [m['group_id'] for m in mem_res.data]
        if g_ids:
            return supabase.table('groups').select('id, name').in_('id', g_ids).execute().data
        return []
    else:
        return supabase.table('groups').select('id, name').execute().data

@app.post("/api/groups/{group_id}/members")
def add_member(group_id: int, member: MemberData):
    try:
        if member.user_id.startswith("virtual_"):
            name_check = supabase.table("group_members").select("*").eq("group_id", group_id).eq("user_name", member.user_name).execute()
            if name_check.data:
                return {"status": "error", "message": "此名稱已存在，請換一個名字喔！"}

        exists = supabase.table("group_members").select("*").eq("group_id", group_id).eq("user_id", member.user_id).execute()
        is_new = False
        
        if not exists.data:
            supabase.table("group_members").insert({
                "group_id": group_id, "user_id": member.user_id, "user_name": member.user_name
            }).execute()
            is_new = True
            
        merged = merge_virtual_member_if_exists(group_id, member.user_id, member.user_name)
        
        if is_new:
            action_detail = '加入了群組 (並與同名虛擬帳號完成融合)' if merged else '加入了群組'
            log_activity(group_id, member.user_name, 'join', action_detail)

        return {"status": "success", "is_new": is_new}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/scan-receipt")
def scan_receipt(file: UploadFile = File(...)):
    temp_file_path = f"temp_scan_{file.filename}"
    with open(temp_file_path, "wb") as buffer:
        buffer.write(file.file.read())
    try:
        result = process_receipt(temp_file_path)
        if isinstance(result, dict) and "error" in result: raise HTTPException(status_code=500, detail=result["error"])
        return result
    finally:
        if os.path.exists(temp_file_path): os.remove(temp_file_path)

@app.post("/api/upload-image")
def upload_image(file: UploadFile = File(...)):
    api_key = os.getenv("IMGBB_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="Missing ImgBB API Key")

    image_content = file.file.read()
    b64_image = base64.b64encode(image_content).decode('utf-8')
    
    try:
        response = requests.post("https://api.imgbb.com/1/upload", data={"key": api_key, "image": b64_image}, timeout=10)
        result = response.json()
        if result.get("success"):
            return {"image_path": result["data"]["display_url"]}
        else:
            raise HTTPException(status_code=400, detail="ImgBB 上傳失敗")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/add_expense")
def add_expense(data: ExpenseData):
    try:
        expense_data = {
            "group_id": data.group_id, "expense_date": data.expense_date, "category": data.category,
            "description": data.description, "notes": data.notes, "amount": data.amount,
            "currency": data.currency, "exchange_rate": data.exchange_rate, "payer_id": data.payer_id,
            "image_path": data.image_path, "items": data.items
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
def get_expenses(group_id: int = 1):
    exp_res = supabase.table("expenses").select("*").eq("group_id", group_id).order("expense_date", desc=True).execute()
    mem_res = supabase.table("group_members").select("user_id, user_name").eq("group_id", group_id).execute()
    members_map = {m['user_id']: m['user_name'] for m in mem_res.data}
    
    results = []
    for e in exp_res.data:
        e['payer_name'] = members_map.get(e['payer_id'], e['payer_id'])
        results.append(e)
    return results

@app.get("/api/expenses/{expense_id}")
def get_single_expense(expense_id: int):
    exp_res = supabase.table("expenses").select("*").eq("id", expense_id).execute()
    if not exp_res.data:
        raise HTTPException(status_code=404, detail="Expense not found")
    row = exp_res.data[0]
    
    splits_res = supabase.table("expense_splits").select("user_id, owed_amount").eq("expense_id", expense_id).execute()
    row['splits'] = splits_res.data
    return row

@app.put("/api/expenses/{expense_id}")
def update_expense(expense_id: int, data: ExpenseData):
    try:
        expense_data = {
            "expense_date": data.expense_date, "category": data.category, "description": data.description,
            "notes": data.notes, "amount": data.amount, "currency": data.currency,
            "exchange_rate": data.exchange_rate, "payer_id": data.payer_id, "image_path": data.image_path,
            "items": data.items
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
def delete_expense(expense_id: int, group_id: int, user_name: str, description: str):
    try:
        supabase.table("expense_splits").delete().eq("expense_id", expense_id).execute()
        supabase.table("expenses").delete().eq("id", expense_id).execute()
        log_activity(group_id, user_name, 'delete', description)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)