"""RateLimiter: the public-demo meter on the LLM routes.

The limit exists to bound LLM SPEND, so anything that costs no call must not
cost a slot — a flow-cache hit is served with zero LLM calls and is refunded.
"""

from megabrain.server.http import RateLimiter


def test_allows_up_to_the_limit_then_blocks():
    rl = RateLimiter(3)
    assert [rl.check("1.1.1.1") for _ in range(3)] == [None, None, None]
    retry = rl.check("1.1.1.1")
    assert retry is not None and retry > 0


def test_each_ip_gets_its_own_quota():
    """Per-visitor, not a shared pool — the whole point of keying by IP."""
    rl = RateLimiter(2)
    rl.check("a")
    rl.check("a")
    assert rl.check("a") is not None      # a is out
    assert rl.check("b") is None          # b is untouched


def test_a_refund_returns_the_slot():
    rl = RateLimiter(2)
    rl.check("ip")
    rl.check("ip")
    assert rl.check("ip") is not None
    rl.refund("ip")
    assert rl.check("ip") is None, "a refunded slot must be usable again"


def test_cache_hits_never_exhaust_a_visitor():
    """A visitor asking only cached questions can go forever: each ask takes a
    slot up front (the cache hit is only known after retrieval) and gives it
    straight back."""
    rl = RateLimiter(2)
    for _ in range(20):
        assert rl.check("ip") is None
        rl.refund("ip")                   # served_from_cache
    assert rl.check("ip") is None


def test_refunding_an_unknown_ip_is_harmless():
    rl = RateLimiter(1)
    rl.refund("never-seen")               # must not raise or create state
    assert rl.check("never-seen") is None


def test_refund_does_not_grant_credit_beyond_the_window():
    """Refund pops one recorded hit; it can't push the count below zero and
    hand out extra quota."""
    rl = RateLimiter(1)
    rl.refund("ip")
    rl.refund("ip")
    assert rl.check("ip") is None
    assert rl.check("ip") is not None      # still exactly one slot
