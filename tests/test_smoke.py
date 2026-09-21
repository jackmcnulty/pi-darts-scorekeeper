import darts


def test_version_is_a_non_empty_string() -> None:
    assert isinstance(darts.__version__, str)
    assert darts.__version__
