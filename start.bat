@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title TCG 查價機器人

where py >nul 2>nul && (py -3 server.py & goto :end)
where python >nul 2>nul && (python server.py & goto :end)
for %%P in ("%USERPROFILE%\anaconda3\python.exe" "%USERPROFILE%\anaconda30\python.exe" "%USERPROFILE%\miniconda3\python.exe" "%ProgramData%\anaconda3\python.exe") do (
  if exist %%P ( %%P server.py & goto :end )
)
echo 找不到 Python。請先安裝 Python 3（https://www.python.org/downloads/ ，安裝時勾選 Add to PATH）
:end
pause
