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
ARTICLES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "articles")
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

    # 免费媒体且摘要太短时，补抓正文
    if not source.get("paywall", False):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        need_fetch = [i for i, a in enumerate(articles)
                      if len(a.get("description", "")) < 80 or not a.get("image")]
        if need_fetch:
            with ThreadPoolExecutor(max_workers=5) as executor:
                future_to_idx = {}
                for i in need_fetch:
                    future = executor.submit(fetch_article_description, articles[i]["link"])
                    future_to_idx[future] = i
                for future in as_completed(future_to_idx, timeout=30):
                    idx = future_to_idx[future]
                    try:
                        full_text, img_url = future.result()
                        if len(full_text) > len(articles[idx].get("description", "")):
                            articles[idx]["description"] = full_text
                        if img_url and not articles[idx].get("image"):
                            articles[idx]["image"] = img_url
                    except Exception:
                        pass
    return articles


def fetch_article_description(url):
    """抓取文章详情页，提取完整正文"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        # 移除无关元素
        for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside", "iframe"]):
            tag.decompose()
        # 尝试识别正文容器
        content = None
        content_selectors = [
            "article", ".article-content", ".article-body", ".content",
            "#content", ".post-content", ".entry-content", ".text",
            ".detail-content", ".news-content", ".article-detail",
            ".main-content", "#article-content", ".articleCont",
            ".art_content", ".article_con", ".content_main", ".txt"
        ]
        for selector in content_selectors:
            try:
                content = soup.select_one(selector)
                if content and len(content.find_all("p")) >= 2:
                    break
            except Exception:
                continue
        if not content:
            content = soup
        # 提取所有有效段落
        paragraphs = content.find_all("p")
        texts = []
        skip_keywords = ["版权所有", "免责声明", "本文来源", "点击查看", "责任编辑",
                         "扫一扫", "关注我们", "推荐阅读", "相关阅读", "原文链接",
                         "欢迎关注", "微信公众号", "APP下载", "客户端", "举报"]
        for p in paragraphs:
            text = clean_text(p.get_text())
            if len(text) > 15 and not any(kw in text for kw in skip_keywords):
                texts.append(text)
        desc = "\n\n".join(t for t in texts if t.strip())
        # 正文太短时回退到meta摘要
        if len(desc) < 40:
            og_tag = soup.find("meta", property="og:description")
            if og_tag and og_tag.get("content"):
                desc = og_tag["content"].strip()
            if not desc:
                meta_tag = soup.find("meta", attrs={"name": "description"})
                if meta_tag and meta_tag.get("content"):
                    desc = meta_tag["content"].strip()
        if len(desc) > 12000:
            desc = desc[:12000] + "..."

        # 提取配图：优先 og:image，其次 twitter:image，最后正文第一张图
        article_image = ""
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            article_image = og_img["content"].strip()
        if not article_image:
            tw_img = soup.find("meta", attrs={"name": "twitter:image"})
            if tw_img and tw_img.get("content"):
                article_image = tw_img["content"].strip()
        if not article_image and content:
            first_img = content.find("img")
            if first_img and first_img.get("src"):
                article_image = first_img["src"].strip()
        # 补全图片URL
        if article_image.startswith("//"):
            article_image = "https:" + article_image
        elif article_image.startswith("/"):
            m = re.match(r"https?://[^/]+", url)
            if m:
                article_image = m.group(0) + article_image
        # 过滤默认图、logo、分享占位图
        bad_img_patterns = ["shareimg", "logo", "default", "placeholder", "icon", "avatar", "telegraph-logo", "/common/"]
        if any(p in article_image.lower() for p in bad_img_patterns):
            article_image = ""
        return (desc, article_image)
    except Exception:
        return ("", "")


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

        # 对所有新闻并发提取正文
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_to_idx = {}
            for i, art in enumerate(articles):
                future = executor.submit(fetch_article_description, art["link"])
                future_to_idx[future] = i
            for future in as_completed(future_to_idx, timeout=40):
                idx = future_to_idx[future]
                try:
                    desc_text, img_url = future.result()
                    if desc_text:
                        articles[idx]["description"] = desc_text
                    if img_url:
                        articles[idx]["image"] = img_url
                except Exception:
                    pass
    except Exception as e:
        print(f"  [WARN] 抓取 {source['name']} 失败: {e}")
    return articles


def calculate_article_score(article, now=None):
    """计算新闻重要性评分，分数越高越重要、越靠前"""
    if now is None:
        now = datetime.now(CST)
    score = 0
    title = article.get("title", "")
    desc = article.get("description", "")

    # 1. 政策/监管/央行动向（最高权重）
    policy_kw = [
        ("国务院", 15), ("中共中央", 15), ("国常会", 13), ("政治局", 13),
        ("国家主席", 12), ("习近平", 12), ("美联储", 12), ("总理", 10),
        ("央行", 10), ("证监会", 10), ("财政部", 10), ("欧洲央行", 10),
        ("发改委", 9), ("银保监", 9), ("Fed", 9), ("特朗普", 8),
        ("利率", 8), ("关税", 8), ("制裁", 8), ("加息", 9), ("降息", 9),
        ("改革", 7), ("决议", 7), ("法案", 7), ("货币政策", 9),
        ("财政政策", 9), ("监管", 6), ("规划", 6), ("条例", 6),
        ("政策", 6), ("意见", 5), ("总统", 6),
    ]
    for kw, weight in policy_kw:
        if kw in title:
            score += weight
        elif kw in desc:
            score += weight * 0.3

    # 2. 重大事件
    major_kw = [
        ("出台", 7), ("重磅", 8), ("重大", 7), ("突发", 7),
        ("历史性", 7), ("熔断", 8), ("崩盘", 8), ("暴跌", 7),
        ("暴涨", 7), ("危机", 7), ("紧急", 6), ("战争", 7),
        ("衰退", 6), ("破产", 6), ("违约", 6), ("停火", 6),
        ("选举", 6), ("峰会", 6), ("首次", 5), ("冲突", 5),
        ("签署", 5), ("谈判", 5), ("并购", 5), ("袭击", 5),
        ("发布", 4), ("宣布", 4), ("最大", 4), ("最高", 4),
    ]
    for kw, weight in major_kw:
        if kw in title:
            score += weight
        elif kw in desc:
            score += weight * 0.3

    # 3. 经济指标/市场
    econ_kw = [
        ("非农", 8), ("GDP", 8), ("CPI", 7), ("PPI", 7),
        ("通胀", 7), ("通缩", 7), ("IPO", 6), ("就业", 5),
        ("汇率", 5), ("人民币", 5), ("楼市", 5), ("房地产", 5),
        ("贸易", 5), ("原油", 5), ("增长", 4), ("出口", 4),
        ("进口", 4), ("美元", 4), ("黄金", 4), ("股市", 4),
        ("债券", 4), ("上市", 4), ("净利润", 4), ("指数", 3),
    ]
    for kw, weight in econ_kw:
        if kw in title:
            score += weight
        elif kw in desc:
            score += weight * 0.3

    # 4. 时效性（越新分越高）
    pub_str = article.get("pubDate", "")
    if pub_str:
        try:
            pub = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=CST)
            hours_ago = max(0, (now - pub).total_seconds() / 3600)
            if hours_ago < 1:
                score += 10
            elif hours_ago < 3:
                score += 8
            elif hours_ago < 6:
                score += 6
            elif hours_ago < 12:
                score += 4
            elif hours_ago < 24:
                score += 2
            elif hours_ago < 48:
                score += 1
        except Exception:
            score += 3

    # 5. 正文完整度（有完整正文通常意味着是重点报道）
    desc_len = len(desc)
    if desc_len > 800:
        score += 5
    elif desc_len > 300:
        score += 3
    elif desc_len > 100:
        score += 1

    return score


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

    # 按重要性评分排序（重大政策/事件靠前，分数相同时新的靠前）
    now = datetime.now(CST)
    def sort_key(a):
        try:
            ts = datetime.fromisoformat(a["pubDate"].replace("Z", "+00:00")).timestamp()
        except Exception:
            ts = 0
        return (calculate_article_score(a, now), ts)
    all_articles.sort(key=sort_key, reverse=True)

    # 打印评分前5的新闻，便于确认排序效果
    print("\n重要性评分 Top 5：")
    for a in all_articles[:5]:
        print(f"  [{calculate_article_score(a, now):.0f}分] {a['sourceName']} - {a['title'][:40]}")

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
.news-card .card-thumb {{ width: 100%; aspect-ratio: 16/9; object-fit: cover; border-radius: 2px; margin-bottom: 14px; background: var(--line-soft); }}
.reading-view .reading-image {{ width: 100%; aspect-ratio: 16/9; object-fit: cover; border-radius: 2px; margin-bottom: 32px; background: var(--line-soft); }}

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
.news-card .read-hint {{ font-size: 12px; color: var(--ink-muted); margin-top: 8px; }}
.news-card .read-original {{ display: inline-block; font-size: 13px; color: var(--accent); font-weight: 500; text-decoration: underline; text-underline-offset: 2px; }}
.news-card .read-original:hover {{ color: var(--accent-light); }}

/* 独立阅读视图 */
.reading-view {{ max-width: 760px; margin: 0 auto; padding: 40px 24px 60px; }}
.reading-view .back-btn {{ display: inline-flex; align-items: center; gap: 6px; background: none; border: 1px solid var(--line); color: var(--ink-soft); font-size: 13px; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-family: var(--sans); margin-bottom: 32px; transition: all 0.2s; }}
.reading-view .back-btn:hover {{ background: var(--accent-bg); border-color: var(--accent); color: var(--accent); }}
.reading-view .reading-meta {{ display: flex; align-items: center; gap: 12px; margin-bottom: 20px; font-size: 13px; color: var(--ink-muted); }}
.reading-view .reading-meta .source-badge {{ font-weight: 700; color: var(--accent); font-family: var(--serif); letter-spacing: 0.05em; }}
.reading-view h1.reading-title {{ font-family: var(--serif); font-size: 36px; font-weight: 700; line-height: 1.4; color: var(--ink); margin-bottom: 28px; letter-spacing: -0.01em; }}
.reading-view .reading-content {{ font-size: 18px; line-height: 2; color: var(--ink-soft); margin-bottom: 36px; }}
.reading-view .reading-content p {{ margin-bottom: 18px; }}
.reading-view .no-desc {{ color: var(--ink-muted); font-style: italic; }}

.update-banner {{ display: none; background: var(--accent); color: #fff; text-align: center; padding: 11px 16px; font-size: 14px; cursor: pointer; letter-spacing: 0.5px; position: sticky; top: 0; z-index: 200; }}
.update-banner.show {{ display: block; }}
.update-banner:hover {{ background: var(--accent-light); }}

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
  .reading-view {{ padding: 24px 16px 48px; }}
  .reading-view h1.reading-title {{ font-size: 28px; }}
  .reading-view .reading-content {{ font-size: 17px; line-height: 1.9; }}
}}
</style>
</head>
<body>

<div id="updateBanner" class="update-banner" onclick="location.reload()">
  <span>有新的财经资讯，点击刷新</span>
</div>

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

<div class="reading-view" id="readingView" style="display:none;"></div>

<footer class="footer">
  <p>国际来源：Reuters · Financial Times · The Economist · NYT Business · BBC Business · CNBC · NPR</p>
  <p>国内来源：财新网 · 第一财经 · 证券时报 · 财联社</p>
  <p style="margin-top:8px;">由 GitHub Actions 每小时云端自动更新 · 内容版权归原媒体所有</p>
</footer>

<script>
const ALL_ARTICLES = {articles_js};
const BUILD_TIME = "{datetime.now(CST).isoformat()}";
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
  const featuredPaywall = featured.paywall === true;
  if (featuredPaywall) {{
    html = '<section class="featured-section"><div class="section-label">今日头条 · Top Story</div><article class="featured-card" onclick="window.open(\\'' + featured.link + '\\',\\'_blank\\')" style="cursor:pointer;"><div class="featured-content">';
    html += '<h2><a href="' + featured.link + '" target="_blank" rel="noopener" onclick="event.stopPropagation()">' + escapeHtml(featured.title) + '</a></h2>';
  }} else {{
    html = '<section class="featured-section"><div class="section-label">今日头条 · Top Story</div><article class="featured-card" onclick="openArticle(-1)" style="cursor:pointer;"><div class="featured-content">';
    html += '<h2>' + escapeHtml(featured.title) + '</h2>';
  }}
  html += '<div class="featured-meta"><span class="source-badge">' + featured.sourceName + '</span>';
  if (featuredPaywall) html += '<span class="paywall-badge">付费</span>';
  html += '<span>' + formatTime(featured.pubDate) + '</span></div>';
  if (featured.description) html += '<p class="featured-desc">' + escapeHtml(featured.description) + '</p>';
  if (!featuredPaywall) html += '<div class="read-hint" style="margin-top:16px;">点击阅读全文 →</div>';
  html += '</div>';
  if (featured.image) html += '<img class="featured-image" src="' + featured.image + '" alt="" loading="lazy" onerror="this.style.display=\\'none\\'">';
  html += '</article></section>';
  html += '<section><div class="section-label">最新资讯 · Latest</div><div class="news-grid">';
  rest.forEach((a, idx) => {{
    const isPaywall = a.paywall === true;
    if (isPaywall) {{
      // 付费媒体：点击直接跳转
      html += '<article class="news-card" onclick="window.open(\\'' + a.link + '\\',\\'_blank\\')" style="cursor:pointer;">';
    }} else {{
      // 免费媒体：点击进入独立阅读页
      html += '<article class="news-card" onclick="openArticle(' + idx + ')" style="cursor:pointer;">';
    }}
    if (a.image) html += '<img class="card-thumb" src="' + a.image + '" alt="" loading="lazy" onerror="this.style.display=\\'none\\'">';
    html += '<div class="card-source">' + a.sourceName;
    if (isPaywall) html += '<span class="paywall-badge">付费</span>';
    html += '</div>';
    if (isPaywall) {{
      html += '<h3><a href="' + a.link + '" target="_blank" rel="noopener" onclick="event.stopPropagation()">' + escapeHtml(a.title) + '</a></h3>';
    }} else {{
      html += '<h3>' + escapeHtml(a.title) + '</h3>';
    }}
    if (a.description) html += '<p class="card-desc">' + escapeHtml(a.description) + '</p>';
    html += '<div class="card-time">' + formatTime(a.pubDate) + '</div>';
    if (!isPaywall) {{
      html += '<div class="read-hint">点击阅读全文 →</div>';
    }}
    html += '</article>';
  }});
  html += '</div></section>';
  main.innerHTML = html;
}}

function renderReadingShell(a, bodyHtml) {{
  let h = '<button class="back-btn" onclick="closeArticle()">← 返回列表</button>';
  h += '<div class="reading-meta"><span class="source-badge">' + a.sourceName + '</span><span>' + formatTime(a.pubDate) + '</span></div>';
  h += '<h1 class="reading-title">' + escapeHtml(a.title) + '</h1>';
  if (a.image) h += '<img class="reading-image" src="' + a.image + '" alt="" loading="lazy" onerror="this.style.display=\\'none\\'">';
  h += '<div class="reading-content">' + bodyHtml + '</div>';
  if (a.link) h += '<a class="read-original" href="' + a.link + '" target="_blank" rel="noopener">阅读原文 →</a>';
  return h;
}}

async function openArticle(idx) {{
  const articles = getFilteredArticles();
  const a = articles[idx + 1]; // +1 因为第一条是featured
  if (!a) return;
  const view = document.getElementById('readingView');
  // 先显示标题和加载状态
  view.innerHTML = renderReadingShell(a, '<p class="no-desc">正文加载中…</p>');
  document.getElementById('mainContent').style.display = 'none';
  view.style.display = 'block';
  window.scrollTo(0, 0);
  try {{
    const resp = await fetch('articles/' + a.articleId + '.json');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const full = await resp.json();
    let body = '';
    const paras = (full.description || '').split(/\\n+/).filter(p => p.trim());
    if (paras.length) {{
      paras.forEach(p => {{ body += '<p>' + escapeHtml(p.trim()) + '</p>'; }});
    }} else {{
      body = '<p class="no-desc">该新闻暂无摘抄摘要，您可以点击下方链接阅读原文。</p>';
    }}
    view.innerHTML = renderReadingShell(full, body);
  }} catch (e) {{
    view.innerHTML = renderReadingShell(a, '<p class="no-desc">正文加载失败，您可以点击下方链接直接阅读原文。</p>');
  }}
}}

function closeArticle() {{
  document.getElementById('readingView').style.display = 'none';
  document.getElementById('mainContent').style.display = 'block';
}}

function setSource(id) {{ activeSource = id; renderAll(); }}
function handleSearch(val) {{ searchKeyword = val.trim(); renderAll(); }}

// 定时检测云端是否有新版本（每10分钟），有则温和提示，不强制刷新
async function checkForUpdates() {{
  try {{
    const resp = await fetch('update.json?_=' + Date.now());
    if (!resp.ok) return;
    const data = await resp.json();
    if (data.buildIso && data.buildIso !== BUILD_TIME) {{
      document.getElementById('updateBanner').classList.add('show');
    }}
  }} catch (e) {{}}
}}
setInterval(checkForUpdates, 10 * 60 * 1000);
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

    # 1. 为每篇文章生成独立JSON（含完整正文），并准备轻量列表数据
    os.makedirs(ARTICLES_DIR, exist_ok=True)
    list_articles = []
    for i, art in enumerate(articles):
        article_id = f"a{i}"
        # 完整内容写入独立文件
        full_data = dict(art)
        full_data["articleId"] = article_id
        with open(os.path.join(ARTICLES_DIR, f"{article_id}.json"), "w", encoding="utf-8") as af:
            json.dump(full_data, af, ensure_ascii=False)
        # 列表版本：只保留短摘要（前150字），正文不进列表
        list_art = dict(art)
        list_art["articleId"] = article_id
        short_desc = art.get("description", "")
        if len(short_desc) > 150:
            list_art["description"] = short_desc[:150] + "..."
        list_articles.append(list_art)

    # 清理旧的文章文件（删除本次不存在的）
    keep_files = {f"a{i}.json" for i in range(len(articles))}
    for old_file in os.listdir(ARTICLES_DIR):
        if old_file.endswith(".json") and old_file not in keep_files:
            try:
                os.remove(os.path.join(ARTICLES_DIR, old_file))
            except Exception:
                pass

    # 2. 用轻量列表数据生成HTML
    html_content = generate_html(list_articles)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"\n[OK] HTML 已生成: {OUTPUT_FILE}")
    print(f"     列表文件大小: {os.path.getsize(OUTPUT_FILE) / 1024:.1f} KB")
    print(f"     独立文章文件: {len(articles)} 个 (articles/ 目录)")

    # 3. 生成版本文件，供前端检测云端是否有新内容
    build_time = datetime.now(CST)
    version_file = os.path.join(os.path.dirname(OUTPUT_FILE), "update.json")
    with open(version_file, "w", encoding="utf-8") as vf:
        json.dump({
            "buildIso": build_time.isoformat(),
            "buildTime": build_time.strftime("%Y-%m-%d %H:%M:%S"),
            "count": len(articles),
        }, vf, ensure_ascii=False)


if __name__ == "__main__":
    main()
