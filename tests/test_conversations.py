import copy
import tempfile
import time
import unittest
from unittest.mock import Mock
from ailink.quota import QuotaGuard,CONTINUE
from ailink.codex_rpc import local_endpoint


class ExistingRPC:
    def __init__(self):
        self.calls=[]
        self.threads={k:{'id':k,'name':name,'cwd':'C:/fixture/project','status':{'type':'idle'},
                         'turns':[{'id':'turn-'+k,'status':status,'items':[]}]} for k,name,status in
                      [('a','已有任务甲','interrupted'),('b','已有任务乙','failed'),('done','已完成任务','completed'),('locked','原窗口运行中','inProgress')]}
    def call(self,method,params=None):
        self.calls.append((method,copy.deepcopy(params or {})))
        if method=='thread/list':return {'data':list(copy.deepcopy(self.threads).values()),'nextCursor':None}
        if method in ('thread/read','thread/resume'):
            if params['threadId']=='locked':raise RuntimeError('thread already has an active writer')
            return {'thread':copy.deepcopy(self.threads[params['threadId']])}
        if method=='turn/start':
            turn={'id':'new-'+params['threadId'],'status':'inProgress'}
            self.threads[params['threadId']]['turns'].append(turn);self.threads[params['threadId']]['status']={'type':'active'}
            return {'turn':turn}
        return {}


class ExistingConversationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.path=temp.name
        self.guard=QuotaGuard(temp.name,Mock());self.rpc=ExistingRPC();self.guard.rpc=self.rpc
        self.guard.plan='plus';self.guard.data['auto_resume']=True
        self.guard.windows=[{'key':'primary','remaining':100},{'key':'secondary','remaining':100}];self.guard.updated=time.time()
    def actions(self,method):return [p for m,p in self.rpc.calls if m==method]
    def attach(self,ids):self.guard.browse_conversations();return self.guard.attach_conversations(ids)

    def test_browse_is_metadata_only_and_adoption_keeps_existing_ids(self):
        result=self.attach(['a','b'])
        self.assertEqual(result['attached'],['a','b']);self.assertEqual([c['id'] for c in self.guard.choices],['a','b'])
        self.assertFalse(self.actions('thread/start'));self.assertFalse(self.actions('turn/start'))
        self.assertTrue(all(set(p)=={'threadId'} for p in self.actions('thread/resume')))
        self.assertEqual(self.guard.catalog['items'][0]['name'],'已有任务甲')

    def test_active_writer_is_visible_failure_and_never_claimed_as_controlled(self):
        result=self.attach(['a','locked'])
        self.assertEqual(result['attached'],['a']);self.assertIn('另一个 Codex 进程',result['failed'][0]['reason'])
        self.assertNotIn('locked',self.guard.data['threads']);self.assertNotIn('locked',{c['id'] for c in self.guard.choices})

    def test_multiple_existing_targets_resume_with_independent_prompts_and_skip_done(self):
        self.attach(['a','b','done'])
        self.guard.configure_continuation('a',True,'')
        self.guard.configure_continuation('b',True,'先继续修复登录问题，再执行测试。')
        self.guard.configure_continuation('done',True,'此内容不应发送给已完成目标')
        self.guard.arm(True);self.guard._evaluate()
        sent=self.actions('turn/start');self.assertEqual([p['threadId'] for p in sent],['a','b'])
        self.assertEqual(sent[0]['input'][0]['text'],CONTINUE)
        self.assertEqual(sent[1]['input'][0]['text'],'先继续修复登录问题，再执行测试。')

    def test_per_target_opt_out_does_not_affect_another_target(self):
        self.attach(['a','b']);self.guard.configure_continuation('a',False,'')
        self.guard.arm(True);self.assertEqual([p['threadId'] for p in self.actions('turn/start')],['b'])

    def test_unknown_control_result_does_not_block_another_selected_target(self):
        self.attach(['a','b']);original=self.rpc.call
        def call(method,params=None):
            if method=='turn/start' and params['threadId']=='a':raise TimeoutError('lost reply for A')
            return original(method,params)
        self.rpc.call=call
        with self.assertRaises(TimeoutError):self.guard.arm(True)
        self.assertEqual(self.guard.data['threads']['a']['state'],'resume_pending')
        self.assertIn('lost reply',self.guard.data['threads']['a']['control_error'])
        self.assertEqual([p['threadId'] for p in self.actions('turn/start')],['b'])
        self.guard._evaluate()
        self.assertEqual([p['threadId'] for p in self.actions('turn/start')],['b'])

    def test_reconnecting_adopted_history_preserves_original_execution_policy(self):
        self.attach(['a']);self.guard.rpc=None;self.guard.rpc_factory=lambda **_:self.rpc
        self.rpc.connect=Mock();self.rpc.close=Mock()
        original=self.rpc.call
        def call(method,params=None):
            if method=='account/read':return {'account':{'type':'chatgpt','email':None,'planType':'plus'}}
            if method=='account/rateLimits/read':return {'rateLimits':{'planType':'plus','primary':{'usedPercent':0},'secondary':{'usedPercent':0}}}
            if method=='thread/loaded/list':return {'data':['a']}
            return original(method,params)
        self.rpc.call=call;self.guard.connect();self.addCleanup(self.guard.close)
        self.assertEqual(self.actions('thread/resume')[-1],{'threadId':'a'})

    def test_unknown_interrupt_does_not_prevent_protection_of_another_target(self):
        self.attach(['a','b'])
        for key in ('a','b'):
            self.rpc.threads[key]['turns'][-1]['status']='inProgress'
            self.rpc.threads[key]['status']={'type':'active'}
            self.guard.runtime[key]=copy.deepcopy(self.rpc.threads[key]['turns'][-1])
            self.guard.data['threads'][key]['state']='watching'
        self.guard.windows[0]['remaining']=15;original=self.rpc.call
        def call(method,params=None):
            if method=='turn/interrupt' and params['threadId']=='a':raise TimeoutError('lost interruption for A')
            return original(method,params)
        self.rpc.call=call
        with self.assertRaises(TimeoutError):self.guard.arm(True)
        self.assertEqual([p['threadId'] for p in self.actions('turn/interrupt')],['b'])
        self.assertTrue(all(self.guard.data['threads'][k]['state']=='interrupt_pending' for k in ('a','b')))
        self.assertIn('lost interruption',self.guard.data['threads']['a']['control_error'])

    def test_rules_persist_and_invalid_rule_does_not_modify_them(self):
        self.attach(['a']);self.guard.configure_continuation('a',True,'继续剩余检查\n保持原有约束')
        saved=copy.deepcopy(self.guard.data)
        for enabled,prompt in [('true','x'),(True,'x'*4001),(True,None)]:
            with self.assertRaises(ValueError):self.guard.configure_continuation('a',enabled,prompt)
            self.assertEqual(self.guard.data,saved)
        restored=QuotaGuard(self.path,Mock())
        self.assertEqual(restored.continuation('a',restored.data['threads']['a'])['prompt'],'继续剩余检查\n保持原有约束')

    def test_unlisted_target_cannot_be_adopted(self):
        self.guard.browse_conversations();count=len(self.rpc.calls)
        with self.assertRaises(ValueError):self.guard.attach_conversations(['forged'])
        self.assertEqual(len(self.rpc.calls),count)

    def test_unknown_result_and_running_guard_prevent_rule_changes(self):
        self.attach(['a']);self.guard.data['threads']['a']['state']='resume_pending'
        with self.assertRaises(RuntimeError):self.guard.configure_continuation('a',True,'changed')
        self.guard.enabled=True
        with self.assertRaises(RuntimeError):self.guard.attach_conversations(['a'])
        with self.assertRaises(RuntimeError):self.guard.configure_continuation('a',True,'changed')

    def test_corrupt_continuation_is_disabled(self):
        self.attach(['a']);self.guard.data['threads']['a']['continuation']={'enabled':'yes'}
        self.assertFalse(self.guard.continuation('a',self.guard.data['threads']['a'])['enabled'])

    def test_shared_endpoint_is_literal_loopback_without_credentials_or_redirect_paths(self):
        self.assertEqual(local_endpoint('ws://127.0.0.1:4500'),'ws://127.0.0.1:4500')
        self.assertEqual(local_endpoint('ws://[::1]:4500'),'ws://[::1]:4500')
        for value in ['ws://example.com:4500','ws://0.0.0.0:4500','ws://127.0.0.1','ws://127.0.0.1:4500/other','ws://user:secret@127.0.0.1:4500','ws://127.0.0.1:4500?token=secret','wss://127.0.0.1:4500',True]:
            with self.subTest(value=value),self.assertRaises(ValueError):local_endpoint(value)

if __name__=='__main__':unittest.main()
