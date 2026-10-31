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
from urllib.parse import urljoin, urlsplit

from . import register
from ..models import RawJob
from .jsonld import parse_jobs

ROOT = Path(__file__).resolve().parents[2]
WATCHLIST = ROOT / 'config/company_watchlist.json'
CACHE = ROOT / 'data/official_board_snapshot.json'
PRODUCT = re.compile(r'产品|\bproduct\b', re.I)
JOB_URL = re.compile(r'(?:job|position|vacanc|career|requisition|detail|post)[^\s]*[0-9]|[?&](?:job|post|position|req)(?:Id|id|ID|Code)=', re.I)
JOB_TITLE = re.compile(r'经理|工程师|专员|策划|分析师|管培|培训生|助理|研究员|设计师|运营|manager|engineer|analyst|associate|graduate|intern|designer|specialist', re.I)
NAV = re.compile(r'校园招聘|应届生|校招职位|招聘职位|全部职位|职位列表|岗位投递|招聘岗位|加入我们|招聘官网|campus|graduate|search jobs|view jobs|careers|job openings', re.I)
FORBIDDEN = re.compile(r'验证码|安全验证|访问过于频繁|人机验证|Access Denied|Verify you are human|captcha', re.I)
EMPTY = re.compile(r'暂无(?:相关)?(?:职位|岗位)|没有找到相关职位|无匹配职位|no (?:matching |open |available )?(?:jobs|positions)|0 (?:jobs|positions)', re.I)
ATS = ('mokahr.com', 'zhiye.com', 'hotjob.cn', 'jobs.feishu.cn', 'myworkdayjobs.com', 'successfactors.com', 'oraclecloud.com', 'avature.net', 'smartrecruiters.com', 'eightfold.ai')
_snapshot = None


def canonical(url):
    return str(url or '').strip().rstrip('/')


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
    title = re.split(r'[\n\r]', str(text or '').strip())[0].strip()
    return title if 4 <= len(title) <= 120 and JOB_TITLE.search(title) else ''


