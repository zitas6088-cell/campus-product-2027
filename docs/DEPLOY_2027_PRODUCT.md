# 2027届产品岗部署说明

基于 Jasmine-Liu-min/job-radar 定制。只检查配置中的六家官网校招来源。
接口依据：GeoSyntax/xiaozhaogw、ruyi1/campus-radar、HA7CH/job-pro；部署前做真实接口校验。

- 每天北京时间约 09:17、14:17、20:17 检查；GitHub 定时任务可能延迟。
- 每个来源首次成功只建立基线，已有岗位在报告里查看，不作为新增刷屏。
- 正式产品岗必须有官网届别、毕业时间范围或已核实且未过期的批次证据。
- 届别未知和实习单列，不发正式校招提醒。默认不限城市。
- 业务码成功才记已推；失败留待下轮补发；推送记录独立保存。
- 只有六家来源，不代表覆盖所有国内大厂。详见 data/campus_product.md 的实际信源状态。
- 本版本不启用公共网页部署；结果在仓库报告、Actions Summary 和运行附件中查看。

## 通知凭证

仓库 Settings → Secrets and variables → Actions 添加 Secret：

- 飞书（本部署默认）：FEISHU_WEBHOOK_URL；仓库 Variable NOTIFY_CHANNEL=feishu。
- 企业微信：WECOM_WEBHOOK_URL；Variable NOTIFY_CHANNEL=wecom。
- PushPlus（可选，个人微信）：PUSHPLUS_TOKEN；Variable NOTIFY_CHANNEL=pushplus。

只需配置所选渠道。不要将凭证写进源码。
Actions → 2027届产品岗监控 → Run workflow；选择 test_notification 可做一次连接测试。
没有凭证时仍可建基线和生成报告；发现新增且无法推送时运行会失败，不会标记已发送。

## 本地验证

Python 3.12；核心任务不需要额外 pip 包。

```text
python -m unittest discover -s tests
python scripts/smoke_test.py
python -m job_radar.sync
python scripts/campus_watch.py --no-sync --dry-run
```
