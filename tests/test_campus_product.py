import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from job_radar.campus_product import classify, identity, is_product, load_config, select_pending
from job_radar.adapters.campus_watch import pages, pair
from scripts import campus_watch
from job_radar.normalize import make_dedup_key


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.job = dict(company_name='京东', source_id='watch-jd', title='2027届AI产品经理',
            official_url='https://campus.jd.com/#/jobs?reqId=1', location='北京', extra={'identifier':'1'})

    def test_confirmed(self):
        self.assertEqual(classify(self.job)[0], 'confirmed')

    def test_all_roles_eligible_and_product_roles_highlighted(self):
        for title in ['2027届产品运营','2027届产品工程师','2027届产品设计师','2027届算法工程师']:
            self.assertEqual(classify(dict(self.job,title=title))[0], 'confirmed')
            self.assertEqual(is_product(dict(self.job,title=title)), '产品' in title)
        self.assertFalse(is_product(dict(self.job,title='算法工程师',jd_text='合作产品经理')))

    def test_unrelated_2027_is_not_evidence(self):
        j=dict(self.job,title='产品经理',publish_time='2027-01-01',jd_text='2027年业务目标增长')
        self.assertEqual(classify(j)[0], 'pending')

    def test_other_cycle_not_assumed(self):
        self.assertEqual(classify(dict(self.job,title='2026届产品经理'))[0], 'pending')

    def test_graduation_window(self):
        j=dict(self.job,title='产品经理',jd_text='2026年10月1日至2027年9月30日期间毕业')
        self.assertEqual(classify(j)[0], 'confirmed')

    def test_application_window_not_graduation(self):
        j=dict(self.job,title='产品经理',jd_text='报名时间2026年10月1日至2027年9月30日')
        self.assertEqual(classify(j)[0], 'pending')

    def test_structured_graduation_year(self):
        j=dict(self.job,title='产品经理',extra={'graduation_year':'2027'})
        self.assertEqual(classify(j)[0], 'confirmed')

    def test_no_intern_or_social_or_expired(self):
        self.assertEqual(classify(dict(self.job,title='2027届产品经理实习生'))[0], 'internship')
        self.assertEqual(classify(dict(self.job,title='2027届资深产品经理'))[0], 'excluded')
        self.assertEqual(classify(dict(self.job,deadline='2025-01-01'))[0], 'excluded')

    def test_no_unknown_company_or_unconfigured_source(self):
        self.assertEqual(classify(dict(self.job,company_name='外包公司'))[0], 'excluded')
        self.assertEqual(classify(dict(self.job,source_id='cn-tencent'))[0], 'excluded')

    def test_verified_cycle_expires(self):
        cfg=load_config()
        cfg['verified_source_cycles']={'watch-jd':{'year':2027,'evidence_url':'https://campus.jd.com/','valid_until':'2026-10-01'}}
        self.assertEqual(classify(dict(self.job,title='产品经理'),cfg,today=dt.date(2026,10,31))[0], 'pending')

    def test_failed_notification_not_lost_next_day(self):
        j=dict(self.job,first_seen='2026-01-01')
        self.assertEqual(select_pending([j],{'delivered':{}}),[j])
        self.assertFalse(select_pending([j],{'delivered':{identity(j):{}}}))

    def test_title_change_same_identifier_does_not_resend(self):
        self.assertEqual(identity(self.job),identity(dict(self.job,title='2027届产品经理（北京）')))

    def test_same_title_different_official_ids_are_distinct(self):
        self.assertNotEqual(make_dedup_key('京东','产品经理','北京','101'),
                            make_dedup_key('京东','产品经理','北京','102'))
        self.assertEqual(make_dedup_key('京东','产品经理','北京','101'),
                         make_dedup_key('京东','AI产品经理','上海','101'))


