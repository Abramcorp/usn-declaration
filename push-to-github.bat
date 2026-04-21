@echo off
chcp 65001 >nul
echo === Загрузка проекта на GitHub ===
echo.

cd /d "%~dp0"

:: Инициализируем git (если ещё нет)
if not exist ".git" (
    git init
    git branch -M main
)

:: Настраиваем remote
git remote remove origin 2>nul
git remote add origin https://github.com/Abramcorp/usn-declaration.git

:: Создаём .gitignore если нет
if not exist ".gitignore" (
    echo __pycache__/> .gitignore
    echo *.pyc>> .gitignore
    echo *.db>> .gitignore
    echo .env>> .gitignore
    echo Include/>> .gitignore
    echo Lib/>> .gitignore
    echo Scripts/>> .gitignore
    echo *.tar.gz>> .gitignore
    echo data/declarations/>> .gitignore
    echo data/uploads/>> .gitignore
    echo app/uploads/>> .gitignore
)

:: Добавляем все файлы и коммитим
git add -A
git commit -m "Full project: USN 6%% tax declaration wizard"

:: Пушим (force чтобы перезаписать README)
git push -u origin main --force

echo.
echo === Готово! Проверьте: https://github.com/Abramcorp/usn-declaration ===
pause
