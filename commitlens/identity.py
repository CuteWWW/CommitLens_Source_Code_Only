"""Canonicalize author identities before calculating contribution-based metrics.

Mailmap is applied by Git at collection time; offline logs and extra aliases
are reconciled here. Same display names alone are never treated as identity.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .model import Commit


@dataclass
class IdentityAudit:
    identities: int
    alias_merges: int
    duplicate_names: list[str]
    renamed_emails: int


def normalize_identities(commits: list[Commit], aliases: dict[str, str]) -> IdentityAudit:
    """Mutate in-memory author labels, never the underlying Git repository.

    Prefer email over name; explicit alias mappings link different emails.
    Multiple people with the same name get disambiguated by email.
    """
    mapping = {a.casefold().strip(): b.casefold().strip() for a, b in aliases.items()}

    def canonical(email: str) -> str:
        key = email.strip().casefold()
        visited = set()
        while key in mapping:
            if key in visited:
                raise ValueError(f"Cycle in identity.aliases at {key}")
            visited.add(key)
            key = mapping[key]
        return key

    names: dict[str, str] = {}
    names_by_email: defaultdict[str, set[str]] = defaultdict(set)
    ids: list[str] = []
    merged = 0
    for c in commits:
        old = c.email.strip().casefold()
        key = canonical(old) if old else f"missing-email:{c.author.casefold()}"
        if key != old:
            merged += 1
        ids.append(key)
        names[key] = c.author.strip() or "Unknown"
        names_by_email[key].add(c.author.strip())

    conflicts: defaultdict[str, set[str]] = defaultdict(set)
    for key, display in names.items():
        conflicts[display.casefold()].add(key)
    duplicates = sorted(names[key] for key in names if len(conflicts[names[key].casefold()]) > 1)

    for c, key in zip(commits, ids):
        display = names[key]
        if len(conflicts[display.casefold()]) > 1:
            display = f"{display} <{key}>"
        c.author = display
        c.email = key

    return IdentityAudit(
        identities=len(names),
        alias_merges=merged,
        duplicate_names=duplicates,
        renamed_emails=sum(len(values) > 1 for values in names_by_email.values()),
    )


def pseudonymize_authors(commits: list[Commit]) -> None:
    """Use stable-in-this-dataset labels; not irreversible anonymization."""
    labels = {email: f"Dev-{i:02d}" for i, email in
              enumerate(sorted({c.email for c in commits}), 1)}
    for c in commits:
        c.author = labels[c.email]
        c.email = "redacted"
