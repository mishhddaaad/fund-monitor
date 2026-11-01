#!/usr/bin/env python3
"""每日收报 — 收盘后一条汇总推送。

行动分层：需要操作的放最顶上，市场信息放底下当背景。
只输出规则信号（4%法 + PE区间），不做任何预测性建议。
白话解读：把当天数据动态翻译成人话（含金额换算），非固定术语表。
"""

import html
import json
import os
import urllib.request
from datetime import datetime, timezone, timedelta

PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "").strip()
PE_STATE_FILE = "pe_state.json"

FUND_MAP = [
    ("110020", "沪深300联接", "sh510300", "沪深300ETF", 980),
    ("001051", "上证50联接", "sh510050", "上证50ETF", 500),
    ("009051", "中证红利联接", "sh515080", "红利ETF", 520),
    ("003376", "中债7-10年", None, None, 800),
    ("000216", "黄金联接", "sh518880", "黄金ETF", 205),
    ("017641", "标普500联接", "sh513500", "标普500ETF", 225),
    ("019172", "纳指100联接", "sh513100", "纳指100ETF", 225),
]
# 最后一段为持仓金额估算（元），每月复盘时更新

INDEX_CODES = [
    ("sh000001", "上证指数"),
    ("sz399006", "创业板指"),
]

CROSS_RULES = {
    (20, "down"): ("🟢 进入加倍区", "白话：变得非常便宜了，有闲钱可以多投点"),
    (20, "up"): ("⚪ 离开加倍区", "白话：从非常便宜回到一般便宜，恢复正常投法"),
    (40, "down"): ("🔵 进入定投区", "白话：变便宜了，可以恢复/开始每天投它"),
    (40, "up"): ("⚪ 离开定投区", "白话：不算便宜了，先持有、暂停投它"),
    (80, "down"): ("⚪ 离开止盈警惕区", "白话：从偏贵回到中等，不用卖了"),
    (80, "up"): ("🔴 进入止盈警惕区", "白话：变贵了，可以考虑分批卖出锁住利润"),
}


def pe_zone(pct):
    if pct is None:
        return ("❓", "数据异常")
    if pct < 20:
        return ("🟢", "低估·加倍区")
    if pct < 40:
        return ("🔵", "偏低·定投区")
    if pct < 80:
        return ("⚪", "适中·维持区")
    return ("🔴", "高估·止盈警惕")


def pe_plain(pct):
    """PE百分位 → 一句白话"""
    if pct is None:
        return "数据没取到"
    cheaper = 100 - pct
    if pct < 20:
        return f"现在比历史上{cheaper:.0f}%的时间都便宜，属于捡漏价，你的规则说可以多投"
    if pct < 40:
        return f"现在比历史上{cheaper:.0f}%的时间便宜，价位不错，你的规则说正常投"
    if pct < 80:
        return f"价格中等（只比历史上{cheaper:.0f}%的时间便宜），你的规则说先拿着、暂不加钱"
    return f"偏贵了（比历史上{pct:.0f}%的时间都贵），你的规则说涨够了可以分批卖"


def fetch_qq_quotes(codes):
    url = "https://qt.gtimg.cn/q=" + ",".join(codes)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        text = resp.read().decode("gbk", errors="ignore")
    result = {}
    for line in text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        # 用行首 v_sh510300 提取完整代码作键（与配置一致）
        raw_code = line.split("=")[0].strip().removeprefix("v_")
        parts = line.split("=")[1].strip('"').split("~")
        if len(parts) < 33:
            continue
        try:
            result[raw_code] = {
                "name": parts[1],
                "price": float(parts[3]),
                "chg_pct": float(parts[32]),
            }
        except (ValueError, IndexError):
            continue
    return result


def fetch_pe_zones():
    url = "https://danjuanfunds.com/djapi/index_eva/dj"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    items = {i.get("name"): i for i in data.get("data", {}).get("items", [])}
    out = {}
    for name in ["沪深300", "上证50", "中证红利", "标普500", "纳指100"]:
        item = items.get(name)
        if item:
            pct = item.get("pe_percentile")
            out[name] = {"pe": item.get("pe"), "pct": pct * 100 if pct else None}
    return out


def load_last_pe_state():
    if not os.path.exists(PE_STATE_FILE):
        return {}
    try:
        with open(PE_STATE_FILE) as f:
            return json.load(f).get("pe", {})
    except (json.JSONDecodeError, OSError):
        return {}


