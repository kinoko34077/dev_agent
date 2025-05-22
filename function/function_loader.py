# /c:/Users/user/Programs/dev_agent/function/function_loader.py

import importlib.util
import os
import inspect
from types import ModuleType

SANDBOX_FUNCTIONS_DIR = "../sandbox/functions" # sandbox/functions ディレクトリへのパス

def load_functions_from_directory(directory_path: str) -> dict:
    """
    指定されたディレクトリからPythonファイル内の関数を動的に読み込む。

    Args:
        directory_path: 関数が含まれるディレクトリへのパス。

    Returns:
        関数名をキー、関数オブジェクトを値とする辞書。
    """
    loaded_functions = {}
    if not os.path.exists(directory_path):
        print(f"Warning: Directory not found: {directory_path}")
        return loaded_functions

    for filename in os.listdir(directory_path):
        if filename.endswith(".py") and filename != "__init__.py":
            module_name = filename[:-3] # .pyを取り除く
            file_path = os.path.join(directory_path, filename)

            try:
                spec = importlib.util.spec_from_file_location(module_name, file_path)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    # モジュール内のすべてのメンバをチェック
                    for name, obj in inspect.getmembers(module):
                        # 関数であり、かつローカルで定義された関数（インポートされたものではない）
                        if inspect.isfunction(obj) and obj.__module__ == module_name:
                            loaded_functions[name] = obj
                            print(f"Loaded function: {name} from {filename}")
            except Exception as e:
                print(f"Error loading functions from {filename}: {e}")

    return loaded_functions

if __name__ == "__main__":
    # テスト用に実行する場合
    # ダミーのsandboxディレクトリとファイルを作成
    dummy_sandbox_dir = "../sandbox/functions"
    os.makedirs(dummy_sandbox_dir, exist_ok=True)
    with open(os.path.join(dummy_sandbox_dir, "example_func.py"), "w") as f:
        f.write("def example_function(text):\n")
        f.write("    print(f'Example function called with: {text}')\n")
        f.write("    return f'Processed: {text}'\n")

    print(f"Loading functions from: {dummy_sandbox_dir}")
    sandbox_functions = load_functions_from_directory(dummy_sandbox_dir)
    print("\nLoaded functions:")
    for name in sandbox_functions:
        print(f"- {name}")

    # ロードした関数を呼び出す例
    if "example_function" in sandbox_functions:
        result = sandbox_functions["example_function"]("Hello from loader!")
        print(f"Result: {result}")

    # テスト用ダミーファイルをクリーンアップ
    # os.remove(os.path.join(dummy_sandbox_dir, "example_func.py"))
    # os.rmdir(dummy_sandbox_dir) # ディレクトリが空でないと削除できないためコメントアウト