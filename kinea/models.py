"""Small dependency-free data models shared by collector modules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    reference_date: str
    value: float


@dataclass
class IngestCounts:
    seen: int = 0
    inserted: int = 0
    revised: int = 0
    updated_same_day: int = 0
    unchanged: int = 0

    def add(self, other: "IngestCounts") -> None:
        self.seen += other.seen
        self.inserted += other.inserted
        self.revised += other.revised
        self.updated_same_day += other.updated_same_day
        self.unchanged += other.unchanged
