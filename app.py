import copy
import json
import os
import random
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv
from ai_advisor import AIAdvisor
import scoring as sc

load_dotenv()

# ── 常數 ──────────────────────────────────────────────────────────────────────

CITIES = ["台北", "台中", "高雄"]
CITY_META = {
    "台北": {"market": 100},
    "台中": {"market": 60},
    "高雄": {"market": 40},
}
COMPETITOR_NAMES = ["送香公"]
COMP_COLORS = {"送香公": "#F44336"}
COMP_ICONS  = {"送香公": "🔴"}

# ── 規格常數 v1.3（數值平衡版，由模擬器逆向推導）
SUBSIDY_EFFICIENCY       = 0.70   # 10 萬補貼 ≈ 台北 +7% 市占，立竿見影
MARKETING_EFFICIENCY     = 1.20   # 行銷轉化率比補貼高 1.5 倍，延遲換效益
MARKETING_GAIN_CAP       = 0.18   # 單回合行銷市占增幅上限（防止小城市一輪爆炸；同城連續衰減另計）
REVENUE_COEFFICIENT      = 2.0    # 放大市占帶來的營收感
FIXED_OPERATIONAL_COST   = 8.0    # 每回合固定維運死支（萬）
NATURAL_DECAY_RATE       = 0.01   # 未投入城市每回合市占自然流失 1%
CONSUMER_SAT_DECAY       = 3      # 未投入城市消費者滿意度自然衰減
EXPANSION_COST           = 50     # 萬
EXPANSION_IMMEDIATE      = 0.02   # 即時市占增幅
EXPANSION_MARKET_GROWTH  = 0.30   # 市場規模永久擴大 30%（對玩家與對手同時生效）
COMMISSION_MIN           = 0.20
COMMISSION_MAX           = 0.40
COMMISSION_STEP          = 0.05
WIN_MONEY                = 320.0  # 萬（challenge 門檻；standard 獨立設定 250；提高至 320 以降低純隨機透過財富勝利的機率）
WIN_SHARE                = 0.70   # 任一城市市占 ≥ 70%（challenge 門檻；standard 為 0.60 獨立設定於 DIFFICULTY_PRESETS）
WIN_CONSUMER_SAT         = 60     # 消費者滿意度 ≥ 60
WIN_RIDER_SAT            = 60     # 外送商家滿意度 ≥ 60
RIDER_SHORTAGE_THRESHOLD         = 40  # 外送荒觸發門檻
CONSUMER_REVIEW_THRESHOLD        = 30  # 負評爆炸門檻
CONSUMER_REVIEW_SHARE_LOSS       = 0.04  # 負評爆炸：市占額外流失（v1.18 由 2% 提高至 4%，加重低滿意度代價）
CONSUMER_REVIEW_TRIGGER_PROB     = 0.5   # 負評爆炸每回合觸發機率（v1.18 新增，避免滿意度卡在門檻下時連續每回合必噴）
CONSUMER_MEDIA_THRESHOLD         = 20  # 媒體負面報導：競爭對手額外獲益 5%

# ── 黑天鵝事件池（每回合 40% 機率觸發一個）
SWAN_EVENTS = [
    {
        "id": "gov_subsidy",
        "name": "🏛️ 政府外送補貼政策",
        "tone": "good",
        "description": "政府宣布鼓勵外送消費專案，本季全台營收係數 ×1.5！",
        "modifiers": {"revenue_multiplier": 1.5},
    },
    {
        "id": "typhoon",
        "name": "🌀 颱風假宅在家效應",
        "tone": "mixed",
        "description": "颱風來襲外出不便，外送需求暴增（營收 ×1.4），但外送員冒風險出勤怨聲載道（全台外送員滿意度 -8）。",
        "modifiers": {"revenue_multiplier": 1.4, "rider_sat_delta": -8},
    },
    {
        "id": "food_scandal",
        "name": "🧪 外送食安風暴",
        "tone": "bad",
        "description": "多家外送平台合作餐廳集體爆發食安問題，全產業形象重創。飛食與送香公的消費者信心同步大跌（雙方消費者滿意度 -10）。",
        "modifiers": {"consumer_sat_delta": -10, "competitor_sat_delta": -10},
    },
    {
        "id": "fuel_hike",
        "name": "⛽ 燃油價格飆漲",
        "tone": "bad",
        "description": "國際油價暴衝，全行業外送員怨聲載道。飛食與送香公外送員滿意度同步 -10，飛食本季額外維運成本 +8 萬。",
        "modifiers": {"rider_sat_delta": -10, "fixed_cost_delta": 8, "competitor_sat_delta": -10},
    },
    {
        "id": "viral_kol",
        "name": "🌟 百萬 KOL 爆推",
        "tone": "good",
        "description": "知名美食網紅大力推薦飛食，{city} 消費者滿意度暴漲 +20！",
        "modifiers": {"random_city_consumer_delta": 20},
    },
    {
        "id": "rider_act",
        "name": "🛵 外送員保障法案通過",
        "tone": "good",
        "description": "立院通過外送員勞動保障法，行業形象大幅提升，全台外送員滿意度 +12。",
        "modifiers": {"rider_sat_delta": 12},
    },
    {
        "id": "platform_outage",
        "name": "💥 平台系統大當機",
        "tone": "bad",
        "description": "飛食 APP 後台重大故障，{city} 本季收入歸零，消費者滿意度 -8。",
        "modifiers": {"random_city_revenue_zero": True, "consumer_sat_delta": -8},
    },
    {
        "id": "competitor_scandal",
        "name": "🔥 送香公爆勞資醜聞",
        "tone": "good",
        "description": "競爭對手深陷外送員大規模抗議，各城市市占自動流失 5%，正是飛食反攻的好時機！",
        "modifiers": {"competitor_share_delta": -0.05},
    },
    {
        "id": "recession",
        "name": "📉 景氣衰退警報",
        "tone": "bad",
        "description": "消費信心下滑，民眾縮衣節食，飛食本季全台營收係數降至 ×0.7。送香公同樣深陷消費者信心危機，雙方消費者滿意度同步下滑（-8）。",
        "modifiers": {"revenue_multiplier": 0.7, "consumer_sat_delta": -8, "competitor_sat_delta": -8},
    },
    {
        "id": "rider_exodus",
        "name": "🏃 外送員大規模出走",
        "tone": "bad",
        "description": "外送行業工作條件惡化，大批外送員轉投物流或餐飲業。飛食與送香公外送員滿意度同步 -12，配送延誤也拖累雙方消費者滿意度 -6。",
        "modifiers": {"rider_sat_delta": -12, "consumer_sat_delta": -6, "competitor_sat_delta": -12},
    },
    {
        "id": "sports_craze",
        "name": "🏆 全民運動熱潮",
        "tone": "good",
        "description": "大型賽事帶動宅在家訂餐熱潮，本季補貼與行銷轉化效率 ×1.3！",
        "modifiers": {"subsidy_efficiency_multiplier": 1.3},
    },
]
SWAN_EVENT_PROB = 0.40  # 每回合觸發機率

# ── 決策概念提示對照表（decision_type, context） → hint data ────────────────────
DECISION_CONCEPT_MAP = {
    ("subsidy", "default"): {
        "concept_id": "subsidy_trap",
        "concept_name": "補貼陷阱",
        "hint": "補貼能快速獲新用戶，但補貼一停，沒有黏著度的用戶就會離開。",
    },
    ("subsidy", "diminishing"): {
        "concept_id": "diminishing_returns",
        "concept_name": "邊際效益遞減",
        "hint": "你已經連續補貼這座城市了，效率會真的打折——換個城市或換種決策，可能比硬撐更有效。",
    },
    ("marketing", "default"): {
        "concept_id": "front_loading",
        "concept_name": "前置投資",
        "hint": "行銷要下一季才見效。你願意先付出、晚點收割嗎？",
    },
    ("commission", "decrease"): {
        "concept_id": "opportunity_cost",
        "concept_name": "機會成本",
        "hint": "降低抽成讓大家開心，但你放棄的那部分收入本來可以拿去做別的事。",
    },
    ("commission", "increase"): {
        "concept_id": "loss_aversion",
        "concept_name": "損失厭惡",
        "hint": "漲抽成的負面反應通常比降抽成的正面反應強 2 倍。外送員特別有感。",
    },
    ("expansion", "default"): {
        "concept_id": "economies_of_scale",
        "concept_name": "規模經濟",
        "hint": "擴張初期成本很高，但如果站穩，規模夠大後單位成本會下降。",
    },
    ("expansion", "already_expanded_other"): {
        "concept_id": "focus_strategy",
        "concept_name": "集中化策略",
        "hint": "你已經在別的城市擴張了。資源有限時，是要繼續分散還是集中突破？",
    },
    ("tech_research", "default"): {
        "concept_id": "front_loading",
        "concept_name": "前置投資",
        "hint": "研發是前期犧牲後期獲利。越早解鎖，複利時間越長，但現金流壓力也越大。",
    },
    ("acquisition", "default"): {
        "concept_id": "war_of_attrition",
        "concept_name": "消耗戰",
        "hint": "花 80 萬買下對手，還是繼續消耗戰等它自己倒？哪個算法比較划算？",
    },
    ("brand_management", "default"): {
        "concept_id": "brand_premium",
        "concept_name": "品牌溢價",
        "hint": "品牌經營不帶來即時爆發，但能建立消費者信任。當滿意度夠高，品牌就能自動成長——投入越早，複利飛輪越早啟動。",
    },
    ("brand_management", "invested"): {
        "concept_id": "front_loading",
        "concept_name": "預置投資",
        "hint": "你已在此城市建立品牌基礎，持續投入將觸發自然成長飛輪——信任累積 → 成長 → 更多信任。",
    },
    ("subsidy", "high_share"): {
        "concept_id": "network_effect",
        "concept_name": "網路效應",
        "hint": "這個城市用戶基數已經夠大，新用戶更容易被吸引——用戶越多，平台越有價值。",
    },
    ("focus", "same_city"): {
        "concept_id": "focus_strategy",
        "concept_name": "集中化策略",
        "hint": "兩個決策集中在同一城市，效率會更高，但其他城市這季會被忽略。",
    },
}

# 科技樹常數
UPGRADE_AI_ROUTING_COST         = 20   # AI 智慧路徑優化研發費（萬，v1.18 多路線改造：30→20，讓科技致富路線可行）
UPGRADE_DYNAMIC_PRICING_COST    = 18   # 雲端動態定價系統研發費（萬，v1.18：25→18，科技路線核心）
UPGRADE_EXCLUSIVE_MERCHANT_COST = 25   # 獨家特約商家聯盟研發費（萬，v1.18：35→25）
RIDER_SAT_NATURAL_DECAY         = 1.5  # 未投入城市外送員滿意度自然衰減 / 回合（v1.12.1 由 2 調降，緩解滿意度回升過慢的問題）

# 品牌經營機制（v1.19）
BRAND_MGMT_COST          = 15    # 萬（固定，不遞減）
BRAND_MGMT_CONSUMER_SAT  = 8
BRAND_MGMT_RIDER_SAT     = 4
BRAND_MGMT_SHARE_GAIN    = 0.02  # 即時市占增幅
BRAND_GROWTH_THRESHOLD   = 75    # 消費者滿意度觸發品牌成長門檻
BRAND_GROWTH_RATE        = 0.05  # 品牌成長每回合自然市占增幅
BRAND_GROWTH_MIN_COUNT   = 2     # 解鎖品牌成長所需最低累積次數

# 競爭對手財務常數
COMPETITOR_INITIAL_MONEY        = 180.0  # 送香公初始資金（萬，平衡模擬調整 v1.12：防止圍剿策略 10 回合內穩定破產）
COMPETITOR_COMMISSION_RATE      = 0.22   # 送香公抽成率（調低使對手可被財務圍剿）
COMPETITOR_FIXED_COST           = 18.0   # 送香公每回合固定維運成本（萬，大公司包袱重）
COST_COMPETITOR_COUNTER         = 15.0   # 防守反擊耗資（萬）
COST_COMPETITOR_OPPORTUNISTIC   = 6.0    # 趁虛而入耗資（萬）
COST_COMPETITOR_NATURAL         = 2.0    # 自然成長維持成本（萬）
WIN_COMPETITOR_BANKRUPT         = True   # 第 4 項勝利條件旗標（送香公破產）
ACQUISITION_COST      = 80.0   # 收購送香公費用（萬）
ACQUISITION_THRESHOLD = 40.0   # 解鎖收購的競爭對手資金門檻（萬）

# 對手滿意度 + 滿意度驅動的市占微幅變化
COMPETITOR_SAT_INITIAL        = 60.0  # 送香公初始滿意度（單一指標，不分消費者/外送員）
COMPETITOR_SAT_PROFIT_DELTA   = 1.0   # 本季財務轉虧轉盈時的滿意度變化
COMPETITOR_SAT_LOSS_DELTA     = 2.0   # 本季虧損時的滿意度變化（高強度防守更傷士氣）
SATISFACTION_SHARE_DRIFT      = 0.005 # 滿意度落差驅動的市占微調幅度（零和，每回合最多 0.5%）
SATISFACTION_DRIFT_THRESHOLD  = 5.0   # 雙方滿意度差距需超過此值才觸發微調

MAX_PLAYER_SHARE = 0.99  # 市占上限封頂 99%，永遠留一點長尾/對手殘存空間，不可能真的吃滿 100%

# ── 概念教學系統：三個輕量機制（所有難度皆啟用，教育功能非難度功能）─────────────────
NETWORK_EFFECT_THRESHOLD = 0.40   # 網路效應：城市市占 ≥ 此值，補貼/行銷效率 +15%
NETWORK_EFFECT_BONUS     = 0.15
BRAND_PREMIUM_THRESHOLD  = 75     # 品牌溢價：消費者滿意度 ≥ 此值，該城營收 +10%
BRAND_PREMIUM_BONUS      = 0.10
FOCUS_STRATEGY_BONUS     = 0.15   # 集中化策略：本回合兩個決策同城時，雙方效率各 +15%
AI_ROUTING_SHARE_THRESHOLD    = 0.35  # AI路由只在市占 < 此值的城市生效（中後期市場優勢消退）
DYNAMIC_PRICING_SAT_THRESHOLD = 65    # 動態定價只在消費者滿意度 ≥ 此值的城市生效
CITY_TRAITS = {
    "台北": {"retaliation_mult": 1.2, "brand_growth_mult": 1.0, "subsidy_mult": 1.0},
    "台中": {"retaliation_mult": 1.0, "brand_growth_mult": 1.5, "subsidy_mult": 1.0},
    "高雄": {"retaliation_mult": 1.0, "brand_growth_mult": 1.0, "subsidy_mult": 1.3},
}

# ── 補貼連續遞減機制（修復「補貼+降抽成」固定排程 100% 必勝的平衡漏洞）─────────────
# 邊際效益遞減從「tooltip 文字」變成真實機制：連續補貼同一城市，效率逐次遞減
SUBSIDY_DECAY_TABLE = {
    1: 1.00,   # 第 1 次補貼：無衰減
    2: 0.75,   # 連續第 2 次：75%
    3: 0.50,   # 連續第 3 次：50%
}
SUBSIDY_DECAY_FLOOR = 0.25  # 第 4 次以上：固定 25%（地板值；v1.18 下調，抑制單城連續補貼投機）


def get_subsidy_decay(consecutive_count: int) -> float:
    """根據連續補貼次數回傳衰減係數。"""
    return SUBSIDY_DECAY_TABLE.get(consecutive_count, SUBSIDY_DECAY_FLOOR)

# ── 難度分層設定（依平衡模擬校準，v1.15）───────────────────────────────────────
# 每個 preset 是當局遊戲的完整設定快照，存成 state["config"]。
# finalize_round / resolve_competitor / check_game_result / UI 顯示函式
# 全部透過 state["config"][...] 讀取門檻與機制開關，不再寫死模組常數。
DIFFICULTY_PRESETS = {
    "beginner": {
        "key": "beginner", "label": "🟢 入門模式", "subtitle": "學概念",
        "max_rounds": 5, "initial_money": 120.0,
        "dual_satisfaction": False,
        "tech_tree": False, "black_swan": False,
        "competitor_finance_visible": False, "competitor_satisfaction_enabled": False,
        "bankruptcy_enabled": False, "acquisition_enabled": False,
        "rider_crisis": False, "consumer_crisis": False,
        "dynamic_retaliation": False, "fixed_retaliation_cost": 8.0, "fixed_retaliation_gain": 0.03,
        "investor_comment": False,
        "brand_management_enabled": False,
        "expansion_enabled": False,
        "win_money": 120.0, "win_share": 0.55, "win_sat": 60.0,  # 單軌合併滿意度
        "win_required": 2, "win_total": 3,
    },
    "standard": {
        "key": "standard", "label": "🟡 標準模式", "subtitle": "學策略",
        "max_rounds": 10, "initial_money": 120.0,
        "dual_satisfaction": True,
        "tech_tree": True, "black_swan": True,
        "competitor_finance_visible": True, "competitor_satisfaction_enabled": False,
        "bankruptcy_enabled": True, "acquisition_enabled": False,
        "rider_crisis": True, "consumer_crisis": False,
        "dynamic_retaliation": False, "fixed_retaliation_cost": 15.0, "fixed_retaliation_gain": 0.05,
        "investor_comment": False,
        "brand_management_enabled": True,
        "win_money": 250.0, "win_share": 0.60, "win_consumer_sat": WIN_CONSUMER_SAT, "win_rider_sat": WIN_RIDER_SAT,
        "win_required": 2, "win_total": 4,
    },
    "challenge": {
        "key": "challenge", "label": "🔴 挑戰模式", "subtitle": "學應變",
        "max_rounds": 10, "initial_money": 120.0,
        "dual_satisfaction": True,
        "tech_tree": True, "black_swan": True,
        "competitor_finance_visible": True, "competitor_satisfaction_enabled": True,
        "bankruptcy_enabled": True, "acquisition_enabled": True,
        "rider_crisis": True, "consumer_crisis": True,
        "dynamic_retaliation": True, "fixed_retaliation_cost": None, "fixed_retaliation_gain": None,
        "investor_comment": True,
        "brand_management_enabled": True,
        "win_money": WIN_MONEY, "win_share": WIN_SHARE,
        "win_consumer_sat": WIN_CONSUMER_SAT, "win_rider_sat": WIN_RIDER_SAT,
        "win_required": 2, "win_total": 4,
    },
}

