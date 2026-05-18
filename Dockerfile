# Используем официальный образ Python
FROM python:3.11-slim

# Устанавливаем ffmpeg (нужен для объединения видео и аудио в 1080p)
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Устанавливаем yt-dlp глобально для удобства
RUN pip install --no-cache-dir yt-dlp

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файл с зависимостями
COPY requirements.txt .

# Устанавливаем зависимости Python
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код бота
COPY main.py .

# Запускаем бота
CMD ["python", "main.py"]
