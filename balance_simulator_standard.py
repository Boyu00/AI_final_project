"""標準模式數值平衡模擬器。

標準模式尚未實作 config 開關系統（計畫書步驟 8），這裡用獨立的簡化結算邏輯
模擬標準模式規則，純粹用於校準勝利門檻；不影響 app.py 正式邏輯。

標準模式規則（依難度分層計畫書）：
- 10 回合、雙軌滿意度（真實雙軌，非合併）
- 5 種決策：補貼/行銷/抽成/擴張/科技研發（無收購）
- 啟用：黑天鵝、外送荒
- 關閉：消費者危機、送香公滿意度、收購
- 動態反擊關閉，改固定反擊（沿用「中度威脅」強度：耗資 15 萬、+5%）
- 保留：自然衰退、固定維運成本、對手財務（破產為唯一第 4 條件，無收購）
- 勝利條件四取二：資金 / 市占 / 雙軌滿意度 / 送香公破產
"""
import copy
import random
import statistics

import app as game

CITIES = game.CITIES

FIXED_RETALIATION_COST = 15.0   # 標準模式固定反擊耗資（沿用挑戰模式「中度威脅」強度）
FIXED_RETALIATION_GAIN = 0.05   # 固定反擊回彈 +5%


def init_standard_state(initial_money=120.0, max_rounds=10):
    state = game.init_game_state(initial_money=initial_money)
    state["max_rounds"] = max_rounds
    return state


def resolve_competitor_standard(state):
    """標準模式競爭 AI：固定反擊（非動態分級），其餘規則沿用。回傳 variable_cost。"""
    if state.get("competitor_bankrupt", False):
        for city in CITIES:
            drain = min(state["competitor"][city], 0.04)
            state["competitor"][city] = max(0.0, state["competitor"][city] - drain)
            state["cities"][city]["share"] = min(game.MAX_PLAYER_SHARE, state["cities"][city]["share"] + drain * 0.5)
        return 0.0

    if state["history"]:
        last_shares = state["history"][-1]["shares_after"]
    else:
        last_shares = dict(game.INITIAL_PLAYER_SHARES)

    for city in CITIES:
        if state["cities"][city]["share"] >= 0.18:
            comp_money = state.get("competitor_money", game.COMPETITOR_INITIAL_MONEY)
            affordable = max(0.0, comp_money - 3.0)
            actual_cost = round(min(FIXED_RETALIATION_COST, affordable), 1)
            ratio = (actual_cost / FIXED_RETALIATION_COST) if FIXED_RETALIATION_COST > 0 else 0.0
            actual_gain = min(round(FIXED_RETALIATION_GAIN * ratio, 4), FIXED_RETALIATION_GAIN)
            state["competitor"][city] = min(state["competitor"][city] + actual_gain, 0.60)
            state["cities"][city]["share"] = max(0.0, state["cities"][city]["share"] - actual_gain)
            return actual_cost

    for city in CITIES:
        if state["cities"][city]["share"] < last_shares.get(city, game.INITIAL_PLAYER_SHARES[city]) - 0.001:
            state["competitor"][city] = min(state["competitor"][city] + 0.01, 0.60)
            state["cities"][city]["share"] = max(0.0, state["cities"][city]["share"] - 0.01)
            return game.COST_COMPETITOR_OPPORTUNISTIC

    best_city = max(CITIES, key=lambda c: state["competitor"][c])
    state["competitor"][best_city] = min(state["competitor"][best_city] + 0.01, 0.60)
    state["cities"][best_city]["share"] = max(0.0, state["cities"][best_city]["share"] - 0.01)
    return game.COST_COMPETITOR_NATURAL


