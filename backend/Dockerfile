FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential libpq-dev curl fontconfig && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install --with-deps chromium

COPY . .
# PDF 頁碼字型：Chromium 的頁尾只能用系統字型，載入不了內嵌字型（見 docs/deployment.md）。
RUN mkdir -p /usr/local/share/fonts/agri \
 && cp app/assets/fonts/AgriPageNumber.otf /usr/local/share/fonts/agri/ \
 && fc-cache -f
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
