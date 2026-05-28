from gc_monitor.data import dur_to_us, ts_to_us


class TestTimeConversions:
    def test_ts_to_us(self):
        assert ts_to_us(1_000_000) == 1_000

    def test_ts_to_us_zero(self):
        assert ts_to_us(0) == 0

    def test_ts_to_us_rounds_down(self):
        assert ts_to_us(1_999) == 1

    def test_dur_to_us(self):
        assert dur_to_us(1_000_000, 3_000_000) == 2_000

    def test_dur_to_us_zero(self):
        assert dur_to_us(1000, 1000) == 0

    def test_dur_to_us_negative(self):
        assert dur_to_us(5_000_000, 2_000_000) == -3_000