def finalize_round_standard(state, decisions):
    ns = copy.deepcopy(state)

    # Step 0：黑天鵝（啟用，簡化版：直接抽，不快取預覽）
    _event_revenue_mult = 1.0
    _event_sub_eff_mult = 1.0
    _event_fixed_cost_extra = 0.0
    _event_outage_city = None
    if random.random() < game.SWAN_EVENT_PROB:
        event = dict(random.choice(game.SWAN_EVENTS))
        mods = event["modifiers"]
        affected_city = random.choice(CITIES) if (
            "random_city_consumer_delta" in mods or "random_city_revenue_zero" in mods
        ) else None
        if "consumer_sat_delta" in mods:
            for cd in ns["cities"].values():
                cd["consumer_satisfaction"] = max(0, min(100, cd["consumer_satisfaction"] + mods["consumer_sat_delta"]))
        if "rider_sat_delta" in mods:
            for cd in ns["cities"].values():
                cd["rider_satisfaction"] = max(0, min(100, cd["rider_satisfaction"] + mods["rider_sat_delta"]))
        if "random_city_consumer_delta" in mods and affected_city:
            ns["cities"][affected_city]["consumer_satisfaction"] = max(
                0, min(100, ns["cities"][affected_city]["consumer_satisfaction"] + mods["random_city_consumer_delta"])
            )
        if "competitor_share_delta" in mods:
            for city in CITIES:
                ns["competitor"][city] = max(0.0, ns["competitor"][city] + mods["competitor_share_delta"])
        _event_revenue_mult = mods.get("revenue_multiplier", 1.0)
        _event_sub_eff_mult = mods.get("subsidy_efficiency_multiplier", 1.0)
        _event_fixed_cost_extra = mods.get("fixed_cost_delta", 0.0)
        if mods.get("random_city_revenue_zero") and affected_city:
            _event_outage_city = affected_city

    # Step 1：行銷緩衝（含 AI 路由加成，科技樹啟用）
    for cd in ns["cities"].values():
        buf = cd.get("marketing_buffer", 0.0)
        if buf > 0:
            _mkt_eff = game.MARKETING_EFFICIENCY * (1.25 if ns["upgrades"].get("aiRouting") else 1.0) * _event_sub_eff_mult
            cd["share"] += (buf * _mkt_eff) / cd["market"]
            cd["marketing_buffer"] = 0.0

    # Step 2：擴張持續效果
    for city, exp_round in ns["expansion_effects"].items():
        if ns["round"] > exp_round:
            ns["cities"][city]["share"] += game.EXPANSION_ONGOING

    # Step 3：執行決策（5 種，無收購）
    subsidized_cities = set()
    for dec in decisions:
        dtype = dec["type"]
        if dtype == "subsidy":
            city, amount = dec["city"], dec["amount"]
            subsidized_cities.add(city)
            _sub_eff = game.SUBSIDY_EFFICIENCY * (1.25 if ns["upgrades"].get("aiRouting") else 1.0) * _event_sub_eff_mult
            ns["cities"][city]["share"] += (amount * _sub_eff) / game.CITY_META[city]["market"]
            ns["cities"][city]["consumer_satisfaction"] = min(100, ns["cities"][city]["consumer_satisfaction"] + 5)
            ns["cities"][city]["rider_satisfaction"] = min(100, ns["cities"][city]["rider_satisfaction"] + 1)
            ns["money"] -= amount
        elif dtype == "upgrade":
            costs = {
                "aiRouting": game.UPGRADE_AI_ROUTING_COST,
                "dynamicPricing": game.UPGRADE_DYNAMIC_PRICING_COST,
                "exclusiveMerchant": game.UPGRADE_EXCLUSIVE_MERCHANT_COST,
            }
            ns["money"] -= costs[dec["upgradeType"]]
            ns["upgrades"][dec["upgradeType"]] = True
        elif dtype == "marketing":
            city, amount = dec["city"], dec["amount"]
            ns["cities"][city]["marketing_buffer"] = ns["cities"][city].get("marketing_buffer", 0.0) + amount
            ns["money"] -= amount
        elif dtype == "commission":
            delta = dec["delta"]
            ns["commission_rate"] = round(max(game.COMMISSION_MIN, min(game.COMMISSION_MAX, ns["commission_rate"] + delta)), 4)
            pct = abs(delta * 100)
            for cd in ns["cities"].values():
                if delta < 0:
                    cd["rider_satisfaction"] = min(100, cd["rider_satisfaction"] + pct * 1.5)
                    cd["consumer_satisfaction"] = min(100, cd["consumer_satisfaction"] + pct * 0.5)
                else:
                    cd["rider_satisfaction"] = max(0, cd["rider_satisfaction"] - pct * 2.5)
                    cd["consumer_satisfaction"] = max(0, cd["consumer_satisfaction"] - pct * 1.0)
        elif dtype == "expansion":
            city = dec["city"]
            ns["money"] -= game.EXPANSION_COST
            ns["cities"][city]["share"] += game.EXPANSION_IMMEDIATE
            ns["cities"][city]["consumer_satisfaction"] = max(0, ns["cities"][city]["consumer_satisfaction"] - 2)
            ns["cities"][city]["rider_satisfaction"] = max(0, ns["cities"][city]["rider_satisfaction"] - 2)
            if city not in ns["expanded_cities"]:
                ns["expanded_cities"].append(city)
            ns["expansion_effects"][city] = state["round"]

    for cd in ns["cities"].values():
        cd["share"] = max(0.0, min(game.MAX_PLAYER_SHARE, cd["share"]))

    # Step 3.5：未投入城市自然衰退
    _rider_decay_base = game.RIDER_SAT_NATURAL_DECAY * (0.5 if ns["upgrades"].get("aiRouting") else 1.0)
    _expanded = set(ns.get("expanded_cities", []))
    for city, cd in ns["cities"].items():
        if city not in subsidized_cities:
            if city not in _expanded:
                cd["share"] = max(0.0, cd["share"] - game.NATURAL_DECAY_RATE)
            cd["consumer_satisfaction"] = max(0, cd["consumer_satisfaction"] - game.CONSUMER_SAT_DECAY)
            cd["rider_satisfaction"] = max(0, cd["rider_satisfaction"] - _rider_decay_base)

    # Step 3.6：外送荒危機檢查（啟用）
    crisis_cities = []
    for city, cd in ns["cities"].items():
        cd["consumer_satisfaction"] = max(0, min(100, cd["consumer_satisfaction"]))
        cd["rider_satisfaction"] = max(0, min(100, cd["rider_satisfaction"]))
        if cd["rider_satisfaction"] < game.RIDER_SHORTAGE_THRESHOLD:
            cd["consumer_satisfaction"] = max(0, cd["consumer_satisfaction"] - 5)
            _share_loss = 0.01 if ns["upgrades"].get("exclusiveMerchant") else 0.03
            cd["share"] = max(0, cd["share"] - _share_loss)
            ns["competitor"][city] = min(0.60, ns["competitor"][city] + _share_loss)
            crisis_cities.append(city)

    # （消費者危機關閉，標準模式跳過 Step 3.7）

    # Steps 4 & 5：收入（外送荒城市歸零）+ 固定維運成本
    revenue = 0.0
    _rev_coef = game.REVENUE_COEFFICIENT * (1.15 if ns["upgrades"].get("dynamicPricing") else 1.0) * _event_revenue_mult
    for city, cd in ns["cities"].items():
        if city not in crisis_cities and city != _event_outage_city:
            revenue += cd["market"] * cd["share"] * ns["commission_rate"] * _rev_coef
    ns["money"] = max(0.0, ns["money"] + revenue - game.FIXED_OPERATIONAL_COST - _event_fixed_cost_extra)

    # Step 6：競爭對手固定反擊 + 財務結算（無送香公滿意度機制）
    comp_money_before = ns.get("competitor_money", game.COMPETITOR_INITIAL_MONEY)
    comp_variable_cost = resolve_competitor_standard(ns)

    if ns.get("competitor_bankrupt", False):
        comp_total_cost = 0.0
    else:
        comp_revenue = sum(
            ns["cities"][city]["market"] * ns["competitor"][city]
            * game.COMPETITOR_COMMISSION_RATE * game.REVENUE_COEFFICIENT
            for city in CITIES
        )
        comp_total_cost = game.COMPETITOR_FIXED_COST + comp_variable_cost
        ns["competitor_money"] = max(0.0, comp_money_before + comp_revenue - comp_total_cost)
        if ns["competitor_money"] <= 0 and not ns.get("competitor_bankrupt", False):
            ns["competitor_bankrupt"] = True

    # Step 7：市場飽和正規化（優先壓縮對手）
    for city, cd in ns["cities"].items():
        comp_share = ns["competitor"][city]
        total = cd["share"] + comp_share
        if total > 1.0:
            ns["competitor"][city] = max(0.0, round(1.0 - cd["share"], 4))

    for cd in ns["cities"].values():
        cd["share"] = max(0.0, min(game.MAX_PLAYER_SHARE, round(cd["share"], 4)))
    ns["commission_rate"] = round(max(game.COMMISSION_MIN, min(game.COMMISSION_MAX, ns["commission_rate"])), 4)

    ns["history"].append({
        "round": state["round"],
        "decisions": decisions,
        "shares_after": {c: round(ns["cities"][c]["share"], 4) for c in CITIES},
        "crisis_cities": list(crisis_cities),
    })
    ns["round"] += 1
    return ns


