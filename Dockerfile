FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    wget \
    xz-utils

# скачать telegram-bot-api
RUN wget -O botapi.tar.gz \
https://github.com/tdlib/telegram-bot-api/releases/latest/download/telegram-bot-api-linux-x86_64.tar.gz \
&& tar -xzf botapi.tar.gz \
&& mv telegram-bot-api/bin/telegram-bot-api /usr/local/bin/telegram-bot-api \
&& chmod +x /usr/local/bin/telegram-bot-api

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x start.sh

CMD ["./start.sh"]
