# Persistent Private Key Storage

## Overview

The MMM app now supports persistent storage of Snowflake private keys in Google Secret Manager. This allows users to save their private key once and reuse it across multiple sessions without having to upload it every time.

## Features

### 1. Save Private Key for Future Sessions

When connecting to Snowflake, users can check the **"💾 Save this key for future sessions"** checkbox to store their private key in Google Secret Manager. This key will be automatically loaded in future sessions.

### 2. Automatic Key Loading

When a user opens the app in a new session, the app automatically checks for a saved private key in Secret Manager. If found:
- The key is loaded into the session
- A notification is displayed: "✅ Found a previously saved private key"
- The user can connect without uploading a new key

### 3. Clear Saved Key

Users can delete their saved private key at any time by clicking the **"🗑️ Clear Saved Key"** button in the connection status panel.

## Technical Implementation

### Secret Manager Integration

The private key is stored in Google Secret Manager with the following secret ID:
- Default: `sf-private-key-persistent`
- Configurable via environment variable: `SF_PERSISTENT_KEY_SECRET`

### Key Storage Format

- Keys are stored in PEM format in Secret Manager
- Keys are converted to DER/PKCS#8 format when used for Snowflake connections
- Keys are kept in session state as DER bytes for performance

### Functions

#### `load_persisted_key() -> Optional[bytes]`
Loads the persisted private key from Secret Manager if it exists.

**Returns:** DER-encoded private key bytes, or `None` if not found

#### `save_persisted_key(pem: str) -> bool`
Saves the private key to Secret Manager for persistence.

**Parameters:**
- `pem`: The private key in PEM format

**Returns:** `True` if successful, `False` otherwise

### Modified Functions

#### `ensure_sf_conn()`
Updated to automatically load the private key from Secret Manager when:
- Connection parameters exist in session state
- Private key bytes are not in session state
- A saved key exists in Secret Manager

## Security Considerations

1. **Access Control**: Only users with appropriate IAM permissions can access the Secret Manager secret
2. **Encryption**: Keys are encrypted at rest by Google Secret Manager
3. **Session Security**: Keys are kept in session state but never exposed to the browser
4. **Audit Trail**: All access to Secret Manager is logged in Cloud Audit Logs

## Environment Variables

- `SF_PERSISTENT_KEY_SECRET`: Secret ID for persistent key storage (default: `sf-private-key-persistent`)
- `PROJECT_ID`: Google Cloud project ID (required)

## IAM Requirements

The service account running the application needs the following IAM roles:

```
roles/secretmanager.secretAccessor  # To read secrets
roles/secretmanager.secretVersionAdder  # To create/update secrets
roles/secretmanager.admin  # To delete secrets (for "Clear Saved Key" feature)
```

Or more granularly:
```
secretmanager.secrets.get
secretmanager.secrets.delete
secretmanager.versions.add
secretmanager.versions.access
```

## Usage Example

### First-Time Setup

1. Navigate to "Connect Your Data" page
2. Enter Snowflake credentials (user, account, warehouse, etc.)
3. Upload or paste your private key
4. ✅ Check "💾 Save this key for future sessions"
5. Click "🔌 Connect"
6. The key is now saved and will be available in future sessions

### Subsequent Sessions

1. Navigate to "Connect Your Data" page
2. See notification: "✅ Found a previously saved private key"
3. Enter only your Snowflake connection parameters
4. Click "🔌 Connect" (no need to upload key again)

### Removing a Saved Key

1. While connected, locate the connection status panel
2. Click "🗑️ Clear Saved Key"
3. The key is deleted from Secret Manager
4. Future sessions will require uploading a new key

## Migration from Session-Only Storage

For users currently using session-only private key storage:

1. The existing session-based functionality continues to work
2. To enable persistence, simply check the save checkbox on next connect
3. No data migration or special steps required

## Troubleshooting

### Key Not Loading

If the saved key is not loading automatically:
1. Check IAM permissions on the service account
2. Verify `PROJECT_ID` environment variable is set
3. Check Cloud Logging for Secret Manager access errors

### Cannot Save Key

If saving fails:
1. Verify the service account has `secretmanager.secretVersionAdder` role
2. Check that the secret name doesn't conflict with existing secrets
3. Review error messages in the UI

### Cannot Clear Saved Key

If deleting fails:
1. Verify the service account has `secretmanager.admin` role or `secretmanager.secrets.delete` permission
2. Check that the secret exists in Secret Manager
3. Review Cloud Audit Logs for access denial events

## Code References

- **UI Implementation**: `app/pages/0_Connect_Your_Data.py`
- **Connection Logic**: `app/app_shared.py` (`ensure_sf_conn()`)
- **Secret Manager Helpers**: `app/gcp_secrets.py`
