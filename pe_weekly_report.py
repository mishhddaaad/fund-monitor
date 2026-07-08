#!/usr/bin/env python3
"""PE 估值周报 — 每周一定投估值提醒。

严格按用户知识库规则执行，无偏向：
  PE百分位 < 20%  → 🟢 加倍定投
  PE百分位 20-40% → 🔵 正常定投
  PE百分位 40-80% → ⚪ 维持定投（不加倍）
  PE百分位 > 80%  → 🔴 警惕，考虑3331分批止盈
"""

import html
import json
import os
import urllib.request

PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "").strip()

# 监控的指数（黄金不看PE，跳过）
TARGETS = {
    "沪深300": {"fund": "110020 沪深300联接A", "category": "A股"},
    "上证50": {"fund": "001051 上证50联接A", "category": "A股"},
    "中证红利": {"fund": "009051 中证红利联接A", "category": "A股"},
    "标普500": {"fund": "017641 标普500联接A", "category": "美股"},
    "纳指100": {"fund": "019172 纳指100联接A", "category": "美股"},
}


def fetch_danjuan_data():
    """从蛋卷基金 API 获取指数估值数据。"""
    url = "https://danjuanfunds.com/djapi/index_eva/dj"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    items = data.get("data", {}).get("items", [])

    # 用 index_code 或 name 匹配
    name_map = {item.get("name", ""): item for item in items}
    result = {}
    for target_name in TARGETS:
        item = name_map.get(target_name)
        if item:
            result[target_name] = {
                "pe": item.get("pe"),
                "pe_percentile": item.get("pe_percentile"),
                "pb": item.get("pb"),
                "roe": item.get("roe"),
                "yeild": item.get("yeild"),
                "eva_type": item.get("eva_type"),
                "ts": item.get("ts"),
            }
    return result


def classify(pe_pct):
    """按用户知识库规则分类，返回(标签, emoji, 建议)。"""
    if pe_pct is None:
        return ("数据异常", "❓", "请手动查询")
    pct = pe_pct * 100  # 蛋卷返回的是小数 0.852 = 85.2%
    if pct < 20:
        return ("低估", "🟢", "加倍定投")
    elif pct < 40:
        return ("偏低", "🔵", "正常定投")
    elif pct < 80:
        return ("适中", "⚪", "维持定投（不加倍）")
    else:
        return ("高估", "🔴", "警惕，考虑3331分批止盈")


