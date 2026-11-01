from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,Mock
from job_radar.adapters.official_board import collect_company


def locator(*,count=0,text='',fields=None):
    value=Mock()
    value.first=value.last=value
    value.count=AsyncMock(return_value=count)
    value.all=AsyncMock(return_value=fields or [])
    value.inner_text=AsyncMock(return_value=text)
    value.is_visible=AsyncMock(return_value=True)
    value.is_enabled=AsyncMock(return_value=True)
    value.get_attribute=AsyncMock(return_value=None)
    return value


class PublicBoardFlowTests(unittest.IsolatedAsyncioTestCase):
    def setup_board(self,snapshot,*,status=200,content='<html></html>'):
        page=Mock();page.url='https://example.com/campus/jobs'
        async def goto(url,**kwargs):
            page.url=url
            return SimpleNamespace(status=status)
        page.goto=AsyncMock(side_effect=goto)
        page.wait_for_load_state=AsyncMock()
        page.wait_for_timeout=AsyncMock()
        page.evaluate=AsyncMock(return_value=snapshot)
        page.content=AsyncMock(return_value=content)
        body=locator(text='2027届 产品经理\n岗位职责：负责产品规划\n任职要求：2027届毕业生')
        page.locator=Mock(side_effect=lambda selector:body if selector=='body' else locator())
        page.get_by_text=Mock(return_value=locator())
        page.get_by_role=Mock(return_value=locator())
        context=Mock();context.new_page=AsyncMock(return_value=page);context.close=AsyncMock()
        context.pages=[page]
        browser=Mock();browser.new_context=AsyncMock(return_value=context)
        spec={'source_id':'test','company':'公司','urls':['https://example.com/campus/jobs']}
        return browser,page,context,spec

    async def test_complete_list_does_not_chase_other_recruitment_navigation(self):
        snapshot={'links':[{'text':'2027届产品经理','href':'https://example.com/jobs/42','context':'2027届'},
                           *[{'text':'Campus info','href':f'https://example.com/campus/faq/{i}','context':''} for i in range(25)]],
                  'titles':[], 'headings':[], 'text':'2027届产品经理'}
        browser,page,context,spec=self.setup_board(snapshot)
        result=await collect_company(browser,spec)
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['jobs']),1)
        self.assertEqual(page.goto.await_count,2) # list and one actual detail
        context.close.assert_awaited_once()

    async def test_unreadable_shell_or_access_error_is_not_a_zero_snapshot(self):
        snapshot={'links':[],'titles':[],'headings':[],'text':'欢迎加入'}
        for status in (200,403):
            browser,_,context,spec=self.setup_board(snapshot,status=status)
            with self.assertRaisesRegex(RuntimeError,'未验证到可读职位列表'):
                await collect_company(browser,spec)
            context.close.assert_awaited_once()

    async def test_explicit_no_jobs_is_a_valid_empty_snapshot(self):
        snapshot={'links':[],'titles':[],'headings':[],'text':'暂无相关职位'}
        browser,_,_,spec=self.setup_board(snapshot)
        result=await collect_company(browser,spec)
        self.assertTrue(result['ok'])
        self.assertEqual(result['jobs'],[])

    async def test_product_card_without_readable_detail_is_not_empty_success(self):
        snapshot={'links':[],'titles':['2027届产品经理'],'headings':[],'text':'2027届产品经理'}
        browser,_,_,spec=self.setup_board(snapshot)
        with self.assertRaisesRegex(RuntimeError,'产品标题尚未取得可验证岗位详情'):
            await collect_company(browser,spec)

    async def test_generic_nonjob_heading_cannot_prove_a_readable_board(self):
        snapshot={'links':[],'titles':[],'extra_titles':['我们的工程师团队'],'headings':[],'text':'欢迎加入'}
        browser,_,_,spec=self.setup_board(snapshot)
        with self.assertRaisesRegex(RuntimeError,'未验证到可读职位列表'):
            await collect_company(browser,spec)

    async def test_shared_portal_jsonld_does_not_relabel_another_employer(self):
        snapshot={'links':[],'titles':[],'headings':[],'text':'暂无相关职位'}
        content='<script type="application/ld+json">{"@type":"JobPosting","title":"2027届产品经理","hiringOrganization":{"name":"其他公司"},"url":"https://example.com/job/1","description":"岗位职责：产品规划"}</script>'
        browser,_,_,spec=self.setup_board(snapshot,content=content)
        spec['require_company_terms']=['目标公司']
        result=await collect_company(browser,spec)
        self.assertEqual(result['jobs'],[])


if __name__=='__main__':unittest.main()
