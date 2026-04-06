"""
Shared exam logic (matches server.js mulberry32 + shuffle + grading).
Used by streamlit_app.py so question order matches the Node/Express app.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _u32(x: int) -> int:
    return int(x) & 0xFFFFFFFF


def _to_int32(x: int) -> int:
    x = _u32(x)
    if x >= 0x80000000:
        x -= 0x100000000
    return x


def _imul(a: int, b: int) -> int:
    return (_to_int32(a) * _to_int32(b)) & 0xFFFFFFFF


class Mulberry32:
    """Same PRNG as server.js mulberry32 (unsigned shifts)."""

    def __init__(self, seed: int):
        self.a = _u32(seed)

    def __call__(self) -> float:
        self.a = _u32(self.a + 0x6D2B79F5)
        t = self.a
        t = _imul(_u32(t) ^ (_u32(t) >> 15), _u32(t) | 1)
        t = _u32(t ^ _u32(t + _imul(_u32(t) ^ (_u32(t) >> 7), _u32(t) | 61)))
        return _u32(_u32(t) ^ (_u32(t) >> 14)) / 4294967296.0


def shuffle_with_seed(items: list[Any], seed: int) -> list[Any]:
    arr = items[:]
    rnd = Mulberry32(_u32(seed))
    for i in range(len(arr) - 1, 0, -1):
        j = int(rnd() * (i + 1))
        arr[i], arr[j] = arr[j], arr[i]
    return arr


def load_bank(base_dir: Path | None = None) -> dict[str, Any]:
    root = base_dir or Path(__file__).resolve().parent
    path = root / "questions.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def ordered_ids_for_variant(bank: dict[str, Any], variant: int) -> list[str]:
    ids = [q["id"] for q in bank["questions"]]
    pick = int(bank.get("questionsPerExam") or 20)
    if len(ids) < pick:
        raise ValueError("question pool smaller than questionsPerExam")
    seeds = bank["variantSeeds"]
    if variant < 1 or variant > len(seeds):
        raise ValueError("invalid variant")
    shuffled = shuffle_with_seed(ids, int(seeds[variant - 1]))
    return shuffled[:pick]


def normalize_key(name: str, section: str) -> str:
    return f"{name.strip().lower()}|{section.strip().lower()}"


def grade(ordered_ids: list[str], answers: dict[str, Any], bank: dict[str, Any]) -> dict[str, Any]:
    correct = bank["correctIndexById"]
    total = 0
    wrong: list[dict[str, Any]] = []
    for i, qid in enumerate(ordered_ids):
        sel = answers.get(qid)
        expected = correct[qid]
        if sel is None or sel == "":
            wrong.append({"questionId": qid, "orderIndex": i + 1, "reason": "empty"})
        elif int(sel) != int(expected):
            wrong.append({"questionId": qid, "orderIndex": i + 1, "reason": "wrong"})
        else:
            total += 1
    return {"total": total, "max": len(ordered_ids), "wrong": wrong}