class PaginationTests(unittest.TestCase):
    def test_byte_fetches_all_campus_categories(self):
        from job_radar.adapters.campus_watch import byte
        with patch('job_radar.adapters.campus_watch.pages',return_value=[]) as fetch:
            byte('')
        body=fetch.call_args.args[1](1)
        self.assertEqual(body['keyword'],'')
        self.assertEqual(body['job_category_id_list'],[])
        self.assertEqual(body['recruitment_id_list'],['201'])

    def test_null_list_is_an_empty_last_page(self):
        self.assertEqual(pair({'data':{'list':None}},'data','list','total'),([],None))

    def test_overlapping_pages_do_not_require_unique_count_to_equal_raw_total(self):
        responses=[{'list':[{'id':1},{'id':2}]},{'list':[{'id':2},{'id':3}]}]
        with patch('job_radar.adapters.campus_watch.post_json',side_effect=responses),patch('time.sleep'):
            self.assertEqual(len(pages('u',lambda p:{},lambda d:(d['list'],4),'r')),3)

    def test_server_page_size_smaller_than_request(self):
        responses=[{'data':{'list':[{'id':1}], 'total':2}}, {'data':{'list':[{'id':2}],'total':2}}]
        with patch('job_radar.adapters.campus_watch.post_json',side_effect=responses), patch('time.sleep'):
            rows=pages('u',lambda p:{},lambda d:(d['data']['list'],d['data']['total']),'r')
        self.assertEqual(len(rows),2)

    def test_http_200_business_failure_is_failure(self):
        with patch('job_radar.adapters.campus_watch.post_json',return_value={'success':False}):
            with self.assertRaises(RuntimeError):
                pages('u',lambda p:{},lambda d:([],None),'r')

    def test_repeated_page_not_successful_snapshot(self):
        d={'success':True,'list':[{'id':1}]}
        with patch('job_radar.adapters.campus_watch.post_json',return_value=d),patch('time.sleep'):
            with self.assertRaises(RuntimeError):
                pages('u',lambda p:{},lambda d:(d['list'],2),'r')


