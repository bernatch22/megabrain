"""Sparse lexical channel over entity-IDs (LocAgent T4) — pure python, no deps.

Each file's document = its path + all symbol qualified names + signatures,
tokenized identifier-aware (split camelCase/snake_case). Catches issues that
mention a symbol descriptively when the dense embedding misses it.

Scoring iterates postings (only the docs containing each query term), not the
whole corpus — O(matching docs) per term instead of O(N).
"""

from __future__ import annotations

import math
import re
from collections import Counter


def tokenize(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", text):
        lw = w.lower()
        out.append(lw)
        for p in re.split(r"_+", w):
            for s in re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", p):
                if len(s) > 1:
                    out.append(s.lower())
    return out


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.N = len(docs)
        self.dl = [len(d) for d in docs]
        self.avgdl = (sum(self.dl) / self.N) if self.N else 0.0
        self.postings: dict[str, list[tuple[int, int]]] = {}  # term -> [(doc, tf)]
        for i, d in enumerate(docs):
            for t, f in Counter(d).items():
                self.postings.setdefault(t, []).append((i, f))
        # df(t) == len(postings[t]); same Okapi idf as before
        self.idf = {t: math.log(1 + (self.N - len(p) + 0.5) / (len(p) + 0.5))
                    for t, p in self.postings.items()}

    def scores(self, query: str):
        import numpy as np
        s = np.zeros(self.N)
        if not self.avgdl:
            return s
        for t in set(tokenize(query)):
            plist = self.postings.get(t)
            if not plist:
                continue
            idf = self.idf[t]
            for i, f in plist:
                s[i] += idf * f * (self.k1 + 1) / (
                    f + self.k1 * (1 - self.b + self.b * self.dl[i] / self.avgdl))
        return s
