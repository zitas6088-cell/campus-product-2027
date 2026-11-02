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

    def test_sf_reads_all_pages_and_filters_company_field(self):
        from job_radar.adapters.official_board import sf_tech_listings
        a={'id':2252,'positionName':'产品经理（顺丰科技）','orgSourceName':'顺丰科技','postDuty':'产品设计','jobRequirement':'2027届'}
        b=dict(a,id=2417,positionName='产品经理（顺丰同城科技）',orgSourceName='顺丰本部')
        pages=[{'pageNum':1,'pages':2,'total':2,'list':[b]}, {'pageNum':2,'pages':2,'total':2,'list':[a]}]
        with patch('job_radar.adapters.official_board.get_json',side_effect=pages):
            jobs,total=sf_tech_listings()
        self.assertEqual(total,2)
        self.assertEqual([j['id'] for j in jobs],[2252])
        with patch('job_radar.adapters.official_board.get_json',side_effect=[pages[0],dict(pages[1],list=[b])]):
            with self.assertRaisesRegex(RuntimeError,'重复'):sf_tech_listings()
        with patch('job_radar.adapters.official_board.get_json',return_value=dict(pages[0],pages=1)):
            with self.assertRaisesRegex(RuntimeError,'完整'):sf_tech_listings()

    def test_scope_change_archives_history_and_does_not_reset_same_scope(self):
        from job_radar.sync import count_scope_peak
        state={'peak_count':19,'last_count':0,'alert':'旧混合统计量归零'}
        self.assertEqual(count_scope_peak(state,0,'campus-v1','已核实独立校园入口'),0)
        self.assertEqual(state['count_scope_history'][0]['peak_count'],19)
        self.assertEqual(state['count_scope_history'][0]['alert'],'旧混合统计量归零')
        state['peak_count']=20
        self.assertEqual(count_scope_peak(state,0,'campus-v1','同一范围'),20)
        self.assertEqual(len(state['count_scope_history']),1)
        with self.assertRaisesRegex(RuntimeError,'核实说明'):count_scope_peak(state,0,'v2','')

    def test_closed_position_is_not_a_new_opportunity(self):
        link={'text':'2027届产品经理','href':'https://careers.example.com/jobs/123'}
        self.assertIsNone(record_from_link('公司',link,'当前职位已停止招聘'))


if __name__=='__main__':unittest.main()
