def test_greet() -> None:
    from gc_monitor.core import greet
    assert greet("World") == "Hello, World"
