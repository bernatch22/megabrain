"""Terminal heatmap: every chunk of ONE file, scored against a query — the
signal-vs-noise view (what retrieval reads vs ignores inside a file).

    python examples/03_chunk_map.py <repo> <file> "<query>"

Uses the same `chunks_for_file` scoring that powers `megabrain chunks`, the
MCP `megabrain_chunks` tool, and `GET /chunks` on serve-api. `selected` marks
chunks the FULL cross-file retrieval would actually put in the bundle.
Needs an embedding key (query embedding only — chunks are already indexed).
"""

import sys

from megabrain.query import chunks_for_file_root


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    root, relpath, query = sys.argv[1:4]
    d = chunks_for_file_root(root, relpath, query)

    lo, hi = d["score_min"], d["score_max"]
    span = (hi - lo) or 1.0
    print(f'\n{d["file"]}  ·  "{query}"  ·  role in bundle: {d["role"]}\n')
    for c in d["chunks"]:
        t = (c["score"] - lo) / span
        bar = "█" * max(1, round(t * 28))
        color = "\x1b[38;5;46m" if c["selected"] else \
                ("\x1b[38;5;214m" if t > 0.6 else "\x1b[38;5;240m")
        mark = "▶" if c["selected"] else " "
        label = c["name"] or c["kind"]
        print(f'{color}{mark} L{c["start_line"]:>5}-{c["end_line"]:<5} '
              f'{c["score"]:.3f} {bar:<28}\x1b[0m {label}')
    print(f'\n{d["selected_count"]}/{len(d["chunks"])} chunks selected '
          f'(green = in the bundle; grey = noise retrieval skips)')


if __name__ == "__main__":
    main()
