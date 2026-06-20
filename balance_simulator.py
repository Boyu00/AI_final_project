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
    decisions = []
    r = state["round"]
    min_rider = min(state["cities"][c]["rider_satisfaction"] for c in game.CITIES)
    
    # 決策 1：降抽成護外送員滿意度（避免外送荒）
    if min_rider < 58 and state["commission_rate"] > game.COMMISSION_MIN:
        decisions.append({"type": "commission", "delta": -game.COMMISSION_STEP})

    # 決策 2/3：購買科技
    if len(decisions) < 2:
        upgs = state.get("upgrades", {})
        catalog = [
            ("dynamicPricing", game.UPGRADE_DYNAMIC_PRICING_COST),
            ("aiRouting", game.UPGRADE_AI_ROUTING_COST),
            ("exclusiveMerchant", game.UPGRADE_EXCLUSIVE_MERCHANT_COST),
        ]
        # 依照順序買：動態定價 -> AI路徑 -> 獨家特約
        for k, c in catalog:
            if not upgs.get(k) and state["money"] >= c + 15: # 保留 15 萬安全水位
                decisions.append({"type": "upgrade", "upgradeType": k})
                break

    # 決策 3/4：有剩餘行動點數時，進行投資
    if len(decisions) < 2:
        # Phase 2: 台中或台北投資
        # 由於 AI路徑 加成補貼，我們在科技出關後重砸台北
        target, budget = "台北", 25
        amt = _money_cap(state, budget)
        safety = 8 + 10
        if amt >= 5 and state["money"] >= amt + safety:
            decisions.append({"type": "subsidy", "city": target, "amount": amt})

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
    """路線 C 品牌正向循環（挑戰模式）：台中品牌投資 → 正向循環 + 補貼衝市占。

    Phase 1 (r1-2): 台中品牌經營（建立 brand_count=2 + consumer_sat≥75，觸發正向循環 +7.5%/回合）
    Phase 2 (r3+):  台中 30萬補貼（正向循環 +7.5%/回合 + 補貼，連續≥2 時穿插台北重置）
    全程: min_rider < 58 → 降抽成
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
            safety = 8 + 10
            if state["money"] >= game.BRAND_MGMT_COST + safety:
                decisions.append({"type": "brand_management", "city": "台中"})
        else:
            # Phase 2：台中補貼衝刺，連續 ≥2 且非最後回合則穿插台北重置
            tc_consec = state["cities"]["台中"].get("consecutive_subsidy_count", 0)
            if tc_consec >= 2 and r < 10:
                target, budget = "台北", 10   # 重置台中 consecutive → 下回合恢復 100% 效率
            else:
                target, budget = "台中", 30   # 正常衝刺

            amt    = _money_cap(state, budget)
            safety = 8 + 10
            if amt >= 5 and state["money"] >= amt + safety:
                decisions.append({"type": "subsidy", "city": target, "amount": amt})

    return decisions[:2]


STRATEGIES = {
    "純隨機":       strategy_random,
    "單城集中":     strategy_single_city("台北"),
    "分散投資":     strategy_distributed,
    "先研發後收割":  strategy_tech_then_harvest,
    "圍剿對手":     strategy_siege("台北"),
    "兼顧滿意度":   strategy_balanced,
    "市占+滿意度":  strategy_share_plus_sat,
    "品牌正向循環(路線C)": strategy_brand_route_c,
}


# ── 模擬執行器 ──────────────────────────────────────────────────────────────

def run_one_game(decision_fn, initial_money=120.0, max_rounds=10):
    state = game.init_game_state(initial_money=initial_money)
    state["max_rounds"] = max_rounds
    bankrupt_round = None
    for r in range(1, max_rounds + 1):
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