# 三個輕量機制是教育功能、不是難度功能，所有 preset 一律啟用
for _preset in DIFFICULTY_PRESETS.values():
    _preset.setdefault("network_effect", True)
    _preset.setdefault("brand_premium", True)
    _preset.setdefault("expansion_enabled", True)
    _preset.setdefault("focus_strategy", True)

# ── 教育模組：行為紀錄 + 概念提示 ───────────────────────────────────────────────

def log_event(state: dict, event_type: str, data: dict = None):
    """統一的事件紀錄函式。所有學習追蹤都透過這裡寫入。"""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "event_type": event_type,
        "round": state.get("round", 0),
        "data": data or {},
    }
    state.setdefault("event_log", []).append(entry)


def get_concept_context(decision_type: str, city: str, state: dict) -> str:
    """根據當前遊戲狀態判斷該顯示哪個情境的概念提示。"""
    if decision_type == "subsidy":
        # 優先級 1：連續補貼遞減（真實機制，最重要要提醒）
        if state["cities"].get(city, {}).get("consecutive_subsidy_count", 0) >= 1:
            return "diminishing"
        # 優先級 2：高市占城市的網路效應
        if state["cities"].get(city, {}).get("share", 0) >= NETWORK_EFFECT_THRESHOLD:
            return "high_share"
        return "default"
    if decision_type == "expansion":
        return "already_expanded_other" if state.get("expanded_cities") else "default"
    if decision_type == "brand_management":
        _bc = state.get("brand_count", {}).get(city or "", 0)
        return "invested" if _bc >= 1 else "default"
    return "default"


def get_hint_for_decision(decision_type: str, context: str) -> dict | None:
    key = (decision_type, context)
    return DECISION_CONCEPT_MAP.get(key) or DECISION_CONCEPT_MAP.get((decision_type, "default"))


def get_concept_hint_text(decision_type: str, city: str | None, state: dict, context_override: str = None) -> str | None:
    """回傳可掛在 widget `help=` 參數上的概念提示文字（滑鼠移過去即顯示，無需 rerun）。
    同時記錄一次 hint_interaction（每回合每種情境只記一次，避免每次 rerun 重複寫入）。
    """
    context = context_override or get_concept_context(decision_type, city or "", state)
    hint_data = get_hint_for_decision(decision_type, context)
    if hint_data is None:
        return None

    logged_key = f"_hint_shown_{decision_type}_{city or 'global'}_{context}_{state['round']}"
    if not st.session_state.get(logged_key):
        log_event(state, "hint_interaction", {
            "decision_type": decision_type,
            "city": city or "global",
            "concept_id": hint_data["concept_id"],
            "concept_name": hint_data["concept_name"],
            "opened": True,  # 滑鼠移過去即可看到，故曝光即計入
        })
        st.session_state[logged_key] = True

    return f"📚 {hint_data['concept_name']}：{hint_data['hint']}"


def detect_concept_triggers(state: dict) -> list:
    """B 層概念：檢查特定遊戲情境是否成立（不靠決策本身，靠連續幾回合的狀態演變）。
    回傳本回合觸發的概念列表，每項含 concept_id/concept_name/context/prompt_instruction。
    """
    triggers = []
    history = state.get("history", [])

    # 邊際效益遞減（已是真實機制，這裡額外觸發是為了讓 AI 報告強調說明，不衝突）
    for city in CITIES:
        count = state["cities"][city].get("consecutive_subsidy_count", 0)
        if count >= 2:
            decay_pct = int(get_subsidy_decay(count) * 100)
            triggers.append({
                "concept_id": "diminishing_returns",
                "concept_name": "邊際效益遞減",
                "context": f"你已經連續 {count} 季補貼{city}，效率降至 {decay_pct}%。",
                "prompt_instruction": f"請在分析中強調「邊際效益遞減」：玩家連續 {count} 季補貼{city}，同樣的投入產出越來越差，建議考慮輪換或改用其他決策方式。",
            })

    # 沉沒成本：對同一城市連續投資 ≥ 3 回合
    if len(history) >= 3:
        for city in CITIES:
            consecutive = sum(
                1 for h in history[-3:]
                if any(d.get("city") == city for d in h.get("decisions", []))
            )
            if consecutive >= 3:
                triggers.append({
                    "concept_id": "sunk_cost",
                    "concept_name": "沉沒成本",
                    "context": f"你已經連續 {consecutive} 季投資{city}。",
                    "prompt_instruction": f"請在分析中提到「沉沒成本」概念：玩家已連續 {consecutive} 季投資{city}，提醒過去的投入不應該綁住未來的決策方向。",
                })

    # 囚徒困境：同城市連續 ≥ 2 回合，玩家補貼/行銷 + 對手防守反擊互相加碼
    if len(history) >= 2:
        for city in CITIES:
            mutual = 0
            for h in history[-2:]:
                player_invested = any(
                    d.get("city") == city and d.get("type") in ("subsidy", "marketing")
                    for d in h.get("decisions", [])
                )
                action_text = h.get("competitor_action", "")
                competitor_retaliated = "反擊" in action_text and city in action_text
                if player_invested and competitor_retaliated:
                    mutual += 1
            if mutual >= 2:
                triggers.append({
                    "concept_id": "prisoners_dilemma",
                    "concept_name": "囚徒困境",
                    "context": f"你和送香公在{city}已經互相加碼 {mutual} 季了。",
                    "prompt_instruction": f"請在分析中提到「囚徒困境」概念：飛食和送香公在{city}持續互相加碼，雙方都在燒錢但都不敢先停手，這正是囚徒困境的典型情境。",
                })

    # 價格敏感型消費者：停止補貼某城市後，市占連續 2 回合下滑
    if len(history) >= 3:
        for city in CITIES:
            last_subsidy_idx = None
            for i, h in enumerate(history):
                if any(d.get("city") == city and d.get("type") == "subsidy" for d in h.get("decisions", [])):
                    last_subsidy_idx = i
            if last_subsidy_idx is not None and last_subsidy_idx <= len(history) - 3:
                shares_seq = [history[last_subsidy_idx]["shares_after"].get(city, 0)]
                for j in range(last_subsidy_idx + 1, min(last_subsidy_idx + 3, len(history))):
                    shares_seq.append(history[j]["shares_after"].get(city, 0))
                if len(shares_seq) >= 3 and shares_seq[1] < shares_seq[0] and shares_seq[2] < shares_seq[1]:
                    triggers.append({
                        "concept_id": "price_sensitive_consumer",
                        "concept_name": "價格敏感型消費者",
                        "context": f"{city}在你停止補貼後市占連續下滑。",
                        "prompt_instruction": f"請在分析中提到「價格敏感型消費者」概念：{city}的用戶在補貼停止後持續流失，這些是因為便宜才來的用戶，黏著度低。",
                    })

    # 品牌飛輪：消費者滿意度達標且累積品牌經營次數觸發自然成長
    if state["config"].get("brand_management_enabled"):
        _brand_count = state.get("brand_count", {})
        for city in CITIES:
            if (state["cities"][city]["consumer_satisfaction"] >= BRAND_GROWTH_THRESHOLD and
                    _brand_count.get(city, 0) >= BRAND_GROWTH_MIN_COUNT):
                triggers.append({
                    "concept_id": "network_effect",
                    "concept_name": "品牌飛輪",
                    "context": f"{city}消費者滿意度 ≥ {BRAND_GROWTH_THRESHOLD} 且品牌累積 {_brand_count.get(city, 0)} 次，每回合自動 +{BRAND_GROWTH_RATE*100:.0f}% 市占。",
                    "prompt_instruction": f"請在分析中提到「品牌飛輪」概念：{city}已觸發品牌成長機制，消費者信任建立到一定程度後便會自我強化，這是長期品牌投資的複利效果，不需要每回合繼續大量投入就能自動累積市占。",
                })

    # 錨定效應：曾經降過抽成，之後又調回原本或更高
    commission_deltas = [
        d.get("delta", 0)
        for h in history
        for d in h.get("decisions", [])
        if d.get("type") == "commission"
    ]
    if len(commission_deltas) >= 2 and any(d < 0 for d in commission_deltas[:-1]) and commission_deltas[-1] > 0:
        triggers.append({
            "concept_id": "anchoring_effect",
            "concept_name": "錨定效應",
            "context": "你之前降過抽成，現在又調回來了。",
            "prompt_instruction": "請在分析中提到「錨定效應」概念：玩家之前降低過抽成，用戶已經習慣較低的價格，現在調回來時的負面反應會比從未降過更強烈。",
        })

    return triggers


def get_experienced_concepts(state: dict) -> dict:
    """統計本局體驗到的概念（A 層 hint_interaction + B 層 concept_triggered），
    回傳 {concept_id: {name, source, round}}，只計第一次出現。
    """
    experienced = {}
    for event in state.get("event_log", []):
        if event["event_type"] in ("hint_interaction", "concept_triggered"):
            cid = event["data"].get("concept_id")
            if cid and cid not in experienced:
                experienced[cid] = {
                    "name": event["data"].get("concept_name", cid),
                    "source": "situation" if event["event_type"] == "concept_triggered" else "mechanism",
                    "round": event["round"],
                }
    return experienced


# 已知概念總數（A 層 9 個 + B 層 4 個 = 13 個），用於結局統計顯示「X/13」
TOTAL_TEACHABLE_CONCEPTS = 13


def build_concept_summary_data(state: dict) -> list:
    """Phase 2：整理每回合「決策 ↔ 概念 ↔ 效果」原始資料，供 AI 生成對照表。
    決策來自 state['history']（已含 before/after 數字），概念來自 event_log 的
    hint_interaction（同回合、同決策類型比對），不需要重新計算任何遊戲數值。
    """
    hints_by_round = {}
    for e in state.get("event_log", []):
        if e["event_type"] == "hint_interaction":
            hints_by_round.setdefault(e["round"], []).append(e["data"])

    rows = []
    for h in state.get("history", []):
        r = h["round"]
        concepts_this_round = hints_by_round.get(r, [])
        decision_concepts = []
        for d in h.get("decisions", []):
            dtype = d.get("type")
            match = next((c for c in concepts_this_round if c["decision_type"] == dtype), None)
            concept_name = None
            if match:
                concept_name = next(
                    (v["concept_name"] for v in DECISION_CONCEPT_MAP.values() if v["concept_id"] == match["concept_id"]),
                    None,
                )
            decision_concepts.append({"decision": d, "concept_name": concept_name})
        rows.append({
            "round": r,
            "decision_concepts": decision_concepts,
            "money_before": h["money_before"],
            "money_after": h["money_after"],
            "shares_before": h["shares_before"],
            "shares_after": h["shares_after"],
        })
    return rows


# ── 遊戲初始化 ────────────────────────────────────────────────────────────────

INITIAL_COMP_SHARES = {"台北": 0.50, "台中": 0.35, "高雄": 0.25}
INITIAL_PLAYER_SHARES = {"台北": 0.10, "台中": 0.08, "高雄": 0.05}


def init_game_state(initial_money: float = None, difficulty: str = "challenge") -> dict:
    preset = DIFFICULTY_PRESETS[difficulty]
    if initial_money is None:
        initial_money = preset["initial_money"]
    return {
        "difficulty": difficulty,
        "config": preset,
        "round": 1,
        "max_rounds": preset["max_rounds"],
        "phase": "MARKET_NEWS",   # MARKET_NEWS | PLAYER_DECISION | REPORT | GAME_OVER
        "money": float(initial_money),
        "commission_rate": 0.30,
        "cities": {
            "台北": {"market": 100, "share": 0.10, "consumer_satisfaction": 60, "rider_satisfaction": 60, "marketing_buffer": 0.0, "consecutive_subsidy_count": 0, "consecutive_marketing_count": 0},
            "台中": {"market": 60,  "share": 0.08, "consumer_satisfaction": 60, "rider_satisfaction": 60, "marketing_buffer": 0.0, "consecutive_subsidy_count": 0, "consecutive_marketing_count": 0},
            "高雄": {"market": 40,  "share": 0.05, "consumer_satisfaction": 60, "rider_satisfaction": 60, "marketing_buffer": 0.0, "consecutive_subsidy_count": 0, "consecutive_marketing_count": 0},
        },
        # 扁平結構：{city: competitor_share}（規格 Step 6 使用）
        "competitor": {"台北": 0.50, "台中": 0.35, "高雄": 0.25},
        "upgrades": {
            "aiRouting": False,        # AI 智慧路徑優化
            "dynamicPricing": False,   # 雲端動態定價系統
            "exclusiveMerchant": False, # 獨家特約商家聯盟
        },
        "competitor_money": COMPETITOR_INITIAL_MONEY,
        "competitor_bankrupt": False,
        "competitor_acquired": False,
        "competitor_satisfaction": {c: COMPETITOR_SAT_INITIAL for c in CITIES},
        "brand_count": {city: 0 for city in CITIES},  # 各城市累積品牌經營次數
        "expanded_cities": [],    # 已擴張城市（每城限一次）
        "expansion_effects": {},  # {city: round_number} 追蹤持續效果
        "history": [],
        "market_news": "",        # AI 生成，MARKET_NEWS 階段顯示
        "round_report": "",       # AI 生成，REPORT 階段顯示
        "investor_comment": "",   # AI 生成，REPORT 階段底部投資人短評
        "ending_report": "",      # AI 生成，GAME_OVER 顯示
        "concept_summary": "",    # AI 生成，GAME_OVER 顯示（決策↔概念↔效果對照表）
        "competitor_action": "",
        "game_result": "playing", # "playing" | "win" | "lose"
        "event_log": [],          # 教育模組行為紀錄
    }

# ── 遊戲邏輯 ──────────────────────────────────────────────────────────────────

def calculate_revenue(state: dict) -> float:
    return sum(
        cd["market"] * cd["share"] * state["commission_rate"] * REVENUE_COEFFICIENT
        for cd in state["cities"].values()
    )


def calculate_overall_satisfaction(state: dict) -> dict:
    """回傳 {"consumer": float, "rider": float} 加權平均。"""
    total_market = sum(cd["market"] for cd in state["cities"].values())
    if total_market == 0:
        return {"consumer": 0.0, "rider": 0.0}
    consumer = sum(cd["market"] * cd["consumer_satisfaction"] for cd in state["cities"].values()) / total_market
    rider    = sum(cd["market"] * cd["rider_satisfaction"]    for cd in state["cities"].values()) / total_market
    return {"consumer": consumer, "rider": rider}


def check_game_result(state: dict) -> str:
    config = state["config"]
    if state["money"] <= 0:
        return "lose"
    if state["round"] <= state["max_rounds"]:
        return "playing"
    achieved = 0
    if state["money"] >= config["win_money"]:
        achieved += 1
    if max(cd["share"] for cd in state["cities"].values()) >= config["win_share"]:
        achieved += 1
    sat = calculate_overall_satisfaction(state)
    if config["dual_satisfaction"]:
        if sat["consumer"] >= config["win_consumer_sat"] and sat["rider"] >= config["win_rider_sat"]:
            achieved += 1
    else:
        merged = (sat["consumer"] + sat["rider"]) / 2
        if merged >= config["win_sat"]:
            achieved += 1
    if config["win_total"] == 4:
        if state.get("competitor_bankrupt", False) or state.get("competitor_acquired", False):
            achieved += 1
    return "win" if achieved >= config["win_required"] else "lose"


_COUNTER_PLAN_NAMES = [
    "鐵壁計畫", "雷霆行動", "鷹眼計畫", "烈焰反撲", "暴風鎖城",
    "決堤計畫", "閃電護盤", "獵豹行動", "鎖鏈戰略", "重錘計畫",
    "穿甲行動", "鋼牆方案", "龍捲戰術", "鐵網計畫", "震懾行動",
]

_OPP_PLAN_NAMES = [
    "禿鷹行動", "蠶食計畫", "獵缺戰術", "漁翁方案", "穿插行動",
    "偷天計畫", "填隙戰略", "乘虛行動", "掘金計畫", "夾縫突破",
]