def save_pe_state(pe_data):
    cn = datetime.now(timezone(timedelta(hours=8)))
    state = {"date": cn.strftime("%Y-%m-%d"), "pe": pe_data}
    with open(PE_STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def detect_crossings(old, new):
    alerts = []
    for name, nd in new.items():
        od = old.get(name)
        if not od or od.get("pct") is None or nd.get("pct") is None:
            continue
        o, n = od["pct"], nd["pct"]
        for threshold in (20, 40, 80):
            direction = None
            if o >= threshold and n < threshold:
                direction = "down"
            elif o < threshold and n >= threshold:
                direction = "up"
            if direction:
                label, plain = CROSS_RULES[(threshold, direction)]
                alerts.append(f"<b>{name}</b> {label}（{o:.1f}%→{n:.1f}%）<br/>"
                              f"<span style='font-size:13px'>{plain}</span>")
                break
    return alerts


def detect_watchlist(pe_data):
    watch = []
    for name, d in pe_data.items():
        pct = d.get("pct")
        if pct is None:
            continue
        for t in (20, 40, 80):
            if 0 < abs(pct - t) < 5:
                watch.append((name, pct, t, abs(pct - t)))
                break
    return watch


def load_pending_signals():
    if not os.path.exists("fund_state.json"):
        return {}
    with open("fund_state.json") as f:
        state = json.load(f)
    out = {}
    name_by_code = {c: (c, n) for c, n, _, _, _ in FUND_MAP}
    for code, s in state.items():
        if not isinstance(s, dict):
            continue
        if s.get("pending_signal"):
            out[code] = name_by_code.get(code, (code, code))
    return out


def build_plain_summary(quotes, pe_data, signals, crossings, watch):
    """核心：把今天所有数据动态翻译成3-6句人话"""
    lines = []

    # 1. 今天要不要操作
    if signals:
        names = "、".join(n for _, n in signals.values())
        lines.append(f"① <b>有打折机会</b>：{names} 比你当初买入的价格跌了4%以上，等于打折促销。想捡便宜可以各补100-300元；不想动也完全没关系，等价格涨回去信号就自动消失。")
    else:
        lines.append("① <b>今天啥也不用做</b>：没有打折信号，也没到该卖的时候，你的自动定投照常扣款就好。")

    # 2. 换挡警报翻译
    for c in crossings:
        lines.append(f"② <b>重要变化</b>：{c}")
    if not crossings:
        w_text = ""
        if watch:
            w_parts = [f"{n}（现在{p:.1f}%，离换挡线{t}%只差{d:.1f}个点）" for n, p, t, d in watch]
            w_text = f"不过 {'；'.join(w_parts)} ——快变盘了，这几天多留意推送。"
        lines.append(f"② <b>贵贱程度没变化</b>，还是老样子。{w_text}")

    # 3. 今天账面赚亏多少钱（百分比→金额）
    if quotes:
        gains, losses = [], []
        for code, fname, etf, ename, amount in FUND_MAP:
            if etf and etf in quotes:
                chg = quotes[etf]["chg_pct"]
                money = round(amount * chg / 100)
                if money > 0:
                    gains.append(f"{fname}赚{money}元")
                elif money < 0:
                    losses.append(f"{fname}亏{abs(money)}元")
        parts = []
        if gains:
            parts.append("、".join(gains[:3]) + ("等" if len(gains) > 3 else ""))
        if losses:
            parts.append("、".join(losses[:3]) + ("等" if len(losses) > 3 else ""))
        total = sum(round(a * quotes[e]["chg_pct"] / 100) for c, f, e, en, a in FUND_MAP if e and e in quotes)
        mood = "今天是回血的一天 🎉" if total > 0 else ("今天是缩水的一天，正常波动别慌" if total < 0 else "今天基本持平")
        lines.append(f"③ <b>今天你的账户</b>：{'；'.join(parts) if parts else '波动都很小'}。合计约{total:+d}元（按你当前仓位估算）。{mood}")

    # 4. QDII时间差提醒（美基当天涨跌≠当天净值）
    lines.append("④ 小提醒：标普500和纳指100显示的是<b>昨晚美股</b>的涨跌，你的基金净值要今晚才更新，数字对不上不是出错。")

    # 5. 大盘一句话
    if "sh000001" in quotes and "sz399006" in quotes:
        sh, cyb = quotes["sh000001"], quotes["sz399006"]
        both_down = sh["chg_pct"] < 0 and cyb["chg_pct"] < 0
        both_up = sh["chg_pct"] > 0 and cyb["chg_pct"] > 0
        mood = "整体都在涨" if both_up else ("整体都在跌" if both_down else "涨跌不一、行情分化")
        lines.append(f"⑤ <b>大盘</b>：{mood}（上证{sh['chg_pct']:+.2f}%，创业板{cyb['chg_pct']:+.2f}%）。")

    return lines


def build_report(quotes, pe_data, signals, crossings, watch):
    cn = datetime.now(timezone(timedelta(hours=8)))
    title = f"📰 每日收报 {cn.strftime('%Y-%m-%d %A')}"

    b = [f"<h2>{html.escape(title)}</h2><hr/>"]

    # ── 换挡警报置顶 ──
    if crossings:
        b.append("<div style='background:#fff3cd;border:1px solid #ffc107;padding:8px;border-radius:6px'>")
        b.append("<b>🚦 换挡警报</b><br/>" + "<br/>".join(crossings))
        b.append("</div>")

    # ── 白话解读（核心）──
    b.append("<h3>🗣️ 今天用大白话说</h3>")
    b.append("<div style='background:#f0f7ff;padding:10px;border-radius:6px;font-size:14px'>")
    for line in build_plain_summary(quotes, pe_data, signals, crossings, watch):
        b.append(f"<p style='margin:6px 0'>{line}</p>")
    b.append("</div>")

    # ── 数据明细 ──
    b.append("<h3>📊 数据明细</h3>")
    b.append("<table border='1' cellpadding='5' style='border-collapse:collapse;font-size:14px'>")
    b.append("<tr><th>基金</th><th>参考价</th><th>今日</th><th>折算金额</th></tr>")
    for code, fname, etf, ename, amount in FUND_MAP:
        if etf and etf in quotes:
            q = quotes[etf]
            color = "#c0392b" if q["chg_pct"] < 0 else "#27ae60"
            money = round(amount * q["chg_pct"] / 100)
            b.append(f"<tr><td>{fname}</td><td>{q['price']:.3f}</td>"
                     f"<td style='color:{color}'>{q['chg_pct']:+.2f}%</td>"
                     f"<td style='color:{color}'>{money:+d}元</td></tr>")
        else:
            b.append(f"<tr><td>{fname}</td><td colspan='3'>债基·波动极小</td></tr>")
    b.append("</table>")

    # ── PE白话表 ──
    b.append("<h3>📈 现在贵还是便宜（PE）</h3>")
    b.append("<table border='1' cellpadding='5' style='border-collapse:collapse;font-size:13px'>")
    b.append("<tr><th>指数</th><th>百分位</th><th>状态</th><th>白话</th></tr>")
    for name, d in pe_data.items():
        emoji, zone = pe_zone(d["pct"])
        pct_str = f"{d['pct']:.1f}%" if d["pct"] is not None else "N/A"
        b.append(f"<tr><td>{name}</td><td>{pct_str}</td><td>{emoji}{zone.split('·')[0]}</td>"
                 f"<td style='font-size:12px'>{pe_plain(d['pct'])}</td></tr>")
    b.append("</table>")
    b.append("<p style='color:#888;font-size:12px'>本报告只翻译你的规则信号，不含预测。金额按上月仓位估算，每月复盘时校准。无信号=不操作。</p>")

    return title, "".join(b)


def send_pushplus(title, content):
    if not PUSHPLUS_TOKEN:
        print("[警告] 未配置 PUSHPLUS_TOKEN")
        return False
    payload = json.dumps({"token": PUSHPLUS_TOKEN, "title": title,
                          "content": content, "template": "html"}).encode()
    req = urllib.request.Request("http://www.pushplus.plus/send", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        r = json.loads(resp.read())
        ok = r.get("code") == 200
        print(f"[通知] PushPlus {'推送成功' if ok else str(r)}")
        return ok


def main():
    print("=== 每日收报启动 ===")
    all_codes = [e for _, _, e, _, _ in FUND_MAP if e] + [c for c, _ in INDEX_CODES]
    try:
        quotes = fetch_qq_quotes(all_codes)
        print(f"行情: {len(quotes)} 个标的")
    except Exception as e:
        print(f"[错误] 行情获取失败: {e}")
        quotes = {}

    try:
        pe_data = fetch_pe_zones()
        print(f"PE: {len(pe_data)} 个指数")
    except Exception as e:
        print(f"[警告] PE获取失败: {e}")
        pe_data = {}

    crossings = detect_crossings(load_last_pe_state(), pe_data)
    if crossings:
        print(f"换挡警报: {len(crossings)} 条")
    watch = detect_watchlist(pe_data)
    if pe_data:
        save_pe_state(pe_data)

    signals = load_pending_signals()
    if signals:
        print(f"挂起信号: {[n for _, n in signals.values()]}")

    title, content = build_report(quotes, pe_data, signals, crossings, watch)
    send_pushplus(title, content)

    # 同步输出纯文本版（供飞书等渠道使用）
    with open("daily_brief_text.txt", "w") as f:
        f.write(f"{title}\n\n")
        f.write(build_text_version(quotes, pe_data, signals, crossings, watch))


def build_text_version(quotes, pe_data, signals, crossings, watch):
    """纯文本版（飞书推送用）：只保留白话+关键数据"""
    lines = []
    if crossings:
        lines.append("🚦 换挡警报")
        for c in crossings:
            lines.append("• " + c.replace("<b>", "").replace("</b>", " ")
                         .replace("<br/>", " — ").replace("<span style='font-size:13px'>", "")
                         .replace("</span>", ""))
        lines.append("")
    lines.append("🗣️ 今天用大白话说")
    for line in build_plain_summary(quotes, pe_data, signals, crossings, watch):
        clean = line.replace("<b>", "").replace("</b>", "")
        lines.append(clean)
    lines.append("")
    lines.append("📊 数据明细")
    for code, fname, etf, ename, amount in FUND_MAP:
        if etf and etf in quotes:
            q = quotes[etf]
            money = round(amount * q["chg_pct"] / 100)
            lines.append(f"{fname}: {q['chg_pct']:+.2f}%（约{money:+d}元）")
        else:
            lines.append(f"{fname}: 债基波动小")
    lines.append("")
    lines.append("📈 贵还是便宜")
    for name, d in pe_data.items():
        if d["pct"] is not None:
            emoji, _ = pe_zone(d["pct"])
            lines.append(f"{name} {d['pct']:.1f}% {emoji} {pe_plain(d['pct'])}")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
