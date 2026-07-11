# AUTO-FORGED — verbatim output of `megabrain forge --specialize` on the
# sinatra repo (Ruby), qwen/qwen3-coder. Reproduced here as an example of what
# specialization generates; NOT hand-edited (the leftover scaffold comments
# below and the simplified _extract_symbol end_line are the model's own).
#
# It installed only after WINNING the measured A/B gate (forge_eval.ab_gate):
# pooled span-IoU 0.049 -> 0.171 (3.5x) across the 15 .rb files it changed,
# every one improved, worst-file +0.064. Diagnosis: Ruby classes with many
# short methods get merged to the 4000-char budget into one blob, so a query
# for one method retrieves the whole class. This router cuts per-method (~500
# nws, snapping to `def`) and delegates every other file to the built-in.
#
# See examples/forged/README.md for the full run + a qualitative query.

from megabrain import Chunk, FileResult, Symbol
from megabrain.chunkers import DEFAULT_BUDGET, nws
from megabrain.indexing.strategies import builtin_strategy_for
import re


class RbSpecialStrategy:
    exts = (".rb",)

    def __init__(self, repo: str = ""):
        self.repo = repo
        self._fallback = builtin_strategy_for(".rb", repo)  # the built-in chunker

    def chunk_file(self, relpath: str, source: str) -> FileResult:
        if not self._is_special(source):
            return self._fallback.chunk_file(relpath, source)   # delegate — unchanged
        lines = source.splitlines(keepends=True)
        total = len(lines)
        if total == 0:
            return FileResult(relpath, [], [], "", True, 0)
        # PARTITION BY CONSTRUCTION — do NOT compute end_lines by hand.
        # 1. compute `cuts`: a sorted list of chunk START lines, always [1, ...]
        cuts = self._cut_lines(lines)            # e.g. [1, 24, 61, 118]
        # 2. derive the chunks mechanically (this can never gap or overlap):
        bounds = list(zip(cuts, [c - 1 for c in cuts[1:]] + [total]))
        # 3. one Chunk per (s, e) in bounds, text = "".join(lines[s-1:e])

        chunks = []
        symbols = []
        breadcrumb_base = f"{self.repo} > {relpath}"

        for i, (s, e) in enumerate(bounds):
            chunk_lines = lines[s-1:e]
            chunk_text = "".join(chunk_lines)
            name = self._name_chunk(chunk_lines, s)
            breadcrumb = f"{breadcrumb_base} > {name}" if name else breadcrumb_base

            chunk = Chunk(
                file=relpath,
                kind="block",
                name=name,
                start_line=s,
                end_line=e,
                text=chunk_text,
                breadcrumb=breadcrumb
            ).finalize()
            chunks.append(chunk)

            # Extract symbols if applicable
            sym = self._extract_symbol(chunk_lines, s, relpath, breadcrumb_base)
            if sym:
                symbols.append(sym)

        skeleton = self._build_skeleton(lines)
        return FileResult(relpath, chunks, symbols, skeleton, True, total)

    def _is_special(self, source: str) -> bool:
        # Heuristic: files with many short method definitions (like data tables)
        lines = source.splitlines()
        method_like_lines = [line for line in lines if re.match(r'^\s*def\s+\w+', line)]
        # Threshold: more than 10 methods in a file under 400 lines
        return len(method_like_lines) > 10 and len(lines) < 400

    def _cut_lines(self, lines: list[str]) -> list[int]:
        cuts = [1]
        current_nws = 0
        start_line = 1

        for i, line in enumerate(lines):
            line_nws = nws(line)
            # If adding this line exceeds budget or it's a new method, cut here
            if current_nws + line_nws > 500 and i > start_line:
                # Look for a good cut point: right before a 'def' line
                cut_point = i + 1  # 1-based line number
                for j in range(i, max(i - 5, 0), -1):
                    if re.match(r'^\s*def\s+\w+', lines[j]):
                        cut_point = j + 1
                        break
                if cut_point > cuts[-1]:
                    cuts.append(cut_point)
                    start_line = cut_point
                    current_nws = sum(nws(l) for l in lines[cut_point-1:i+1])
                else:
                    current_nws += line_nws
            else:
                current_nws += line_nws

        return cuts

    def _name_chunk(self, chunk_lines: list[str], start_line: int) -> str:
        # Name the chunk based on the first method definition
        for line in chunk_lines:
            match = re.match(r'^\s*def\s+(\w+)', line)
            if match:
                return match.group(1)
        return f"lines_{start_line}_to_{start_line + len(chunk_lines) - 1}"

    def _extract_symbol(self, chunk_lines: list[str], start_line: int, relpath: str, breadcrumb_base: str) -> Symbol | None:
        for i, line in enumerate(chunk_lines):
            match = re.match(r'^\s*def\s+(\w+)', line)
            if match:
                name = match.group(1)
                line_no = start_line + i
                end_line_no = line_no  # Simplified; in practice, find the end
                signature = line.strip()
                return Symbol(
                    file=relpath,
                    name=name,
                    kind="method",
                    line=line_no,
                    end_line=end_line_no,
                    signature=signature
                )
        return None

    def _build_skeleton(self, lines: list[str]) -> str:
        # Skeleton is all the non-method lines
        skeleton_lines = [line for line in lines if not re.match(r'^\s*def\s+\w+', line)]
        return "".join(skeleton_lines)

    def build_edge_ctx(self, sources, repo_name):
        return None

    def extract_edges(self, relpath, source, ctx):
        return None
