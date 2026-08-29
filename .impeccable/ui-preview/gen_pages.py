# -*- coding: utf-8 -*-
import io

CSS = """
*{margin:0;padding:0;box-sizing:border-box;}
html,body{width:390px;height:844px;overflow:hidden;}
.phone{width:390px;height:844px;display:flex;flex-direction:column;position:relative;
  background:#FAF8F4;color:#191C20;
  font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;}
.statusbar{height:44px;display:flex;justify-content:space-between;align-items:center;padding:0 26px 0 30px;font-size:14px;font-weight:600;flex:0 0 auto;}
.masthead{padding:10px 24px 0;flex:0 0 auto;}
.masthead .brand{font-size:11px;font-weight:700;letter-spacing:3px;color:#C0492E;}
.masthead .date{font-size:12px;color:#6b6f76;margin-top:3px;display:flex;justify-content:space-between;}
.masthead .rule{height:3px;background:#191C20;margin-top:8px;}
.masthead .rule.thin{height:1px;margin-top:2px;}
.scroll{flex:1;overflow:hidden;padding:0 24px;display:flex;flex-direction:column;}
.section-label{font-size:12px;font-weight:800;letter-spacing:2px;color:#3E5C76;margin:16px 0 10px;display:flex;align-items:center;gap:8px;}
.section-label::after{content:"";flex:1;height:1px;background:#d9d5cd;}
.footer{margin-top:auto;padding:12px 0 16px;font-size:10.5px;color:#a0a4ab;display:flex;justify-content:space-between;border-top:1px solid #d9d5cd;}
.tabbar{flex:0 0 auto;display:flex;padding:10px 10px 22px;background:#FFFFFF;border-top:1px solid #E5E1D8;}
.tab{flex:1;text-align:center;font-size:10.5px;color:#8a8e96;padding-top:4px;}
.tab svg{width:22px;height:22px;display:block;margin:0 auto 4px;}
.tab.on{color:#191C20;font-weight:700;}
.tab.on .pill{display:block;width:38px;height:3.5px;border-radius:2px;margin:5px auto 0;background:#C0492E;}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:4px;}
.chip{font-size:12px;padding:5px 13px;border-radius:99px;border:1px solid #d9d5cd;color:#5c6066;}
.chip.on{background:#191C20;color:#FAF8F4;border-color:#191C20;font-weight:700;}
.bars{display:flex;align-items:flex-end;gap:7px;height:90px;padding:6px 2px 0;}
.bars .b{flex:1;display:flex;flex-direction:column;justify-content:flex-end;height:100%;}
.bars .b i{display:block;background:#3E5C76;border-radius:3px 3px 0 0;}
.bars .b.weak i{background:#C9C4BA;}
.baseline-note{font-size:10.5px;color:#8a8e96;margin-top:7px;display:flex;justify-content:space-between;}
.night{margin-bottom:11px;}
.night .row1{display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:5px;}
.night .row1 b{font-weight:700;}
.night .row1 span{color:#6b6f76;font-size:11px;}
.nbar{height:10px;border-radius:5px;background:#EAE6DD;overflow:hidden;display:flex;}
.nbar .sleep{background:#3E5C76;height:100%;}
.nbar .awake{background:#C0492E;height:100%;}
.index-row{display:flex;justify-content:space-between;align-items:baseline;padding:11px 0;border-bottom:1px solid #E5E1D8;}
.index-row .d{font-size:12px;color:#8a8e96;width:64px;flex:0 0 auto;}
.index-row .t{flex:1;font-size:13.5px;font-weight:600;}
.index-row .st{font-size:10.5px;flex:0 0 auto;padding:2px 9px;border-radius:99px;}
.st.open{background:#F4E3E0;color:#C0492E;}
.st.closed{background:#EAE6DD;color:#6b6f76;}
.answer{border:1px solid #E5E1D8;border-radius:14px;padding:14px 16px;background:#FFFFFF;}
.answer .lead{font-size:15.5px;font-weight:800;line-height:1.6;margin-bottom:8px;}
.answer .body{font-size:13px;line-height:1.8;color:#33373d;}
.answer .news{margin-top:9px;display:flex;flex-direction:column;gap:7px;}
.answer .item{display:flex;gap:10px;font-size:12.5px;line-height:1.65;align-items:flex-start;}
.answer .item .no{color:#C0492E;font-weight:900;flex:0 0 auto;}
.ans-meta{font-size:10.5px;color:#a0a4ab;margin-top:9px;}
.bubble{background:#191C20;color:#FAF8F4;font-size:13.5px;padding:10px 15px;border-radius:16px 16px 4px 16px;margin:4px 0 12px;align-self:flex-end;max-width:70%;margin-left:auto;}
.inputbar{flex:0 0 auto;display:flex;gap:10px;padding:10px 20px 20px;background:#FAF8F4;border-top:1px solid #E5E1D8;}
.inputbar .field{flex:1;border:1.5px solid #191C20;border-radius:14px;padding:12px 15px;font-size:13px;color:#8a8e96;}
.inputbar .send{width:46px;height:46px;border-radius:14px;background:#191C20;color:#FAF8F4;display:flex;align-items:center;justify-content:center;}
.pattern{border:1px solid #E5E1D8;border-radius:14px;background:#FFFFFF;padding:14px 16px;margin-bottom:11px;}
.pattern .trig{font-size:14.5px;font-weight:800;margin-bottom:6px;}
.pattern .trig .arrow{color:#C0492E;font-weight:900;margin:0 4px;}
.pattern .stat{font-size:12px;color:#5c6066;line-height:1.7;}
.pattern .meta{font-size:10.5px;color:#a0a4ab;margin-top:6px;display:flex;justify-content:space-between;}
.obs{display:flex;gap:10px;padding:12px 14px;border:1px dashed #C9C4BA;border-radius:12px;margin-bottom:9px;align-items:center;}
.obs .ic{color:#C0492E;font-weight:900;font-size:14px;flex:0 0 auto;}
.obs .tx{font-size:12.5px;color:#4a4e55;line-height:1.6;}
.setrow{display:flex;justify-content:space-between;align-items:center;padding:13px 2px;border-bottom:1px solid #E5E1D8;}
.setrow .l .n{font-size:14px;font-weight:700;}
.setrow .l .s{font-size:11px;color:#8a8e96;margin-top:2px;}
.setrow .mark{width:18px;height:18px;border-radius:50%;border:1.5px solid #C9C4BA;flex:0 0 auto;}
.setrow.on .mark{border-color:#C0492E;position:relative;}
.setrow.on .mark::after{content:"";position:absolute;inset:4px;background:#C0492E;border-radius:50%;}
.colophon{font-size:10.5px;color:#a0a4ab;line-height:1.9;}
.colophon b{color:#5c6066;font-weight:700;}
"""

