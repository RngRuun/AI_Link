import tempfile
import unittest
from pathlib import Path
from ailink.instance import DEFAULT_STATE,instance_name

class InstanceTests(unittest.TestCase):
    def test_default_retains_compatibility_with_installed_versions(self):
        self.assertEqual(instance_name(DEFAULT_STATE),'Local\\AI-Link-Native-Desktop-0.2')
    def test_explicit_isolated_states_do_not_share_the_default_mutex(self):
        with tempfile.TemporaryDirectory() as root:
            a=Path(root)/'one';b=Path(root)/'two'
            self.assertNotEqual(instance_name(a),instance_name(b));self.assertNotEqual(instance_name(a),instance_name())
            self.assertEqual(instance_name(a),instance_name(a/'.'))
