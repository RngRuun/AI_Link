import json
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime,timezone
from pathlib import Path
from ailink.team_usage import TeamUsage


def record(thread,tokens,at=None,fork=False):
    meta={'type':'session_meta','payload':{'id':thread}}
    if fork:meta['payload']['forked_from_id']='parent'
    events=[{'timestamp':datetime.fromtimestamp((at or time.time())+i,timezone.utc).isoformat(),
             'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'total_tokens':n}}}} for i,n in enumerate(tokens)]
    return '\n'.join(json.dumps(e) for e in [meta,*events])


class TeamTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.team=TeamUsage(self.temp.name)
        self.a=self.team.member('张三')['memberId'];self.b=self.team.member('李四')['memberId']
    def tearDown(self):self.temp.cleanup()
    def test_two_people_pie_total_share_and_persistence(self):
        self.team.import_text(record('a',[100,300],time.time()-100),self.a,'plus')
        self.team.import_text(record('b',[100],time.time()-100),self.b,'plus')
        report=TeamUsage(self.temp.name).snapshot()['plans']['plus']['all']
        self.assertEqual(report['total'],400)
        self.assertEqual([m['share'] for m in report['members']],[75,25])
        self.assertEqual([m['sessions'] for m in report['members']],[1,1])
    def test_old_history_all_is_independent_of_rolling_windows_and_plan(self):
        self.team.import_text(record('old',[80],time.time()-90*86400),self.a,'pro')
        self.team.import_text(record('week',[20],time.time()-86400),self.b,'pro')
        plans=self.team.snapshot()['plans']
        self.assertEqual(plans['pro']['all']['total'],100)
        self.assertEqual(plans['pro']['secondary']['total'],20)
        self.assertEqual(plans['pro']['primary']['total'],0)
        self.assertEqual(plans['plus']['all']['total'],0)
    def test_overlapping_exports_deduplicate_and_conflicts_are_atomic(self):
        self.team.import_text(record('a',[100,300],time.time()-100),self.a,'plus')
        self.assertEqual(self.team.import_text(record('a',[100,500],time.time()-100),self.a,'plus')['tokens'],200)
        before=self.team.path.read_bytes()
        with self.assertRaises(ValueError):self.team.import_text(record('other',[30])+'\n'+record('a',[500]),self.b,'plus')
        self.assertEqual(self.team.path.read_bytes(),before)
        self.assertEqual(self.team.snapshot()['plans']['plus']['all']['total'],500)
    def test_reassign_does_not_duplicate_or_drop_usage(self):
        self.team.import_text(record('a',[50]),self.a,'plus')
        key=self.team.snapshot()['sessions'][0]['key']
        self.team.assign(key,self.b,'pro')
        state=self.team.snapshot()
        self.assertEqual(state['plans']['plus']['all']['total'],0)
        self.assertEqual(state['plans']['pro']['all']['members'][1]['tokens'],50)
    def test_fork_counter_reset_and_replay_are_not_new_tokens(self):
        result=self.team.import_text(record('fork',[1000,1050,1005,1100],time.time()-10,fork=True),self.a,'plus')
        self.assertEqual(result['tokens'],100)
        self.assertEqual(result['skipped'],1)
        self.assertEqual(self.team.import_text(record('fork',[1000,1100],time.time()-10,fork=True),self.a,'plus')['tokens'],0)
    def test_plain_chat_and_account_percentages_are_not_token_records(self):
        before=self.team.path.read_bytes()
        for text in ['ordinary conversation',json.dumps({'usedPercent':30}),record('a',[True])]:
            with self.assertRaises(ValueError):self.team.import_text(text,self.a,'plus')
        self.assertEqual(self.team.path.read_bytes(),before)
    def test_csv_formula_and_svg_markup_are_escaped(self):
        self.team.member('=1+1',self.a);self.team.member('<script>&',self.b)
        self.team.import_text(record('a',[10]),self.a,'plus');self.team.import_text(record('b',[30]),self.b,'plus')
        svg=self.team.export('plus','all','svg')['data'];ET.fromstring(svg)
        self.assertNotIn('<script>',svg);self.assertIn('&lt;script&gt;',svg)
        self.assertIn("'=1+1",self.team.export('plus','all','csv')['data'])
        self.assertNotIn('session_meta',svg)
    def test_empty_export_is_valid_and_no_invented_slices(self):
        svg=self.team.export('plus','all','svg')['data'];ET.fromstring(svg)
        self.assertEqual([e for e in ET.fromstring(svg).iter() if e.attrib.get('fill','').startswith('url(#piece-')],[]);self.assertIn('暂无用量',svg)
    def test_invalid_member_and_large_input_do_not_change_data(self):
        before=self.team.path.read_bytes()
        for name in ['','张三','x\ny','x'*25]:
            with self.assertRaises(ValueError):self.team.member(name)
        with self.assertRaises(ValueError):self.team.import_text('x'*64001,self.a,'plus')
        with self.assertRaises(ValueError):self.team.import_files([__file__],self.a,'plus')
        self.assertEqual(self.team.path.read_bytes(),before)
    def test_oversized_total_is_atomic_and_never_rounded_in_chart(self):
        before=self.team.path.read_bytes()
        text='\n'.join(record('large-'+str(i),[10**15],time.time()-10) for i in range(10))
        with self.assertRaises(ValueError):self.team.import_text(text,self.a,'plus')
        self.assertEqual(self.team.path.read_bytes(),before)
    def test_corrupt_file_is_preserved(self):
        self.team.path.write_text('{broken',encoding='utf-8');broken=TeamUsage(self.temp.name)
        self.assertTrue(broken.snapshot()['error'])
        with self.assertRaises(ValueError):broken.member('新成员')
        self.assertEqual(self.team.path.read_text(encoding='utf-8'),'{broken')
    def test_file_and_paste_deduplication(self):
        text=record('a',[100],time.time()-5);p=Path(self.temp.name)/'mine.jsonl';p.write_text(text,encoding='utf-8')
        self.assertEqual(self.team.import_files([str(p)],self.a,'plus')['tokens'],100)
        self.assertEqual(self.team.import_text(text,self.a,'plus')['tokens'],0)
    def test_future_and_timezone_free_events_are_rejected(self):
        for text in [record('future',[1],time.time()+3600),record('local',[1]).replace('+00:00','')]:
            with self.assertRaises(ValueError):self.team.import_text(text,self.a,'plus')

if __name__=='__main__':unittest.main()
