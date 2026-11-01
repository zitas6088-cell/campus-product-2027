import unittest
from unittest.mock import patch
from job_radar.adapters.employer_apis import workday,huawei
from job_radar.adapters.official_board import record_from_link


class OfficialApiTests(unittest.TestCase):
    def test_workday_reads_details_and_ignores_non_china_locations(self):
        page={'jobPostings':[{'externalPath':'/job/Shanghai/Product-Manager_1','title':'Product Manager','locationsText':'China, Shanghai'},
                             {'externalPath':'/job/London/Product-Manager_2','title':'Product Manager','locationsText':'London'}], 'total':2}
        detail={'jobPostingInfo':{'title':'Product Manager','jobDescription':'Graduating in 2027','jobReqId':'1','location':'Shanghai'}}
        with patch('job_radar.adapters.employer_apis.post_json',return_value=page),patch('job_radar.adapters.employer_apis.get_json',return_value=detail) as get:
            jobs=workday('example.myworkdayjobs.com|example|Careers')
        self.assertEqual(len(jobs),1)
        self.assertEqual(get.call_count,1)
        self.assertEqual(jobs[0].raw['identifier'],'1')
        self.assertIn('2027',jobs[0].jd_text)

    def test_workday_incomplete_list_or_missing_detail_is_error(self):
        probe={'jobPostings':[], 'total':1}
        with patch('job_radar.adapters.employer_apis.post_json',return_value=probe):
            with self.assertRaisesRegex(RuntimeError,'提前结束'):workday('h|t|s')
        page={'jobPostings':[{'externalPath':'/job/Shanghai/1','locationsText':'China'}],'total':1}
        with patch('job_radar.adapters.employer_apis.post_json',return_value=page),patch('job_radar.adapters.employer_apis.get_json',return_value={}):
            with self.assertRaisesRegex(RuntimeError,'详情'):workday('h|t|s')

    def test_huawei_empty_and_broken_pagination_are_distinct(self):
        with patch('job_radar.adapters.employer_apis.get_json',return_value={'result':[],'pageVO':{'totalPages':0}}):
            self.assertEqual(huawei(''),[])
        with patch('job_radar.adapters.employer_apis.get_json',return_value={'result':[],'pageVO':{'totalPages':2}}):
            with self.assertRaisesRegex(RuntimeError,'提前结束'):huawei('')
        with patch('job_radar.adapters.employer_apis.get_json',side_effect=OSError('offline')):
            with self.assertRaises(OSError):huawei('')

    def test_closed_position_is_not_a_new_opportunity(self):
        link={'text':'2027届产品经理','href':'https://careers.example.com/jobs/123'}
        self.assertIsNone(record_from_link('公司',link,'当前职位已停止招聘'))


if __name__=='__main__':unittest.main()
