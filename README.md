# ludinggong-xueqiu-monitor — 超级鹿鼎公雪球帖增量监控

只针对**雪球**，增量抓取「超级鹿鼎公」(UID `8790885129`) 的**原创帖**，
追加进 `data/posts.json` 底库（新→旧），并在有新帖时向飞书推送一条**仅成功入库**的通知。

- 主程序：[monitor.py](monitor.py)，依赖仅 [curl_cffi](requirements.txt)（模拟 Chrome TLS 指纹过雪球阿里云 WAF）
- 触发：**cron-job.org 外部触发（推荐，准时）** + GitHub Actions schedule 兜底（可能有延迟）
- 去重：优先按**雪球 id**，退化 **(日期, 正文)** 与 **(日期, 正文前30字)** 双键，历史绝不重复
- 收**原创帖 + 回复帖**（含转发式回复中的本人评论），无评论的纯转发跳过
- 接口：`https://www.xueqiu.com/v4/statuses/user_timeline.json`（旧接口已 404 下线；匿名可拉第 1 页，配 Cookie 可翻深页）
- `XQ_COOKIE` **可选**：不配置则匿名抓第 1 页（日常增量足够）；配置后可翻更深页，用于长时间中断后的补抓
- 飞书通知格式（不含正文、无新帖不推）：
  > ✅ 已成功将 N 条超级鹿鼎公的雪球帖加入帖子库（posts.json）（日期区间）
  > ⚠️ 失败时推送告警（Cookie 过期 / 抓取异常）

## 数据格式（data/posts.json）

```json
{
  "meta": { "source": "...", "uid": "8790885129", "count": 3641, ... },
  "posts": [
    { "id": null, "date": "2026-10-06", "text": "…", "category": "周期与供需" },
    { "id": "321…", "date": "2026-10-30", "text": "…" }
  ]
}
```

- `id: null` = xlsx 历史底库帖（无雪球 id），按 (日期, 正文) 参与去重
- 在线浏览：GitHub Pages 上的 [index.html](index.html)（运行时拉取 posts.json，自动更新，支持搜索/年份/分类筛选）

## 配置 GitHub Secrets

| Secret | 说明 |
|---|---|
| `XQ_COOKIE` | 浏览器登录 xueqiu.com 后的 Cookie（至少含 `xq_a_token`、`xqat`）；过期后监控会飞书告警，重新复制更新即可 |
| `FEISHU_WEBHOOK` | 飞书自定义机器人 Webhook |

## 手动触发

```bash
curl -X POST \
  -H "Authorization: Bearer <PAT>" \
  -H "Accept: application/vnd.github+json" \
  -d '{"event_type":"xueqiu-monitor"}' \
  https://api.github.com/repos/<owner>/ludinggong-xueqiu-monitor/dispatches
```