def record_from_link(company, link, jd='', batch=''):
    title = title_from_text(link.get('text'))
    url = link.get('href', '')
    if not title or not PRODUCT.search(title) or not JOB_URL.search(url):
        return None
    return RawJob(company_name=company, title=title, official_url=url,
                  jd_text=jd or link.get('context', ''),
                  raw={'identifier': hashlib.sha256(url.encode()).hexdigest()[:24],
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
 const links = Array.from(document.querySelectorAll('a[href]')).filter(visible).map(e => ({
   text: (e.innerText || e.getAttribute('aria-label') || e.getAttribute('title') || '').trim(),
   href: e.href, context: (e.closest('article,li,[class*="job-item"],[class*="position-item"],[class*="jobItem"],[class*="positionItem"]') || e.parentElement).innerText.slice(0,1800)
 }));
 const headings = Array.from(document.querySelectorAll('h1,h2,[class*="banner"] img,[class*="Banner"] img')).filter(visible).map(e => e.innerText || e.alt || '');
 const titles = Array.from(document.querySelectorAll('[class*="job-name"],[class*="position-name"],[class*="jobName"],[class*="positionName"],[class*="job-title"],[class*="position-title"]')).filter(visible).map(e=>e.innerText.trim()).filter(t=>t && t.length < 120);
 return {links, headings, titles, text: document.body.innerText.slice(0,100000)};
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


async def collect_company(browser, spec):
    context = await browser.new_context(locale='zh-CN')
    page = await context.new_page()
    page.set_default_timeout(3500)
    captures, tasks = [], []

    async def response_data(response):
        if re.search(r'job|position|recruit', response.url, re.I) and 'json' in response.headers.get('content-type', ''):
            try:
                data = await response.json()
                captures.extend(json_jobs(data))
            except (ValueError, TypeError):
                return

    def on_response(response):
        tasks.append(asyncio.create_task(response_data(response)))

    page.on('response', on_response)
    queue = list(dict.fromkeys(spec['urls']))
    visited, links, headings, reasons = set(), {}, [], []
    all_listings, explicit_empty, pages_read = set(), False, 0
    known = {}
    try:
        while queue and pages_read < 12:
            url = queue.pop(0)
            if canonical(url) in visited:
                continue
            visited.add(canonical(url))
            try:
                response = await page.goto(url, wait_until='domcontentloaded', timeout=25000)
                if response and response.status >= 400:
                    reasons.append(f'HTTP {response.status}'); continue
                await page.wait_for_timeout(1600)
                snapshot = await page.evaluate(DOM_SNAPSHOT)
                if FORBIDDEN.search(snapshot['text'][:6000]):
                    reasons.append('官网安全验证或访问限制，未绕过'); continue
                pages_read += 1
                headings.extend(snapshot['headings'])
                for job in parse_jobs(await page.content(), page.url):
                    if PRODUCT.search(job.title):
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
                                if PRODUCT.search(title): links[canonical(link['href'])] = link
                            elif NAV.search(link['text']) and len(link['text']) < 85 and own_or_linked(link['href'], spec['urls']):
                                if canonical(link['href']) not in visited and link['href'] not in queue:
                                    queue.append(link['href'])
                        next_page = page.locator('button[aria-label="Next page"],button[aria-label="下一页"],li.ant-pagination-next:not(.ant-pagination-disabled),button.btn-next:not([disabled]),a.next:not(.disabled)').first
                        if not await next_page.count() or not await next_page.is_visible() or not await next_page.is_enabled():
                            return
                        if number == 29: raise RuntimeError('职位分页超过30页，拒绝不完整基线')
                        await next_page.click()
                        await page.wait_for_timeout(900)
                        following = await page.evaluate(DOM_SNAPSHOT)
                        if following['links'] == current['links']:
                            raise RuntimeError('下一页未发生变化，无法确认完整列表')
                        current = following
                # Paginate each language's complete search result separately.
                # Never take only the first Chinese page then paginate English.
                searched = False
                for field in await page.locator('input[placeholder]').all():
                    if not await field.is_visible(): continue
                    hint = await field.get_attribute('placeholder') or ''
                    if re.search(r'职位名称|职位关键词|搜索职位|搜索岗位|job title|search jobs|keyword', hint, re.I):
                        for term in ('产品', 'product'):
                            await field.fill(term)
                            await field.press('Enter')
                            await page.wait_for_timeout(1000)
                            await scan_results(await page.evaluate(DOM_SNAPSHOT))
                        searched = True
                        break
                if not searched:
                    await scan_results(snapshot)
            except Exception as exc:
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
        if any(PRODUCT.search(item['title']) for item in captures) and not links and not known:
            raise RuntimeError('官网存在产品职位，但未取得可验证详情链接；需适配该招聘系统')
        if len(links) > 120: raise RuntimeError('产品职位超过详情读取上限，拒绝不完整基线')
        for key, link in links.items():
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
                job = record_from_link(spec['company'], link, jd, batch)
                if job:
                    job.location = meta.get('location', '')
                    known[key] = job
            except Exception as exc:
                reasons.append('详情未读: ' + type(exc).__name__)
        if not pages_read or not (all_listings or captures or explicit_empty):
            raise RuntimeError('未验证到可读职位列表；' + '; '.join(reasons[-3:]))
        if queue and pages_read >= 12:
            raise RuntimeError('招聘页面导航超过上限，需专用适配器')
        if links and len(known) < len(links) and reasons:
            raise RuntimeError('部分产品职位详情读取失败；' + '; '.join(reasons[-3:]))
        return {'ok': True, 'company': spec['company'], 'jobs': [asdict(j) for j in known.values()],
                'pages_read': pages_read, 'listings_seen': len(all_listings), 'notes': reasons,
                'explicit_empty': explicit_empty, 'urls_read': sorted(visited)}
    finally:
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
