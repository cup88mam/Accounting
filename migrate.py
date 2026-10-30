from database import get_supabase_client, init_db

# 1. 取得 Turso 客戶端並確保資料表已建立
turso = get_supabase_client()
init_db()

print("🧹 正在清理舊有或預設資料（依外鍵反向順序刪除）...")
# 必須先刪子表、再刪主表，才不會觸發 FOREIGN KEY constraint failed
for t in ["activities", "expense_splits", "expenses", "group_members", "groups"]:
    turso.execute_sql(f"DELETE FROM {t}")

print("🚀 開始匯入你的 Supabase 備份資料...")

# 1. 匯入 groups (主表最先匯入)
groups_sql = """
INSERT INTO groups (id, name, created_at, line_group_id) VALUES 
(1, '預設個人/群組', '2026-07-17 09:43:12.00119', null), 
(15, '💬 聊天室專屬群組', '2026-07-17 16:34:59.469412', '2b270454-46a3-4396-a848-5f770d1ba124'), 
(20, 'Wonder Japon', '2026-07-18 10:37:51.175508', 'C6cbc2d4f0f11fb251e5d822dc136faef'), 
(21, '💬 聊天室專屬群組', '2026-07-20 18:36:40.119662', 'C1920a4b1553d962cdc9f47fb58ba96cc'), 
(28, '沖繩', '2026-09-21 09:03:43.036845', null), 
(29, '💬 聊天室專屬群組', '2026-09-22 05:30:32.730724', 'Cc6c6a2adaf53e1eded6cd2164804a40d'), 
(30, '東騎', '2026-10-10 14:52:18.546364', 'C03e5cbc216ac3b87f007f76e8066bf08'), 
(31, '名古屋溫泉行', '2026-10-12 13:08:08.230809', null), 
(32, 'Ecobalance 2026', '2026-10-29 16:18:33.330901', 'C776b24d151c4c22c760da5419fbe34ff');
"""
turso.execute_sql(groups_sql)
print("✅ 1/5: groups 匯入完成 (9 筆)")

# 2. 匯入 group_members
group_members_sql = """
INSERT INTO group_members (group_id, user_id, user_name) VALUES 
(1, 'me', '我'), 
(15, 'Ude8f25a4e687b26af6bef99a03c6a345', '陳怡瑋*'), 
(20, 'U295e663e06728ced3fe69a6c2185dc06', '丁'), 
(20, 'U8a530fb60f9e05f3954d07bb6c943e33', '劉'), 
(20, 'Ub5483d0853c9b5981012550c71d50004', '紀詠仁'), 
(28, 'Uf5c6e3c8ba143f4a286f2d57043adf38', '翁國哲'), 
(29, 'U902ff0f0c9e4fd70a7d9cbb5dbdc197f', 'Bilguun 比樂君'), 
(29, 'Ub5483d0853c9b5981012550c71d50004', '紀詠仁'), 
(30, 'U902ff0f0c9e4fd70a7d9cbb5dbdc197f', 'Bilguun 比樂君'), 
(30, 'Ub5483d0853c9b5981012550c71d50004', '紀詠仁'), 
(30, 'Ude8f25a4e687b26af6bef99a03c6a345', '陳怡瑋*'), 
(30, 'Uf5c6e3c8ba143f4a286f2d57043adf38', '翁國哲'), 
(31, 'Ude8f25a4e687b26af6bef99a03c6a345', '陳怡瑋*'), 
(31, 'Uf5c6e3c8ba143f4a286f2d57043adf38', '翁國哲'), 
(32, 'U78cc945d478ddbdad39d67c4fb46e2af', '蕭禹恩'), 
(32, 'Uf5c6e3c8ba143f4a286f2d57043adf38', '翁國哲');
"""
turso.execute_sql(group_members_sql)
print("✅ 2/5: group_members 匯入完成 (16 筆)")