def check_result_standard(state, win_money, win_share, win_consumer_sat, win_rider_sat):
    if state["money"] <= 0:
        return "lose", 0
    if state["round"] <= state["max_rounds"]:
        return "playing", 0
    achieved = 0
    if state["money"] >= win_money:
        achieved += 1
    if max(cd["share"] for cd in state["cities"].values()) >= win_share:
        achieved += 1
    sat = game.calculate_overall_satisfaction(state)
    if sat["consumer"] >= win_consumer_sat and sat["rider"] >= win_rider_sat:
        achieved += 1
    if state.get("competitor_bankrupt", False):
        achieved += 1
    return ("win" if achieved >= 2 else "lose"), achieved


# ── 策略 ──────────────────────────────────────────────────────────────────

def _money_cap(state, max_amt=30):
    cap = max(5, int(state["money"] // 5) * 5)
    return min(max_amt, cap)


def strategy_random(state):
    decisions = []
    budget = state["money"]
    n = random.randint(0, 2)
    pool = ["subsidy", "marketing", "commission", "expansion", "upgrade", "none"]
    for _ in range(n):
        t = random.choice(pool)
        if t in ("subsidy", "marketing"):
            cap = _money_cap(state, 30)
            if cap < 5 or budget < 5:
                continue
            amt = random.choice(range(5, cap + 1, 5))
            if amt > budget:
                continue
            decisions.append({"type": t, "city": random.choice(CITIES), "amount": amt})
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
        if len(decisions) >= 2:
            break
    return decisions[:2]


def strategy_single_city(city="台北"):
    def fn(state):
        amt = _money_cap(state, 30)
        if amt < 5 or state["money"] < 5:
            return []
        return [{"type": "subsidy", "city": city, "amount": amt}]
    return fn


def strategy_balanced(state):
    if state["round"] % 3 == 0 and state["commission_rate"] > game.COMMISSION_MIN:
        return [{"type": "commission", "delta": -game.COMMISSION_STEP}]
    amt = _money_cap(state, 25)
    if amt < 5 or state["money"] < 5:
        return []
    return [{"type": "subsidy", "city": "台北", "amount": amt}]


def strategy_tech_then_harvest(state):
    if state["round"] <= 3:
        upgs = state.get("upgrades", {})
        catalog = [
            ("aiRouting", game.UPGRADE_AI_ROUTING_COST),
            ("dynamicPricing", game.UPGRADE_DYNAMIC_PRICING_COST),
            ("exclusiveMerchant", game.UPGRADE_EXCLUSIVE_MERCHANT_COST),
        ]
        available = [(k, c) for k, c in catalog if not upgs.get(k) and state["money"] >= c]
        if available:
            k, _ = available[0]
            return [{"type": "upgrade", "upgradeType": k}]
        return []
    amt = _money_cap(state, 30)
    if amt < 5 or state["money"] < 5:
        return []
    return [{"type": "subsidy", "city": "台北", "amount": amt}]


STRATEGIES = {
    "純隨機":     strategy_random,
    "單城集中":   strategy_single_city("台北"),
    "兼顧滿意度":  strategy_balanced,
    "先研發後收割": strategy_tech_then_harvest,
}


def run_one_game(decision_fn, win_money, win_share, win_csat, win_rsat, initial_money=120.0, max_rounds=10):
    state = init_standard_state(initial_money=initial_money, max_rounds=max_rounds)
    early_bankrupt = False
    for r in range(1, max_rounds + 1):
        decisions = decision_fn(state)
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
        if total_spend > state["money"]:
            decisions = []
        state = finalize_round_standard(state, decisions)
        if state["money"] <= 0:
            if r <= 3:
                early_bankrupt = True
            break
    result, achieved = check_result_standard(state, win_money, win_share, win_csat, win_rsat)
    sat = game.calculate_overall_satisfaction(state)
    return {
        "result": result,
        "achieved": achieved,
        "rounds_played": state["round"] - 1,
        "final_money": state["money"],
        "max_share": max(cd["share"] for cd in state["cities"].values()),
        "consumer_sat": sat["consumer"],
        "rider_sat": sat["rider"],
        "early_bankrupt": early_bankrupt,
    }


def run_trials(decision_fn, n_trials, win_money, win_share, win_csat, win_rsat, **kwargs):
    results = [run_one_game(decision_fn, win_money, win_share, win_csat, win_rsat, **kwargs) for _ in range(n_trials)]
    wins = sum(1 for r in results if r["result"] == "win")
    all4 = sum(1 for r in results if r["achieved"] == 4)
    early_bk = sum(1 for r in results if r["early_bankrupt"])
    return {
        "win_rate": wins / n_trials,
        "all4_rate": all4 / n_trials,
        "early_bankrupt_rate": early_bk / n_trials,
        "avg_rounds": statistics.mean(r["rounds_played"] for r in results),
        "avg_money": statistics.mean(r["final_money"] for r in results),
        "avg_share": statistics.mean(r["max_share"] for r in results),
        "avg_csat": statistics.mean(r["consumer_sat"] for r in results),
        "avg_rsat": statistics.mean(r["rider_sat"] for r in results),
    }


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    candidates = [
        (150, 0.35, 55, 55),
        (170, 0.40, 55, 55),
        (180, 0.40, 58, 58),
        (200, 0.45, 60, 60),
    ]
    for wm, ws, wcs, wrs in candidates:
        print(f"\n=== WIN_MONEY={wm}萬 WIN_SHARE={ws*100:.0f}% WIN_SAT=consumer{wcs}/rider{wrs} ===")
        for name, fn in STRATEGIES.items():
            n = 1500 if name == "純隨機" else 400
            r = run_trials(fn, n, wm, ws, wcs, wrs)
            print(
                f"  {name:12s} 通關率={r['win_rate']*100:5.1f}%  四項全達標={r['all4_rate']*100:4.1f}%  "
                f"3回合內破產={r['early_bankrupt_rate']*100:4.1f}%  平均資金={r['avg_money']:6.1f}萬  "
                f"平均市占={r['avg_share']*100:5.1f}%  消費者={r['avg_csat']:5.1f}  外送員={r['avg_rsat']:5.1f}"
            )
