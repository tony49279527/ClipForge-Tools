# Security

Implemented safeguards:

- Upload filenames are sanitized.
- Local storage blocks directory traversal.
- MIME type allowlist is enforced.
- Upload size is limited by `V3_MAX_UPLOAD_BYTES`.
- Local storage access uses controlled `/v3/storage/local/{project_id}/{filename}` paths.
- Payload previews and operation logs sanitize API keys, tokens, bearer credentials, and secret-like fields.
- `/v3/health` and `/v3/ready` do not leak secrets.
- FFmpeg is called with argument arrays, not shell strings.
- V3 SQL table-name helpers use allowlists.

Operational requirements:

- Do not commit `data/`, `uploads/`, `outputs/`, API keys, YouTube tokens, or provider responses with secrets.
- Treat remote asset URLs as untrusted. Use timeouts and MIME checks before enabling remote downloads.
- Use Secret Manager or equivalent for production credentials.
