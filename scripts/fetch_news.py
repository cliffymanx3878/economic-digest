# -*- coding: utf-8 -*-
"""
全球经济日报 - 新闻抓取与HTML生成脚本
每日由 GitHub Actions 自动运行
"""
import os
import re
import json
import html
import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from collections import Counter

try:
    import jieba
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False

# ===== 配置 =====
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "index.html")
CST = timezone(timedelta(hours=8))

# 国外权威财经媒体 RSS
GLOBAL_SOURCES = [
    {"id": "reuters",   "name": "Reuters",        "full": "路透社",
     "rss": "https://feeds.reuters.com/reuters/businessNews", "paywall": False},
    {"id": "ft",        "name": "Financial Times","full": "金融时报",
     "rss": "https://www.ft.com/rss/home", "paywall": True},
    {"id": "economist", "name": "The Economist",  "full": "经济学人",
     "rss": "https://www.economist.com/finance-and-economics/rss.xml", "paywall": True},
    {"id": "nyt",       "name": "NYT Business",   "full": "纽约时报商业版",
     "rss": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "paywall": True},
    {"id": "bbc",       "name": "BBC Business",   "full": "BBC 商业",
     "rss": "http://feeds.bbci.co.uk/news/business/rss.xml", "paywall": False},
    {"id": "cnbc",      "name": "CNBC",           "full": "CNBC 财经",
     "rss": "https://www.cnbc.com/id/10001147/device/rss/rss.html", "paywall": False},
    {"id": "npr",       "name": "NPR Business",   "full": "NPR 商业",
     "rss": "https://feeds.npr.org/1006/rss.xml", "paywall": False},
]

