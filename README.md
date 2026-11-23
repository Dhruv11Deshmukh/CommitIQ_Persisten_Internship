# CommitIQ (Commi-Sense)

AI assistant that reads Gmail, Microsoft Teams and meeting transcripts, extracts commitments
(who promised what, by when) with Gemini, ranks them, tracks dependencies and shows everything in a
React dashboard and a chat popup.

**Safety by design:** the app never sends email on its own (AI emails are saved as Gmail drafts)
and meetings are created without guests.

**Stack:** FastAPI, SQLAlchemy, PostgreSQL 16, Google Gemini, React + Vite.

```
commitment-tracker/     FastAPI app (app/main.py), requirements.txt, .env.example
commitment-tracker-ui/  React + Vite app, package.json, .env.example
docker-compose.yml   PostgreSQL 16
```

## 1. Prerequisites

| Tool | Version | Check |
| --- | --- | --- |
| Python | 3.10 or newer | `python --version` |
| Node.js | 18 or newer | `node --version` |
| Docker Desktop | any recent | `docker --version` |
| Git | any | `git --version` |

## 2. Get the code

```bash
git clone https://github.com/<your-username>/<repo-name>.git
cd <repo-name>
```

## 3. Start the database

```bash
docker compose up -d
```

This creates a PostgreSQL 16 database named `commitments` on port 5432.
(If you change the password in `docker-compose.yml`, change it in `DATABASE_URL` too.)

## 4. Get your own credentials

You need your own keys. Nothing secret is stored in this repository.

1. **Gemini API key** (required): create one at https://aistudio.google.com/apikey.
2. **Google OAuth client** (required for Gmail sign-in):
   - Open https://console.cloud.google.com, create a project.
   - Enable the **Gmail API** and **Google Calendar API**.
   - Configure the OAuth consent screen and add your own Google account as a test user.
   - Create credentials, **OAuth client ID**, type **Web application**.
   - Add the authorised redirect URI `http://localhost:8000/auth/google/callback`
     (it must match `GOOGLE_REDIRECT_URI` in your `.env`).
   - Download the JSON and save it as `commitment-tracker/credentials.json`.
3. **Microsoft Teams** (optional): register an app in Azure, give it the delegated `Chat.Read`
   permission, and copy its client ID and tenant ID. Skip this to run without Teams.

## 5. Set up the backend

```bash
cd commitment-tracker
python -m venv venv

# activate the virtual environment
#   Windows (PowerShell):  venv\Scripts\Activate.ps1
#   Windows (cmd):         venv\Scripts\activate.bat
#   macOS / Linux:         source venv/bin/activate

pip install -r requirements.txt

# create your environment file, then edit it with YOUR values
#   Windows:       copy .env.example .env
#   macOS / Linux: cp .env.example .env
```

Open `commitment-tracker/.env` and fill in at least `GEMINI_API_KEY`, `JWT_SECRET`, `OWNER_NAME`
and the `DATABASE_URL` password.

Start the server:

```bash
uvicorn app.main:app --reload
```

The API runs at http://localhost:8000 (interactive docs at http://localhost:8000/docs).
Tables are created on start-up.

## 6. Set up the frontend

Open a second terminal:

```bash
cd commitment-tracker-ui
npm install
npm run dev
```

Open http://localhost:5173 and sign in with Google.
If the backend is not on port 8000, copy `.env.example` to `.env` and change `VITE_API_URL`.

## 7. Try it

Send yourself an email such as *"I will send the Q3 report to Priya by next Friday."*
Within about a minute the commitment appears on the dashboard. Use the chat popup (bottom right)
to ask "what is due today?".

## Running the tests (optional)

```bash
cd commitment-tracker
pip install -r requirements-test.txt   # if present
pytest
```

Tests use a separate `_test` database and never call the real Gemini or Gmail.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `connection refused` on port 5432 | Docker is not running: start Docker Desktop, then `docker compose up -d`. |
| `password authentication failed` | The password in `DATABASE_URL` must equal `POSTGRES_PASSWORD` in `docker-compose.yml`. If you changed it later, run `docker compose down -v` and start again. |
| `redirect_uri_mismatch` from Google | The redirect URI in Google Cloud must exactly match `GOOGLE_REDIRECT_URI`. |
| Google says the app is not verified | Add your account as a test user on the OAuth consent screen. |
| Gemini errors or 429 | Check `GEMINI_API_KEY`; the free tier has rate limits, wait a minute and retry. |
| Port 5173 or 8000 already in use | Stop the other program or start on another port (`uvicorn app.main:app --port 8001`, and update `VITE_API_URL`). |

## Security notes

- Never commit `.env`, `credentials.json` or any token file. `.gitignore` already excludes them.
- If a key is ever pushed by mistake, revoke it immediately and create a new one.
