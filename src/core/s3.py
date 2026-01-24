import boto3
import os
import shutil
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
                print("✅ S3 Client initialized.")
            except Exception as e:
                print(f"❌ Failed to initialize S3: {e}")
        else:
            print("⚠️ S3 Env variables missing. Uploading disabled.")

    def upload_session_folder(self, local_folder_path: str, target_s3_path: str, delete_after: bool = True):
        """
        Загружает содержимое локальной папки в S3.
        delete_after: Если False, папка не удаляется (для промежуточных логов).
        """
        if not self.s3_client:
            print("⚠️ S3 Client not ready. Skipping upload.")
            if delete_after and os.path.exists(local_folder_path):
                try:
                    shutil.rmtree(local_folder_path)
                except:
                    pass
            return

        if not os.path.exists(local_folder_path):
            print(f"⚠️ Local log folder not found: {local_folder_path}")
            return

        print(f"☁️ Uploading logs to S3: {target_s3_path} (Delete: {delete_after})...")

        uploaded_count = 0
        try:
            for root, dirs, files in os.walk(local_folder_path):
                for filename in files:
                    local_file = os.path.join(root, filename)

                    # Формируем путь в S3
                    # Если в папке есть подпапки, сохраняем структуру относительно local_folder_path
                    rel_path = os.path.relpath(local_file, local_folder_path)
                    # Заменяем обратные слеши на прямые для S3
                    rel_path = rel_path.replace("\\", "/")

                    s3_key = f"{target_s3_path}/{rel_path}"

                    try:
                        self.s3_client.upload_file(local_file, self.bucket_name, s3_key)
                        uploaded_count += 1
                    except Exception as e:
                        print(f"❌ S3 Upload Failed for {filename}: {e}")

            print(f"✅ Uploaded {uploaded_count} files to bucket '{self.bucket_name}'")

        except Exception as e:
            print(f"🔥 S3 Global Error: {e}")

        # Удаляем локальную папку только если запрошено
        if delete_after:
            try:
                shutil.rmtree(local_folder_path)
                print(f"🗑️ Local logs deleted: {local_folder_path}")
            except Exception as e:
                print(f"⚠️ Error cleaning up local logs: {e}")


# Глобальный инстанс
s3_uploader = S3Uploader()