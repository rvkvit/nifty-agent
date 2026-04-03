# Nifty Trading Agent — Deploy on Render.com

## Step-by-Step Deployment

### 1. Push to GitHub
Create a new GitHub repo and push this folder:
```bash
cd nifty-agent-render
git init
git add .
git commit -m "Nifty Trading Agent"
git remote add origin https://github.com/YOUR_USERNAME/nifty-agent.git
git branch -M main
git push -u origin main
```

### 2. Deploy on Render
1. Go to https://render.com → Sign up / Login
2. Click "New" → "Web Service"
3. Connect your GitHub repo
4. Render auto-detects the settings, but verify:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn server:app --bind 0.0.0.0:$PORT --workers 2 --timeout 30`

### 3. Set Environment Variables
In Render dashboard → your service → "Environment":

| Key              | Value                                    |
|------------------|------------------------------------------|
| KITE_API_KEY     | your_zerodha_api_key                     |
| KITE_API_SECRET  | your_zerodha_api_secret                  |
| PUBLIC_URL       | https://your-app-name.onrender.com       |
| ACCESS_PASSWORD  | any_password_for_friends                 |

### 4. Update Zerodha Redirect URL
Go to developers.kite.trade → your app → edit:
**Redirect URL**: `https://your-app-name.onrender.com/callback`

### 5. Daily Usage
- **You (admin)**: Visit `https://your-app.onrender.com/admin/login` every morning
- **Friends**: Visit `https://your-app.onrender.com` → enter password

### 6. iPhone Home Screen App
1. Open URL in Safari
2. Tap Share button (box with arrow)
3. Tap "Add to Home Screen"
4. Name it "Nifty Agent"
5. Done — it opens full-screen like a real app!

## Files
```
├── server.py          # Flask backend with keep-alive
├── static/index.html  # Dashboard UI
├── requirements.txt   # Dependencies
├── Procfile           # Render start command
└── render.yaml        # Render config (optional)
```
