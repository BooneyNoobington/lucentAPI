import pytest

from lucentapi.numbering import LUCENT


def test_capacity():
    assert LUCENT.capacity == 10 * 7 * 10 * 24


def test_known_identifiers():
    assert LUCENT.identifier(0) == "0·Red·0·Alpha"
    assert LUCENT.identifier(1000) == "0·Cyan·1·Rho"
    assert LUCENT.identifier(LUCENT.capacity - 1) == "9·Violet·9·Omega"


def test_round_trip():
    for sequence in range(LUCENT.capacity):
        assert LUCENT.sequence(LUCENT.identifier(sequence)) == sequence


def test_out_of_range():
    assert LUCENT.identifier(-1) is None
    assert LUCENT.identifier(LUCENT.capacity) is None


@pytest.mark.parametrize("identifier", ["", "0·Red·0", "0·Magenta·0·Alpha", "0-Red-0-Alpha"])
def test_foreign_identifiers(identifier):
    assert LUCENT.sequence(identifier) is None