def build_report(data):
    """构建推送内容，严格按用户规则分类。"""
    from datetime import datetime, timezone, timedelta

    cn_time = datetime.now(timezone(timedelta(hours=8)))
    date_str = cn_time.strftime("%Y-%m-%d")

    # 按类别分组
    a_stock = []
    us_stock = []
    for name, info in data.items():
        target = TARGETS.get(name, {})
        category = target.get("category", "")
        pe = info.get("pe")
        pe_pct = info.get("pe_percentile")
        label, emoji, advice = classify(pe_pct)

        row = {
            "name": name,
            "fund": target.get("fund", name),
            "pe": pe,
            "pe_pct": pe_pct,
            "label": label,
            "emoji": emoji,
            "advice": advice,
        }
        if category == "A股":
            a_stock.append(row)
        elif category == "美股":
            us_stock.append(row)

    # 统计各区数量
    all_rows = a_stock + us_stock
    zones = {"🟢": [], "🔵": [], "⚪": [], "🔴": [], "❓": []}
    for r in all_rows:
        zones[r["emoji"]].append(r["name"])

    # 构建 HTML
    title = f"📊 PE估值周报 {date_str}"

    body = f"<h2>{html.escape(title)}</h2>"
    body += "<hr/>"

    # 区域汇总
    body += "<h3>📋 本周汇总</h3>"
    body += "<table border='1' cellpadding='6' style='border-collapse:collapse;font-size:14px'>"
    body += "<tr><th>区间</th><th>规则</th><th>指数</th></tr>"
    body += f"<tr><td>🟢 &lt;20%</td><td>加倍定投</td><td>{'、'.join(zones['🟢']) or '无'}</td></tr>"
    body += f"<tr><td>🔵 20-40%</td><td>正常定投</td><td>{'、'.join(zones['🔵']) or '无'}</td></tr>"
    body += f"<tr><td>⚪ 40-80%</td><td>维持（不加倍）</td><td>{'、'.join(zones['⚪']) or '无'}</td></tr>"
    body += f"<tr><td>🔴 &gt;80%</td><td>考虑3331止盈</td><td>{'、'.join(zones['🔴']) or '无'}</td></tr>"
    body += "</table><br/>"

    # A股明细
    body += "<h3>🇨🇳 A股</h3>"
    body += "<table border='1' cellpadding='6' style='border-collapse:collapse;font-size:14px'>"
    body += "<tr><th>指数</th><th>PE</th><th>百分位</th><th>判定</th><th>建议</th></tr>"
    for r in a_stock:
        pct_str = f"{r['pe_pct']*100:.1f}%" if r["pe_pct"] is not None else "N/A"
        pe_str = f"{r['pe']:.2f}" if r["pe"] is not None else "N/A"
        body += f"<tr><td>{html.escape(r['name'])}</td><td>{pe_str}</td><td>{pct_str}</td><td>{r['emoji']} {r['label']}</td><td>{html.escape(r['advice'])}</td></tr>"
    body += "</table><br/>"

    # 美股明细
    body += "<h3>🇺🇸 美股</h3>"
    body += "<table border='1' cellpadding='6' style='border-collapse:collapse;font-size:14px'>"
    body += "<tr><th>指数</th><th>PE</th><th>百分位</th><th>判定</th><th>建议</th></tr>"
    for r in us_stock:
        pct_str = f"{r['pe_pct']*100:.1f}%" if r["pe_pct"] is not None else "N/A"
        pe_str = f"{r['pe']:.2f}" if r["pe"] is not None else "N/A"
        body += f"<tr><td>{html.escape(r['name'])}</td><td>{pe_str}</td><td>{pct_str}</td><td>{r['emoji']} {r['label']}</td><td>{html.escape(r['advice'])}</td></tr>"
    body += "</table><br/>"

    # 备注
    body += "<p style='color:#888;font-size:12px'>📌 黄金(000216)为商品类资产，不适用PE估值，已跳过。</p>"
    body += "<p style='color:#888;font-size:12px'>📌 规则：&lt;20%加倍 / 20-40%正常 / 40-80%维持 / &gt;80%考虑止盈。按你自己的纪律执行，不被短期波动带偏。</p>"
    body += "<p style='color:#888;font-size:12px'>📌 数据来源：蛋卷基金（雪球）。PE百分位为全历史口径。</p>"

    return title, body


def send_pushplus(title, content):
    """通过 PushPlus 推送到微信。"""
    if not PUSHPLUS_TOKEN:
        print("[警告] 未配置 PUSHPLUS_TOKEN，跳过推送")
        return False

    payload = json.dumps({
        "token": PUSHPLUS_TOKEN,
        "title": title,
        "content": content,
        "template": "html",
    }).encode("utf-8")

    req = urllib.request.Request(
        "http://www.pushplus.plus/send",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            if result.get("code") == 200:
                print("[通知] PushPlus 推送成功")
                return True
            else:
                print(f"[错误] PushPlus 返回: {result}")
                return False
    except Exception as e:
        print(f"[错误] PushPlus 推送失败: {e}")
        return False


def main():
    print(f"=== PE 估值周报启动 ===")

    # 获取数据
    try:
        data = fetch_danjuan_data()
        print(f"获取到 {len(data)} 个指数数据")
        for name, info in data.items():
            pe = info.get("pe")
            pct = info.get("pe_percentile")
            pct_str = f"{pct*100:.1f}%" if pct is not None else "N/A"
            print(f"  {name}: PE={pe}, 百分位={pct_str}")
    except Exception as e:
        print(f"[错误] 数据获取失败: {e}")
        # 降级推送：告知失败 + 手动查询链接
        title = "📊 PE周报 - 数据获取失败"
        body = f"<h2>{html.escape(title)}</h2>"
        body += f"<p>⚠️ 自动获取失败: {html.escape(str(e))}</p>"
        body += "<p>请手动查询：</p>"
        body += "<p>• A股: <a href='https://danjuanfunds.com/djmodule/value-center'>蛋卷基金估值</a></p>"
        body += "<p>• 美股: <a href='https://www.lixinger.com'>理杏仁</a></p>"
        send_pushplus(title, body)
        return

    # 检查是否有缺失
    missing = [name for name in TARGETS if name not in data]
    if missing:
        print(f"[警告] 缺失指数: {missing}")

    # 构建并推送
    title, content = build_report(data)
    send_pushplus(title, content)


if __name__ == "__main__":
    main()
