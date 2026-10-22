import json
import queue
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
from datetime import datetime, timezone
from ailink.personal_usage import PersonalUsage
from ailink.quota import QuotaGuard


def event(thread,total):
    return {"threadId":thread,"turnId":"turn","tokenUsage":{"total":{"totalTokens":total}}}


class PersonalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.usage=PersonalUsage(self.tmp.name)
        self.now=time.time()

    def total(self,profile='me',plan='plus',key='primary',now=None):
        return self.usage.snapshot(profile,now)["plans"][plan][key]["total"]

    def test_existing_thread_first_snapshot_is_not_personal_history(self):
        self.assertEqual(self.usage.observe('me','plus','account',event('t',9000)),0)
        self.assertEqual(self.total(),0)
        self.usage.observe('me','plus','account',event('t',9100));self.assertEqual(self.total(),100)

    def test_new_owned_thread_counts_tokens_and_ignores_replays(self):
        self.usage.prime('account','new',empty=True)
        for total in [100,100,90,150]:self.usage.observe('me','plus','account',event('new',total))
        self.assertEqual(self.total(),150)

    def test_profile_plan_and_rolling_windows_are_independent(self):
        for thread,profile,plan,at in [('a','me','plus',self.now-18010),('b','me','pro',self.now),('c','other','plus',self.now)]:
            self.usage.prime('x',thread,True);self.usage.observe(profile,plan,'x',event(thread,50),at)
        self.assertEqual(self.total(),0);self.assertEqual(self.total(key='secondary'),50)
        self.assertEqual(self.total(plan='pro'),50);self.assertEqual(self.total(profile='other'),50)

    def test_percentage_or_account_usage_is_never_accepted(self):
        for bad in ({'usedPercent':90},{'tokenUsage':{'total':{'totalTokens':True}}},{'tokenUsage':[]},[]):
            self.assertEqual(self.usage.observe('me','plus','x',bad),0)
        self.assertEqual(self.total(),0)

    def fixture(self):
        path=Path(self.tmp.name)/'personal.jsonl'
        rows=[{'type':'session_meta','payload':{'id':'t'}}]
        for i,total in enumerate([100,200,250]):
            rows.append({'timestamp':datetime.fromtimestamp(self.now-120+i*60,timezone.utc).isoformat(),'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'total_tokens':total}},'rate_limits':{'primary':{'used_percent':99}}}})
        path.write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf-8');return path

    def test_manual_import_is_idempotent_across_accounts_and_profiles(self):
        path=self.fixture()
        self.assertEqual(self.usage.import_file(path,'me','plus','account1'),250)
        self.assertEqual(self.usage.import_file(path,'me','plus','account2'),0)
        self.assertEqual(self.usage.import_file(path,'other','plus','account2'),0)
        self.assertEqual(self.total(),250)
        self.assertEqual(self.total('other'),0)
        reloaded=PersonalUsage(self.tmp.name);self.assertEqual(reloaded.import_file(path,'me','plus','new'),0)

    def test_live_and_imported_intervals_do_not_double_count(self):
        self.usage.prime('a','t',True);self.usage.observe('me','plus','a',event('t',100),self.now-120)
        self.assertEqual(self.usage.import_file(self.fixture(),'me','plus','a'),150)
        self.usage.observe('me','plus','a',event('t',300));self.assertEqual(self.total(),300)

    def test_fork_does_not_import_inherited_initial_total(self):
        p=self.fixture();rows=[json.loads(l) for l in p.read_text().splitlines()];rows[0]['payload']['forked_from_id']='parent';p.write_text('\n'.join(json.dumps(r) for r in rows))
        self.assertEqual(self.usage.import_file(p,'me','plus','x'),150)

    def test_corrupt_ledger_is_marked_incomplete(self):
        self.usage.path.write_text('{bad')
        self.assertTrue(PersonalUsage(self.tmp.name).incomplete)


class PersonalGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.guard=QuotaGuard(self.tmp.name,Mock());self.guard.plan='plus';self.guard.enabled=True
        self.guard.updated=time.time();self.guard.windows=[{'key':'primary','remaining':90},{'key':'secondary','remaining':90}]
        self.guard.rpc=Mock();self.guard.rpc.usage_events=queue.Queue();self.guard.rpc.usage_overflow=False
        self.guard.rpc.call.return_value={'thread':{'turns':[{'id':'turn','status':'inProgress'}]}}
        self.guard.data['caps']['plus']={'enabled':True,'primary':100,'secondary':1000}
        self.guard.data['account_fingerprint']='scope'
        self.guard.choices=[{'id':'mine','name':'My task'},{'id':'someone','name':'Other task'}]
        self.guard.data['threads']={'mine':{'state':'watching','selected':True,'personal_profile':'me'},'someone':{'state':'watching','selected':True}}

    def consume(self,total=100):
        self.guard.personal.prime('scope','mine',True)
        self.guard.rpc.usage_events.put(event('mine',total));self.guard._drain_usage()

    def test_personal_cap_only_stops_attributed_guarded_threads(self):
        self.consume();self.guard._evaluate()
        calls=[c for c in self.guard.rpc.call.call_args_list if c.args[0]=='turn/interrupt']
        self.assertEqual(len(calls),1);self.assertEqual(calls[0].args[1]['threadId'],'mine')
        self.assertEqual(self.guard.data['threads']['mine']['stop_reason'],'personal_cap')

    def test_other_people_events_are_excluded_even_if_account_is_shared(self):
        self.guard.rpc.usage_events.put(event('someone',999999));self.guard._drain_usage()
        self.assertFalse(self.guard.cap_reached())

    def test_personal_limit_can_stop_when_global_quota_read_is_unavailable(self):
        self.consume();self.guard.windows=[];self.guard.updated=0;self.guard._evaluate()
        self.assertEqual(self.guard.data['threads']['mine']['state'],'interrupt_pending')

    def test_global_full_does_not_resume_exhausted_personal_budget(self):
        self.consume();r=self.guard.data['threads']['mine'];r.update(state='paused',turn_id='turn',windows=['primary','secondary'])
        self.guard.rpc.call.return_value={'thread':{'turns':[{'id':'turn','status':'interrupted'}]}}
        self.guard.windows=[{'key':'primary','remaining':100},{'key':'secondary','remaining':100}];self.guard.data['auto_resume']=True
        self.guard._evaluate();self.assertFalse(any(c.args[0]=='turn/start' for c in self.guard.rpc.call.call_args_list))

    def test_profiles_and_limits_persist_without_account_percentage_estimation(self):
        self.guard.enabled=False;self.guard.profile(name='Alice')
        cap={'enabled':True,'primary':1234,'secondary':5678}
        self.guard.configure('pro',10,False,'codex',cap)
        saved=QuotaGuard(self.tmp.name,Mock());self.assertEqual(saved.data['caps']['pro'],cap)
        self.assertEqual(saved.data['profiles'][saved.data['active_profile']]['name'],'Alice')
        with self.assertRaises(ValueError):self.guard.validate_caps({'enabled':True,'primary':True,'secondary':1})


if __name__=='__main__':unittest.main()