ICONS = """
<div class="tab on"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="17" rx="3"/><path d="M8 2v4M16 2v4M3 10h18"/></svg>今日<span class="pill"></span></div>
<div class="tab"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>历史</div>
<div class="tab"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 17l6-6 4 4 8-8"/></svg>我的规律</div>
<div class="tab"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 5-6 8-6s6.5 2 8 6"/></svg>我的</div>
"""

ICONS_H = ICONS.replace('<div class="tab on"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="17" rx="3"/><path d="M8 2v4M16 2v4M3 10h18"/></svg>今日', '<div class="tab"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="17" rx="3"/><path d="M8 2v4M16 2v4M3 10h18"/></svg>今日')
ICONS_H = ICONS_H.replace('<div class="tab"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>历史', '<div class="tab on"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>历史')

ICONS_P = ICONS.replace('<div class="tab"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 17l6-6 4 4 8-8"/></svg>我的规律', '<div class="tab on"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 17l6-6 4 4 8-8"/></svg>我的规律')
ICONS_M = ICONS.replace('<div class="tab"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 5-6 8-6s6.5 2 8 6"/></svg>我的', '<div class="tab on"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 5-6 8-6s6.5 2 8 6"/></svg>我的')


def page(name, tabbar, brand, date, body, footer=None):
    footer_html = '<div class="footer">%s</div>' % footer if footer else ''
    f = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><style>%s</style></head><body>
<div class="phone">
  <div class="statusbar"><span>14:40</span><span>●●●</span></div>
  <div class="masthead"><div class="brand">%s</div><div class="date"><span>%s</span><span>私人版 · 仅你可见</span></div><div class="rule"></div><div class="rule thin"></div></div>
  <div class="scroll">
  %s
  %s
  </div>
  <div class="tabbar">%s</div>
