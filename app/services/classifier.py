"""
Operation Classifier for Russian tax declaration system (ИП на УСН 6%).
Classifies bank operations as income or not_income based on patterns and custom rules.
"""
import json
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from decimal import Decimal


class OperationClassifier:
    """
    Classifies bank operations as income or not_income for tax purposes.

    Classification logic priority (highest to lowest):
    1. Hard rules from dictionaries (exclude_markers, income_markers)
    2. Custom rules from database (project-specific or global)
    3. Fallback to "disputed" classification with low confidence
    """

    def __init__(self, project_id: int, db_session):
        """
        Initialize the classifier with project-specific context.

        Args:
            project_id: ID of the project (ИП)
            db_session: SQLAlchemy session for accessing ClassificationRule models
        """
        self.project_id = project_id
        self.db_session = db_session

        # Load dictionaries from JSON files
        self.income_markers = self._load_dictionary("income_markers.json")
        self.exclude_markers = self._load_dictionary("exclude_markers.json")

        # Load custom rules from database
        self.custom_rules = self._load_custom_rules()

    def _load_dictionary(self, filename: str) -> List[str]:
        """
        Load keyword patterns from JSON dictionary file.

        Args:
            filename: Name of the dictionary file (e.g., "income_markers.json")

        Returns:
            List of keyword patterns, or empty list if file not found
        """
        try:
            # Navigate from current module location to dictionaries folder
            # Works cross-platform using pathlib
            current_dir = Path(__file__).parent.parent.parent
            dict_path = current_dir / "dictionaries" / filename

            if dict_path.exists():
                with open(dict_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            # Log error but don't fail - use empty list as fallback
            print(f"Warning: Could not load dictionary {filename}: {e}")

        return []

    def _load_custom_rules(self) -> Dict[str, List[Dict]]:
        """
        Load custom classification rules from database.
        Returns rules organized by type for quick access.

        Returns:
            Dictionary with rule type as key and list of rules as value
            Structure: {
                "keyword_income": [...],
                "keyword_exclude": [...],
                "counterparty_income": [...],
                "counterparty_exclude": [...]
            }
        """
        from app.models import ClassificationRule

        rules_by_type = {
            "keyword_income": [],
            "keyword_exclude": [],
            "counterparty_income": [],
            "counterparty_exclude": []
        }

        try:
            # Get active rules for this project or global rules
            db_rules = self.db_session.query(ClassificationRule).filter(
                ClassificationRule.is_active == True,
                (ClassificationRule.project_id == self.project_id) |
                (ClassificationRule.project_id == None)
            ).all()

            for rule in db_rules:
                if rule.rule_type in rules_by_type:
                    rules_by_type[rule.rule_type].append({
                        "pattern": rule.pattern,
                        "description": rule.description,
                        "is_global": rule.project_id is None
                    })
        except Exception as e:
            # Log error but don't fail - use empty rules
            print(f"Warning: Could not load custom rules: {e}")

        return rules_by_type

    def classify(self, operation: Dict) -> Dict:
        """
        Classify a single bank operation as income or not_income.

        Args:
            operation: Dictionary with keys:
                - amount: Decimal, operation amount
                - direction: str, "income" or "expense"
                - purpose: str, payment purpose / description
                - counterparty: str, counterparty name (optional)
                - counterparty_inn: str, counterparty INN (optional)

        Returns:
            Dictionary with classification result:
            {
                "classification": "income" | "not_income" | "disputed",
                "included_in_tax_base": bool,
                "rule_matched": str | None,
                "confidence": float (0.0-1.0),
                "exclusion_reason": str | None
            }
        """
        # STEP 0: Only classify income operations
        # Expenses are never included in tax base
        if operation.get("direction") != "income":
            return {
                "classification": "not_income",
                "included_in_tax_base": False,
                "rule_matched": "Expense operation",
                "confidence": 1.0,
                "exclusion_reason": "Operation direction is expense"
            }

        # Normalize text for matching
        purpose = self._normalize_text(operation.get("purpose", ""))
        counterparty = self._normalize_text(operation.get("counterparty", ""))

        # STEP 1: Hard rules from dictionaries
        # Check exclude_markers first (highest priority - if matched, exclude)
        is_excluded, matched_marker = self._check_markers(purpose, self.exclude_markers)
        if is_excluded:
            return {
                "classification": "not_income",
                "included_in_tax_base": False,
                "rule_matched": f"Exclude marker: {matched_marker}",
                "confidence": 0.95,
                "exclusion_reason": f"Matched exclude pattern: {matched_marker}"
            }

        # Check income_markers (if matched, classify as income)
        is_income, matched_marker = self._check_markers(purpose, self.income_markers)
        if is_income:
            return {
                "classification": "income",
                "included_in_tax_base": True,
                "rule_matched": f"Income marker: {matched_marker}",
                "confidence": 0.90,
                "exclusion_reason": None
            }

        # STEP 2: Custom counterparty rules from database
        # Check if counterparty is in custom exclude rules
        for rule in self.custom_rules.get("counterparty_exclude", []):
            if self._pattern_matches(counterparty, rule["pattern"]):
                return {
                    "classification": "not_income",
                    "included_in_tax_base": False,
                    "rule_matched": rule["description"] or f"Custom exclude: {rule['pattern']}",
                    "confidence": 0.85,
                    "exclusion_reason": rule["description"] or f"Matched exclude counterparty: {rule['pattern']}"
                }

        # Check if counterparty is in custom income rules
        for rule in self.custom_rules.get("counterparty_income", []):
            if self._pattern_matches(counterparty, rule["pattern"]):
                return {
                    "classification": "income",
                    "included_in_tax_base": True,
                    "rule_matched": rule["description"] or f"Custom income: {rule['pattern']}",
                    "confidence": 0.85,
                    "exclusion_reason": None
                }

        # STEP 3: Custom keyword rules from database
        # Check if purpose matches custom exclude keywords
        for rule in self.custom_rules.get("keyword_exclude", []):
            is_match, _ = self._check_markers(purpose, [rule["pattern"]])
            if is_match:
                return {
                    "classification": "not_income",
                    "included_in_tax_base": False,
                    "rule_matched": rule["description"] or f"Custom exclude keyword: {rule['pattern']}",
                    "confidence": 0.80,
                    "exclusion_reason": rule["description"] or f"Matched exclude keyword: {rule['pattern']}"
                }

        # Check if purpose matches custom income keywords
        for rule in self.custom_rules.get("keyword_income", []):
            is_match, _ = self._check_markers(purpose, [rule["pattern"]])
            if is_match:
                return {
                    "classification": "income",
                    "included_in_tax_base": True,
                    "rule_matched": rule["description"] or f"Custom income keyword: {rule['pattern']}",
                    "confidence": 0.80,
                    "exclusion_reason": None
                }

        # STEP 4: No rule matched — для УСН 6% все поступления = доход по умолчанию
        # Деньги пришли на счёт ИП → это доход, если не доказано обратное
        return {
            "classification": "income",
            "included_in_tax_base": True,
            "rule_matched": "По умолчанию: все поступления — доход (УСН 6%)",
            "confidence": 0.60,
            "exclusion_reason": None
        }

    def classify_batch(self, operations: List[Dict]) -> List[Dict]:
        """
        Classify a batch of operations.

        Args:
            operations: List of operation dictionaries

        Returns:
            List of classification results in same order as input
        """
        return [self.classify(op) for op in operations]

    def _normalize_text(self, text: str) -> str:
        """
        Normalize text for matching: lowercase, strip whitespace, remove extra spaces.

        Args:
            text: Text to normalize

        Returns:
            Normalized text
        """
        if not text:
            return ""

        # Lowercase
        text = text.lower()
        # Strip leading/trailing whitespace
        text = text.strip()
        # Normalize multiple spaces to single space
        text = re.sub(r'\s+', ' ', text)

        return text

    def _check_markers(self, text: str, markers: List[str]) -> Tuple[bool, Optional[str]]:
        """
        Check if any marker from the list matches the text.

        Args:
            text: Text to check (should be normalized)
            markers: List of keyword patterns to match against

        Returns:
            Tuple of (matched: bool, matched_marker: str or None)
        """
        for marker in markers:
            if self._pattern_matches(text, marker):
                return True, marker

        return False, None

    def _pattern_matches(self, text: str, pattern: str) -> bool:
        """
        Check if pattern matches text.
        Uses substring matching for keywords.
        Can be extended for regex patterns if needed.

        Args:
            text: Text to search in (should be normalized)
            pattern: Pattern to match (keyword)

        Returns:
            True if pattern is found in text
        """
        # Normalize both for comparison
        text_normalized = self._normalize_text(text)
        pattern_normalized = self._normalize_text(pattern)

        if not pattern_normalized:
            return False

        # Simple substring matching for keywords
        return pattern_normalized in text_normalized
