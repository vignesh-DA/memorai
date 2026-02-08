@echo off
REM Quick start script for Windows

echo ================================================
echo Long-Form Memory System - Quick Start
echo ================================================
echo.

REM Check if virtual environment exists
if not exist "venv\" (
    echo Creating virtual environment...
    python -m venv venv
    echo.
)

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate
echo.

REM Install dependencies
echo Installing dependencies...
pip install -r requirements.txt
echo.

REM Check if .env exists
if not exist ".env" (
    echo Creating .env from template...
    copy .env.example .env
    echo.
    echo ⚠️  IMPORTANT: Edit .env file with your API keys!
    echo    - OPENAI_API_KEY
    echo    - PINECONE_API_KEY
    echo    - PINECONE_ENVIRONMENT
    echo.
    pause
)

REM Start Docker services
echo Starting PostgreSQL and Redis...
docker-compose up -d postgres redis
echo.

REM Wait for services
echo Waiting for services to be ready...
timeout /t 10 /nobreak >nul
echo.

REM Initialize database
echo Initializing database schema...
python scripts\init_db.py
echo.

echo ================================================
echo ✓ Setup complete!
echo ================================================
echo.
echo Next steps:
echo   1. Edit .env with your API keys
echo   2. Run the API server:
echo      uvicorn app.main:app --reload
echo.
echo   3. In a new terminal, run Celery worker:
echo      celery -A app.worker.celery_app worker --loglevel=info
echo.
echo   4. Try the example:
echo      python example_usage.py
echo.
echo   5. Visit API docs:
echo      http://localhost:8000/docs
echo.
pause
