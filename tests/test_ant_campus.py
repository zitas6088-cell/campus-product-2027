import unittest
from unittest.mock import patch

from job_radar.adapters import ant_campus
from job_radar.campus_product import classify


class AntCampusTests(unittest.TestCase):
    def row(self, ident=1, **extra):
        return dict(id=ident,name='AI产品经理',batchType='graduate',
                    batchName='蚂蚁集团2027届应届生招聘',batchId=26070900089909,
                    description='负责产品规划',requirement='本科及以上',
                    workLocations=['杭州'],**extra)

    def test_observed_request_and_complete_pagination(self):
        responses=[dict(success=True,totalCount=2,currentPage=1,pageSize=1,content=[self.row(1)]),
                   dict(success=True,totalCount=2,currentPage=2,pageSize=1,content=[self.row(2)])]
        with patch.object(ant_campus,'post_json',side_effect=responses) as send:
            self.assertEqual(len(ant_campus.search('产品')),2)
        body=send.call_args_list[0].args[1]
        self.assertEqual(body['language'],'zh')
        self.assertEqual(body['batchIds'],[])

    def test_internship_and_other_year_cannot_inherit_2027(self):
        current=self.row()
        intern=dict(self.row(2),batchType='trainee',batchName='2027届实习招聘')
        other=dict(self.row(3),batchName='蚂蚁集团2026届应届生招聘')
        with patch.object(ant_campus,'search',side_effect=[[current,intern,other],[current]]):
            jobs=ant_campus.fetch()
        self.assertEqual(len(jobs),1)
        job=jobs[0]
        self.assertEqual(job.official_url,'https://talent.antgroup.com/campus-position?positionId=1')
        self.assertEqual(classify(dict(company_name=job.company_name,source_id='watch-sheet-005',
                         title=job.title,official_url=job.official_url,extra=job.raw))[0],'confirmed')

    def test_business_failure_null_and_repeated_pages_fail_closed(self):
        for payload in [dict(success=False,totalCount=0,content=None),
                        dict(success=True,totalCount=2,currentPage=1,pageSize=10,content=None)]:
            with patch.object(ant_campus,'post_json',return_value=payload):
                with self.assertRaises(RuntimeError):ant_campus.search('产品')
        responses=[dict(success=True,totalCount=2,currentPage=1,pageSize=1,content=[self.row()]),
                   dict(success=True,totalCount=2,currentPage=2,pageSize=1,content=[self.row()])]
        with patch.object(ant_campus,'post_json',side_effect=responses):
            with self.assertRaisesRegex(RuntimeError,'重复'):ant_campus.search('产品')

    def test_missing_product_requirements_is_not_healthy(self):
        with patch.object(ant_campus,'search',return_value=[dict(self.row(),requirement='')]):
            with self.assertRaisesRegex(RuntimeError,'职责或要求'):ant_campus.fetch()

    def test_explicit_zero_is_valid(self):
        with patch.object(ant_campus,'post_json',return_value=dict(success=True,totalCount=0,content=None)):
            self.assertEqual(ant_campus.search('产品'),[])
