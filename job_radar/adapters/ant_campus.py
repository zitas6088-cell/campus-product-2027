"""Anonymous Ant campus search observed on the employer's public website."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlencode

from .http import post_json
from ..models import RawJob

ENDPOINT = 'https://hrcareersweb.antgroup.com/api/campus/position/search'
HEADERS = {'Referer': 'https://talent.antgroup.com/',
           'User-Agent': 'campus-product-2027-monitor/1.0'}


def search(term):
    """Read every result page, including internships, before selecting a cohort."""
    rows, seen, received, expected = [], set(), 0, None
    for page in range(1, 101):
        body = dict(channel='campus_group_official_site', language='zh',
                    regions='', subCategories='', bgCode='', key=term,
                    pageIndex=page, pageSize=10, recruitType=[], batchIds=[])
        data = post_json(ENDPOINT, body, headers=HEADERS)
        if (not isinstance(data, dict) or data.get('success') is not True
                or type(data.get('totalCount')) is not int or data['totalCount'] < 0
                or 'content' not in data):
            raise RuntimeError('蚂蚁公开岗位接口格式或业务状态异常')
        total, batch = data['totalCount'], data['content']
        if expected is None:
            expected = total
        if total != expected:
            raise RuntimeError('蚂蚁搜索总数在分页期间变化，拒绝不完整快照')
        if total == 0 and batch in (None, []):
            return []
        if (not isinstance(batch, list) or not batch
                or data.get('currentPage') != page or not data.get('pageSize')):
            raise RuntimeError('蚂蚁岗位列表提前结束或页码异常')
        added = 0
        for row in batch:
            if not isinstance(row, dict) or not str(row.get('id') or '').isdigit():
                raise RuntimeError('蚂蚁岗位缺少官网编号')
            ident = str(row['id'])
            if ident not in seen:
                seen.add(ident); rows.append(row); added += 1
        if not added:
            raise RuntimeError('蚂蚁岗位分页重复')
        received += len(batch)
        if received >= total:
            if len(seen) != total:
                raise RuntimeError('蚂蚁岗位分页计数不一致')
            return rows
    raise RuntimeError('蚂蚁岗位分页超过上限')


def fetch():
    """Return RawJob only; normalization and notification stay downstream."""
    results = {}
    for term in ('产品', 'product'):
        for row in search(term):
            title = str(row.get('name') or '').strip()
            batch = str(row.get('batchName') or '')
            # The old public homepage config contains an obsolete batch ID.
            # Eligibility comes from each current job's actual batch metadata.
            if (row.get('batchType') != 'graduate'
                    or not re.search(r'2027\s*届.*应届生招聘', batch)
                    or not re.search(r'产品|\bproduct\b', title, re.I)):
                continue
            if re.search(r'实习|资深|总监|\bintern(?:ship)?\b|\bsenior\b', title, re.I):
                continue
            if not row.get('description') or not row.get('requirement'):
                raise RuntimeError('蚂蚁产品岗位缺少完整职责或要求')
            ident = str(row['id'])
            # Actual route opened from the official job card, with its public
            # positionId. The varying tid is analytics, not a job identity.
            url = 'https://talent.antgroup.com/campus-position?' + urlencode({'positionId': ident})
            results[ident] = RawJob(
                company_name='蚂蚁集团', title=title,
                location='、'.join(row.get('workLocations') or []),
                publish_time=str(row.get('publishTime') or '')[:10],
                official_url=url,
                jd_text='职位描述\n' + row['description'] + '\n职位要求\n' + row['requirement'],
                raw={'identifier': hashlib.sha256(url.encode()).hexdigest()[:24],
                     'platform': 'official_board', 'batch_text': batch,
                     'official_position_id': ident, 'official_batch_id': row.get('batchId')})
    return list(results.values())
