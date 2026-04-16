# GCP Cloud Run Deployment Guide

Enterprise deployment of the Client Clearing Lead Gen Engine on Google Cloud Platform.

## Architecture

```
                    ┌─────────────────┐
                    │   Cloud DNS      │
                    │  yourdomain.com  │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  Load Balancer   │
                    │   (HTTPS/TLS)    │
                    └───┬─────────┬───┘
                        │         │
              ┌─────────▼──┐  ┌──▼─────────┐
              │  Frontend   │  │  Backend    │
              │  Cloud Run  │  │  Cloud Run  │
              │  (Next.js)  │  │  (FastAPI)  │
              └──────┬──────┘  └──────┬──────┘
                     │                │
                     │     ┌──────────▼──────────┐
                     │     │  Cloud SQL / Neon    │
                     └─────►  (PostgreSQL 15)     │
                           └─────────────────────┘
```

Both services → Cloud Run (NOT Vercel) per PRD requirement.

---

## Prerequisites

1. **GCP Project** with billing enabled
2. **gcloud CLI** installed: https://cloud.google.com/sdk/docs/install
3. **Docker** installed locally
4. **Domain name** (optional but recommended for HTTPS)

---

## Step 1: GCP Project Setup

```bash
# Set your project ID
export PROJECT_ID=your-gcp-project-id
export REGION=us-east1

# Authenticate
gcloud auth login
gcloud config set project $PROJECT_ID

# Enable required APIs
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  sqladmin.googleapis.com

# Create Artifact Registry for Docker images
gcloud artifacts repositories create lead-gen-engine \
  --repository-format=docker \
  --location=$REGION \
  --description="Lead Gen Engine Docker images"

# Configure Docker auth for GCP
gcloud auth configure-docker $REGION-docker.pkg.dev
```

---

## Step 2: Store Secrets in GCP Secret Manager

```bash
# Create secrets (do this for each sensitive value)
echo -n "your-better-auth-secret" | \
  gcloud secrets create BETTER_AUTH_SECRET --data-file=-

echo -n "your-database-url" | \
  gcloud secrets create DATABASE_URL --data-file=-

echo -n "your-gemini-api-key" | \
  gcloud secrets create GEMINI_API_KEY --data-file=-

echo -n "your-apollo-api-key" | \
  gcloud secrets create APOLLO_API_KEY --data-file=-

echo -n "your-resend-api-key" | \
  gcloud secrets create RESEND_API_KEY --data-file=-
```

---

## Step 3: Build & Push Docker Images

From the project root directory:

```bash
export REGISTRY=$REGION-docker.pkg.dev/$PROJECT_ID/lead-gen-engine

# Build and push Backend
docker build -t $REGISTRY/backend:latest ./backend
docker push $REGISTRY/backend:latest

# Build and push Frontend
docker build \
  --build-arg NEXT_PUBLIC_APP_URL=https://yourdomain.com \
  -t $REGISTRY/frontend:latest \
  ./frontend
docker push $REGISTRY/frontend:latest
```

---

## Step 4: Deploy Backend to Cloud Run

```bash
gcloud run deploy lead-gen-backend \
  --image=$REGISTRY/backend:latest \
  --region=$REGION \
  --platform=managed \
  --port=8000 \
  --memory=2Gi \
  --cpu=2 \
  --min-instances=1 \
  --max-instances=5 \
  --timeout=300 \
  --allow-unauthenticated \
  --set-env-vars="ENVIRONMENT=production" \
  --set-env-vars="API_V1_PREFIX=/api/v1" \
  --set-env-vars="BACKEND_CORS_ORIGINS=https://yourdomain.com" \
  --set-env-vars="DATA_SOURCE_MODE=live" \
  --set-env-vars="LLM_PROVIDER=gemini" \
  --set-env-vars="GEMINI_PDF_MODEL=gemini-2.5-flash" \
  --set-env-vars="CONTACT_ENRICHMENT_PROVIDER=apollo" \
  --set-env-vars="SEC_USER_AGENT=YourCompany ops@yourcompany.com" \
  --set-secrets="DATABASE_URL=DATABASE_URL:latest" \
  --set-secrets="BETTER_AUTH_SECRET=BETTER_AUTH_SECRET:latest" \
  --set-secrets="GEMINI_API_KEY=GEMINI_API_KEY:latest" \
  --set-secrets="APOLLO_API_KEY=APOLLO_API_KEY:latest"

# Get the backend URL
BACKEND_URL=$(gcloud run services describe lead-gen-backend --region=$REGION --format='value(status.url)')
echo "Backend URL: $BACKEND_URL"
```

---

## Step 5: Deploy Frontend to Cloud Run

```bash
gcloud run deploy lead-gen-frontend \
  --image=$REGISTRY/frontend:latest \
  --region=$REGION \
  --platform=managed \
  --port=3000 \
  --memory=1Gi \
  --cpu=1 \
  --min-instances=1 \
  --max-instances=3 \
  --timeout=60 \
  --allow-unauthenticated \
  --set-env-vars="NODE_ENV=production" \
  --set-env-vars="NEXT_PUBLIC_APP_URL=https://yourdomain.com" \
  --set-env-vars="INTERNAL_API_URL=$BACKEND_URL" \
  --set-secrets="DATABASE_URL=DATABASE_URL:latest" \
  --set-secrets="BETTER_AUTH_SECRET=BETTER_AUTH_SECRET:latest" \
  --set-secrets="RESEND_API_KEY=RESEND_API_KEY:latest"

# Get the frontend URL
FRONTEND_URL=$(gcloud run services describe lead-gen-frontend --region=$REGION --format='value(status.url)')
echo "Frontend URL: $FRONTEND_URL"
```

