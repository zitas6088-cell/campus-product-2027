"""Per-company check results; a failed or missing source is never 'no new jobs'."""
import json
import re
from .campus_product import is_product


def company_rows(cfg, catalog, health, new_jobs, first_sources=()):
    specs = {s['company']:s for s in catalog if s['source_id'] in cfg['source_ids']}
    sources = {s['source_id']:s for s in health.get('sources', [])}
    grouped = {}
    for job in new_jobs:
        if job['company_name'] in cfg['companies'] and job['source_id'] in cfg['source_ids'] and is_product(job,cfg):
            grouped.setdefault((job['company_name'],job['source_id']),[]).append(job)
    rows = []
    for company in cfg['companies']:
        sid = specs.get(company,{}).get('source_id')
        source = sources.get(sid)
        issue = ''
        if not sid: issue = '未找到对应官网配置'
        elif not source or source.get('not_run'): issue = '本轮未检查到该公司'
        elif source.get('last_error') or source.get('alert'):
            issue = source.get('last_error') or source['alert']
        elif source.get('skipped'): issue = source.get('note') or '本轮跳过，未完成检查'
        elif type(source.get('last_count')) is not int or source['last_count'] < 0:
            issue = '没有有效的本轮读取结果'
        jobs = grouped.get((company,sid),[]) if not issue else []
        detail = re.sub(r'^(?:RuntimeError:\s*)+', '', ' '.join(str(issue).split()))[:160]
        if not issue and sid in first_sources:
            detail = '首次成功检查，仅建立现有岗位基线，旧岗位不计新增'
        rows.append({'company':company,'source_id':sid,
                     'status':'异常' if issue else ('新增' if jobs else '没有新增'),
                     'detail':detail,'jobs':jobs})
    return rows


def report_parts(rows, slot, completed_at, run_url=''):
    counts = {s:sum(r['status']==s for r in rows) for s in ('新增','没有新增','异常')}
    header = (f'2027届产品岗逐公司检查结果｜{slot[:16].replace("T"," ")}（北京时间）\n'
              f'完成时间：{completed_at}\n'
              f'共{len(rows)}家公司：新增{counts["新增"]}家；没有新增{counts["没有新增"]}家；异常{counts["异常"]}家。\n'
              '新增仅指官网确认适用2027届的产品相关正式校招。')
    if run_url: header += '\n运行记录：'+run_url
    atoms = []
    for row in rows:
        text = row['company']+'：'+row['status']
        if row['jobs']: text += f' {len(row["jobs"])}个岗位'
        if row['detail']: text += '（'+row['detail']+'）'
        atoms.append(text)
        for job in row['jobs']:
            atoms.append(f'  {row["company"]}｜{job["title"]}\n{job["official_url"]}')
    bodies, current = [], []
    for atom in atoms:
        trial='\n\n'.join(current+[atom])
        # Bound the actual JSON-escaped text, leaving room for provider fields.
        if len(json.dumps(header+'\n\n'+trial).encode()) > 10000:
            if not current: raise RuntimeError('单条公司状态超过通知长度上限')
            bodies.append('\n\n'.join(current));current=[]
            if len(json.dumps(header+'\n\n'+atom).encode()) > 10000:
                raise RuntimeError('单条岗位链接超过通知长度上限')
        current.append(atom)
    if current: bodies.append('\n\n'.join(current))
    return [header+f'\n第{i+1}/{len(bodies)}条\n\n'+body for i,body in enumerate(bodies)]
