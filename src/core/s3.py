import boto3
import os
import logging
import shutil
from botocore.exceptions import NoCredentialsError, ClientError

class S3Uploader:
    def __init__(self):
        self.endpoint = os.getenv("S3_ENDPOINT_URL")
        self.access_key = os.getenv("S3_ACCESS_KEY")
        self.secret_key = os.getenv("S3_SECRET_KEY")
        self.bucket_name = os.getenv("S3_BUCKET_NAME")
        self.s3_client = None

        # Инициализация только если есть ключи
        if all([self.endpoint, self.access_key, self.secret_key, self.bucket_name]):
            try:
                self.s3_client = boto3.client(
                    's3',
                    endpoint_url=self.endpoint,
                    aws_access_key_id=self.access_key,
                    aws_secret_access_key=self.secret_key
                )
                print("✅ S3 Client initialized.")
            except Exception as e:
                print(f"❌ Failed to initialize S3: {e}")
        else:
            print("⚠️ S3 Env variables missing. Logging will be local only.")

    def upload_session_folder(self, local_folder_path: str):
        """
        Загружает содержимое папки сессии в S3 и удаляет локальную папку.
        """
        # Если клиента нет или папки нет - просто удаляем локально (чтобы не забивать диск)
        if not os.path.exists(local_folder_path):
            return

        if not self.s3_client:
            try:
                shutil.rmtree(local_folder_path)
            except: pass
            return

        folder_name = os.path.basename(local_folder_path)
        print(f"☁️ Uploading session: {folder_name}...")

        # Рекурсивно обходим файлы
        for root, dirs, files in os.walk(local_folder_path):
            for filename in files:
                local_path = os.path.join(root, filename)
                s3_key = f"{folder_name}/{filename}"

                try:
                    self.s3_client.upload_file(local_path, self.bucket_name, s3_key)
                except Exception as e:
                    logging.error(f"S3 Upload Error ({filename}): {e}")

        # Удаляем локальную папку после загрузки
        try:
            shutil.rmtree(local_folder_path)
            print(f"✅ Uploaded and cleaned: {folder_name}")
        except Exception as e:
            print(f"⚠️ Error cleaning up {local_folder_path}: {e}")

# Глобальный инстанс
s3_uploader = S3Uploader()