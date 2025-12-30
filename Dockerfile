# Используем полную версию Python (не slim!), чтобы избежать проблем с DNS и системными библиотеками
FROM python:3.11

# Создаем пользователя с ID 1000 (требование безопасности Hugging Face Spaces)
RUN useradd -m -u 1000 user

# Переключаемся на пользователя
USER user

# Добавляем путь к локальным пакетам пользователя
ENV PATH="/home/user/.local/bin:$PATH"
# Отключаем буферизацию логов (видим ошибки сразу)
ENV PYTHONUNBUFFERED=1

# Рабочая папка
WORKDIR /app

# Копируем зависимости с правами пользователя
COPY --chown=user requirements.txt requirements.txt

# Устанавливаем зависимости
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем код проекта с правами пользователя
COPY --chown=user . .

# Создаем папку для логов
RUN mkdir -p Logs

# Порт, который ожидает Hugging Face
ENV PORT=7860

# Запуск
CMD ["python", "main.py"]