_NATURAL_PLAN_NAMES = [
    "穩盤計畫", "深耕戰略", "固本行動", "磐石方案", "暖灶計畫",
    "守望行動", "耕耘戰術", "根基計畫", "細水方案", "蟄伏策略",
]


def resolve_competitor(state: dict) -> tuple:
    """3 規則競爭 AI + 財務結算。回傳 (action_text: str, variable_cost: float)。
    直接修改 state["competitor"] 市占；財務由 finalize_round 統一更新。
    """
    config = state["config"]
    comp_name = COMPETITOR_NAMES[0]

    # ── 破產模式：無力反擊，市占每回合自動流失給玩家
    if state.get("competitor_bankrupt", False):
        for city in CITIES:
            drain = min(state["competitor"][city], 0.04)
            state["competitor"][city] = max(0.0, state["competitor"][city] - drain)
            state["cities"][city]["share"] = min(MAX_PLAYER_SHARE, state["cities"][city]["share"] + drain * 0.5)
        return random.choice([
            f"💀【破產特報】{comp_name} 進入法院監管重整程序，各城市配送網路人心惶惶，大批商家主動尋求與飛食合作，本季市占全面萎縮。",
            f"💀【重整觀察】{comp_name} 債務危機持續蔓延，留守外送員大規模跳槽，消費者口耳相傳平台服務惡化，飛食順勢接收流失客群。",
            f"💀【清算快訊】{comp_name} 資金鏈斷裂後無力維持補貼政策，各城市商家合約陸續到期不續簽，市場版圖迅速向飛食傾斜。",
            f"💀【崩盤日誌】{comp_name} 旗下外送員大規模解約求去，留守團隊士氣低落，各城市訂單承接量跌至谷底，飛食趁勢接手失血版圖。",
            f"💀【殘局追蹤】{comp_name} 法務代理人宣布暫停對外簽約，合作餐廳紛紛轉投飛食懷抱，市場重組已成定局。",
        ]), 0.0

    # 取上回合玩家市占作為 Rule 2 的比較基準
    if state["history"]:
        last_shares = state["history"][-1]["shares_after"]
    else:
        last_shares = dict(INITIAL_PLAYER_SHARES)

    # 規則 1：防守反擊（依玩家市占威脅等級動態調整投入與回彈幅度）
    for city in CITIES:
        player_share = state["cities"][city]["share"]
        if player_share >= 0.18:
            comp_money = state.get("competitor_money", COMPETITOR_INITIAL_MONEY)

            if config["dynamic_retaliation"]:
                # 威脅等級（6 級，挑戰模式）：決定基礎花費與基礎市占回彈
                # 觸發門檻最高 40%——超過後對手已全力反撲，不再額外升級
                if player_share >= 0.40:
                    tier_label, base_cost, base_gain = "生死危機", 40.0, 0.33
                elif player_share >= 0.34:
                    tier_label, base_cost, base_gain = "嚴重威脅", 28.0, 0.26
                elif player_share >= 0.28:
                    tier_label, base_cost, base_gain = "重度威脅", 20.0, 0.19
                elif player_share >= 0.23:
                    tier_label, base_cost, base_gain = "中度威脅", 14.0, 0.13
                elif player_share >= 0.20:
                    tier_label, base_cost, base_gain = "中度警戒",  9.0, 0.08
                else:
                    tier_label, base_cost, base_gain = "輕度警戒",  5.0, 0.04

                # 決戰期（Q8 起）對手傾盡全力反擊，預算與回彈上限同步上修
                _endgame = state["round"] >= 8
                _endgame_mult = 1.2 if _endgame else 1.0
                base_cost *= _endgame_mult
                gain_cap = 0.30 if _endgame else 0.25  # v1.26：降低上限使 70% 市占在數學上可達成
            else:
                # 固定反擊（入門/標準模式）：強度固定，不隨威脅等級或回合 escalate
                tier_label = "防守反擊"
                base_cost = config["fixed_retaliation_cost"]
                base_gain = config["fixed_retaliation_gain"]
                _endgame_mult = 1.0
                gain_cap = base_gain

            # 資金不足時按比例縮減（保留 3 萬底線，避免直接破產）
            affordable = max(0.0, comp_money - 3.0)
            actual_cost = round(min(base_cost, affordable), 1)
            ratio = (actual_cost / base_cost) if base_cost > 0 else 0.0
            actual_gain = min(round(base_gain * _endgame_mult * ratio, 4), gain_cap)
            actual_gain = max(0.0, actual_gain - 0.03)
            actual_gain *= CITY_TRAITS.get(city, {}).get("retaliation_mult", 1.0)

            player_pct = player_share * 100
            gain_pct = actual_gain * 100
            money_after = max(0.0, comp_money - actual_cost)
            plan = random.choice(_COUNTER_PLAN_NAMES)

            if comp_money > 80:
                finance_note = random.choice([
                    f"憑藉充裕的 {comp_money:.0f} 萬資金儲備，此舉游刃有餘。",
                    f"賬上尚有 {comp_money:.0f} 萬，這波反擊屬於低風險操作。",
                    f"以 {comp_money:.0f} 萬的雄厚家底支撐，{comp_name}底氣十足。",
                ])
            elif comp_money > 40:
                finance_note = random.choice([
                    f"此舉令資金從 {comp_money:.0f} 萬降至約 {money_after:.0f} 萬，財務壓力逐漸浮現。",
                    f"反擊後資金將縮至 {money_after:.0f} 萬，連續幾回合這樣燒下去恐怕吃不消。",
                    f"帳上 {comp_money:.0f} 萬看似尚足，但持續高強度防守不是長久之計。",
                ])
            else:
                finance_note = random.choice([
                    f"目前帳上僅剩 {comp_money:.0f} 萬，這筆 {actual_cost:.0f} 萬的防守支出已讓資金鏈岌岌可危。",
                    f"以 {comp_money:.0f} 萬殘存資金強行護盤，{comp_name}已在懸崖邊緣蹣跚。",
                    f"資金告急至 {comp_money:.0f} 萬仍執意反撲，財務底線岌岌可危。",
                ])

            # 反擊是零和搶市占：對手拿到的市占直接從玩家手上扣除，而不是無中生有
            state["competitor"][city] = min(state["competitor"][city] + actual_gain, 0.60)
            state["cities"][city]["share"] = max(0.0, state["cities"][city]["share"] - actual_gain)
            return random.choice([
                f"⚔️【{tier_label}】飛食{city}市占突破 {player_pct:.0f}%，觸動{comp_name}警戒紅線！總部緊急授權砸下 {actual_cost:.0f} 萬護盤預算，展開大規模補貼閃電戰，強行將流失客群拉回，{city}市占回升 {gain_pct:.0f}%。{finance_note}",
                f"⚔️【{tier_label}】{comp_name}{city}大區主管緊急召開應對會議——飛食已滲透至 {player_pct:.0f}% 市占，威脅核心版圖。本季動用 {actual_cost:.0f} 萬專項防守基金，對商家祭出獨家返利方案，{city}市占強行鞏固 +{gain_pct:.0f}%。{finance_note}",
                f"⚔️【{tier_label}】偵測到飛食在{city}市占飆至 {player_pct:.0f}%，{comp_name}高層拍板啟動「{plan}」，投入 {actual_cost:.0f} 萬進行全城補貼轟炸，{city}市占強拉 +{gain_pct:.0f}%。{finance_note}",
                f"⚔️【{tier_label}】{comp_name}即時情報系統偵測飛食{city}突破 {player_pct:.0f}%，五分鐘內完成「{plan}」授權，{actual_cost:.0f} 萬資金立刻到位，全城外送員啟動加碼激勵，{city}市占回彈 +{gain_pct:.0f}%。{finance_note}",
                f"⚔️【{tier_label}】面對飛食{city} {player_pct:.0f}%市占威脅，{comp_name}戰略部啟動「{plan}」，動員 {actual_cost:.0f} 萬在{city}各大商圈密集投放優惠券，部分動搖中的合作餐廳被拉回{comp_name}陣營，{city}市占硬守回升 +{gain_pct:.0f}%。{finance_note}",
                f"⚔️【{tier_label}】{comp_name}執行長親批「{plan}」，授權{city}地區主管動用 {actual_cost:.0f} 萬進行精準補貼阻擊——飛食用戶每筆訂單立享折抵，商家端提前鎖單獨家協議，{city}市占保住並反彈 +{gain_pct:.0f}%。{finance_note}",
            ]), actual_cost

    # 規則 2：趁虛而入（耗資 6 萬）
    for city in CITIES:
        if state["cities"][city]["share"] < last_shares.get(city, INITIAL_PLAYER_SHARES[city]) - 0.001:
            plan = random.choice(_OPP_PLAN_NAMES)
            budget_desc = random.choice([
                f"{COST_COMPETITOR_OPPORTUNISTIC:.0f} 萬",
                f"約 {COST_COMPETITOR_OPPORTUNISTIC:.0f} 萬",
                f"近 {COST_COMPETITOR_OPPORTUNISTIC + 1:.0f} 萬",
                f"僅 {COST_COMPETITOR_OPPORTUNISTIC:.0f} 萬",
                f"不到 {COST_COMPETITOR_OPPORTUNISTIC + 2:.0f} 萬",
            ])
            state["competitor"][city] = min(state["competitor"][city] + 0.01, 0.60)
            state["cities"][city]["share"] = max(0.0, state["cities"][city]["share"] - 0.01)
            return random.choice([
                f"📈【市場情報】飛食{city}本季擴張力道不足，市占出現鬆動。{comp_name}商業情報部門即時捕捉到這個空窗期，迅速調撥{budget_desc}行銷資源趁勢填補，成功從飛食手中多搶下 1% 市場。",
                f"📈【機會主義出擊】{comp_name}觀察到飛食{city}陣線後退，立刻啟動「{plan}」，以{budget_desc}預算精準收割動搖的商家與消費者，{city}版圖悄然擴大 1%。",
                f"📈【競爭動態】飛食{city}市占環比下滑，{comp_name}區域總監評估後認定時機成熟，批准{budget_desc}的定向投放，低調但有效地將飛食退出的缺口納入自身版圖。",
                f"📈【滲透報告】「{plan}」本季執行完畢——{comp_name}投入{budget_desc}在{city}進行靜默式用戶遷移，透過老用戶回購折扣搶回 1% 市占，飛食渾然未覺已失守。",
                f"📈【精準獵缺】{comp_name}數據中心標記出飛食{city}投入縮水，立即啟動「{plan}」：{budget_desc}精準投放給最近七天未下單用戶，{city}市占靜悄悄回升 1%。",
                f"📈【趁虛挺進】飛食{city}本季戰線收縮，{comp_name}機動組以{budget_desc}資金發動「{plan}」——商家激勵金迅速到位，消費端優惠同步推送，將飛食讓出的空間一口吞下，市占 +1%。",
            ]), COST_COMPETITOR_OPPORTUNISTIC

    # 規則 3：自然成長（耗資 2 萬）
    best_city = max(CITIES, key=lambda c: state["competitor"][c])
    plan = random.choice(_NATURAL_PLAN_NAMES)
    budget_desc = random.choice([
        f"{COST_COMPETITOR_NATURAL:.0f} 萬",
        f"約 {COST_COMPETITOR_NATURAL:.0f} 萬",
        f"僅 {COST_COMPETITOR_NATURAL:.0f} 萬",
        f"不到 {COST_COMPETITOR_NATURAL + 1:.0f} 萬",
        f"區區 {COST_COMPETITOR_NATURAL:.0f} 萬",
    ])
    state["competitor"][best_city] = min(state["competitor"][best_city] + 0.01, 0.60)
    state["cities"][best_city]["share"] = max(0.0, state["cities"][best_city]["share"] - 0.01)
    return random.choice([
        f"ℹ️【例行公告】{comp_name}本季維持穩健經營策略，持續深耕旗下最強根據地{best_city}，投入{budget_desc}進行常態化社群維繫與外送員激勵，{best_city}市占微幅成長 1%，整體態勢平穩。",
        f"ℹ️【產業觀察】在無重大威脅的情況下，{comp_name}選擇以守代攻，將本季資源集中投注於{best_city}，以{budget_desc}維持成本換取 1% 的穩定市占增長。",
        f"ℹ️【業務更新】{comp_name}啟動「{plan}」，{best_city}團隊花費{budget_desc}推進商家關係維護與舊用戶回購活動，{best_city}市占穩步上揚 1%，基本盤依舊穩固。",
        f"ℹ️【穩盤快報】{comp_name}本季無意主動進攻，「{plan}」默默在{best_city}收攏老用戶，投入{budget_desc}的維運支出換得 +1% 市占，不張揚卻紮實。",
        f"ℹ️【經營週報】{comp_name}{best_city}分部本季執行「{plan}」——{budget_desc}資金分散用於外送員福利加碼與商家續約補貼，{best_city}市占有驚無險地微增 1%。",
        f"ℹ️【季報摘要】無強敵威脅，{comp_name}選擇以最小成本鞏固{best_city}優勢地位，「{plan}」僅動用{budget_desc}便完成目標：市占 +1%，現金流保持健康。",
    ]), COST_COMPETITOR_NATURAL


