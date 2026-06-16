# ClipForge V3 Object Storage

## 1. Why R2

ClipForge V3 needs two different storage behaviors:

- Product reference images must have stable public HTTPS URLs so Ark can read them during image-to-video generation.
- Generated videos should be durable but should not require the entire bucket to be public.

Cloudflare R2 is the current alpha target because it is S3-compatible, inexpensive for small alpha workloads, supports custom public domains for product images, and can generate short-lived signed GET URLs for private generated videos. AWS S3 remains a compatible fallback, but R2 has the lowest operational friction for this branch.

## 2. Local And R2 Modes

The storage selector lives in `clipforge_v3/services/storage_service.py::get_storage()`.

- `V3_STORAGE_BACKEND=local` is the default.
- `V3_STORAGE_BACKEND=r2` enables `R2Storage`.
- Legacy `STORAGE_BACKEND=local` is still accepted as a fallback, but V3 should use `V3_STORAGE_BACKEND`.

Local mode keeps the existing development behavior:

- uploads are stored under `uploads/v3/{project_id}/...`
- generated videos are stored under `outputs/{project_id}/shots/{shot_id}/takes/{take_number}/video.mp4`
- `/v3/storage/local/{project_id}/{filename}` serves local uploaded assets

R2 mode keeps a local cache for upload validation, then writes the durable object to R2 and stores object metadata in the database.

## 3. Environment Variables

Required for R2 mode:

```bash
V3_STORAGE_BACKEND=r2
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=
R2_PUBLIC_BASE_URL=
R2_ENDPOINT_URL=
```

`R2_ENDPOINT_URL` is optional when `R2_ACCOUNT_ID` is set. If omitted, ClipForge builds:

```text
https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com
```

`R2_PUBLIC_BASE_URL` must be HTTPS and is normalized without a trailing slash. Secrets are never written to the database and should not be printed in logs.

## 4. Bucket Requirements

Create a dedicated alpha bucket, not a production bucket. Configure:

- S3-compatible R2 API credentials with least privilege for the test bucket.
- A public HTTPS base URL or custom domain for product reference images.
- Private object access for generated videos unless a separate public media policy is intentionally added later.

## 5. Product Image Access

When `V3_STORAGE_BACKEND=r2`, `clipforge_v3/router.py::v3_upload_asset` calls `R2Storage.save_upload()`.

Flow:

```text
UploadFile
-> local validation cache
-> R2 put_object
-> head_object verification
-> v3_assets.storage_backend = r2
-> v3_assets.object_key
-> v3_assets.access_url = {R2_PUBLIC_BASE_URL}/{object_key}
-> provider_asset_resolver accepts the HTTPS URL
-> Ark payload content[].image_url.url
```

The resolver still rejects empty URLs, local paths, `file://`, non-HTTPS URLs, localhost, private IPs, and loopback hosts. R2 credentials are never exposed to Ark.

## 6. Video Access

When a provider task succeeds in R2 mode, `clipforge_v3/services/generation_service.py::_store_provider_video_artifact()` uploads the downloaded video to R2 before finalizing the Take.

Flow:

```text
Ark signed result URL
-> temporary local download
-> R2 object upload
-> head_object verification
-> v3_takes.storage_backend = r2
-> v3_takes.object_key
-> v3_takes.content_type = video/mp4
-> v3_takes.size_bytes
-> no persisted presigned playback URL
```

`R2Storage.get_download_url(object_key, expires_in=...)` generates a short-lived signed GET URL at read time. Expiry is clamped between 5 and 60 minutes. UI playback for private R2 videos is still a follow-up task; the current service layer is in place and tested with mocks.

## 7. Object Key Structure

Object keys are built by `build_object_key()` and sanitized by `sanitize_object_key_part()`. User-provided filenames are reduced to safe ASCII filename components.

Current structures:

```text
projects/{project_id}/assets/{sha256_prefix}/{safe_filename}
projects/{project_id}/shots/{shot_id}/submissions/{submission_id}/video.mp4
```

These keys:

- do not include local absolute paths
- reject `../` traversal
- do not include credentials or signed query strings
- are stable across machines
- avoid collisions for uploaded assets by content digest prefix
- are stable for generated videos by submission ID

## 8. Upload And Recovery

Product image upload failure:

- raises `StorageError`
- returns an HTTP 400 from the upload route
- does not create a false available `v3_assets` row
- does not allow Ark submission through the R2 asset URL path

Generated video upload failure:

- happens after provider success, so provider generation cost is still recorded once
- sets `v3_generation_submissions.submission_status = artifact_upload_failed`
- stores a sanitized `artifact_upload_failed` error
- returns `storage_recovery_required`
- does not create a Take
- does not call `submit_task()`
- can be retried by recovering the existing submission and provider task

If the R2 object already exists with the expected size, `R2Storage.save_file()` reuses it instead of uploading again.

## 9. Security Rules

- Default mode remains local.
- R2 mode requires explicit `V3_STORAGE_BACKEND=r2`.
- No R2 secrets are persisted.
- No presigned GET URL is persisted.
- Public product image URLs contain no secret query parameters.
- Generated videos are designed for dynamic short-lived signed URLs.
- Tests mock all S3/R2 calls.
- Storage recovery must never call `submit_task()`.

## 10. Local Development

For normal local work:

```bash
export V3_STORAGE_BACKEND=local
python -m pytest -q tests/v3
```

No R2 account is required. Uploaded assets and generated mock videos continue to use local files.

## 11. Production Configuration

For an alpha R2 environment:

```bash
export V3_STORAGE_BACKEND=r2
export R2_ACCOUNT_ID=...
export R2_ACCESS_KEY_ID=...
export R2_SECRET_ACCESS_KEY=...
export R2_BUCKET_NAME=...
export R2_PUBLIC_BASE_URL=https://...
```

Do not paste secrets into chat, docs, logs, or Git. Use environment-specific secret storage.

## 12. Not Complete

- No real R2 upload/read/delete validation has been executed in this branch.
- UI playback for private R2 videos still needs a signed-download endpoint or equivalent page integration.
- Lifecycle policies, retention, and bucket cleanup are not yet automated.
- Multi-user authorization for private video access is not implemented.
