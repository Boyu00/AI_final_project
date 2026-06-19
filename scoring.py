"""飛食 v1.23 結局評分模組：策略徽章、四維評分、概念地圖。"""
from __future__ import annotations

CITIES = ["台北", "台中", "高雄"]
BRAND_GROWTH_THRESHOLD = 75   # 同步 app.py
TOTAL_CONCEPTS = 13

CONCEPT_INFO: dict[str, dict] = {
    "subsidy_trap":             {"name": "補貼陷阱",         "icon": "💰"},
    "diminishing_returns":      {"name": "邊際效益遞減",      "icon": "📉"},
    "front_loading":            {"name": "前置投資",          "icon": "🔮"},
    "opportunity_cost":         {"name": "機會成本",          "icon": "⚖️"},
    "loss_aversion":            {"name": "損失厭惡",          "icon": "😨"},
    "economies_of_scale":       {"name": "規模經濟",          "icon": "🗺️"},
    "focus_strategy":           {"name": "集中化策略",        "icon": "🎯"},
    "brand_premium":            {"name": "品牌溢價",          "icon": "✨"},
    "network_effect":           {"name": "網路效應",          "icon": "🌐"},
    "sunk_cost":                {"name": "沉沒成本",          "icon": "🪨"},
    "prisoners_dilemma":        {"name": "囚徒困境",          "icon": "🤝"},
    "price_sensitive_consumer": {"name": "價格敏感型消費者",  "icon": "🏷️"},
    "anchoring_effect":         {"name": "錨定效應",          "icon": "⚓"},
}

PRIMARY_BADGE_INFO: dict[str, tuple[str, str]] = {
    "併購終結":   ("🤝", "以資本終結競爭，完成最後一擊"),
    "品牌經營家": ("🌸", "細水長流——用口碑而非補貼贏得市場"),
    "科技先驅":   ("🚀", "用技術槓桿彎道超車，以研發換效率"),
    "市場霸主":   ("🏆", "以壓倒性市占統治市場"),
    "穩健經營家": ("💼", "均衡部署每項資源，穩健達標"),
    "新手上路":   ("📗", "這局學到了很多——下一局會更好"),
}

SECONDARY_BADGE_INFO: dict[str, tuple[str, str]] = {
    "三城制霸":   ("🌍", "三城市占均達 20%，全面布局"),
    "零危機":     ("🕊️", "全局無任何外送荒或負評爆炸"),
    "逆風翻盤":   ("🔄", "曾資金告急，最終仍逆轉勝出"),
    "概念蒐集家": ("📚", "觸發 10 個以上經濟學概念"),
}

BADGE_TONES: dict[str, str] = {
    "併購終結":   "請用銳利的創投合夥人語氣，強調資本效率與出手時機",
    "品牌經營家": "請用品牌策略顧問語氣，強調長期口碑的複利效果",
    "科技先驅":   "請用矽谷加速器導師語氣，強調技術投資報酬率與先行者優勢",
    "市場霸主":   "請用市場競爭策略顧問語氣，強調制高點佔領邏輯",
    "穩健經營家": "請用 MBA 案例教授語氣，強調均衡策略與風險管控",
    "新手上路":   "請用鼓勵型商業導師語氣，以正向肯定為主，只點出最重要一個改進方向",
}


# ── 內部工具 ──────────────────────────────────────────────────────────────────

def _triggered_concepts(state: dict) -> set[str]:
    seen: set[str] = set()
    for e in state.get("event_log", []):
        if e["event_type"] in ("hint_interaction", "concept_triggered"):
            cid = e["data"].get("concept_id")
            if cid:
                seen.add(cid)
    return seen


# ── 主要 API ──────────────────────────────────────────────────────────────────

