import unittest
from unittest.mock import Mock
from ailink.desktop_bridge import DesktopBridge


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.app=Mock()
        self.app.shutdown_event.is_set.return_value=False
        self.bridge=DesktopBridge(self.app)

    def test_rejects_arbitrary_methods_and_extra_fields(self):
        for method,params in [('eval',{}),('network.enable',{'code':'x'}),('quota.refresh',{'token':'x'}),('status',[])]:
            with self.assertRaises(ValueError):self.bridge.dispatch(method,params)
        self.app.enable.assert_not_called()

    def test_switch_types_are_validated(self):
        for method,params in [('quota.arm',{'enabled':'false'}),('quota.select',{'threadId':'t','selected':1}),('quota.connect',{'shared':0})]:
            with self.assertRaises(ValueError):self.bridge.dispatch(method,params)
        self.app.quota.arm.assert_not_called()

    def test_desktop_shutdown_prevents_new_actions(self):
        self.app.shutdown_event.is_set.return_value=True
        with self.assertRaises(RuntimeError):self.bridge.dispatch('network.enable',{})
        self.app.enable.assert_not_called()

    def test_reports_exclude_quota_threads_and_account_state(self):
        self.app.status.return_value={k:k for k in ['version','config','telemetry','diagnostics','kernel','quota','system','network','team']}
        report=self.bridge.dispatch('report',{})
        self.assertNotIn('quota',report)
        self.assertNotIn('system',report)
        self.assertNotIn('team',report)
        self.assertEqual(len(report),6)

    def test_network_configuration_uses_service_validation(self):
        self.bridge.dispatch('network.configure',{'config':{'attempts':4}})
        self.app.configure.assert_called_once_with({'attempts':4})

    def test_quota_configuration_keeps_explicit_opt_in(self):
        self.bridge.dispatch('quota.configure',{'mode':'pro','threshold':20,'autoResume':False,'bucket':'codex'})
        self.app.quota.configure.assert_called_once_with('pro',20,False,'codex')


if __name__=='__main__':unittest.main()
