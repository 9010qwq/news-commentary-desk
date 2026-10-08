# 第三方与开源选型

核对日期：2026-10-08。没有复制以下竞品的源代码。许可证以实际安装版本随附文本为准；发布二进制时应保留依赖与 Chromium 自带的许可通知。

## 使用的组件

- LocalDesk v0.1：用户提供的旧工具，MIT，Copyright (c) 2026 LocalDesk contributors。本版复用本机工作台思路和访问保护结构，保留根目录原 LICENSE；旧项目/聊天数据库不导入
- [FastAPI](https://github.com/fastapi/fastapi)：MIT，轻量本机 API；[Starlette](https://github.com/encode/starlette)、[Uvicorn](https://github.com/encode/uvicorn)、[HTTPX](https://github.com/encode/httpx)：BSD-3-Clause
- [Playwright Python](https://github.com/microsoft/playwright-python)：Apache-2.0，用于真实浏览器网页截图。[官方 PyInstaller 说明](https://playwright.dev/python/docs/library#pyinstaller)支持便携应用与浏览器打包；本次尚未执行 Windows 构建
- [Trafilatura](https://github.com/adbar/trafilatura)：本包要求 >=1.8，Apache-2.0，用于定向解析失败时的正文回退；它不能自动解决评论栏目的发现问题。旧版本曾使用不同许可证，不复用旧版
- [lxml](https://github.com/lxml/lxml)：BSD 类许可，解析原站 HTML/元数据；其底层库有各自许可
- [openpyxl](https://openpyxl.readthedocs.io/)：MIT，用于应用运行时生成 XLSX
- [keyring](https://github.com/jaraco/keyring)：MIT，仅显式使用 Windows 凭据管理器；不选择明文后端
- SQLite、zipfile、smtplib、zoneinfo：Python 标准库及其底层项目许可；tzdata 用于 Windows IANA 时区支持

## 为什么没有直接套整套平台

- [TrendRadar](https://github.com/sansan0/TrendRadar)：GPL-3.0，已有 RSS/AI/邮件等能力，但未核实原网页截图 + 每日五篇真正时评 + 每周 XLSX/截图 ZIP 的完整流程。大幅 fork 会增加维护和界面负担
- [RSSHub](https://github.com/DIYgod/RSSHub)：当前主分支 AGPL-3.0，不把旧 MIT 印象当作当前许可；可作为未来用户自行配置的外部 RSS 服务，但本包没有复制其路由代码、没有部署它
- [FreshRSS](https://github.com/FreshRSS/FreshRSS)：AGPL-3.0，成熟阅读器，但与本任务的截图归档和邮件双附件流程不同，额外 PHP/服务部署对新手更重
- [Crawl4AI](https://github.com/unclecode/crawl4ai)：当前许可文件含 Apache 正文外的署名要求，本包未引入；这里不需要通用大爬虫框架

本包选择少量成熟组件加五家媒体的小适配器，方便独立本地运行和后续替换。没有把依赖服务的收费或站点访问许可包装成“完全免费无限使用”。
