"""
Bank statement parser for Russian tax declaration system.

Supports multiple Russian bank statement formats:
- 1C Bank Statement format
- Tab/pipe-separated tables
- Structured text with headers
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple
from pathlib import Path


class BankStatementParser:
    """Parse Russian bank statement .txt files in various formats."""

    # Common Russian date separators and formats
    DATE_PATTERNS = [
        (r'(\d{2})\.(\d{2})\.(\d{4})', '%d.%m.%Y'),      # DD.MM.YYYY
        (r'(\d{2})/(\d{2})/(\d{4})', '%d/%m/%Y'),        # DD/MM/YYYY
        (r'(\d{4})-(\d{2})-(\d{2})', '%Y-%m-%d'),        # YYYY-MM-DD
    ]

    # 1C format field patterns (Russian)
    FIELD_PATTERNS_1C = {
        'document_type': r'СекцияДокумент\s*=\s*(.+)',
        'number': r'Номер\s*=\s*(.+)',
        'date': r'Дата\s*=\s*(.+)',
        'amount': r'Сумма\s*=\s*(.+)',
        'payer_account': r'ПлательщикСчет\s*=\s*(.+)',
        'payer': r'Плательщик\s*=\s*(.+)',
        'payer_inn': r'ПлательщикИНН\s*=\s*(.+)',
        'recipient_account': r'ПолучательСчет\s*=\s*(.+)',
        'recipient': r'Получатель\s*=\s*(.+)',
        'recipient_inn': r'ПолучательИНН\s*=\s*(.+)',
        'purpose': r'НазначениеПлатежа\s*=\s*(.+)',
        'document_end': r'КонецДокумента',
    }

    ENCODINGS_TO_TRY = ['cp1251', 'utf-8', 'utf-8-sig', 'latin-1']

    def __init__(self):
        """Initialize the parser."""
        pass

    def parse(self, file_path: str, encoding: Optional[str] = None) -> Dict:
        """
        Parse a bank statement file.

        Args:
            file_path: Path to the .txt file
            encoding: Explicit encoding (if None, will auto-detect)

        Returns:
            Dictionary with parsed data:
            {
                "format_detected": str,
                "account_number": str | None,
                "period_start": date | None,
                "period_end": date | None,
                "opening_balance": Decimal | None,
                "closing_balance": Decimal | None,
                "operations": [
                    {
                        "operation_date": date,
                        "posting_date": date | None,
                        "amount": Decimal,
                        "direction": "income" | "expense",
                        "purpose": str,
                        "counterparty": str | None,
                        "counterparty_inn": str | None,
                        "counterparty_account": str | None,
                        "document_number": str | None,
                    }
                ],
                "errors": [str],
                "warnings": [str],
                "total_operations": int,
                "total_income": Decimal,
                "total_expense": Decimal,
            }
        """
        result = {
            "format_detected": None,
            "account_number": None,
            "owner_inn": None,
            "owner_name": None,
            "period_start": None,
            "period_end": None,
            "opening_balance": None,
            "closing_balance": None,
            "operations": [],
            "errors": [],
            "warnings": [],
            "total_operations": 0,
            "total_income": Decimal("0.00"),
            "total_expense": Decimal("0.00"),
        }

        # Read file
        content = self._read_file(file_path, encoding)
        if content is None:
            result["errors"].append(f"Could not read file {file_path}")
            return result

        # Detect format
        detected_format = self._detect_format(content)
        result["format_detected"] = detected_format

        # Parse based on format
        if detected_format == "1c":
            parsed = self._parse_1c_format(content)
        elif detected_format in ["pipe", "tab", "semicolon"]:
            delimiter_map = {"pipe": "|", "tab": "\t", "semicolon": ";"}
            parsed = self._parse_table_format(
                content, delimiter_map[detected_format]
            )
        elif detected_format == "structured_text":
            parsed = self._parse_structured_text(content)
        else:
            # Try all table formats as fallback before giving up
            for delim_name, delim_char in [("semicolon", ";"), ("tab", "\t"), ("pipe", "|")]:
                parsed = self._parse_table_format(content, delim_char)
                if parsed.get("operations"):
                    result["format_detected"] = delim_name + "_fallback"
                    break
            else:
                # Last resort: try structured text
                parsed = self._parse_structured_text(content)
                if not parsed.get("operations"):
                    result["errors"].append("Формат файла не распознан. Поддерживаются: 1С, таблицы (;/табуляция/|), структурированный текст.")
                    return result

        # Merge parsed results
        result.update(parsed)

        # Calculate totals
        for op in result["operations"]:
            if op.get("direction") == "income":
                result["total_income"] += op.get("amount", Decimal("0"))
            elif op.get("direction") == "expense":
                result["total_expense"] += op.get("amount", Decimal("0"))

        result["total_operations"] = len(result["operations"])

        return result

    def _read_file(self, file_path: str, encoding: Optional[str] = None) -> Optional[str]:
        """
        Read file with auto-detection of encoding.

        Args:
            file_path: Path to file
            encoding: Explicit encoding to use (if None, auto-detect)

        Returns:
            File content or None if failed
        """
        encodings_to_try = [encoding] if encoding else self.ENCODINGS_TO_TRY

        for enc in encodings_to_try:
            try:
                with open(file_path, 'r', encoding=enc) as f:
                    return f.read()
            except (UnicodeDecodeError, LookupError):
                continue

        return None

    def _detect_format(self, content: str) -> Optional[str]:
        """
        Detect which bank statement format.

        Args:
            content: File content

        Returns:
            Format identifier: "1c", "pipe", "tab", "semicolon", "structured_text", or None
        """
        content_lower = content.lower()

        # Check for 1C format markers
        if any(
            marker in content
            for marker in [
                "СекцияДокумент",
                "ПлательщикСчет",
                "ПолучательСчет",
                "КонецДокумента",
            ]
        ):
            return "1c"

        # Check for structured text format
        if any(
            marker in content_lower
            for marker in [
                "выписка по счету",
                "выписка банка",
                "банковская выписка",
                "счет",
            ]
        ):
            # Check if it's a table
            lines = content.split('\n')
            if len(lines) > 1:
                first_line = lines[0]
                if '|' in first_line:
                    return "pipe"
                elif '\t' in first_line:
                    return "tab"
                elif ';' in first_line:
                    return "semicolon"
            return "structured_text"

        # Check for table format
        lines = content.split('\n')
        if len(lines) > 1:
            first_line = lines[0]
            if '|' in first_line and '\t' not in first_line:
                return "pipe"
            elif '\t' in first_line:
                return "tab"
            elif ';' in first_line and '|' not in first_line:
                return "semicolon"

        return None

    def _parse_1c_format(self, content: str) -> Dict:
        """
        Parse 1C Client-Bank Exchange format (1CClientBankExchange).

        Structure:
        1. Global header: 1CClientBankExchange, ВерсияФормата, РасчСчет, ДатаНачала, ДатаКонца
        2. СекцияРасчСчет blocks: daily balance summaries (not individual operations)
        3. СекцияДокумент blocks: actual payment documents with payer/recipient details

        Direction logic:
        - Extract our account (РасчСчет) from the global header
        - For each document: if ПлательщикСчет == our account → expense (we pay out)
        - For each document: if ПолучательСчет == our account → income (we receive)

        Args:
            content: File content

        Returns:
            Parsed data dictionary
        """
        result = {
            "account_number": None,
            "owner_inn": None,
            "owner_name": None,
            "period_start": None,
            "period_end": None,
            "opening_balance": None,
            "closing_balance": None,
            "operations": [],
            "errors": [],
            "warnings": [],
        }

        # ============================================================
        # STEP 1: Parse global header to extract our account number
        # ============================================================
        # The header is everything before the first СекцияДокумент or СекцияРасчСчет
        header_end = len(content)
        for marker in ['СекцияДокумент', 'СекцияРасчСчет']:
            pos = content.find(marker)
            if pos != -1 and pos < header_end:
                header_end = pos
        header = content[:header_end]

        # Extract our account from global header (РасчСчет=XXXX)
        our_account = None
        acc_match = re.search(r'^РасчСчет\s*=\s*(\d{20})', header, re.MULTILINE)
        if acc_match:
            our_account = acc_match.group(1).strip()
            result["account_number"] = our_account

        # Extract period dates from header
        period_start_match = re.search(r'^ДатаНачала\s*=\s*(.+)', header, re.MULTILINE)
        if period_start_match:
            result["period_start"] = self._parse_date(period_start_match.group(1).strip())

        period_end_match = re.search(r'^ДатаКонца\s*=\s*(.+)', header, re.MULTILINE)
        if period_end_match:
            result["period_end"] = self._parse_date(period_end_match.group(1).strip())

        # ============================================================
        # STEP 2: Parse СекцияРасчСчет blocks for opening/closing balances
        # ============================================================
        balance_sections = re.findall(
            r'СекцияРасчСчет(.*?)КонецРасчСчет', content, re.DOTALL
        )
        if balance_sections:
            # First section has opening balance
            first_section = balance_sections[0]
            ob_match = re.search(r'НачальныйОстаток\s*=\s*(.+)', first_section)
            if ob_match:
                result["opening_balance"] = self._parse_amount(ob_match.group(1).strip())

            # Last section has closing balance
            last_section = balance_sections[-1]
            cb_match = re.search(r'КонечныйОстаток\s*=\s*(.+)', last_section)
            if cb_match:
                result["closing_balance"] = self._parse_amount(cb_match.group(1).strip())

            # If no account from header, try from balance sections
            if not our_account:
                for section in balance_sections:
                    sec_acc = re.search(r'РасчСчет\s*=\s*(\d{20})', section)
                    if sec_acc:
                        our_account = sec_acc.group(1).strip()
                        result["account_number"] = our_account
                        break

        # ============================================================
        # STEP 3: Fallback — detect our account from most frequent account
        # ============================================================
        if not our_account:
            # Count all payer and recipient accounts
            all_payer_accounts = re.findall(r'ПлательщикСчет\s*=\s*(\d{20})', content)
            all_recip_accounts = re.findall(r'ПолучательСчет\s*=\s*(\d{20})', content)
            all_accounts = all_payer_accounts + all_recip_accounts
            if all_accounts:
                from collections import Counter
                account_counts = Counter(all_accounts)
                our_account = account_counts.most_common(1)[0][0]
                result["account_number"] = our_account
                result["warnings"].append(
                    f"РасчСчет не найден в заголовке, определён по частоте: {our_account}"
                )

        if not our_account:
            result["errors"].append(
                "Не удалось определить расчётный счёт ИП. "
                "Файл может быть повреждён или иметь нестандартный формат."
            )
            return result

        # ============================================================
        # STEP 4: Parse СекцияДокумент blocks into operations
        # ============================================================
        # Split by СекцияДокумент= to get each document block
        doc_blocks = re.split(r'(?=СекцияДокумент\s*=)', content)

        for doc_block in doc_blocks:
            if not doc_block.strip().startswith('СекцияДокумент'):
                continue

            operation = {}

            # Extract all fields from the document block
            for field_name, pattern in self.FIELD_PATTERNS_1C.items():
                if field_name == 'document_end':
                    continue

                match = re.search(pattern, doc_block)
                if match:
                    value = match.group(1).strip()

                    if field_name == 'document_type':
                        operation['document_type'] = value

                    elif field_name == 'date':
                        parsed_date = self._parse_date(value)
                        if parsed_date:
                            operation['operation_date'] = parsed_date
                        else:
                            result['errors'].append(f"Неверный формат даты: {value}")

                    elif field_name == 'amount':
                        parsed_amount = self._parse_amount(value)
                        if parsed_amount is not None:
                            operation['amount'] = parsed_amount
                        else:
                            result['errors'].append(f"Неверный формат суммы: {value}")

                    elif field_name == 'number':
                        operation['document_number'] = value

                    elif field_name == 'payer_account':
                        operation['payer_account'] = value

                    elif field_name == 'payer':
                        operation['payer'] = value

                    elif field_name == 'payer_inn':
                        operation['payer_inn'] = value

                    elif field_name == 'recipient_account':
                        operation['recipient_account'] = value

                    elif field_name == 'recipient':
                        operation['recipient'] = value

                    elif field_name == 'recipient_inn':
                        operation['recipient_inn'] = value

                    elif field_name == 'purpose':
                        operation['purpose'] = value

            # Also extract posting date from ДатаСписано or ДатаПоступило
            debit_date_match = re.search(r'ДатаСписано\s*=\s*(.+)', doc_block)
            credit_date_match = re.search(r'ДатаПоступило\s*=\s*(.+)', doc_block)
            if debit_date_match:
                pd = self._parse_date(debit_date_match.group(1).strip())
                if pd:
                    operation['posting_date'] = pd
            if credit_date_match:
                pd = self._parse_date(credit_date_match.group(1).strip())
                if pd:
                    operation['posting_date'] = pd

            # Skip documents without essential fields
            if 'operation_date' not in operation or 'amount' not in operation:
                continue

            # ========================================================
            # DIRECTION DETECTION: compare accounts with our account
            # ========================================================
            payer_acc = operation.get('payer_account', '').strip()
            recip_acc = operation.get('recipient_account', '').strip()

            if payer_acc == our_account:
                # WE are the payer → money goes OUT → expense
                operation['direction'] = 'expense'
                operation['counterparty'] = operation.get('recipient', '')
                operation['counterparty_inn'] = operation.get('recipient_inn', '')
                operation['counterparty_account'] = recip_acc
            elif recip_acc == our_account:
                # WE are the recipient → money comes IN → income
                operation['direction'] = 'income'
                operation['counterparty'] = operation.get('payer', '')
                operation['counterparty_inn'] = operation.get('payer_inn', '')
                operation['counterparty_account'] = payer_acc
            else:
                # Neither account matches — could be internal transfer or data issue
                # Default to income for tax safety (УСН 6%)
                operation['direction'] = 'income'
                operation['counterparty'] = operation.get('payer', '')
                operation['counterparty_inn'] = operation.get('payer_inn', '')
                operation['counterparty_account'] = payer_acc
                result['warnings'].append(
                    f"Док. №{operation.get('document_number', '?')}: "
                    f"ни ПлательщикСчет ({payer_acc}), ни ПолучательСчет ({recip_acc}) "
                    f"не совпадают с РасчСчет ({our_account}). Учтён как доход."
                )

            # ========================================================
            # EXTRACT OWNER INFO: ИНН и ФИО владельца счёта
            # Если мы плательщик — наши данные в payer/payer_inn
            # Если мы получатель — в recipient/recipient_inn
            # ========================================================
            if not result["owner_inn"]:
                if operation.get('direction') == 'expense':
                    inn = operation.get('payer_inn', '').strip()
                    name = operation.get('payer', '').strip()
                elif operation.get('direction') == 'income':
                    inn = operation.get('recipient_inn', '').strip()
                    name = operation.get('recipient', '').strip()
                else:
                    inn = name = ''
                if inn:
                    result["owner_inn"] = inn
                    result["owner_name"] = name

            # Clean up internal fields (not needed in output)
            for key in ['payer_account', 'payer', 'payer_inn',
                        'recipient_account', 'recipient', 'recipient_inn',
                        'document_type']:
                operation.pop(key, None)

            result['operations'].append(operation)

        return result

    def _parse_table_format(self, content: str, delimiter: str) -> Dict:
        """
        Parse table-style (pipe/tab/semicolon separated) bank statement.

        Args:
            content: File content
            delimiter: Column delimiter character

        Returns:
            Parsed data dictionary
        """
        result = {
            "account_number": None,
            "operations": [],
            "errors": [],
            "warnings": [],
        }

        lines = content.strip().split('\n')
        if len(lines) < 2:
            result['errors'].append("No data rows found in table")
            return result

        # Parse header
        header_line = lines[0]
        headers = [h.strip() for h in header_line.split(delimiter)]

        # Normalize header names to lowercase for matching
        headers_lower = [h.lower() for h in headers]

        # Map common Russian column names
        column_mapping = {
            'дата': 'date',
            'дата операции': 'date',
            'дата проводки': 'posting_date',
            'номер документа': 'document_number',
            'номер': 'document_number',
            'дебет': 'debit',
            'кредит': 'credit',
            'сумма': 'amount',
            'назначение платежа': 'purpose',
            'назначение': 'purpose',
            'контрагент': 'counterparty',
            'получатель': 'counterparty',
            'плательщик': 'payer',
            'инн контрагента': 'counterparty_inn',
            'инн': 'counterparty_inn',
            'счет': 'account',
            'счет контрагента': 'counterparty_account',
        }

        # Build column index map
        col_indices = {}
        for header_lower, full_header in zip(headers_lower, headers):
            mapped_name = column_mapping.get(header_lower, header_lower)
            col_indices[mapped_name] = headers_lower.index(header_lower)

        # Parse data rows
        for line_num, line in enumerate(lines[1:], start=2):
            if not line.strip():
                continue

            cells = [c.strip() for c in line.split(delimiter)]

            operation = {}

            # Extract date
            if 'date' in col_indices:
                date_str = cells[col_indices['date']]
                parsed_date = self._parse_date(date_str)
                if parsed_date:
                    operation['operation_date'] = parsed_date
                else:
                    result['errors'].append(
                        f"Line {line_num}: Invalid date format '{date_str}'"
                    )
                    continue

            # Extract posting date if available
            if 'posting_date' in col_indices:
                posting_str = cells[col_indices['posting_date']]
                parsed_posting = self._parse_date(posting_str)
                if parsed_posting:
                    operation['posting_date'] = parsed_posting

            # Extract document number
            if 'document_number' in col_indices:
                operation['document_number'] = cells[col_indices['document_number']]

            # Extract amount and determine direction
            amount = None
            direction = None

            if 'debit' in col_indices and 'credit' in col_indices:
                debit_str = cells[col_indices['debit']]
                credit_str = cells[col_indices['credit']]

                debit = self._parse_amount(debit_str) if debit_str else None
                credit = self._parse_amount(credit_str) if credit_str else None

                if credit and credit > 0:
                    amount = credit
                    direction = 'expense'
                elif debit and debit > 0:
                    amount = debit
                    direction = 'income'
            elif 'amount' in col_indices:
                amount_str = cells[col_indices['amount']]
                amount = self._parse_amount(amount_str)

            if amount is None:
                result['warnings'].append(
                    f"Line {line_num}: Could not parse amount"
                )
                continue

            operation['amount'] = amount
            if direction:
                operation['direction'] = direction

            # Extract purpose
            if 'purpose' in col_indices:
                operation['purpose'] = cells[col_indices['purpose']]

            # Extract counterparty
            if 'counterparty' in col_indices:
                operation['counterparty'] = cells[col_indices['counterparty']]
            elif 'payer' in col_indices:
                operation['counterparty'] = cells[col_indices['payer']]

            # Extract counterparty INN
            if 'counterparty_inn' in col_indices:
                operation['counterparty_inn'] = cells[col_indices['counterparty_inn']]

            # Extract counterparty account
            if 'counterparty_account' in col_indices:
                operation['counterparty_account'] = cells[
                    col_indices['counterparty_account']
                ]

            result['operations'].append(operation)

        return result

    def _parse_structured_text(self, content: str) -> Dict:
        """
        Parse simple structured text format with headers.

        Args:
            content: File content

        Returns:
            Parsed data dictionary
        """
        result = {
            "account_number": None,
            "operations": [],
            "errors": [],
            "warnings": [],
        }

        # Try to extract account number from header
        account_match = re.search(
            r'[Сс]чет[а-я]?\s*:?\s*(\d{20})', content
        )
        if account_match:
            result['account_number'] = account_match.group(1)

        # Try to extract period dates
        period_match = re.search(
            r'[Пп]ериод\s*:?\s*([0-9./-]+)\s*[-–]\s*([0-9./-]+)',
            content
        )
        if period_match:
            start_date = self._parse_date(period_match.group(1))
            end_date = self._parse_date(period_match.group(2))
            if start_date:
                result['period_start'] = start_date
            if end_date:
                result['period_end'] = end_date

        # Try to extract opening/closing balances
        opening_match = re.search(
            r'[Оо]статок на начало[а-я]*\s*:?\s*([\d\s,.-]+)',
            content
        )
        if opening_match:
            amount = self._parse_amount(opening_match.group(1))
            if amount:
                result['opening_balance'] = amount

        closing_match = re.search(
            r'[Оо]статок на конец[а-я]*\s*:?\s*([\d\s,.-]+)',
            content
        )
        if closing_match:
            amount = self._parse_amount(closing_match.group(1))
            if amount:
                result['closing_balance'] = amount

        # Try to find operation blocks (crude pattern matching)
        # Look for lines with dates and amounts
        operation_pattern = r'(\d{2}[./\-]\d{2}[./\-]\d{4})[^\n]*?([\d\s,.-]+)[^\n]*?([а-яА-Я][а-яА-Я ]+)?'

        for match in re.finditer(operation_pattern, content):
            operation = {}

            date_str = match.group(1)
            parsed_date = self._parse_date(date_str)
            if parsed_date:
                operation['operation_date'] = parsed_date
            else:
                continue

            amount_str = match.group(2)
            amount = self._parse_amount(amount_str)
            if amount:
                operation['amount'] = amount
            else:
                continue

            if match.group(3):
                operation['purpose'] = match.group(3).strip()

            result['operations'].append(operation)

        return result

    def _parse_date(self, date_str: str) -> Optional[date]:
        """
        Parse Russian date formats.

        Args:
            date_str: Date string to parse

        Returns:
            Parsed date object or None
        """
        if not date_str:
            return None

        date_str = date_str.strip()

        for pattern, fmt in self.DATE_PATTERNS:
            if re.match(pattern, date_str):
                try:
                    return datetime.strptime(date_str, fmt).date()
                except ValueError:
                    continue

        return None

    def _parse_amount(self, amount_str: str) -> Optional[Decimal]:
        """
        Parse Russian amount format.

        Handles:
        - Comma as decimal separator: 1234,56
        - Space as thousands separator: 1 234 567,89
        - Dot notation: 1.234.567,89

        Args:
            amount_str: Amount string to parse

        Returns:
            Parsed Decimal or None
        """
        if not amount_str:
            return None

        amount_str = amount_str.strip()

        # Remove empty strings and return None
        if not amount_str:
            return None

        # Handle empty cells (debit/credit format)
        if amount_str in ['', '-', '—']:
            return None

        # Remove currency symbols and other non-numeric characters (keep digits, separators, minus)
        amount_str = re.sub(r'[^\d,.\s—–-]', '', amount_str)

        # Determine decimal and thousand separators
        # Russian format typically uses comma for decimal and space for thousands
        if ',' in amount_str:
            # Comma is decimal separator
            amount_str = amount_str.replace(' ', '').replace('.', '')
            amount_str = amount_str.replace(',', '.')
        elif '.' in amount_str:
            # Could be thousand separator (1.234.567,89) or decimal (1.23)
            # Count occurrences
            dot_count = amount_str.count('.')
            comma_count = amount_str.count(',')

            if comma_count > 0:
                # Comma is decimal, dots are thousands
                amount_str = amount_str.replace('.', '').replace(',', '.')
            else:
                # Single dot is likely decimal, multiple dots are thousands
                if dot_count == 1:
                    last_dot_pos = amount_str.rfind('.')
                    after_dot = amount_str[last_dot_pos + 1:]
                    if len(after_dot) == 2:
                        # Likely decimal
                        pass
                    else:
                        # Likely thousands separator, remove it
                        amount_str = amount_str.replace('.', '')
                else:
                    # Multiple dots, remove them (thousands)
                    amount_str = amount_str.replace('.', '')

        # Remove spaces (thousands separators)
        amount_str = amount_str.replace(' ', '')

        # Remove heading/trailing minus or em-dash (for expenses)
        is_negative = amount_str.startswith('-') or amount_str.startswith('—')
        amount_str = amount_str.lstrip('-—')

        try:
            amount = Decimal(amount_str)
            if is_negative:
                amount = -amount
            return amount
        except (InvalidOperation, ValueError):
            return None

    def _determine_direction(
        self,
        debit: Optional[Decimal],
        credit: Optional[Decimal],
        account_number: Optional[str],
        operation_data: Dict,
    ) -> str:
        """
        Determine if operation is income or expense for the ИП.

        Args:
            debit: Debit amount
            credit: Credit amount
            account_number: Our account number
            operation_data: Operation data dictionary

        Returns:
            "income" or "expense"
        """
        if credit and credit > 0:
            return 'expense'
        elif debit and debit > 0:
            return 'income'

        return 'expense'  # Default


def validate_inn(inn_str: str) -> bool:
    """
    Validate Russian INN (ИНН).

    Args:
        inn_str: INN string to validate

    Returns:
        True if valid, False otherwise
    """
    inn_str = str(inn_str).strip()

    # INN should be 10 or 12 digits
    if not inn_str.isdigit():
        return False

    if len(inn_str) not in [10, 12]:
        return False

    # Simplified validation (checksum validation is complex)
    return True


def validate_account(account_str: str) -> bool:
    """
    Validate Russian bank account number.

    Args:
        account_str: Account number string

    Returns:
        True if valid, False otherwise
    """
    account_str = str(account_str).strip()

    # Russian accounts are 20 digits
    if not account_str.isdigit():
        return False

    return len(account_str) == 20
