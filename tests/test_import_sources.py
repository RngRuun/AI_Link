import json
import os
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from ailink.import_paths import collect_jsonl
from ailink.personal_usage import PersonalUsage
from ailink.personal_source import PersonalSource
from ailink.quota import QuotaGuard
from ailink.team_usage import TeamUsage


def records(thread, total, at=None, fork=False):
    return '\n'.join(json.dumps(row) for row in [
        {'type':'session_meta','payload':{'id':thread,**({'forked_from_id':'parent'} if fork else {})}},
        {'timestamp':datetime.fromtimestamp(at or time.time()-10,timezone.utc).isoformat(),'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'total_tokens':total}}}},
    ])+'\n'


class ImportFixtureMixin:
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.logs=self.root/'sessions';self.logs.mkdir()

    def file(self, name, text):
        path=self.logs/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8');return path

class RecursiveImportTests(ImportFixtureMixin, unittest.TestCase):
    def test_year_month_day_roots_and_overlap_have_one_copy(self):
        a=self.file('2026/01/02/a.jsonl',records('a',10));b=self.file('2026/02/03/b.JSONL',records('b',20))
        self.file('2026/01/notes.txt','ignored')
        found=collect_jsonl([str(self.logs/'2026'),str(a.parent),str(a)])
        self.assertEqual(found['files'],2);self.assertEqual(set(found['paths']),{a,b})
        self.assertEqual(collect_jsonl([str(self.logs/'2026/02')])['paths'],[b])

    def test_folder_accepts_more_than_twenty_records(self):
        for i in range(27):self.file(f'2026/01/01/{i}.jsonl',records(str(i),i+1))
        self.assertEqual(collect_jsonl([str(self.logs)])['files'],27)

    def test_empty_invalid_oversize_and_count_bounds_are_not_silent(self):
        for paths in [[],[''],['missing.jsonl'],[str(self.file('x.txt','x'))]]:
            with self.assertRaises(ValueError):collect_jsonl(paths)
        self.file('x.jsonl','1234')
        with patch('ailink.import_paths.MAX_FILE_BYTES',3),self.assertRaises(ValueError):collect_jsonl([str(self.logs)])
        self.file('y.jsonl','12')
        with patch('ailink.import_paths.MAX_FILES',1),self.assertRaises(ValueError):collect_jsonl([str(self.logs)])
        with patch('ailink.import_paths.MAX_TOTAL_BYTES',4),self.assertRaises(ValueError):collect_jsonl([str(self.logs)])

    def test_link_is_not_followed(self):
        target=self.file('real.jsonl',records('a',10));link=self.logs/'linked.jsonl'
        try:link.symlink_to(target)
        except OSError:
            if os.name!='nt':self.skipTest('OS does not allow links on this filesystem')
            folder=self.logs/'real-folder';folder.mkdir();target.rename(folder/'real.jsonl');link=self.logs/'linked-folder'
            self.assertTrue(folder.resolve().is_relative_to(self.root.resolve()))
            subprocess.run(['cmd','/c','mklink','/J',str(link),str(folder)],capture_output=True,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
        found=collect_jsonl([str(self.logs)]);self.assertEqual(found['files'],1);self.assertEqual(found['skipped_links'],1)
        with self.assertRaises(ValueError):collect_jsonl([str(link)])

    def test_recursive_team_import_preserves_owner_conflicts_and_dedup(self):
        team=TeamUsage(self.root/'state');a=team.member('甲')['memberId'];b=team.member('乙')['memberId']
        self.file('2026/01/01/a.jsonl',records('a',100));self.file('2026/02/01/b.jsonl',records('b',200))
        result=team.import_files([str(self.logs)],a,'plus');self.assertEqual((result['files'],result['tokens']),(2,300))
        self.assertEqual(team.import_files([str(self.logs)],a,'plus')['tokens'],0)
        before=team.path.read_bytes();self.file('2026/03/01/c.jsonl',records('c',20))
        with self.assertRaises(ValueError):team.import_files([str(self.logs)],b,'plus')
        self.assertEqual(team.path.read_bytes(),before)

    def test_personal_batch_is_atomic_on_late_encoding_failure(self):
        usage=PersonalUsage(self.root/'state');self.file('a.jsonl',records('a',100));bad=self.file('z.jsonl','placeholder');bad.write_bytes(b'\xff')
        usage.save();before=usage.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'本批未写入'):usage.import_files([str(self.logs)],'me','plus','local')
        self.assertEqual(usage.path.read_bytes(),before);self.assertEqual(usage.snapshot('me')['plans']['plus']['primary']['total'],0)


class AutomaticSourceTests(ImportFixtureMixin, unittest.TestCase):
    # Only automatic-specific cases inherit shared fixture helpers.
    def setUp(self):
        super().setUp();self.guard=QuotaGuard(self.root/'state',Mock())
        self.source=PersonalSource(self.root/'state',self.guard.source_context,self.guard.source_credit,self.guard.source_flush,root=self.logs)

    def test_changed_files_are_automatic_and_restart_does_not_duplicate(self):
        path=self.file('2026/10/22/rollout.jsonl',records('auto',100));self.source.scan()
        self.assertEqual(self.guard.personal.snapshot('me')['plans']['plus']['primary']['total'],100)
        self.source.scan();self.assertEqual(self.source.snapshot()['changed'],0)
        path.write_text(records('auto',160),encoding='utf-8');self.source.scan()
        self.assertEqual(self.source.snapshot()['tokens'],60)
        reloaded=QuotaGuard(self.root/'state',Mock());again=PersonalSource(self.root/'state',reloaded.source_context,reloaded.source_credit,reloaded.source_flush,root=self.logs);again.scan()
        self.assertEqual(reloaded.personal.snapshot('me')['plans']['plus']['primary']['total'],160)

    def test_recent_timestamp_plan_and_live_event_dedup(self):
        self.guard.personal.prime('local-session','same',empty=True)
        self.guard.personal.observe('me','plus','local-session',{'threadId':'same','tokenUsage':{'total':{'totalTokens':80}}})
        self.file('a.jsonl',records('same',100));self.file('old.jsonl',records('old',900,time.time()-20*86400))
        self.source.scan();self.assertEqual(self.guard.personal.snapshot('me')['plans']['plus']['primary']['total'],100)
        self.guard.data['mode']='pro';self.file('b.jsonl',records('new',50));self.source.scan()
        self.assertEqual(self.guard.personal.snapshot('me')['plans']['pro']['primary']['total'],50)

    def test_partial_line_is_recovered_after_append_and_pause_stops_reads(self):
        path=self.file('partial.jsonl',records('t',40));self.source.configure(False);self.source.scan()
        self.assertEqual(self.guard.personal.snapshot('me')['plans']['plus']['primary']['total'],0)
        self.source.configure(True);text=records('t',80);path.write_text(text[:-8],encoding='utf-8');self.source.scan()
        path.write_text(text,encoding='utf-8');self.source.scan()
        self.assertEqual(self.guard.personal.snapshot('me')['plans']['plus']['primary']['total'],80)

    def test_failed_automatic_observation_is_not_treated_as_zero_for_caps(self):
        self.guard.source=self.source;self.guard.data['caps']['plus']['enabled']=True
        self.source.state['error']='unreadable directory'
        self.assertTrue(self.guard.cap_reached());self.assertTrue(self.guard.snapshot()['personal']['incomplete'])
        self.source.state['error']=None
        self.assertFalse(self.guard.cap_reached());self.assertFalse(self.guard.snapshot()['personal']['incomplete'])
