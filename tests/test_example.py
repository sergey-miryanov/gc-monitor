def test_greet():
    from examplepkg.core import greet
    assert greet("World") == "Hello, World"
