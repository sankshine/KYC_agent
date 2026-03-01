"""S3/local storage utilities for document handling."""
import boto3, os, shutil
from pathlib import Path

class DocumentStorage:
    def __init__(self, use_s3: bool = False):
        self.use_s3 = use_s3
        if use_s3:
            self.s3 = boto3.client("s3")
            self.bucket = os.getenv("AWS_S3_BUCKET", "kyc-documents")

    def upload(self, local_path: str, key: str) -> str:
        if self.use_s3:
            self.s3.upload_file(local_path, self.bucket, key)
            return f"s3://{self.bucket}/{key}"
        dest = Path(f"./uploads/{key}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(local_path, dest)
        return str(dest)
