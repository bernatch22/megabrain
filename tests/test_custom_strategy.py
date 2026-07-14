"""Custom chunking strategies — `index_repo(strategies=[...])` plugs any
content type in without forking; custom strategies are checked before the
built-ins (so they can also override one). Runnable example:
examples/02_custom_chunker.py."""

import re

from megabrain import Chunk, ChunkStrategy, FileResult, Symbol, validate_partition
from megabrain.indexing.indexer import index_repo
from megabrain.retrieval.bundle import search
from megabrain.store import Store

SQL = (
    "-- customers master table\n"
    "CREATE TABLE customers (\n"
    "    id INTEGER PRIMARY KEY,\n"
    "    name TEXT NOT NULL\n"
    ");\n"
    "\n"
    "-- invoices belong to customers\n"
    "CREATE TABLE invoices (\n"
    "    id INTEGER PRIMARY KEY,\n"
    "    customer_id INTEGER REFERENCES customers(id),\n"
    "    amount REAL\n"
    ");\n"
)


class SqlStrategy:
    """Toy .sql strategy: one chunk per `;`-terminated statement."""

    exts = (".sql",)

    def chunk_file(self, relpath: str, source: str) -> FileResult:
        lines = source.splitlines(keepends=True)
        total = len(lines)
        chunks, symbols = [], []
        start = 1
        for i, ln in enumerate(lines, 1):
            if ln.rstrip().endswith(";") or i == total:
                text = "".join(lines[start - 1:i])
                m = re.search(r"CREATE\s+(\w+)\s+(\w+)", text, re.I)
                kind = m.group(1).lower() if m else "statement"
                name = m.group(2) if m else None
                chunks.append(Chunk(relpath, kind, name, start, i, text,
                                    f"{relpath} > {name or kind}").finalize())
                if name:
                    symbols.append(Symbol(relpath, name, kind, start, i,
                                          text.strip().splitlines()[0]))
                start = i + 1
        skeleton = "\n".join(s.signature for s in symbols)
        return FileResult(relpath, chunks, symbols, skeleton, True, total)

    def build_edge_ctx(self, sources, repo_name):
        return None

    def extract_edges(self, relpath, source, ctx):
        return None


def test_protocol_conformance():
    assert isinstance(SqlStrategy(), ChunkStrategy)


def test_partition_and_symbols():
    r = SqlStrategy().chunk_file("schema.sql", SQL)
    assert validate_partition(r) == []
    assert [s.name for s in r.symbols] == ["customers", "invoices"]
    assert {c.kind for c in r.chunks} == {"table"}


def test_index_and_search_with_custom_strategy(tmp_path, fake_embedder):
    (tmp_path / "schema.sql").write_text(SQL)
    (tmp_path / "app.py").write_text("def unrelated():\n    return 1\n")
    index_repo(tmp_path, quiet=True, strategies=[SqlStrategy()])
    with Store(tmp_path) as st:
        assert "schema.sql" in st.all_paths()
        kinds = {r[0] for r in st.db.execute(
            "SELECT kind FROM chunks WHERE file='schema.sql'")}
    assert kinds == {"table"}
    res = search(tmp_path, "customers invoices table")
    bundle = [t["file"] for t in res["tier1"]] + [t["file"] for t in res["tier2"]]
    assert "schema.sql" in bundle


def test_custom_strategy_overrides_builtin(tmp_path, fake_embedder):
    class WholeFilePy:
        """Claims .py AHEAD of the built-in PythonStrategy."""
        exts = (".py",)

        def chunk_file(self, relpath, source):
            lines = source.splitlines(keepends=True)
            c = Chunk(relpath, "file", None, 1, len(lines), source, relpath).finalize()
            return FileResult(relpath, [c], [], "", True, len(lines))

        def build_edge_ctx(self, sources, repo_name):
            return None

        def extract_edges(self, relpath, source, ctx):
            return None

    (tmp_path / "app.py").write_text(
        "def a():\n    return 1\n\n\ndef b():\n    return 2\n")
    index_repo(tmp_path, quiet=True, strategies=[WholeFilePy()])
    with Store(tmp_path) as st:
        kinds = [r[0] for r in st.db.execute(
            "SELECT kind FROM chunks WHERE file='app.py'")]
    assert kinds == ["file"]   # custom won; built-in would emit function chunks
