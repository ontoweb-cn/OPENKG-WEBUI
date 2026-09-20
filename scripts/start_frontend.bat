@echo off
REM OPENKG-WebUI Frontend Startup Script
REM Starts the frontend Next.js development server

REM Move to the project root (this script lives in scripts/)
cd /d "%~dp0.."
echo Starting OPENKG-WebUI Frontend...
echo Frontend will be available at: http://localhost:8092
echo Press Ctrl+C to stop the server.
cd web
npm run dev -- -p 8092
pause