def finalize_round(state: dict, decisions: list) -> dict:
    """執行 9 步結算引擎，回傳新 state。"""
    ns = copy.deepcopy(state)
    config = ns["config"]

    # Step 0：黑天鵝事件即時效果（滿意度、對手市占）
    active_event = ns.pop("pending_event", None)
    ns["pending_event"] = None  # 清空，避免下回合重複
    _event_revenue_mult    = 1.0
    _event_sub_eff_mult    = 1.0
    _event_fixed_cost_extra = 0.0
    _event_outage_city     = None

    if active_event:
        mods = active_event.get("modifiers", {})
        affected_city = active_event.get("affected_city")
        # 即時效果已在 _roll_swan_event 套用，此處只處理延遲效果
        if not active_event.get("immediate_applied"):
            # 舊存檔相容：若未標記則補套用
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
            if "competitor_sat_delta" in mods:
                for city in CITIES:
                    ns["competitor_satisfaction"][city] = max(
                        0.0, min(100.0, ns["competitor_satisfaction"][city] + mods["competitor_sat_delta"])
                    )
        # 延遲效果：收入/補貼乘數與固定成本
        _event_revenue_mult     = mods.get("revenue_multiplier", 1.0)
        _event_sub_eff_mult     = mods.get("subsidy_efficiency_multiplier", 1.0)
        _event_fixed_cost_extra = mods.get("fixed_cost_delta", 0.0)
        if mods.get("random_city_revenue_zero") and affected_city:
            _event_outage_city = affected_city

    # Step 1：行銷緩衝 → 市占（上回合投放，本回合生效；含網路效應）
    # 先更新連續行銷計數（buffer > 0 代表上回合有行銷此城市，衰減同補貼機制）
    for _, _mkt_cd in ns["cities"].items():
        if _mkt_cd.get("marketing_buffer", 0.0) > 0:
            _mkt_cd["consecutive_marketing_count"] = _mkt_cd.get("consecutive_marketing_count", 0) + 1
        else:
            _mkt_cd["consecutive_marketing_count"] = 0
    for city, cd in ns["cities"].items():
        buf = cd.get("marketing_buffer", 0.0)
        if buf > 0:
            _mkt_ai = ns["upgrades"].get("aiRouting") and cd["share"] < AI_ROUTING_SHARE_THRESHOLD
            _mkt_eff = MARKETING_EFFICIENCY * (1.25 if _mkt_ai else 1.0) * _event_sub_eff_mult
            if config["network_effect"] and cd["share"] >= NETWORK_EFFECT_THRESHOLD:
                _mkt_eff *= (1 + NETWORK_EFFECT_BONUS)
            _mkt_decay = get_subsidy_decay(cd.get("consecutive_marketing_count", 1))
            _mkt_gain = min((buf * _mkt_eff * _mkt_decay) / cd["market"], MARKETING_GAIN_CAP)
            cd["share"] += _mkt_gain
            cd["marketing_buffer"] = 0.0

    # Step 2.5：品牌成長觸發（消費者滿意度 ≥ 80 且累積品牌經營 ≥ 2 次 → 每回合自然 +3%）
    _brand_growth_cities = []
    if config.get("brand_management_enabled"):
        ns.setdefault("brand_count", {c: 0 for c in CITIES})
        for _bg_city, _bg_cd in ns["cities"].items():
            if (_bg_cd["consumer_satisfaction"] >= BRAND_GROWTH_THRESHOLD and
                    ns["brand_count"].get(_bg_city, 0) >= BRAND_GROWTH_MIN_COUNT):
                _bg_rate = BRAND_GROWTH_RATE * CITY_TRAITS.get(_bg_city, {}).get("brand_growth_mult", 1.0)
                _bg_cd["share"] = min(MAX_PLAYER_SHARE, _bg_cd["share"] + _bg_rate)
                _brand_growth_cities.append(_bg_city)

    # 記錄決策前市占（供競爭 AI 使用）
    prev_shares = {c: cd["share"] for c, cd in ns["cities"].items()}

    # 集中化策略：本回合兩個決策若指定同一城市，雙方效率各 +15%
    _decision_cities = [d.get("city") for d in decisions if d.get("city") is not None]
    _focus_city = _decision_cities[0] if (
        config["focus_strategy"] and len(_decision_cities) == 2 and _decision_cities[0] == _decision_cities[1]
    ) else None

    # 補貼連續遞減：先更新本回合的連續補貼計數，後面 Step 3 直接讀取更新後的值
    _subsidy_cities_this_round = {d["city"] for d in decisions if d.get("type") == "subsidy"}
    for _city_key in CITIES:
        if _city_key in _subsidy_cities_this_round:
            ns["cities"][_city_key]["consecutive_subsidy_count"] = ns["cities"][_city_key].get("consecutive_subsidy_count", 0) + 1
        else:
            ns["cities"][_city_key]["consecutive_subsidy_count"] = 0

    # Step 3：執行玩家決策，同時記錄本回合被補貼的城市
    subsidized_cities = set()
    for dec in decisions:
        dtype = dec["type"]
        if dtype == "subsidy":
            city, amount = dec["city"], dec["amount"]
            subsidized_cities.add(city)
            _sub_ai = ns["upgrades"].get("aiRouting") and ns["cities"][city]["share"] < AI_ROUTING_SHARE_THRESHOLD
            _sub_eff = SUBSIDY_EFFICIENCY * (1.25 if _sub_ai else 1.0) * _event_sub_eff_mult
            _sub_eff *= CITY_TRAITS.get(city, {}).get("subsidy_mult", 1.0)
            _sub_eff *= get_subsidy_decay(ns["cities"][city]["consecutive_subsidy_count"])
            if config["network_effect"] and ns["cities"][city]["share"] >= NETWORK_EFFECT_THRESHOLD:
                _sub_eff *= (1 + NETWORK_EFFECT_BONUS)
            if _focus_city == city:
                _sub_eff *= (1 + FOCUS_STRATEGY_BONUS)
            ns["cities"][city]["share"] += (amount * _sub_eff) / CITY_META[city]["market"]
            ns["cities"][city]["consumer_satisfaction"] = min(100, ns["cities"][city]["consumer_satisfaction"] + 5)
            ns["cities"][city]["rider_satisfaction"]    = min(100, ns["cities"][city]["rider_satisfaction"]    + 1)
            ns["money"] -= amount
        elif dtype == "upgrade":
            _upgrade_costs = {
                "aiRouting":         UPGRADE_AI_ROUTING_COST,
                "dynamicPricing":    UPGRADE_DYNAMIC_PRICING_COST,
                "exclusiveMerchant": UPGRADE_EXCLUSIVE_MERCHANT_COST,
            }
            ns["money"] -= _upgrade_costs[dec["upgradeType"]]
            ns["upgrades"][dec["upgradeType"]] = True
        elif dtype == "marketing":
            city, amount = dec["city"], dec["amount"]
            _buffer_amount = amount * (1 + FOCUS_STRATEGY_BONUS) if _focus_city == city else amount
            ns["cities"][city]["marketing_buffer"] = ns["cities"][city].get("marketing_buffer", 0.0) + _buffer_amount
            ns["money"] -= amount
        elif dtype == "commission":
            delta = dec["delta"]
            ns["commission_rate"] = round(
                max(COMMISSION_MIN, min(COMMISSION_MAX, ns["commission_rate"] + delta)), 4
            )
            pct = abs(delta * 100)
            for cd in ns["cities"].values():
                if delta < 0:
                    # 降抽成：讓利生態圈
                    cd["rider_satisfaction"]    = min(100, cd["rider_satisfaction"]    + pct * 1.5)
                    cd["consumer_satisfaction"] = min(100, cd["consumer_satisfaction"] + pct * 0.5)
                else:
                    # 升抽成：壓榨生態圈（損失厭惡懲罰加倍）
                    cd["rider_satisfaction"]    = max(0, cd["rider_satisfaction"]    - pct * 2.5)
                    cd["consumer_satisfaction"] = max(0, cd["consumer_satisfaction"] - pct * 1.0)
        elif dtype == "acquisition":
            ns["money"] -= ACQUISITION_COST
            ns["competitor_acquired"] = True
            ns["competitor_bankrupt"] = True  # 進入退出模式：停止行動、市占逐步流失
        elif dtype == "brand_management":
            city = dec["city"]
            ns["money"] -= BRAND_MGMT_COST
            ns["cities"][city]["consumer_satisfaction"] = min(100, ns["cities"][city]["consumer_satisfaction"] + BRAND_MGMT_CONSUMER_SAT)
            ns["cities"][city]["rider_satisfaction"]    = min(100, ns["cities"][city]["rider_satisfaction"]    + BRAND_MGMT_RIDER_SAT)
            # 集中策略：市占增幅套用加成，但滿意度效果不加倍（spec v1.19）
            _bm_share = BRAND_MGMT_SHARE_GAIN * (1 + FOCUS_STRATEGY_BONUS) if _focus_city == city else BRAND_MGMT_SHARE_GAIN
            ns["cities"][city]["share"] = min(MAX_PLAYER_SHARE, ns["cities"][city]["share"] + _bm_share)
            ns.setdefault("brand_count", {c: 0 for c in CITIES})
            ns["brand_count"][city] = ns["brand_count"].get(city, 0) + 1
            subsidized_cities.add(city)  # 視為投入城市，免除自然衰退
        elif dtype == "expansion":
            city = dec["city"]
            ns["money"] -= EXPANSION_COST
            _exp_gain = EXPANSION_IMMEDIATE * (1 + FOCUS_STRATEGY_BONUS) if _focus_city == city else EXPANSION_IMMEDIATE
            ns["cities"][city]["share"] += _exp_gain
            # 市場做大：雙方共用同一 market 數值，對手營收也同步受益
            if city not in ns.get("expanded_cities", []):
                ns["cities"][city]["market"] = round(ns["cities"][city]["market"] * (1 + EXPANSION_MARKET_GROWTH))
            if city not in ns["expanded_cities"]:
                ns["expanded_cities"].append(city)

    # Step 3 結束後立即夾住單一平台市占上限（避免疊加多項決策後暫時超過上限，
    # 導致後續對手 AI 文案或畫面顯示出不合理的數字，如「市占飆至 164%」）
    for cd in ns["cities"].values():
        cd["share"] = max(0.0, min(MAX_PLAYER_SHARE, cd["share"]))

    # 集中化策略實際觸發時記錄一次（用於結局概念統計）
    if _focus_city:
        log_event(ns, "concept_triggered", {
            "concept_id": "focus_strategy",
            "concept_name": "集中化策略",
            "context": f"本回合兩個決策都投在{_focus_city}，效率各 +{FOCUS_STRATEGY_BONUS*100:.0f}%。",
            "source": "situation",
        })

    # Step 3.5：未投入城市自然衰退（不進則退）
    # 已擴張城市設有實體據點，市占不再自然流失；滿意度仍正常衰退（沒補貼就無主動互動）
    _rider_decay_base = RIDER_SAT_NATURAL_DECAY * (0.5 if ns["upgrades"].get("aiRouting") else 1.0)
    _expanded = set(ns.get("expanded_cities", []))
    for city, cd in ns["cities"].items():
        if city not in subsidized_cities:
            if city not in _expanded:
                cd["share"] = max(0.0, cd["share"] - NATURAL_DECAY_RATE)
            cd["consumer_satisfaction"] = max(0, cd["consumer_satisfaction"] - CONSUMER_SAT_DECAY)
            cd["rider_satisfaction"] = max(0, cd["rider_satisfaction"] - _rider_decay_base)

    # Step 3.6：外送荒危機檢查（在計算收入前；入門/標準模式視 config 關閉）
    crisis_cities = []
    for city, cd in ns["cities"].items():
        cd["consumer_satisfaction"] = max(0, min(100, cd["consumer_satisfaction"]))
        cd["rider_satisfaction"]    = max(0, min(100, cd["rider_satisfaction"]))
        if config["rider_crisis"] and cd["rider_satisfaction"] < RIDER_SHORTAGE_THRESHOLD:
            cd["consumer_satisfaction"] = max(0, cd["consumer_satisfaction"] - 5)
            _share_loss = 0.01 if ns["upgrades"].get("exclusiveMerchant") else 0.03
            cd["share"] = max(0, cd["share"] - _share_loss)
            ns["competitor"][city] = min(0.60, ns["competitor"][city] + _share_loss)
            crisis_cities.append(city)

    # Step 3.7：消費者滿意度危機檢查（入門/標準模式關閉）
    consumer_crisis_cities = []  # list of (city, level) level="moderate"|"severe"
    if config["consumer_crisis"]:
        for city, cd in ns["cities"].items():
            c_sat = cd["consumer_satisfaction"]
            if c_sat < CONSUMER_MEDIA_THRESHOLD:
                # 媒體負面報導：對手趁機搶市占
                cd["share"] = max(0.0, cd["share"] - 0.05)
                ns["competitor"][city] = min(0.60, ns["competitor"][city] + 0.05)
                consumer_crisis_cities.append((city, "severe"))
            elif c_sat < CONSUMER_REVIEW_THRESHOLD and random.random() < CONSUMER_REVIEW_TRIGGER_PROB:
                # 負評爆炸：市占額外流失（機率觸發，避免每回合必噴）
                cd["share"] = max(0.0, cd["share"] - CONSUMER_REVIEW_SHARE_LOSS)
                consumer_crisis_cities.append((city, "moderate"))

    # Steps 4 & 5：計算收入（外送荒城市歸零）並扣除固定維運成本；含品牌溢價
    revenue = 0.0
    _rev_coef = REVENUE_COEFFICIENT * _event_revenue_mult
    for city, cd in ns["cities"].items():
        if city not in crisis_cities and city != _event_outage_city:
            _city_rev_coef = _rev_coef
            if ns["upgrades"].get("dynamicPricing") and cd["consumer_satisfaction"] >= DYNAMIC_PRICING_SAT_THRESHOLD:
                _city_rev_coef *= 1.15
            if config["brand_premium"] and cd["consumer_satisfaction"] >= BRAND_PREMIUM_THRESHOLD:
                _city_rev_coef *= (1 + BRAND_PREMIUM_BONUS)
            revenue += cd["market"] * cd["share"] * ns["commission_rate"] * _city_rev_coef
    ns["money"] = max(0.0, ns["money"] + revenue - FIXED_OPERATIONAL_COST - _event_fixed_cost_extra)

    # Step 6：競爭對手行動 + 財務結算
    comp_money_before = ns.get("competitor_money", COMPETITOR_INITIAL_MONEY)
    competitor_action, comp_variable_cost = resolve_competitor(ns)

    # 計算對手本季營收（破產模式下無收入）
    if ns.get("competitor_bankrupt", False):
        comp_revenue = 0.0
        comp_total_cost = 0.0
    else:
        comp_revenue = sum(
            ns["cities"][city]["market"] * ns["competitor"][city]
            * COMPETITOR_COMMISSION_RATE * REVENUE_COEFFICIENT
            for city in CITIES
        )
        comp_total_cost = COMPETITOR_FIXED_COST + comp_variable_cost
        ns["competitor_money"] = max(0.0, comp_money_before + comp_revenue - comp_total_cost)
        # 破產觸發檢查（入門模式關閉：對手永遠不會破產，維持固定反擊強度到底）
        if config["bankruptcy_enabled"] and ns["competitor_money"] <= 0 and not ns.get("competitor_bankrupt", False):
            ns["competitor_bankrupt"] = True
            competitor_action += f"　【大捷！{COMPETITOR_NAMES[0]} 現金流斷裂，宣告破產重組！】"

    # Step 6.5：對手滿意度更新 + 滿意度落差驅動的市占微調（零和，僅挑戰模式啟用）
    if config["competitor_satisfaction_enabled"] and not ns.get("competitor_bankrupt", False):
        _comp_profit = comp_revenue - comp_total_cost
        _comp_sat_delta = COMPETITOR_SAT_PROFIT_DELTA if _comp_profit >= 0 else -COMPETITOR_SAT_LOSS_DELTA
        for city in CITIES:
            ns["competitor_satisfaction"][city] = max(
                0.0, min(100.0, ns["competitor_satisfaction"][city] + _comp_sat_delta)
            )
            _player_avg_sat = (ns["cities"][city]["consumer_satisfaction"] + ns["cities"][city]["rider_satisfaction"]) / 2
            _sat_diff = _player_avg_sat - ns["competitor_satisfaction"][city]
            if abs(_sat_diff) >= SATISFACTION_DRIFT_THRESHOLD:
                _drift = SATISFACTION_SHARE_DRIFT if _sat_diff > 0 else -SATISFACTION_SHARE_DRIFT
                ns["cities"][city]["share"] = max(0.0, min(MAX_PLAYER_SHARE, ns["cities"][city]["share"] + _drift))
                ns["competitor"][city] = max(0.0, min(1.0, ns["competitor"][city] - _drift))

    # Step 7：市場飽和正規化（各城市總市占不超過 100%）
    # 玩家市占在 Step 3 後已夾在合法範圍內，是真實投資結果；若總和仍超標，
    # 該被壓縮的是對手的殘餘份額，不應該連帶把玩家剛拿到手的市占也按比例砍掉。
    for city, cd in ns["cities"].items():
        comp_share = ns["competitor"][city]
        total = cd["share"] + comp_share
        if total > 1.0:
            ns["competitor"][city] = max(0.0, round(1.0 - cd["share"], 4))

    # Steps 8 & 9：clamp 所有數值
    overall_sat = calculate_overall_satisfaction(ns)
    for cd in ns["cities"].values():
        cd["share"] = max(0.0, min(MAX_PLAYER_SHARE, round(cd["share"], 4)))
        cd["consumer_satisfaction"] = max(0, min(100, round(cd["consumer_satisfaction"], 1)))
        cd["rider_satisfaction"]    = max(0, min(100, round(cd["rider_satisfaction"],    1)))
    ns["commission_rate"] = round(max(COMMISSION_MIN, min(COMMISSION_MAX, ns["commission_rate"])), 4)

    # 記錄歷史
    sat_before = calculate_overall_satisfaction(state)
    crisis_note = "；".join(f"【⚠️外送荒】{c}" for c in crisis_cities)
    comp_action_full = competitor_action + (f"　{crisis_note}" if crisis_note else "")
    ns["history"].append({
        "round": state["round"],
        "decisions": decisions,
        "money_before": round(state["money"], 2),
        "money_after": round(ns["money"], 2),
        "revenue": round(revenue, 2),
        "shares_before": {c: round(prev_shares[c], 4) for c in CITIES},
        "shares_after": {c: round(ns["cities"][c]["share"], 4) for c in CITIES},
        "consumer_sat_before": round(sat_before["consumer"], 1),
        "consumer_sat_after":  round(overall_sat["consumer"], 1),
        "rider_sat_before":    round(sat_before["rider"], 1),
        "rider_sat_after":     round(overall_sat["rider"], 1),
        "competitor_action": comp_action_full,
        "crisis_cities": list(crisis_cities),
        "consumer_crisis_cities": [(c, lv) for c, lv in consumer_crisis_cities],
        "swan_event": {"name": active_event["name"], "description": active_event["description"], "tone": active_event["tone"]} if active_event else None,
        "competitor_money_before": round(comp_money_before, 2),
        "competitor_money_after": round(ns.get("competitor_money", comp_money_before), 2),
        "competitor_revenue": round(comp_revenue, 2),
        "competitor_cost": round(comp_total_cost, 2),
        "competitor_bankrupt": ns.get("competitor_bankrupt", False),
        "competitor_acquired": ns.get("competitor_acquired", False),
        "brand_count": dict(ns.get("brand_count", {})),
        "brand_growth_cities": list(_brand_growth_cities),
    })

    # B 層概念情境觸發偵測（沉沒成本/囚徒困境/價格敏感型消費者/錨定效應）
    _concept_triggers = detect_concept_triggers(ns)
    for _trig in _concept_triggers:
        log_event(ns, "concept_triggered", {
            "concept_id": _trig["concept_id"],
            "concept_name": _trig["concept_name"],
            "context": _trig["context"],
            "source": "situation",
        })
    ns["history"][-1]["concept_triggers"] = _concept_triggers

    ns["competitor_action"] = comp_action_full
    ns["round_report"] = ""   # 等進入 REPORT 階段再生成
    ns["round"] += 1
    ns["game_result"] = check_game_result(ns)

    log_event(ns, "round_end", {
        "money": round(ns["money"], 2),
        "shares": {c: round(ns["cities"][c]["share"], 4) for c in CITIES},
        "satisfaction": {
            c: {
                "consumer": ns["cities"][c]["consumer_satisfaction"],
                "rider": ns["cities"][c]["rider_satisfaction"],
            }
            for c in CITIES
        },
        "competitor_satisfaction": dict(ns.get("competitor_satisfaction", {})),
        "competitor_action": comp_action_full,
    })

    return ns

# ── UI 工具函式 ───────────────────────────────────────────────────────────────

def _pct(share: float) -> str:
    return f"{share * 100:.1f}%"


def _share_bar(label: str, share: float, color: str, max_width: int = 35):
    filled = max(0, int(share * max_width))
    bar = "█" * filled + "░" * (max_width - filled)
    st.markdown(
        f"<span style='color:{color}'>{label}</span> `{bar}` **{_pct(share)}**",
        unsafe_allow_html=True,
    )


