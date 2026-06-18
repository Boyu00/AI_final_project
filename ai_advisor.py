import re

import google.generativeai as genai

_BASE_PROMPT_HEAD = """你是一位台灣外送平台產業分析師，精通平台經濟學。
玩家正在經營一個名為「飛食」的外送平台，在台北、台中、高雄三城市與競爭對手「送香公」競爭。
"""

_BASE_PROMPT_TAIL = """
【回答風格】
- 使用繁體中文，語氣專業，引用台灣本地案例
- 每個分析都要連結經濟學概念（雙邊市場、網路效應、補貼陷阱、損失厭惡、機會成本等）
- 回答簡潔有力
"""


def build_system_prompt(config: dict | None) -> str:
    """依難度 config 動態組裝 SYSTEM_PROMPT，避免 AI 提到玩家這局根本沒有的機制。
    config=None 時視為挑戰模式（向後相容，預設最完整規則）。
    """
    if config is None:
        config = {
            "max_rounds": 10, "dual_satisfaction": True, "tech_tree": True, "black_swan": True,
            "competitor_finance_visible": True, "competitor_satisfaction_enabled": True,
            "bankruptcy_enabled": True, "acquisition_enabled": True,
            "rider_crisis": True, "consumer_crisis": True, "dynamic_retaliation": True,
            "win_money": 250.0, "win_share": 0.60, "win_consumer_sat": 60.0, "win_rider_sat": 60.0,
            "win_required": 2, "win_total": 4,
        }

    parts = [_BASE_PROMPT_HEAD]

    decisions_line = "- 每回合可執行最多 2 項決策\n- 補貼：提升市占（即時）與滿意度\n- 行銷投放：市占效果延遲一回合生效（行銷緩衝機制）\n- 抽成調整：全域生效，降低抽成提高滿意度，提高抽成損失厭惡效應更大\n- 區域擴張：每城市限一次，即時 +2% 市占，之後每回合持續 +1%；擴張後該城市永久免除市占自然流失（1%/回合），等於永久插旗"
    if config["tech_tree"]:
        decisions_line += "\n- 核心技術研發：一次性花費解鎖永久 Buff（AI 路徑/動態定價/獨家聯盟）"
    decisions_line += "\n- 競爭對手採用 3 規則 AI：防守反擊、趁虛而入、自然成長"
    parts.append(f"【遊戲背景】\n{decisions_line}\n")

    if config["max_rounds"] == 10:
        parts.append(
            "【遊戲節奏（10 回合制，三階段）】\n"
            "- Q1–Q3【開拓期】：固定死支壓力大，玩家處於虧損、精準投資階段\n"
            "- Q4–Q7【相持期】：玩家市占成長逼出對手反擊"
            + ("，科技樹開始發揮" if config["tech_tree"] else "") + "\n"
            "- Q8–Q10【決戰期】：逼退對手或死守現金流決定最終勝負\n"
        )
    else:
        parts.append(f"【遊戲節奏】共 {config['max_rounds']} 回合，節奏緊湊，每個決策都很關鍵。\n")

    wc_lines = [f"1. 資金 ≥ {config['win_money']:.0f} 萬", f"2. 任一城市市占 ≥ {config['win_share']*100:.0f}%"]
    if config["dual_satisfaction"]:
        wc_lines.append(
            f"3. 消費者滿意度 ≥ {config['win_consumer_sat']:.0f} 且 外送商家滿意度 ≥ {config['win_rider_sat']:.0f}（雙軌同時達標才算）"
        )
    else:
        wc_lines.append(f"3. 整體滿意度 ≥ {config['win_sat']:.0f}（單軌，不分消費者/外送員）")
    if config["win_total"] == 4:
        if config["acquisition_enabled"]:
            wc_lines.append(
                "4. 送香公破產（💀 現金流歸零，進入清算重組）或 收購（🤝 玩家花費 80 萬直接買下對手，"
                "解鎖條件：送香公資金 ≤ 40 萬）"
            )
        else:
            wc_lines.append("4. 送香公破產（💀 現金流歸零，進入清算重組，無收購選項）")
    parts.append(
        f"【勝利條件（{config['max_rounds']}回合後達成 {config['win_total']} 項中 {config['win_required']} 項即勝）】\n"
        + "\n".join(wc_lines) + "\n"
    )

    if config["dual_satisfaction"]:
        sat_lines = [
            "- 消費者滿意度：補貼 +5，降抽成 +0.5×pct%，升抽成 -1×pct%，擴張 -2",
            "- 外送商家滿意度：補貼 +1，降抽成 +1.5×pct%，升抽成 -2.5×pct%（損失厭惡），擴張 -2",
        ]
        if config["consumer_crisis"]:
            sat_lines.append("- 消費者滿意度 < 30 → 負評爆炸：該城市市占額外流失 2%")
            sat_lines.append("- 消費者滿意度 < 20 → 媒體負面報導：送香公趁機搶走 5% 市占")
        if config["rider_crisis"]:
            sat_lines.append("- 外送商家滿意度 < 40 → 外送荒罷工：該城市本季收入歸零、市占 -3%")
        sat_lines.append("- 每回合固定維運成本 8 萬；未補貼城市市占自然流失 1%，消費者滿意度 -3")
        parts.append("【雙軌滿意度機制】\n" + "\n".join(sat_lines) + "\n")
    else:
        parts.append(
            "【整體滿意度機制（單軌，新手簡化版）】\n"
            "- 補貼會同時推升消費者與外送員的感受，反映在整體滿意度上\n"
            "- 每回合固定維運成本 8 萬；未補貼城市市占自然流失 1%，滿意度緩慢下滑\n"
        )

    if config["black_swan"]:
        parts.append(
            "【黑天鵝隨機事件（每回合 40% 機率觸發）】\n"
            "- 好事：政府補貼（營收 ×1.5）、颱風效應（營收 ×1.4 但外送員 -8）、KOL 爆推（一城消費者 +20）、"
            "騎手保障法（外送員 +12）、運動熱潮（補貼效率 ×1.3）\n"
            "- 壞事：食安醜聞（消費者 -10）、燃油飆漲（外送員 -10，固定成本 +8 萬）、系統當機（一城收入歸零，消費者 -8）、景氣衰退（營收 ×0.7）\n"
            "- 好事（對對手）：送香公爆醜聞（送香公各城 -2% 市占）\n"
            "分析時請將當季黑天鵝納入解讀，說明它如何放大或削弱玩家決策效果。\n"
        )

    if config["competitor_finance_visible"]:
        if config["dynamic_retaliation"]:
            fin_lines = (
                "- 送香公初始資金 180 萬，固定抽成 32%，每回合固定成本 10 萬\n"
                "- 防守反擊（玩家市占 ≥ 18%）依威脅等級動態投入：18-35% 耗資 8 萬（+2%）、35-50% 耗資 15 萬（+5%）、"
                "50-65% 耗資 22 萬（+8%）、≥65% 耗資 30 萬（+12%），資金不足時按比例縮減；趁虛而入耗資 6 萬；自然成長耗資 2 萬\n"
                "- 決戰期（Q8 起）對手傾盡全力反擊，上述花費與回彈上限再 ×1.4 / 上修至 16%\n"
            )
        else:
            fin_lines = (
                "- 送香公初始資金 180 萬，固定抽成 32%，每回合固定成本 10 萬\n"
                "- 防守反擊（玩家市占 ≥ 18%）強度固定，不隨威脅等級或回合升級\n"
            )
        fin_lines += "- 對手的市占增益皆為零和轉移（直接從玩家手上扣），不會無中生有\n"
        if config["bankruptcy_enabled"]:
            fin_lines += "- 送香公資金歸零 → 宣告破產：進入清算模式，每回合各城自動流失 4% 市占給玩家\n"
            fin_lines += "- 圍剿策略：持續衝破 18% 強逼對手反擊，消耗其現金流，加速破產\n"
        parts.append("【送香公財務系統】\n" + fin_lines)

    if config["competitor_satisfaction_enabled"]:
        parts.append(
            "【送香公滿意度（單一指標，不分消費者/外送員）】\n"
            "- 初始 60，本季財務有賺 → +1，虧損 → -2（高強度防守更傷士氣）\n"
            "- 每回合比較玩家平均滿意度與送香公滿意度，差距 ≥ 5 時觸發零和市占微調（每回合最多 0.5%）\n"
            "- 這是長期、溫和的競爭壓力，就算不主動出手，維持高滿意度本身就是一種策略\n"
        )

    parts.append(_BASE_PROMPT_TAIL)
    return "".join(parts)


