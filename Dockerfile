FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl

RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash -

RUN apt-get install -y nodejs

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

RUN pip install -U "yt-dlp[default]"

COPY . .

CMD ["python","main.py"]