---

## Step 6: Custom Domain + HTTPS

```bash
# Map your domain to the frontend service
gcloud run domain-mappings create \
  --service=lead-gen-frontend \
  --domain=yourdomain.com \
  --region=$REGION

# Map API subdomain to backend
gcloud run domain-mappings create \
  --service=lead-gen-backend \
  --domain=api.yourdomain.com \
  --region=$REGION
```

Then add the DNS records shown by gcloud to your domain registrar.
Cloud Run provides free managed TLS certificates automatically.

---

## Step 7: Update CORS and Auth URLs

After domain setup, update the backend service:

```bash
gcloud run services update lead-gen-backend \
  --region=$REGION \
  --set-env-vars="BACKEND_CORS_ORIGINS=https://yourdomain.com"

gcloud run services update lead-gen-frontend \
  --region=$REGION \
  --set-env-vars="NEXT_PUBLIC_APP_URL=https://yourdomain.com" \
  --set-env-vars="BETTER_AUTH_URL=https://yourdomain.com" \
  --set-env-vars="INTERNAL_API_URL=https://api.yourdomain.com"
```

---

## Quick Test (Without Custom Domain)

If you just want to test without a custom domain, use the Cloud Run URLs directly:

```bash
# Update backend CORS to allow the Cloud Run frontend URL
gcloud run services update lead-gen-backend \
  --region=$REGION \
  --set-env-vars="BACKEND_CORS_ORIGINS=$FRONTEND_URL"

# Update frontend to use Cloud Run backend URL
gcloud run services update lead-gen-frontend \
  --region=$REGION \
  --set-env-vars="INTERNAL_API_URL=$BACKEND_URL" \
  --set-env-vars="NEXT_PUBLIC_APP_URL=$FRONTEND_URL" \
  --set-env-vars="BETTER_AUTH_URL=$FRONTEND_URL"
```

---

## Local Docker Test (Before GCP)

Test the production build locally first:

```bash
# From project root
docker-compose -f docker-compose.prod.yml up --build
```

Then open http://localhost:3000

---

## CI/CD with GitHub Actions (Optional)

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy to Cloud Run

on:
  push:
    branches: [main]

env:
  PROJECT_ID: your-gcp-project-id
  REGION: us-east1
  REGISTRY: us-east1-docker.pkg.dev/your-gcp-project-id/lead-gen-engine

jobs:
  deploy-backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: google-github-actions/auth@v2
        with:
          credentials_json: ${{ secrets.GCP_SA_KEY }}
      - uses: google-github-actions/setup-gcloud@v2
      - run: gcloud auth configure-docker ${{ env.REGION }}-docker.pkg.dev
      - run: |
          docker build -t $REGISTRY/backend:${{ github.sha }} ./backend
          docker push $REGISTRY/backend:${{ github.sha }}
          gcloud run deploy lead-gen-backend \
            --image=$REGISTRY/backend:${{ github.sha }} \
            --region=$REGION

  deploy-frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: google-github-actions/auth@v2
        with:
          credentials_json: ${{ secrets.GCP_SA_KEY }}
      - uses: google-github-actions/setup-gcloud@v2
      - run: gcloud auth configure-docker ${{ env.REGION }}-docker.pkg.dev
      - run: |
          docker build \
            --build-arg NEXT_PUBLIC_APP_URL=https://yourdomain.com \
            -t $REGISTRY/frontend:${{ github.sha }} ./frontend
          docker push $REGISTRY/frontend:${{ github.sha }}
          gcloud run deploy lead-gen-frontend \
            --image=$REGISTRY/frontend:${{ github.sha }} \
            --region=$REGION
```

---

## Cost Estimate (Monthly)

| Service | Config | Est. Cost |
|---------|--------|-----------|
| Cloud Run Backend | 1-5 instances, 2GB/2vCPU | $15-50 |
| Cloud Run Frontend | 1-3 instances, 1GB/1vCPU | $10-30 |
| Cloud SQL (if used) | db-f1-micro | $8-15 |
| Neon DB (current) | Free tier / Pro | $0-19 |
| Secret Manager | ~10 secrets | <$1 |
| Load Balancer + SSL | Managed | Free with Cloud Run |
| **Total** | | **$25-100/month** |

---

## Checklist Before Going Live

- [ ] All secrets in GCP Secret Manager (not in code)
- [ ] CORS restricted to production domain only
- [ ] HTTPS enabled with valid certificate
- [ ] `ENVIRONMENT=production` set on both services
- [ ] `useSecureCookies: true` in BetterAuth (auto when ENVIRONMENT=production)
- [ ] Backend min-instances=1 (cold start protection)
- [ ] Database backups configured
- [ ] Resend EMAIL_FROM set to a verified domain (not onboarding@resend.dev)
- [ ] SEC_USER_AGENT set to real company info
- [ ] Test login/logout, data display, export, alerts
