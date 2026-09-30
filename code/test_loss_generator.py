"""Standard-library generator unit tests; no simulator needed.

From the submission root: python3 -m unittest discover -s code -p 'test_*.py' -v
Checks seed replay, satellite independence, event bounds/non-overlap, zero peak
frequency, and invalid inputs. All fixtures use temporary files.
"""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from gen_loss_state import generate


class LossGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        (self.state / "tles.txt").write_text("1 3\n")
        self.params = dict(event_seed=42, satellites="all", link_types="isl",
                           base_loss_probability=0.001, peak_loss_probability=0.5,
                           peak_probability_per_second=0.5,
                           peak_duration_min_ms=20, peak_duration_max_ms=200)

    def run_generator(self, name, **changes):
        params = dict(self.params, **changes)
        config = self.root / f"{name}.properties"
        config.write_text("".join(f"{k}={v}\n" for k, v in params.items()))
        with contextlib.redirect_stdout(io.StringIO()):
            return generate(self.state, config, 30, self.root / name)

    def test_reproducible_and_independent_satellites(self):
        all_events = self.run_generator("a")
        self.assertEqual(all_events, self.run_generator("b"))
        for file in ("loss_events.csv", "loss_model.properties"):
            self.assertEqual((self.root / "a" / file).read_bytes(), (self.root / "b" / file).read_bytes())
        self.assertEqual([e for e in all_events if e[0] == 1], self.run_generator("one", satellites="1"))
        self.assertNotEqual(all_events, self.run_generator("different", event_seed=43))

    def test_durations_and_no_overlap(self):
        last = {}
        for sat, start, end in self.run_generator("events"):
            self.assertGreaterEqual(start, last.get(sat, 0))
            self.assertGreater(end, start)
            self.assertLessEqual(end - start, 200_000_000)
            self.assertLessEqual(end, 30_000_000_000)
            last[sat] = end

    def test_zero_peak_probability(self):
        self.assertEqual([], self.run_generator("none", peak_probability_per_second=0))

    def test_invalid_parameters(self):
        for index, changes in enumerate((dict(peak_probability_per_second=1),
                                        dict(base_loss_probability=0.8),
                                        dict(peak_duration_min_ms=0),
                                        dict(satellites="3"), dict(link_types="radio"),
                                        dict(peak_loss_probability="nan"))):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_generator(f"invalid{index}", **changes)


if __name__ == "__main__":
    unittest.main()
