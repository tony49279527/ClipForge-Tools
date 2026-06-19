#!/usr/bin/env bash
set -euo pipefail

EXECUTE=0
PROJECT=""
REGION="us-central1"
INSTANCE="clipforge-postgres-test"
DATABASE="clipforge_test"
USER_NAME="clipforge_app"
TIER="db-f1-micro"

usage() {
  cat <<'EOF'
Prepare a Cloud SQL PostgreSQL test instance plan for ClipForge V3.

Default mode prints the plan only. Use --execute to run gcloud commands.
This script never accepts or prints a database password. Set it later with
Secret Manager or an interactive gcloud/sql command outside this script.

Options:
  --project PROJECT
  --region REGION                 default: us-central1
  --instance INSTANCE             default: clipforge-postgres-test
  --database DATABASE             default: clipforge_test
  --user USER                     default: clipforge_app
  --tier TIER                     default: db-f1-micro
  --execute                       actually run gcloud commands
  --help

Example:
  bash scripts/v3/prepare_cloud_sql_postgres.sh --project my-gcp-project --plan
  bash scripts/v3/prepare_cloud_sql_postgres.sh --project my-gcp-project --execute
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="${2:?}"; shift 2 ;;
    --region) REGION="${2:?}"; shift 2 ;;
    --instance) INSTANCE="${2:?}"; shift 2 ;;
    --database) DATABASE="${2:?}"; shift 2 ;;
    --user) USER_NAME="${2:?}"; shift 2 ;;
    --tier) TIER="${2:?}"; shift 2 ;;
    --execute) EXECUTE=1; shift ;;
    --plan) EXECUTE=0; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$PROJECT" ]]; then
  echo "--project is required" >&2
  usage
  exit 2
fi

commands=(
  "gcloud sql instances create ${INSTANCE} --project ${PROJECT} --database-version POSTGRES_16 --region ${REGION} --tier ${TIER} --storage-type SSD --storage-size 10GB --backup-start-time 03:00 --deletion-protection"
  "gcloud sql databases create ${DATABASE} --project ${PROJECT} --instance ${INSTANCE}"
  "gcloud sql users create ${USER_NAME} --project ${PROJECT} --instance ${INSTANCE} --type BUILT_IN"
)

echo "CLOUD SQL POSTGRESQL TEST PLAN"
echo "project=${PROJECT}"
echo "region=${REGION}"
echo "instance=${INSTANCE}"
echo "database=${DATABASE}"
echo "user=${USER_NAME}"
echo "tier=${TIER}"
echo "execute=${EXECUTE}"
echo
echo "Security notes:"
echo "- Use a dedicated test instance and database."
echo "- Do not open PostgreSQL to 0.0.0.0/0."
echo "- Use Cloud SQL connector or Unix socket from Cloud Run."
echo "- Store DATABASE_URL in Secret Manager or Cloud Run secret env vars."
echo "- Do not put database passwords on the command line."
echo

for cmd in "${commands[@]}"; do
  echo "+ ${cmd}"
  if [[ "$EXECUTE" == "1" ]]; then
    eval "$cmd"
  fi
done

if [[ "$EXECUTE" != "1" ]]; then
  echo
  echo "PLAN ONLY: no Cloud SQL resources were created."
fi