def determine_badges(
    state: dict,
) -> tuple[str, str, str, str | None, str | None, str | None]:
    """
    回傳 (primary_key, primary_icon, primary_desc,
           secondary_key | None, secondary_icon | None, secondary_desc | None)
    """
    result   = state.get("game_result", "lose")
    history  = state.get("history", [])
    upgrades = state.get("upgrades", {})
    brand_count = state.get("brand_count", {})
    cities   = state["cities"]
    config   = state["config"]

    # ── 主徽章（依優先順序判斷）
    if state.get("competitor_acquired"):
        primary = "併購終結"

    elif (
        config.get("brand_management_enabled") and
        sum(brand_count.values()) >= 4 and
        any(
            brand_count.get(c, 0) >= 2 and
            cities[c]["consumer_satisfaction"] >= BRAND_GROWTH_THRESHOLD
            for c in CITIES
        ) and
        any(h.get("brand_growth_cities") for h in history)
    ):
        primary = "品牌經營家"

    elif (
        config.get("tech_tree") and
        sum(1 for v in upgrades.values() if v) >= 2 and
        any(
            any(d.get("type") == "upgrade" for d in h.get("decisions", []))
            for h in history if h["round"] <= 2
        )
    ):
        primary = "科技先驅"

    elif max(cities[c]["share"] for c in CITIES) >= 0.70:
        primary = "市場霸主"

    elif result == "win":
        primary = "穩健經營家"

    else:
        primary = "新手上路"

    p_icon, p_desc = PRIMARY_BADGE_INFO[primary]

    # ── 副徽章（最多 1 個，取第一個符合）
    secondary: str | None = None
    if all(cities[c]["share"] >= 0.20 for c in CITIES):
        secondary = "三城制霸"
    elif not any(h.get("crisis_cities") for h in history):
        secondary = "零危機"
    elif result == "win" and any(h["money_after"] < 50 for h in history):
        secondary = "逆風翻盤"
    elif len(_triggered_concepts(state)) >= 10:
        secondary = "概念蒐集家"

    if secondary:
        s_icon, s_desc = SECONDARY_BADGE_INFO[secondary]
    else:
        s_icon = s_desc = None

    return primary, p_icon, p_desc, secondary, s_icon, s_desc


def calculate_grade(state: dict) -> dict:
    """
    回傳 {efficiency, market, reputation, depth, total, grade}
    各維度 0-100，grade 為 S/A/B/C/D。
    """
    history = state.get("history", [])
    cities  = state["cities"]
    config  = state["config"]

    initial_money = config.get("initial_money", 120.0)

    # 2. 市場掌控（25%）：三城市占均值 / 0.6（先算，效率也需要）
    avg_share = sum(cities[c]["share"] for c in CITIES) / 3
    market = min(100.0, avg_share / 0.6 * 100)

    # 1. 經營效率（30%）：現金保存（60%）＋市占建立（40%）
    #    舊公式以支出為分母，什麼都不做反而分母→1，效率虛高。
    #    新公式：不行動 → 市占低 → market_component ≈ 0，整體效率必然低落。
    cash_component   = min(60.0, state["money"] / initial_money * 60)
    market_component = min(40.0, avg_share / 0.5 * 40)
    efficiency = cash_component + market_component

    # 3. 品牌聲譽（25%）：(消費者均值 + 外送員均值) / 2（市場加權）
    total_market = sum(cities[c]["market"] for c in CITIES)
    c_sat = sum(cities[c]["market"] * cities[c]["consumer_satisfaction"] for c in CITIES) / total_market
    if config.get("dual_satisfaction"):
        r_sat = sum(cities[c]["market"] * cities[c]["rider_satisfaction"] for c in CITIES) / total_market
        reputation = (c_sat + r_sat) / 2
    else:
        reputation = c_sat

    # 4. 策略深度（20%）：概念覆蓋率 60% + 決策多樣性 40%
    concept_count = len(_triggered_concepts(state))
    concept_score = min(100.0, concept_count / TOTAL_CONCEPTS * 100)

    used_types: set[str] = set()
    for h in history:
        for d in h.get("decisions", []):
            t = d.get("type", "")
            if t:
                used_types.add(t)
    available = 3  # subsidy / marketing / commission 永遠可用
    for flag in ("expansion_enabled", "tech_tree", "acquisition_enabled", "brand_management_enabled"):
        if config.get(flag):
            available += 1
    diversity = min(1.0, len(used_types) / max(available, 1))
    depth = min(100.0, concept_score * 0.6 + diversity * 100 * 0.4)

    total = efficiency * 0.30 + market * 0.25 + reputation * 0.25 + depth * 0.20

    if   total >= 85: grade = "S"
    elif total >= 70: grade = "A"
    elif total >= 55: grade = "B"
    elif total >= 40: grade = "C"
    else:             grade = "D"

    return {
        "efficiency": round(efficiency, 1),
        "market":     round(market, 1),
        "reputation": round(reputation, 1),
        "depth":      round(depth, 1),
        "total":      round(total, 1),
        "grade":      grade,
    }


