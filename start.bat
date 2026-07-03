@echo off
echo Starting AI Test Platform...

echo.
echo [1/2] Starting backend (FastAPI on port 8000)...
start "Backend" cmd /k "cd backend && pip install -r requirements.txt && python run.py"

timeout /t 3 /nobreak >nul

echo [2/2] Starting frontend (Vite on port 5173)...
start "Frontend" cmd /k "cd frontend && npm install && npm run dev"

echo.
echo Backend: http://localhost:8000
echo Frontend: http://localhost:5173
echo API Docs: http://localhost:8000/docs
