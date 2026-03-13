# MemorAI Vercel Deployment Guide

## Architecture Overview

MemorAI uses a **split-deployment architecture**:
- **Frontend**: Static vanilla JavaScript site deployed on **Vercel**
- **Backend**: FastAPI application deployed on **Render**, **Railway**, or similar
- **Database**: PostgreSQL 16 + pgvector (managed service like Neon or Supabase)

```
┌─────────────────┐         ┌──────────────────┐         ┌─────────────────┐
│   Vercel CDN    │────────▶│ Backend Service  │────────▶│  PostgreSQL +   │
│   (Frontend)    │ HTTPS   │ (Render/Railway) │         │ Pgvector        │
└─────────────────┘         └──────────────────┘         └─────────────────┘
                                     │
                                     ▼
                            ┌──────────────────┐
                            │  Redis (Upstash) │
                            │  Pinecone        │
                            │  Groq API        │
                            └──────────────────┘
```

## Step 1: Prepare Your Code for Vercel

### Frontend Configuration ✅ (Already Done)
Your frontend is already configured for Vercel:
- `vercel.json` - Deployment configuration
- `frontend/config.js` - Environment-based API URL handling
- `frontend/index.html` - Loads config before app.js
- `frontend/app.js` - Supports environment variable injection

### Environment Variable
The frontend uses `VITE_API_BASE_URL` to connect to your backend. This will be set in Vercel dashboard.

## Step 2: Deploy Frontend to Vercel

### 2a. Create GitHub Repository
```bash
# Initialize git repo
git init
git add .
git commit -m "Initial commit: MemorAI with Vercel configuration"

# Push to GitHub
git remote add origin https://github.com/YOUR_USERNAME/memorai.git
git branch -M main
git push -u origin main
```

