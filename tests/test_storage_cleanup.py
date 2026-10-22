import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch
import zipfile
from ailink.storage_cleanup import StorageCleanup
from ailink.retention_content import safe_file

class LocalRPC:
    def __init__(self,home):self.home=home;self.rows={};self.calls=[];self.locked=set();self.fail=None
    def connect(self):pass
    def close(self):pass
    def call(self,method,params=None):
        self.calls.append((method,copy.deepcopy(params)))
        if method==self.fail:raise TimeoutError('uncertain '+method)
        if method=='thread/list':return {'data':[copy.deepcopy(r) for r in self.rows.values() if r['archived']==params['archived']],'nextCursor':None}
        identity=params['threadId'];row=self.rows[identity]
        if method=='thread/resume' and identity in self.locked:raise RuntimeError('thread already has an active writer')
        if method in ('thread/read','thread/resume'):return {'thread':copy.deepcopy(row)}
        if method in ('thread/archive','thread/unarchive'):
            archive=method=='thread/archive';target=self.home/('archived_sessions' if archive else 'sessions')/(identity+'.jsonl');target.parent.mkdir(exist_ok=True)
            shutil.move(row['path'],target);row.update(path=str(target),archived=archive);return {'thread':copy.deepcopy(row)}
        if method=='thread/delete':Path(row['path']).unlink();self.rows.pop(identity);return {}
        raise ValueError(method)

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.base=Path(self.temp.name)
        self.home=self.base/'codex';self.workspace=self.base/'project';self.workspace.mkdir();(self.home/'sessions').mkdir(parents=True)
        self.quota=Mock();self.quota.data={'threads':{}};self.quota.lock=threading.RLock();self.rpc=LocalRPC(self.home)
        self.storage=StorageCleanup(self.base/'state',self.quota,lambda:self.rpc,home=self.home)
        self.policy={'source':'local','workspace':'','olderDays':30,'from':None,'to':None,'includeLogs':False,'keep':{'files':True,'final':True,'decisions':True,'code':True}}
        self.add('a');self.add('b')
    def add(self,identity,**kwargs):
        path=self.home/'sessions'/(identity+'.jsonl')
        output=self.workspace/'report.md';output.write_text('valuable output',encoding='utf-8')
        records=[{'type':'session_meta','payload':{'id':identity,'cwd':str(self.workspace)}},
                 {'type':'response_item','payload':{'type':'message','role':'assistant','phase':'final_answer','content':[{'type':'output_text','text':f'结论：已完成验证。\n\n[报告]({output})\n\n```python\nprint(42)\n```'}]}}]
        path.write_text('\n'.join(json.dumps(r) for r in records)+'\n',encoding='utf-8')
        self.rpc.rows[identity]={'id':identity,'name':'示例 '+identity,'cwd':str(self.workspace),'updatedAt':time.time()-60*86400,'path':str(path),'archived':False,
                                 'status':{'type':'idle'},'turns':[{'id':'turn-'+identity,'status':'completed'}],**kwargs}
    def preview(self,ids=None,policy=None):self.storage.scan('local');return self.storage.preview(ids or ['a'],policy or self.policy)
    def archive(self,ids=None):plan=self.preview(ids);return self.storage.execute(plan['token'])
    def calls(self,method):return [p for m,p in self.rpc.calls if m==method]
    def batch(self):return list(self.storage.batches.values())[-1]
    def original_backup(self,entry=None):
        batch=self.batch();entry=entry or batch['items'][0]
        return self.storage.root/'batches'/batch['id']/entry['folder']/'original'

    def test_scan_is_read_only_and_does_not_start_generation(self):
        self.storage.scan('local');snapshot=self.storage.snapshot();self.assertEqual(len(snapshot['items']),2)
        self.assertEqual(snapshot['items'][0]['classification'],{'final':1,'decisions':1,'code':1,'files':1})
        self.assertTrue(all(m=='thread/list' for m,_ in self.rpc.calls));self.assertFalse(self.storage.root.exists())

    def test_recent_fixed_guarded_and_related_targets_are_excluded(self):
        self.rpc.rows['a']['updatedAt']=time.time();self.quota.data['threads']['b']={'selected':True}
        self.add('fixed',isPinned=True);self.add('child',forkedFromId='fixed')
        self.storage.scan('local');self.assertTrue(all(r['protected'] for r in self.storage.snapshot()['items']))
        with self.assertRaises(ValueError):self.storage.preview(['b'],self.policy)

    def test_workspace_time_and_invalid_keep_are_checked_in_backend(self):
        self.storage.scan('local')
        for change in [{'workspace':'another'},{'from':time.time()},{'to':1},{'keep':{'files':False,'final':False,'decisions':False,'code':False}},{'olderDays':True}]:
            with self.subTest(change=change),self.assertRaises(ValueError):self.storage.preview(['a'],{**self.policy,**change})
        self.assertFalse(self.calls('thread/archive'))

    def test_preview_is_one_use_and_expired_plan_does_not_mutate(self):
        plan=self.preview();self.storage.plans[plan['token']]['created']-=601
        with self.assertRaises(ValueError):self.storage.execute(plan['token'])
        with self.assertRaises(ValueError):self.storage.execute(plan['token'])
        self.assertFalse(self.calls('thread/archive'))

    def test_files_only_preserves_referenced_deliverable_and_project_source(self):
        policy={**self.policy,'keep':{'files':True,'final':False,'decisions':False,'code':False}}
        plan=self.preview(policy=policy);self.storage.execute(plan['token']);entry=self.batch()['items'][0]
        folder=self.original_backup().parent/'preserved'
        self.assertFalse((folder/'final.md').exists());self.assertTrue(list((folder/'files').glob('*')))
        self.assertEqual((self.workspace/'report.md').read_text(),'valuable output');self.assertEqual(entry['state'],'archived')

    def test_archive_restores_original_thread_with_verified_backup(self):
        self.archive();batch=self.batch();entry=batch['items'][0];backup=self.original_backup()
        self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(),entry['sha256'])
        self.storage.restore(batch['id'],'a');self.assertEqual(entry['state'],'restored');self.assertTrue((self.home/'sessions/a.jsonl').exists())
        self.assertEqual(self.calls('thread/unarchive'),[{'threadId':'a'}])

    def test_active_writer_failure_does_not_affect_other_selected_target(self):
        self.rpc.locked.add('a');self.archive(['a','b']);entries=self.batch()['items']
        self.assertEqual([e['state'] for e in entries],['failed','archived']);self.assertIn('active writer',entries[0]['error'])
        self.assertEqual(self.calls('thread/archive'),[{'threadId':'b'}])

    def test_file_change_after_preview_is_not_archived(self):
        plan=self.preview();path=Path(self.rpc.rows['a']['path']);path.write_text(path.read_text()+'{}\n')
        self.storage.execute(plan['token']);self.assertFalse(self.calls('thread/archive'));self.assertEqual(self.batch()['items'][0]['state'],'failed')

    def test_new_guard_selection_after_preview_prevents_archival(self):
        plan=self.preview();self.quota.data['threads']['a']={'selected':True};self.storage.execute(plan['token'])
        self.assertFalse(self.calls('thread/archive'))

    def test_resume_metadata_append_is_backed_up_and_allowed(self):
        original=self.rpc.call
        def call(method,params=None):
            result=original(method,params)
            if method=='thread/resume':
                with Path(self.rpc.rows[params['threadId']]['path']).open('a') as stream:stream.write(json.dumps({'type':'event_msg','payload':{'type':'thread_settings_applied'}})+'\n')
            return result
        self.rpc.call=call;self.archive();self.assertEqual(self.batch()['items'][0]['state'],'archived');self.assertIn(b'thread_settings_applied',self.original_backup().read_bytes())

    def test_new_content_during_resume_is_never_accepted_as_metadata(self):
        original=self.rpc.call
        def call(method,params=None):
            result=original(method,params)
            if method=='thread/resume':
                with Path(self.rpc.rows[params['threadId']]['path']).open('a') as stream:stream.write(json.dumps({'type':'event_msg','payload':{'type':'user_message','message':'new task'}})+'\n')
            return result
        self.rpc.call=call;self.archive();self.assertFalse(self.calls('thread/archive'));self.assertEqual(self.batch()['items'][0]['state'],'failed')

    def test_new_child_after_preview_prevents_cascading_archive(self):
        plan=self.preview();self.add('child',forkedFromId='a');self.storage.execute(plan['token']);self.assertFalse(self.calls('thread/archive'))

    def test_changed_archived_record_is_not_overwritten_by_restore(self):
        self.archive();path=Path(self.rpc.rows['a']['path']);path.write_text('changed')
        with self.assertRaises(RuntimeError):self.storage.restore(self.batch()['id'],'a')
        self.assertEqual(path.read_text(),'changed');self.assertFalse(self.calls('thread/unarchive'))

    def test_permanent_purge_keeps_preserved_output_and_removes_original_backup(self):
        self.archive();entry=self.batch()['items'][0];backup=self.original_backup();preserved=backup.parent/'preserved/final.md'
        plan=self.storage.purge_preview(self.batch()['id']);result=self.storage.purge(plan['token'])
        self.assertEqual(result['completed'],1);self.assertEqual(entry['state'],'purged');self.assertTrue(preserved.exists());self.assertFalse(backup.exists())
        self.assertNotIn('a',self.rpc.rows);self.assertTrue((self.workspace/'report.md').exists())

    def test_purge_requires_preview_and_rechecks_archive_content(self):
        self.archive();plan=self.storage.purge_preview(self.batch()['id']);Path(self.rpc.rows['a']['path']).write_text('changed')
        result=self.storage.purge(plan['token']);self.assertEqual(result['failed'],1);self.assertTrue(self.original_backup().exists());self.assertFalse(self.calls('thread/delete'))
        with self.assertRaises(ValueError):self.storage.purge(plan['token'])

    def test_unknown_delete_result_keeps_backup_and_is_not_replayed(self):
        self.archive();plan=self.storage.purge_preview(self.batch()['id']);self.rpc.fail='thread/delete';result=self.storage.purge(plan['token'])
        self.assertEqual(result['failed'],1);self.assertTrue(self.original_backup().exists());self.assertEqual(self.batch()['items'][0]['state'],'delete_pending')
        with self.assertRaises(ValueError):self.storage.purge_preview(self.batch()['id'])

    def test_log_quarantine_and_restore_do_not_overwrite_new_log(self):
        log=self.home/'log/old.log';log.parent.mkdir();log.write_text('old diagnostic');os.utime(log,(time.time()-60*86400,)*2)
        self.storage.scan('local');identity=next(k for k in self.storage.catalog if k.startswith('log:'))
        plan=self.storage.preview([identity],{**self.policy,'includeLogs':True});self.storage.execute(plan['token']);self.assertFalse(log.exists())
        log.write_text('new log')
        with self.assertRaises(RuntimeError):self.storage.restore(self.batch()['id'],identity)
        self.assertEqual(log.read_text(),'new log')

    def test_important_pin_survives_restart_and_cannot_be_previewed(self):
        self.storage.scan('local');self.storage.pin('a',True)
        restored=StorageCleanup(self.base/'state',self.quota,lambda:self.rpc,home=self.home);restored.scan('local')
        with self.assertRaises(ValueError):restored.preview(['a'],self.policy)

    def test_paths_outside_codex_and_hardlinks_are_protected(self):
        self.rpc.rows['a']['path']=str(self.workspace/'report.md');os.link(self.home/'sessions/b.jsonl',self.home/'sessions/hardlink')
        self.storage.scan('local');self.assertTrue(all(r['protected'] for r in self.storage.snapshot()['items']))
        with self.assertRaises(ValueError):safe_file(self.workspace/'report.md',self.home)

    def cloud_file(self,name='cloud.json'):
        conversation={'id':'sample-cloud-1','title':'示例云端成果','update_time':time.time()-60*86400,'current_node':'final','mapping':{
            'root':{'parent':None,'children':['final'],'message':None},'final':{'parent':'root','children':[],'message':{'author':{'role':'assistant'},'content':{'parts':['结论：验证完成。\n\n[文件](sandbox:/mnt/data/report.pdf)']}}}}}
        path=self.base/name;path.write_text(json.dumps([conversation]),encoding='utf-8');return path,conversation

    def test_cloud_import_deduplicates_per_account_group_and_does_not_touch_source(self):
        path,_=self.cloud_file();original=path.read_bytes()
        self.storage.import_cloud(path,'个人');self.storage.import_cloud(path,'个人');self.assertEqual(len(self.storage.cloud.records),1)
        self.storage.import_cloud(path,'工作');self.assertEqual(len(self.storage.cloud.records),2);self.assertEqual(original,path.read_bytes())

    def test_cloud_plan_preserves_content_but_never_claims_cloud_deletion(self):
        path,_=self.cloud_file();self.storage.import_cloud(path,'个人');identity=next(iter(self.storage.catalog));plan=self.storage.preview([identity],{**self.policy,'source':'cloud'})
        result=self.storage.execute(plan['token']);self.assertFalse(result['cloud_modified']);self.assertEqual(self.batch()['items'][0]['state'],'cloud_pending')
        self.assertEqual(len(self.storage.cloud.records),1);self.assertFalse(self.rpc.calls)
        with self.assertRaises(ValueError):self.storage.purge_preview(self.batch()['id'])

    def test_malicious_zip_and_ambiguous_branch_imports_are_atomic(self):
        path,conversation=self.cloud_file();self.storage.import_cloud(path,'个人');before=copy.deepcopy(self.storage.cloud.records)
        archive=self.base/'bad.zip'
        with zipfile.ZipFile(archive,'w') as stream:stream.writestr('../conversations.json',json.dumps([conversation]))
        with self.assertRaises(ValueError):self.storage.import_cloud(archive,'个人')
        conversation.pop('current_node');conversation['mapping']['root']['children']=['final','alternate'];path.write_text(json.dumps([conversation]))
        with self.assertRaises(ValueError):self.storage.import_cloud(path,'个人')
        self.assertEqual(self.storage.cloud.records,before)

    def test_zip_numbered_export_import_and_selected_branch_only(self):
        path,conversation=self.cloud_file();conversation['mapping']['alternate']={'parent':'root','message':{'author':{'role':'assistant'},'content':{'parts':['Do not retain alternate branch']}}}
        archive=self.base/'export.zip'
        with zipfile.ZipFile(archive,'w') as stream:stream.writestr('conversations-001.json',json.dumps([conversation]))
        self.storage.import_cloud(archive,'个人');content=next(iter(self.storage.cloud.records.values()))['content']
        self.assertNotIn('alternate',content['final']);self.assertIn('验证',content['final'])

    def test_corrupt_cleanup_preferences_do_not_crash_other_workspaces_or_overwrite_data(self):
        self.storage.root.mkdir(parents=True);self.storage.config_path.write_text('invalid')
        restored=StorageCleanup(self.base/'state',self.quota,lambda:self.rpc,home=self.home)
        self.assertIn('只读模式',restored.snapshot()['error']);restored.scan('local')
        plan=restored.preview(['a'],self.policy)
        with self.assertRaises(RuntimeError):restored.execute(plan['token'])
        self.assertEqual(self.storage.config_path.read_text(),'invalid');self.assertFalse(self.calls('thread/archive'))

    def test_corrupt_cloud_index_does_not_crash_local_cleanup_or_get_overwritten(self):
        cloud_root=self.storage.root/'cloud';cloud_root.mkdir(parents=True);index=cloud_root/'index.json';index.write_text('invalid')
        restored=StorageCleanup(self.base/'state',self.quota,lambda:self.rpc,home=self.home);restored.scan('local')
        self.assertEqual(len(restored.snapshot()['items']),2)
        path,_=self.cloud_file()
        with self.assertRaises(RuntimeError):restored.import_cloud(path,'个人')
        self.assertEqual(index.read_text(),'invalid')

    def test_removing_manual_pin_preserves_automatic_recent_and_fixed_protection(self):
        self.rpc.rows['a']['updatedAt']=time.time();self.rpc.rows['b']['isPinned']=True;self.storage.pinned=['a','b'];self.storage.scan('local')
        for identity in ('a','b'):
            self.storage.pin(identity,False)
            self.assertTrue(self.storage.catalog[identity]['protected'])
            with self.assertRaises(ValueError):self.storage.preview([identity],{**self.policy,'olderDays':0})

    def test_valid_json_with_wrong_config_or_batch_shape_is_read_only(self):
        self.storage.root.mkdir(parents=True);self.storage.config_path.write_text('[]')
        restored=StorageCleanup(self.base/'state',self.quota,lambda:self.rpc,home=self.home)
        self.assertIsNotNone(restored.snapshot()['error'])
        self.storage.config_path.write_text('{"pinned":[]}');batch=self.storage.root/'batches'/('c'*32);batch.mkdir(parents=True);(batch/'manifest.json').write_text('[]')
        restored=StorageCleanup(self.base/'state',self.quota,lambda:self.rpc,home=self.home)
        self.assertIsNotNone(restored.snapshot()['error']);self.assertEqual((batch/'manifest.json').read_text(),'[]')

    def test_non_object_jsonl_record_is_protected_instead_of_crashing_scan(self):
        Path(self.rpc.rows['a']['path']).write_text('[]\n')
        self.storage.scan('local');self.assertTrue(self.storage.catalog['a']['protected']);self.assertFalse(self.storage.catalog['b']['protected'])

if __name__=='__main__':unittest.main()
