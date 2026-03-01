"""
GCP Cloud Storage utilities for document handling.
Replaces AWS S3 — uses Google Cloud Storage with signed URLs and lifecycle policies.
"""
import os
from datetime import timedelta
from google.cloud import storage as gcs
from google.oauth2 import service_account


class DocumentStorage:
    """GCS-backed document storage with automatic expiry for temp files."""

    def __init__(self, use_gcs: bool = True):
        self.use_gcs = use_gcs
        self.bucket_name = os.getenv("GCS_BUCKET_NAME", "kyc-documents-bucket")
        if use_gcs:
            self.client = gcs.Client()
            self.bucket = self.client.bucket(self.bucket_name)

    def upload(self, local_path: str, blob_name: str, temp: bool = True) -> str:
        """Upload file to GCS. temp=True sets metadata for lifecycle deletion."""
        if not self.use_gcs:
            import shutil, pathlib
            dest = pathlib.Path(f"./uploads/{blob_name}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(local_path, dest)
            return str(dest)

        blob = self.bucket.blob(blob_name)
        blob.metadata = {"temp": "true"} if temp else {"reviewed": "true"}
        blob.upload_from_filename(local_path)
        blob.patch()  # apply metadata
        return f"gs://{self.bucket_name}/{blob_name}"

    def generate_signed_url(self, blob_name: str, expiry_minutes: int = 15) -> str:
        """Generate a short-lived signed URL for secure document access."""
        blob = self.bucket.blob(blob_name)
        url = blob.generate_signed_url(
            expiration=timedelta(minutes=expiry_minutes),
            method="GET",
            version="v4"
        )
        return url

    def delete(self, blob_name: str):
        if self.use_gcs:
            blob = self.bucket.blob(blob_name)
            blob.delete()

    def download(self, blob_name: str, local_path: str):
        if self.use_gcs:
            blob = self.bucket.blob(blob_name)
            blob.download_to_filename(local_path)
