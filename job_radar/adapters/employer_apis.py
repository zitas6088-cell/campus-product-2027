"""Additional official employer APIs already documented in this repository."""
from __future__ import annotations
import re
from urllib.parse import urlsplit
from . import register
from .http import get_json, post_json
from ..models import RawJob
from .workday import _find_china_facet, _CN


@register('watch_workday')
def workday(endpoint):
    host, tenant, site = endpoint.split('|',2)
    base=f'https://{host}/wday/cxs/{tenant}/{site}'
    def query(body):
        data=post_json(base+'/jobs',body,headers={'Referer':f'https://{host}/'})
        if not isinstance(data,dict) or not isinstance(data.get('jobPostings'),list) or 'total' not in data:
            raise RuntimeError('Workday职位接口结构改变，未作为空列表')
        return data
    data=query({'appliedFacets':{},'limit':20,'offset':0,'searchText':'product'})
    facet,china=_find_china_facet(data)
    applied={facet:[china]} if china else {}
    out=[];seen=set();received=0
    for page in range(100):
        data=query({'appliedFacets':applied,'limit':20,'offset':received,'searchText':'product'})
        batch=data['jobPostings'];total=int(data['total'])
        if not batch:
            if received < total: raise RuntimeError('Workday列表提前结束')
            return out
        for item in batch:
            path=item.get('externalPath','')
            if not path or path in seen: raise RuntimeError('Workday职位缺少链接或分页重复')
            seen.add(path)
            if not china and not _CN.search(item.get('locationsText','')): continue
            detail=get_json(base+path,headers={'Referer':f'https://{host}/'})
            info=detail.get('jobPostingInfo') or {}
            if not info.get('jobDescription'): raise RuntimeError('Workday职位详情未能读取')
            out.append(RawJob(company_name='',title=info.get('title') or item.get('title',''),
                location=info.get('location') or item.get('locationsText',''),
                official_url=f'https://{host}/en-US/{site}{path}',jd_text=info['jobDescription'],
                raw={'identifier':str(info.get('jobReqId') or path),'platform':'workday',
                     'employmentType':str(info.get('timeType') or '')}))
        received+=len(batch)
        if received>=total:return out
    raise RuntimeError('Workday分页超出上限，拒绝不完整快照')


@register('watch_huawei')
def huawei(endpoint):
    url='https://career.huawei.com/reccampportal/services/portal/portalpub/getJob/newHr/page/20/{}?'
    out=[];seen=set()
    for page in range(1,101):
        data=get_json(url.format(page),headers={'Referer':'https://career.huawei.com/reccampportal/portal5/campus-recruitment.html'})
        if not isinstance(data,dict) or not isinstance(data.get('result'),list):
            raise RuntimeError('华为职位接口结构改变，未作为空列表')
        total_pages=(data.get('pageVO') or {}).get('totalPages')
        if total_pages is None:raise RuntimeError('华为接口未返回完整分页信息')
        if not data['result'] and page <= int(total_pages):raise RuntimeError('华为职位列表提前结束')
        for item in data['result']:
            ident=str(item.get('jobId') or '')
            if not ident or ident in seen:raise RuntimeError('华为职位缺少编号或分页重复')
            seen.add(ident)
            out.append(RawJob(company_name='华为',title=str(item.get('nameCn') or item.get('jobName') or ''),
                location=str(item.get('jobCity') or item.get('workPlace') or ''),
                official_url='https://career.huawei.com/reccampportal/portal5/campus-recruitment-detail.html?jobId='+ident,
                jd_text='\n'.join(str(item.get(k) or '') for k in ('mostlyDuty','demand','jobRequire')),
                publish_time=str(item.get('releaseDate') or '')[:10],deadline=str(item.get('expirationDate') or '')[:10],
                raw={'identifier':ident,'platform':'huawei','batch_text':str(item.get('projectName') or '')}))
        if page>=int(total_pages):return out
    raise RuntimeError('华为职位分页超出上限')
