# Cloud Run Secret Hardening

## Scope

- Date: 2026-06-17
- Google Cloud project: `gen-lang-client-0817070175`
- Cloud Run service: `clipforge-tools`
- Region: `us-central1`
- Previous revision: `clipforge-tools-00103-x9z`
- Hardened revision: `clipforge-tools-00104-hwm`

No secret values are recorded in this document.

## Sensitive Environment Variables

The following Cloud Run environment variables were migrated from plaintext values to Secret Manager references:

- `ARK_API_KEY`
- `CLIPFORGE_API_KEY`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_CLIENT_SECRET_AURO`
- `GOOGLE_REFRESH_TOKEN`
- `GOOGLE_REFRESH_TOKEN_AURO`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`

Secret names follow the `clipforge-<lowercase-variable-name>` convention with underscores replaced by hyphens:

- `clipforge-ark-api-key`
- `clipforge-clipforge-api-key`
- `clipforge-google-client-secret`
- `clipforge-google-client-secret-auro`
- `clipforge-google-refresh-token`
- `clipforge-google-refresh-token-auro`
- `clipforge-r2-access-key-id`
- `clipforge-r2-secret-access-key`

The Cloud Run runtime service account has `roles/secretmanager.secretAccessor` on these ClipForge secrets only:

```text
550177383294-compute@developer.gserviceaccount.com
```

## Cloud Run Safeguards

The same controlled update temporarily reduced SQLite/GCSFuse write-concurrency risk:

- Container concurrency: `1`
- Max instances: `1`
- Min instances: preserved as `1`
- GCSFuse `/data` volume: preserved
- `DATA_DIR=/data`: preserved
- `DB_PATH=/data/clipforge.db`: preserved
- `DATABASE_URL`: not set
- Cloud SQL: not created
- Production database migration: not performed

This is a temporary risk reduction only. It does not make SQLite on GCSFuse production-safe.

## Verification

After the update:

- Revision `clipforge-tools-00104-hwm` was Ready.
- Traffic was `100%` to `clipforge-tools-00104-hwm`.
- `/` returned HTTP `200`.
- `/v3` returned HTTP `200`.
- `/v3/ready` returned HTTP `200`.
- Sensitive variables were present as Secret Manager references.
- No same-name plaintext sensitive variables remained in the Cloud Run service configuration.
- Recent logs did not show Secret Manager access denial, startup failure, continuous restart, or obvious secret-value leakage.

## R2 Smoke

The existing R2 smoke script was run with Cloud Run-equivalent configuration and Secret Manager values supplied only in process memory.

Results:

- Public bucket upload/read/delete: passed.
- Private bucket upload/presigned-read/delete: passed.
- Private object was not publicly readable.
- Test objects were deleted.
- No Ark or Seedance task was created.
- No database writes were performed by the smoke script.
- No full presigned URL was printed.

## Token Rotation Recommendation

The R2 token is now stored in Secret Manager, but it previously existed as plaintext Cloud Run environment variables. Rotate the Cloudflare R2 token when operationally convenient:

1. Create a new minimal-permission R2 token in Cloudflare.
2. Add new Secret Manager versions for the R2 access key and secret access key.
3. Deploy or refresh Cloud Run to use the new secret versions.
4. Verify `/v3/ready` and the R2 smoke script.
5. Revoke the old Cloudflare R2 token.

Do not revoke the old token before the new Secret Manager versions have been verified.

