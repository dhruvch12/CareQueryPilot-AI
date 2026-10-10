
# CareQueryPilot AI

An AI-powered customer support triage SaaS built to streamline
ticket analysis, priority assessment, and support workflows.

## Features

- Customer ticket analysis using Jev AI integration
- Priority and urgency assessment
- Department routing recommendations
- Suggested customer replies
- Company workspaces and authentication
- Ticket history and business insights
- CSV import and export

## Tech Stack

- Python
- FastAPI
- Jev AI API integration
- SQLite
- HTML, CSS, and JavaScript
- Uvicorn

## Run Locally

1. Clone this repository.
2. Create and activate a Python virtual environment.
3. Install dependencies:

   pip install -r requirements.txt

4. Create your local `.env` file with the required
   environment variables.
5. Start the application:

   python -m uvicorn app:app --reload

6. Open http://127.0.0.1:8000

## Security

Never commit API tokens, session secrets, environment files,
or private customer data.

## Status

Personal AI engineering project and working MVP.
