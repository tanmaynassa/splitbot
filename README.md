# SplitBot 🛒

A Telegram bot that splits grocery bills with your flatmates and logs expenses to Splitwise automatically. Works with Zepto, Blinkit, and other grocery invoices.

## User Flow

1. User sends `/start` → bot sends Splitwise OAuth link
2. User authorizes → bot asks "Who do you split with?"
3. User sends names → bot finds them on Splitwise → confirms
4. Setup done! User sends grocery invoice PDF anytime
5. Bot shows item list → user tags items → bot splits and logs to Splitwise

## Setup

### 1. Create Telegram Bot
- Message `@BotFather` → `/newbot` → copy token

### 2. Splitwise OAuth App
- Go to https://secure.splitwise.com/oauth_clients
- Register app with callback URL: `https://YOUR-APP.onrender.com/auth/callback`
- Copy Consumer Key and Consumer Secret

### 3. Supabase Database
- Go to https://supabase.com → create free project
- Go to SQL Editor → paste and run `schema.sql`
- Go to Settings → API → copy Project URL and anon key

### 4. Deploy on Render
- Push to GitHub
- New Web Service → connect repo
- Build: `pip install -r requirements.txt`
- Start: `python app.py`
- Add env vars: TELEGRAM_BOT_TOKEN, SPLITWISE_CONSUMER_KEY, SPLITWISE_CONSUMER_SECRET, SUPABASE_URL, SUPABASE_KEY

### 5. Update Splitwise Callback URL
- After Render deploys, copy your Render URL
- Go to Splitwise OAuth clients → update callback URL to: `https://YOUR-APP.onrender.com/auth/callback`

## Commands
- `/start` — connect Splitwise and set up
- `/setup` — change flatmates
- `/reset` — disconnect and start over
- `/cancel` — cancel current operation