def show_city_cards(state: dict):
    """顯示三城市市占卡片（依 config 切換單軌/雙軌滿意度顯示）。"""
    config = state["config"]
    cols = st.columns(3)
    comp_name = COMPETITOR_NAMES[0]
    for i, city in enumerate(CITIES):
        cd = state["cities"][city]
        comp_share = state["competitor"][city]
        no_platform = max(0.0, 1.0 - cd["share"] - comp_share)
        c_sat = cd["consumer_satisfaction"]
        r_sat = cd["rider_satisfaction"]
        c_icon = "😊" if c_sat >= 75 else ("😐" if c_sat >= 50 else "😠")
        r_icon = "😊" if r_sat >= 75 else ("😐" if r_sat >= 50 else "😠")
        shortage_warn = config["rider_crisis"] and r_sat < RIDER_SHORTAGE_THRESHOLD
        with cols[i]:
            _city_trait_label = {"台北": "⚔️ 激烈競爭", "台中": "🌱 口碑成長", "高雄": "💰 補貼高效"}.get(city, "")
            st.markdown(f"**🏙️ {city}**　市場規模 {cd['market']}　`{_city_trait_label}`")
            _mkt_buf = cd.get("marketing_buffer", 0.0)
            if _mkt_buf > 0:
                _ai_routing_buf = state.get("upgrades", {}).get("aiRouting", False)
                _ai_routing_buf_active = _ai_routing_buf and state["cities"][city]["share"] < AI_ROUTING_SHARE_THRESHOLD
                _mkt_eff_preview = MARKETING_EFFICIENCY * (1.25 if _ai_routing_buf_active else 1.0)
                _expected_gain = (_mkt_buf * _mkt_eff_preview) / cd["market"]
                st.info(f"📣 行銷緩衝待生效：{_mkt_buf:.0f} 萬　下回合預期市占 +{_expected_gain*100:.2f}%")
            if shortage_warn:
                st.error(f"🚨 外送荒警告！外送商家滿意度 {r_sat:.0f} < {RIDER_SHORTAGE_THRESHOLD}")
            if config["consumer_crisis"] and c_sat < CONSUMER_MEDIA_THRESHOLD:
                st.error(f"📰 媒體負評爆發！消費者滿意度 {c_sat:.0f} < {CONSUMER_MEDIA_THRESHOLD}，送香公趁機搶市占 -5%")
            elif config["consumer_crisis"] and c_sat < CONSUMER_REVIEW_THRESHOLD:
                st.warning(f"⭐ 負評風險！消費者滿意度 {c_sat:.0f} < {CONSUMER_REVIEW_THRESHOLD}，本季有 {CONSUMER_REVIEW_TRIGGER_PROB*100:.0f}% 機率爆炸負評，市占額外流失 -{CONSUMER_REVIEW_SHARE_LOSS*100:.0f}%")
            if config["dual_satisfaction"]:
                st.markdown(
                    f"<span style='font-size:1.3rem'>👤 消費者 {c_icon} **{c_sat:.0f}**　"
                    f"🛵 外送商家 {r_icon} **{r_sat:.0f}**</span>",
                    unsafe_allow_html=True,
                )
                st.progress(c_sat / 100, text=f"消費者 {c_sat:.0f}/100")
                st.progress(r_sat / 100, text=f"外送商家 {r_sat:.0f}/100")
            else:
                _merged = (c_sat + r_sat) / 2
                _merged_icon = "😊" if _merged >= 75 else ("😐" if _merged >= 50 else "😠")
                st.markdown(
                    f"<span style='font-size:1.3rem'>😊 整體滿意度 {_merged_icon} **{_merged:.0f}**</span>",
                    unsafe_allow_html=True,
                )
                st.progress(_merged / 100, text=f"整體滿意度 {_merged:.0f}/100")
            _share_bar("🟢 飛食", cd["share"], "#4CAF50")
            _share_bar(
                f"{COMP_ICONS.get(comp_name, '🔴')} {comp_name}",
                comp_share,
                COMP_COLORS.get(comp_name, "#F44336"),
            )
            if no_platform > 0.01:
                _share_bar("⚪ 未覆蓋", no_platform, "#9E9E9E")
            if (config["competitor_satisfaction_enabled"]
                    and not state.get("competitor_bankrupt") and not state.get("competitor_acquired")):
                _comp_sat = state.get("competitor_satisfaction", {}).get(city, COMPETITOR_SAT_INITIAL)
                _comp_sat_icon = "😊" if _comp_sat >= 75 else ("😐" if _comp_sat >= 50 else "😠")
                st.caption(f"{COMP_ICONS.get(comp_name, '🔴')} {comp_name}滿意度 {_comp_sat_icon} {_comp_sat:.0f}")
            if config.get("brand_management_enabled"):
                _bc = state.get("brand_count", {}).get(city, 0)
                if _bc > 0:
                    _can_grow = cd["consumer_satisfaction"] >= BRAND_GROWTH_THRESHOLD and _bc >= BRAND_GROWTH_MIN_COUNT
                    _growth_icon = "🚀" if _can_grow else "🏷️"
                    _growth_note = f"　成長飛輪啟動！+{BRAND_GROWTH_RATE*100:.0f}%/季" if _can_grow else ""
                    st.caption(f"{_growth_icon} 品牌累積 {_bc} 次{_growth_note}")


def show_win_conditions(state: dict):
    """顯示勝利條件進度（依 config 切換 3/4 項、單軌/雙軌滿意度）。"""
    config = state["config"]
    cities = state["cities"]
    max_share = max(cd["share"] for cd in cities.values())
    best_city = max(cities, key=lambda c: cities[c]["share"])
    sat = calculate_overall_satisfaction(state)
    money = state["money"]
    wc1 = money >= config["win_money"]
    wc2 = max_share >= config["win_share"]

    cols = st.columns(config["win_total"])
    cols[0].metric(
        f"{'✅' if wc1 else '⚠️'} 資金 ≥ {config['win_money']:.0f} 萬",
        f"{money:.1f} 萬",
    )
    cols[1].metric(
        f"{'✅' if wc2 else '⚠️'} 市占 ≥ {config['win_share']*100:.0f}%",
        f"{best_city} {_pct(max_share)}",
    )

    if config["dual_satisfaction"]:
        consumer_ok = sat["consumer"] >= config["win_consumer_sat"]
        rider_ok    = sat["rider"]    >= config["win_rider_sat"]
        wc3 = consumer_ok and rider_ok
        wc3_icon = "✅" if wc3 else ("🔶" if (consumer_ok or rider_ok) else "⚠️")
        cols[2].metric(
            f"{wc3_icon} 雙軌滿意度（需同時達標）",
            f"消費者 {sat['consumer']:.1f}  /  外送商家 {sat['rider']:.1f}",
        )
        sat_lines = (
            f"{'✅' if consumer_ok else '⚠️'} 消費者 {sat['consumer']:.1f} / {config['win_consumer_sat']:.0f}"
            f"　{'✅' if rider_ok else '⚠️'} 外送商家 {sat['rider']:.1f} / {config['win_rider_sat']:.0f}"
            + ("　→ ✅ 雙軌同時達標，計為一項勝利條件！" if wc3 else "　→ 需兩項同時達標才算達成")
        )
    else:
        merged = (sat["consumer"] + sat["rider"]) / 2
        wc3 = merged >= config["win_sat"]
        cols[2].metric(
            f"{'✅' if wc3 else '⚠️'} 整體滿意度 ≥ {config['win_sat']:.0f}",
            f"{merged:.1f}",
        )
        sat_lines = None

    if config["win_total"] == 4:
        bankrupt  = state.get("competitor_bankrupt", False)
        acquired  = state.get("competitor_acquired", False)
        comp_money = state.get("competitor_money", COMPETITOR_INITIAL_MONEY)
        wc4 = bankrupt or acquired
        _last_h = state["history"][-1] if state["history"] else None
        if bankrupt:
            _c4_delta = None
        elif _last_h and "competitor_money_after" in _last_h:
            _comp_delta = comp_money - _last_h["competitor_money_before"]
            _c4_delta = f"{_comp_delta:+.1f} 萬"
        else:
            _c4_delta = f"初始 {COMPETITOR_INITIAL_MONEY:.0f} 萬"
        _show_acq_spark = config["acquisition_enabled"] and comp_money <= ACQUISITION_THRESHOLD and not bankrupt and not acquired
        _wc4_label = "✅" if wc4 else ("⚡" if _show_acq_spark else "⚠️")
        _wc4_value = "💀 已破產" if (bankrupt and not acquired) else ("🤝 已收購" if acquired else f"{comp_money:.1f} 萬")
        _wc4_title = "送香公破產/收購" if config["acquisition_enabled"] else "送香公破產"
        cols[3].metric(
            f"{_wc4_label} {_wc4_title}",
            _wc4_value,
            delta=_c4_delta if config["competitor_finance_visible"] else None,
        )

    if sat_lines:
        st.caption(sat_lines)

# ── UI：設定畫面 ──────────────────────────────────────────────────────────────

