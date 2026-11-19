"""把仓库根目录加入 sys.path，使 python3 -I 下也能导入 attribution 包。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
