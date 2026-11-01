"""Read a small selection of public boards and export diagnosis as an artifact."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from job_radar.adapters.official_board import collect_company


async def main():
    value=os.environ.get('PROBE_ROWS','').strip()
    if not re.fullmatch(r'\d+(?:\s*,\s*\d+){0,29}',value):
        raise ValueError('PROBE_ROWS需为1至30个表格行号，以逗号分隔')
    rows={int(row) for row in value.split(',')}
    specs=json.loads((ROOT/'config/company_watchlist.json').read_text(encoding='utf-8'))['companies']
    specs=[spec for spec in specs if spec['sheet_row'] in rows and spec['method']=='official_board']
    if {spec['sheet_row'] for spec in specs}!=rows:
        raise ValueError('行号不存在或不是公开网页信源；未执行')
    from playwright.async_api import async_playwright
    output=ROOT/'probe_output';output.mkdir(exist_ok=True)
    result={};semaphore=asyncio.Semaphore(3)
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        async def one(spec):
            async with semaphore:
                diagnostics=[]
                try:
                    item=await asyncio.wait_for(collect_company(browser,spec,diagnostics),timeout=180)
                except Exception as exc:
                    item={'ok':False,'company':spec['company'],'error':type(exc).__name__+': '+str(exc)[:500]}
                item['diagnostics']=diagnostics[-4:]
                result[spec['source_id']]=item
                print(('OK ' if item['ok'] else 'FAIL ')+spec['company']+' '+str(item.get('error','')),flush=True)
                (output/'source_probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        await asyncio.gather(*(one(spec) for spec in specs))
        await browser.close()
    print('Diagnosis complete. No notification or production data changes.')


if __name__=='__main__':asyncio.run(main())