</div></body></html>""" % (CSS, brand, date, body, footer_html, tabbar)
    io.open(name, "w", encoding="utf-8").write(f)


# ============ 历史 ============
history_body = """
<div class="chips"><span class="chip on">睡眠</span><span class="chip">步数</span><span class="chip">心率</span><span class="chip">静息心率</span><span class="chip">血氧</span><span class="chip">压力</span></div>
<div class="section-label">最近 14 晚 睡眠时长</div>
<div class="bars">
  <div class="b"><i style="height:62%"></i></div><div class="b"><i style="height:70%"></i></div><div class="b weak"><i style="height:40%"></i></div><div class="b"><i style="height:82%"></i></div><div class="b"><i style="height:74%"></i></div><div class="b"><i style="height:58%"></i></div><div class="b"><i style="height:66%"></i></div><div class="b weak"><i style="height:48%"></i></div><div class="b"><i style="height:78%"></i></div><div class="b"><i style="height:88%"></i></div><div class="b"><i style="height:72%"></i></div><div class="b weak"><i style="height:44%"></i></div><div class="b"><i style="height:64%"></i></div><div class="b"><i style="height:90%"></i></div>
</div>
<div class="lbl"><span>8/16</span><span></span><span></span><span></span><span></span><span></span><span>8/22</span><span></span><span></span><span></span><span></span><span></span><span></span><span>8/29</span></div>
<div class="baseline-note"><span>┄ 你的通常水平 8 小时 57 分</span><span>昨晚 7 小时 53 分</span></div>
<div class="section-label">睡眠结构 · 清醒占比</div>
<div class="night"><div class="row1"><b>8月29日 · 7 小时 53 分</b><span>清醒 16 分 · 3.3%</span></div><div class="nbar"><div class="sleep" style="width:96.7%"></div><div class="awake" style="width:3.3%"></div></div></div>
<div class="night"><div class="row1"><b>8月28日 · 5 小时 48 分</b><span>清醒 25 分 · 6.8%</span></div><div class="nbar"><div class="sleep" style="width:93.2%"></div><div class="awake" style="width:6.8%"></div></div></div>
<div class="night"><div class="row1"><b>8月27日 · 9 小时 08 分</b><span>无清醒记录</span></div><div class="nbar"><div class="sleep" style="width:100%"></div></div></div>
<div class="section-label">身体变化记录</div>
<div class="index-row"><span class="d">8月29日</span><span class="t">睡眠不足</span><span class="st open">进行中</span></div>
<div class="index-row"><span class="d">8月28日</span><span class="t">原因还不明确</span><span class="st closed">已结束</span></div>
<div class="index-row"><span class="d">8月27日</span><span class="t">睡眠不足</span><span class="st closed">已结束</span></div>
<div class="index-row"><span class="d">8月23日</span><span class="t">恢复压力</span><span class="st closed">已结束</span></div>
"""
page("mock-b-history.html", ICONS_H, "PHE 档案", "历史 · 数据回看最近 14 天", history_body,
     "其余 5 天状态平稳，完整保存 · 点按行查看当天的判断与情况")

# ============ 问答 ============
qa_body = """
<div style="font-size:12.5px;color:#6b6f76;line-height:1.7;padding:4px 0 8px;border-bottom:1px solid #E5E1D8;">以你的身体状态为核心的决策助手。回答基于引擎的当前证据，不是通用聊天。</div>
<div style="height:12px"></div>
<div class="answer">
  <div class="lead">今天不适合安排高强度训练。</div>
  <div class="body">依据你的睡眠与恢复状态，今天以恢复为主。</div>
  <div class="news">
    <div class="item"><span class="no">一</span><span>优先保证今晚充足睡眠，避免熬夜。</span></div>
    <div class="item"><span class="no">二</span><span>可安排中等强度活动，穿插短暂休息。</span></div>
  </div>
  <div class="ans-meta">已按安全策略完成审查（不用于诊断）· 依据 8 月 29 日个人数据</div>
</div>
<div class="bubble">今天能练腿吗</div>
<div class="answer">
  <div class="lead">今天可以练腿，但建议降到轻重量。</div>
  <div class="body">昨晚睡眠偏低，力量表现可能受影响，注意组间休息。</div>
  <div class="ans-meta">已按安全策略完成审查（不用于诊断）· 依据 8 月 29 日个人数据</div>