# 国内财经媒体（网页抓取）
CN_SOURCES = [
    {"id": "caixin", "name": "财新网",   "full": "财新网",
     "url": "https://www.caixin.com/", "paywall": True},
    {"id": "yicai",  "name": "第一财经", "full": "第一财经",
     "url": "https://www.yicai.com/", "paywall": False},
    {"id": "stcn",   "name": "证券时报", "full": "证券时报",
     "url": "https://www.stcn.com/", "paywall": False},
    {"id": "cls",    "name": "财联社",   "full": "财联社",
     "url": "https://www.cls.cn/", "paywall": False},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def clean_text(text):
    """清理文本，去除多余空白和HTML标签"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def strip_html_tags(text):
    """去除HTML标签"""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text)


def fetch_rss(source):
    """抓取单个RSS源"""
    articles = []
    try:
        resp = requests.get(source["rss"], headers=HEADERS, timeout=15)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        for entry in feed.entries[:10]:
            title = clean_text(entry.get("title", ""))
            if not title or len(title) < 5:
                continue
            link = entry.get("link", "")
            published = entry.get("published", "") or entry.get("updated", "")
            description = clean_text(entry.get("summary", "") or entry.get("description", ""))
            if len(description) > 600:
                description = description[:600] + "..."
            # 提取图片
            image = ""
            if hasattr(entry, "media_content") and entry.media_content:
                image = entry.media_content[0].get("url", "")
            elif hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
                image = entry.media_thumbnail[0].get("url", "")
            articles.append({
                "title": title,
                "link": link,
                "pubDate": published,
                "description": description,
                "image": image,
                "sourceId": source["id"],
                "sourceName": source["name"],
                "sourceFull": source["full"],
                "paywall": source.get("paywall", False),
            })
    except Exception as e:
        print(f"  [WARN] 抓取 {source['name']} 失败: {e}")
    return articles


def fetch_article_description(url):
    """抓取文章详情页，提取摘要"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=8)
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        # 优先取 og:description
        desc = ""
        og_tag = soup.find("meta", property="og:description")
        if og_tag and og_tag.get("content"):
            desc = og_tag["content"].strip()
        if not desc:
            meta_tag = soup.find("meta", attrs={"name": "description"})
            if meta_tag and meta_tag.get("content"):
                desc = meta_tag["content"].strip()
        if not desc:
            # 尝试取正文段落
            paragraphs = soup.find_all("p")
            for p in paragraphs:
                text = clean_text(p.get_text())
                if len(text) > 30:
                    desc = text
                    break
        desc = clean_text(desc)
        if len(desc) > 500:
            desc = desc[:500] + "..."
        return desc
    except Exception:
        return ""


def fetch_cn_site(source):
    """抓取国内财经网站首页新闻，并提取摘要"""
    articles = []
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=15)
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        # 广告/推广/导航过滤关键词
        ad_keywords = [
            "登录", "注册", "首页", "更多", "视频", "图片", "专题", "客户端", "下载",
            "关于我们", "联系方式", "广告", "推广", "赞助", "软文", "商业推广",
            "优惠", "促销", "热卖", "热销", "爆款", "抢购", "秒杀", "打折",
            "招商", "加盟", "代理", "开户", "配资", "理财课", "炒股", "牛股", "涨停",
            "优惠券", "红包", "福利", "礼包", "奖品", "抽奖",
            "点击进入", "立即购买", "马上抢购", "立即体验", "点击领取", "免费领取",
            "游戏", "彩票", "直播", "带货", "主播",
            "品牌专区", "新品发布", "产品中心", "解决方案",
        ]
        # 新闻详情页URL特征
        news_url_keywords = ["/news", "/article", "/detail", "/content", "/story"]

        seen_titles = set()
        for a_tag in soup.find_all("a", href=True):
            title = clean_text(a_tag.get_text())
            href = a_tag["href"]
            if (not title or len(title) < 8 or len(title) > 80
                    or title in seen_titles
                    or any(kw in title for kw in ad_keywords)):
                continue
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = source["url"].rstrip("/") + href
            elif not href.startswith("http"):
                continue
            # URL过滤：只保留新闻详情页（含数字ID或新闻关键词，且路径有足够深度）
            href_path = href.split("?")[0].split("#")[0]
            has_digit = any(c.isdigit() for c in href_path)
            has_news_kw = any(p in href_path.lower() for p in news_url_keywords)
            path_parts = [p for p in href_path.rstrip("/").split("/") if p]
            if not (has_digit or has_news_kw) or len(path_parts) <= 2:
                continue

            seen_titles.add(title)
            articles.append({
                "title": title,
                "link": href,
                "pubDate": datetime.now(CST).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                "description": "",
                "image": "",
                "sourceId": source["id"],
                "sourceName": source["name"],
                "sourceFull": source["full"],
                "paywall": source.get("paywall", False),
            })
            if len(articles) >= 8:
                break

        # 对前5条并发提取摘要
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_idx = {}
            for i, art in enumerate(articles[:5]):
                future = executor.submit(fetch_article_description, art["link"])
                future_to_idx[future] = i
            for future in as_completed(future_to_idx, timeout=25):
                idx = future_to_idx[future]
                try:
                    articles[idx]["description"] = future.result()
                except Exception:
                    pass
    except Exception as e:
        print(f"  [WARN] 抓取 {source['name']} 失败: {e}")
    return articles


