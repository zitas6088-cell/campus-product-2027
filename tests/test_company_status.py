import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from job_radar.company_status import company_rows, report_parts
from job_radar.campus_product import identity, load_config
from scripts import campus_watch as watch


class CompanyStatusTests(unittest.TestCase):
    def setUp(self):
        self.cfg=load_config()
        self.cfg.update(companies=['京东','腾讯'],source_ids=['watch-jd','watch-tencent'],notify_company_status=True)
        self.catalog=[dict(company='京东',source_id='watch-jd'),dict(company='腾讯',source_id='watch-tencent')]
        self.job=dict(company_name='京东',source_id='watch-jd',title='2027届AI产品经理',identifier='new',official_url='https://example.com/job/42')
        self.health={'sources':[dict(source_id='watch-jd',last_count=1),dict(source_id='watch-tencent',last_count=0)]}
        self.slot='2026-11-02T15:00+08:00'

    def test_new_none_and_missing_or_failed_source_are_distinct(self):
        rows=company_rows(self.cfg,self.catalog,self.health,[self.job])
        self.assertEqual([r['status'] for r in rows],['新增','没有新增'])
        self.assertEqual(rows[0]['jobs'],[self.job])
        for src in [None,dict(source_id='watch-tencent',last_count=4,not_run=True),
                    dict(source_id='watch-tencent',last_count=4,last_error='读取失败'),
                    dict(source_id='watch-tencent',last_count=0,skipped=True)]:
            health={'sources':self.health['sources'][:1]+([src] if src else [])}
            self.assertEqual(company_rows(self.cfg,self.catalog,health,[])[1]['status'],'异常')

    def test_first_snapshot_is_not_advertised_as_new_and_other_roles_are_absent(self):
        jobs=[dict(self.job,title='2027届算法工程师'),dict(self.job,company_name='范围外公司')]
        rows=company_rows(self.cfg,self.catalog,self.health,jobs,['watch-jd'])
        self.assertEqual(rows[0]['status'],'没有新增')
        self.assertIn('基线',rows[0]['detail'])
        self.assertFalse(rows[0]['jobs'])

    def test_every_company_is_included_and_message_parts_fit_wire_budget(self):
        cfg=dict(self.cfg,companies=['公司'+str(i) for i in range(43)],source_ids=['s'+str(i) for i in range(43)])
        rows=company_rows(cfg,[],{'sources':[]},[])
        self.assertEqual(len(rows),43)
        self.assertTrue(all(r['status']=='异常' for r in rows))
        parts=report_parts(rows,self.slot,'2026-11-02 15:10')
        for row in rows:self.assertIn(row['company']+'：异常','\n'.join(parts))
        many=[dict(company='京东',status='新增',detail='',jobs=[dict(self.job,title='2027届产品经理'+str(i),official_url='https://example.com/'+('a'*300)+str(i)) for i in range(60)])]
        parts=report_parts(many,self.slot,'2026-11-02 15:10')
        self.assertGreater(len(parts),1)
        self.assertTrue(all(len(json.dumps(p).encode())<11000 for p in parts))
        self.assertEqual(sum(p.count('https://example.com/') for p in parts),60)

    def test_no_new_still_notifies_once_per_slot_and_dry_run_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)
            with patch.object(watch,'DATA',data),patch.object(watch,'load_config',return_value=self.cfg),patch.object(watch,'deliver') as send:
                watch.send_company_status(self.health,[],[],self.slot,dry_run=True)
                send.assert_not_called()
                self.assertFalse((data/'company_summary_notify_state.json').exists())
                watch.send_company_status(self.health,[],[],self.slot)
                watch.send_company_status(self.health,[],[],self.slot)
                send.assert_called_once()
                self.assertIn('京东：没有新增',send.call_args.args[0])
                self.assertIn('腾讯：没有新增',send.call_args.args[0])

    def test_partial_summary_delivery_resumes_only_unsent_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(watch,'DATA',Path(tmp)),patch.object(watch,'load_config',return_value=self.cfg),patch.object(watch,'report_parts',return_value=['first','second']):
                with patch.object(watch,'deliver',side_effect=[None,RuntimeError('offline')]):
                    with self.assertRaises(RuntimeError):watch.send_company_status(self.health,[self.job],[],self.slot)
                with patch.object(watch,'deliver') as send:
                    watch.send_company_status(self.health,[],[],self.slot)
                    self.assertEqual(send.call_args_list[0].args,('second',))
                    send.assert_called_once()

    def test_failed_summary_keeps_slot_unfinished_without_resending_job_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp);state=data/'notify.json'
            (data/'jobs.json').write_text(json.dumps([self.job]),encoding='utf-8')
            state.write_text(json.dumps({'sources_initialized':self.cfg['source_ids'],'delivered':{}}),encoding='utf-8')
            with patch.object(watch,'DATA',data),patch.object(watch,'STATE',state),patch.object(watch,'load_config',return_value=self.cfg),patch.object(watch,'monitor_slot',return_value=self.slot),patch.object(watch.sync,'run',return_value=self.health),patch('sys.argv',['watch']),patch.dict('os.environ',{'GITHUB_EVENT_NAME':'schedule','GITHUB_STEP_SUMMARY':''}):
                with patch.object(watch,'deliver',side_effect=[None,RuntimeError('summary offline')]):
                    with self.assertRaises(RuntimeError):watch.main()
                self.assertIn(identity(self.job),json.loads(state.read_text(encoding='utf-8'))['delivered'])
                self.assertFalse((data/'monitor_slot_state.json').exists())
                with patch.object(watch,'deliver') as send:
                    watch.main();watch.main()
                    send.assert_called_once()
                    self.assertIn('京东：新增 1个岗位',send.call_args.args[0])
                    self.assertIn(self.job['title'],send.call_args.args[0])
                self.assertEqual(json.loads((data/'monitor_slot_state.json').read_text())['completed_slot'],self.slot)

    def test_all_failed_sends_abnormal_status_without_initializing_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp);state=data/'notify.json';original={'sources_initialized':[],'delivered':{'old':{'sent_at':'original'}}}
            state.write_text(json.dumps(original),encoding='utf-8');(data/'jobs.json').write_text('[]')
            with patch.object(watch,'DATA',data),patch.object(watch,'STATE',state),patch.object(watch,'load_config',return_value=self.cfg),patch.object(watch.sync,'run',return_value={'sources':[]}),patch('sys.argv',['watch']),patch.dict('os.environ',{'GITHUB_STEP_SUMMARY':''}),patch.object(watch,'deliver') as send:
                with self.assertRaises(RuntimeError):watch.main()
                self.assertIn('京东：异常',send.call_args.args[0]);self.assertIn('腾讯：异常',send.call_args.args[0])
            self.assertEqual(json.loads(state.read_text()),original)
            self.assertFalse((data/'monitor_slot_state.json').exists())
