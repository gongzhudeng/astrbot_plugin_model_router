import sys
from pathlib import Path

# 让测试可以从插件根导入 core 包
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
