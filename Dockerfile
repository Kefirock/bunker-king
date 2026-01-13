# Используем полный Python 3.11, как вы и просили
FROM python:3.11

# Переменные окружения:
# PYTHONDONTWRITEBYTECODE - не создавать .pyc файлы
# PYTHONUNBUFFERED - сразу выводить логи в консоль (важно для дебага!)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Устанавливаем рабочую директорию
WORKDIR /app

# Сначала копируем только requirements.txt для кэширования слоев
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем ВЕСЬ проект в контейнер
# (.) - текущая папка на компьютере -> (.) - папка /app в контейнере
COPY . .

# Создаем папку для логов, чтобы избежать ошибок при старте
RUN mkdir -p Logs

# Koyeb и другие облака часто используют порт 8000
ENV PORT=8000

# Запускаем бота
CMD ["python", "main.py"]