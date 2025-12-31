import boto3
import os
import logging
from botocore.exceptions import NoCredentialsError, ClientError


class S3Uploader:
    def __init__(self):
        self.endpoint = os.getenv("S3_ENDPOINT_URL")
        self.access_key = os.getenv("S3_ACCESS_KEY")
        self.secret_key = os.getenv("S3_SECRET_KEY")
        self.bucket_name = os.getenv("S3_BUCKET_NAME")
        self.s3_client = None

        if all([self.endpoint, self.access_key, self.secret_key, self.bucket_name]):
            try:
                self.s3_client = boto3.client(
                    's3',
                    endpoint_url=self.endpoint,
                    aws_access_key_id=self.access_key,
                    aws_secret_access_key=self.secret_key
                )
                print("✅ S3 Client initialized successfully.")
            except Exception as e:
                print(f"❌ Failed to initialize S3 Client: {e}")
        else:
            print("⚠️ S3 Env variables missing. Uploading disabled.")

    def upload_session_folder(self, local_folder_path: str) -> bool:
        """
        Загружает содержимое папки сессии в S3.
        Возвращает True, если успешно, иначе False.
        """
        if not self.s3_client:
            logging.error("S3 Client not active. Skipping upload.")
            return False

        folder_name = os.path.basename(local_folder_path)
        # Список файлов, которые мы ожидаем (согласно твоему ТЗ)
        target_files = ["chat_history.log", "game_logic.log", "raw_debug.log"]

        uploaded_count = 0

        print(f"☁️ Starting upload for: {folder_name}...")

        for filename in target_files:
            local_file_path = os.path.join(local_folder_path, filename)

            # Проверяем, существует ли файл (вдруг игра вылетела раньше создания файла)
            if not os.path.exists(local_file_path):
                continue

            # Путь в бакете: Папка_Сессии/Имя_Файла
            s3_key = f"{folder_name}/{filename}"

            try:
                self.s3_client.upload_file(local_file_path, self.bucket_name, s3_key)
                uploaded_count += 1
            except (NoCredentialsError, ClientError, Exception) as e:
                logging.error(f"❌ S3 Upload Error ({filename}): {e}")
                # Мы не прерываем цикл, пробуем загрузить остальные файлы

        if uploaded_count > 0:
            print(f"✅ Uploaded {uploaded_count} files to S3 bucket '{self.bucket_name}/{folder_name}'")
            return True
        else:
            print("⚠️ No files were uploaded to S3.")
            return False


# Глобальный инстанс
s3_uploader = S3Uploader()