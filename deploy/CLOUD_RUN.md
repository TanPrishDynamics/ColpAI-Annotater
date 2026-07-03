# ColpAI — Deploying to Google Cloud Run (Step-by-Step)

Your project is a **Flask + Gunicorn** app that already uses **Supabase Postgres** for the database and **Supabase Storage** for images. This makes it an ideal fit for Cloud Run — no local SQLite or disk storage needed at runtime.

> ⚠️ **Your `.env` file contains your Supabase credentials. Never commit this file** (it's already in `.gitignore`). On Cloud Run, you'll set these as environment variables or secrets instead.

---

## Prerequisites

Before you start, make sure you have:
- A **Google Cloud account** — [cloud.google.com](https://cloud.google.com) (free tier includes Cloud Run)
- A **GitHub account** with your repo: `TanPrishDynamics/ColpAI-Annotater`
- The **Dockerfile** and **.dockerignore** already added to the repo root

---

## Step 1 — Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Click the **project dropdown** at the top-left → **New Project**
3. Enter a project name, e.g. `colpai-annotater`
4. Click **Create**
5. Make sure the new project is **selected** in the dropdown

---

## Step 2 — Enable Required APIs

Go to [APIs & Services → Library](https://console.cloud.google.com/apis/library) and enable these APIs (search for each and click **Enable**):

| API | Why |
|---|---|
| **Cloud Run Admin API** | To deploy and manage the service |
| **Cloud Build API** | To build Docker images from your repo |
| **Artifact Registry API** | To store your Docker images |
| **Secret Manager API** | To securely store your Supabase credentials |

> 💡 You can also enable all at once with gcloud CLI (see Step 3).

---

## Step 3 — Install Google Cloud CLI (Optional but Recommended)

Download and install from: [cloud.google.com/sdk/docs/install](https://cloud.google.com/sdk/docs/install)

After installing, open a terminal and run:

```bash
gcloud init
```

Follow the prompts to:
- Log in with your Google account
- Select your `colpai-annotater` project

Then enable the APIs:

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
```

---

## Step 4 — Push Dockerfile to GitHub

Make sure the `Dockerfile` and `.dockerignore` are committed and pushed:

```bash
git add Dockerfile .dockerignore
git commit -m "Add Dockerfile and .dockerignore for Cloud Run deployment"
git push origin main
```

> Replace `main` with your actual branch name if it's different (e.g. `master`).

---

## Step 5 — Store Secrets in Secret Manager

Your app needs sensitive credentials. **Do NOT put them directly as plain-text env vars.** Use Secret Manager instead.

### Via Google Cloud Console:

1. Go to [Secret Manager](https://console.cloud.google.com/security/secret-manager)
2. Click **Create Secret** for each of these:

| Secret Name | Value |
|---|---|
| `COLPAI_SECRET_KEY` | Generate a new one: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `COLPAI_DATABASE_URI` | Your Supabase Postgres connection string |
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Your Supabase service role JWT key |

### Via gcloud CLI:

```bash
# Generate and store a strong secret key
python -c "import secrets; print(secrets.token_hex(32))" | gcloud secrets create COLPAI_SECRET_KEY --data-file=-

# Store Supabase database URI
echo -n "postgresql+psycopg://YOUR_CONNECTION_STRING" | gcloud secrets create COLPAI_DATABASE_URI --data-file=-

# Store Supabase URL
echo -n "https://YOUR_PROJECT.supabase.co" | gcloud secrets create SUPABASE_URL --data-file=-

# Store Supabase Service Key
echo -n "YOUR_FULL_JWT_KEY_HERE" | gcloud secrets create SUPABASE_SERVICE_KEY --data-file=-
```

---

## Step 6 — Deploy via Cloud Run (Connect to GitHub)

### Option A: Deploy via Console (Easiest — Recommended for First Time)

1. Go to [Cloud Run](https://console.cloud.google.com/run)
2. Click **Create Service**
3. Select **"Continuously deploy from a repository"**
4. Click **Set Up with Cloud Build**:
   - **Source**: Click **Manage connected repositories** → authenticate with GitHub → select `TanPrishDynamics/ColpAI-Annotater`
   - **Branch**: `^main$` (or your default branch)
   - **Build type**: Select **Dockerfile** → path: `/Dockerfile`
   - Click **Save**
5. Configure the service:
   - **Service name**: `colpai-annotater`
   - **Region**: Pick one close to your users (e.g. `asia-south1` for India)
   - **Authentication**: Select **"Allow unauthenticated invocations"** (your app has its own login system)
6. Expand **Container, Volumes, Networking, Security**:

   **Container tab:**
   - **Container port**: `8080`
   - **Memory**: `512 MiB` (bump to `1 GiB` if you do large exports)
   - **CPU**: `1`
   - **Request timeout**: `300` seconds
   - **Maximum instances**: `3` (keeps costs low)
   - **Minimum instances**: `0` (scale to zero when idle — saves money)

   **Environment variables (plain text):**

   | Variable | Value |
   |---|---|
   | `COLPAI_CONFIG` | `prod` |
   | `COLPAI_STORAGE_BACKEND` | `supabase` |
   | `SUPABASE_BUCKET` | `colpai-annotetor` |
   | `COLPAI_MAX_UPLOAD_MB` | `200` |

   **Secrets (click "Reference a Secret"):**

   For each secret, click **Add a secret reference**:
   | Env Variable Name | Secret | Version |
   |---|---|---|
   | `COLPAI_SECRET_KEY` | `COLPAI_SECRET_KEY` | `latest` |
   | `COLPAI_DATABASE_URI` | `COLPAI_DATABASE_URI` | `latest` |
   | `SUPABASE_URL` | `SUPABASE_URL` | `latest` |
   | `SUPABASE_SERVICE_KEY` | `SUPABASE_SERVICE_KEY` | `latest` |

7. Click **Create**

Cloud Build will pull your code from GitHub, build the Docker image, and deploy it. This takes **3-5 minutes** the first time.

---

### Option B: Deploy via gcloud CLI

```bash
# First, grant Cloud Run access to your secrets
PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')

# Grant the Cloud Run service account permission to access secrets
for SECRET in COLPAI_SECRET_KEY COLPAI_DATABASE_URI SUPABASE_URL SUPABASE_SERVICE_KEY; do
  gcloud secrets add-iam-policy-binding $SECRET \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
done

# Deploy!
gcloud run deploy colpai-annotater \
  --source . \
  --region asia-south1 \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --memory 512Mi \
  --cpu 1 \
  --timeout 300 \
  --min-instances 0 \
  --max-instances 3 \
  --set-env-vars "COLPAI_CONFIG=prod,COLPAI_STORAGE_BACKEND=supabase,SUPABASE_BUCKET=colpai-annotetor,COLPAI_MAX_UPLOAD_MB=200" \
  --set-secrets "COLPAI_SECRET_KEY=COLPAI_SECRET_KEY:latest,COLPAI_DATABASE_URI=COLPAI_DATABASE_URI:latest,SUPABASE_URL=SUPABASE_URL:latest,SUPABASE_SERVICE_KEY=SUPABASE_SERVICE_KEY:latest"
```

---

## Step 7 — Run Database Migrations

Since your database is on Supabase (accessible from anywhere), run the migration **locally**:

```bash
# Your .env already has the Supabase connection string
flask --app wsgi db upgrade
```

This applies the migrations directly to your Supabase Postgres from your local machine.

---

## Step 8 — Create the Admin User

Same as migrations — easiest to do locally:

```bash
python -m scripts.create_user --username admin --role admin --full-name "Site Admin"
```

This connects to your Supabase Postgres (via the `COLPAI_DATABASE_URI` in `.env`).

---

## Step 9 — Verify the Deployment

1. Go to [Cloud Run](https://console.cloud.google.com/run)
2. Click on your **colpai-annotater** service
3. You'll see a URL like: `https://colpai-annotater-XXXXX-el.a.run.app`
4. Click it — you should see the **login page**
5. Test the health endpoint: `https://colpai-annotater-XXXXX-el.a.run.app/api/v1/health`
   - Should return: `{"status": "ok", "config": "prod"}`

---

## Step 10 — Continuous Deployment (Auto-Deploy on Push)

If you used **Option A** (Console with "Continuously deploy from a repository"), this is **already configured**. Every `git push` to `main` automatically:

1. Detects the push
2. Builds a new Docker image
3. Deploys it to Cloud Run with zero-downtime rollout

If you used **Option B** (CLI), set up a Cloud Build trigger:

1. Go to [Cloud Build → Triggers](https://console.cloud.google.com/cloud-build/triggers)
2. Click **Create Trigger**
3. **Name**: `deploy-colpai`
4. **Event**: Push to a branch
5. **Source**: Connect to `TanPrishDynamics/ColpAI-Annotater`
6. **Branch**: `^main$`
7. **Build configuration**: Dockerfile → `/Dockerfile`
8. Click **Create**

---

## Step 11 — Custom Domain (Optional)

Instead of the long `.run.app` URL, use your own domain:

1. Go to your Cloud Run service → **Manage Custom Domains**
2. Click **Add Mapping**
3. Select your service → enter your domain (e.g. `colpai.yourdomain.com`)
4. Google gives you a **DNS record** to add at your domain registrar
5. Add the record, wait for verification (~15 min)
6. HTTPS is **automatic** (Google manages the certificate)

---

## Architecture

```
┌─────────────┐     git push      ┌──────────────┐
│   GitHub     │ ─────────────────→│  Cloud Build  │
│   Repo       │                   │  (Dockerfile) │
└─────────────┘                   └──────┬───────┘
                                         │ deploys
                                         ▼
┌─────────────┐   HTTPS requests  ┌──────────────┐
│   Doctors   │ ─────────────────→│  Cloud Run    │
│  (Browser)  │ ←─────────────────│  (Flask App)  │
└─────────────┘                   └──────┬───────┘
                                         │
                        ┌────────────────┼────────────────┐
                        ▼                                 ▼
                ┌──────────────┐                  ┌──────────────┐
                │  Supabase    │                  │  Supabase    │
                │  Postgres    │                  │  Storage     │
                │  (Database)  │                  │  (Images)    │
                └──────────────┘                  └──────────────┘
```

---

## Cost Estimate

Cloud Run charges per use. For a small team of doctors:

| Resource | Free Tier (per month) | Estimated Usage | Cost |
|---|---|---|---|
| CPU | 180,000 vCPU-seconds | ~5,000 sec | **Free** |
| Memory | 360,000 GiB-seconds | ~10,000 sec | **Free** |
| Requests | 2 million | ~5,000 | **Free** |
| Cloud Build | 120 build-min/day | ~5 min/deploy | **Free** |
| Artifact Registry | 500 MB | ~200 MB image | **Free** |

> 💡 For a small annotation team, your Cloud Run costs will likely be **$0/month** within the free tier.

---

## Troubleshooting

### "Container failed to start"
- Check Cloud Run logs: **Service → Logs tab**
- Most common cause: missing environment variables. Verify all secrets are mapped correctly.

### "502 Bad Gateway" or timeouts
- Increase **Memory** to `1 GiB` and **Request timeout** to `300s`
- Check if gunicorn is binding to the right port (`$PORT` = `8080`)

### Database connection errors
- Verify `COLPAI_DATABASE_URI` secret value is correct
- Check that your Supabase project hasn't paused (free tier pauses after inactivity)

### "COLPAI_SECRET_KEY must be set"
- The secret isn't being injected. Check:
  1. The secret exists in Secret Manager
  2. The Cloud Run service account has `Secret Manager Secret Accessor` role
  3. The secret is referenced correctly in the service config

### Image uploads failing
- Verify `SUPABASE_SERVICE_KEY` is correctly set
- Check that the `colpai-annotetor` bucket exists in your Supabase Storage
- Confirm `COLPAI_STORAGE_BACKEND=supabase` is set

---

## Quick Reference Commands

```bash
# Check service status
gcloud run services describe colpai-annotater --region asia-south1

# View live logs
gcloud run services logs read colpai-annotater --region asia-south1 --limit 50

# Force a new deployment from latest code
gcloud run deploy colpai-annotater --source . --region asia-south1

# Update an environment variable
gcloud run services update colpai-annotater \
  --region asia-south1 \
  --update-env-vars "COLPAI_MAX_UPLOAD_MB=500"

# Scale settings
gcloud run services update colpai-annotater \
  --region asia-south1 \
  --min-instances 1 \
  --max-instances 5

# Delete the service (if needed)
gcloud run services delete colpai-annotater --region asia-south1
```
