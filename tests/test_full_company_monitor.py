import csv
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from job_radar.campus_product import identity, is_product, load_config, select_pending
from job_radar.adapters import official_board
from scripts import campus_watch

ROOT = Path(__file__).resolve().parents[1]


class FullCompanyMonitorTests(unittest.TestCase):
    def test_every_sheet_company_has_one_scheduled_source(self):
        watch = json.loads((ROOT/'config/company_watchlist.json').read_text(encoding='utf-8'))['companies']
        cfg = load_config()
        self.assertEqual(len(watch), 163)
        self.assertEqual(set(item['sheet_row'] for item in watch), set(range(2,165)))
        self.assertEqual(len({item['company'] for item in watch}), 163)
        self.assertEqual(set(cfg['companies']), {item['company'] for item in watch})
        self.assertEqual(set(cfg['source_ids']), {item['source_id'] for item in watch})
        with (ROOT/'config/sources.csv').open(encoding='utf-8') as f:
            sources = {item['source_id']:item for item in csv.DictReader(f) if item.get('source_id')}
        self.assertTrue(all(sources[item['source_id']]['status']=='active' for item in watch))
        self.assertEqual(cfg['check_times'], ['09:00','15:00'])
        self.assertTrue(cfg['notify_product_only'])

    def test_nonproduct_never_enters_notifications(self):
        def job(title, ident):
            return dict(company_name='京东',source_id='watch-jd', title=title, identifier=ident,
                        official_url='https://campus.jd.com/#/jobs?reqId='+ident,
                        extra={'graduation_year':'2027'})
        jobs=[job('AI产品经理','1'),job('算法工程师','2'),job('Production Engineer','3'),
              job('Product Analyst','4'),job('数据分析师','5')]
        self.assertEqual(select_pending(jobs, {'delivered':{}}), [jobs[0],jobs[3]])
        self.assertFalse(is_product({'title':'数据分析师','jd_text':'与产品经理沟通需求'}))
        batches=list(campus_watch.notification_batches(jobs))
        self.assertEqual([j for _,batch in batches for j in batch],[jobs[0],jobs[3]])

    def test_failed_source_cannot_be_misreported_as_no_jobs(self):
        with patch.object(official_board,'_snapshot',{'x':{'ok':False,'error':'登录墙'}}):
            with self.assertRaisesRegex(RuntimeError,'登录墙'): official_board.fetch('x')
        with patch.object(official_board,'_snapshot',{'x':{'ok':True,'jobs':[]}}):
            self.assertEqual(official_board.fetch('x'),[])

    def test_real_zero_snapshot_initializes_before_first_new_job(self):
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp);state=data/'state.json'
            (data/'jobs.json').write_text('[]',encoding='utf-8')
            health={'sources':[{'source_id':'watch-jd','last_count':0}]}
            (data/'health_report.json').write_text(json.dumps(health),encoding='utf-8')
            with patch.object(campus_watch,'DATA',data),patch.object(campus_watch,'STATE',state),patch('sys.argv',['watch','--no-sync']),patch.object(campus_watch,'deliver') as deliver:
                campus_watch.main()
                self.assertIn('watch-jd',json.loads(state.read_text())['sources_initialized'])
                job=dict(company_name='京东',source_id='watch-jd',title='2027届产品经理',identifier='new',official_url='https://campus.jd.com/new')
                (data/'jobs.json').write_text(json.dumps([job]),encoding='utf-8')
                campus_watch.main()
                deliver.assert_called_once()

    def test_real_detail_link_required_and_batch_dates_not_invented(self):
        self.assertIsNone(official_board.record_from_link('腾讯',{'text':'产品经理','href':'https://join.qq.com/'}))
        job=official_board.record_from_link('腾讯',{'text':'产品经理\n深圳','href':'https://join.qq.com/position/123','context':'2027届校招'})
        self.assertEqual(job.title,'产品经理')
        self.assertEqual(official_board.specific_batch(['2027年公司新闻'],'https://example.com/campus'),'')
        self.assertEqual(official_board.specific_batch(['2027届校园招聘'],'https://example.com/campus'),'2027届校园招聘')
        self.assertEqual(official_board.specific_batch(['2027届校园招聘'],'https://example.com/social'),'')

    def test_category_objects_do_not_become_jobs(self):
        self.assertEqual(list(official_board.json_jobs({'title':'产品类','id':'1','children':[]})),[])
        jobs=list(official_board.json_jobs({'data':[{'title':'AI产品经理','id':'j1','description':'2027届'}]}))
        self.assertEqual(len(jobs),1)


if __name__=='__main__': unittest.main()
