# 全球经济日报 · Global Economic Digest

一个由 GitHub Actions 每日自动更新的全球财经新闻聚合页面，无需电脑开机，无需外网，通过固定网址随时阅读。

## 功能特点

- **云端每日自动更新**：每天北京时间 07:15 自动抓取最新新闻并更新页面
- **11家权威财经媒体**：
  - 国际：Reuters、Financial Times、The Economist、NYT Business、BBC Business、CNBC、NPR
  - 国内：财新网、第一财经、证券时报、财联社
- **简约高级排版**：报纸杂志风格，衬线标题，暖白纸感背景
- **来源筛选 + 关键词搜索**：快速定位感兴趣的内容
- **响应式设计**：适配电脑和手机

## 部署步骤（约5分钟）

### 1. 创建 GitHub 仓库

1. 登录 [GitHub](https://github.com)
2. 点击右上角 `+` → `New repository`
3. 仓库名随意（如 `economic-digest`），选择 **Public**，勾选 `Add a README file`，点击创建

### 2. 上传文件

1. 进入刚创建的仓库页面
2. 点击 `Add file` → `Upload files`
3. 把以下文件/文件夹拖进去：
   - `index.html`
   - `.github/` 文件夹（包含 `workflows/update.yml`）
   - `scripts/` 文件夹（包含 `fetch_news.py`）
4. 底部点击 `Commit changes`

### 3. 开启 GitHub Pages

1. 仓库页面点击 `Settings`
2. 左侧菜单点击 `Pages`
3. `Source` 选择 `Deploy from a branch`
4. `Branch` 选择 `main`，文件夹选 `/ (root)`，点击 `Save`
5. 等待 1-2 分钟，页面上方会显示你的访问地址：
   `https://你的用户名.github.io/仓库名/`

### 4. 配置 Actions 写权限（重要）

1. 仓库页面点击 `Settings`
2. 左侧菜单点击 `Actions` → `General`
3. 找到 `Workflow permissions`，选择 **Read and write permissions**
4. 点击 `Save`

### 5. 手动触发第一次更新

1. 仓库页面点击 `Actions`
2. 左侧选择 `每日更新经济日报`
3. 点击 `Run workflow` → 再点 `Run workflow`
4. 等待 2-3 分钟完成，刷新你的 Pages 地址即可看到最新内容

之后每天早上 07:15 会自动更新，无需任何操作。

## 访问地址

部署成功后，通过以下地址访问：
```
https://你的GitHub用户名.github.io/仓库名/
```

可以把这个地址添加到浏览器书签，每天打开就是最新的经济日报。

## 文件说明

```
.
├── .github/
│   └── workflows/
│       └── update.yml      # GitHub Actions 定时任务配置
├── scripts/
│   └── fetch_news.py       # 新闻抓取与HTML生成脚本
├── index.html               # 生成的页面（每日自动更新）
└── README.md                # 本说明文档
```

## 自定义

- **修改更新时间**：编辑 `.github/workflows/update.yml` 中的 `cron` 表达式（UTC时间）
- **增删新闻源**：编辑 `scripts/fetch_news.py` 中的 `GLOBAL_SOURCES` 和 `CN_SOURCES`
- **手动更新**：在仓库 `Actions` 页面手动触发工作流

## 注意事项

- 国内新闻通过抓取媒体首页获取，如遇网站结构变化可能暂时抓取失败
- 国际新闻在 GitHub 云端服务器抓取，可正常访问国外网站
- 内容版权归原媒体所有，本项目仅作聚合阅读用途
