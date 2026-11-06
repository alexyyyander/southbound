import subprocess
import unittest
from unittest.mock import patch

from southbound.discover import parse_arp


class ArpDiscoveryTest(unittest.TestCase):
    @patch("southbound.discover.platform.system", return_value="Darwin")
    @patch("southbound.discover.run_cmd")
    def test_numeric_lookup_keeps_neighbors_without_reverse_dns(self, run_cmd, _system):
        def numeric_only(command, timeout):
            # Reverse DNS may stall until timeout before arp prints any rows.
            if "-an" not in command:
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 0, "\n".join([
                "? (192.168.100.1) at 88:cf:98:7e:b5:d1 on en0 ifscope [ethernet]",
                "? (192.168.100.2) at (incomplete) on en0 ifscope [ethernet]",
                "? (192.168.100.75) at 22:64:ca:a:fe:71 on en0 ifscope [ethernet]",
                "? (192.168.100.255) at ff:ff:ff:ff:ff:ff on en0 [ethernet]",
                "? (224.0.0.251) at 1:0:5e:0:0:fb on en0 [ethernet]",
            ]), "")

        run_cmd.side_effect = numeric_only
        self.assertEqual(parse_arp(), {
            "192.168.100.1": "88:cf:98:7e:b5:d1",
            "192.168.100.75": "22:64:ca:a:fe:71",
        })