def show_setup_screen():
    st.markdown(
        """
        <style>
        /* 開場標題 */
        .setup-title {
            font-size: 4rem;
            font-weight: 900;
            margin-bottom: 0.5rem;
            line-height: 1.2;
        }
        /* 開場說明文字 */
        .setup-desc {
            font-size: 1.8rem;
            line-height: 2.5rem;
        }
        /* subheader 字體加大 */
        section[data-testid="stMain"] h2 {
            font-size: 2.2rem !important;
        }
        /* label / widget 字體加大 */
        section[data-testid="stMain"] label,
        section[data-testid="stMain"] .stRadio label,
        section[data-testid="stMain"] .stSelectbox label,
        section[data-testid="stMain"] .stSlider label,
        section[data-testid="stMain"] .stNumberInput label,
        section[data-testid="stMain"] .stTextInput label {
            font-size: 1.5rem !important;
        }
        /* 輸入框內的文字加大 */
        section[data-testid="stMain"] input {
            font-size: 1.4rem !important;
            padding: 0.8rem !important;
        }
        /* 開始遊戲按鈕加大 */
        section[data-testid="stMain"] .stButton > button {
            font-size: 1.8rem !important;
            padding: 1rem 3rem !important;
            font-weight: bold;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("<div class='setup-title'>🛵 飛食平台經營模擬器</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='setup-desc'>"
        "你是飛食外送平台的 CEO，在 <b>台北、台中、高雄</b> 三城市與競爭對手「送香公」競爭。"
        "選一個難度開始：</div>",
        unsafe_allow_html=True,
    )

    st.divider()
    st.subheader("🎯 選擇難度")

    _DIFFICULTY_DESC = {
        "beginner": {
            "win_lines": "💰 資金 ≥ 120 萬　📊 市占 ≥ 55%　😊 整體滿意度 ≥ 60",
            "extra": "三取二即勝。單軌滿意度，關閉科技樹/黑天鵝/外送荒/送香公財務細節，專注學概念提示。",
        },
        "standard": {
            "win_lines": f"💰 資金 ≥ 250 萬　📊 市占 ≥ 60%　😊 消費者+外送員各 ≥ {int(WIN_CONSUMER_SAT)}　💀 送香公破產",
            "extra": "四取二即勝。雙軌滿意度，啟用科技樹/黑天鵝/外送荒，對手固定強度反擊，無收購選項。",
        },
        "challenge": {
            "win_lines": f"💰 資金 ≥ 250 萬　📊 市占 ≥ 60%　😊 消費者+外送員各 ≥ {int(WIN_CONSUMER_SAT)}　💀 送香公破產 / 🤝 收購",
            "extra": "四取二即勝。全機制開啟：動態反擊、消費者危機、送香公滿意度微調、收購、投資人評語。",
        },
    }

    if "_setup_difficulty" not in st.session_state:
        st.session_state["_setup_difficulty"] = "challenge"

    cols = st.columns(3)
    for col, key in zip(cols, ["beginner", "standard", "challenge"]):
        preset = DIFFICULTY_PRESETS[key]
        with col:
            _selected = st.session_state["_setup_difficulty"] == key
            if st.button(
                f"{preset['label']}\n{preset['subtitle']}",
                key=f"_diff_btn_{key}",
                type="primary" if _selected else "secondary",
                use_container_width=True,
            ):
                st.session_state["_setup_difficulty"] = key
                st.rerun()
            st.caption(f"{preset['max_rounds']} 回合｜初始資金 {preset['initial_money']:.0f} 萬")

    _chosen = st.session_state["_setup_difficulty"]
    _chosen_preset = DIFFICULTY_PRESETS[_chosen]
    _desc = _DIFFICULTY_DESC[_chosen]
    st.markdown(
        f"<div class='setup-desc' style='font-size:1.3rem;line-height:2rem'>"
        f"<b>{_chosen_preset['label']} － {_chosen_preset['subtitle']}</b><br>"
        f"{_desc['win_lines']}<br>"
        f"<small>{_desc['extra']}</small>"
        f"</div>",
        unsafe_allow_html=True,
    )

    st.divider()
    st.subheader("🔑 Gemini API Key")
    api_key_default = os.getenv("GEMINI_API_KEY", "")
    try:
        api_key_default = open(
            os.path.join(os.path.dirname(__file__), "api_key.txt")
        ).read().strip() or api_key_default
    except Exception:
        pass

    api_key = st.text_input(
        "Gemini API Key",
        value=api_key_default,
        type="password",
        help="免費申請：https://aistudio.google.com/apikey",
    )

    if st.button("🚀 開始遊戲", type="primary", disabled=not api_key):
        state = init_game_state(difficulty=_chosen)
        log_event(state, "game_start")
        st.session_state["state"] = state
        st.session_state["api_key"] = api_key
        st.session_state["advisor"] = AIAdvisor(api_key, config=state["config"])
        st.rerun()

# ── UI：側邊欄 ────────────────────────────────────────────────────────────────

def show_sidebar(state: dict):
    with st.sidebar:
        st.markdown(
            """
            <style>
            /* 側邊欄 header */
            section[data-testid="stSidebar"] h1 {
                font-size: 1.5rem !important;
            }
            /* 側邊欄 subheader */
            section[data-testid="stSidebar"] h2,
            section[data-testid="stSidebar"] h3 {
                font-size: 1.25rem !important;
            }
            /* metric label */
            section[data-testid="stSidebar"] [data-testid="stMetricLabel"] {
                font-size: 1.05rem !important;
            }
            /* metric value */
            section[data-testid="stSidebar"] [data-testid="stMetricValue"] {
                font-size: 1.6rem !important;
            }
            /* 一般 markdown 文字 */
            section[data-testid="stSidebar"] .stMarkdown p {
                font-size: 1.1rem !important;
            }
            /* caption */
            section[data-testid="stSidebar"] .stCaptionContainer p {
                font-size: 1rem !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        st.header("📊 飛食平台概況")
        advisor = st.session_state.get("advisor")
        if advisor:
            st.caption(f"🤖 AI 模型：{advisor.current_model}")

        display_round = state["round"] - 1 if state["phase"] == "REPORT" else state["round"]
        display_round = min(display_round, state["max_rounds"])
        st.metric("回合", f"{display_round} / {state['max_rounds']}")
        st.metric("💰 資金", f"{state['money']:.1f} 萬")
        st.metric("📊 抽成率", f"{state['commission_rate']*100:.0f}%")
        sat = calculate_overall_satisfaction(state)
        config = state["config"]
        if config["dual_satisfaction"]:
            st.metric("👤 消費者滿意度", f"{sat['consumer']:.1f}")
            st.metric("🛵 外送商家滿意度", f"{sat['rider']:.1f}")
        else:
            st.metric("😊 整體滿意度", f"{(sat['consumer']+sat['rider'])/2:.1f}")

        st.divider()
        st.subheader("競爭對手")
        comp_name = COMPETITOR_NAMES[0]
        icon = COMP_ICONS.get(comp_name, "🔴")
        shares = [state["competitor"][c] for c in CITIES]
        avg_share = sum(shares) / len(shares)
        st.markdown(f"{icon} **{comp_name}**　平均市占 {_pct(avg_share)}")
        if config["competitor_finance_visible"]:
            _comp_bankrupt = state.get("competitor_bankrupt", False)
            _comp_money = state.get("competitor_money", COMPETITOR_INITIAL_MONEY)
            if config["acquisition_enabled"] and state.get("competitor_acquired"):
                st.success("🤝 送香公已被飛食收購！")
            elif config["bankruptcy_enabled"] and _comp_bankrupt:
                st.error("💀 送香公已破產重組！")
            else:
                _money_pct = _comp_money / COMPETITOR_INITIAL_MONEY
                _bar_filled = max(0, int(_money_pct * 20))
                _bar = "█" * _bar_filled + "░" * (20 - _bar_filled)
                st.markdown(
                    f"💸 資金 `{_bar}` **{_comp_money:.1f} 萬**",
                    unsafe_allow_html=True,
                )

        _sidebar_upgs = state.get("upgrades", {})
        if any(_sidebar_upgs.values()):
            st.divider()
            st.subheader("🔬 科技解鎖")
            _sidebar_upg_labels = {
                "aiRouting":         "AI 智慧路徑",
                "dynamicPricing":    "雲端動態定價",
                "exclusiveMerchant": "獨家特約聯盟",
            }
            for _k, _label in _sidebar_upg_labels.items():
                if _sidebar_upgs.get(_k):
                    st.success(f"✨ {_label}")

        if state["history"]:
            st.divider()
            st.subheader("上回合")
            h = state["history"][-1]
            profit = h["money_after"] - h["money_before"]
            delta_color = "green" if profit >= 0 else "red"
            st.markdown(
                f"收入 {h['revenue']:.1f} 萬，"
                f"<span style='color:{delta_color}'>資金 {'+'if profit>=0 else ''}{profit:.1f} 萬</span>",
                unsafe_allow_html=True,
            )

# ── UI：MARKET_NEWS 階段 ──────────────────────────────────────────────────────

def _roll_swan_event(state: dict) -> dict | None:
    """每回合第一次進入 MARKET_NEWS 時抽一次黑天鵝，結果存入 state。
    滿意度與對手市占等即時效果在此直接套用，讓城市卡片即時反映；
    revenue/補貼乘數等延遲效果留給 finalize_round 處理。
    """
    if state.get("event_rolled_round") == state["round"]:
        return state.get("pending_event")  # 已抽過，回傳快取
    event = None
    if random.random() < SWAN_EVENT_PROB:
        event = dict(random.choice(SWAN_EVENTS))  # shallow copy 避免污染常數
        mods = event["modifiers"]
        # 預先決定隨機城市（讓玩家看到後才決策）
        if "random_city_consumer_delta" in mods or "random_city_revenue_zero" in mods:
            event["affected_city"] = random.choice(CITIES)
        # 格式化 description 裡的 {city} 佔位符
        event["description"] = event["description"].replace(
            "{city}", event.get("affected_city", "")
        )
        # ── 即時效果：立刻套用到 state，讓城市卡片即時反映
        affected_city = event.get("affected_city")
        if "consumer_sat_delta" in mods:
            for cd in state["cities"].values():
                cd["consumer_satisfaction"] = max(0, min(100, cd["consumer_satisfaction"] + mods["consumer_sat_delta"]))
        if "rider_sat_delta" in mods:
            for cd in state["cities"].values():
                cd["rider_satisfaction"] = max(0, min(100, cd["rider_satisfaction"] + mods["rider_sat_delta"]))
        if "random_city_consumer_delta" in mods and affected_city:
            state["cities"][affected_city]["consumer_satisfaction"] = max(
                0, min(100, state["cities"][affected_city]["consumer_satisfaction"] + mods["random_city_consumer_delta"])
            )
        if "competitor_share_delta" in mods:
            for city in CITIES:
                state["competitor"][city] = max(0.0, state["competitor"][city] + mods["competitor_share_delta"])
        if "competitor_sat_delta" in mods:
            for city in CITIES:
                state["competitor_satisfaction"][city] = max(
                    0.0, min(100.0, state["competitor_satisfaction"][city] + mods["competitor_sat_delta"])
                )
        event["immediate_applied"] = True  # 標記已套用，finalize_round 跳過
    state["pending_event"] = event
    state["event_rolled_round"] = state["round"]
    return event


def show_market_news_phase(state: dict, advisor: AIAdvisor):
    st.subheader(f"📰 第 {state['round']} 回合　產業快訊")

    # 抽黑天鵝（每回合只抽一次；入門模式關閉黑天鵝）
    event = _roll_swan_event(state) if state["config"]["black_swan"] else None
    st.session_state["state"] = state

    # 黑天鵝橫幅
    if event:
        _tone_colors = {"good": "#2E7D32", "bad": "#C62828", "mixed": "#E65100"}
        _tone_bg     = {"good": "#E8F5E9", "bad": "#FFEBEE", "mixed": "#FFF3E0"}
        _tc = _tone_colors.get(event["tone"], "#1565C0")
        _bg = _tone_bg.get(event["tone"], "#E3F2FD")
        st.markdown(
            f"<div style='background:{_bg};border-left:5px solid {_tc};"
            f"padding:0.8rem 1.2rem;border-radius:6px;margin-bottom:1rem'>"
            f"<b style='color:{_tc};font-size:1.2rem'>⚡ 本季黑天鵝事件：{event['name']}</b><br>"
            f"<span style='color:#1a1a1a;font-size:1rem'>{event['description']}</span></div>",
            unsafe_allow_html=True,
        )

    # 生成快訊（只生成一次，存入 state）
    if not state.get("market_news"):
        with st.spinner("記者撰寫本季快訊中…"):
            state["market_news"] = advisor.generate_market_news(state["round"], state)
        log_event(state, "report_interaction", {"section": "market_news", "opened": True})
        st.session_state["state"] = state

    st.info(state["market_news"])

    st.divider()
    show_city_cards(state)
    st.divider()
    _cfg = state["config"]
    st.subheader(f"🏆 勝利條件進度（{_cfg['win_total']} 項中達成 {_cfg['win_required']} 項即勝）")
    show_win_conditions(state)
    st.divider()

    if st.button("▶️ 進入決策", type="primary"):
        state["phase"] = "PLAYER_DECISION"
        st.session_state["state"] = state
        st.rerun()

# ── UI：PLAYER_DECISION 階段 ──────────────────────────────────────────────────


# ── helpers for two-step decision flow ──────────────────────────────────────

_DSL_UPGRADE_CATALOG = [
    ("AI 智慧路徑優化",  "aiRouting",         UPGRADE_AI_ROUTING_COST,         "補貼/行銷轉化 +25%，外送員衰退減半"),
    ("雲端動態定價系統", "dynamicPricing",    UPGRADE_DYNAMIC_PRICING_COST,    "消費者滿意 ≥65 的城市營收 +15%"),
    ("獨家特約商家聯盟", "exclusiveMerchant", UPGRADE_EXCLUSIVE_MERCHANT_COST, "外送荒市占流失 3%→1%"),
]

_DSL_CARD_DEFS = [
    ("subsidy",     "💰", "補貼",     "即時市占 + 滿意度"),
    ("marketing",   "📣", "行銷",     "下回合生效，轉化率高"),
    ("commission",  "📊", "抽成調整", "降低讓利 / 提高壓榨"),
    ("expansion",   "🏗️", "區域擴張", "空間卡位，市場 ×1.3"),
    ("brand",       "🏷️", "品牌經營", "養滿意度、啟動飛輪"),
    ("upgrade",     "🔬", "科技研發", "永久解鎖加成"),
    ("acquisition", "🤝", "收購對手", "一舉終結競爭"),
]


def _dsl_maybe_reset(state: dict):
    """Reset two-slot state when a new round begins."""
    cur = state["round"]
    if st.session_state.get("dsl_round") != cur:
        for k in ("dsl_slot1_type", "dsl_slot1_data", "dsl_slot2_type", "dsl_slot2_data"):
            st.session_state[k] = None
        st.session_state["dsl_slot1_locked"] = False
        st.session_state["dsl_slot2_locked"] = False
        st.session_state["dsl_round"] = cur


def _dsl_card_available(card_type: str, state: dict, exclude_type) -> bool:
    config = state["config"]
    if card_type == exclude_type:
        return False
    if card_type == "brand" and not config.get("brand_management_enabled"):
        return False
    if card_type == "expansion":
        if not config.get("expansion_enabled", True):
            return False
        if not [c for c in CITIES if c not in state["expanded_cities"]]:
            return False
    if card_type == "upgrade":
        if not config.get("tech_tree"):
            return False
        upgs = state.get("upgrades", {})
        if all(upgs.get(k) for _, k, _, _ in _DSL_UPGRADE_CATALOG):
            return False
    if card_type == "acquisition":
        if not config.get("acquisition_enabled"):
            return False
        comp_money = state.get("competitor_money", COMPETITOR_INITIAL_MONEY)
        if (state.get("competitor_acquired")
                or state.get("competitor_bankrupt")
                or comp_money > ACQUISITION_THRESHOLD):
            return False
    return True


def _dsl_show_cards(state: dict, slot_num: int, exclude_type):
    st.caption(f"**決策 {slot_num}**：點選類型")
    cards = [(ct, ic, nm, dc) for ct, ic, nm, dc in _DSL_CARD_DEFS
             if _dsl_card_available(ct, state, exclude_type)]
    rows = [cards[i:i + 3] for i in range(0, len(cards), 3)]
    for row in rows:
        cols = st.columns(3)
        for j, (ct, ic, nm, dc) in enumerate(row):
            with cols[j]:
                if st.button(f"{ic} {nm}", key=f"dsl_card_{slot_num}_{ct}",
                             use_container_width=True):
                    st.session_state[f"dsl_slot{slot_num}_type"] = ct
                    st.rerun()
                st.caption(dc)
    if slot_num == 2:
        if st.button("⏭️ 跳過，只選一項", key="dsl_skip"):
            st.session_state["dsl_slot2_type"] = "skip"
            st.session_state["dsl_slot2_locked"] = True
            st.session_state["dsl_slot2_data"] = None
            st.rerun()


def _dsl_show_detail(state: dict, slot_num: int):
    slot_type = st.session_state[f"dsl_slot{slot_num}_type"]
    prefix = f"dsl_s{slot_num}_"
    commission_pct = int(state["commission_rate"] * 100)
    money_cap = max(5, int(state["money"] // 5) * 5)
    not_expanded = [c for c in CITIES if c not in state["expanded_cities"]]
    _ai_routing = state.get("upgrades", {}).get("aiRouting", False)
    _icon_map = {ct: ic for ct, ic, _, _ in _DSL_CARD_DEFS}
    _name_map = {ct: nm for ct, ic, nm, _ in _DSL_CARD_DEFS}
    st.markdown(f"**{_icon_map.get(slot_type, '')} {_name_map.get(slot_type, slot_type)}**　決策 {slot_num}")

    decision_data = None
    valid = True

    if slot_type == "subsidy":
        city = st.selectbox("目標城市", CITIES, key=f"{prefix}city")
        _s_ai = _ai_routing and state["cities"][city]["share"] < AI_ROUTING_SHARE_THRESHOLD
        _s_mult = CITY_TRAITS.get(city, {}).get("subsidy_mult", 1.0)
        _decay = get_subsidy_decay(
            state["cities"][city].get("consecutive_subsidy_count", 0) + 1)
        _eff = SUBSIDY_EFFICIENCY * (1.25 if _s_ai else 1.0) * _s_mult
        amt = st.slider("金額（萬）", min_value=5, max_value=money_cap,
                        value=10, step=5, key=f"{prefix}amt")
        gain = (amt * _eff * _decay) / CITY_META[city]["market"]
        notes = []
        if _s_ai:        notes.append("✨AI路由 +25%")
        if _s_mult != 1: notes.append(f"🏙️{city}加成 ×{_s_mult}")
        if _decay < 1:   notes.append(f"⚠️連續補貼效率 {_decay*100:.0f}%")
        st.caption(
            f"市占 +{gain*100:.2f}%　👤 消費者 +5　🛵 外送員 +1"
            + ("　" + "　".join(notes) if notes else ""))
        decision_data = {"type": "subsidy", "city": city, "amount": amt}

    elif slot_type == "marketing":
        city = st.selectbox("目標城市", CITIES, key=f"{prefix}city")
        _m_ai = _ai_routing and state["cities"][city]["share"] < AI_ROUTING_SHARE_THRESHOLD
        _m_eff = MARKETING_EFFICIENCY * (1.25 if _m_ai else 1.0)
        amt = st.slider("金額（萬）", min_value=5, max_value=money_cap,
                        value=10, step=5, key=f"{prefix}amt")
        gain = (amt * _m_eff) / CITY_META[city]["market"]
        st.caption(
            f"下回合市占 +{gain*100:.2f}%（延遲生效）"
            + ("　✨AI路由" if _m_ai else ""))
        decision_data = {"type": "marketing", "city": city, "amount": amt}

    elif slot_type == "commission":
        _at_min = commission_pct <= int(COMMISSION_MIN * 100)
        _at_max = commission_pct >= int(COMMISSION_MAX * 100)
        if _at_min:
            st.info(f"已達下限 {int(COMMISSION_MIN*100)}%，無法再降")
            valid = False
        elif _at_max:
            st.info(f"已達上限 {int(COMMISSION_MAX*100)}%，無法再升")
            valid = False
        else:
            direction = st.radio("方向", ["降低 5%", "提高 5%"], key=f"{prefix}dir")
            delta = -COMMISSION_STEP if direction == "降低 5%" else COMMISSION_STEP
            new_pct = max(int(COMMISSION_MIN * 100),
                         min(int(COMMISSION_MAX * 100),
                             commission_pct + int(delta * 100)))
            if delta < 0:
                effect = (f"全域外送員 +{int(abs(delta*100)*1.5):.0f}、"
                          f"消費者 +{int(abs(delta*100)*0.5)}")
            else:
                effect = (f"全域外送員 -{int(delta*100*2.5):.0f}、"
                          f"消費者 -{int(delta*100)}")
            st.caption(f"抽成 {commission_pct}% → {new_pct}%　{effect}")
            decision_data = {"type": "commission", "delta": delta}

    elif slot_type == "expansion":
        if not not_expanded:
            st.success("三城市均已完成擴張")
            valid = False
        elif state["money"] < EXPANSION_COST:
            st.error(f"資金不足（需 {EXPANSION_COST} 萬）")
            valid = False
        else:
            city = st.selectbox("目標城市", not_expanded, key=f"{prefix}city")
            old_mkt = state["cities"][city]["market"]
            new_mkt = round(old_mkt * (1 + EXPANSION_MARKET_GROWTH))
            st.caption(
                f"市占即時 +{EXPANSION_IMMEDIATE*100:.0f}%　"
                f"市場規模 {old_mkt}→{new_mkt}　免除自然流失")
            decision_data = {"type": "expansion", "city": city}

    elif slot_type == "brand":
        if state["money"] < BRAND_MGMT_COST:
            st.error(f"資金不足（需 {BRAND_MGMT_COST} 萬）")
            valid = False
        else:
            city = st.selectbox("目標城市", CITIES, key=f"{prefix}city")
            _bc = state.get("brand_count", {}).get(city, 0)
            _can_fly = (_bc >= BRAND_GROWTH_MIN_COUNT
                        and state["cities"][city]["consumer_satisfaction"]
                        >= BRAND_GROWTH_THRESHOLD)
            _fly_rate = (BRAND_GROWTH_RATE
                         * CITY_TRAITS.get(city, {}).get("brand_growth_mult", 1.0))
            extra = (f"　🚀 飛輪啟動中 +{_fly_rate*100:.0f}%/季"
                     if _can_fly
                     else f"　品牌累積 {_bc}/{BRAND_GROWTH_MIN_COUNT} 次")
            st.caption(
                f"消費者 +{BRAND_MGMT_CONSUMER_SAT}　"
                f"外送員 +{BRAND_MGMT_RIDER_SAT}　"
                f"市占 +{BRAND_MGMT_SHARE_GAIN*100:.0f}%　"
                f"費用 {BRAND_MGMT_COST}萬{extra}")
            decision_data = {"type": "brand_management", "city": city}

    elif slot_type == "upgrade":
        _cur_upg = state.get("upgrades", {})
        _available = [(n, k, c, d) for n, k, c, d in _DSL_UPGRADE_CATALOG
                      if not _cur_upg.get(k)]
        for n, k, _, _ in _DSL_UPGRADE_CATALOG:
            if _cur_upg.get(k):
                st.success(f"✨ {n} — 已解鎖")
        if not _available:
            st.info("三項科技均已解鎖")
            valid = False
        elif state["money"] < min(c for _, _, c, _ in _available):
            st.error(f"資金不足（最低 {min(c for _,_,c,_ in _available)} 萬）")
            valid = False
        else:
            _labels = [f"{n}（{c} 萬）— {d}" for n, _, c, d in _available]
            idx = st.radio("選擇研發項目", range(len(_available)),
                           format_func=lambda i: _labels[i],
                           key=f"{prefix}upg_idx")
            _, upgrade_key, upg_cost, _ = _available[idx]
            if state["money"] < upg_cost:
                st.warning(f"此項費用 {upg_cost} 萬，超出現有資金")
                valid = False
            else:
                decision_data = {"type": "upgrade", "upgradeType": upgrade_key}

    elif slot_type == "acquisition":
        if state["money"] < ACQUISITION_COST:
            st.error(f"資金不足（需 {ACQUISITION_COST:.0f} 萬）")
            valid = False
        else:
            comp_money = state.get("competitor_money", COMPETITOR_INITIAL_MONEY)
            st.caption(
                f"花費 {ACQUISITION_COST:.0f} 萬買下送香公"
                f"（現有 {comp_money:.1f} 萬），計入勝利條件")
            decision_data = {"type": "acquisition"}

    col_ok, col_back = st.columns([2, 1])
    with col_ok:
        if valid and st.button(
                f"✅ 確認決策 {slot_num}",
                key=f"dsl_confirm_{slot_num}",
                type="primary",
                use_container_width=True):
            st.session_state[f"dsl_slot{slot_num}_data"] = decision_data
            st.session_state[f"dsl_slot{slot_num}_locked"] = True
            st.rerun()
    with col_back:
        if st.button("↩️ 重選", key=f"dsl_back_{slot_num}",
                     use_container_width=True):
            st.session_state[f"dsl_slot{slot_num}_type"] = None
            st.rerun()


def _dsl_locked_label(data, state: dict) -> str:
    if data is None:
        return "（跳過）"
    t = data["type"]
    if t == "subsidy":
        city, amt = data["city"], data["amount"]
        _decay = get_subsidy_decay(
            state["cities"][city].get("consecutive_subsidy_count", 0) + 1)
        _mult = CITY_TRAITS.get(city, {}).get("subsidy_mult", 1.0)
        gain = (amt * SUBSIDY_EFFICIENCY * _decay * _mult) / CITY_META[city]["market"]
        return f"💰 補貼{city} {amt}萬 → 市占 +{gain*100:.1f}%"
    if t == "marketing":
        return f"📣 行銷{data['city']} {data['amount']}萬（下回合生效）"
    if t == "commission":
        return f"📊 抽成{'調低' if data['delta'] < 0 else '調高'} 5%"
    if t == "expansion":
        return f"🏗️ 擴張{data['city']} → 市場 ×{1+EXPANSION_MARKET_GROWTH:.1f}"
    if t == "brand_management":
        return (f"🏷️ 品牌{data['city']} → "
                f"消費者 +{BRAND_MGMT_CONSUMER_SAT}、外送員 +{BRAND_MGMT_RIDER_SAT}")
    if t == "upgrade":
        _names = {
            "aiRouting": "AI路由",
            "dynamicPricing": "動態定價",
            "exclusiveMerchant": "獨家聯盟",
        }
        return f"🔬 研發：{_names.get(data['upgradeType'], data['upgradeType'])}"
    if t == "acquisition":
        return f"🤝 收購送香公（{ACQUISITION_COST:.0f}萬）"
    return str(data)

def show_decision_phase(state: dict):
    st.subheader(f"🎯 第 {state['round']} 回合　決策（最多 2 項）")

    # 資金狀態橫幅
    _money = state["money"]
    _after_fixed = _money - FIXED_OPERATIONAL_COST
    _money_color = "#4CAF50" if _money >= 60 else ("#FF9800" if _money >= 30 else "#F44336")
    st.markdown(
        f"<div style='background:{_money_color}22;border-left:4px solid {_money_color};"
        f"padding:0.6rem 1rem;border-radius:4px;margin-bottom:0.5rem;color:inherit'>"
        f"💰 <b>目前資金：{_money:.1f} 萬</b>　｜　"
        f"扣除本回合固定維運成本 {FIXED_OPERATIONAL_COST:.0f} 萬後剩餘 "
        f"<b>{_after_fixed:.1f} 萬</b></div>",
        unsafe_allow_html=True,
    )
    st.caption(f"未補貼城市市占 -{NATURAL_DECAY_RATE*100:.0f}%，消費者滿意度 -{CONSUMER_SAT_DECAY} / 季")

    show_city_cards(state)
    st.divider()

    _dsl_maybe_reset(state)

    s1_type   = st.session_state.get("dsl_slot1_type")
    s1_locked = st.session_state.get("dsl_slot1_locked", False)
    s1_data   = st.session_state.get("dsl_slot1_data")
    s2_type   = st.session_state.get("dsl_slot2_type")
    s2_locked = st.session_state.get("dsl_slot2_locked", False)

    # ── Slot 1 ──────────────────────────────────────────────────────────────
    if not s1_locked:
        if s1_type is None:
            _dsl_show_cards(state, slot_num=1, exclude_type=None)
            st.divider()
            if st.button("⏭️ 跳過本回合，不做任何決策", key="dsl_skip_all"):
                log_event(state, "decision_made", {"decisions": [], "money_before": state["money"]})
                new_state = finalize_round(state, [])
                new_state["phase"] = "REPORT"
                st.session_state["state"] = new_state
                st.rerun()
        else:
            _dsl_show_detail(state, slot_num=1)
    else:
        st.success(f"✅ 決策 1：{_dsl_locked_label(s1_data, state)}")
        col_e1, _ = st.columns([1, 4])
        with col_e1:
            if st.button("🗑️ 刪除", key="dsl_edit_1"):
                for k in ("dsl_slot1_type", "dsl_slot1_data",
                          "dsl_slot2_type", "dsl_slot2_data"):
                    st.session_state[k] = None
                st.session_state["dsl_slot1_locked"] = False
                st.session_state["dsl_slot2_locked"] = False
                st.rerun()

        st.divider()

        # ── Slot 2 ──────────────────────────────────────────────────────────
        if not s2_locked:
            if s2_type is None:
                _dsl_show_cards(state, slot_num=2, exclude_type=s1_type)
            else:
                _dsl_show_detail(state, slot_num=2)
        else:
            s2_data = st.session_state.get("dsl_slot2_data")
            if s2_data is not None:
                st.success(f"✅ 決策 2：{_dsl_locked_label(s2_data, state)}")
                col_e2, _ = st.columns([1, 4])
                with col_e2:
                    if st.button("🗑️ 刪除", key="dsl_edit_2"):
                        st.session_state["dsl_slot2_type"] = None
                        st.session_state["dsl_slot2_locked"] = False
                        st.session_state["dsl_slot2_data"] = None
                        st.rerun()

            st.divider()

            # ── Submit ───────────────────────────────────────────────────────
            s2_data = st.session_state.get("dsl_slot2_data")
            decisions = [d for d in [s1_data, s2_data] if d is not None]
            _total_spend = 0
            for d in decisions:
                if d["type"] in ("subsidy", "marketing"):
                    _total_spend += d.get("amount", 0)
                elif d["type"] == "expansion":
                    _total_spend += EXPANSION_COST
                elif d["type"] == "acquisition":
                    _total_spend += ACQUISITION_COST
                elif d["type"] == "brand_management":
                    _total_spend += BRAND_MGMT_COST
                elif d["type"] == "upgrade":
                    _total_spend += next(
                        c for _, k, c, _ in _DSL_UPGRADE_CATALOG
                        if k == d["upgradeType"])
            if _total_spend > state["money"]:
                st.error(
                    f"💸 組合花費 {_total_spend:.0f} 萬超出現有資金 "
                    f"{state['money']:.1f} 萬，請修改決策")
            else:
                if st.button(
                        "✅ 確認送出，執行本回合決策",
                        type="primary",
                        key="dsl_submit",
                        use_container_width=True):
                    log_event(state, "decision_made", {
                        "decisions": [
                            {
                                "type": d["type"],
                                "city": d.get("city"),
                                "amount": d.get("amount"),
                                "delta": d.get("delta"),
                            }
                            for d in decisions
                        ],
                        "money_before": state["money"],
                    })
                    new_state = finalize_round(state, decisions)
                    new_state["phase"] = "REPORT"
                    st.session_state["state"] = new_state
                    st.rerun()

# ── UI：REPORT 階段 ───────────────────────────────────────────────────────────

def generate_round_headline(state: dict, last: dict) -> str:
    """純 Python 生成回合結算一句話重點（按優先順序選最重要的事件）。"""
    config = state["config"]
    # 1. 外送荒
    if last.get("crisis_cities"):
        city = last["crisis_cities"][0]
        loss = "1%" if state.get("upgrades", {}).get("exclusiveMerchant") else "3%"
        return f"⚠️ {city} 外送荒爆發，本季營收斷流、市占 -{loss}"
    # 2. 消費者危機
    if last.get("consumer_crisis_cities"):
        city, level = last["consumer_crisis_cities"][0]
        loss = "5%" if level == "severe" else f"{CONSUMER_REVIEW_SHARE_LOSS*100:.0f}%"
        return f"⚠️ {city} 消費者負評爆發，市占流失 -{loss}"
    # 3. 對手首次破產 / 收購
    if last.get("competitor_bankrupt"):
        prev_bankrupt = state["history"][-2].get("competitor_bankrupt", False) if len(state["history"]) >= 2 else False
        if not prev_bankrupt:
            tag = "被收購" if last.get("competitor_acquired") else "宣告破產"
            return f"🎉 送香公{tag}！市場版圖正在重整"
    # 4. 品牌飛輪首次觸發
    new_flywheel = [
        c for c in last.get("brand_growth_cities", [])
        if c not in (state["history"][-2].get("brand_growth_cities", []) if len(state["history"]) >= 2 else [])
    ]
    if new_flywheel:
        city = new_flywheel[0]
        rate = BRAND_GROWTH_RATE * CITY_TRAITS.get(city, {}).get("brand_growth_mult", 1.0)
        return f"🚀 {city} 品牌飛輪啟動！消費者口碑轉化為每季自動 +{rate*100:.0f}% 市占"
    # 5. 市占首次突破網路效應門檻
    for city in CITIES:
        prev_share = last.get("shares_before", {}).get(city, 0)
        curr_share = last.get("shares_after", {}).get(city, 0)
        if curr_share >= NETWORK_EFFECT_THRESHOLD > prev_share:
            return f"🌐 {city} 市占突破 {NETWORK_EFFECT_THRESHOLD*100:.0f}%！網路效應啟動，補貼轉化效率 +15%"
    # 6. 任一城市超過勝利門檻
    win_share = config.get("win_share", 0.60)
    for city in CITIES:
        share = last.get("shares_after", {}).get(city, 0)
        if share >= win_share:
            return f"🏆 {city} 市占 {share*100:.0f}%，已超越勝利門檻 {win_share*100:.0f}%！"
    # 7. 本季淨虧損 > 20萬
    net = last["money_after"] - last["money_before"]
    if net < -20:
        return f"📉 本季淨虧損 {abs(net):.0f} 萬，現金儲備告急"
    # 8. 黑天鵝
    swan = last.get("swan_event")
    if swan:
        emoji = {"good": "🌟", "bad": "⛈️", "mixed": "⚡"}.get(swan.get("tone", "mixed"), "⚡")
        return f"{emoji} 黑天鵝：{swan['name']}"
    # 預設
    return f"📌 第 {last['round']} 季結算完畢，現有資金 {last['money_after']:.0f} 萬"


def show_report_phase(state: dict, advisor: AIAdvisor):
    completed_round = state["round"] - 1
    st.subheader(f"📋 第 {completed_round} 回合　結算報告")

    last = state["history"][-1]

    # 關鍵數字
    profit = last["money_after"] - last["money_before"]
    config = state["config"]
    if config["dual_satisfaction"]:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("本回合收入", f"{last['revenue']:.1f} 萬")
        c2.metric("資金變化", f"{last['money_after']:.1f} 萬", delta=f"{profit:+.1f} 萬")
        c3.metric(
            "👤 消費者滿意度",
            f"{last['consumer_sat_after']:.1f}",
            delta=f"{last['consumer_sat_after']-last['consumer_sat_before']:+.1f}",
        )
        c4.metric(
            "🛵 外送商家滿意度",
            f"{last['rider_sat_after']:.1f}",
            delta=f"{last['rider_sat_after']-last['rider_sat_before']:+.1f}",
        )
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("本回合收入", f"{last['revenue']:.1f} 萬")
        c2.metric("資金變化", f"{last['money_after']:.1f} 萬", delta=f"{profit:+.1f} 萬")
        _merged_after  = (last["consumer_sat_after"] + last["rider_sat_after"]) / 2
        _merged_before = (last["consumer_sat_before"] + last["rider_sat_before"]) / 2
        c3.metric(
            "😊 整體滿意度",
            f"{_merged_after:.1f}",
            delta=f"{_merged_after-_merged_before:+.1f}",
        )
    if last.get("crisis_cities"):
        _em = "1%" if state.get("upgrades", {}).get("exclusiveMerchant") else "3%"
        for c in last["crisis_cities"]:
            st.error(f"🚨 {c} 爆發外送荒！本季營收斷流，市占 -{_em} 直接轉讓給送香公。")
    for _city, _lv in last.get("consumer_crisis_cities", []):
        if _lv == "severe":
            st.error(f"📰 {_city} 消費者滿意度跌破 {CONSUMER_MEDIA_THRESHOLD}，媒體負面報導發酵，送香公趁虛搶走 5% 市占。")
        else:
            st.warning(f"⭐ {_city} 消費者負評爆炸（滿意度 < {CONSUMER_REVIEW_THRESHOLD}），本季市占額外流失 {CONSUMER_REVIEW_SHARE_LOSS*100:.0f}%。")

    # 品牌經營執行通知
    _bm_decisions = [d for d in last.get("decisions", []) if d.get("type") == "brand_management"]
    for _bmd in _bm_decisions:
        _bm_city = _bmd.get("city", "")
        _bm_bc_after = last.get("brand_count", {}).get(_bm_city, 0)
        st.success(
            f"🏷️ {_bm_city} 品牌經營執行！消費者滿意度 +{BRAND_MGMT_CONSUMER_SAT}、外送商家 +{BRAND_MGMT_RIDER_SAT}、市占 +{BRAND_MGMT_SHARE_GAIN*100:.0f}%　｜　累積次數：{_bm_bc_after}"
        )
    _bm_growth = last.get("brand_growth_cities", [])
    for _bg_city in _bm_growth:
        st.info(f"🚀 {_bg_city} 品牌成長飛輪觸發！消費者信任達標，本季自動 +{BRAND_GROWTH_RATE*100:.0f}% 市占。")

    # 科技發動通知
    _newly_unlocked = [d["upgradeType"] for d in last.get("decisions", []) if d.get("type") == "upgrade"]
    _upg_names = {
        "aiRouting":         "AI 智慧路徑優化",
        "dynamicPricing":    "雲端動態定價系統",
        "exclusiveMerchant": "獨家特約商家聯盟",
    }
    for _ut in _newly_unlocked:
        st.success(f"🔬 研發成功！**{_upg_names[_ut]}** 已解鎖，效果持續生效至遊戲結束！")
    _active_buffs = []
    _upgs = state.get("upgrades", {})
    if _upgs.get("aiRouting") and "aiRouting" not in _newly_unlocked:
        _active_buffs.append(f"✨【AI 智慧路徑】市占 < {AI_ROUTING_SHARE_THRESHOLD*100:.0f}% 的城市補貼/行銷轉化 +25%，外送員滿意衰退減半")
    if _upgs.get("dynamicPricing") and "dynamicPricing" not in _newly_unlocked:
        _active_buffs.append(f"✨【動態定價】消費者滿意度 ≥ {DYNAMIC_PRICING_SAT_THRESHOLD} 的城市本季營收 +15%")
    if _upgs.get("exclusiveMerchant") and "exclusiveMerchant" not in _newly_unlocked:
        _active_buffs.append("✨【獨家聯盟】外送荒市占流失率降至 1%")
    if _active_buffs:
        st.info("**🔬 科技加成生效中**\n\n" + "\n\n".join(_active_buffs))

    # 市占變化（和上回合結束時比較，避免行銷緩衝/擴張持續效果讓數字變奇怪）
    st.markdown("**市占變化**")
    sh_cols = st.columns(3)
    _prev_shares = (
        state["history"][-2]["shares_after"] if len(state["history"]) >= 2
        else INITIAL_PLAYER_SHARES
    )
    for i, city in enumerate(CITIES):
        after  = last["shares_after"][city]
        prev   = _prev_shares[city]
        with sh_cols[i]:
            st.metric(city, _pct(after), delta=f"{(after-prev)*100:+.1f}%")
            _decay_count = state["cities"][city].get("consecutive_subsidy_count", 0)
            if _decay_count >= 2:
                _decay_pct = int(get_subsidy_decay(_decay_count) * 100)
                st.caption(f"⚠️ 連續補貼第 {_decay_count} 季，效率僅 {_decay_pct}%")

    # 黑天鵝事件回顧
    _swan = last.get("swan_event")
    if _swan:
        _swan_colors = {"good": "#2E7D32", "bad": "#C62828", "mixed": "#E65100"}
        _swan_bg     = {"good": "#E8F5E9", "bad": "#FFEBEE", "mixed": "#FFF3E0"}
        _sc = _swan_colors.get(_swan["tone"], "#1565C0")
        _sb = _swan_bg.get(_swan["tone"], "#E3F2FD")
        st.markdown(
            f"<div style='background:{_sb};border-left:4px solid {_sc};"
            f"padding:0.5rem 1rem;border-radius:4px;color:#1a1a1a'>"
            f"⚡ <b>本季黑天鵝：{_swan['name']}</b>　{_swan['description']}</div>",
            unsafe_allow_html=True,
        )

    # 競爭對手事件 + 財務動態
    if last.get("competitor_action"):
        st.warning(f"**對手動態**　{last['competitor_action']}")
    if state["config"]["competitor_finance_visible"] and "competitor_money_before" in last:
        _cm_before = last["competitor_money_before"]
        _cm_after  = last["competitor_money_after"]
        _cm_delta  = _cm_after - _cm_before
        _cm_color  = "green" if _cm_delta >= 0 else "red"
        _bankrupt_tag = "　💀 **已破產**" if last.get("competitor_bankrupt") else ""
        st.markdown(
            f"📊 **送香公財務**　"
            f"營收 {last['competitor_revenue']:.1f} 萬　成本 {last['competitor_cost']:.1f} 萬　"
            f"資金 {_cm_before:.1f} → **{_cm_after:.1f} 萬**　"
            f"<span style='color:{_cm_color}'>({'+'if _cm_delta>=0 else ''}{_cm_delta:.1f} 萬)</span>"
            f"{_bankrupt_tag}",
            unsafe_allow_html=True,
        )

    st.divider()

    # 一句話重點（純 Python，不呼叫 AI）
    _headline = generate_round_headline(state, last)
    st.subheader(_headline)

    # AI 經營報告
    st.subheader("🤖 AI 顧問經營報告")
    if not state.get("round_report"):
        with st.spinner("顧問撰寫報告中…"):
            state["round_report"] = advisor.generate_round_report(
                completed_round, last, state
            )
        log_event(state, "report_interaction", {"section": "round_report", "opened": True})
        st.session_state["state"] = state
    st.markdown(state["round_report"])

    st.divider()

    # 勝利條件
    _cfg = state["config"]
    st.subheader(f"🏆 勝利條件進度（{_cfg['win_total']} 項中達成 {_cfg['win_required']} 項即勝）")
    show_win_conditions(state)

    st.divider()

    # 投資人短評（僅挑戰模式啟用）
    if state["config"]["investor_comment"]:
        if not state.get("investor_comment"):
            with st.spinner("投資人正在看數字…"):
                state["investor_comment"] = advisor.generate_investor_comment(
                    completed_round, last, state
                )
            st.session_state["state"] = state
        _ic = state["investor_comment"]
        st.markdown(
            f"<div style='background:#F3E5F5;border-left:4px solid #7B1FA2;"
            f"padding:0.6rem 1.2rem;border-radius:6px;color:#1a1a1a'>"
            f"<span style='color:#6A1B9A;font-size:0.9rem'>💼 投資人評語</span><br>"
            f"<span style='font-size:1.1rem;font-style:italic'>「{_ic}」</span></div>",
            unsafe_allow_html=True,
        )

    st.divider()
    game_over = state["game_result"] in ("win", "lose") or state["round"] > state["max_rounds"]

    col_next, col_restart = st.columns(2)
    if not game_over:
        if col_next.button("▶️ 進入下一回合", type="primary"):
            state["phase"] = "MARKET_NEWS"
            state["market_news"] = ""      # 清空，下回合重新生成
            state["investor_comment"] = "" # 清空，下回合重新生成
            st.session_state["state"] = state
            st.rerun()
    else:
        if col_next.button("🏁 查看最終結算", type="primary"):
            state["phase"] = "GAME_OVER"
            st.session_state["state"] = state
            st.rerun()

    if col_restart.button("🔄 重新開始"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

# ── UI：GAME_OVER 畫面 ────────────────────────────────────────────────────────

def _score_bar(label: str, value: float, weight_text: str):
    st.markdown(f"**{label}　{value:.0f} / 100（{weight_text}）**")
    st.progress(value / 100)

def show_gameover_screen(state: dict, advisor: AIAdvisor):
    result = state.get("game_result", check_game_result(state))

    # 第一次進入結算畫面時捲回頂部
    if not st.session_state.get("_game_end_logged"):
        st.components.v1.html("<script>window.parent.scrollTo({top: 0, behavior: 'smooth'});</script>", height=0)

    # 只在第一次進入 GAME_OVER 時記錄 game_end（避免 rerun 重複寫入）
    if not st.session_state.get("_game_end_logged"):
        log_event(state, "game_end", {
            "result": result,
            "rounds_played": state["round"] - 1,
            "total_decisions": sum(1 for e in state["event_log"] if e["event_type"] == "decision_made"),
            "concepts_seen": list({
                e["data"]["concept_id"]
                for e in state["event_log"]
                if e["event_type"] == "hint_interaction" and e["data"].get("opened")
            }),
        })
        st.session_state["_game_end_logged"] = True

    # ── 計算評分（只算一次，存入 session_state）
    if "_scoring" not in st.session_state:
        primary, p_icon, p_desc, secondary, s_icon, s_desc = sc.determine_badges(state)
        grade_data = sc.calculate_grade(state)
        st.session_state["_scoring"] = {
            "primary": primary, "primary_icon": p_icon, "primary_desc": p_desc,
            "secondary": secondary, "secondary_icon": s_icon, "secondary_desc": s_desc,
            **grade_data,
        }
    _sc = st.session_state["_scoring"]

    _cfg = state["config"]
    _grade_emoji = {"S": "🌟", "A": "🟢", "B": "🔵", "C": "🟠", "D": "🔴"}

    # ── 第一階段：大版評分 + 徽章，等玩家點按鈕再進結算報告
    if not st.session_state.get("_scoring_revealed"):
        # 結果標題（大）
        if result == "win":
            st.markdown("<h1 style='text-align:center;font-size:2.8rem'>🎉 Series A 融資成功！</h1>", unsafe_allow_html=True)
        else:
            st.markdown("<h1 style='text-align:center;font-size:2.8rem'>😔 Series A 融資未通過</h1>", unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # 等級超大顯示
        _g = _sc["grade"]
        st.markdown(
            f"<div style='text-align:center;font-size:5rem;line-height:1.1'>{_grade_emoji[_g]}</div>"
            f"<div style='text-align:center;font-size:3rem;font-weight:700'>{_g} 級　{_sc['total']:.0f} 分</div>",
            unsafe_allow_html=True,
        )

        st.markdown("<br>", unsafe_allow_html=True)

        # 四維評分
        col_l, col_mid, col_r = st.columns([1, 3, 1])
        with col_mid:
            _score_bar("經營效率", _sc["efficiency"], "權重 30%")
            _score_bar("市場掌控", _sc["market"],     "權重 25%")
            _score_bar("品牌聲譽", _sc["reputation"], "權重 25%")
            _score_bar("策略深度", _sc["depth"],      "權重 20%")

        st.markdown("<br>", unsafe_allow_html=True)

        # 徽章（置中）
        st.markdown(
            f"<div style='text-align:center;font-size:3rem'>{_sc['primary_icon']}</div>"
            f"<div style='text-align:center;font-size:1.6rem;font-weight:700'>{_sc['primary']}</div>"
            f"<div style='text-align:center;font-size:0.95rem;opacity:0.7'>{_sc['primary_desc']}</div>",
            unsafe_allow_html=True,
        )
        if _sc["secondary"]:
            st.markdown(
                f"<div style='text-align:center;margin-top:0.8rem;font-size:1.1rem'>"
                f"{_sc['secondary_icon']} <b>{_sc['secondary']}</b>　{_sc['secondary_desc']}</div>",
                unsafe_allow_html=True,
            )

        st.markdown("<br><br>", unsafe_allow_html=True)
        if st.button("📋 查看完整結算報告", type="primary", use_container_width=True):
            st.session_state["_scoring_revealed"] = True
            st.rerun()
        return

    # ── 第二階段：CEO 報告版面 ────────────────────────────────────────────────

    # 全域報告樣式
    st.markdown("""
<style>
.report-header {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 60%, #0f3460 100%);
    color: #fff;
    padding: 2rem 2.5rem 1.5rem;
    border-radius: 8px;
    margin-bottom: 1.5rem;
}
.report-header .company { font-size: 0.85rem; letter-spacing: 0.18em; color: #a0aec0; text-transform: uppercase; }
.report-header .doc-title { font-size: 1.9rem; font-weight: 700; margin: 0.3rem 0 0.2rem; }
.report-header .doc-meta { font-size: 0.88rem; color: #90cdf4; }
.report-header .verdict-win  { display:inline-block; margin-top:0.8rem; background:#276749; color:#c6f6d5; padding:0.25rem 0.9rem; border-radius:4px; font-size:0.9rem; font-weight:600; }
.report-header .verdict-lose { display:inline-block; margin-top:0.8rem; background:#742a2a; color:#fed7d7; padding:0.25rem 0.9rem; border-radius:4px; font-size:0.9rem; font-weight:600; }
.section-heading {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    border-left: 4px solid #3182ce;
    padding-left: 0.75rem;
    margin: 2rem 0 0.8rem;
    font-size: 1.15rem;
    font-weight: 700;
    color: inherit;
}
.section-num { color: #3182ce; font-size: 0.8rem; font-weight: 600; letter-spacing: 0.05em; }
</style>
""", unsafe_allow_html=True)

    # 報告文件標頭
    _rounds_played = state["round"] - 1
    _verdict_class = "verdict-win" if result == "win" else "verdict-lose"
    _verdict_text  = "✓ Series A 融資核准" if result == "win" else "✗ Series A 融資未通過"
    st.markdown(f"""
<div class="report-header">
  <div class="company">飛食科技股份有限公司　FlyEats Technology Co., Ltd.</div>
  <div class="doc-title">董事會年度營運總結報告</div>
  <div class="doc-meta">
    報告期間：第 1 季 ～ 第 {_rounds_played} 季　｜
    難度：{_cfg.get('name', '標準')}　｜
    策略徽章：{_sc['primary_icon']} {_sc['primary']}　｜
    綜合評等：{_sc['grade']} 級 {_sc['total']:.0f} 分
  </div>
  <div class="{_verdict_class}">{_verdict_text}</div>
</div>
""", unsafe_allow_html=True)

    # § 1  勝利條件評核
    st.markdown('<div class="section-heading"><span class="section-num">§ 1</span> 勝利條件評核</div>', unsafe_allow_html=True)
    show_win_conditions(state)

    # § 2  各城市市場現況
    st.markdown('<div class="section-heading"><span class="section-num">§ 2</span> 各城市市場現況</div>', unsafe_allow_html=True)
    show_city_cards(state)

    # § 3  顧問總結意見
    st.markdown('<div class="section-heading"><span class="section-num">§ 3</span> 資深顧問總結意見</div>', unsafe_allow_html=True)
    if not state.get("ending_report"):
        with st.spinner("AI 顧問撰寫中…"):
            state["ending_report"] = advisor.generate_ending_report(state, scoring=_sc)
        st.session_state["state"] = state
    st.markdown(state["ending_report"])

    # § 4  概念地圖
    st.markdown('<div class="section-heading"><span class="section-num">§ 4</span> 經濟學概念地圖</div>', unsafe_allow_html=True)
    _concept_map = sc.build_concept_map(state)
    _triggered_n = sum(1 for v in _concept_map.values() if v["status"] == "triggered")
    _near_n      = sum(1 for v in _concept_map.values() if v["status"] == "near_miss")
    st.caption(f"✅ 已觸發 {_triggered_n} 個　🔶 差一點 {_near_n} 個　⬜ 未探索 {len(_concept_map) - _triggered_n - _near_n} 個")
    _map_items = list(_concept_map.items())
    _cols_per_row = 4
    for row_start in range(0, len(_map_items), _cols_per_row):
        row = _map_items[row_start : row_start + _cols_per_row]
        cols = st.columns(len(row))
        for col, (cid, info) in zip(cols, row):
            with col:
                status = info["status"]
                label  = f"{info['icon']} {info['name']}"
                if status == "triggered":
                    st.success(label)
                elif status == "near_miss":
                    st.warning(label)
                    if info["hint"]:
                        st.caption(info["hint"])
                else:
                    st.info(label)

    # § 5  決策概念對照分析
    st.markdown('<div class="section-heading"><span class="section-num">§ 5</span> 決策概念對照分析</div>', unsafe_allow_html=True)
    if not state.get("concept_summary"):
        with st.spinner("整理中…"):
            _summary_rows = build_concept_summary_data(state)
            state["concept_summary"] = advisor.generate_concept_summary(state, _summary_rows)
        st.session_state["state"] = state
    st.markdown(state["concept_summary"])

    # § 6  營運數據歷程
    if state["history"]:
        import pandas as pd
        rounds = [h["round"] for h in state["history"]]
        st.markdown('<div class="section-heading"><span class="section-num">§ 6</span> 營運數據歷程</div>', unsafe_allow_html=True)

        st.caption("資金走勢（萬）")
        df_money = pd.DataFrame({"回合": rounds, "資金（萬）": [h["money_after"] for h in state["history"]]})
        st.line_chart(df_money.set_index("回合"))

        st.caption("三城市占（%）")
        df_share = pd.DataFrame({
            "回合": rounds,
            **{city: [h["shares_after"][city] * 100 for h in state["history"]] for city in CITIES},
        })
        st.line_chart(df_share.set_index("回合"))

        st.caption("消費者／外送商家滿意度")
        df_sat = pd.DataFrame({
            "回合": rounds,
            "消費者滿意度": [h["consumer_sat_after"] for h in state["history"]],
            "外送商家滿意度": [h["rider_sat_after"] for h in state["history"]],
        })
        st.line_chart(df_sat.set_index("回合"))

    st.divider()
    _col_dl, _col_restart = st.columns(2)
    with _col_dl:
        _log_json = json.dumps(state.get("event_log", []), ensure_ascii=False, indent=2)
        st.download_button(
            label="📥 下載本局學習紀錄（JSON）",
            data=_log_json,
            file_name=f"flyeats_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            mime="application/json",
        )
    with _col_restart:
        if st.button("🔄 再玩一局", type="primary"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()

# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="飛食平台經營模擬器",
        page_icon="🛵",
        layout="wide",
    )

    # 主畫面字體大幅加大 CSS
    st.markdown(
        """
        <style>
        /* 主畫面標題與副標題 */
        section[data-testid="stMain"] h1 { font-size: 3.5rem !important; }
        section[data-testid="stMain"] h2 { font-size: 2.5rem !important; }
        section[data-testid="stMain"] h3 { font-size: 2rem !important; }
        
        /* 一般內文與 Markdown */
        section[data-testid="stMain"] .stMarkdown p,
        section[data-testid="stMain"] .stMarkdown li,
        section[data-testid="stMain"] .stMarkdown span { 
            font-size: 1.4rem !important; 
            line-height: 1.8;
        }
        
        /* Metric 數字與標籤 */
        section[data-testid="stMain"] [data-testid="stMetricLabel"] * { font-size: 1.8rem !important; }
        section[data-testid="stMain"] [data-testid="stMetricValue"] { font-size: 2.8rem !important; }
        section[data-testid="stMain"] [data-testid="stMetricDelta"] * { font-size: 1.6rem !important; }
        
        /* 表單與選擇器 Label */
        section[data-testid="stMain"] .stCheckbox label,
        section[data-testid="stMain"] .stRadio label,
        section[data-testid="stMain"] .stSelectbox label {
            font-size: 1.5rem !important;
        }
        
        /* 下拉選單內部文字與展開的選項清單 */
        section[data-testid="stMain"] div[data-baseweb="select"] {
            min-height: 3.5rem !important;
            height: auto !important;
        }
        section[data-testid="stMain"] div[data-baseweb="select"] * {
            font-size: 1.5rem !important;
            line-height: 1.2 !important;
        }
        ul[role="listbox"] li {
            font-size: 1.5rem !important;
            padding-top: 0.8rem !important;
            padding-bottom: 0.8rem !important;
        }
        
        /* 進度條（st.progress，包含滿意度文字） */
        section[data-testid="stMain"] div[data-testid="stProgressBar"] * {
            font-size: 1.6rem !important;
            font-weight: bold;
        }
        
        /* 按鈕字體加大 */
        section[data-testid="stMain"] .stButton > button {
            font-size: 1.6rem !important;
            padding: 0.8rem 2rem !important;
            font-weight: bold;
        }
        
        /* Expander 標題 */
        section[data-testid="stMain"] .stExpander summary p {
            font-size: 1.6rem !important;
            font-weight: 800;
        }
        
        /* Info / Success / Warning / Error 提示框文字 */
        section[data-testid="stMain"] .stAlert p {
            font-size: 1.5rem !important;
        }

        /* 滑鼠移到 (?) 圖示時彈出的 help tooltip（概念提示用） */
        div[data-baseweb="tooltip"] {
            font-size: 1.3rem !important;
            max-width: 420px !important;
        }
        div[data-baseweb="tooltip"] * {
            font-size: 1.3rem !important;
            line-height: 1.6 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    if "state" not in st.session_state:
        show_setup_screen()
        return

    state = st.session_state["state"]
    advisor = st.session_state.get("advisor")
    phase = state.get("phase", "MARKET_NEWS")

    # 安全閥：回合超過上限強制結算（REPORT 階段例外，讓玩家先看完第 10 回合報告）
    if phase not in ("GAME_OVER", "REPORT") and state["round"] > state["max_rounds"] and state["game_result"] != "playing":
        state["phase"] = "GAME_OVER"
        st.session_state["state"] = state
        st.rerun()
        return

    if phase == "setup":
        show_setup_screen()
        return

    show_sidebar(state)

    if phase == "MARKET_NEWS":
        if advisor:
            show_market_news_phase(state, advisor)
        else:
            st.error("未設定 API Key")

    elif phase == "PLAYER_DECISION":
        show_decision_phase(state)

    elif phase == "REPORT":
        if advisor:
            show_report_phase(state, advisor)
        else:
            st.error("未設定 API Key")

    elif phase == "GAME_OVER":
        if advisor:
            show_gameover_screen(state, advisor)
        else:
            st.title("🏁 遊戲結束")
            show_win_conditions(state)
            if st.button("再玩一局"):
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()


if __name__ == "__main__":
    main()
