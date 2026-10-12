"""Synthetic checks for epoch boundaries and measurement semantics."""
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from extract_metrics import measure
from scenarios.generate_congestion import capacities, generate
from scenarios.generate_handover_loss import Handover, loss_windows, select_handovers
from run_data import Run, edges, goodput, jain, time_stats


class ExperimentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_post_handover_loss_merges_and_clips(self):
        path = ((4, 0, 0, 1), (0, 5, 1, 0))
        events = [Handover(t, (path,), (path,)) for t in (100, 200, 950)]
        self.assertEqual(loss_windows(events, 200, 1000, 'after')[4, 'gsl', '*'],
                         [(100, 400), (950, 1000)])
        changed = ((4, 1, 0, 1), (1, 5, 1, 0))
        self.assertEqual(select_handovers([Handover(1, (path,), (changed,))], 'gsl'),
                         [Handover(1, (path,), (changed,))])

    def test_epoch_generation_deduplicates_and_preserves_changes(self):
        handovers = self.root / 'handovers.csv'
        handovers.write_text('time_s,source,target\n1,4,5\n1,5,4\n2.5,4,5\n')
        out = self.root / 'scenario'
        self.assertEqual(generate(out, handovers, 4_000_000_000, 4, 'alternating', 20, 100, 1), 3)
        self.assertEqual((out / 'congestion_scenario.txt').read_text().splitlines()[1:],
                         ['4,gsl,*,0,1,100', '4,gsl,*,1,2.5,20', '4,gsl,*,2.5,4,100'])
        with self.assertRaises(FileExistsError):
            generate(out, handovers, 4_000_000_000, 4, 'drop', 20, 100, 1)
        self.assertEqual(capacities(3, 'rise', 20, 100, 1), [20, 100, 100])
        self.assertEqual(capacities(3, 'drop', 20, 100, 1), [100, 20, 20])
        self.assertEqual(capacities(4, 'random', 20, 100, 42), capacities(4, 'random', 20, 100, 42))
        self.assertNotEqual(capacities(4, 'random', 20, 100, 42), capacities(4, 'random', 20, 100, 43))

    def test_step_goodput_and_time_weighted_rtt(self):
        # No linear interpolation across an idle second; preserve the partial last bin.
        bounds = edges(0, 2.5, 1)
        self.assertEqual(bounds, [0, 1, 2, 2.5])
        self.assertEqual(goodput(([0, 2, 2.5], [0, 125000, 250000]), bounds), [0, 1, 2])
        self.assertAlmostEqual(jain([1, 3]), 0.8)
        self.assertIsNone(jain([1]))
        self.assertIsNone(jain([0, 0]))
        self.assertEqual(time_stats(([0, 0.1, 0.2, 1], [10, 10, 10, 30]), 0, 4), (25, 30))

    def fixture(self):
        run = self.root / 'run'
        logs = run / 'logs_ns3'
        logs.mkdir(parents=True)
        (logs / 'finished.txt').write_text('Yes\n')
        (run / 'config_ns3.properties').write_text('simulation_end_time_ns=6000000000\nsimulation_seed=1\n'
                                                  'tcp_socket_type=TcpCubic\nlink_loss_scenario_file="loss.txt"\n')
        (run / 'schedule.csv').write_text('0,4,5,100000000000,0,,\n1,4,5,100000000000,0,,\n')
        (run / 'epochs.csv').write_text('epoch,start_s,end_s,capacity_mbps\n0,0,2,1\n1,2,6,2\n')
        (run / 'handovers.csv').write_text('time_s\n2\n')
        (run / 'loss.txt').write_text('base_loss_rate=0\n4,gsl,*,2,3,1\n')
        for f in (0, 1):
            (logs / f'tcp_flow_{f}_progress.csv').write_text(''.join(
                f'{f},{t*10**9},{value}\n' for t, value in enumerate([0, 62500, 125000, 125000, 250000, 375000, 500000])))
            (logs / f'tcp_flow_{f}_rtt.csv').write_text(f'{f},0,10000000\n{f},3000000000,30000000\n')
        return run

    def test_scores_outage_recovery_and_incomplete_run(self):
        path = self.fixture()
        summary, flows, epochs, handovers = measure(Run(path), bin_s=1)
        self.assertAlmostEqual(summary['goodput_mbps'], 8 / 6)
        self.assertEqual(summary['jain_goodput'], 1)
        self.assertEqual(summary['longest_zero_goodput_s'], 1)
        self.assertEqual(summary['srtt_flow_mean_ms'], 20)
        self.assertEqual(summary['capacity_normalized_goodput'], 0.8)
        self.assertEqual(handovers[0]['outage_end_s'], 3)
        self.assertEqual(handovers[0]['recovery_s'], 0)
        self.assertFalse(handovers[0]['censored'])
        self.assertIsNone(summary['queue_drop_packets'])
        self.assertEqual(len(flows), 2)
        self.assertEqual(epochs[1]['goodput_mbps'], 1.5)
        _, _, _, censored = measure(Run(path), bin_s=1, hold_bins=4)
        self.assertTrue(censored[0]['censored'])
        self.assertIsNone(censored[0]['recovery_s'])
        (path / 'logs_ns3/finished.txt').write_text('No')
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            Run(path)

    def test_metrics_cli_and_warmup(self):
        path = self.fixture()
        summary, _, epochs, events = measure(Run(path), start=3, bin_s=1)
        self.assertEqual(summary['goodput_mbps'], 2)
        self.assertEqual(len(epochs), 1)
        self.assertEqual(events, [])
        output = self.root / 'metrics'
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'extract_metrics.py'),
                                 str(path), '--output', str(output)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with (output / 'summary.csv').open() as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 1)
        self.assertEqual(json.loads((output / 'measurement.json').read_text())['hold_bins'], 3)

    def test_completed_or_staggered_flows_do_not_get_bulk_fairness(self):
        path = self.fixture()
        schedule = path / 'schedule.csv'
        original = schedule.read_text()
        for replacement in (original.replace('100000000000', '500000'),
                            original.replace('1,4,5,100000000000,0', '1,4,5,100000000000,1000000000')):
            schedule.write_text(replacement)
            summary, _, _, _ = measure(Run(path))
            self.assertIsNone(summary['jain_goodput'])
            self.assertIsNone(summary['jain_bin_time_mean'])


if __name__ == '__main__':
    unittest.main()