def fetch_all_news():
    """抓取所有新闻"""
    all_articles = []

    print("=== 抓取国外财经媒体 RSS ===")
    for src in GLOBAL_SOURCES:
        print(f"  正在抓取 {src['name']}...")
        items = fetch_rss(src)
        print(f"    获取 {len(items)} 条")
        all_articles.extend(items)

    print("\n=== 抓取国内财经媒体 ===")
    for src in CN_SOURCES:
        print(f"  正在抓取 {src['name']}...")
        items = fetch_cn_site(src)
        print(f"    获取 {len(items)} 条")
        all_articles.extend(items)

    # 按时间排序（有时间的排前面）
    def sort_key(a):
        try:
            return datetime.fromisoformat(a["pubDate"].replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0
    all_articles.sort(key=sort_key, reverse=True)

    print(f"\n共获取 {len(all_articles)} 条新闻")
    return all_articles


def generate_html(articles):
    """生成完整HTML页面"""
    now = datetime.now(CST)
    now_str = f"{now.year}年{now.month}月{now.day}日"
    weekdays = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"]
    weekday = weekdays[now.weekday()]
    date_display = f"{now_str} · {weekday}"
    update_time = now.strftime("%Y-%m-%d %H:%M")

    # 生成新闻数据JS
    articles_js = json.dumps(articles, ensure_ascii=False, indent=2)

    # 来源标签
    all_sources = GLOBAL_SOURCES + CN_SOURCES
    source_tags_html = ""
    for s in all_sources:
        source_tags_html += f'<button class="source-tag" onclick="setSource(\'{s["id"]}\')">{s["name"]}</button>\n'

    html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>全球经济日报 · Global Economic Digest</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' fill='%231B365D'/><text x='50' y='68' font-size='52' font-family='serif' fill='%23FAFAF8' text-anchor='middle' font-weight='bold'>E</text></svg>">
<link rel="stylesheet" href="https://miaoda.feishu.cn/fonts/css2?family=Noto+Serif+SC:wght@400;600;700;900&family=Noto+Sans+SC:wght@300;400;500;700&display=swap">
<style>
:root {{
  --bg: #F2EDE4; --bg-card: #FFFFFF; --ink: #1A1A1A; --ink-soft: #4A4A4A;
  --ink-muted: #8A8A8A; --line: #D9D3C7; --line-soft: #E5E0D6;
  --accent: #1B365D; --accent-light: #2C5282; --accent-bg: #E8E2D6;
  --serif: 'Noto Serif SC', 'Songti SC', 'SimSun', Georgia, serif;
  --sans: 'Noto Sans SC', -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  --maxw: 1200px;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: var(--sans); background: var(--bg); color: var(--ink); line-height: 1.7; font-size: 15px; }}
a {{ color: inherit; text-decoration: none; }}
img {{ max-width: 100%; display: block; }}
button {{ font-family: inherit; cursor: pointer; border: none; background: none; }}

.masthead {{ border-bottom: 2px solid var(--ink); padding: 28px 24px 20px; text-align: center; }}
.masthead-inner {{ max-width: var(--maxw); margin: 0 auto; }}
.masthead-title {{ font-family: var(--serif); font-size: clamp(28px, 5vw, 44px); font-weight: 900; letter-spacing: 0.04em; line-height: 1.2; }}
.masthead-sub {{ font-family: var(--serif); font-size: 13px; color: var(--ink-muted); letter-spacing: 0.15em; margin-top: 6px; text-transform: uppercase; }}
.masthead-meta {{ display: flex; justify-content: space-between; align-items: center; margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--line); font-size: 13px; color: var(--ink-soft); }}
.masthead-date {{ font-family: var(--serif); font-weight: 600; }}