class DeliveryTests(unittest.TestCase):
    def test_feishu_signature_payload_and_fresh_timestamp(self):
        class Response:
            status=200
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self): return b'{"code":0}'
        env={'NOTIFY_CHANNEL':'feishu','FEISHU_WEBHOOK_URL':'https://example.com/test',
             'FEISHU_SIGN_SECRET':'test-sign-secret'}
        with patch.dict('os.environ',env),patch('time.time',return_value=1599360473),patch('urllib.request.urlopen',return_value=Response()) as send:
            campus_watch.deliver('2027 test')
        body=json.loads(send.call_args.args[0].data)
        self.assertEqual(body['timestamp'],'1599360473')
        self.assertEqual(body['sign'],'IguO2D+OUoxoId7q5yiQoWCADK2fpcXqxir8wy4Nbnc=')
        self.assertEqual(body['content']['text'],'2027 test')
        self.assertNotIn('test-sign-secret',json.dumps(body))

    def test_product_notifications_go_first_and_are_separate(self):
        def job(title): return {'title':title}
        batches=list(campus_watch.notification_batches([job('算法工程师'),job('产品运营'),job('产品经理')]))
        self.assertIn('⭐',batches[0][0])
        self.assertEqual([j['title'] for j in batches[0][1]],['产品运营','产品经理'])
        self.assertEqual([j['title'] for j in batches[1][1]],['算法工程师'])

    def test_scope_expansion_baselines_once_and_keeps_product_retries(self):
        cfg=load_config()
        def job(id,title):
            return dict(company_name='字节跳动',source_id='watch-byte',title=title,
                identifier=id,official_url='https://jobs.bytedance.com/'+id,
                extra={'graduation_year':'2027'})
        old=job('old','算法工程师')
        product=job('product','产品经理')
        state={'sources_initialized':['watch-byte'],'delivered':{'previous':{'sent_at':'original'}}}
        campus_watch.expansion_baseline([old,product],state,set(),cfg,'now')
        self.assertNotIn(identity(old),state['delivered'])
        campus_watch.expansion_baseline([old,product],state,{'watch-byte'},cfg,'now')
        self.assertIn(identity(old),state['delivered'])
        self.assertNotIn(identity(product),state['delivered'])
        new=job('new','算法工程师')
        campus_watch.expansion_baseline([old,product,new],state,{'watch-byte'},cfg,'later')
        self.assertEqual(select_pending([old,product,new],state),[product,new])
        self.assertEqual(state['delivered']['previous'],{'sent_at':'original'})

    def test_business_error_cannot_mark_sent(self):
        class Response:
            status=200
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self): return b'{"code":19002}'
        with patch.dict('os.environ',{'NOTIFY_CHANNEL':'feishu','FEISHU_WEBHOOK_URL':'https://example.com/test'}),patch('urllib.request.urlopen',return_value=Response()):
            with self.assertRaises(RuntimeError): campus_watch.deliver('test')

    def test_credentials_not_in_exception(self):
        with patch.dict('os.environ',{'NOTIFY_CHANNEL':'feishu','FEISHU_WEBHOOK_URL':'https://example.com/secret'}),patch('urllib.request.urlopen',side_effect=ValueError('secret')):
            with self.assertRaises(RuntimeError) as result: campus_watch.deliver('test')
            self.assertNotIn('secret',str(result.exception))

    def test_first_run_baselines_and_dry_run_has_no_state(self):
        job=dict(company_name='京东',source_id='watch-jd',title='2027届产品经理',identifier='1',official_url='https://campus.jd.com/')
        health={'sources':[{'source_id':'watch-jd','last_count':1}]}
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp)
            (data/'jobs.json').write_text(json.dumps([job]),encoding='utf-8')
            (data/'health_report.json').write_text(json.dumps(health),encoding='utf-8')
            with patch.object(campus_watch,'DATA',data),patch.object(campus_watch,'STATE',data/'state.json'),patch.object(campus_watch,'deliver') as send:
                with patch('sys.argv',['watch','--no-sync','--dry-run']): campus_watch.main()
                self.assertFalse((data/'state.json').exists())
                with patch('sys.argv',['watch','--no-sync']): campus_watch.main()
                send.assert_not_called()
                state=json.loads((data/'state.json').read_text(encoding='utf-8'))
                self.assertIn(identity(job),state['delivered'])

    def test_failed_delivery_stays_pending_and_success_is_not_resent(self):
        job=dict(company_name='京东',source_id='watch-jd',title='2027届产品经理',identifier='new',official_url='https://campus.jd.com/new',first_seen='2026-01-01')
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp)
            (data/'jobs.json').write_text(json.dumps([job]),encoding='utf-8')
            (data/'health_report.json').write_text(json.dumps({'sources':[{'source_id':'watch-jd','last_count':1}]}),encoding='utf-8')
            state=data/'state.json'
            state.write_text(json.dumps({'version':1,'sources_initialized':['watch-jd'],'delivered':{}}),encoding='utf-8')
            with patch.object(campus_watch,'DATA',data),patch.object(campus_watch,'STATE',state),patch('sys.argv',['watch','--no-sync']):
                with patch.object(campus_watch,'deliver',side_effect=RuntimeError('offline')):
                    with self.assertRaises(RuntimeError): campus_watch.main()
                self.assertNotIn(identity(job),json.loads(state.read_text(encoding='utf-8'))['delivered'])
                with patch.object(campus_watch,'deliver') as send:
                    campus_watch.main()
                    send.assert_called_once()
                self.assertIn(identity(job),json.loads(state.read_text(encoding='utf-8'))['delivered'])
                with patch.object(campus_watch,'deliver') as send:
                    campus_watch.main()
                    send.assert_not_called()

    def test_all_sources_failed_never_initializes_state(self):
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp)
            with patch.object(campus_watch,'DATA',data),patch.object(campus_watch,'STATE',data/'state.json'),patch('sys.argv',['watch','--no-sync']):
                with self.assertRaises(RuntimeError): campus_watch.main()
                self.assertFalse((data/'state.json').exists())


if __name__=='__main__': unittest.main()
