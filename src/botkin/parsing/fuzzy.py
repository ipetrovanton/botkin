"""Fuzzy name matching using rapidfuzz — replaces custom Levenshtein implementation."""

from __future__ import annotations

from rapidfuzz.distance import DamerauLevenshtein
from rapidfuzz import process


class FuzzyNameMatcher:
    """Match OCR-distorted names against a canonical list using Damerau-Levenshtein distance.

    Args:
        choices: List of canonical names to match against.
        synonyms: Optional dict mapping lowercase synonyms/abbreviations to canonical names.
    """

    def __init__(self, choices: list[str], synonyms: dict[str, str] | None = None) -> None:
        self._choices = choices
        self._synonyms = synonyms or {}
        self._lower_choices = [c.lower() for c in choices]

    def match(self, query: str, threshold_ratio: float = 0.25) -> str | None:
        """Match a query string to the closest canonical name.

        Args:
            query: The OCR-distorted name to match.
            threshold_ratio: Maximum edit distance as a fraction of the canonical name length.

        Returns:
            The canonical name, or None if no match within threshold.
        """
        low_query = query.lower().strip()

        # Exact synonym match (case-insensitive)
        if low_query in self._synonyms:
            return self._synonyms[low_query]

        # Exact case-insensitive match
        for i, low_can in enumerate(self._lower_choices):
            if low_query == low_can:
                return self._choices[i]

        # Fuzzy match using rapidfuzz
        best = process.extractOne(
            low_query,
            self._lower_choices,
            scorer=DamerauLevenshtein.distance,
            score_cutoff=max(3, len(low_query) // 4),
        )
        if best is not None:
            idx = best[2]
            return self._choices[idx]

        return None
