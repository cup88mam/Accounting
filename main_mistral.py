import json
import base64
import os
from datetime import datetime
from mistralai.client import Mistral 

client = Mistral(api_key=os.getenv("MISTRAL_API_KEY"))

def process_receipt(image_path):
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    try:
        with open(image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')
        
        prompt = prompt = f"""
        【任務】你是一個專業的會計助手，請從這張收據/發票/帳單截圖中擷取消費資訊。
        
        【欄位嚴格定義】
        1. description: 商店名稱或主要消費類別。
        2. amount: 整張帳單的「最終付款總額」。必須是純數字。
        3. notes: 「純字串」。將各個小項目明細整理成一段文字。
        4. category: 請從「飲食」、「交通」、「住宿」、「娛樂」、「購物」、「一般支出」中選擇最適合的一項。
        5. date: 消費日期。請從圖片中尋找，格式必須為 YYYY/MM/DD (例如 2026/07/17)。若找不到，請填入 {today_str}。
        6. currency: 幣別。請判斷幣別，且「只能」輸出 "TWD"、"JPY" 或 "USD" 其中之一。(台灣發票/新台幣請務必輸出 "TWD")
        
        【輸出規則】
        必須且只能回傳一個完全符合以下結構的 JSON 物件：
        {{
            "description": "商店名稱",
            "amount": 1000,
            "currency": "TWD",
            "date": "2026/07/17",
            "category": "飲食",
            "notes": "明細1, 明細2..."
        }}
        """

        response = client.chat.complete(
            model="pixtral-12b-2409",
            response_format={"type": "json_object"}, 
            messages=[
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": f"data:image/jpeg;base64,{base64_image}"}
                ]}
            ]
        )
        
        raw_text = response.choices[0].message.content.strip()
        data = json.loads(raw_text)
        
        try:
            data['amount'] = float(data.get('amount', 0))
        except:
            data['amount'] = 0
            
        if 'notes' in data and not isinstance(data['notes'], str):
            data['notes'] = json.dumps(data['notes'], ensure_ascii=False, indent=2)
            
        return data

    except Exception as e:
        return {"error": f"系統處理錯誤: {str(e)}"}