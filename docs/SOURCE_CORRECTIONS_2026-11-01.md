# 招聘入口核对记录 · 2026-11-01

仅保留公司公开招聘入口，不包含个人投递信息。入口核对与成功抓取是两件事；当轮是否读取成功，以 `data/company_monitor_coverage.md` 为准。届别仍须由岗位或明确校招批次证实。

| 公司 | 本次采用的入口 | 核对依据 |
| --- | --- | --- |
| 中芯国际 | https://smics.zhiye.com/campus/jobs | 官方招聘页面可见2027校园招聘批次 |
| 国轩高科 | https://gotion.zhiye.com/campus | 当前官方招聘入口，替代失效的gotionsz.hotjob.cn |
| 中国银联 | https://join.unionpay.com/wt/unionpayhr/web/index | [银联官方招聘指引](https://cn.unionpay.com/upowhtml/cn/templates/application/application.html)的网申链接 |
| 完美世界 | https://app.mokahr.com/campus-recruitment/pwrd/172467?locale=zh-CN | [企业发布于南开大学就业网的2027校招公告](https://career.nankai.edu.cn/correcruit/content/id/117197.html)列明官方网申入口 |
| 小马智行 | https://ponyai.jobs.feishu.cn/ponycampus | [公司2027校招指引](https://campus.pony.ai/guideline)的“热招职位”链接 |
| 澜起科技 | https://www.montage-tech.com/cn/Open_Positions | [公司官网](https://www.montage-tech.com/cn/)“加入我们”链接 |
| 德勤 | https://www.deloitte.com/cn/zh/cn-careers/students.html | 德勤当前学生招聘页，替代已失效的www2旧入口；不据此自动确认2027届 |
| 小鹏汽车 | https://xiaopeng.jobs.feishu.cn/campus | [北京小鹏汽车有限公司在南开大学发布的2027校招公告](https://career.nankai.edu.cn/correcruit/content/id/116251.html)列明该投递地址 |
| 宝马集团 | https://www.bmwgroup.jobs/cn/en/current-vacancies.html | [宝马中国机会页面](https://www.bmwgroup.jobs/cn/en/opportunities.html)的Current Vacancies入口，旧cn/zh.html返回404 |
| 西门子 | https://jobs.siemens.com.cn/ | [西门子现行官方校园招聘说明](https://www.siemens.com/zh-cn/company/jobs/campus-recruiting/)列明中国招聘官网 |
| 中国兵器工业集团 | https://norincogroupzhaopin.zhiye.com/campus/jobs | [海南师范大学2027秋招公告](https://jy.hainnu.edu.cn/web/Index/jobs-brief-detail?id=VYUXSNR)列明入口；[集团子公司在吉林大学的招聘公告](https://jdjyw.jlu.edu.cn/mportal/recruit/details?id=104b560270e94947b901ed13738e81ae)交叉确认同一官方招聘平台 |

英伟达使用其现有官方Workday公开职位接口，在华工作地点过滤及详情实测成功。Unity旧接口返回HTTP422、华为旧接口返回空列表但未证实为当前批次，均未切换到这些未验证接口。realme暂未找到足够依据支持替换，不猜测地址。

## 针对原86家异常来源的再次复测

以下入口更新已进入监控配置。入口存在不等于整家公司已经稳定读取；最终状态仍以主监控报告为准。

| 公司 | 本次入口 | 依据及验证 |
| --- | --- | --- |
| 联想 | https://talent.lenovo.com.cn/position | 联想官网的“招聘岗位”页面，替代只从介绍首页查找列表 |
| 华为 | https://career.huawei.com/cn/campus-recruitment-job-list | 华为现行官方“校招职位列表”，保留校园介绍页为备用 |
| 顺丰科技 | https://campus.sf-express.com/index.html | 顺丰2027校园招聘首页，另保留[顺丰科技加入我们](https://www.sf-tech.com.cn/join/)作为官方引导入口 |
| 用友网络 | https://career.yonyou.com/SU67ac41886202cc7916ae3029/pb/school.html | 从原官方首页实际进入的校园职位列表；独立实测2条产品标题岗位及详情 |
| 宁德时代 | https://app.mokahr.com/campus-recruitment/catlhr/148948#/jobs | [宁德时代招聘门户](https://talent.catl.com/social-recruitment/catlhr/96144/)实际链接的校园职位列表；独立实测19条产品标题岗位及详情，未据此称19条均适用2027届 |
| 华海清科 | https://hwatsing1.zhiye.com/jobs | 页面明确标识华海清科、2027校招及公司官网联系方式；独立实测明确空列表 |

用友、宁德时代和华海清科三个入口已同步金山表格D/E列，投递进度和备注未修改。独立探针不持有通知密钥、不修改生产数据；只有正式主运行的成功来源才进入日常新增判断。读到0条但触发历史数量异常的来源继续列为异常。
