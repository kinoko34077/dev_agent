from Archive.client_functioner import GeminiFunctionClient


import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

client = GeminiFunctionClient()
result = client.invoke("システムログに '初期化完了' を追加して")

print("🔁 Geminiが返した関数呼出し:")
print(result)