def build_concept_map(state: dict) -> dict[str, dict]:
    """
    回傳 {concept_id: {name, icon, status, hint}}
    status: "triggered" | "near_miss" | "unexplored"
    """
    triggered = _triggered_concepts(state)
    config    = state["config"]

    result: dict[str, dict] = {}

    for cid, info in CONCEPT_INFO.items():
        name, icon = info["name"], info["icon"]
        if cid in triggered:
            result[cid] = {"name": name, "icon": icon, "status": "triggered", "hint": ""}
        else:
            hint   = _near_miss_hint(cid, state, triggered)
            status = "near_miss" if hint else "unexplored"
            result[cid] = {"name": name, "icon": icon, "status": status, "hint": hint}

    # war_of_attrition：僅 acquisition_enabled 才顯示
    if config.get("acquisition_enabled"):
        cid = "war_of_attrition"
        status = "triggered" if cid in triggered else "unexplored"
        result[cid] = {"name": "消耗戰", "icon": "🔥", "status": status, "hint": ""}

    return result


def _near_miss_hint(cid: str, state: dict, triggered: set) -> str:
    """達到 near_miss 條件回傳提示文字，否則回傳空字串。"""
    history    = state.get("history", [])
    cities     = state["cities"]
    brand_count = state.get("brand_count", {})
    config     = state["config"]

    if cid == "brand_premium" and config.get("brand_management_enabled"):
        max_csat = max(cities[c]["consumer_satisfaction"] for c in CITIES)
        if 65 <= max_csat < BRAND_GROWTH_THRESHOLD:
            gap = BRAND_GROWTH_THRESHOLD - max_csat
            return f"消費者滿意度最高 {max_csat:.0f}，差 {gap:.0f} 點就能觸發品牌溢價"

    elif cid == "network_effect":
        max_share = max(cities[c]["share"] for c in CITIES)
        if 0.30 <= max_share < 0.40:
            return f"市占最高 {max_share*100:.0f}%，差 {(0.40 - max_share)*100:.0f}% 可觸發網路效應"

    elif cid == "sunk_cost":
        for c in CITIES:
            cur = cur_max = 0
            for h in history:
                if any(d.get("city") == c for d in h.get("decisions", [])):
                    cur += 1
                    cur_max = max(cur_max, cur)
                else:
                    cur = 0
            if cur_max == 2:
                return f"你在{c}連續投資 2 季，再多一季就能體驗沉沒成本效應"

    elif cid == "price_sensitive_consumer":
        for c in CITIES:
            last_sub: int | None = None
            for i, h in enumerate(history):
                if any(d.get("city") == c and d.get("type") == "subsidy" for d in h.get("decisions", [])):
                    last_sub = i
            if last_sub is not None and last_sub < len(history) - 1:
                after = [
                    history[j]["shares_after"].get(c, 0)
                    for j in range(last_sub, min(last_sub + 3, len(history)))
                ]
                if len(after) >= 2 and after[1] < after[0] and (len(after) < 3 or after[2] >= after[1]):
                    return f"停止補貼{c}後市占有下滑，再觀察多一季可觸發價格敏感型消費者概念"

    elif cid == "diminishing_returns":
        for c in CITIES:
            if cities[c].get("consecutive_subsidy_count", 0) >= 1:
                return f"{c}目前連續補貼中，繼續補貼可觸發邊際效益遞減提示"

    return ""
