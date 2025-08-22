from google.cloud import secretmanager

def _client():
    return secretmanager.SecretManagerServiceClient()

def upsert_secret(secret_id: str, payload: bytes, project_id=None):
    client = _client()
    if project_id is None:
        # infer from ADC
        from google.auth import default
        creds, proj = default()
        project_id = proj
    parent = f"projects/{project_id}"
    name = f"{parent}/secrets/{secret_id}"

    # create if not exists
    try:
        client.create_secret(
            parent=parent, secret_id=secret_id,
            secret={"replication": {"automatic": {}}}
        )
    except Exception:
        pass  # already exists

    # add version
    client.add_secret_version(parent=name, payload={"data": payload})

def access_secret(secret_id: str, project_id=None) -> bytes | None:
    client = _client()
    if project_id is None:
        from google.auth import default
        creds, proj = default()
        project_id = proj
    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    try:
        resp = client.access_secret_version(name=name)
        return resp.payload.data
    except Exception:
        return None
