import json
import unittest
from unittest.mock import patch

from job_radar.adapters import baidu_campus
from job_radar.campus_product import classify


class BaiduCampusTests(unittest.TestCase):
    def payload(self, data):
        return '<script>window.__INITIAL_DATA__ ='+json.dumps(data)+'; neverExecute()</script>'

    def listing(self):
        return {'listData':{'recruitType':'GRADUATE','total':1,
            'listConfig':[{'recruitType':'GRADUATE','subtitle':'面向全球2027届毕业生'}],
            'graduateProjectList':[{'value':'1','label':'校招'}]}}

    def job(self, **extra):
        row=dict(postId='actual-id',name='北京-AI产品经理(J100665)',projectTypeCode='1',
                 workContent='负责产品设计',serviceCondition='本科及以上')
        row.update(extra);return row

    def test_ssr_is_data_not_executed_and_strings_are_preserved(self):
        self.assertEqual(baidu_campus.initial_data('<script>window.__INITIAL_DATA__ ={"x":undefined,"s":"undefined"}; throw new Error()</script>'),{'x':None,'s':'undefined'})
        with self.assertRaises(RuntimeError):baidu_campus.initial_data('<html>空页面</html>')

    def test_real_pagination_uses_cur_page_and_form_encoding(self):
        responses=[{'status':'ok','data':{'total':'2','pageNum':1,'list':[self.job()]}},
                   {'status':'ok','data':{'total':'2','pageNum':2,'list':[self.job(postId='second')]}}]
        with patch.object(baidu_campus,'post_form',side_effect=responses) as send:
            self.assertEqual(len(baidu_campus.listings(expected_total=2)),2)
        self.assertEqual(send.call_args.args[1]['curPage'],2)
        self.assertEqual(send.call_args.args[1]['recruitType'],'GRADUATE')

    def test_listing_failure_or_incomplete_page_is_not_zero(self):
        for response in [None,{'status':'fail','message':'need-login'},
                         {'status':'ok','data':{'total':1,'pageNum':1,'list':[]}},
                         {'status':'ok','data':{'total':1,'pageNum':2,'list':[self.job()]}}]:
            with patch.object(baidu_campus,'post_form',return_value=response):
                with self.assertRaises(RuntimeError):baidu_campus.listings()
        with patch.object(baidu_campus,'post_form',return_value={'status':'ok','data':{'total':0,'pageNum':1,'list':[]}}):
            with self.assertRaisesRegex(RuntimeError,'总数不一致'):baidu_campus.listings(expected_total=1)

    def test_same_official_detail_and_graduate_scope_are_required(self):
        detail={'detailData':{'isValid':True,'recruitType':'GRADUATE','postInfo':self.job()}}
        with patch.object(baidu_campus,'get_text',side_effect=[self.payload(self.listing()),self.payload(detail)]),patch.object(baidu_campus,'listings',return_value=[self.job()]):
            jobs=baidu_campus.fetch()
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0].official_url,'https://talent.baidu.com/jobs/detail/GRADUATE/actual-id')
        self.assertEqual(classify(dict(company_name='百度',source_id='watch-sheet-009',title=jobs[0].title,official_url=jobs[0].official_url,extra=jobs[0].raw))[0],'confirmed')
        for change in [{'recruitType':'INTERN'},{'isValid':False},{'postInfo':self.job(postId='another')}]:
            wrong={'detailData':dict(detail['detailData'],**change)}
            with patch.object(baidu_campus,'get_text',side_effect=[self.payload(self.listing()),self.payload(wrong)]),patch.object(baidu_campus,'listings',return_value=[self.job()]):
                with self.assertRaises(RuntimeError):baidu_campus.fetch()

    def test_wrong_campaign_cannot_supply_year(self):
        listing=self.listing();listing['listData']['listConfig'][0]['subtitle']='面向全球2026届毕业生'
        with patch.object(baidu_campus,'get_text',return_value=self.payload(listing)),patch.object(baidu_campus,'listings') as read:
            with self.assertRaises(RuntimeError):baidu_campus.fetch()
            read.assert_not_called()
