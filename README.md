# 股息率自动监测 + 微信提醒

一套**完全免费、个人自用**的小程序：每个工作日 A 股收盘后自动检查你关注的股票，算出当前股息率，达到设定阈值就把一条消息推到你微信；同时提供一个手机、电脑都能打开的网页看板。

**不用开电脑，不用装 App，不用花一分钱。**

---

## 一、它是怎么工作的

```
GitHub Actions（免费定时任务，云端）
   ↓ 每个工作日 15:35（北京时间）自动运行
monitor.py  →  AKShare 取全市场最新价（免费开源行情库）
   ↓ 股息率 = 近12个月每股现金分红(税前) ÷ 最新价
   ├─ 触发阈值 → PushPlus → 微信消息（手机响一下）
   └─ 每次都写 docs/latest.json → 网页看板随时可看
```

关键点：**监测跑在云端，不依赖你手机或电脑开着**。网页只是看板，微信推送才是提醒，两者互相独立。

| 项目 | 成本 |
| --- | --- |
| GitHub Actions 定时任务 | 0 元（公开仓库免费；每天约 1~2 分钟，一个月约 40 分钟） |
| AKShare 行情数据 | 0 元（开源、免注册、免 token） |
| PushPlus 微信推送 | 0 元（免费约 200 条/天，本程序每天最多 1 条） |
| 服务器 / 域名 | 不需要 |

---

## 二、部署步骤（约 15 分钟，全程点鼠标即可）

### 第 1 步 · 拿到微信推送 token