### 2b. Connect to Vercel
1. Go to [vercel.com](https://vercel.com)
2. Sign in with GitHub account
3. Click **"New Project"**
4. Select your `memorai` repository
5. Click **Import**

### 2c. Configure Environment Variable
1. In **Settings** → **Environment Variables**
2. Add new variable:
   - **Name**: `VITE_API_BASE_URL`
   - **Value**: (leave empty for now, you'll set this after deploying backend)
3. Click **Save**

### 2d. Deploy
Click **Deploy**. Vercel will:
- Run the build command (generates `config.js` if env var is set)
- Deploy frontend to `https://your-project.vercel.app`
- Live in ~1-2 minutes

## Step 3: Deploy Backend

Choose one of these platforms:

### Option A: Render (Recommended - Simplest)

1. **Create Render Account**: [render.com](https://render.com)
2. **Connect GitHub**: Auth with your GitHub account
3. **Create New Service**:
   - Service Type: **Web Service**
   - Repository: Select `memorai`
   - Branch: `main`
   - Runtime: **Python 3.11**
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. **Configure Environment Variables**:
   ```
   DATABASE_URL=postgresql://user:pass@host:5432/long_form_memory
   REDIS_URL=redis://user:pass@host:6379
   GROQ_API_KEY=your-groq-key
   PINECONE_API_KEY=your-pinecone-key
   PINECONE_INDEX=your-index-name
   JWT_SECRET_KEY=your-secret
   ```
5. **Deploy**: Render auto-deploys on GitHub push
6. **Get URL**: Copy your service URL (e.g., `https://memorai-api.onrender.com`)

### Option B: Railway

1. **Create Railway Account**: [railway.app](https://railway.app)
2. **Create New Project** → **Deploy from GitHub**
3. **Select Repository**: Choose `memorai`
4. **Add PostgreSQL**: Railway will add database automatically
5. **Configure Environment Variables** (same as Render)
6. **Deploy**: Auto-deploys on push
7. **Get URL**: Copy generated domain

### Option C: Fly.io

1. **Install Fly CLI**: `curl -L https://fly.io/install.sh | sh`
2. **Login**: `flyctl auth login`
3. **Create App**: `flyctl launch` in project directory
4. **Configure secrets**: `flyctl secrets set KEY=VALUE`
5. **Deploy**: `flyctl deploy`

## Step 4: Database Setup

### Option A: Neon (PostgreSQL Hosting)

1. Go to [neon.tech](https://neon.tech)
2. Create new database
3. Copy connection string: `postgresql://user:pass@host/dbname`
4. Set as `DATABASE_URL` in your backend environment

### Option B: Supabase

1. Go to [supabase.com](https://supabase.com)
2. Create new project with PostgreSQL 16
3. Create pgvector extension:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```
4. Copy connection string from settings

## Step 5: Connect Frontend to Backend

1. **Get your backend URL**:
   - Render: `https://memorai-api.onrender.com`
   - Railway/Fly: (from deployment dashboard)

2. **Update Vercel Environment Variable**:
   - Go to Vercel → Project Settings → Environment Variables
   - Update `VITE_API_BASE_URL` to: `https://your-backend-url/api/v1`
   - Example: `https://memorai-api.onrender.com/api/v1`
   - Click **Save**

3. **Redeploy Frontend**:
   - Vercel → Deployments → Click **Redeploy** on latest deployment
   - Or push a new commit to main
   - Wait ~1-2 minutes for deployment

## Step 6: Test the Deployment

1. Open your Vercel frontend: `https://your-project.vercel.app/app`
2. Test features:
   - Sign up / Login
   - Chat message
   - Upload PDF/image
   - Memory retrieval (in logs: should see "Retrieved X memories")

3. Check logs:
   - **Frontend**: Vercel Analytics
   - **Backend**: `flyctl logs` (Fly) or Render/Railway dashboard

## Environment Variables Reference

### Backend Required Variables
```
# Database
DATABASE_URL=postgresql://user:pass@host:5432/long_form_memory

# Cache
REDIS_URL=redis://user:pass@host:6379

# LLM
GROQ_API_KEY=gsk_... (from console.groq.com)

# Vector Store
PINECONE_API_KEY=... (from pinecone.io)
PINECONE_INDEX=memories

# Auth
JWT_SECRET_KEY=super-secret-change-this (min 32 chars)
JWT_ALGORITHM=HS256

# CORS (for frontend domain)
FRONTEND_URL=https://your-project.vercel.app
```

### Frontend Configuration
```
VITE_API_BASE_URL=https://your-backend-url/api/v1
```

## Production Checklist

- [ ] Backend DATABASE_URL configured with production database
- [ ] Redis connection secured (use Upstash for managed Redis)
- [ ] Groq API key added and tested
- [ ] Pinecone index created and API key added
- [ ] JWT_SECRET_KEY set to strong random value (NOT default)
- [ ] Backend deployed and responding at `https://your-backend-url/health`
- [ ] Frontend VITE_API_BASE_URL updated with backend URL
- [ ] Frontend redeployed to pick up new API URL
- [ ] Login/signup working
- [ ] Chat endpoint returning 200 responses
- [ ] Memory system logging retrievals (check backend logs)

## Troubleshooting

### Frontend Cannot Connect to Backend
**Error**: "Failed to fetch" in browser console
**Cause**: VITE_API_BASE_URL not set or incorrect
**Fix**:
1. Check Vercel → Settings → Environment Variables
2. Verify `VITE_API_BASE_URL=https://your-backend-url/api/v1`
3. Click **Redeploy** on Vercel
4. Hard refresh browser (Ctrl+Shift+R or Cmd+Shift+R)

### Backend Returns 401 Unauthorized
**Cause**: JWT_SECRET_KEY changed or mismatched
**Fix**: Logout completely, clear browser storage, login again

### Memory Retrieval Not Working
**Error**: Memory retrieval returning 0 results
**Check**:
1. Backend logs: `flyctl logs` or Render dashboard
2. Verify Pinecone connection: `PINECONE_API_KEY` and `PINECONE_INDEX`
3. Check PostgreSQL: Has memories been stored?

### Database Connection Failed
**Error**: `FATAL: Ident authentication failed`
**Cause**: Incorrect DATABASE_URL
**Fix**:
1. Copy connection string directly from your database provider
2. Update in backend environment variables
3. Restart backend (redeploy)

## Monitoring

### Vercel Analytics
- Frontend performance metrics
- Visit [vercel.com/analytics](https://vercel.com/analytics)

### Backend Logs
- **Render**: Logs tab in dashboard
- **Railway**: Logs in project view
- **Fly**: `flyctl logs`

### Database Monitoring
- **Neon**: Monitoring tab in dashboard
- **Supabase**: Logs and metrics in console

## Optional: Custom Domain

1. Add domain in Vercel → Settings → Domains
2. Update DNS records (Vercel provides instructions)
3. Frontend auto-redirects HTTPS traffic

## Next Steps

After successful deployment:
1. Share `https://your-project.vercel.app` with users
2. Monitor logs for errors
3. Add custom domain (optional)
4. Setup monitoring alerts
5. Plan for database backups
6. Configure auto-scaling if needed

## Support

For issues:
- **Vercel**: [vercel.com/docs](https://vercel.com/docs)
- **Render**: [render.com/docs](https://render.com/docs)
- **FastAPI**: [fastapi.tiangolo.com](https://fastapi.tiangolo.com)
- **PostgreSQL**: [postgresql.org/docs](https://www.postgresql.org/docs)