# 3. 匯入 expenses
expenses_sql = """
INSERT INTO expenses (id, group_id, expense_date, category, description, amount, currency, exchange_rate, payer_id, image_path, notes, created_at, items) VALUES 
(44, 28, '2026-09-21', '交通', 'SQ N RENT A CAR NAHA A', 15229, 'TWD', 1, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 'https://i.ibb.co/Fk6Lqf3H/8798b9bda4a0.png', '現金回饋JCB晶縷卡 2408，消費國家：日本', '2026-09-21 09:08:11.139187', '[]'), 
(45, 30, '2023-10-24', '住宿', 'Homestay 住宿費用', 7630, 'TWD', 1, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 'https://i.ibb.co/tMQ4sqVJ/c9050839d8ef.jpg', '包含三筆住宿消費，日期分別為 2023/10/22、2023/10/23、2023/10/24。', '2026-10-10 15:19:25.059717', '[{"name":"池上","price":3600,"splitters":["Uf5c6e3c8ba143f4a286f2d57043adf38"]},{"name":"台東車站","price":2500,"splitters":["Uf5c6e3c8ba143f4a286f2d57043adf38"]},{"name":"玉里","price":1530,"splitters":["Uf5c6e3c8ba143f4a286f2d57043adf38"]}]'), 
(46, 30, '2026-10-25', '住宿', '知本', 5450, 'TWD', 1, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 'https://i.ibb.co/Kz7hMQbJ/0f3c2370f954.jpg', '發票編號: NTD 5450。備註文字包含「本店為您提供優質服務，感謝惠顧」。', '2026-10-10 15:20:54.891522', '[]'), 
(47, 31, '2026-10-12', '交通', '車票', 25040, 'JPY', 0.21, 'Uf5c6e3c8ba143f4a286f2d57043adf38', '', '', '2026-10-12 13:51:22.224721', '[]'), 
(48, 31, '2026-11-07', '住宿', 'Youjian B&B (幽靜民宿)', 1744, 'TWD', 1, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 'https://i.ibb.co/NgtkH2pS/6a6656fcecbb.jpg', '標準雙人房，含早餐，2張單人床，有間民宿不含空調', '2026-10-12 13:54:42.381054', '[]'), 
(49, 31, '2026-10-02', '住宿', '上諏訪湖溫泉Rako華乃井', 34562, 'JPY', 0.2, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 'https://i.ibb.co/tTf5Xgnf/ff472e93354c.jpg', '發票編號: 2131509578138。地址: 長野縣諏訪市上野原2-1200-3。電話: 0268-54-0956。', '2026-10-12 14:00:00.911767', '[]'), 
(50, 31, '2026-11-08', '交通', 'JR East Rent-a-Car 予約確認', 25210, 'JPY', 0.2, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 'https://i.ibb.co/cXDKh2qj/611c24aa3b20.jpg', '車輛預約編號：302610120108。預約時間為2026年11月8日09:30至11月10日19:00。車輛類型：S級（如フィット、ヤリス、ソリオ等）。包含選項：禁煙車、フルサポート安心プラン（免費補償+NOCサポート+フルサポート）、スタッドレスタイヤ（希望）、クイックチェックイン、クイックスタート。若預約內容變更或取消，請至https://www.jrerl.co.jp/yoyaku/inquiry確認。', '2026-10-12 14:01:18.448284', '[{"name":"基本料金","price":23560,"splitters":["Uf5c6e3c8ba143f4a286f2d57043adf38","Ude8f25a4e687b26af6bef99a03c6a345"]},{"name":"フルサポート","price":1650,"splitters":["Uf5c6e3c8ba143f4a286f2d57043adf38","Ude8f25a4e687b26af6bef99a03c6a345"]}]'), 
(51, 31, '2026-11-10', '住宿', '名古屋クラウンホテル 都心の天然温泉', 10220, 'JPY', 0.2, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 'https://i.ibb.co/rfxBbgzt/19aba9e225e0.jpg', '預約編號: 08NGLQ19，預約日期: 2026年10月12日，人數: 2名，會員階級: シルバー，支付方式: オンラインカード決済，可獲得寄付金額的30%クーポン，預計加算點數: 290點，預計加算分數: 11620スコア', '2026-10-12 14:05:41.062588', '[]'), 
(52, 31, '2026-10-12', '住宿', '草津溫泉', 27240, 'JPY', 0.2, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 'https://i.ibb.co/TDVJGzHG/963a1fc6b872.jpg', '', '2026-10-12 14:10:58.181728', '[]'), 
(54, 31, '2026-10-29', '轉帳', '轉帳還款', 13224, 'TWD', 1, 'Ude8f25a4e687b26af6bef99a03c6a345', '', '', '2026-10-29 10:55:52.517866', '[]'), 
(55, 28, '2026-10-29', '住宿', 'Airbnb', 13620.1, 'TWD', 1, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 'https://i.ibb.co/fWGBwq7/c830e47833ed.jpg', '國泰世華 CUBE 卡（原 KOKO 卡）核准支付，聯絡國泰世華銀行協助爭議或詢問。', '2026-10-30 02:20:19.997356', '[]'), 
(56, 32, '2026-10-29', '住宿', 'Airbnb', 13620.1, 'TWD', 1, 'U78cc945d478ddbdad39d67c4fb46e2af', 'https://i.ibb.co/fWGBwq7/c830e47833ed.jpg', '國泰世華 CUBE 卡（原 KOKO 卡）核准交易，聯絡國泰世華銀行協助爭議或詢問', '2026-10-30 02:21:25.966284', '[]');
"""
turso.execute_sql(expenses_sql)
print("✅ 3/5: expenses 匯入完成 (12 筆)")

# 4. 匯入 expense_splits
expense_splits_sql = """
INSERT INTO expense_splits (expense_id, user_id, owed_amount) VALUES 
(44, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 15229), 
(45, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 7630), 
(46, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 5450), 
(47, 'Ude8f25a4e687b26af6bef99a03c6a345', 12520), 
(47, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 12520), 
(48, 'Ude8f25a4e687b26af6bef99a03c6a345', 872), 
(48, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 872), 
(49, 'Ude8f25a4e687b26af6bef99a03c6a345', 17281), 
(49, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 17281), 
(50, 'Ude8f25a4e687b26af6bef99a03c6a345', 12605), 
(50, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 12605), 
(51, 'Ude8f25a4e687b26af6bef99a03c6a345', 5110), 
(51, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 5110), 
(52, 'Ude8f25a4e687b26af6bef99a03c6a345', 13620), 
(52, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 13620), 
(54, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 13224), 
(55, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 13620.1), 
(56, 'Uf5c6e3c8ba143f4a286f2d57043adf38', 13620.1);
"""
turso.execute_sql(expense_splits_sql)
print("✅ 4/5: expense_splits 匯入完成 (18 筆)")