1. 打开 [pushplus.plus](https://www.pushplus.plus/)，微信扫码登录
2. 在「一对一推送」页面复制你的 **token**（一串字母数字）
3. 按提示关注它的微信公众号，否则消息送不到微信
4. 按平台要求可能需要实名认证（约 1~2 元）。不想认证可改用 Server酱，见「六、更换推送渠道」

> ⚠️ 这个 token 相当于发消息的钥匙，不要发给别人、不要写进代码文件。

### 第 2 步 · 在网页上建仓库和文件（不用装 git）

**完全可以全程在 GitHub 网站上操作**，不需要装任何软件、不用敲命令。

1. 注册并登录 [github.com](https://github.com)
2. 右上角 **+** → **New repository**，仓库名填 `dividend-monitor`，选 **Public**（公开仓库免费额度最充裕），点 **Create repository**
3. 用 **Add file → Create new file** 逐个新建下面 5 个文件。**关键：在文件名输入框里直接写完整路径**，GitHub 会自动创建中间的文件夹，包括 `.github` 这个隐藏文件夹——这是 GitHub 官方教程的做法，填一次就能建出 `.github/workflows/`：

   | 文件名输入框里填 | 内容从哪里复制 |
   | --- | --- |
   | `.github/workflows/monitor.yml` | 同名文件 |
   | `monitor.py` | 同名文件 |
   | `config.json` | 同名文件 |
   | `requirements.txt` | 同名文件 |
   | `docs/index.html` | 同名文件 |

   每次粘完内容点 **Commit changes**，弹窗里务必选 **Commit directly to the main branch**（直接提交到主分支）再确认。

   ⚠️ 两个最容易踩的坑：**别选「Create a new branch」**（工作流文件必须在默认分支 `main` 上才会被识别）；**路径一个字都不能改**，必须是 `.github/workflows/monitor.yml`（区分大小写，`workflows` 是复数）。YAML 对缩进敏感，请整段复制、不要手动调整空格。

4. 其余文件**可以不传**：`state.json`、`docs/latest.json` 程序首次运行会自动生成并回写；`tools/check_dividend.py` 只是本地核对分红的小工具，云端用不到；`README.md` 就是本说明。

> 想少点几次也可以用 git 命令一次传完（会保留完整目录结构），但完全不是必须的：
> ```bash
> cd dividend-monitor
> git init
> git add .
> git commit -m "init"
> git remote add origin https://github.com/你的用户名/dividend-monitor.git
> git push -u origin main
> ```

### 第 3 步 · 把 token 存进 Secrets

仓库页 → **Settings** → 左侧 **Secrets and variables** → **Actions** → **New repository secret**

| Name | Secret |
| --- | --- |
| `PUSHPLUS_TOKEN` | 第 1 步复制的 token |

点 **Add secret**。密钥只存在 GitHub 里，不会出现在代码或日志中。

### 第 4 步 · 手动跑一次，验证链路

1. 仓库页 → **Actions** 标签 → 若提示启用工作流，点「I understand my workflows, go ahead and enable them」
2. 左侧选 **股息率监测** → 右侧 **Run workflow** → 弹出框里点 **Run workflow**
   - 如果当天是**周末或节假日**，请勾选「忽略交易日限制，立即强制执行一次」，否则程序会判断为休市直接跳过（正常设计，不是出错）
3. 等 1~3 分钟（首次要装依赖）：
   - 绿色 ✅ 表示成功；红色 ❌ 点进去看日志
   - 有股票触发阈值，微信就会收到消息
   - 仓库里会自动生成 `state.json`、`docs/latest.json`（首次新建，之后每次更新），说明回写正常

> 想只测试计算、不真发微信：本地跑 `python monitor.py --dry-run`，见第五节。

### 第 5 步 ·（重要）核对一次分红数字

看 Actions 日志里 `×× 自动获取分红：0.4614 元/股` 这类行，或看网页看板的「每股分红」列，拿去和券商 App 的 **F10 / 年报「分配方案」** 核对。

自动抓取的规则是：**近 12 个月内「已实施」的现金分红（税前）累加，最多取 4 次**，未派发的预案不计入；结果缓存 30 天，每月 1 号刷新。程序还内置了保护：**股息率 > 25% 或 < 0.05% 会直接跳过该股并标注"数值异常"**，不会误报。

⚠️ 但第三方数据源偶尔会缺记录或延迟，口径（税前 / 近 12 个月）也可能和你想的不一样。首次部署建议花两分钟核一遍。

发现某只不准，就在 `config.json` 里给它手工填 `dividend`，程序会**优先用手工值**。也可以用工具查：

```bash
pip install -r requirements.txt
python tools/check_dividend.py 601398
```

### 第 6 步 · 改成你自己的股票和阈值

编辑 `config.json`（见第三节），改完直接推送到仓库，下次运行自动生效，**不用重启任何东西**。

### 第 7 步 ·（可选）打开网页看板

仓库 **Settings** → **Pages** → Source 选 `Deploy from a branch` → Branch 选 `main`、目录选 **`/docs`** → **Save**。等 1~2 分钟，访问：

```
https://你的用户名.github.io/dividend-monitor/
```

手机浏览器打开后，在菜单里选「**添加到主屏幕**」，图标和 App 一样，点开就是看板。

最后把 `config.json` 里的 `notify.board_url` 改成这个地址，微信提醒里就会附上「打开网页看板」的链接。

---

## 三、config.json 配置说明

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| `stocks[].code` | 6 位股票代码（也兼容 `sh601398` 写法） | `"601398"` |
| `stocks[].name` | 显示名称，随便写 | `"工商银行"` |
| `stocks[].dividend` | **近 12 个月每股现金分红（元，税前）**；填 `null` 由程序自动抓取并缓存 30 天 | `null` 或 `0.3060` |
| `stocks[].high` | 股息率 **≥** 此值提醒（通常是股价跌出性价比）；`null` 关闭 | `5.5` |
| `stocks[].low` | 股息率 **≤** 此值提醒（可关注减仓）；`null` 关闭 | `3.5` |
| `default_high` / `default_low` | 个股没填时的默认值 | `5.0` / `3.0` |
| `repeat_days` | 持续停在提醒区间时，每隔几天复报一次 | `7` |
| `notify.channel` | 推送渠道 | `"pushplus"` |
| `notify.board_url` | 看板地址，用于微信消息里的链接 | 见第 7 步 |

配置里所有以 `_` 开头的键都是备注，程序会忽略，你可以随便改。

> **怎么查每股分红**：券商 App → 个股 F10 → 分红融资 / 分配方案，找到最近 12 个月已实施的「每 10 股派 X 元（含税）」，把各次 X 相加后**除以 10**。例如一年内 10 派 1.86 元 + 10 派 1.20 元 = 3.06 元/10 股 → 填 `0.306`。

---

## 四、提醒规则

- **只会穿越阈值时提醒**：从正常区间进入提醒区间才推一次，不会天天重复轰炸
- **持续停留会复报**：一直停在区间内时，每 `repeat_days` 天补一条
- **回落自动重置**：回到正常区间后再达标，会重新提醒
- **合并成一条**：多只股票同时触发，合并成一条微信消息，省额度也不刷屏
- **休市自动跳过**：用交易日历判断，周末和节假日不跑、不推送
- **没触发就不推**：想看当前状态，打开网页看板即可（随时可看，不用等推送）

---

## 五、本地试跑（可选）

在你自己电脑上：

```bash
cd dividend-monitor
pip install -r requirements.txt
python monitor.py --dry-run
```

`--dry-run` 只计算并打印，**不推送微信、不写任何文件**，可以放心试。加 `--force` 可忽略交易日判断（周末测试用）。

---

## 六、更换推送渠道

改 `config.json` 的 `notify.channel`，并在 GitHub Secrets 里配好对应变量：

| channel | 送到哪里 | 需要的 Secret | 免费额度 |
| --- | --- | --- | --- |
| `pushplus` | 微信服务号（默认） | `PUSHPLUS_TOKEN` | 约 200 条/天 |
| `serverchan` | 微信服务号（Server酱） | `SERVERCHAN_KEY` | 5 条/天，够用 |
| `wecom` | 企业微信群机器人 | `WECOM_WEBHOOK` | 20 条/分钟，额度最宽松 |
| `none` | 不推送，只更新看板 | 无 | — |

企业微信方式：注册一个免费的企业微信，建一个只有自己的群 → 群设置 → 添加群机器人 → 复制 Webhook 地址存进 `WECOM_WEBHOOK`。

---

## 七、常见问题

**Q：算出来的数字和券商 App 显示的股息率对不上？**
A：口径不同，这是最常见的疑问。App 可能用静态、TTM、动态或税后口径，彼此能差一两个百分点。本程序固定用「近 12 个月每股现金分红（税前）÷ 最新价」，只要分红数字填对，拿计算器就能复算，透明可核对。请不要直接拿两个数字比大小。

**Q：网页上建完文件，Actions 里找不到「股息率监测」？**
A：按顺序查三处：① 提交时是不是选了「Create a new branch」，文件没进 `main`；② 路径是不是写成了 `.github/workflow/…`、`.Github/…` 或放到了仓库根目录；③ 仓库 **Settings → Actions → General** 里工作流被禁用，改成允许全部 Actions 后随便提交一次即可。

**Q：定时任务突然不跑了？**
A：GitHub 规定仓库**连续 60 天无任何活动会自动暂停定时工作流**，去 Actions 页面手动 Run workflow 一次即可恢复。另外 GitHub 的定时任务可能延迟几分钟到半小时，不是准点触发，属正常现象。

**Q：日志报「行情获取最终失败」？**
A：脚本已内置 4 次重试 + 东方财富/新浪双数据源。偶发失败先手动重跑一次；若连续几天失败，多半是数据源改版，执行 `pip install akshare --upgrade` 升级即可（requirements.txt 默认装最新版，重跑任务会自动升级）。境外节点访问国内财经站点偶有超时，也属正常。

**Q：想一天检查多次 / 改检查时间？**
A：编辑 `.github/workflows/monitor.yml` 的 `schedule`。注意 **cron 用的是 UTC 时间**，北京时间减 8 小时。例如北京 10:00 = UTC 02:00，加一行 `- cron: '0 2 * * 1-5'`。不过股息率一天变化很小，一天一次足够。

**Q：安全吗？**
A：token 存在 GitHub Secrets，不进代码也不进日志。仓库里只有股票代码和阈值，没有个人隐私。介意的话可以设为 Private，Actions 每月仍有 2000 分钟免费额度，完全够用。

**Q：想加港股 / 美股？**
A：分红频率（半年报、季度分红）、除净日和币种都不一样，需要换数据源和口径，可以再找我改。

---

## 八、文件清单

| 文件 | 作用 |
| --- | --- |
| `monitor.py` | 主程序：取行情、算股息率、判阈值、推微信、写看板数据 |
| `config.json` | **你唯一需要改的文件**：股票清单和阈值 |
| `.github/workflows/monitor.yml` | 云端定时任务定义 |
| `requirements.txt` | 依赖（akshare、requests） |
| `state.json` | 自动维护：提醒去重状态、分红缓存（不用手改） |
| `docs/index.html` | 网页看板页面 |
| `docs/latest.json` | 自动生成的看板数据（不用手改） |
| `tools/check_dividend.py` | 辅助工具：核对某只股票的分红数字 |

---

## 附：一个可能更省事的选择

如果你只盯三五只股票，其实**可以完全不写代码**。分红方案在两次分红之间是固定的，所以「股息率涨到 5% 提醒」等价于「股价跌到 `每股分红 ÷ 5%` 提醒」，直接在雪球 / 同花顺 / 券商 App 里设一个价格提醒就行，推送还比自建的更可靠。

这套程序的价值在于：股票多、想统一口径横向对比、想留下历史状态、或者想同时按上下限双向提醒。如果只是几只票，先用 App 的价格提醒试一个月，觉得不够用再回来部署也不迟。
