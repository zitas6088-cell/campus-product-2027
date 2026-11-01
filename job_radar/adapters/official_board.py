"""Read public employer job boards; never equate an unreadable page with zero jobs.

The optional browser dependency is only loaded for a real collection run. A batch
uses isolated employer contexts and bounded concurrency. No login, CAPTCHA
solving, certificate override or access-control bypass is attempted.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import register
from ..models import RawJob
from .jsonld import parse_jobs

ROOT = Path(__file__).resolve().parents[2]
WATCHLIST = ROOT / 'config/company_watchlist.json'
CACHE = ROOT / 'data/official_board_snapshot.json'
PRODUCT = re.compile(r'产品|\bproduct\b', re.I)
JOB_URL = re.compile(r'(?:job|position|vacanc|career|requisition|detail|post)[^\s]*[0-9]|[?&](?:job|post|position|req)(?:Id|id|ID|Code)=', re.I)
JOB_TITLE = re.compile(r'经理|工程师|架构师|专员|策划|分析师|商业分析|经营分析|管培|培训生|助理|研究员|设计师|产品设计|产品规划|产品管理|运营|manager|engineer|architect|analyst|associate|coordinator|graduate|intern|designer|specialist', re.I)
NAV = re.compile(r'^职位$|校园招聘|应届生|校招职位|招聘职位|全部职位|全部岗位|职位列表|岗位投递|招聘岗位|校招岗位|热招职位|立即投递|一键投递|查看职位|加入我们|招聘官网|campus|graduate|search jobs|view jobs|careers|job openings|current vacancies|apply to bmw', re.I)
FORBIDDEN = re.compile(r'验证码|安全验证|访问过于频繁|人机验证|Access Denied|Verify you are human|captcha', re.I)
EMPTY = re.compile(r'暂无(?:相关)?(?:职位|岗位)|没有找到相关职位|无匹配职位|no (?:matching |open |available )?(?:jobs|positions)|0 (?:jobs|positions)', re.I)
ATS = ('mokahr.com', 'zhiye.com', 'hotjob.cn', 'jobs.feishu.cn', 'myworkdayjobs.com', 'successfactors.com', 'oraclecloud.com', 'avature.net', 'smartrecruiters.com', 'eightfold.ai', 'tupu360.com')
_snapshot = None


def canonical(url):
    parts = urlsplit(str(url or '').strip())
    tracking = {'sourcetoken', 'source', 'ref', 'referralcode', 'spread', 'spm', 'from', 'timestamp', 't'}
    def query(value):
        pairs = parse_qsl(value, keep_blank_values=True)
        has_job_id = any(re.fullmatch(r'(?:job|post|position|req)(?:id|code)', k, re.I) for k,v in pairs)
        context_keys = {'ky', 'keyword', 'keywords', 'search', 'searchtext', 'pageindex', 'pagenumber', 'page', 'c', 'p', 'jc'} if has_job_id else set()
        return urlencode(sorted((k,v) for k,v in pairs
                                if k.lower() not in tracking | context_keys and not k.lower().startswith('utm_')))
    fragment = parts.fragment
    if '?' in fragment:
        route, suffix = fragment.split('?',1)
        fragment = route + ('?' + query(suffix) if query(suffix) else '')
    return urlunsplit(('https' if parts.scheme in ('http','https') else parts.scheme,
                      parts.netloc.lower(),parts.path.rstrip('/'),query(parts.query),fragment)).rstrip('/')


def own_or_linked(url, seeds):
    """Only actual links from the employer page are queued by the caller."""
    host = (urlsplit(url).hostname or '').lower()
    if not host or urlsplit(url).scheme not in ('http', 'https'):
        return False
    if any(host == x or host.endswith('.' + x) for x in ATS):
        return True
    return any(host == (urlsplit(seed).hostname or '').lower()
               or host.split('.')[-2:] == (urlsplit(seed).hostname or '').lower().split('.')[-2:]
               for seed in seeds)


def title_from_text(text):
    for title in re.split(r'[\n\r]', str(text or '').strip()):
        title = title.strip()
        if (4 <= len(title) <= 120 and JOB_TITLE.search(title)
                and not re.search(r'[；;。]|^\d{1,2}[、.)）]', title)
                and not re.search(r'(?:正式|全职|兼职).*(?:/| - )|产品\s*/\s*策划\s*/\s*项目',title)
                and not re.search(r'(?:本科|硕士|博士)(?:及以上)?$',title)
                and not (len(title) < 14 and title.endswith('类'))):
            return title
    return ''


def target_product(title):
    title = re.sub(r'可提前实习|可以实习|接受实习|可实习','',title)
    return bool(PRODUCT.search(title) and not re.search(
        r'实习|\bintern(?:ship|ships|s)?\b|资深|总监|\bsenior\b|\bprincipal\b|\bhead of\b',title,re.I))


def listing_url(url):
    parts=urlsplit(url)
    route=parts.path+'/' + parts.fragment.split('?',1)[0]
    return bool(re.search(r'(?:^|/)(?:jobs|positions?)(?:/(?:list|index|search))?(?:\.html)?/*$',route,re.I))


def record_from_link(company, link, jd='', batch=''):
    title = title_from_text(link.get('text'))
    url = link.get('href', '')
    if not title or not PRODUCT.search(title) or not JOB_URL.search(url):
        return None
    if re.search(r'职位已停止招聘|职位已下线|招聘已结束|job (?:is |has |)(?:no longer available|expired)|position is no longer available', jd, re.I):
        return None
    return RawJob(company_name=company, title=title, official_url=url,
                  jd_text=jd or link.get('context', ''),
                  raw={'identifier': hashlib.sha256(canonical(url).encode()).hexdigest()[:24],
                       'platform': 'official_board', 'batch_text': batch})


def specific_batch(headings, url):
    """Only a named campus campaign heading counts, never a general page date."""
    if not re.search(r'campus|school|graduate|校招', url, re.I):
        return ''
    out = []
    for text in headings:
        if len(text) < 140 and re.search(r'2027\s*届?.{0,15}(?:校园招聘|校招)|(?:校园招聘|校招).{0,15}2027', text):
            if not re.search(r'202[0-68-9]\s*届', text):
                out.append(text)
    return ' '.join(out)


DOM_SNAPSHOT = r'''() => {
 const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
 const text = e => String(e?.innerText || e?.textContent || '');
 const links = Array.from(document.querySelectorAll('a[href]')).filter(visible).map(e => ({
   text: (e.innerText || e.getAttribute('aria-label') || e.getAttribute('title') || e.querySelector('img')?.alt || '').trim(),
   href: e.href, context: text(e.closest('article,li,[class*="job-item"],[class*="position-item"],[class*="jobItem"],[class*="positionItem"]') || e.parentElement).slice(0,1800)
 }));
 const headings = Array.from(document.querySelectorAll('h1,h2,[class*="banner"] img,[class*="Banner"] img')).filter(visible).map(e => e.innerText || e.alt || '');
 const titles = Array.from(document.querySelectorAll('[class*="job-name"],[class*="position-name"],[class*="jobName"],[class*="positionName"],[class*="job-title"],[class*="position-title"],[class*="pos-title"],[class*="JobTitle"],[class*="jobTitle"],[class*="positionTitle"]')).filter(visible).map(e=>text(e).trim()).filter(t=>t && t.length < 120);
 const extra_titles = Array.from(document.querySelectorAll('[class*="title"],[class*="Title"],h3')).filter(visible).map(e=>text(e).trim()).filter(t=>t && t.length < 120 && /产品|\bproduct\b/i.test(t));
 const inputs = Array.from(document.querySelectorAll('input[placeholder]')).filter(visible).map(e=>e.placeholder);
 const buttons = Array.from(document.querySelectorAll('button,[role="button"]')).filter(visible).slice(-30).map(e=>({text:e.innerText,aria:e.getAttribute('aria-label'),class:e.className,disabled:e.disabled}));
 const candidate_nodes = Array.from(document.querySelectorAll('div,span,p,h2,h3,h4')).filter(e=>visible(e) && e.children.length===0 && /产品|中国兵器工业集团/.test(e.innerText) && e.innerText.length < 100).slice(0,30).map(e=>({text:e.innerText,html:e.parentElement.outerHTML.slice(0,1800)}));
 return {links, headings, titles, extra_titles, inputs, buttons, candidate_nodes, text: document.body.innerText.slice(0,100000)};
}'''


def json_jobs(value):
    """Recognize actual job objects in a public response, not facets/categories."""
    if isinstance(value, list):
        for item in value:
            yield from json_jobs(item)
    elif isinstance(value, dict):
        title = value.get('title') or value.get('positionName') or value.get('postName') or value.get('jobName')
        ident = value.get('id') or value.get('jobId') or value.get('positionId') or value.get('postId')
        desc = value.get('description') or value.get('jobDescription') or value.get('jobDuty') or value.get('workContent')
        if title and ident and (desc or value.get('requirement') or value.get('qualification')):
            yield {'title': str(title), 'id': str(ident), 'jd': '\n'.join(str(x or '') for x in (desc, value.get('requirement'), value.get('qualification'))),
                   'url': value.get('url') or value.get('jobUrl') or value.get('positionUrl') or '',
                   'location': str(value.get('workplace') or value.get('workPlaceStr') or ''),
                   'batch': str(value.get('projectName') or value.get('batchName') or '')}
        for child in value.values():
            if isinstance(child, (list, dict)):
                yield from json_jobs(child)


async def collect_company(browser, spec, diagnostics=None):
    context = await browser.new_context(locale='zh-CN')
    page = await context.new_page()
    page.set_default_timeout(3500)
    captures, tasks = [], []

    async def response_data(response):
        if 'json' in response.headers.get('content-type', ''):
            try:
                data = await response.json()
                captures.extend(json_jobs(data))
            except Exception:
                # Navigation can evict an auxiliary response body; the visible
                # DOM/detail readers remain required, so this is not success.
                return

    def on_response(response):
        tasks.append(asyncio.create_task(response_data(response)))

    page.on('response', on_response)
    queue = list(dict.fromkeys(spec['urls']))
    visited, links, headings, reasons = set(), {}, [], []
    all_listings, unresolved_titles, explicit_empty, pages_read = set(), set(), False, 0
    known = {}
    listing_complete = False

    async def reveal_listing():
        # Official campaign sites often hide their list until a normal scroll
        # triggers its entrance animation. Return to the top for virtual lists.
        for _ in range(3):
            await page.mouse.wheel(0, 800)
            await page.wait_for_timeout(180)
        await page.mouse.wheel(0, -2400)
        await page.wait_for_timeout(220)

    try:
        while queue and pages_read < 12:
            url = queue.pop(0)
            if canonical(url) in visited:
                continue
            visited.add(canonical(url))
            diagnostic = {'requested_url': url}
            if diagnostics is not None:
                diagnostics.append(diagnostic)
            try:
                response = await page.goto(url, wait_until='domcontentloaded', timeout=25000)
                diagnostic['status'] = response.status if response else None
                if response and response.status >= 400:
                    reasons.append(f'HTTP {response.status}'); continue
                try:
                    await page.wait_for_load_state('networkidle', timeout=8000)
                except Exception:
                    # Some employer pages poll continuously. Visible listings
                    # still have to pass the checks below.
                    pass
                await page.wait_for_timeout(1600)
                await reveal_listing()
                snapshot = await page.evaluate(DOM_SNAPSHOT)
                if diagnostics is not None:
                    diagnostic.update(url=page.url, headings=snapshot['headings'],titles=snapshot['titles'][:40],
                        extra_titles=snapshot.get('extra_titles', [])[:40],inputs=snapshot.get('inputs', []),
                        buttons=snapshot.get('buttons', []),candidate_nodes=snapshot.get('candidate_nodes', []),links=snapshot['links'][:60],text=snapshot['text'][:8000])
                if FORBIDDEN.search(snapshot['text'][:6000]):
                    reasons.append('官网安全验证或访问限制，未绕过'); continue
                pages_read += 1
                headings.extend(snapshot['headings'])
                if re.search(r'social|experienced|off-campus',page.url,re.I):
                    campus_links=[link['href'] for link in snapshot['links']
                        if re.search(r'校招|校园招聘|campus|graduate',link['text'],re.I)
                        and re.search(r'campus|school|graduate',link['href'],re.I)
                        and own_or_linked(link['href'],spec['urls'])
                        and canonical(link['href']) not in visited]
                    if campus_links:
                        queue.insert(0,campus_links[0])
                        continue
                if (not snapshot['titles']
                        and not any(target_product(title_from_text(text)) for text in snapshot.get('extra_titles',[]))
                        and not any(title_from_text(x['text']) and JOB_URL.search(x['href']) for x in snapshot['links'])):
                    # Several official portals use navigation controls without
                    # href attributes. Only open listing/campus navigation;
                    # never click a job's apply/submit control.
                    nav_controls = page.get_by_text(re.compile(r'^(职位|校园招聘|应届生招聘|应届生职位|校招职位|职位列表|全部职位|全部岗位|热招职位|岗位投递|查看所有职位|Campus Recruitment|Graduate Jobs|Fresh Graduates Program|View all jobs)$',re.I))
                    for nav in (await nav_controls.all())[:8]:
                        if not await nav.is_visible(): continue
                        before = page.url
                        old_pages = set(context.pages)
                        try:
                            await nav.click(timeout=3000)
                            await page.wait_for_timeout(500)
                            opened = [p for p in context.pages if p not in old_pages]
                            target = opened[-1] if opened else page
                            await target.wait_for_load_state('domcontentloaded',timeout=12000)
                            changed = canonical(target.url) != canonical(before)
                            if changed and own_or_linked(target.url,spec['urls']):
                                if canonical(target.url) not in visited and target.url not in queue: queue.append(target.url)
                            if opened:
                                for popup in opened: await popup.close()
                            if canonical(page.url) != canonical(before):
                                await page.go_back(wait_until='domcontentloaded',timeout=15000)
                            if changed: break
                        except Exception:
                            continue
                for job in parse_jobs(await page.content(), page.url):
                    terms = spec.get('require_company_terms', [])
                    original = job.company_name + ' ' + job.title + ' ' + job.jd_text
                    if terms and not any(term.lower() in original.lower() for term in terms):
                        continue
                    if target_product(job.title):
                        job.company_name = spec['company']; known[canonical(job.official_url)] = job
                    all_listings.add(job.official_url)
                async def scan_results(current):
                    nonlocal explicit_empty
                    for number in range(30):
                        explicit_empty = explicit_empty or bool(EMPTY.search(current['text']))
                        for link in current['links']:
                            title = title_from_text(link['text'])
                            if title and JOB_URL.search(link['href']):
                                all_listings.add(canonical(link['href']))
                                if target_product(title): links[canonical(link['href'])] = link
                            elif (NAV.search(link['text']) or listing_url(link['href'])) and len(link['text']) < 85 and own_or_linked(link['href'], spec['urls']):
                                if canonical(link['href']) not in visited and link['href'] not in queue:
                                    queue.append(link['href'])
                        # Beisen and other boards use clickable title controls
                        # and open the real detail in a new tab instead of <a>.
                        captured_titles=[item['title'] for item in captures if target_product(item['title'])]
                        for raw_title in dict.fromkeys(current['titles'] + current.get('extra_titles', []) + captured_titles):
                            title = title_from_text(raw_title)
                            if not title: continue
                            if raw_title in current['titles']:
                                all_listings.add('title:' + canonical(page.url) + ':' + title)
                            if not target_product(title): continue
                            if any(title_from_text(link['text']) == title for link in links.values()): continue
                            before = page.url
                            opened = []
                            try:
                                candidates = await page.get_by_text(raw_title, exact=True).all()
                                candidates = [candidate for candidate in candidates if await candidate.is_visible()]
                                # API metadata may include another result page.
                                # Only an actual visible card may be opened.
                                if (not candidates and raw_title in captured_titles
                                        and raw_title not in current['titles'] + current.get('extra_titles',[])):
                                    continue
                                unresolved_titles.add(title)
                                for candidate in candidates:
                                    if not await candidate.is_visible(): continue
                                    old_pages = set(context.pages)
                                    await candidate.click()
                                    await page.wait_for_timeout(600)
                                    opened = [p for p in context.pages if p not in old_pages]
                                    target = opened[-1] if opened else page
                                    if opened:
                                        await target.wait_for_url(lambda value: str(value) not in ('about:blank',''),timeout=12000)
                                    await target.wait_for_load_state('domcontentloaded', timeout=15000)
                                    if diagnostics is not None:
                                        diagnostic.setdefault('title_attempts',[]).append({'title':title,'url':target.url})
                                    if canonical(target.url) == canonical(before) or not JOB_URL.search(target.url): break
                                    try:
                                        await target.get_by_text(re.compile(r'工作职责|职位描述|任职要求|任职资格|Responsibilities|Qualifications',re.I)).first.wait_for(timeout=8000)
                                    except Exception:
                                        await target.wait_for_timeout(700)
                                    jd = await target.locator('body').inner_text()
                                    if diagnostics is not None:
                                        diagnostic['title_attempts'][-1]['detail_head']=jd[:1200]
                                    if not re.search(r'工作职责|岗位职责|职位描述|任职要求|任职资格|岗位要求|Job Description|Responsibilities|Qualifications',jd,re.I):
                                        reasons.append('标题导航未出现岗位详情，不作为职位')
                                        break
                                    if not FORBIDDEN.search(jd[:6000]):
                                        unresolved_titles.discard(title)
                                        all_listings.add(canonical(target.url))
                                    terms = spec.get('require_company_terms', [])
                                    if not FORBIDDEN.search(jd[:6000]) and (not terms or any(t.lower() in (title+' '+jd).lower() for t in terms)):
                                        link = {'text':title,'href':target.url,'context':jd}
                                        job = record_from_link(spec['company'],link,jd,specific_batch(headings,target.url))
                                        if job:
                                            links[canonical(target.url)] = link
                                            known[canonical(target.url)] = job
                                    break
                            except Exception as exc:
                                unresolved_titles.add(title)
                                if diagnostics is not None:
                                    diagnostic.setdefault('title_errors',[]).append({'title':title,'error':str(exc)[:400]})
                                reasons.append('标题详情未读: ' + type(exc).__name__)
                            finally:
                                for popup in opened: await popup.close()
                                if canonical(page.url) != canonical(before):
                                    await page.go_back(wait_until='domcontentloaded',timeout=15000)
                                    await page.wait_for_timeout(500)
                        next_page = page.locator('button[aria-label="Next page"],button[aria-label="下一页"],li.ant-pagination-next:not(.ant-pagination-disabled),button.btn-next:not([disabled]),a.next:not(.disabled)').first
                        if not await next_page.count():
                            next_page = page.get_by_role('link',name=re.compile(r'^(下一页|Next|Next page|›|»)$',re.I)).first
                        if await next_page.count():
                            disabled_class = await next_page.get_attribute('class') or ''
                            if re.search(r'disabled|is-disabled', disabled_class,re.I) or await next_page.get_attribute('aria-disabled') == 'true':
                                return
                        if not await next_page.count() or not await next_page.is_visible() or not await next_page.is_enabled():
                            titles = page.locator('[class*="JobTitle"],[class*="job-title"],[class*="jobName"],[class*="positionName"]')
                            if not await titles.count(): return
                            await titles.last.scroll_into_view_if_needed()
                            await page.mouse.wheel(0,1000)
                            await page.wait_for_timeout(850)
                            following = await page.evaluate(DOM_SNAPSHOT)
                            if following['links'] == current['links'] and following['titles'] == current['titles']:
                                return
                            if number == 29: raise RuntimeError('职位滚动分页超过30页，拒绝不完整基线')
                            current = following
                            continue
                        if number == 29: raise RuntimeError('职位分页超过30页，拒绝不完整基线')
                        await next_page.click()
                        await page.wait_for_timeout(900)
                        await reveal_listing()
                        following = await page.evaluate(DOM_SNAPSHOT)
                        # Some portals update the selected page before the jobs
                        # arrive. Wait for actual cards, not just the paginator.
                        for _ in range(6):
                            if (following['titles'] != current['titles']
                                    or following.get('extra_titles',[]) != current.get('extra_titles',[])
                                    or [x for x in following['links'] if title_from_text(x['text']) and JOB_URL.search(x['href'])]
                                       != [x for x in current['links'] if title_from_text(x['text']) and JOB_URL.search(x['href'])]):
                                break
                            await page.wait_for_timeout(600)
                            following = await page.evaluate(DOM_SNAPSHOT)
                        if (following['links'] == current['links'] and following['titles'] == current['titles']
                                and following.get('extra_titles', []) == current.get('extra_titles', [])):
                            raise RuntimeError('下一页未发生变化，无法确认完整列表')
                        current = following
                # Paginate each language's complete search result separately.
                # Never take only the first Chinese page then paginate English.
                searched = False
                for field in await page.locator('input[placeholder]').all():
                    if not await field.is_visible(): continue
                    hint = await field.get_attribute('placeholder') or ''
                    is_job_search = re.search(r'职位名称|职位关键词|搜索职位|搜索岗位|职位/公司/城市|job title|job keywords|search jobs|search for a job',hint,re.I)
                    if not is_job_search and re.search(r'关键词|关键字|keyword',hint,re.I):
                        is_job_search=bool(snapshot['titles'] or captures or any(title_from_text(link['text']) and JOB_URL.search(link['href']) for link in snapshot['links']))
                    if is_job_search:
                        for term in ('产品', 'product'):
                            # Reactive portals rebuild inputs after a search;
                            # resolve the actual visible input again each time.
                            candidates = await page.get_by_placeholder(hint,exact=True).all()
                            visible_fields = [candidate for candidate in candidates if await candidate.is_visible()]
                            if not visible_fields:
                                # A homepage search may navigate to a list with
                                # a different placeholder (e.g. Ant Group).
                                for candidate in await page.locator('input[placeholder]').all():
                                    current_hint=await candidate.get_attribute('placeholder') or ''
                                    if await candidate.is_visible() and re.search(r'职位名称|职位关键词|搜索职位|搜索岗位|job title|job keywords|search jobs|search for a job',current_hint,re.I):
                                        visible_fields.append(candidate)
                            if not visible_fields: raise RuntimeError('职位搜索框重绘后不可用')
                            current_field = visible_fields[-1]
                            await current_field.fill(term)
                            buttons = await page.get_by_role('button',name=re.compile(r'^(搜索|查询|搜索职位|搜索岗位|Search|Search jobs)$',re.I)).all()
                            visible_buttons = [button for button in buttons if await button.is_visible()]
                            if not visible_buttons:
                                buttons = await page.get_by_text(re.compile(r'^(搜索|查询)$')).all()
                                visible_buttons = [button for button in buttons if await button.is_visible()]
                            if visible_buttons:
                                await visible_buttons[0].click()
                            else:
                                await current_field.press('Enter')
                            await page.wait_for_timeout(1600)
                            await reveal_listing()
                            search_snapshot=await page.evaluate(DOM_SNAPSHOT)
                            if diagnostics is not None:
                                diagnostic.setdefault('searches',[]).append({'term':term,'url':page.url,
                                    'titles':search_snapshot['titles'][:30],'extra_titles':search_snapshot.get('extra_titles',[])[:30],
                                    'candidates':search_snapshot.get('candidate_nodes',[])[:8],
                                    'links':search_snapshot['links'][:10],'text':search_snapshot['text'][:4000]})
                            await scan_results(search_snapshot)
                        searched = True
                        break
                if not searched:
                    await scan_results(snapshot)
                list_route = listing_url(page.url) or bool(re.search(r'jobList|position/list',page.url,re.I))
                if (searched or list_route) and (all_listings or captures or explicit_empty):
                    # A verified complete list is the authority. Do not chase
                    # social-hire links/FAQs after finishing a campus board.
                    listing_complete = True
                    queue.clear()
                    break
            except Exception as exc:
                diagnostic['error'] = type(exc).__name__ + ': ' + str(exc)[:400]
                reasons.append(type(exc).__name__ + ': ' + str(exc)[:160])
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if any('分页' in reason or '下一页' in reason for reason in reasons):
            raise RuntimeError('职位列表分页未完成；' + '; '.join(reasons[-3:]))
        # Map returned job descriptions to the exact visible detail links. A
        # response without a real detail URL is not turned into a guessed URL.
        metadata = {canonical(j['url']): j for j in captures if j['url'].startswith(('http://','https://'))}
        for item in captures:
            for key in links:
                if re.search(r'(?:[/=?&#]|^)' + re.escape(item['id']) + r'(?:[/&#?]|$)', key): metadata[key] = item
        if any(target_product(item['title']) for item in captures) and not links and not known:
            raise RuntimeError('官网存在产品职位，但未取得可验证详情链接；需适配该招聘系统')
        if unresolved_titles:
            raise RuntimeError('产品标题尚未取得可验证岗位详情：' + '、'.join(sorted(unresolved_titles))[:160])
        if len(links) > 120: raise RuntimeError('产品职位超过详情读取上限，拒绝不完整基线')
        for key, link in links.items():
            if key in known: continue
            meta = metadata.get(key, {})
            jd = meta.get('jd', '')
            batch = meta.get('batch', '') or specific_batch(headings, link['href'])
            try:
                if not jd:
                    await page.goto(link['href'], wait_until='domcontentloaded', timeout=20000)
                    await page.wait_for_timeout(650)
                    text = await page.locator('body').inner_text()
                    if FORBIDDEN.search(text[:6000]): raise RuntimeError('岗位详情要求安全验证')
                    # Restrict evidence to the main detail before similar jobs.
                    jd = re.split(r'相似职位|相关职位|推荐职位|Similar Jobs|Related Jobs', text, flags=re.I)[0][-25000:]
                terms = spec.get('require_company_terms', [])
                if terms and not any(term.lower() in (link['text']+' '+jd).lower() for term in terms): continue
                if re.search(r'职位已停止招聘|职位已下线|招聘已结束|job (?:is |has |)(?:no longer available|expired)|position is no longer available', jd, re.I):
                    continue
                job = record_from_link(spec['company'], link, jd, batch)
                if job:
                    job.location = meta.get('location', '')
                    known[key] = job
            except Exception as exc:
                reasons.append('详情未读: ' + type(exc).__name__)
        if not pages_read or not (all_listings or captures or explicit_empty):
            raise RuntimeError('未验证到可读职位列表；' + '; '.join(reasons[-3:]))
        if not listing_complete and queue and pages_read >= 12:
            raise RuntimeError('招聘页面导航超过上限，需专用适配器')
        if links and len(known) < len(links) and reasons:
            raise RuntimeError('部分产品职位详情读取失败；' + '; '.join(reasons[-3:]))
        return {'ok': True, 'company': spec['company'], 'jobs': [asdict(j) for j in known.values()],
                'pages_read': pages_read, 'listings_seen': len(all_listings), 'notes': reasons,
                'explicit_empty': explicit_empty, 'urls_read': sorted(visited)}
    finally:
        if diagnostics is not None and diagnostics:
            diagnostics[-1]['api_product_titles'] = [
                {'title':item['title'],'id':item['id'],'url':item['url']}
                for item in captures if target_product(item['title'])][-30:]
        for task in tasks:
            if not task.done(): task.cancel()
        await context.close()


async def collect_all(specs, destination=CACHE):
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError('全公司官网监控需要可选依赖 Playwright；原六家API仍可单独运行') from exc
    semaphore = asyncio.Semaphore(5)
    results = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        async def one(spec):
            async with semaphore:
                try:
                    result = await asyncio.wait_for(collect_company(browser, spec), timeout=150)
                except Exception as exc:
                    result = {'ok': False, 'company': spec['company'], 'error': type(exc).__name__ + ': ' + str(exc)[:400]}
                results[spec['source_id']] = result
                print(('OK ' if result['ok'] else 'FAIL ') + spec['company'] + ' ' + str(len(result.get('jobs', []))) + ' product listings', flush=True)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        await asyncio.gather(*(one(spec) for spec in specs))
        await browser.close()
    return results


def prepare():
    global _snapshot
    specs = json.loads(WATCHLIST.read_text(encoding='utf-8'))['companies']
    specs = [spec for spec in specs if spec['method'] == 'official_board']
    try:
        _snapshot = asyncio.run(collect_all(specs))
    except Exception as exc:
        # Keep a visible error for every affected employer while allowing the
        # independent API sources to continue. No source is called healthy.
        _snapshot = {spec['source_id']: {'ok': False, 'company': spec['company'],
                     'error': type(exc).__name__ + ': ' + str(exc)[:400]} for spec in specs}
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(_snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    return _snapshot


@register('watch_official')
def fetch(endpoint):
    if _snapshot is None: raise RuntimeError('官网批次尚未采集，需先运行 official_board.prepare()')
    item = _snapshot.get(endpoint, {})
    if not item.get('ok'): raise RuntimeError(item.get('error') or '官网本轮没有成功快照')
    return [RawJob(**job) for job in item['jobs']]
