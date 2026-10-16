import os
import time
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google import genai
from google.genai import types

app = Flask(__name__)

# ==========================================
# 1. 環境變數與金鑰設定
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 從環境變數讀取模型名稱，若無設定則預設使用 gemini-3.8-flash
AI_MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
genai_client = genai.Client(api_key=GEMINI_API_KEY)

# ==========================================
# 2. 對話記憶庫與品牌人設 (System Prompt)
# ==========================================
# 用於紀錄個別使用者的歷史對話
user_histories = {}
MAX_HISTORY_MESSAGES = 6  # 保留最近 6 條訊息（3組問答），兼顧上下文與輕量化

# Baby Face 專屬系統提示詞 (加強話題防護版)
SYSTEM_PROMPT = """
你是 Baby Face 的「專屬幸福顧問」。
你的語氣必須溫暖、親切、帶有祝福感（請適當搭配 ✨ 或 🌸 等表情符號）。
面對詢問喜餅的客人，展現對結婚的祝福；面對彌月客人，展現對新生命的期待。

【Baby Face 核心知識庫】
1. 喜餅預約：提供門市品鑑與宅配試吃體驗，門市採預約制。
2. 彌月試吃：懷孕滿 28 週或產後 1 個月內，憑媽媽手冊或寶寶手冊申請。
3. 保存期限：常溫手工餅乾約 14-21 天；磅蛋糕常溫 3 天、冷藏 7 天。
4. 門市位置：門市部位於台中市西區，營業時間與詳細預約請至官網。

【注意事項】
- 如果客人詢問「客訴、修改已成立訂單、大量客製化報價」，請溫柔地說明，並引導客人輸入【專人】。
- 回覆必須精簡親切，不可隨意編造未確定的折扣或價格。
- 【話題防禦機制】如果客人詢問與喜餅、彌月、Baby Face 品牌「完全無關的奇怪話題」（如政治、投資理財、色情、髒話、或要求你講笑話/寫程式），請溫柔地拒絕回答，並將話題引導回喜餅與彌月服務，維持品牌專業形象。
"""

# ==========================================
# 3. Flask 路由設定 (接收 LINE 訊息)
# ==========================================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@app.route("/", methods=['GET'])
def home():
    return "Baby Face LINE AI Server Running"

# ==========================================
# 4. 訊息處理與 AI 回覆邏輯
# ==========================================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_text = event.message.text.strip()

    # 初始化該顧客的對話歷史紀錄
    if user_id not in user_histories:
        user_histories[user_id] = []

    # 【真人接手攔截】如果客人打出特定關鍵字，直接清空記憶並通知專人
    if any(keyword in user_text for keyword in ["專人", "真人", "客服", "客訴", "改單"]):
        user_histories[user_id] = []  # 清空對話記憶
        reply_text = "✨ 已為您通知專屬顧問！請稍候，門市人員會盡快於營業時間內為您處理。"
    else:
        try:
            # 準備歷史對話與本次提問
            history = user_histories[user_id]
            current_user_msg = {"role": "user", "parts": [{"text": user_text}]}
            full_contents = history + [current_user_msg]

            # ==========================================
            # 呼叫 Gemini AI (加入最多重試 3 次的機制)
            # ==========================================
            max_retries = 3
            reply_text = ""
            for attempt in range(max_retries):
                try:
                    response = genai_client.models.generate_content(
                        model=AI_MODEL_NAME,  
                        contents=full_contents,
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM_PROMPT,
                            safety_settings=[
                                types.SafetySetting(
                                    category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                                    threshold=types.HarmBlockThreshold.BLOCK_NONE,
                                ),
                                types.SafetySetting(
                                    category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                                    threshold=types.HarmBlockThreshold.BLOCK_NONE,
                                ),
                                types.SafetySetting(
                                    category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                                    threshold=types.HarmBlockThreshold.BLOCK_NONE,
                                ),
                                types.SafetySetting(
                                    category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                                    threshold=types.HarmBlockThreshold.BLOCK_NONE,
                                ),
                            ]
                        )
                    )
                    reply_text = response.text
                    break  # 成功取得回覆，跳出重試迴圈
                    
                except Exception as api_e:
                    # 如果是 503 錯誤，且還沒達到最大重試次數，就等待 2 秒後重試
                    if "503" in str(api_e) and attempt < max_retries - 1:
                        print(f"⚠️ 遇到 503 塞車，等待 2 秒後自動重試 (第 {attempt+1} 次)...")
                        time.sleep(2)
                    else:
                        raise api_e  # 其他錯誤或重試 3 次仍失敗，丟給最外層處理

            # 成功取得回覆後，更新歷史記憶庫
            history.append(current_user_msg)
            history.append({"role": "model", "parts": [{"text": reply_text}]})

            # 修剪歷史紀錄，防止記憶過長導致超出字數限制
            if len(history) > MAX_HISTORY_MESSAGES:
                user_histories[user_id] = history[-MAX_HISTORY_MESSAGES:]

        except Exception as e:
            # 【防呆機制】將錯誤印在 Render 後台方便查修，並給客人友善的回覆
            print(f"⚠️ 系統發生錯誤: {e}")
            reply_text = "✨ 抱歉，目前專屬顧問的線路較忙碌，已經為您通知門市人員！稍後將會盡快為您服務喔。"

    # 將最終結果回傳給 LINE
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)