FROM python:3.11-slim

WORKDIR /app

# 安裝系統編譯與基本工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 複製並安裝 Python 相依套件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製專案的所有程式碼
COPY . .

# Cloud Run 會自動傳入 PORT 變數，預設為 8080
ENV PORT=8080
EXPOSE 8080

# 啟動 FastAPI 服務
CMD exec uvicorn app:app --host 0.0.0.0 --port $PORT