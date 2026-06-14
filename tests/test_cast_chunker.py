"""Tests for the cAST chunker (megabrain.chunker). Run: python3 -m pytest tests/test_cast_chunker.py -q"""

from megabrain.chunker import CastChunker, validate_partition, embed_text


def chunk(src: str, budget: int = 4000, repo: str = "test"):
    return CastChunker(budget=budget, repo=repo).chunk_file("mod.py", src)


# ---------------------------------------------------------------- basics


def test_small_file_single_chunk():
    src = "import os\n\nX = 1\n\ndef f():\n    return X\n"
    r = chunk(src)
    assert validate_partition(r) == []
    assert len(r.chunks) == 1
    assert r.chunks[0].text == src


def test_empty_file():
    r = chunk("")
    assert r.chunks == [] and r.total_lines == 0


def test_comment_only_file():
    r = chunk("# just a comment\n# another\n")
    assert validate_partition(r) == []
    assert len(r.chunks) == 1


def test_syntax_error_fallback():
    r = chunk("def broken(:\n    pass\n")
    assert not r.parse_ok
    assert validate_partition(r) == []
    assert r.chunks[0].kind in ("file", "block")


def test_determinism():
    src = "class A:\n    def m(self, x):\n        return x + 1\n\n\ndef top():\n    return A().m(2)\n"
    a = chunk(src)
    b = chunk(src)
    assert [c.to_dict() for c in a.chunks] == [c.to_dict() for c in b.chunks]


# ---------------------------------------------------------------- merging


def test_merge_small_functions_into_one_chunk():
    src = "\n\n".join(f"def f{i}():\n    return {i}" for i in range(5)) + "\n"
    r = chunk(src)
    assert validate_partition(r) == []
    assert len(r.chunks) == 1  # all tiny, merged
    assert "f0" in (r.chunks[0].name or "") and "f4" in (r.chunks[0].name or "")


def test_budget_forces_split():
    body = "    x = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'\n" * 20
    src = "\n".join(f"def f{i}():\n{body}" for i in range(6))
    r = chunk(src, budget=1500)
    assert validate_partition(r) == []
    assert len(r.chunks) > 1
    for c in r.chunks:
        assert c.nws_chars <= 1500


def test_comments_attach_to_following_def():
    src = "def a():\n    pass\n\n# explains b in detail\n# more\ndef b():\n    pass\n"
    r = chunk(src, budget=20)  # force a, b into separate chunks
    assert validate_partition(r) == []
    b_chunk = next(c for c in r.chunks if c.name == "b")
    assert "# explains b" in b_chunk.text


def test_decorators_included():
    src = "import x\n\n@x.route('/foo')\n@x.auth\ndef handler():\n    pass\n" + "def pad():\n    " + "y=1;" * 200 + "\n"
    r = chunk(src, budget=300)
    h = next(c for c in r.chunks if c.name and "handler" in c.name)
    assert "@x.route('/foo')" in h.text and "@x.auth" in h.text


# ---------------------------------------------------------------- class splitting


BIG_CLASS = (
    "CONST = {'a': 1}\n\n\n"
    "class Big:\n"
    '    """Big class doc."""\n\n'
    "    attr = 1\n\n"
    + "\n".join(
        f"    def m{i}(self, x):\n"
        f'        """method {i}"""\n'
        + f"        v = 'zzzzzzzzzzzzzzzzzzzzzzzzzzzzz{i}'\n" * 12
        for i in range(8)
    )
    + "\n"
)


def test_big_class_splits_into_header_and_methods():
    r = chunk(BIG_CLASS, budget=900)
    assert validate_partition(r) == []
    kinds = {c.kind for c in r.chunks}
    assert "method" in kinds or "block" in kinds
    header = next(c for c in r.chunks if "class Big" in c.text)
    assert "Big class doc" in header.text and "attr = 1" in header.text
    m3 = next(c for c in r.chunks if c.name and "Big.m3" in c.name)
    assert m3.kind == "method"
    assert "class Big" in m3.breadcrumb  # method breadcrumb carries class context


def test_method_breadcrumb_has_signature():
    r = chunk(BIG_CLASS, budget=900)
    m0 = next(c for c in r.chunks if c.name == "Big.m0")
    assert "def m0(self, x)" in m0.breadcrumb
    assert m0.breadcrumb.startswith("test > mod.py")


def test_oversized_function_splits_into_parts():
    src = "def huge():\n" + "    q = 'wwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww'\n" * 100
    r = chunk(src, budget=1000)
    assert validate_partition(r) == []
    blocks = [c for c in r.chunks if c.name == "huge"]
    assert len(blocks) > 1
    assert all(c.part for c in blocks)
    assert blocks[0].part.startswith("1/")
    assert "def huge()" in blocks[0].text  # header stays with first block


def test_giant_dict_literal_line_split():
    src = "TABLE = {\n" + "".join(f"    'k{i}': 'v{i}'*9,\n" for i in range(400)) + "}\n"
    r = chunk(src, budget=1200)
    assert validate_partition(r) == []
    assert len(r.chunks) > 1


# ---------------------------------------------------------------- symbols


def test_symbols_extraction():
    src = (
        "MAX_ROUNDS = 5\n"
        "class Svc:\n"
        '    """Service."""\n'
        "    limit = 3\n"
        "    @staticmethod\n"
        "    def go(x: int) -> str:\n"
        '        """Run it."""\n'
        "        return str(x)\n"
        "    async def stream(self):\n"
        "        pass\n"
        "def top():\n"
        "    pass\n"
    )
    r = chunk(src)
    by_name = {s.name: s for s in r.symbols}
    assert by_name["MAX_ROUNDS"].kind == "constant"
    assert by_name["Svc"].kind == "class" and by_name["Svc"].doc == "Service."
    assert by_name["Svc.limit"].kind == "class_attr"
    assert by_name["Svc.go"].kind == "method"
    assert "staticmethod" in by_name["Svc.go"].decorators
    assert by_name["Svc.go"].signature == "def go(x: int) -> str"
    assert by_name["Svc.stream"].kind == "async_method"
    assert by_name["top"].kind == "function"


def test_skeleton_contains_signatures_and_doc():
    src = '"""Module doc."""\nLIMIT = 9\nclass A:\n    def run(self, q):\n        """Does run."""\n        pass\n'
    r = chunk(src)
    sk = r.skeleton
    assert "Module doc." in sk
    assert "LIMIT = 9" in sk
    assert "class A" in sk
    assert "def run(self, q)" in sk and "Does run." in sk


def test_embed_text_has_breadcrumb_header():
    r = chunk("def f():\n    pass\n")
    t = embed_text(r.chunks[0])
    assert t.startswith("# test > mod.py")


def test_whitespace_only_docstring():
    src = 'def f():\n    """   """\n    pass\n\nclass C:\n    """\n\n    """\n    pass\n'
    r = chunk(src)
    assert validate_partition(r) == []
    assert all(s.doc is None for s in r.symbols)
