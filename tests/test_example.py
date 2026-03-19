def test_greet():
    from gc_monitor.core import greet
    assert greet("World") == "Hello, World"