# 向後相容：模組層級的預設 SYSTEM_PROMPT（挑戰模式規則）
SYSTEM_PROMPT = build_system_prompt(None)

FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-3.1-flash-lite"]

_FALLBACK_KEYWORDS = (
    "quota", "resource exhausted", "429", "rate limit", "too many requests",
    "404", "not found", "not supported", "deprecated",
)


def _is_quota_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(kw in msg for kw in _FALLBACK_KEYWORDS)


class AIAdvisor:
    def __init__(self, api_key: str, config: dict | None = None):
        genai.configure(api_key=api_key)
        self._api_key = api_key
        self._model_index = 0
        self._system_prompt = build_system_prompt(config)
        self._build_model()

    def _build_model(self):
        model_name = FALLBACK_MODELS[self._model_index]
        self.current_model = model_name
        self._model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=self._system_prompt,
        )
        self._chat = self._model.start_chat(history=[])

    def _try_fallback(self) -> bool:
        if self._model_index + 1 < len(FALLBACK_MODELS):
            self._model_index += 1
            self._build_model()
            return True
        return False

    def _send(self, prompt: str) -> str:
        while True:
            try:
                return self._chat.send_message(prompt).text
            except Exception as e:
                if _is_quota_error(e) and self._try_fallback():
                    continue
                raise

    @staticmethod
    def _extract_short_reaction(text: str, max_len: int = 40) -> str:
        """部分模型（如 gemini-2.5-flash）會把推理過程也寫進回應文字，
        這裡用啟發式規則把最後一句『真正的答案』撈出來，過濾掉前面的思考內容。
        """
        text = text.strip()
        # 優先抓最後一個中文引號/直引號包住的片段（模型通常把最終答案放在引號內）
        quoted = re.findall(r"[「\"]([^「」\"]{4,%d})[」\"]" % max_len, text)
        if quoted:
            return quoted[-1].strip()
        # 沒有引號就取最後一個非空行，並濾掉明顯帶有大量英文推理痕跡的行
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for ln in reversed(lines):
            ascii_ratio = sum(1 for c in ln if c.isascii() and c.isalpha()) / max(len(ln), 1)
            if ascii_ratio < 0.3:  # 中文為主的行才採用
                return ln.strip("「」\"' ")
        return text[:max_len]

    @staticmethod
    def _fmt_decisions(decisions: list) -> str:
        texts = []
        for d in decisions:
            dtype = d.get("type", "")
            if dtype == "subsidy":
                texts.append(f"在{d['city']}補貼 {d['amount']} 萬")
            elif dtype == "marketing":
                texts.append(f"在{d['city']}行銷投放 {d['amount']} 萬")
            elif dtype == "commission":
                dir_text = "降低" if d.get("delta", 0) < 0 else "提高"
                texts.append(f"抽成{dir_text} {abs(d.get('delta', 0)*100):.0f}%")
            elif dtype == "expansion":
                texts.append(f"區域擴張 {d['city']}")
        return "、".join(texts) if texts else "無決策"

    # ── 主要生成方法 ───────────────────────────────────────────────────────────

    def generate_market_news(self, round_num: int, game_state: dict) -> str:
        """產生回合開場市場快訊（80-120 字）。"""
        history = game_state.get("history", [])
        last = history[-1] if history else None
        cities = game_state["cities"]
        comp = game_state["competitor"]

        shares_line = "  ".join(
            f"{c}：我方 {cities[c]['share']*100:.0f}%，送香公 {comp[c]*100:.0f}%"
            for c in ["台北", "台中", "高雄"]
        )

        # 本季已抽好的黑天鵝事件
        pending = game_state.get("pending_event")
        if pending:
            swan_line = f"\n本季黑天鵝事件：{pending['name']} — {pending['description']}"
        else:
            swan_line = ""

        if last is None:
            data_block = (
                f"回合：Q1（開局）\n"
                f"市占現況：{shares_line}\n"
                f"初始資金：{game_state['money']:.1f} 萬\n"
                f"抽成率：{game_state['commission_rate']*100:.0f}%"
                f"{swan_line}"
            )
            instruction = f"請撰寫【Q1 產業快訊】開局背景介紹，說明飛食的起步處境。"
        else:
            last_swan = last.get("swan_event")
            last_swan_line = f"\n上季黑天鵝：{last_swan['name']} — {last_swan['description']}" if last_swan else ""
            data_block = (
                f"回合：Q{round_num}\n"
                f"上回合決策：{self._fmt_decisions(last['decisions'])}\n"
                f"資金變化：{last['money_before']:.1f} → {last['money_after']:.1f} 萬（收入 {last['revenue']:.1f} 萬）\n"
                f"台北市占：{last['shares_before']['台北']*100:.1f}% → {last['shares_after']['台北']*100:.1f}%\n"
                f"台中市占：{last['shares_before']['台中']*100:.1f}% → {last['shares_after']['台中']*100:.1f}%\n"
                f"高雄市占：{last['shares_before']['高雄']*100:.1f}% → {last['shares_after']['高雄']*100:.1f}%\n"
                f"對手動態：{last['competitor_action']}"
                f"{last_swan_line}\n"
                f"當前資金：{game_state['money']:.1f} 萬  市占現況：{shares_line}"
                f"{swan_line}"
            )
            instruction = (
                f"請撰寫【Q{round_num} 產業快訊】，報導上季玩家決策市場影響與對手動態，客觀不給建議。"
                + ("若有本季黑天鵝事件，以 1 句帶入新聞敘述中（不要分析，只報導事實）。" if pending else "")
            )

        prompt = (
            f"你是台灣外送產業記者，撰寫 80-120 字的季度市場快訊。\n"
            f"語氣像商業新聞，客觀有洞察，不要給建議，只報導事實，用繁體中文。\n"
            f"注意：市場只有「飛食」與「送香公」兩家業者，請勿使用「雙寡頭」一詞，改用「雙強競逐」、「兩強對決」或直接點名業者名稱。\n\n"
            f"【資料】\n{data_block}\n\n{instruction}"
        )
        try:
            return self._send(prompt)
        except Exception as e:
            return f"【Q{round_num} 產業快訊】本季外送市場競爭激烈，飛食與送香公持續在台北、台中、高雄三城市角力，市場格局逐漸明朗。"

    def generate_round_report(
        self,
        round_num: int,
        history_entry: dict,
        game_state: dict,
    ) -> str:
        """產生回合結尾經營報告（150-250 字）。"""
        cities = game_state["cities"]
        max_share = max(cd["share"] for cd in cities.values())
        best_city = max(cities, key=lambda c: cities[c]["share"])
        c_sat = history_entry["consumer_sat_after"]
        r_sat = history_entry["rider_sat_after"]
        money = history_entry["money_after"]
        remaining = game_state["max_rounds"] - round_num
        crisis = history_entry.get("crisis_cities", [])

        config = game_state["config"]
        bankrupt  = game_state.get("competitor_bankrupt", False)
        acquired  = game_state.get("competitor_acquired", False)

        wc_parts = [
            f"資金 {'✅' if money >= config['win_money'] else '⚠️'}（{money:.1f}/{config['win_money']:.0f} 萬）",
            f"市占 {'✅' if max_share >= config['win_share'] else '⚠️'}（{best_city} {max_share*100:.1f}%/{config['win_share']*100:.0f}%）",
        ]
        if config["dual_satisfaction"]:
            wc_parts.append(f"消費者滿意度 {'✅' if c_sat >= config['win_consumer_sat'] else '⚠️'}（{c_sat:.1f}/{config['win_consumer_sat']:.0f}）")
            wc_parts.append(f"外送商家滿意度 {'✅' if r_sat >= config['win_rider_sat'] else '⚠️'}（{r_sat:.1f}/{config['win_rider_sat']:.0f}）")
        else:
            merged = (c_sat + r_sat) / 2
            wc_parts.append(f"整體滿意度 {'✅' if merged >= config['win_sat'] else '⚠️'}（{merged:.1f}/{config['win_sat']:.0f}）")
        if config["win_total"] == 4:
            if acquired:
                comp_money_str = "✅ 已收購"
            elif bankrupt:
                comp_money_str = "✅ 已破產"
            else:
                comp_money_str = f"⚠️ {game_state.get('competitor_money', 180):.0f} 萬"
            wc_parts.append(f"送香公破產{'/收購' if config['acquisition_enabled'] else ''} {comp_money_str}")
        wc = "  ".join(wc_parts)
        crisis_line = f"\n⚠️ 外送荒爆發城市：{'、'.join(crisis)}（這些城市本季營收歸零）" if crisis else ""

        swan = history_entry.get("swan_event")
        swan_line = f"\n🌪️ 本季黑天鵝：{swan['name']} — {swan['description']}" if swan else ""

        concept_triggers = history_entry.get("concept_triggers", [])
        trigger_line = (
            "\n本回合觸發了以下經濟學情境，請在分析中自然帶入（不要生硬列舉）：\n"
            + "\n".join(t["prompt_instruction"] for t in concept_triggers)
            if concept_triggers else ""
        )

        if config["dual_satisfaction"]:
            _sat_mechanic_line = (
                f"本遊戲有雙軌滿意度：消費者滿意度（補貼提升）與外送商家滿意度（抽成打壓）。"
                + ("若外送商家滿意度 < 40 觸發外送荒，造成嚴重懲罰。\n" if config["rider_crisis"] else "\n")
            )
            _sat_data_lines = (
                f"消費者滿意度：{history_entry['consumer_sat_before']:.1f} → {c_sat:.1f}\n"
                f"外送商家滿意度：{history_entry['rider_sat_before']:.1f} → {r_sat:.1f}\n"
            )
        else:
            _sat_mechanic_line = "本遊戲為單軌整體滿意度（不分消費者/外送員），補貼能提升整體滿意度。\n"
            _merged_before = (history_entry["consumer_sat_before"] + history_entry["rider_sat_before"]) / 2
            _merged_after = (c_sat + r_sat) / 2
            _sat_data_lines = f"整體滿意度：{_merged_before:.1f} → {_merged_after:.1f}\n"

        prompt = (
            f"你是外送產業投資分析師，撰寫 150-250 字的季度經營報告。\n"
            f"必須包含至少一個經濟學概念（雙邊市場、網路效應、補貼陷阱、損失厭惡、囚徒困境、機會成本、邊際效益遞減、規模經濟、市場滲透率等）。\n"
            f"{_sat_mechanic_line}"
            + (f"本季發生黑天鵝事件「{swan['name']}」，請在「決策效果分析」段落中說明它如何放大或削弱玩家決策的效果。\n" if swan else "")
            + trigger_line
            + f"\n格式：\n"
            f"【Q{round_num} 飛食平台季度經營報告】\n"
            f"本季摘要：（1-2句）\n"
            f"決策效果分析：（含經濟學概念）\n"
            f"風險提示：（1-2個）\n"
            f"距離勝利：{wc}\n\n"
            f"【數據】\n"
            f"Q{round_num} 決策：{self._fmt_decisions(history_entry['decisions'])}\n"
            f"資金：{history_entry['money_before']:.1f} → {history_entry['money_after']:.1f} 萬（本季收入 {history_entry['revenue']:.1f} 萬）\n"
            f"台北市占：{history_entry['shares_before']['台北']*100:.1f}% → {history_entry['shares_after']['台北']*100:.1f}%\n"
            f"台中市占：{history_entry['shares_before']['台中']*100:.1f}% → {history_entry['shares_after']['台中']*100:.1f}%\n"
            f"高雄市占：{history_entry['shares_before']['高雄']*100:.1f}% → {history_entry['shares_after']['高雄']*100:.1f}%\n"
            f"{_sat_data_lines}"
            f"抽成率：{game_state['commission_rate']*100:.0f}%\n"
            f"對手行為：{history_entry['competitor_action']}"
            f"{crisis_line}"
            f"{swan_line}\n"
            f"剩餘回合：{remaining}"
        )
        try:
            return self._send(prompt)
        except Exception as e:
            rev = history_entry["revenue"]
            return (
                f"【Q{round_num} 飛食平台季度經營報告】\n"
                f"本季收入 {rev:.1f} 萬，資金餘額 {money:.1f} 萬。\n"
                f"勝利條件進度：{wc}"
            )

    def generate_investor_comment(
        self,
        round_num: int,
        history_entry: dict,
        game_state: dict,
    ) -> str:
        """產生投資人情緒化單句短評（不分析、只反應）。"""
        money = history_entry["money_after"]
        profit = history_entry["money_after"] - history_entry["money_before"]
        max_share = max(cd["share"] for cd in game_state["cities"].values()) * 100
        comp_money = game_state.get("competitor_money", 130)
        remaining = game_state["max_rounds"] - round_num
        crisis = history_entry.get("crisis_cities", [])
        bankrupt = game_state.get("competitor_bankrupt", False)
        acquired = game_state.get("competitor_acquired", False)
        swan = history_entry.get("swan_event")

        context = (
            f"Q{round_num}，剩 {remaining} 回合。"
            f"資金 {money:.1f} 萬（本季 {'+'if profit>=0 else ''}{profit:.1f} 萬）。"
            f"最高市占 {max_share:.1f}%。"
            + (f"送香公已{'收購' if acquired else '破產'}。" if (acquired or bankrupt) else f"送香公剩 {comp_money:.1f} 萬。")
            + (f"外送荒爆發（{'/'.join(crisis)}）。" if crisis else "")
            + (f"黑天鵝：{swan['name']}。" if swan else "")
        )

        prompt = (
            f"你是一位說話直接、有點情緒化的台灣 VC 投資人，正在看飛食本季的數字。"
            f"用第一人稱說出你**此刻的直覺反應**，只要 1 句話（25 字以內），繁體中文。"
            f"不要分析，不要建議，只說你現在的感受或擔憂或興奮。語氣可以是：擔憂、不耐、讚許、質疑、緊張、驚喜。\n\n"
            f"本季狀況：{context}\n\n"
            f"【格式要求，務必遵守】直接輸出這一句話本身，不要有任何前言、不要展示思考過程、"
            f"不要列出多個選項、不要使用英文、不要加引號。只能有一行繁體中文輸出。"
        )
        try:
            raw = self._send(prompt)
            return self._extract_short_reaction(raw)
        except Exception:
            if profit < -10:
                return "這季燒太快了，我開始有點擔心 runway 的問題。"
            elif profit > 20:
                return "這季的數字還不錯，繼續保持！"
            elif crisis:
                return "外送荒這種事不能再發生，對品牌傷害太大了。"
            elif bankrupt or acquired:
                return "送香公倒了，現在就看你能不能把機會變成真正的獲利。"
            else:
                return f"還有 {remaining} 回合，現在這個節奏夠嗎？我有點看不懂你的策略。"

    def generate_concept_summary(self, game_state: dict, summary_rows: list) -> str:
        """Phase 2：根據整局決策↔概念↔效果原始資料，生成教學對照表（markdown table）。"""
        lines = []
        for row in summary_rows:
            dec_parts = []
            for dc in row["decision_concepts"]:
                desc = self._fmt_decisions([dc["decision"]])
                tag = f"（概念：{dc['concept_name']}）" if dc["concept_name"] else ""
                dec_parts.append(f"{desc}{tag}")
            dec_text = "、".join(dec_parts) if dec_parts else "無決策"
            money_delta = row["money_after"] - row["money_before"]
            share_text = "  ".join(
                f"{c}:{row['shares_before'][c]*100:.1f}%→{row['shares_after'][c]*100:.1f}%"
                for c in row["shares_before"]
            )
            lines.append(
                f"Q{row['round']}：{dec_text}；資金 {row['money_before']:.1f}→{row['money_after']:.1f}萬"
                f"（{money_delta:+.1f}）；{share_text}"
            )
        data_block = "\n".join(lines)

        prompt = (
            f"你是教育設計師，根據以下玩家整局的決策紀錄，挑出 3-5 個最具教學意義的回合"
            f"（例如：補貼帶來市占但隨後流失、漲抽成換來收入卻打擊滿意度、研發前期燒錢後期回本等），"
            f"製作一張「決策 ↔ 概念 ↔ 效果」對照表，用 markdown table，表頭為："
            f"回合 | 你的決策 | 對應概念 | 效果。\n"
            f"「效果」欄要具體點出數字變化，並用一句話說明這個概念在這個情境下為什麼成立。\n"
            f"只能使用資料裡標註的概念，不要自己發明資料中沒出現過的概念名稱。\n"
            f"用繁體中文，整段（含表格）控制在 300 字以內，不要逐回合列完，只挑最有教學價值的。\n"
            f"輸出格式：先一行小標題「📚 本局決策概念對照表」，接著直接是 markdown table，不要有其他前言。\n\n"
            f"【整局決策紀錄】\n{data_block}"
        )
        try:
            return self._send(prompt)
        except Exception:
            return "📚 本局決策概念對照表\n\n（生成失敗，可從上方各回合報告自行回顧決策與效果）"

    def generate_ending_report(self, game_state: dict) -> str:
        """產生遊戲結束總結報告（200-300 字）。"""
        result = game_state.get("game_result", "lose")
        history = game_state.get("history", [])
        cities = game_state["cities"]
        max_share = max(cd["share"] for cd in cities.values())
        best_city = max(cities, key=lambda c: cities[c]["share"])
        total_market = sum(cd["market"] for cd in cities.values())
        c_sat = sum(cd["market"] * cd["consumer_satisfaction"] for cd in cities.values()) / total_market
        r_sat = sum(cd["market"] * cd["rider_satisfaction"]    for cd in cities.values()) / total_market

        history_text = "\n".join(
            f"Q{h['round']}: 決策={self._fmt_decisions(h['decisions'])}, "
            f"資金 {h['money_before']:.1f}→{h['money_after']:.1f}萬, 收入 {h['revenue']:.1f}萬"
            + (f", 外送荒：{'、'.join(h.get('crisis_cities', []))}" if h.get("crisis_cities") else "")
            + (f", 黑天鵝：{h['swan_event']['name']}" if h.get("swan_event") else "")
            for h in history
        )
        result_text = "勝利（Series A 融資通過）" if result == "win" else "失敗"

        swan_rounds = [h for h in history if h.get("swan_event")]
        swan_summary = "、".join(f"Q{h['round']}「{h['swan_event']['name']}」" for h in swan_rounds)
        swan_note = f"\n本局共發生 {len(swan_rounds)} 次黑天鵝事件：{swan_summary}，請於關鍵轉折點分析中至少提及一次。" if swan_rounds else ""

        config = game_state["config"]
        _style_eval = "經營風格評估（特別分析消費者 vs 外送商家滿意度的平衡）" if config["dual_satisfaction"] else "經營風格評估"
        _sat_line = (
            f"消費者滿意度：{c_sat:.1f}　外送商家滿意度：{r_sat:.1f}\n" if config["dual_satisfaction"]
            else f"整體滿意度：{(c_sat+r_sat)/2:.1f}\n"
        )

        prompt = (
            f"你是投資分析師，撰寫 200-300 字的 Series A 審查總結。\n"
            f"包含：1){_style_eval} "
            f"2)1-2個關鍵轉折點分析（含黑天鵝事件影響） 3)本局體現的經濟學概念清單\n"
            f"用繁體中文。{swan_note}\n\n"
            f"【結果】{result_text}\n"
            f"最終資金：{game_state['money']:.1f} 萬\n"
            f"台北市占：{cities['台北']['share']*100:.1f}%\n"
            f"台中市占：{cities['台中']['share']*100:.1f}%\n"
            f"高雄市占：{cities['高雄']['share']*100:.1f}%\n"
            f"最高市占：{best_city} {max_share*100:.1f}%\n"
            f"{_sat_line}"
            f"抽成率：{game_state['commission_rate']*100:.0f}%\n\n"
            f"【各回合紀錄】\n{history_text}"
        )
        try:
            return self._send(prompt)
        except Exception as e:
            return (
                f"（{'遊戲勝利！' if result == 'win' else '遊戲結束。'}"
                f"{game_state['max_rounds']}回合總結：最終資金 {game_state['money']:.1f} 萬，最高市占 {best_city} {max_share*100:.1f}%，"
                f"消費者滿意度 {c_sat:.1f}，外送商家滿意度 {r_sat:.1f}。）"
            )
