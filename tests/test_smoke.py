def test_package_importable():
    import semtune

    assert semtune.__version__ == "0.1.0"