.filter-bar {{ border-bottom: 1px solid var(--line); padding: 14px 24px; background: var(--bg); position: sticky; top: 0; z-index: 100; backdrop-filter: blur(8px); }}
.filter-inner {{ max-width: var(--maxw); margin: 0 auto; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
.source-tags {{ display: flex; gap: 6px; flex-wrap: wrap; flex: 1; }}
.source-tag {{ padding: 5px 12px; font-size: 12px; font-weight: 500; border: 1px solid var(--line); border-radius: 2px; color: var(--ink-soft); transition: all 0.15s; background: var(--bg-card); white-space: nowrap; }}
.source-tag:hover {{ border-color: var(--accent); color: var(--accent); }}
.source-tag.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
.search-box {{ position: relative; flex-shrink: 0; }}
.search-box input {{ width: 200px; padding: 7px 12px 7px 34px; border: 1px solid var(--line); border-radius: 2px; font-size: 13px; font-family: var(--sans); background: var(--bg-card); color: var(--ink); outline: none; }}
.search-box input:focus {{ border-color: var(--accent); }}
.search-box svg {{ position: absolute; left: 10px; top: 50%; transform: translateY(-50%); width: 15px; height: 15px; color: var(--ink-muted); }}

.main {{ max-width: var(--maxw); margin: 0 auto; padding: 32px 24px 60px; }}
.section-label {{ font-family: var(--serif); font-size: 12px; font-weight: 700; letter-spacing: 0.2em; text-transform: uppercase; color: var(--accent); margin-bottom: 16px; padding-bottom: 8px; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 8px; }}
.section-label::after {{ content: ''; flex: 1; height: 1px; background: var(--line); }}

.featured-section {{ margin-bottom: 36px; }}
.featured-card {{ display: grid; grid-template-columns: 1fr; gap: 24px; padding-bottom: 28px; border-bottom: 1px solid var(--line); }}
@media (min-width: 768px) {{ .featured-card {{ grid-template-columns: 1.2fr 1fr; align-items: start; }} }}
.featured-content h2 {{ font-family: var(--serif); font-size: clamp(22px, 3vw, 30px); font-weight: 700; line-height: 1.35; margin-bottom: 12px; }}
.featured-content h2 a:hover {{ color: var(--accent); }}
.featured-meta {{ display: flex; align-items: center; gap: 12px; font-size: 12px; color: var(--ink-muted); margin-bottom: 14px; }}
.featured-meta .source-badge {{ font-weight: 700; color: var(--accent); font-family: var(--serif); }}
.featured-desc {{ font-size: 15px; color: var(--ink-soft); line-height: 1.8; display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }}
.featured-image {{ width: 100%; aspect-ratio: 16/10; object-fit: cover; border-radius: 2px; background: var(--line-soft); }}

.news-grid {{ display: grid; grid-template-columns: 1fr; gap: 0; }}
@media (min-width: 600px) {{ .news-grid {{ grid-template-columns: repeat(2, 1fr); }} }}
@media (min-width: 900px) {{ .news-grid {{ grid-template-columns: repeat(3, 1fr); }} }}
.news-card {{ padding: 24px 20px; border-bottom: 1px solid var(--line-soft); transition: background 0.15s; cursor: pointer; }}
@media (min-width: 600px) {{ .news-card {{ border-right: 1px solid var(--line-soft); }} .news-card:nth-child(2n) {{ border-right: none; }} }}
@media (min-width: 900px) {{ .news-card:nth-child(2n) {{ border-right: 1px solid var(--line-soft); }} .news-card:nth-child(3n) {{ border-right: none; }} }}
.news-card:hover {{ background: var(--accent-bg); }}
.news-card .card-source {{ font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); margin-bottom: 8px; font-family: var(--serif); display: flex; align-items: center; gap: 8px; }}
.paywall-badge {{ display: inline-block; font-size: 10px; font-weight: 500; color: #B45309; background: #FEF3C7; border: 1px solid #FDE68A; padding: 1px 6px; border-radius: 2px; letter-spacing: 0.05em; text-transform: none; font-family: var(--sans); }}
.news-card h3 {{ font-family: var(--serif); font-size: 17px; font-weight: 600; line-height: 1.45; margin-bottom: 10px; }}
.news-card h3 a:hover {{ color: var(--accent); }}
.news-card .card-desc {{ font-size: 13px; color: var(--ink-soft); line-height: 1.7; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; margin-bottom: 12px; }}
.news-card .card-time {{ font-size: 12px; color: var(--ink-muted); }}
.news-card .card-detail {{ display: none; margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--line); }}
.news-card.expanded .card-detail {{ display: block; }}
.news-card .card-detail p {{ font-size: 14px; color: var(--ink-soft); line-height: 1.8; margin-bottom: 12px; }}
.news-card .read-original {{ display: inline-block; font-size: 13px; color: var(--accent); font-weight: 500; text-decoration: underline; text-underline-offset: 2px; }}
.news-card .read-original:hover {{ color: var(--accent-light); }}
.news-card .expand-hint {{ font-size: 12px; color: var(--ink-muted); margin-top: 8px; }}
.news-card.expanded .expand-hint {{ display: none; }}

.empty-state {{ text-align: center; padding: 60px 20px; color: var(--ink-muted); }}
.footer {{ border-top: 2px solid var(--ink); padding: 28px 24px; text-align: center; font-size: 12px; color: var(--ink-muted); }}
.footer p {{ margin-bottom: 4px; }}

@media (max-width: 600px) {{
  .masthead {{ padding: 20px 16px 16px; }}
  .masthead-meta {{ flex-direction: column; gap: 10px; }}
  .filter-bar {{ padding: 10px 16px; }}
  .search-box input {{ width: 100%; font-size: 16px; }}
  .search-box {{ width: 100%; order: -1; }}
  .main {{ padding: 20px 16px 40px; }}
  .news-card {{ padding: 20px 16px; }}
}}
</style>
</head>
<body>

<header class="masthead">
  <div class="masthead-inner">
    <h1 class="masthead-title">全球经济日报</h1>
    <p class="masthead-sub">Global Economic Daily Digest</p>
    <div class="masthead-meta">
      <span class="masthead-date">{date_display}</span>
      <span style="font-size:12px;color:var(--ink-muted);">云端自动更新 · {update_time}</span>
    </div>
  </div>
</header>

<nav class="filter-bar">
  <div class="filter-inner">
    <div class="source-tags" id="sourceTags">
      <button class="source-tag active" onclick="setSource('all')">全部</button>
      {source_tags_html}
    </div>
    <div class="search-box">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
      <input type="text" id="searchInput" placeholder="搜索新闻关键词..." oninput="handleSearch(this.value)">
    </div>
  </div>
</nav>

<main class="main" id="mainContent"></main>

<footer class="footer">
  <p>国际来源：Reuters · Financial Times · The Economist · NYT Business · BBC Business · CNBC · NPR</p>
  <p>国内来源：财新网 · 第一财经 · 证券时报 · 财联社</p>
  <p style="margin-top:8px;">由 GitHub Actions 每日云端自动更新 · 内容版权归原媒体所有</p>
</footer>

<script>
const ALL_ARTICLES = {articles_js};
let activeSource = 'all';
let searchKeyword = '';

function formatTime(dateStr) {{
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return '';
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return Math.floor(diff/60) + '分钟前';
  if (diff < 86400) return Math.floor(diff/3600) + '小时前';
  return (d.getMonth()+1) + '/' + d.getDate();
}}

function escapeHtml(str) {{
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}}

function renderSourceTags() {{
  const container = document.getElementById('sourceTags');
  const allTag = '<button class="source-tag ' + (activeSource==='all'?'active':'') + '" onclick="setSource(\\'all\\')">全部</button>';
  const sources = {json.dumps([{"id": s["id"], "name": s["name"]} for s in all_sources], ensure_ascii=False)};
  const tags = sources.map(s => '<button class="source-tag ' + (activeSource===s.id?'active':'') + '" onclick="setSource(\\'' + s.id + '\\')">' + s.name + '</button>').join('');
  container.innerHTML = allTag + tags;
}}

function getFilteredArticles() {{
  let list = ALL_ARTICLES;
  if (activeSource !== 'all') list = list.filter(a => a.sourceId === activeSource);
  if (searchKeyword) {{
    const kw = searchKeyword.toLowerCase();
    list = list.filter(a => a.title.toLowerCase().includes(kw) || (a.description||'').toLowerCase().includes(kw));
  }}
  return list;
}}

function renderAll() {{
  renderSourceTags();
  const articles = getFilteredArticles();
  const main = document.getElementById('mainContent');
  if (articles.length === 0) {{
    main.innerHTML = '<div class="empty-state"><p>没有找到匹配的新闻</p></div>';
    return;
  }}
  const featured = articles[0];
  const rest = articles.slice(1);
  let html = '<section class="featured-section"><div class="section-label">今日头条 · Top Story</div><article class="featured-card"><div class="featured-content">';
  html += '<h2><a href="' + featured.link + '" target="_blank" rel="noopener">' + escapeHtml(featured.title) + '</a></h2>';
  html += '<div class="featured-meta"><span class="source-badge">' + featured.sourceName + '</span><span>' + formatTime(featured.pubDate) + '</span></div>';
  if (featured.description) html += '<p class="featured-desc">' + escapeHtml(featured.description) + '</p>';
  html += '</div>';
  if (featured.image) html += '<img class="featured-image" src="' + featured.image + '" alt="" loading="lazy" onerror="this.style.display=\\'none\\'">';
  html += '</article></section>';
  html += '<section><div class="section-label">最新资讯 · Latest</div><div class="news-grid">';
  rest.forEach((a, idx) => {{
    const cardId = 'card-' + idx;
    const isPaywall = a.paywall === true;
    if (isPaywall) {{
      // 付费媒体：点击直接跳转
      html += '<article class="news-card" onclick="window.open(\\'' + a.link + '\\',\\'_blank\\')" style="cursor:pointer;">';
    }} else {{
      // 免费媒体：点击展开正文
      html += '<article class="news-card" id="' + cardId + '" onclick="toggleExpand(\\'' + cardId + '\\')">';
    }}
    html += '<div class="card-source">' + a.sourceName;
    if (isPaywall) html += '<span class="paywall-badge">付费</span>';
    html += '</div>';
    if (isPaywall) {{
      // 付费媒体：标题是链接，点击跳转
      html += '<h3><a href="' + a.link + '" target="_blank" rel="noopener" onclick="event.stopPropagation()">' + escapeHtml(a.title) + '</a></h3>';
    }} else {{
      // 免费媒体：标题不是链接，点击卡片展开
      html += '<h3 style="cursor:pointer;">' + escapeHtml(a.title) + '</h3>';
    }}
    if (a.description) html += '<p class="card-desc">' + escapeHtml(a.description) + '</p>';
    html += '<div class="card-time">' + formatTime(a.pubDate) + '</div>';
    if (!isPaywall) {{
      html += '<div class="expand-hint">点击展开阅读全文 ↓</div>';
      html += '<div class="card-detail">';
      if (a.description) {{
        html += '<p>' + escapeHtml(a.description) + '</p>';
      }} else {{
        html += '<p style="color:var(--ink-muted);">该新闻暂无摘要，点击下方链接阅读原文。</p>';
      }}
      html += '<a class="read-original" href="' + a.link + '" target="_blank" rel="noopener" onclick="event.stopPropagation()">阅读原文 →</a>';
      html += '</div>';
    }}
    html += '</article>';
  }});
  html += '</div></section>';
  main.innerHTML = html;
}}

function toggleExpand(cardId) {{
  const card = document.getElementById(cardId);
  if (card) card.classList.toggle('expanded');
}}

function setSource(id) {{ activeSource = id; renderAll(); }}
function handleSearch(val) {{ searchKeyword = val.trim(); renderAll(); }}
renderAll();
</script>
</body>
</html>'''
    return html_content


def main():
    print("=" * 50)
    print("全球经济日报 - 新闻抓取与生成")
    print(f"运行时间: {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    articles = fetch_all_news()
    if not articles:
        print("[ERROR] 未获取到任何新闻，退出")
        return

    html_content = generate_html(articles)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"\n[OK] HTML 已生成: {OUTPUT_FILE}")
    print(f"     文件大小: {os.path.getsize(OUTPUT_FILE) / 1024:.1f} KB")


if __name__ == "__main__":
    main()
