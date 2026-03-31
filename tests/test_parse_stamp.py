"""
Tests for parse_stamp and parse_rev_key functions used in View Results pages.

These functions are duplicated across View_Results.py, View_Best_Results.py,
and Review_Model_Stability.py. This test ensures they handle edge cases correctly
and return comparable values to prevent TypeError during sorting.
"""

import datetime as dt
import re


# Copy the functions here for testing without importing the full modules
def parse_stamp(stamp: str):
    """Parse timestamp string - copied from View_Results.py"""
    try:
        return dt.datetime.strptime(stamp, "%m%d_%H%M%S")
    except Exception:
        # Return datetime.min for unparseable stamps so they sort to the end
        return dt.datetime.min


def parse_rev_key(rev: str):
    """Parse revision for sorting - copied from View_Results.py"""
    m = re.search(r"(\d+)$", (rev or "").strip())
    if m:
        return (0, int(m.group(1)))
    return (1, (rev or "").lower())


def test_parse_stamp_valid_format():
    """Test parse_stamp with valid timestamp format"""
    # Test valid timestamps
    result = parse_stamp("0219_085819")
    assert isinstance(result, dt.datetime)
    assert result.month == 2
    assert result.day == 19
    assert result.hour == 8
    assert result.minute == 58
    assert result.second == 19

    result = parse_stamp("1231_235959")
    assert isinstance(result, dt.datetime)
    assert result.month == 12
    assert result.day == 31
    assert result.hour == 23
    assert result.minute == 59
    assert result.second == 59


def test_parse_stamp_invalid_format():
    """Test parse_stamp with invalid timestamp formats"""
    # Test invalid formats - should return datetime.min
    invalid_stamps = [
        "invalid_stamp",
        "2024-02-19",
        "not_a_timestamp",
        "",
        "abc123",
    ]

    for stamp in invalid_stamps:
        result = parse_stamp(stamp)
        assert isinstance(result, dt.datetime), f"Failed for {stamp}"
        assert (
            result == dt.datetime.min
        ), f"Should return datetime.min for {stamp}"


def test_parse_stamp_sorting():
    """Test that parse_stamp results can be sorted without TypeError"""
    # Mix of valid and invalid timestamps
    stamps = [
        "0219_085819",
        "invalid_stamp",
        "1231_235959",
        "not_a_timestamp",
        "0101_120000",
    ]

    # Parse all stamps
    parsed = [parse_stamp(s) for s in stamps]

    # Should all be datetime objects
    assert all(isinstance(p, dt.datetime) for p in parsed)

    # Should be sortable without TypeError
    try:
        sorted_parsed = sorted(parsed, reverse=True)
        assert len(sorted_parsed) == len(parsed)
    except TypeError as e:
        assert False, f"Sorting failed with TypeError: {e}"


def test_parse_stamp_consistency():
    """Test that parse_stamp handles valid and invalid stamps consistently"""
    test_stamp = "0219_085819"
    invalid_stamp = "invalid"

    # Valid stamp should parse correctly
    result_valid = parse_stamp(test_stamp)
    assert isinstance(result_valid, dt.datetime)
    assert result_valid.month == 2
    assert result_valid.day == 19

    # Invalid stamp should return datetime.min
    result_invalid = parse_stamp(invalid_stamp)
    assert isinstance(result_invalid, dt.datetime)
    assert result_invalid == dt.datetime.min


def test_parse_rev_key():
    """Test parse_rev_key function"""
    # Test with numeric suffix (should return (0, number))
    result = parse_rev_key("myname_1")
    assert result == (0, 1)

    result = parse_rev_key("test_123")
    assert result == (0, 123)

    result = parse_rev_key("r100")
    assert result == (0, 100)

    # Test without numeric suffix (should return (1, lowercase))
    result = parse_rev_key("myname")
    assert result == (1, "myname")

    result = parse_rev_key("TestRev")
    assert result == (1, "testrev")

    # Test edge cases
    result = parse_rev_key("")
    assert result == (1, "")

    result = parse_rev_key(None)
    assert result == (1, "")


def test_sorting_with_parse_functions():
    """Test that sorting works correctly with parse functions"""
    # Create test keys: (rev, country, stamp)
    keys = [
        ("myname_1", "US", "0219_085819"),
        ("myname_2", "US", "0219_095819"),
        ("myname_1", "UK", "invalid_stamp"),
        ("myname_3", "US", "0219_075819"),
        ("test", "US", "0219_105819"),
    ]

    # Sort by (rev, stamp) - should not raise TypeError
    try:
        sorted_keys = sorted(
            keys,
            key=lambda k: (parse_rev_key(k[0]), parse_stamp(k[2])),
            reverse=True,
        )
        assert len(sorted_keys) == len(keys)
    except TypeError as e:
        assert False, f"Sorting failed with TypeError: {e}"

    # Sort by stamp only - should not raise TypeError
    try:
        sorted_by_stamp = sorted(
            keys, key=lambda k: parse_stamp(k[2]), reverse=True
        )
        assert len(sorted_by_stamp) == len(keys)

        # Invalid stamps should be at the end (datetime.min < valid dates when sorted reverse=True means they go to end)
        # Find the key with invalid_stamp
        invalid_key_index = next(
            i for i, k in enumerate(sorted_by_stamp) if k[2] == "invalid_stamp"
        )
        # It should be last since datetime.min is smallest
        assert invalid_key_index == len(sorted_by_stamp) - 1
    except TypeError as e:
        assert False, f"Sorting by stamp failed with TypeError: {e}"


if __name__ == "__main__":
    # Run tests
    test_parse_stamp_valid_format()
    print("✓ test_parse_stamp_valid_format passed")

    test_parse_stamp_invalid_format()
    print("✓ test_parse_stamp_invalid_format passed")

    test_parse_stamp_sorting()
    print("✓ test_parse_stamp_sorting passed")

    test_parse_stamp_consistency()
    print("✓ test_parse_stamp_consistency passed")

    test_parse_rev_key()
    print("✓ test_parse_rev_key passed")

    test_sorting_with_parse_functions()
    print("✓ test_sorting_with_parse_functions passed")

    print("\nAll tests passed!")
