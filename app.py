import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google import genai
from google.genai import types

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
genai_client = genai.Client(api_key=GEMINI_API_KEY)

# ---------------------------------------------------------------------------
# 記憶庫設定：用於紀錄個別使用者的歷史對話
# 結構：{ "user_id_1": [{"role": "user", ...}, {"role": "model", ...}], ... }
# ---------------------------------------------------------------------------
user_histories = {}
MAX_HISTORY_MESSAGES = 6  # 保留最近 6 條訊息（即 3 組一問一答），兼顧上下文與輕量化

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
"""

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

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    # 1. 取得顧客獨有的 LINE User ID
    user_id = event.source.user_id
    user_text = event.message.text.strip()

    # 初始化該顧客的對話歷史紀錄
    if user_id not in user_histories:
        user_histories[user_id] = []

    # 2. 人工接手關鍵字攔截：觸發時順便重置對話記憶
    if any(keyword in user_text for keyword in ["專人", "真人", "客服", "客訴", "改單"]):
        user_histories[user_id] = []  # 清空對話記憶
        reply_text = "✨ 已為您通知專屬顧問！請稍候，門市人員會盡快於營業時間內為您處理。"
    else:
        try:
            # 3. 取出過往歷史紀錄，並建構本次提問
            history = user_histories[user_id]
            current_user_msg = {"role": "user", "parts": [{"text": user_text}]}

            # 將歷史紀錄與最新提問組合
            full_contents = history + [current_user_msg]

            # 4. 傳送歷史對話與 System Prompt 給 Gemini
            response = genai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=full_contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT
                )
            )
            reply_text = response.text

            # 5. 生成成功後，把「問與答」一併追加進該使用者的歷史紀錄中
            history.append(current_user_msg)
            history.append({"role": "model", "parts": [{"text": reply_text}]})

            # 6. 修剪歷史紀錄，只留最近 N 條，防止 context 過長
            if len(history) > MAX_HISTORY_MESSAGES:
                user_histories[user_id] = history[-MAX_HISTORY_MESSAGES:]

        except Exception as e:
            reply_text = "抱歉，目前系統連線較忙碌，請稍後再試，或直接輸入【專人】由門市人員為您服務。"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)