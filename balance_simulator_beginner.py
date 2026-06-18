"""入門模式數值平衡模擬器。

入門模式尚未實作 config 開關系統（計畫書步驟 8），這裡用獨立的簡化結算邏輯
模擬入門模式規則，純粹用於校準勝利門檻；不影響 app.py 正式邏輯。

入門模式規則（依難度分層計畫書）：
- 5 回合
- 單軌滿意度（顯示用 (consumer+rider)/2，底層仍沿用雙軌公式）
- 4 種決策：補貼/行銷/抽成/擴張（無科技樹、無收購）
- 關閉：黑天鵝、外送荒、消費者危機、送香公滿意度、對手財務顯示
- 動態反擊關閉，固定反擊 +3%
- 保留：自然衰退、固定維運成本
- 勝利條件三取二：資金 / 市占 / 整體滿意度
"""
import random
import statistics

import app as game

CITIES = game.CITIES

FIXED_RETALIATION_GAIN = 0.03  # 入門模式固定反擊 +3%（取代動態威脅分級）


def init_beginner_state(initial_money=120.0, max_rounds=5):
    state = game.init_game_state(initial_money=initial_money)
    state["max_rounds"] = max_rounds
    return state


def resolve_competitor_beginner(state):
    """簡化版競爭 AI：固定反擊 +3%（零和），其餘規則沿用 +1%。"""
    if state["history"]:
        last_shares = state["history"][-1]["shares_after"]
    else:
        last_shares = dict(game.INITIAL_PLAYER_SHARES)

    for city in CITIES:
        if state["cities"][city]["share"] >= 0.18:
            gain = FIXED_RETALIATION_GAIN
            state["competitor"][city] = min(state["competitor"][city] + gain, 0.60)
            state["cities"][city]["share"] = max(0.0, state["cities"][city]["share"] - gain)
            return

    for city in CITIES:
        if state["cities"][city]["share"] < last_shares.get(city, game.INITIAL_PLAYER_SHARES[city]) - 0.001:
            state["competitor"][city] = min(state["competitor"][city] + 0.01, 0.60)
            state["cities"][city]["share"] = max(0.0, state["cities"][city]["share"] - 0.01)
            return

    best_city = max(CITIES, key=lambda c: state["competitor"][c])
    state["competitor"][best_city] = min(state["competitor"][best_city] + 0.01, 0.60)
    state["cities"][best_city]["share"] = max(0.0, state["cities"][best_city]["share"] - 0.01)


def finalize_round_beginner(state, decisions):
    """簡化版結算引擎：對應入門模式規則子集。"""
    import copy
    ns = copy.deepcopy(state)

    # Step 1：行銷緩衝生效（無 AI 路由加成，科技樹關閉）
    for cd in ns["cities"].values():
        buf = cd.get("marketing_buffer", 0.0)
        if buf > 0:
            cd["share"] += (buf * game.MARKETING_EFFICIENCY) / cd["market"]
            cd["marketing_buffer"] = 0.0

    # Step 2：擴張持續效果
    for city, exp_round in ns["expansion_effects"].items():
        if ns["round"] > exp_round:
            ns["cities"][city]["share"] += game.EXPANSION_ONGOING

    prev_shares = {c: cd["share"] for c, cd in ns["cities"].items()}

    # Step 3：執行決策（僅 4 種，無科技樹/收購）
    subsidized_cities = set()
    for dec in decisions:
        dtype = dec["type"]
        if dtype == "subsidy":
            city, amount = dec["city"], dec["amount"]
            subsidized_cities.add(city)
            ns["cities"][city]["share"] += (amount * game.SUBSIDY_EFFICIENCY) / game.CITY_META[city]["market"]
            ns["cities"][city]["consumer_satisfaction"] = min(100, ns["cities"][city]["consumer_satisfaction"] + 5)
            ns["cities"][city]["rider_satisfaction"] = min(100, ns["cities"][city]["rider_satisfaction"] + 1)
            ns["money"] -= amount
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

    # Step 3.5：未投入城市自然衰退（無外送荒/消費者危機，入門模式關閉）
    _expanded = set(ns.get("expanded_cities", []))
    for city, cd in ns["cities"].items():
        if city not in subsidized_cities:
            if city not in _expanded:
                cd["share"] = max(0.0, cd["share"] - game.NATURAL_DECAY_RATE)
            cd["consumer_satisfaction"] = max(0, cd["consumer_satisfaction"] - game.CONSUMER_SAT_DECAY)
            cd["rider_satisfaction"] = max(0, cd["rider_satisfaction"] - game.RIDER_SAT_NATURAL_DECAY)
        cd["consumer_satisfaction"] = max(0, min(100, cd["consumer_satisfaction"]))
        cd["rider_satisfaction"] = max(0, min(100, cd["rider_satisfaction"]))

    # Step 4&5：收入（無動態定價加成）+ 固定維運成本
    revenue = 0.0
    for city, cd in ns["cities"].items():
        revenue += cd["market"] * cd["share"] * ns["commission_rate"] * game.REVENUE_COEFFICIENT
    ns["money"] = max(0.0, ns["money"] + revenue - game.FIXED_OPERATIONAL_COST)

    # Step 6：競爭對手固定反擊（無對手財務/破產/滿意度機制）
    resolve_competitor_beginner(ns)

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
    })
    ns["round"] += 1
    return ns


