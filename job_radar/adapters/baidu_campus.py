"""Read Baidu's public graduate API and server-rendered job details."""
from __future__ import annotations

import hashlib
import json
import re

from .http import get_text, post_form
from ..models import RawJob

LIST_URL = 'https://talent.baidu.com/jobs/list?recruitType=GRADUATE'
ENDPOINT = 'https://talent.baidu.com/httservice/getPostListNew'
HEADERS = {'Referer': LIST_URL, 'User-Agent': 'campus-product-2027-monitor/1.0'}


def initial_data(html):
    """Decode the public SSR payload as data; never execute page JavaScript."""
    match = re.search(r'window\.__INITIAL_DATA__\s*=\s*', html)
    if not match:
        raise RuntimeError('百度官网缺少可验证的服务端岗位数据')
    value = html[match.end():]
    # The serializer emits undefined. Replace only tokens outside strings.
    value = re.sub(r'"(?:\\.|[^"\\])*"|\bundefined\b',
                   lambda m: 'null' if m.group() == 'undefined' else m.group(), value)
    data, _ = json.JSONDecoder().raw_decode(value)
    if not isinstance(data, dict):
        raise RuntimeError('百度官网服务端数据结构变化')
    return data


def listings(expected_total=None):
    rows, seen, expected = [], set(), None
    for page in range(1, 101):
        # The official client uses qs form encoding and curPage, not pageNum.
        # Keep its supported pageSize=10; unsupported larger sizes are rejected.
        response = post_form(ENDPOINT, dict(recruitType='GRADUATE', pageSize=10,
                             keyWord='', curPage=page, projectType=''), headers=HEADERS)
        data = response.get('data') if isinstance(response, dict) else None
        if (not isinstance(response, dict) or response.get('status') != 'ok' or not isinstance(data, dict)
                or not re.fullmatch(r'\d+', str(data.get('total', '')))
                or not isinstance(data.get('list'), list) or data.get('pageNum') != page):
            raise RuntimeError('百度公开校招接口或页码异常')
        total = int(data['total'])
        if expected is None:
            expected = total
            if expected_total is not None and expected != expected_total:
                raise RuntimeError('百度官网与公开接口岗位总数不一致')
        if total != expected:
            raise RuntimeError('百度校招总数在分页期间变化')
        batch = data['list']
        if not batch and len(rows) < expected:
            raise RuntimeError('百度校招分页提前结束')
        for row in batch:
            ident = row.get('postId') if isinstance(row, dict) else None
            if not ident or ident in seen:
                raise RuntimeError('百度校招缺少编号或分页重复')
            seen.add(ident); rows.append(row)
        if len(rows) >= expected:
            if len(rows) != expected:
                raise RuntimeError('百度校招分页计数不一致')
            return rows
    raise RuntimeError('百度校招分页超过上限')


def fetch():
    from .official_board import target_product
    listing = initial_data(get_text(LIST_URL, headers=HEADERS)).get('listData', {})
    if listing.get('recruitType') != 'GRADUATE':
        raise RuntimeError('百度官网没有进入正式校招范围')
    configs = [c for c in listing.get('listConfig', []) if c.get('recruitType') == 'GRADUATE']
    if len(configs) != 1 or not re.search(r'面向全球2027届毕业生', str(configs[0].get('subtitle', ''))):
        raise RuntimeError('百度官网未明确当前校招适用2027届')
    batch = configs[0]['subtitle']
    projects = {str(p['value']) for p in listing.get('graduateProjectList', [])}
    if not projects:
        raise RuntimeError('百度校招项目范围缺失')
    jobs = []
    if type(listing.get('total')) is not int:
        raise RuntimeError('百度官网岗位总数缺失')
    for row in listings(expected_total=listing['total']):
        title = str(row.get('name') or '')
        if not target_product(title):
            continue
        if str(row.get('projectTypeCode')) not in projects:
            raise RuntimeError('百度产品岗位项目不属于校招范围')
        ident = str(row['postId'])
        # This is the exact route constructed by the official visible job card.
        url = 'https://talent.baidu.com/jobs/detail/GRADUATE/' + ident
        detail = initial_data(get_text(url, headers=HEADERS)).get('detailData', {})
        info = detail.get('postInfo', {})
        if (detail.get('isValid') is not True or detail.get('recruitType') != 'GRADUATE'
                or str(info.get('postId')) != ident or info.get('name') != title
                or str(info.get('projectTypeCode')) not in projects
                or not info.get('workContent') or not info.get('serviceCondition')):
            raise RuntimeError('百度产品详情编号、职责或校招类型未验证')
        jobs.append(RawJob(company_name='百度',title=title,
                    location=str(info.get('workPlace') or ''),
                    publish_time=str(info.get('publishDate') or ''),official_url=url,
                    jd_text='工作职责\n'+info['workContent']+'\n任职要求\n'+info['serviceCondition'],
                    raw={'identifier':hashlib.sha256(url.encode()).hexdigest()[:24],
                         'platform':'official_board','batch_text':batch,
                         'official_position_id':ident}))
    return jobs
