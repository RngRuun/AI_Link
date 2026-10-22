import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
from ailink.quota import QuotaGuard, quota_windows


def limits(primary=10, secondary=20):
    return {"rateLimitsByLimitId":{"codex":{"planType":"plus", "primary":{"usedPercent":primary,"resetsAt":1,"windowDurationMins":300},
            "secondary":None if secondary is None else {"usedPercent":secondary,"resetsAt":2,"windowDurationMins":10080}}}}


class FakeRPC:
    def __init__(self):
        self.calls=[]
        self.thread={"id":"t", "turns":[{"id":"turn1","status":"inProgress","items":[]}]}
        self.limits=limits()
        self.fail=None

    def call(self, method, params=None):
        self.calls.append((method,copy.deepcopy(params)))
        if method==self.fail:raise TimeoutError("lost reply")
        if method=="thread/read":return {"thread":copy.deepcopy(self.thread)}
        if method=="account/read":return {"account":{"type":"chatgpt","email":"test","planType":"plus"}}
        if method=="account/rateLimits/read":return self.limits
        return {}


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.guard=QuotaGuard(self.tmp.name,Mock())
        self.guard.rpc=self.rpc=FakeRPC()
        self.guard.account_key="test"
        self.guard.shared=False
        self.guard.plan="plus"
        self.guard.choices=[{"id":"t","name":"task"}]
        self.guard.data["threads"]={"t":{"selected":True,"state":"watching"}}
        self.guard.data["auto_resume"]=True
        self.guard.enabled=True
        self.set_windows(90,20)

    def set_windows(self,p,s=20):
        self.guard.windows,_=quota_windows(limits(p,s))
        self.guard.updated=time.time()

    def actions(self,method):
        return [p for m,p in self.rpc.calls if m==method]

    def pause(self):
        self.guard._evaluate()
        self.rpc.thread["turns"][-1]["status"]="interrupted"
        self.guard._evaluate()

    def test_threshold_interrupts_once_and_waits_for_confirmation(self):
        self.guard._evaluate();self.guard._evaluate()
        self.assertEqual(len(self.actions("turn/interrupt")),1)
        self.assertEqual(self.guard.data["threads"]["t"]["state"],"interrupt_pending")
        self.assertFalse(self.actions("turn/start"))

    def test_resumes_only_when_both_windows_full_once(self):
        self.pause();self.set_windows(0,1);self.guard._evaluate()
        self.assertFalse(self.actions("turn/start"))
        self.set_windows(0,0);self.guard._evaluate()
        # Simulate server returning the new active turn after accepted continuation.
        self.rpc.thread["turns"].append({"id":"turn2","status":"inProgress"})
        self.guard._evaluate()
        self.assertEqual(len(self.actions("turn/start")),1)

    def test_missing_previously_known_secondary_never_resumes(self):
        self.pause();self.set_windows(0,None);self.guard._evaluate()
        self.assertFalse(self.actions("turn/start"))

    def test_auto_resume_switch_and_master_switch(self):
        self.pause();self.set_windows(0,0)
        self.guard.data["auto_resume"]=False;self.guard._evaluate()
        self.guard.data["auto_resume"]=True;self.guard.enabled=False;self.guard._evaluate()
        self.assertFalse(self.actions("turn/start"))

    def test_new_user_turn_cancels_saved_continuation(self):
        self.pause();self.set_windows(0,0)
        self.rpc.thread["turns"].append({"id":"user2","status":"completed"})
        self.guard._evaluate()
        self.assertFalse(self.actions("turn/start"))
        self.assertEqual(self.guard.data["threads"]["t"]["state"],"watching")

    def test_stale_data_or_empty_data_never_resumes(self):
        self.pause();self.set_windows(0,0);self.guard.updated-=70;self.guard._evaluate()
        self.guard.windows=[];self.guard.updated=time.time();self.guard._evaluate()
        self.assertFalse(self.actions("turn/start"))

    def test_unknown_continuation_reply_is_not_replayed(self):
        self.pause();self.set_windows(0,0);self.rpc.fail="turn/start"
        with self.assertRaises(TimeoutError):self.guard._evaluate()
        self.rpc.fail=None;self.guard._evaluate()
        self.assertEqual(len(self.actions("turn/start")),1)
        saved=QuotaGuard(self.tmp.name,Mock())
        self.assertFalse(saved.enabled)
        self.assertEqual(saved.data["threads"]["t"]["state"],"resume_pending")

    def test_unknown_interrupt_reply_does_not_retry(self):
        self.rpc.fail="turn/interrupt"
        with self.assertRaises(TimeoutError):self.guard._evaluate()
        self.rpc.fail=None;self.guard._evaluate()
        self.assertEqual(len(self.actions("turn/interrupt")),1)

    def test_confirmed_still_running_same_turn_gets_bounded_interrupt_retry(self):
        self.rpc.fail='turn/interrupt'
        with self.assertRaises(TimeoutError):self.guard._evaluate()
        self.rpc.fail=None;record=self.guard.data['threads']['t']
        for _ in range(4):record['interrupt_requested_at']=time.time()-6;self.guard._evaluate()
        self.assertEqual(len(self.actions('turn/interrupt')),3)
        self.assertIn('仍未确认',record['control_error'])

    def test_new_observed_turn_after_unknown_resume_is_still_guarded(self):
        self.pause();self.set_windows(0,0);self.rpc.fail='turn/start'
        with self.assertRaises(TimeoutError):self.guard._evaluate()
        self.rpc.fail=None;self.rpc.thread['turns'].append({'id':'accepted','status':'inProgress'})
        self.guard._evaluate();self.set_windows(90,20);self.guard._evaluate()
        self.assertEqual(self.actions('turn/interrupt')[-1]['turnId'],'accepted')
        self.assertEqual(len(self.actions('turn/start')),1)

    def test_scope_is_explicit_and_deselection_disables(self):
        self.guard.data["threads"]["t"]["selected"]=False;self.guard._evaluate()
        self.assertFalse(self.actions("turn/interrupt"))
        self.guard.data["threads"]["t"]["selected"]=True;self.guard.choices=[];self.guard._evaluate()
        self.assertFalse(self.actions("turn/interrupt"))

    def test_plan_mismatch_disables_guard(self):
        self.guard.plan="pro";self.guard._evaluate()
        self.assertFalse(self.guard.enabled)
        self.assertFalse(self.actions("turn/interrupt"))

    def test_separate_editable_plus_pro_thresholds(self):
        self.guard.enabled=False
        self.guard.configure("pro",22,False)
        self.guard.configure("plus",35,True)
        self.assertEqual(self.guard.data["thresholds"],{"plus":{"primary":35,"secondary":35},"pro":{"primary":22,"secondary":22}})
        with self.assertRaises(ValueError):self.guard.configure("plus",100,False)
        with self.assertRaises(ValueError):self.guard.configure("plus",True,False)

    def test_failed_refresh_invalidates_previous_data(self):
        self.rpc.fail="account/rateLimits/read"
        with self.assertRaises(TimeoutError):self.guard.refresh()
        self.assertFalse(self.guard.windows)
        self.assertEqual(self.guard.updated,0)

    def test_short_and_long_thresholds_trigger_independently_on_boundary(self):
        self.guard.enabled=False
        self.guard.configure('plus',{'primary':25,'secondary':7},False)
        self.guard.enabled=True
        for used_primary,used_secondary,expected in [(74,92,[]),(75,0,['primary']),(0,93,['secondary'])]:
            with self.subTest(expected=expected):
                self.rpc.calls.clear();self.guard.data['threads']['t']['state']='watching'
                self.set_windows(used_primary,used_secondary);self.guard._evaluate()
                self.assertEqual(bool(self.actions('turn/interrupt')),bool(expected))
                if expected:self.assertEqual(self.guard.data['threads']['t']['trigger_windows'],expected)

    def test_independent_modes_persist_and_legacy_settings_migrate(self):
        self.guard.enabled=False
        self.guard.configure('plus',{'primary':26,'secondary':8},False)
        self.guard.configure('pro',{'primary':12,'secondary':3},False)
        saved=QuotaGuard(self.tmp.name,Mock())
        self.assertEqual(saved.data['thresholds'],{'plus':{'primary':26,'secondary':8},'pro':{'primary':12,'secondary':3}})
        data=json.loads(self.guard.path.read_text('utf-8'));data['thresholds']={'plus':36,'pro':19}
        self.guard.path.write_text(json.dumps(data),encoding='utf-8')
        migrated=QuotaGuard(self.tmp.name,Mock())
        self.assertEqual(migrated.data['thresholds'],{'plus':{'primary':36,'secondary':36},'pro':{'primary':19,'secondary':19}})
        self.assertIn('t',migrated.data['threads'])

    def test_invalid_window_limits_never_mutate_policy(self):
        self.guard.enabled=False
        before=copy.deepcopy(self.guard.data)
        for bad in [{'primary':20},{'primary':20,'secondary':0},{'primary':20,'secondary':True},
                    {'primary':20,'secondary':96},{'primary':20,'secondary':7,'other':1},
                    {'primary':20.5,'secondary':7}]:
            with self.subTest(value=bad),self.assertRaises(ValueError):self.guard.configure('plus',bad,False)
            self.assertEqual(self.guard.data,before)

    def test_new_task_is_blocked_by_either_window_before_rpc(self):
        self.guard.enabled=False;self.guard.configure('plus',{'primary':25,'secondary':7},False)
        self.guard.enabled=True
        for p,s in [(75,0),(0,93)]:
            self.set_windows(p,s);self.rpc.calls.clear()
            with self.assertRaisesRegex(RuntimeError,'触及'):self.guard.start_chat('test',self.tmp.name)
            self.assertFalse(self.actions('thread/start'));self.assertFalse(self.actions('turn/start'))

    def test_missing_long_window_cannot_arm_or_start_task(self):
        self.guard.enabled=False;self.set_windows(0,None)
        with self.assertRaisesRegex(RuntimeError,'长期'):self.guard.arm(True)
        self.guard.enabled=True
        with self.assertRaisesRegex(RuntimeError,'长期'):self.guard.start_chat('test',self.tmp.name)
        self.assertFalse(self.actions('turn/start'))

    def test_long_window_missing_at_stop_cannot_be_treated_as_full(self):
        self.set_windows(90,None);self.pause()
        self.set_windows(0,None);self.guard._evaluate()
        self.assertFalse(self.actions('turn/start'))

    def test_unreadable_target_does_not_skip_protection_for_another_target(self):
        self.guard.choices.insert(0,{'id':'unavailable','name':'unavailable'})
        self.guard.data['threads']={'unavailable':{'selected':True,'state':'watching'},**self.guard.data['threads']}
        original=self.rpc.call
        def call(method,params=None):
            if method=='thread/read' and params.get('threadId')=='unavailable':raise TimeoutError('control read timed out')
            return original(method,params)
        self.rpc.call=call;self.guard._evaluate()
        self.assertEqual(self.actions('turn/interrupt')[0]['threadId'],'t')
        self.assertIn('timed out',self.guard.data['threads']['unavailable']['control_error'])

    def test_prepared_task_at_capacity_sends_to_same_target_and_not_twice(self):
        self.set_windows(0,0)
        record=self.guard.data['threads']['t'];record.update(owned=True,prepared=True,cwd=str(Path(self.tmp.name).resolve()))
        for i in range(99):self.guard.data['threads'][str(i)]={'selected':False,'state':'watching'}
        result=self.guard.start_chat('one',self.tmp.name,'t')
        self.assertEqual(result,'t');self.assertFalse(self.actions('thread/start'))
        self.assertEqual(self.actions('turn/start')[0]['threadId'],'t')
        with self.assertRaises(ValueError):self.guard.start_chat('two',self.tmp.name,'t')
        self.assertEqual(len(self.actions('turn/start')),1)

    def test_unknown_initial_send_is_journaled_and_not_replayed(self):
        self.set_windows(0,0);record=self.guard.data['threads']['t']
        record.update(owned=True,prepared=True,cwd=str(Path(self.tmp.name).resolve()));self.rpc.fail='turn/start'
        with self.assertRaises(TimeoutError):self.guard.start_chat('one',self.tmp.name,'t')
        self.assertTrue(record['start_pending']);self.assertIn('未自动重发',record['control_error'])
        self.assertTrue(QuotaGuard(self.tmp.name,Mock()).data['threads']['t']['start_pending'])
        self.rpc.fail=None
        with self.assertRaises(ValueError):self.guard.start_chat('one',self.tmp.name,'t')
        self.assertEqual(len(self.actions('turn/start')),1)

    def test_reconnect_recreates_only_an_unused_prepared_target(self):
        self.guard.enabled=False;self.guard.rpc=None
        self.rpc.connect=lambda:None;self.rpc.close=lambda:None
        original=self.rpc.call
        def call(method,params=None):
            if method=='thread/resume':raise RuntimeError('no rollout found for thread id t')
            if method=='thread/start':return {'thread':{'id':'restored'}}
            return original(method,params)
        self.rpc.call=call;self.guard.rpc_factory=lambda **_:self.rpc
        self.guard.data['threads']['t'].update(owned=True,prepared=True,cwd=self.tmp.name)
        self.guard.data['resume_target']='t'
        self.addCleanup(self.guard.close);self.guard.connect()
        self.assertEqual([c['id'] for c in self.guard.choices],['restored'])
        self.assertEqual(self.guard.data['resume_target'],'restored')
        self.assertTrue(self.guard.data['threads']['restored']['prepared'])
        self.assertTrue(self.guard.data['threads']['restored']['selected'])
        self.assertFalse(self.actions('turn/start'))

    def test_reconnect_never_recreates_an_unknown_initial_send(self):
        self.guard.enabled=False;self.guard.rpc=None
        self.rpc.connect=lambda:None;self.rpc.close=lambda:None
        original=self.rpc.call
        def call(method,params=None):
            if method=='thread/resume':raise RuntimeError('no rollout found for thread id t')
            return original(method,params)
        self.rpc.call=call;self.guard.rpc_factory=lambda **_:self.rpc
        self.guard.data['threads']['t'].update(owned=True,prepared=True,start_pending=True,cwd=self.tmp.name)
        self.addCleanup(self.guard.close);self.guard.connect()
        self.assertFalse(self.actions('thread/start'));self.assertFalse(self.actions('turn/start'))
        self.assertIn('t',self.guard.data['threads'])

    def test_corrupt_or_absent_usage_is_not_zero(self):
        for usage in (None,True,"0",float('nan'),-1,101):
            payload=limits();payload["rateLimitsByLimitId"]["codex"]["primary"]["usedPercent"]=usage
            self.assertEqual(quota_windows(payload)[0],[])
        self.assertEqual(quota_windows({})[0],[])
        self.assertEqual(quota_windows(limits(),"missing")[0],[])

    def test_no_auto_reset_credit_consumption(self):
        self.pause();self.set_windows(0,0);self.guard._evaluate()
        self.assertFalse(any('consume' in m for m,_ in self.rpc.calls))

    def test_completed_target_is_skipped_at_threshold_and_full_quota(self):
        self.rpc.thread["turns"][-1]["status"]="completed"
        self.guard._evaluate()
        self.assertEqual(self.guard.data['threads']['t']['state'],'watching')
        self.assertFalse(self.actions('turn/interrupt'))
        self.set_windows(0,0);self.guard._evaluate();self.guard._evaluate()
        self.assertEqual(len(self.actions('turn/start')),0)

    def test_arm_rejects_observation_without_any_controlled_target(self):
        self.guard.enabled=False;self.guard.choices=[]
        with self.assertRaisesRegex(RuntimeError,'没有已接管'):self.guard.arm(True)
        self.assertFalse(self.guard.enabled)

    def test_full_continuation_only_goes_to_explicit_target(self):
        self.guard.choices.append({'id':'u','name':'other'})
        self.guard.data['threads']['u']={'selected':True,'state':'paused','turn_id':'turn1','windows':['primary','secondary']}
        self.guard.data['resume_target']='t'
        self.pause();self.set_windows(0,0);self.guard._evaluate()
        self.assertEqual([v['threadId'] for v in self.actions('turn/start')],['t'])

    def test_invalid_resume_target_does_not_mutate_saved_policy(self):
        self.guard.enabled=False;before=copy.deepcopy(self.guard.data)
        with self.assertRaises(ValueError):self.guard.configure('pro',20,True,resume_target='not-controlled')
        self.assertEqual(self.guard.data,before)

    def test_disable_or_deselect_cancels_an_idle_continuation(self):
        self.rpc.thread['turns'][-1]['status']='completed';self.guard._evaluate()
        self.guard.select('t',False);self.guard.select('t',True);self.set_windows(0,0);self.guard._evaluate()
        self.assertFalse(self.actions('turn/start'))

    def test_prepare_creates_an_owned_target_without_sending_a_model_turn(self):
        self.guard.enabled=False
        original=self.rpc.call
        def call(method,params=None):
            if method=='thread/start':return {'thread':{'id':'prepared'}}
            return original(method,params)
        self.rpc.call=call
        result=self.guard.prepare_chat(self.tmp.name)
        self.assertEqual(result['threadId'],'prepared');self.assertTrue(self.guard.data['threads']['prepared']['prepared'])
        self.assertFalse(self.actions('turn/start'))


if __name__ == '__main__':unittest.main()
