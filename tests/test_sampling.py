from mcg_swarm.sampling import select_sample

def test_small_returns_all():
    keys = list(range(50))
    assert select_sample(keys, full_threshold=300, sample_size=300) == keys

def test_large_is_spread_and_bounded():
    keys = list(range(1_000_000))
    s = select_sample(keys, full_threshold=300, sample_size=300)
    assert len(s) <= 300
    assert s[0] == 0 and s[-1] == 999_999      # head and tail included
    assert s == sorted(s)                       # original order preserved
    assert len(set(s)) == len(s)                # no duplicates
    assert max(s) - min(s) > 900_000            # genuinely spans the table (not first-N)

def test_threshold_boundary():
    keys = list(range(300))
    assert select_sample(keys, full_threshold=300, sample_size=300) == keys  # == threshold -> all

def test_sample_size_one_does_not_crash():
    keys = list(range(1000))
    s = select_sample(keys, full_threshold=300, sample_size=1)
    assert s == [0, 999]