def merged_satisfaction(state):
    sat = game.calculate_overall_satisfaction(state)
    return (sat["consumer"] + sat["rider"]) / 2


def check_result_beginner(state, win_money, win_share, win_sat):
    if state["money"] <= 0:
        return "lose"
    if state["round"] <= state["max_rounds"]:
        return "playing"
    achieved = 0
    if state["money"] >= win_money:
        achieved += 1
    if max(cd["share"] for cd in state["cities"].values()) >= win_share:
        achieved += 1
    if merged_satisfaction(state) >= win_sat:
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
    pool = ["subsidy", "marketing", "commission", "expansion", "none"]
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
    if state["round"] % 2 == 0 and state["commission_rate"] > game.COMMISSION_MIN:
        return [{"type": "commission", "delta": -game.COMMISSION_STEP}]
    amt = _money_cap(state, 25)
    if amt < 5 or state["money"] < 5:
        return []
    return [{"type": "subsidy", "city": "台北", "amount": amt}]


STRATEGIES = {
    "純隨機":   strategy_random,
    "單城集中": strategy_single_city("台北"),
    "兼顧滿意度": strategy_balanced,
}


def run_one_game(decision_fn, win_money, win_share, win_sat, initial_money=120.0, max_rounds=5):
    state = init_beginner_state(initial_money=initial_money, max_rounds=max_rounds)
    for r in range(1, max_rounds + 1):
        decisions = decision_fn(state)
        total_spend = sum(
            d.get("amount", 0) if d["type"] in ("subsidy", "marketing")
            else (game.EXPANSION_COST if d["type"] == "expansion" else 0)
            for d in decisions
        )
        if total_spend > state["money"]:
            decisions = []
        state = finalize_round_beginner(state, decisions)
        if state["money"] <= 0:
            break
    result = check_result_beginner(state, win_money, win_share, win_sat)
    if isinstance(result, tuple):
        result, achieved = result
    else:
        achieved = None
    max_share = max(cd["share"] for cd in state["cities"].values())
    return {
        "result": result,
        "final_money": state["money"],
        "max_share": max_share,
        "merged_sat": merged_satisfaction(state),
        "achieved": achieved,
    }


def run_trials(decision_fn, n_trials, win_money, win_share, win_sat, **kwargs):
    results = [run_one_game(decision_fn, win_money, win_share, win_sat, **kwargs) for _ in range(n_trials)]
    wins = sum(1 for r in results if r["result"] == "win")
    all3 = sum(1 for r in results if r["achieved"] == 3)
    return {
        "win_rate": wins / n_trials,
        "all3_rate": all3 / n_trials,
        "avg_money": statistics.mean(r["final_money"] for r in results),
        "avg_share": statistics.mean(r["max_share"] for r in results),
        "avg_sat": statistics.mean(r["merged_sat"] for r in results),
    }


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    candidates = [
        (60, 0.20, 60),
        (70, 0.25, 60),
        (70, 0.25, 65),
        (80, 0.25, 65),
        (80, 0.30, 65),
        (90, 0.30, 70),
    ]
    for wm, ws, wsat in candidates:
        print(f"\n=== WIN_MONEY={wm}萬 WIN_SHARE={ws*100:.0f}% WIN_SAT={wsat} ===")
        for name, fn in STRATEGIES.items():
            n = 2000 if name == "純隨機" else 500
            r = run_trials(fn, n, wm, ws, wsat)
            print(
                f"  {name:10s} 通關率={r['win_rate']*100:5.1f}%  三項全達標={r['all3_rate']*100:4.1f}%  "
                f"平均資金={r['avg_money']:6.1f}萬  平均市占={r['avg_share']*100:5.1f}%  平均滿意度={r['avg_sat']:5.1f}"
            )
