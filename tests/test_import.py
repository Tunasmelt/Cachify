def test_import() -> None:
    import pcg

    assert isinstance(pcg.__version__, str)
