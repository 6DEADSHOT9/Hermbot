import hashlib
import base64
from uuid import NAMESPACE_URL, uuid5

import boto3
from botocore.exceptions import ClientError

from .db import event
from .service import fail
from .settings import settings


class ArtifactStore:
    bucket = "hermbot"

    def __init__(self):
        self.client = boto3.client("s3", endpoint_url=settings.storage_endpoint, aws_access_key_id="hermbot",
                                   aws_secret_access_key=settings.storage_password, region_name="us-east-1")

    def initialize(self):
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            if exc.response["ResponseMetadata"]["HTTPStatusCode"] not in (404,):
                raise
            self.client.create_bucket(Bucket=self.bucket)

    def publish(self, conn, task, name, content, content_type, receipt_id, previous_id=None):
        from . import library
        library.filename(name)
        data = content.encode("utf-8")
        if len(data) > 1_000_000:
            fail(413, "Text published through model tool arguments is limited to 1 MB")
        artifact_id = uuid5(NAMESPACE_URL, receipt_id)
        artifact = library.save(conn, self, name=name, data=data, content_type=content_type,
                                conversation_id=task['conversation_id'], previous_id=previous_id,
                                task=task, artifact_id=artifact_id, source='bot')
        conn.execute("INSERT INTO artifact_access VALUES (%s,%s) ON CONFLICT DO NOTHING", (artifact_id, task["id"]))
        # Returning child outputs to their owning parents also grants the parent the artifact.
        if task["parent_id"]:
            conn.execute("INSERT INTO artifact_access VALUES (%s,%s) ON CONFLICT DO NOTHING", (artifact_id, task["parent_id"]))
        event(conn, "artifact.published", task, data={"artifact_id": str(artifact_id), "name": name})
        return {"artifact_id": str(artifact_id), "name": artifact["name"], "sha256": artifact["sha256"], "size": artifact["size"], "version": artifact['version'], "family_id": str(artifact['family_id'])}

    def read(self, artifact):
        return self.read_bytes(artifact).decode("utf-8")

    def publish_file(self, conn, task, path, name, content_type, receipt_id, previous_id=None):
        from . import library, native
        if content_type not in library.FILE_TYPES:
            fail(422, 'Choose a supported artifact file type')
        exported = native.request(task['bot_id'], 'POST', '/workspace/export', json={'path': path})
        try:
            data = base64.b64decode(exported['content_base64'], validate=True)
            if len(data) > library.MAX_BYTES or exported['size'] != len(data) or exported['sha256'] != hashlib.sha256(data).hexdigest():
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            fail(502, 'Workspace export integrity check failed')
        library.validate_bytes(data, content_type)
        artifact = library.save(conn, self, name=library.filename(name or exported['name']), data=data, content_type=content_type,
                                conversation_id=task['conversation_id'], previous_id=previous_id, task=task,
                                artifact_id=uuid5(NAMESPACE_URL, receipt_id), source='bot')
        for recipient in [task['id']] + ([task['parent_id']] if task['parent_id'] else []):
            conn.execute('INSERT INTO artifact_access VALUES(%s,%s) ON CONFLICT DO NOTHING', (artifact['id'], recipient))
        event(conn, 'artifact.published', task, data={'artifact_id': str(artifact['id']), 'name': artifact['name']})
        return {**library.projection(artifact), 'artifact_id': str(artifact['id'])}

    def read_bytes(self, artifact, limit=10_000_000):
        response = self.client.get_object(Bucket=self.bucket, Key=artifact["object_key"])
        try:
            data = response["Body"].read(limit + 1)
        finally:
            response['Body'].close()
        if len(data) != artifact["size"] or hashlib.sha256(data).hexdigest() != artifact["sha256"]:
            fail(502, "Artifact integrity check failed")
        return data
