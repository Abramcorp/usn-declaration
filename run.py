#!/usr/bin/env python3
"""
Налоговая декларация ИП на УСН 6%
Запуск: python run.py

Зависимости ставит ЗАПУСТИТЬ.bat (на Linux/Mac — ставьте вручную через pip).
"""

import os
import sys
import subprocess
import webbrowser
import threading
import time


REQUIRED = ["fastapi", "uvicorn", "sqlalchemy", "openpyxl", "pydantic", "aiofiles"]
OPTIONAL = ["reportlab"]  # Не обязательные — старый PDF генератор


def check_dependencies():
    """Вернуть список недостающих пакетов."""
    missing = []
    for mod in REQUIRED:
        try:
            __import__(mod if mod != "multipart" else "multipart")
        except ImportError:
            missing.append(mod)
    return missing


def open_browser(port: int):
    """
    Открыть браузер через 3 секунды после старта.
    Перебирает несколько способов — webbrowser / os.startfile / start.
    """
    url = f"http://localhost:{port}"
    time.sleep(3)

    opened = False
    try:
        opened = webbrowser.open(url, new=2, autoraise=True)
    except Exception:
        opened = False

    if not opened and sys.platform == "win32":
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            opened = True
        except Exception:
            try:
                subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
                opened = True
            except Exception:
                opened = False

    if not opened and sys.platform == "darwin":
        try:
            subprocess.Popen(["open", url])
            opened = True
        except Exception:
            opened = False

    if not opened and sys.platform.startswith("linux"):
        for cmd in (["xdg-open", url], ["sensible-browser", url]):
            try:
                subprocess.Popen(cmd)
                opened = True
                break
            except Exception:
                continue

    if not opened:
        print()
        print("=" * 68)
        print("  [!] Не удалось автоматически открыть браузер.")
        print(f"  [!] Откройте вручную: {url}")
        print("=" * 68)


def main():
    # Перейти в директорию проекта
    project_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_dir)

    # Проверить зависимости (установка — в bat-файле)
    missing = check_dependencies()
    if missing:
        print()
        print("=" * 68)
        print("  [ОШИБКА] Не установлены библиотеки:")
        print("     " + ", ".join(missing))
        print()
        print("  Запустите ЗАПУСТИТЬ.bat — он автоматически поставит их.")
        print("  Либо вручную: pip install -r requirements.txt")
        print("=" * 68)
        # НЕ делаем sys.exit, чтобы bat мог перехватить и показать паузу
        return 1

    try:
        import uvicorn
    except ImportError as e:
        print(f"[ОШИБКА] Не удалось импортировать uvicorn: {e}")
        return 1

    # Проверить что приложение импортируется
    try:
        from app.main import app  # noqa: F401
    except Exception as e:
        print()
        print("=" * 68)
        print("  [ОШИБКА] Не удалось загрузить приложение:")
        print(f"     {type(e).__name__}: {e}")
        print()
        import traceback
        traceback.print_exc()
        print("=" * 68)
        return 1

    # Создать папки
    os.makedirs("data", exist_ok=True)
    os.makedirs("uploads", exist_ok=True)

    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "127.0.0.1")
    url = f"http://localhost:{port}"

    print()
    print("=" * 68)
    print("   Налоговая декларация ИП на УСН 6%")
    print("=" * 68)
    print()
    print(f"   >>>  ОТКРОЙТЕ В БРАУЗЕРЕ:  {url}  <<<")
    print()
    print("   (сейчас попробую открыть автоматически через 3 секунды)")
    print("   Остановка сервера: Ctrl+C или закройте это окно.")
    print("=" * 68)
    print()

    # Открыть браузер
    if os.environ.get("NO_BROWSER") != "1":
        threading.Thread(target=open_browser, args=(port,), daemon=True).start()

    try:
        uvicorn.run(
            "app.main:app",
            host=host,
            port=port,
            reload=False,
            log_level="info",
        )
    except OSError as e:
        if "10048" in str(e) or "Address already in use" in str(e):
            print()
            print("=" * 68)
            print("  [ОШИБКА] Порт 8000 уже занят другим процессом.")
            print("  Закройте старое окно ЗАПУСТИТЬ.bat или перезагрузите компьютер.")
            print("=" * 68)
            return 1
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
