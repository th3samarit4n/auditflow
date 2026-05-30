# AuditFlow AI

This is a FastAPI app built with Uvicorn, SQLite, and Groq for AI content generation.

## Railway Deployment

1. Push this repository to GitHub.
2. Create a new Railway project and connect your GitHub repository.
3. Set environment variables in Railway:
   - `GROQ_API_KEY`
   - `PAGESPEED_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Railway should detect Python and install dependencies from `requirements.txt`.
5. Ensure the start command is:
   `uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}`

## Notes

- `leads.db` is a local SQLite database. Railway containers are ephemeral, so consider using a managed database for production persistence.
