"""飛食 FlyEats 數值平衡模擬器。

不呼叫 Streamlit / AI，純粹重複呼叫 app.py 的結算引擎（finalize_round）跑完整局，
統計不同決策策略下的通關率，用於難度分層門檻校準。

用法：
    python balance_simulator.py
"""
import random
import statistics

import app as game

CITIES = game.CITIES


# ── 策略：決策產生函式 ──────────────────────────────────────────────────────
# 每個策略函式簽名為 fn(state) -> list[dict]，回傳本回合要送進 finalize_round 的決策。
# 必須自行確保：最多 2 項、總花費 <= state["money"]（模擬 UI 的驗證邏輯）。

def _money_cap(state, max_amt=30):
    """模擬 UI 滑桿：5 的倍數、不超過現有資金。"""
    cap = max(5, int(state["money"] // 5) * 5)
    return min(max_amt, cap)


def strategy_random(state):
    """純隨機：每回合隨機選 0-2 項決策、隨機城市/金額。"""
    decisions = []
    budget = state["money"]
    n = random.randint(0, 2)
    pool = ["subsidy", "marketing", "commission", "expansion", "upgrade", "acquisition", "none"]
    for _ in range(n):
        t = random.choice(pool)
        if t in ("subsidy", "marketing"):
            cap = _money_cap(state, 30)
            if cap < 5 or budget < 5:
                continue
            amt = random.choice(range(5, cap + 1, 5))
            if amt > budget:
                continue
            city = random.choice(CITIES)
            decisions.append({"type": t, "city": city, "amount": amt})
            budget -= amt
        elif t == "commission":
            decisions.append({"type": "commission", "delta": random.choice([-game.COMMISSION_STEP, game.COMMISSION_STEP])})
        elif t == "expansion":
            not_expanded = [c for c in CITIES if c not in state.get("expanded_cities", [])]
            if not_expanded and budget >= game.EXPANSION_COST:
                decisions.append({"type": "expansion", "city": random.choice(not_expanded)})
                budget -= game.EXPANSION_COST
        elif t == "upgrade":
            upgs = state.get("upgrades", {})
            catalog = [
                ("aiRouting", game.UPGRADE_AI_ROUTING_COST),
                ("dynamicPricing", game.UPGRADE_DYNAMIC_PRICING_COST),
                ("exclusiveMerchant", game.UPGRADE_EXCLUSIVE_MERCHANT_COST),
            ]
            available = [(k, c) for k, c in catalog if not upgs.get(k) and budget >= c]
            if available:
                k, c = random.choice(available)
                decisions.append({"type": "upgrade", "upgradeType": k})
                budget -= c
        elif t == "acquisition":
            comp_money = state.get("competitor_money", game.COMPETITOR_INITIAL_MONEY)
            if (not state.get("competitor_acquired") and not state.get("competitor_bankrupt")
                    and comp_money <= game.ACQUISITION_THRESHOLD and budget >= game.ACQUISITION_COST):
                decisions.append({"type": "acquisition"})
                budget -= game.ACQUISITION_COST
        if len(decisions) >= 2:
            break
    return decisions[:2]


def strategy_single_city(city="台北"):
    """單城集中：每回合全力補貼同一城市。"""
    def fn(state):
        amt = _money_cap(state, 30)
        if amt < 5 or state["money"] < 5:
            return []
        return [{"type": "subsidy", "city": city, "amount": amt}]
    return fn


def strategy_distributed(state):
    """分散投資：依回合數輪流補貼三城市。"""
    city = CITIES[(state["round"] - 1) % len(CITIES)]
    amt = _money_cap(state, 20)
    if amt < 5 or state["money"] < 5:
        return []
    return [{"type": "subsidy", "city": city, "amount": amt}]


def strategy_tech_then_harvest(state):
    """先研發後收割（路線B）：買 dynamicPricing+aiRouting → 台中盾牌 → 高雄主攻。

    路線B 的核心邏輯：
    - 先研發（R1-R2）：買兩個升級（共 55萬），升級回合補台中 10萬維持盾牌
    - 後收割（R3+）：台中 15萬（盾牌，確保競爭者持續打台中而非高雄）
                      + 高雄 10萬（主攻，subsidy_mult=1.3 + aiRouting 加速）
    - 高雄連續 ≥3 回合時重置（只補台中），讓高雄 consecutive 歸零恢復效率
    - 比路線B_科技致富（strategy_route_b）多花 25萬在 dynamicPricing，
      導致少 1 回合高雄攻勢，通關率略低（~30-40% vs ~44%）
    """
    decisions = []
    upgs = state.get("upgrades", {})
    r = state["round"]
    min_rider = min(state["cities"][c]["rider_satisfaction"] for c in CITIES)

    # 決策 1：降抽成護外送員
    if min_rider < 58 and state["commission_rate"] > game.COMMISSION_MIN:
        decisions.append({"type": "commission", "delta": -game.COMMISSION_STEP})

    # 決策 2：買升級（dynamicPricing → aiRouting，研發期 2 回合）
    if len(decisions) < 2:
        for k, c in [("dynamicPricing", game.UPGRADE_DYNAMIC_PRICING_COST),
                     ("aiRouting",      game.UPGRADE_AI_ROUTING_COST)]:
            if not upgs.get(k) and state["money"] >= c + 10:
                decisions.append({"type": "upgrade", "upgradeType": k})
                # 升級回合只剩一個決策槽：補台中 10萬維持盾牌
                amt_tc = min(10, _money_cap(state, 10))
                if amt_tc >= 5 and state["money"] >= c + amt_tc:
                    decisions.append({"type": "subsidy", "city": "台中", "amount": amt_tc})
                return decisions[:2]

    # 決策 3：收割期 - 台中盾牌 + 高雄主攻
    if len(decisions) < 2:
        ks_consec = state["cities"]["高雄"].get("consecutive_subsidy_count", 0)
        final_round = (r == 10)

        if final_round:
            # 最終回合：全力衝高雄
            amt_ks = _money_cap(state, 20)
            amt_tc = 15
            if state["money"] >= amt_ks + amt_tc:
                decisions.append({"type": "subsidy", "city": "台中", "amount": amt_tc})
                decisions.append({"type": "subsidy", "city": "高雄", "amount": amt_ks})
            elif amt_ks >= 5 and state["money"] >= amt_ks:
                decisions.append({"type": "subsidy", "city": "高雄", "amount": amt_ks})
        elif ks_consec >= 3:
            # 高雄重置回合：台中盾牌 + 台北消費者滿意度緩衝
            amt_tc = _money_cap(state, 15)
            amt_tp = 10
            if state["money"] >= amt_tc + amt_tp + 5 and amt_tc >= 5:
                decisions.append({"type": "subsidy", "city": "台中", "amount": amt_tc})
                decisions.append({"type": "subsidy", "city": "台北", "amount": amt_tp})
            elif amt_tc >= 5 and state["money"] >= amt_tc + 5:
                decisions.append({"type": "subsidy", "city": "台中", "amount": amt_tc})
        else:
            # 一般回合：台中 15萬（盾牌）+ 高雄 10萬（主攻）
            amt_tc = 15
            amt_ks = _money_cap(state, 10)
            if state["money"] >= amt_tc + amt_ks and amt_ks >= 5:
                decisions.append({"type": "subsidy", "city": "台中", "amount": amt_tc})
                decisions.append({"type": "subsidy", "city": "高雄", "amount": amt_ks})
            elif amt_tc >= 5 and state["money"] >= amt_tc:
                decisions.append({"type": "subsidy", "city": "台中", "amount": amt_tc})

    return decisions[:2]


def strategy_route_b(state):
    """路線 B：科技致富（v3 盾牌版）

    核心發現（CITIES = [台北, 台中, 高雄]，Rule 1 按序觸發）：
    只要台中 ≥18%，競爭者 Rule 1 就先打台中，高雄得以自由累積。

    Phase 1 (R1)：買 aiRouting（省 25萬不買 dynamicPricing）
                  + 補台中 10萬建立早期盾牌
    Phase 2 (R2-R4)：每輪補台中 15萬（維持盾牌）+ 補高雄 10萬（連續 3 輪）
    Phase 3 (R5)：只補台中（跳過高雄，重置 consecutive_subsidy_count=0）
    Phase 4 (R6-R8)：再補台中 15萬 + 高雄 10萬（連續 3 輪，效率全開）
    Phase 5 (R9)：只補台中（再次重置）
    Phase 6 (R10)：高雄 25萬 + 台中 15萬（全力衝刺，台中擋最後一波反撲）

    不降抽成（保持 0.30 收入）、只買一個升級（保留資金給補貼）。
    台中連續遞減後期只剩 25% 效率，但 15萬仍給 6.6%，足以維持盾牌 ≥18%。
    """
    upgrades = state["upgrades"]
    r = state["round"]
    decisions = []

    # Phase 1：買 aiRouting + 補台中開盾
    if not upgrades.get("aiRouting") and state["money"] >= game.UPGRADE_AI_ROUTING_COST + 10:
        decisions.append({"type": "upgrade", "upgradeType": "aiRouting"})
        amt_tc = _money_cap(state, 10)
        if amt_tc >= 5 and state["money"] >= game.UPGRADE_AI_ROUTING_COST + amt_tc:
            decisions.append({"type": "subsidy", "city": "台中", "amount": amt_tc})
        return decisions[:2]

    # Phase 2-6：盾牌 + 主攻
    # 重置回合：R5, R9（只補台中，讓高雄 consecutive 歸零）
    reset_round = (r == 5 or r == 9)
    # 最後衝刺：R10 全力
    final_round = (r == 10)

    if reset_round:
        # 只補台中，高雄 consecutive 自動歸零
        # R5 額外降抽成一次（0.30→0.25）護台北外送員滿意度，否則台北累積衰減拉低全局 rider_sat
        if r == 5 and state["commission_rate"] > game.COMMISSION_MIN:
            decisions.append({"type": "commission", "delta": -game.COMMISSION_STEP})
        amt = _money_cap(state, 25)
        if amt >= 5 and state["money"] >= amt:
            decisions.append({"type": "subsidy", "city": "台中", "amount": amt})
    elif final_round:
        # R10：全力衝高雄 + 台中擋最後反撲
        amt_ks = _money_cap(state, 25)
        amt_tc = 15
        if state["money"] >= amt_ks + amt_tc:
            decisions.append({"type": "subsidy", "city": "高雄", "amount": amt_ks})
            decisions.append({"type": "subsidy", "city": "台中", "amount": amt_tc})
        elif state["money"] >= amt_ks:
            decisions.append({"type": "subsidy", "city": "高雄", "amount": amt_ks})
    else:
        # 一般回合：台中 15萬（盾牌）+ 高雄 10萬（主攻）
        amt_tc = min(15, _money_cap(state, 15))
        amt_ks = _money_cap(state, 10)
        total = amt_tc + amt_ks
        if state["money"] >= total and amt_ks >= 5 and amt_tc >= 5:
            decisions.append({"type": "subsidy", "city": "高雄", "amount": amt_ks})
            decisions.append({"type": "subsidy", "city": "台中", "amount": amt_tc})
        elif state["money"] >= amt_ks and amt_ks >= 5:
            decisions.append({"type": "subsidy", "city": "高雄", "amount": amt_ks})

    return decisions[:2]


def strategy_siege(city="台北"):
    """圍剿對手：持續全力衝同一城市，目標是逼對手破產（搭配收購機會）。"""
    def fn(state):
        decisions = []
        amt = _money_cap(state, 30)
        if amt >= 5 and state["money"] >= 5:
            decisions.append({"type": "subsidy", "city": city, "amount": amt})
        comp_money = state.get("competitor_money", game.COMPETITOR_INITIAL_MONEY)
        remaining_budget = state["money"] - (amt if decisions else 0)
        if (not state.get("competitor_acquired") and not state.get("competitor_bankrupt")
                and comp_money <= game.ACQUISITION_THRESHOLD and remaining_budget >= game.ACQUISITION_COST
                and len(decisions) < 2):
            decisions.append({"type": "acquisition"})
        return decisions[:2]
    return fn


def strategy_balanced(state):
    """兼顧滿意度：每 3 回合降一次抽成維持滿意度，其他回合補貼主力城市（同時使用 2 種決策類型）。"""
    if state["round"] % 3 == 0 and state["commission_rate"] > game.COMMISSION_MIN:
        return [{"type": "commission", "delta": -game.COMMISSION_STEP}]
    amt = _money_cap(state, 25)
    if amt < 5 or state["money"] < 5:
        return []
    return [{"type": "subsidy", "city": "台北", "amount": amt}]


def strategy_share_plus_sat(state):
    """兩階段通關：台北養滿意度 → 高雄後期衝市占。

    Phase 1 (r1-6): 台北 10萬/round（保守燒錢，累積台北 consumer_sat，
                    台北最終達 ~84，加權均值 ~63 ≥ 60 ✓）
    Phase 2 (r7+): 高雄 30萬，但每補貼 2 回合就穿插一回合台北（重置連續計數），
                   讓第 3 次高雄補貼以 100% 效率執行：
                   r7(高雄+68%)→51% / r8(+51%)→72% / r9(台北重置) / r10(高雄+68%)→72% ✓
    全程: min_rider < 58 → 降抽成（+7.5 全城，共需 2 次），加權外送滿意 ~71 ✓。
    """
    decisions = []
    r         = state["round"]
    min_rider = min(state["cities"][c]["rider_satisfaction"] for c in CITIES)

    # 決策 1：降抽成護外送員滿意度（全程最多觸發 2 次，抽成 0.30→0.20）
    if min_rider < 58 and state["commission_rate"] > game.COMMISSION_MIN:
        decisions.append({"type": "commission", "delta": -game.COMMISSION_STEP})

    # 決策 2：主補貼
    if len(decisions) < 2:
        if r >= 7:
            # Phase 2：高雄衝刺，但連續 ≥2 次且還沒到最後一回合時穿插台北重置
            gs_consec = state["cities"]["高雄"].get("consecutive_subsidy_count", 0)
            if gs_consec >= 2 and r < 10:
                target, budget = "台北", 10   # 重置高雄 consecutive → 下回合高雄恢復 100% 效率
            else:
                target, budget = "高雄", 30   # 正常衝刺
        else:
            target, budget = "台北", 10       # Phase 1：台北護 consumer_sat

        amt    = _money_cap(state, budget)
        safety = 8 + 10  # 固定成本 + 緩衝
        if amt >= 5 and state["money"] >= amt + safety:
            decisions.append({"type": "subsidy", "city": target, "amount": amt})

    return decisions[:2]


def strategy_brand_route_c(state):
    """路線 C 品牌正向循環（挑戰模式）：台中品牌投資 → 台中飛輪盾牌 + 高雄主攻。

    Phase 1 (r1-2): 台中品牌經營（brand_count=2 + consumer_sat≥75，啟動飛輪 +7.5%/回合）
    Phase 2 (r3+):  台中 15萬（飛輪盾牌）+ 高雄 15萬（subsidy_mult 1.3 主攻）
                    高雄連續 ≥3 回合時穿插台中 20萬單獨重置
    差異於路線B：不買科技升級，靠品牌飛輪免費加成台中守城。
    """
    decisions = []
    r         = state["round"]
    min_rider = min(state["cities"][c]["rider_satisfaction"] for c in CITIES)

    # 決策 1：降抽成護外送員
    if min_rider < 58 and state["commission_rate"] > game.COMMISSION_MIN:
        decisions.append({"type": "commission", "delta": -game.COMMISSION_STEP})

    if len(decisions) < 2:
        brand_count_tc = state.get("brand_count", {}).get("台中", 0)
        flywheel_ready = (
            brand_count_tc >= game.BRAND_GROWTH_MIN_COUNT and
            state["cities"]["台中"]["consumer_satisfaction"] >= game.BRAND_GROWTH_THRESHOLD
        )

        if r <= 2 and not flywheel_ready and state["config"].get("brand_management_enabled"):
            # Phase 1：台中品牌投資
            if state["money"] >= game.BRAND_MGMT_COST + 10:
                decisions.append({"type": "brand_management", "city": "台中"})
        else:
            # Phase 2：台中飛輪盾牌 15萬 + 高雄主攻 15萬
            ks_consec = state["cities"]["高雄"].get("consecutive_subsidy_count", 0)

            if ks_consec >= 3 and r < 10:
                # 高雄重置：台中飛輪盾牌 + 台北消費者滿意度緩衝
                amt_tc = _money_cap(state, 15)
                amt_tp = 10
                if state["money"] >= amt_tc + amt_tp + 5 and amt_tc >= 5:
                    decisions.append({"type": "subsidy", "city": "台中", "amount": amt_tc})
                    decisions.append({"type": "subsidy", "city": "台北", "amount": amt_tp})
                elif amt_tc >= 5 and state["money"] >= amt_tc + 5:
                    decisions.append({"type": "subsidy", "city": "台中", "amount": amt_tc})
            else:
                # 台中 15萬（飛輪盾牌）+ 高雄 15萬（主攻）
                amt_tc = 15
                amt_ks = _money_cap(state, 15)
                if state["money"] >= amt_tc + amt_ks + 5 and amt_ks >= 5:
                    decisions.append({"type": "subsidy", "city": "台中", "amount": amt_tc})
                    decisions.append({"type": "subsidy", "city": "高雄", "amount": amt_ks})
                elif amt_tc >= 5 and state["money"] >= amt_tc + 5:
                    decisions.append({"type": "subsidy", "city": "台中", "amount": amt_tc})

    return decisions[:2]


def strategy_route_a(state):
    """路線 A：市占主導（challenge 模式）- dynamicPricing 效率 + 台中主攻 + 台北消費者基礎。

    買 dynamicPricing（25萬，R1），讓台中份額 <35% 時補貼效率 ×1.5，搶佔市占。
    與路線B的差異：
    - 路線A：dynamicPricing + 台中主攻（市場規模大、帶動消費者滿意度），台北輔助
    - 路線B：aiRouting+dynamicPricing + 高雄主攻（subsidy_mult 1.3）+ 台中盾牌
    台中連續 ≥3 回合遞減時重置台北（避免台北消費者滿意度崩盤）。
    """
    decisions = []
    upgs = state.get("upgrades", {})
    min_rider = min(state["cities"][c]["rider_satisfaction"] for c in CITIES)

    # 降抽成護外送員（優先）
    if min_rider < 58 and state["commission_rate"] > game.COMMISSION_MIN:
        decisions.append({"type": "commission", "delta": -game.COMMISSION_STEP})

    if len(decisions) < 2:
        # 買 dynamicPricing（補貼效率加成，只買一次）
        if not upgs.get("dynamicPricing") and state["money"] >= game.UPGRADE_DYNAMIC_PRICING_COST + 10:
            decisions.append({"type": "upgrade", "upgradeType": "dynamicPricing"})
            amt_tc = min(10, _money_cap(state, 10))
            if amt_tc >= 5 and state["money"] >= game.UPGRADE_DYNAMIC_PRICING_COST + amt_tc:
                decisions.append({"type": "subsidy", "city": "台中", "amount": amt_tc})
            return decisions[:2]

        tc_consec = state["cities"]["台中"].get("consecutive_subsidy_count", 0)

        if tc_consec >= 3:
            # 重置：台北補貼維持消費者滿意度，台中 consecutive 歸零
            amt_tp = _money_cap(state, 25)
            if amt_tp >= 5 and state["money"] >= amt_tp + 5:
                decisions.append({"type": "subsidy", "city": "台北", "amount": amt_tp})
        else:
            # 台中 18萬主攻（dynamicPricing 加成）+ 台北 10萬消費者基礎
            amt_tc = _money_cap(state, 18)
            amt_tp = 10
            if state["money"] >= amt_tc + amt_tp + 5 and amt_tc >= 5:
                decisions.append({"type": "subsidy", "city": "台中", "amount": amt_tc})
                decisions.append({"type": "subsidy", "city": "台北", "amount": amt_tp})
            elif amt_tc >= 5 and state["money"] >= amt_tc + 5:
                decisions.append({"type": "subsidy", "city": "台中", "amount": amt_tc})

    return decisions[:2]


def strategy_route_d(state):
    """路線 D：併購終結（challenge 模式）

    同路線 A，但全程監測：若對手資金 ≤ 門檻且自身資金充足則立即收購。
    """
    comp_money = state.get("competitor_money", 999)
    if (state["config"].get("acquisition_enabled")
            and comp_money <= game.ACQUISITION_THRESHOLD
            and state["money"] >= game.ACQUISITION_COST):
        return [{"type": "acquisition"}]
    return strategy_route_a(state)


STRATEGIES = {
    "純隨機":            strategy_random,
    "單城集中":          strategy_single_city("台北"),
    "分散投資":          strategy_distributed,
    "先研發後收割":       strategy_tech_then_harvest,
    "路線A_市占主導":     strategy_route_a,
    "路線B_科技致富":     strategy_route_b,
    "圍剿對手":          strategy_siege("台北"),
    "兼顧滿意度":        strategy_balanced,
    "市占+滿意度":       strategy_share_plus_sat,
    "品牌正向循環(路線C)": strategy_brand_route_c,
    "路線D_併購終結":     strategy_route_d,
}


# ── 模擬執行器 ──────────────────────────────────────────────────────────────

def run_one_game(decision_fn, initial_money=120.0, max_rounds=10, difficulty="challenge"):
    state = game.init_game_state(initial_money=initial_money, difficulty=difficulty)
    state["max_rounds"] = max_rounds
    bankrupt_round = None
    for r in range(1, max_rounds + 1):
        if state["config"]["black_swan"]:
            game._roll_swan_event(state)
        decisions = decision_fn(state)
        # 預算保險：若策略不小心超支，砍到買得起為止（模擬 UI 擋下超支）
        total_spend = 0.0
        for d in decisions:
            if d["type"] in ("subsidy", "marketing"):
                total_spend += d.get("amount", 0)
            elif d["type"] == "expansion":
                total_spend += game.EXPANSION_COST
            elif d["type"] == "upgrade":
                costs = {
                    "aiRouting": game.UPGRADE_AI_ROUTING_COST,
                    "dynamicPricing": game.UPGRADE_DYNAMIC_PRICING_COST,
                    "exclusiveMerchant": game.UPGRADE_EXCLUSIVE_MERCHANT_COST,
                }
                total_spend += costs[d["upgradeType"]]
            elif d["type"] == "acquisition":
                total_spend += game.ACQUISITION_COST
        total_spend += game.BRAND_MGMT_COST * sum(1 for d in decisions if d["type"] == "brand_management")
        if total_spend > state["money"]:
            decisions = []  # 超支就視為本回合不行動（保守處理）
        state = game.finalize_round(state, decisions)
        if bankrupt_round is None and state["money"] <= 0:
            bankrupt_round = r
        if state["game_result"] in ("win", "lose"):
            break
    max_share = max(cd["share"] for cd in state["cities"].values())
    sat = game.calculate_overall_satisfaction(state)
    return {
        "result": state["game_result"],
        "rounds_played": state["round"] - 1,
        "final_money": state["money"],
        "max_share": max_share,
        "consumer_sat": sat["consumer"],
        "rider_sat": sat["rider"],
        "bankrupt_round": bankrupt_round,
    }


def run_trials(decision_fn, n_trials, **kwargs):
    results = [run_one_game(decision_fn, **kwargs) for _ in range(n_trials)]
    wins   = [r for r in results if r["result"] == "win"]
    losses = [r for r in results if r["result"] != "win"]
    early_bankrupt = sum(1 for r in results if r["bankrupt_round"] is not None and r["bankrupt_round"] <= 3)
    avg_rounds = statistics.mean(r["rounds_played"] for r in results)
    avg_money  = statistics.mean(r["final_money"] for r in results)
    avg_share  = statistics.mean(r["max_share"] for r in results)
    avg_csat   = statistics.mean(r["consumer_sat"] for r in results)
    avg_rsat   = statistics.mean(r["rider_sat"] for r in results)
    # 敗因分類（僅 loss 樣本）
    miss_share = sum(1 for r in losses if r["max_share"] < 0.70) if losses else 0
    miss_csat  = sum(1 for r in losses if r["consumer_sat"] < 60) if losses else 0
    miss_rsat  = sum(1 for r in losses if r["rider_sat"] < 60) if losses else 0
    return {
        "n": n_trials,
        "win_rate": len(wins) / n_trials,
        "early_bankrupt_rate": early_bankrupt / n_trials,
        "avg_rounds": avg_rounds,
        "avg_final_money": avg_money,
        "avg_max_share": avg_share,
        "avg_csat": avg_csat,
        "avg_rsat": avg_rsat,
        "miss_share_pct": miss_share / len(losses) if losses else 0,
        "miss_csat_pct":  miss_csat  / len(losses) if losses else 0,
        "miss_rsat_pct":  miss_rsat  / len(losses) if losses else 0,
    }


def print_report(title, strategy_results):
    print(f"\n{'='*70}\n{title}\n{'='*70}")
    print(f"{'策略':12s}  {'通關率':>7s}  {'平均市占':>8s}  {'消費者':>6s}  {'外送員':>6s}  敗因(市占/消費者/外送員)")
    for name, r in strategy_results.items():
        print(
            f"{name:12s}  {r['win_rate']*100:6.1f}%  {r['avg_max_share']*100:7.1f}%"
            f"  {r['avg_csat']:5.1f}  {r['avg_rsat']:5.1f}"
            f"  {r['miss_share_pct']*100:4.0f}% / {r['miss_csat_pct']*100:4.0f}% / {r['miss_rsat_pct']*100:4.0f}%"
        )


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    N_RANDOM = 2000
    N_DIRECTED = 500

    results = {}
    for name, fn in STRATEGIES.items():
        n = N_RANDOM if name == "純隨機" else N_DIRECTED
        results[name] = run_trials(fn, n)

    print_report(
        f"挑戰模式（現行全開版本，WIN_MONEY={game.WIN_MONEY:.0f}萬 / WIN_SHARE={game.WIN_SHARE*100:.0f}% / "
        f"對手初始資金={game.COMPETITOR_INITIAL_MONEY:.0f}萬）",
        results,
    )
