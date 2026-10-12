"""Run with: python3 -m unittest discover -s code/test -p 'test_*.py'"""
import argparse
import csv
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scenarios import generate_handover_loss as loss


class HandoverLossTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.routes = self.state / 'dynamic_state_100ms_for_1s'
        self.routes.mkdir(parents=True)
        (self.state / 'tles.txt').write_text('1 4\n')
        (self.state / 'ground_stations.txt').write_text('0,source\n1,target\n')
        # Forward: 4-0-1-3-5. Reverse: 5-3-1-0-4.
        self.table = {(4, 5): (0, 0, 2), (0, 5): (1, 0, 0),
                      (1, 5): (3, 1, 0), (3, 5): (5, 2, 0),
                      (5, 4): (3, 0, 2), (3, 4): (1, 0, 1),
                      (1, 4): (0, 0, 0), (0, 4): (4, 2, 0)}
        for ms in range(0, 1000, 100):
            self.snapshot(ms, self.table if ms == 0 else {})

    def snapshot(self, ms, rows):
        (self.routes / f'fstate_{ms * 1_000_000}.txt').write_text(''.join(
            f'{node},{dst},{peer},{own},{remote}\n'
            for (node, dst), (peer, own, remote) in rows.items()))

    def changes(self, duration=1_000_000_000):
        return loss.detect_handovers(self.routes, 100_000_000, duration, 4, 5, 6)

    def cli(self, output, *options):
        return subprocess.run([sys.executable, str(Path(loss.__file__)), str(output),
                               '--state', str(self.state), '--src', '4', '--dst', '5', *options],
                              capture_output=True, text=True)

    def test_deltas_repetition_unrelated_changes_and_restoration(self):
        alternate = {(0, 5): (2, 1, 0), (2, 5): (3, 1, 1)}
        self.snapshot(100, alternate)
        self.snapshot(200, {(2, 4): (0, 0, 1)})  # Not on either active path.
        self.snapshot(300, dict(reversed(list((self.table | alternate | {(2, 4): (0, 0, 1)}).items()))))
        self.snapshot(400, {(0, 5): self.table[0, 5]})
        events = self.changes()
        self.assertEqual([e.time_ns for e in events], [100_000_000, 400_000_000])
        self.assertEqual(len(events[0].before[0]), len(events[0].after[0]))
        self.assertEqual(events[0].before[1], events[0].after[1])
        windows = loss.loss_windows(events, 30_000_000, 1_000_000_000)
        expected = [(70_000_000, 100_000_001), (370_000_000, 400_000_001)]
        self.assertEqual(windows[4, 'gsl', '*'], expected)
        self.assertEqual(windows[5, 'gsl', '*'], expected)  # Include ACKs even if reverse stays fixed.
        self.assertEqual(set(windows), {(4, 'gsl', '*'), (5, 'gsl', '*')})

    def test_overlapping_windows_and_simulation_boundaries(self):
        self.snapshot(100, {(0, 5): (2, 1, 0), (2, 5): (3, 1, 1)})
        self.snapshot(200, {(0, 5): self.table[0, 5]})
        windows = loss.loss_windows(self.changes(), 200_000_000, 1_000_000_000)
        self.assertEqual(windows[4, 'gsl', '*'], [(0, 200_000_001)])
        self.assertEqual(self.changes(duration=100_000_000), [])
        self.assertEqual(loss.merge_windows([(20, 30), (0, 10), (10, 25)]), [(0, 30)])

    def test_reverse_only_and_interface_only_changes(self):
        self.snapshot(100, {(3, 4): (2, 1, 1), (2, 4): (0, 0, 1)})
        self.snapshot(200, {(0, 5): (1, 2, 0)})
        events = self.changes()
        self.assertEqual([e.time_ns for e in events], [100_000_000, 200_000_000])
        self.assertEqual(events[0].before[0], events[0].after[0])
        self.assertEqual(events[1].before[0][1][1], events[1].after[0][1][1])

    def test_generated_files_and_existing_output_protection(self):
        self.snapshot(100, {(0, 5): (2, 1, 0), (2, 5): (3, 1, 1)})
        output = self.root / 'out'
        result = self.cli(output)
        self.assertEqual(result.returncode, 0, result.stderr)
        for ms, expected_start in [(30, '0.07'), (100, '0'), (200, '0')]:
            lines = (output / f'loss_{ms}ms.txt').read_text().splitlines()
            self.assertIn('base_loss_rate=0', lines)
            rows = list(csv.reader(line for line in lines if line and line[0].isdigit()))
            self.assertIn(['4', 'gsl', '*', expected_start, '0.100000001', '1'], rows)
            self.assertEqual({tuple(row[:3]) for row in rows}, {('4', 'gsl', '*'), ('5', 'gsl', '*')})
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(len(row) == 6 and row[-1] == '1' for row in rows))
        with (output / 'handovers.csv').open() as stream:
            events = list(csv.DictReader(stream))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['old_path'], '4>0>1>3>5')
        self.assertEqual(events[0]['new_path'], '4>0>2>3>5')
        before = {p.name: p.read_bytes() for p in output.iterdir()}
        self.assertNotEqual(self.cli(output).returncode, 0)
        self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})

    def test_no_changes_produces_zero_loss(self):
        self.assertEqual(self.changes(), [])
        output = self.root / 'no_changes'
        self.assertEqual(self.cli(output).returncode, 0)
        lines = (output / 'loss_30ms.txt').read_text().splitlines()
        self.assertEqual([s for s in lines if not s.startswith('#')], ['base_loss_rate=0'])

    def test_bad_routes_fail_before_creating_output(self):
        for name, update in [('loop', {(0, 5): (4, 2, 0)}),
                             ('unreachable', {(0, 5): (-1, -1, -1)}),
                             ('missing', {(0, 5): (2, 1, 0)})]:
            self.snapshot(100, update)
            result = self.cli(self.root / name)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('fstate_100000000.txt', result.stderr)
            self.assertFalse((self.root / name).exists())
        (self.routes / 'fstate_100000000.txt').unlink()
        self.assertNotEqual(self.cli(self.root / 'gap').returncode, 0)
        self.assertFalse((self.root / 'gap').exists())

    def test_invalid_parameters(self):
        for options in [('--duration', '0'), ('--duration', '2'), ('--duration', 'nan'),
                        ('--duration', '0.0000000001'), ('--src', '0'), ('--dst', '4')]:
            result = self.cli(self.root / 'invalid', *options)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('Traceback', result.stderr)
            self.assertFalse((self.root / 'invalid').exists())
        self.assertEqual(loss.nanoseconds('0.000000001'), 1)
        with self.assertRaises(argparse.ArgumentTypeError):
            loss.nanoseconds('infinity')


if __name__ == '__main__':
    unittest.main()