# 5. 匯入 activities
activities_sql = """
INSERT INTO activities (id, group_id, user_id, action_type, target_name, details, created_at) VALUES 
(14, 15, '陳怡瑋*', 'join', '透過 LINE 聊天室自動加入', '', '2026-07-17 16:34:59.595079'), 
(22, 20, '紀詠仁', 'join', '加入了群組', '', '2026-07-18 10:37:56.729212'), 
(29, 20, '劉', 'add', '農會直賣所', '快速記帳 (TWD)', '2026-07-21 07:32:48.217119'), 
(60, 20, '劉', 'delete', '農會直賣所', '', '2026-07-22 13:06:03.398663'), 
(90, 28, '翁國哲', 'join', '建立並加入了群組', '', '2026-09-21 09:03:43.404212'), 
(91, 28, '翁國哲', 'add', 'SQ N RENT A CAR NAHA A', 'TWD 15229.0', '2026-09-21 09:08:11.559208'), 
(92, 30, '翁國哲', 'join', '加入了群組', '', '2026-10-10 14:52:27.245896'), 
(93, 30, '翁國哲', 'add', 'Homestay 住宿費用', 'TWD 7630.0', '2026-10-10 15:19:25.34795'), 
(94, 30, '陳怡瑋*', 'join', '加入了群組', '', '2026-10-10 15:20:07.9465'), 
(95, 30, '翁國哲', 'add', '知本', 'TWD 5450.0', '2026-10-10 15:20:55.23334'), 
(96, 31, '翁國哲', 'join', '建立並加入了群組', '', '2026-10-12 13:08:08.580129'), 
(97, 31, '陳怡瑋*', 'join', '加入了群組', '', '2026-10-12 13:46:20.725014'), 
(98, 31, '翁國哲', 'add', '車票', 'JPY 25040.0', '2026-10-12 13:51:22.527949'), 
(99, 31, '翁國哲', 'add', 'Youjian B&B (幽靜民宿)', 'TWD 1744.0', '2026-10-12 13:54:42.739746'), 
(100, 31, '翁國哲', 'add', '上諏訪湖溫泉Rako華乃井', 'JPY 34562.0', '2026-10-12 14:00:01.423128'), 
(101, 31, '翁國哲', 'add', 'JR East Rent-a-Car 予約確認', 'JPY 25210.0', '2026-10-12 14:01:18.865898'), 
(102, 31, '翁國哲', 'add', '名古屋クラウンホテル 都心の天然温泉', 'JPY 10220.0', '2026-10-12 14:05:41.547479'), 
(103, 31, '翁國哲', 'add', '草津溫泉', 'JPY 27240.0', '2026-10-12 14:10:58.521248'), 
(104, 31, '翁國哲', 'update', '草津溫泉', '', '2026-10-12 14:11:43.058414'), 
(105, 31, '翁國哲', 'add', '轉帳還款', 'TWD 13224.0', '2026-10-12 14:31:10.939219'), 
(106, 31, '翁國哲', 'delete', '轉帳還款', '', '2026-10-29 10:52:34.471477'), 
(107, 31, '翁國哲', 'update', '上諏訪湖溫泉Rako華乃井', '', '2026-10-29 10:54:14.956114'), 
(108, 31, '翁國哲', 'add', '轉帳還款', 'TWD 13224.0', '2026-10-29 10:55:52.838794'), 
(109, 28, '翁國哲', 'add', 'Airbnb', 'TWD 13620.13', '2026-10-30 02:20:20.352533'), 
(110, 32, '翁國哲', 'add', 'Airbnb', 'TWD 13620.13', '2026-10-30 02:21:26.461502'), 
(111, 32, '蕭禹恩', 'join', '加入了群組', '', '2026-10-30 02:36:55.62446'), 
(112, 32, '蕭禹恩', 'update', 'Airbnb', '', '2026-10-30 02:37:10.573669');
"""
turso.execute_sql(activities_sql)
print("✅ 5/5: activities 匯入完成 (27 筆)")

# 6. 校正 SQLite 自增主鍵計數器 (避免未來新增資料與舊 ID 衝突)
turso.execute_sql("DELETE FROM sqlite_sequence WHERE name IN ('groups', 'expenses', 'activities')")
turso.execute_sql("INSERT INTO sqlite_sequence (name, seq) VALUES ('groups', 32), ('expenses', 56), ('activities', 112)")

print("\n🎉 所有歷史資料已 100% 完整移轉至 Turso！你可以刪除 migrate.py 並推送部署了！")