# 国泰航空积分机票监控 / Cathay Pacific Award Monitor

自动轮询国泰航空积分兑换商务舱机票，香港出发，10月往返，发现可用机票时 Telegram 推送通知。

## 监控路线

| 去程 | 回程（Open-Jaw 支持） |
|------|----------------------|
| HKG → LAX | LAX → HKG |
| HKG → BCN | BCN → HKG |
| HKG → MAD | MAD → HKG（或 BCN → HKG）|
| HKG → PVG | PVG → HKG |

**Open-Jaw 示例**：HKG→马德里去，巴塞罗那回香港，属于有效行程。

## 功能

- 每 30 分钟自动扫描所有路线 × 10 月所有日期
- 商务舱积分票，日期灵活（不限定固定出发/回程日期）
- 去回程独立搜索后组合匹配，支持 Open-Jaw
- 结果存入 SQLite，避免重复通知
- 无 Telegram 配置时打印到控制台

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt
playwright install chromium

# 2. 配置（可选，用于 Telegram 通知）
cp .env.example .env
# 编辑 .env 填入 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID

# 3. 测试能否访问国泰网站
python3 main.py discover

# 4. 跑一次扫描，看结果
python3 main.py scan

# 5. 启动持续轮询
python3 main.py run
```

## 命令

| 命令 | 说明 |
|------|------|
| `python3 main.py run` | 启动持续轮询（默认，每30分钟） |
| `python3 main.py scan` | 执行一次完整扫描后退出 |
| `python3 main.py list` | 查看数据库中已找到的所有组合 |
| `python3 main.py discover` | 探测国泰 API 端点，打印发现的接口信息 |

## 配置说明（`.env`）

```env
TELEGRAM_BOT_TOKEN=    # Telegram Bot Token
TELEGRAM_CHAT_ID=      # 接收通知的 Chat ID
SEARCH_YEAR=2026       # 搜索年份
SEARCH_MONTH=10        # 搜索月份
POLL_INTERVAL_MINUTES=30
HEADLESS=true          # false 可看到浏览器操作过程
```

## 通知格式

```
✈️ 国泰积分商务舱 | HKG→MAD + BCN→HKG

去程: CX239  2026-10-08 (周四)
  出发: HKG 00:10 → MAD 07:30+1
  里程: 85,000 miles | 税费: HKD 920
  余座: 2

回程: CX238  2026-10-22 (周四)
  出发: BCN 11:50 → HKG 06:40+1
  里程: 85,000 miles | 税费: HKD 920
  余座: 1

合计里程: 170,000 | 合计税费: HKD 1,840
停留: 14 天

🔗 立即预订: https://book.cathaypacific.com/
```

## 工作原理

1. **Playwright** 打开国泰积分搜索页面，拦截所有 XHR/Fetch 请求
2. 识别包含航班可用性数据的 API 端点
3. 提取商务舱（C/J/D 舱位码）积分票信息
4. 将去程和回程独立存储，按停留天数（3–35天）匹配有效往返组合
5. 新组合通过 Telegram 推送，已通知的组合不重复发送

## 注意

- 本工具仅供个人研究使用，请遵守国泰航空网站使用条款
- 请设置合理的轮询间隔（默认 30 分钟），避免对服务器造成压力
- 国泰积分票源稀少，建议长期运行监控