</div>
<div class="bubble">今天能练胸吗</div>
"""
qa = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><style>%s</style></head><body>
<div class="phone">
  <div class="statusbar"><span>14:40</span><span>●●●</span></div>
  <div class="masthead"><div class="brand">PHE 问答</div><div class="date"><span>问我的状态</span><span>私人版 · 仅你可见</span></div><div class="rule"></div><div class="rule thin"></div></div>
  <div class="scroll">%s</div>
  <div class="inputbar"><div class="field">问一个和你身体状态有关的问题…</div><div class="send"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg></div></div>
  <div class="tabbar">%s</div>
</div></body></html>""" % (CSS, qa_body, ICONS)
io.open("mock-b-qa.html", "w", encoding="utf-8").write(qa)

# ============ 我的规律 ============
patterns_body = """
<div style="font-size:12.5px;color:#6b6f76;line-height:1.7;padding:4px 0 8px;border-bottom:1px solid #E5E1D8;">这些是从你的数据里反复验证过的关联。验证越久、次数越多，越可信赖。</div>
<div style="height:10px"></div>
<div class="section-label">已确认的规律</div>
<div class="pattern">
  <div class="trig">高强度训练 <span class="arrow">→</span> 静息心率偏高</div>
  <div class="stat">过去 5 次高强度训练后，4 次出现次日静息心率高于个人基线。</div>
  <div class="meta"><span>首次观察 8月16日</span><span>较稳定规律</span></div>
</div>
<div class="pattern">
  <div class="trig">熬夜 <span class="arrow">→</span> 次日睡眠补偿变长</div>
  <div class="stat">过去 4 次熬夜后，3 次出现次日睡眠时长明显增加。</div>
  <div class="meta"><span>首次观察 8月18日</span><span>较稳定规律</span></div>
</div>
<div class="section-label">正在观察</div>
<div class="obs"><span class="ic">观</span><span class="tx">「晚睡」之后静息心率是否升高 —— 已观察 3 次，暂无明显结论。</span></div>
<div class="obs"><span class="ic">观</span><span class="tx">「饮酒」之后睡眠清醒时间是否变长 —— 已观察 2 次，继续积累。</span></div>
<div class="section-label">数据不足</div>
<div class="obs"><span class="ic">待</span><span class="tx">「压力」与睡眠的关联 —— 需要更多记录，你在补充情况里提到压力的次数还不够。</span></div>
"""
page("mock-b-patterns.html", ICONS_P, "PHE 观察", "我的规律 · 反复验证的关联", patterns_body,
     "规律只来自你自己的数据，永远不会是通用建议")

# ============ 我的 ============
me_body = """
<div class="section-label">通知订阅</div>
<div class="setrow on"><div class="l"><div class="n">智能（当前）</div><div class="s">只通知可能影响下一步决策的重大变化；稳定日不打扰</div></div><span class="mark"></span></div>
<div class="setrow"><div class="l"><div class="n">安静</div><div class="s">仅重要安全事件</div></div><span class="mark"></span></div>
<div class="setrow" style="border-bottom:none;"><div class="l"><div class="n">每日</div><div class="s">每天状态更新 + 重要变化</div></div><span class="mark"></span></div>
<div class="setrow" style="border-bottom:none;"><div class="l"><div class="n">免打扰时段</div><div class="s">22:00 - 07:00（点按修改）</div></div><span style="color:#8a8e96;">›</span></div>
<div class="section-label">记录</div>
<div class="setrow" style="border-bottom:none;"><div class="l"><div class="n">通知与决策记录</div><div class="s">已发送与被抑制的通知都留痕</div></div><span style="color:#8a8e96;">›</span></div>
<div class="section-label">关于</div>
<div style="font-size:13px;line-height:1.85;color:#33373d;padding:2px 0 10px;">PHE 只对比「现在的你」和「你自己的长期正常状态」，帮你决定今天怎么安排。它不做诊断，没有健康分，你补充的每一条情况都会让它更懂你。</div>
<div class="section-label">版权页 · 技术信息</div>
<div class="colophon">
  <b>引擎</b> 累计完成 46 次完整分析，其中 12 次用到人工智能，其余靠复用之前的结果。<br>
  <b>连接</b> 已连接 · 47.111.229.39（点按修改服务器或令牌）<br>
  <b>数据</b> 全部数据仅你可见，可随时导出或删除。<br>
  <b>版本</b> PHE 0.2.0 · 2026年8月29日
</div>
"""
page("mock-b-me.html", ICONS_M, "PHE 版权页", "我的 · 订阅与记录", me_body,
     "个人健康引擎 · 不构成医疗建议")

print("4 pages written")
