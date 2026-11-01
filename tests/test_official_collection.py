from types import SimpleNamespace
import asyncio
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
        page.mouse.wheel=AsyncMock()
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

    async def test_title_card_pagination_is_detected_when_navigation_links_stay_the_same(self):
        first={'links':[],'titles':['算法工程师'],'headings':[],'text':'算法工程师'}
        second=dict(first,titles=['数据工程师'],text='数据工程师')
        browser,page,_,spec=self.setup_board(first)
        page.evaluate.side_effect=[first,second]
        next_button=locator(count=1);next_button.click=AsyncMock()
        next_button.is_enabled.side_effect=[True,False]
        original=page.locator.side_effect
        page.locator.side_effect=lambda selector:next_button if selector.startswith('button[aria-label=') else original(selector)
        result=await collect_company(browser,spec)
        self.assertTrue(result['ok'])
        self.assertEqual(result['listings_seen'],2)
        next_button.click.assert_awaited_once()

    async def assert_detail_popup(self,use_api=False,extra_title=False):
        snapshot={'links':[],'titles':[] if use_api or extra_title else ['2027届产品经理'],
                  'extra_titles':['2027届产品经理'] if extra_title else [],'headings':[],'text':'2027届产品经理'}
        browser,page,context,spec=self.setup_board(snapshot)
        if use_api:
            events={}
            page.on.side_effect=lambda event,callback:events.update({event:callback})
            response=Mock();response.url='https://example.com/api/jobs';response.headers={'content-type':'application/json'}
            response.json=AsyncMock(return_value={'data':[{'id':'42','title':'2027届产品经理','description':'岗位职责：产品规划'}]})
            original_goto=page.goto.side_effect
            async def goto_with_api(url,**kwargs):
                result=await original_goto(url,**kwargs)
                events['response'](response)
                await asyncio.sleep(0)
                return result
            page.goto.side_effect=goto_with_api
        popup=Mock();popup.url='about:blank';popup.close=AsyncMock()
        async def ready(*args,**kwargs):popup.url='https://example.com/jobs/42'
        popup.wait_for_url=AsyncMock(side_effect=ready)
        popup.wait_for_load_state=AsyncMock()
        popup.get_by_text=Mock(return_value=locator())
        popup.get_by_text.return_value.wait_for=AsyncMock()
        popup.locator=Mock(return_value=locator(text='2027届产品经理\n职位描述：产品规划'))
        candidate=locator()
        async def open_detail():context.pages.append(popup)
        candidate.click=AsyncMock(side_effect=open_detail)
        page.get_by_text=Mock(side_effect=lambda value,**kwargs:locator(fields=[candidate]) if value=='2027届产品经理' else locator())
        result=await collect_company(browser,spec)
        self.assertEqual(len(result['jobs']),1)
        popup.wait_for_url.assert_awaited_once()
        popup.close.assert_awaited_once()
        if extra_title:
            # A real card already proves this is a listing. Chasing the campus
            # navigation here can replace the page under a stale snapshot.
            self.assertTrue(all(isinstance(call.args[0],str) for call in page.get_by_text.call_args_list))

    async def test_new_detail_tab_waits_for_navigation_out_of_about_blank(self):
        await self.assert_detail_popup()

    async def test_public_api_title_can_find_a_real_card_without_known_css_classes(self):
        await self.assert_detail_popup(use_api=True)

    async def test_product_title_does_not_reenter_campus_navigation_before_opening_detail(self):
        await self.assert_detail_popup(extra_title=True)

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

    async def test_search_button_is_used_when_enter_is_not_a_site_action(self):
        snapshot={'links':[],'titles':[],'headings':[],'text':'暂无相关职位'}
        browser,page,_,spec=self.setup_board(snapshot)
        field=locator();field.fill=AsyncMock();field.press=AsyncMock()
        field.get_attribute=AsyncMock(return_value='输入职位名称关键字搜索')
        original=page.locator.side_effect
        page.locator.side_effect=lambda selector:locator(fields=[field]) if selector=='input[placeholder]' else original(selector)
        page.get_by_placeholder=Mock(return_value=locator(fields=[field]))
        button=locator();button.click=AsyncMock()
        page.get_by_role.side_effect=lambda role,**kwargs:locator(fields=[button]) if role=='button' else locator()
        self.assertTrue((await collect_company(browser,spec))['ok'])
        self.assertEqual(button.click.await_count,2)
        field.press.assert_not_awaited()

    async def test_shared_portal_jsonld_does_not_relabel_another_employer(self):
        snapshot={'links':[],'titles':[],'headings':[],'text':'暂无相关职位'}
        content='<script type="application/ld+json">{"@type":"JobPosting","title":"2027届产品经理","hiringOrganization":{"name":"其他公司"},"url":"https://example.com/job/1","description":"岗位职责：产品规划"}</script>'
        browser,_,_,spec=self.setup_board(snapshot,content=content)
        spec['require_company_terms']=['目标公司']
        result=await collect_company(browser,spec)
        self.assertEqual(result['jobs'],[])

    async def test_other_employers_jsonld_cannot_prove_target_company_is_healthy(self):
        snapshot={'links':[],'titles':[],'headings':[],'text':'共享招聘门户'}
        content='<script type="application/ld+json">{"@type":"JobPosting","title":"算法工程师","hiringOrganization":{"name":"其他公司"},"url":"https://example.com/job/1","description":"岗位职责"}</script>'
        browser,_,_,spec=self.setup_board(snapshot,content=content)
        spec['require_company_terms']=['目标公司']
        with self.assertRaisesRegex(RuntimeError,'未验证到可读职位列表'):
            await collect_company(browser,spec)


if __name__=='__main__':unittest.main()
