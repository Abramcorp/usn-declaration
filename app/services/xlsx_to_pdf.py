"""
Конвертер XLSX → PDF для готовой декларации КНД 1152017.

Поскольку официальный бланк ФНС уже свёрстан в XLSX-шаблоне, самый надёжный
способ получить красивый PDF — сконвертировать именно этот заполненный XLSX
без перерисовки формы с нуля.

Стратегии (в порядке приоритета):

1. LibreOffice (soffice --headless --convert-to pdf) — кроссплатформенно,
   точно отражает верстку XLSX.
2. Microsoft Excel через COM (pywin32) — только Windows; используется если
   LibreOffice не установлен, а Excel есть.

Если ни одна стратегия не сработала — бросаем XlsxToPdfError с понятным
сообщением, что делать пользователю (установить LibreOffice).
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional


class XlsxToPdfError(RuntimeError):
    """Не удалось сконвертировать XLSX → PDF ни одним из доступных способов."""


# ---------------------------------------------------------------------------
# Поиск бинарника soffice (LibreOffice)
# ---------------------------------------------------------------------------
def _candidate_soffice_paths() -> List[str]:
    """Возможные пути к soffice на разных ОС."""
    paths: List[str] = []

    # В PATH
    for name in ("soffice", "soffice.exe", "libreoffice"):
        found = shutil.which(name)
        if found:
            paths.append(found)

    # Явные пути на Windows (типичные места установки)
    if platform.system() == "Windows":
        for base in (
            r"C:\Program Files\LibreOffice\program",
            r"C:\Program Files (x86)\LibreOffice\program",
            r"C:\Program Files\LibreOfficePortable\App\libreoffice\program",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\LibreOffice\program"),
        ):
            exe = Path(base) / "soffice.exe"
            if exe.exists():
                paths.append(str(exe))

    # Unix / macOS
    for p in (
        "/usr/bin/soffice",
        "/usr/bin/libreoffice",
        "/usr/lib/libreoffice/program/soffice",
        "/opt/libreoffice/program/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if Path(p).exists():
            paths.append(p)

    # Убираем дубликаты, сохраняя порядок
    seen = set()
    uniq: List[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def find_soffice() -> Optional[str]:
    """Вернуть путь к soffice или None, если не найден."""
    candidates = _candidate_soffice_paths()
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# Стратегия 1: LibreOffice (soffice --headless --convert-to pdf)
# ---------------------------------------------------------------------------
def _convert_with_libreoffice(xlsx_path: Path, pdf_path: Path, timeout: int = 120) -> None:
    """
    Конвертация через LibreOffice в headless-режиме.

    soffice пишет PDF в указанную директорию, используя имя исходного
    файла с расширением .pdf — потом мы переименовываем, если нужно.
    """
    soffice = find_soffice()
    if not soffice:
        raise XlsxToPdfError("LibreOffice (soffice) не найден в системе")

    xlsx_path = xlsx_path.resolve()
    out_dir = pdf_path.parent.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="lopdf_") as work_dir:
        # Используем отдельный user profile, чтобы параллельные запуски
        # не мешали друг другу (LibreOffice не любит shared profile).
        user_profile_url = Path(work_dir).as_uri()
        cmd = [
            soffice,
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            f"-env:UserInstallation={user_profile_url}",
            "--convert-to", "pdf",
            "--outdir", str(out_dir),
            str(xlsx_path),
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                # На Windows скрываем окно консоли soffice
                creationflags=(
                    subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
                    if platform.system() == "Windows" and hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0
                ),
            )
        except subprocess.TimeoutExpired as exc:
            raise XlsxToPdfError(
                f"LibreOffice не уложился в {timeout} сек: {exc}"
            ) from exc
        except FileNotFoundError as exc:
            raise XlsxToPdfError(f"Не удалось запустить soffice: {exc}") from exc

        if result.returncode != 0:
            raise XlsxToPdfError(
                f"LibreOffice вернул код {result.returncode}\n"
                f"stdout: {result.stdout[-500:]}\n"
                f"stderr: {result.stderr[-500:]}"
            )

    produced = out_dir / f"{xlsx_path.stem}.pdf"
    if not produced.exists():
        raise XlsxToPdfError(
            f"LibreOffice отработал, но файл {produced.name} не появился"
        )

    # Если целевой путь отличается от того, что создал soffice — переименуем.
    if produced.resolve() != pdf_path.resolve():
        if pdf_path.exists():
            pdf_path.unlink()
        produced.replace(pdf_path)


# ---------------------------------------------------------------------------
# Стратегия 2: Microsoft Excel через COM (Windows only)
# ---------------------------------------------------------------------------
def _convert_with_excel_com(xlsx_path: Path, pdf_path: Path) -> None:
    """
    Конвертация через Microsoft Excel (COM-автоматизация).

    Работает только на Windows при установленном Excel. Используем
    Workbook.ExportAsFixedFormat(xlTypePDF=0).
    """
    if platform.system() != "Windows":
        raise XlsxToPdfError("Excel COM доступен только в Windows")

    try:
        import win32com.client  # type: ignore
        import pythoncom  # type: ignore
    except ImportError as exc:
        raise XlsxToPdfError(
            f"pywin32 не установлен: {exc}. Нужен для fallback через Excel."
        ) from exc

    xlsx_path = xlsx_path.resolve()
    pdf_path = pdf_path.resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    pythoncom.CoInitialize()
    excel = None
    wb = None
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        wb = excel.Workbooks.Open(str(xlsx_path), ReadOnly=True)
        # 0 = xlTypePDF, Quality=0 (стандартное), IncludeDocProperties=True
        wb.ExportAsFixedFormat(0, str(pdf_path), 0, True, False)
    except Exception as exc:
        raise XlsxToPdfError(f"Excel COM не сработал: {exc}") from exc
    finally:
        try:
            if wb is not None:
                wb.Close(SaveChanges=False)
        except Exception:
            pass
        try:
            if excel is not None:
                excel.Quit()
        except Exception:
            pass
        pythoncom.CoUninitialize()


# ---------------------------------------------------------------------------
# Публичная функция
# ---------------------------------------------------------------------------
def convert_xlsx_to_pdf(
    xlsx_path: Path,
    pdf_path: Path,
    *,
    timeout: int = 120,
) -> Path:
    """
    Сконвертировать .xlsx в .pdf, перебирая доступные стратегии.

    Args:
        xlsx_path: путь к исходному заполненному XLSX.
        pdf_path:  путь, куда писать итоговый PDF.
        timeout:   таймаут на одну попытку в секундах.

    Returns:
        Path к созданному PDF (совпадает с pdf_path).

    Raises:
        XlsxToPdfError: если все стратегии не сработали.
    """
    xlsx_path = Path(xlsx_path)
    pdf_path = Path(pdf_path)
    if not xlsx_path.exists():
        raise XlsxToPdfError(f"Исходный XLSX не найден: {xlsx_path}")

    errors: List[str] = []

    # 1) LibreOffice
    try:
        _convert_with_libreoffice(xlsx_path, pdf_path, timeout=timeout)
        return pdf_path
    except XlsxToPdfError as exc:
        errors.append(f"LibreOffice: {exc}")

    # 2) Excel COM (Windows)
    if platform.system() == "Windows":
        try:
            _convert_with_excel_com(xlsx_path, pdf_path)
            return pdf_path
        except XlsxToPdfError as exc:
            errors.append(f"Excel COM: {exc}")

    details = "\n  — " + "\n  — ".join(errors) if errors else ""
    raise XlsxToPdfError(
        "Не удалось сконвертировать XLSX в PDF ни одним способом.\n"
        "Установите LibreOffice (бесплатно, https://www.libreoffice.org/download/) — "
        "после установки запустите декларацию заново."
        + details
    )
